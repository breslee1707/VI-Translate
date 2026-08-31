#!/usr/bin/env python3
"""Find, download, and stage a newer release, then hand the swap to a helper.

The app ships as a one-folder build that locks its own DLLs while it runs, so
it can never replace itself in place. What it can do is unpack the new build
next to the old one and start a detached PowerShell helper that waits for this
process to exit, moves the two folders, and starts the new executable. Every
function here is either that staging work or one of the checks that decide
whether it is safe to attempt it. When any of them says no, the caller falls
back to what this module used to do alone: point at the release page.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

APP_VERSION = "0.2.4"
REPOSITORY = "breslee1707/VI-Translate"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"

# QA points this at a local server to rehearse a real download and swap without
# publishing a release. It is unset on any machine that is not testing one.
API_VARIABLE = "PDFTRANSLATE_UPDATE_API"

WINDOWS_ASSET = "PDFTranslate-windows.zip"
# The two files build.ps1 refuses to package without. Enough to tell a real
# payload from a truncated download or a zip of the wrong thing.
REQUIRED_PAYLOAD = ("PDFTranslate.exe", "_internal/base_library.zip")
STAGED_MARKER = "staged-version.txt"
STAGING_SUFFIX = ".update"
BACKUP_SUFFIX = ".old"
# The unpacked build is about twice the archive, and the old one stays on disk
# until the swap finishes. Four times the download, with a floor for the case
# where the API does not report a size.
FREE_SPACE_FLOOR = 700_000_000

DOWNLOAD_CHUNK = 1 << 20


@dataclass(frozen=True)
class Release:
    """A published release this build can install."""

    tag: str
    url: str
    size: int


def version_parts(tag: str) -> tuple[int, ...]:
    """Turn a release tag into numbers: "v0.10.0" -> (0, 10, 0).

    Raises ValueError on anything that is not a dotted number, which is how
    check_for_update rejects tags like "nightly".
    """
    core = tag.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    if not core:
        raise ValueError(f"not a version tag: {tag!r}")
    return tuple(int(piece) for piece in core.split("."))


def is_newer(candidate: str, current: str) -> bool:
    """Compare as numbers, padded to equal length.

    Comparing the strings would be wrong: "0.9.0" sorts above "0.10.0".
    """
    left, right = version_parts(candidate), version_parts(current)
    length = max(len(left), len(right))
    pad = (0,) * length
    return (left + pad)[:length] > (right + pad)[:length]


def api_url() -> str:
    return os.environ.get(API_VARIABLE) or RELEASES_API


def latest_release(current: str = APP_VERSION) -> Release | None:
    """Return the newer release with a Windows asset, or None. Never raises.

    Being offline is normal and must stay silent, so every failure - no
    network, rate limit, no release yet, a tag that is not a version, a
    release whose assets have not finished uploading - is just "no update".
    """
    try:
        import requests

        response = requests.get(api_url(), timeout=10)
        response.raise_for_status()
        payload = response.json()
        tag = payload["tag_name"]
        if not is_newer(tag, current):
            return None
        for asset in payload.get("assets") or ():
            if asset.get("name") == WINDOWS_ASSET:
                return Release(tag, asset["browser_download_url"], int(asset.get("size") or 0))
        return Release(tag, "", 0)
    except Exception:  # noqa: BLE001 - an update check must never break startup
        return None


def check_for_update(current: str = APP_VERSION) -> str | None:
    """Return the newer tag name, or None. Never raises."""
    release = latest_release(current)
    return release.tag if release else None


# -- where the new build goes -------------------------------------------------


def install_directory() -> Path | None:
    """The folder the one-folder build runs from, or None when not frozen.

    Running from source has no folder to swap, so every caller reads None as
    "this build cannot update itself".
    """
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def staging_directory(install: Path) -> Path:
    """Sibling of the install folder, because the swap is a rename.

    A temp directory would sit on another volume often enough to turn the swap
    into a 386 MB copy, performed while the app is closed and the user is
    watching nothing happen.
    """
    return install.parent / (install.name + STAGING_SUFFIX)


def staged_payload(install: Path) -> Path:
    return staging_directory(install) / "new"


def backup_directory(install: Path) -> Path:
    return install.parent / (install.name + BACKUP_SUFFIX)


def payload_is_complete(directory: Path) -> bool:
    """A staged build is only usable if the files the swap depends on are there."""
    return all((directory / name).is_file() for name in REQUIRED_PAYLOAD)


def is_writable(directory: Path) -> bool:
    probe = directory / ".pdftranslate-write-test"
    try:
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def has_room_for(size: int, directory: Path) -> bool:
    try:
        free = shutil.disk_usage(directory).free
    except OSError:
        return False
    return free >= max(size * 4, FREE_SPACE_FLOOR)


def can_self_update(install: Path | None = None) -> bool:
    """A Windows one-folder build, in a folder whose parent we may rename in."""
    if sys.platform != "win32":
        return False
    install = install or install_directory()
    return install is not None and is_writable(install.parent)


# -- staging ------------------------------------------------------------------


def download_release(
    release: Release,
    destination: Path,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """Stream the asset to disk, reporting bytes as they land.

    Downloads to a .part file and renames on success, so an interrupted
    download can never be mistaken for a complete archive.
    """
    import requests

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    try:
        # Every unlink is outside the open handle: Windows refuses to delete a
        # file that is still open, and that error would replace the real one.
        with requests.get(release.url, stream=True, timeout=30) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or release.size or 0)
            received = 0
            with partial.open("wb") as handle:
                for chunk in response.iter_content(DOWNLOAD_CHUNK):
                    if cancel is not None and cancel():
                        raise RuntimeError("update download cancelled")
                    handle.write(chunk)
                    received += len(chunk)
                    if progress is not None:
                        progress(received, total)
        if release.size and partial.stat().st_size != release.size:
            raise RuntimeError("update download is the wrong size")
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(destination)
    return destination


def extract_payload(archive: Path, target: Path) -> Path:
    """Unpack into a fresh folder and refuse anything that is not a build."""
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    if not payload_is_complete(target):
        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError(f"the archive is not a {WINDOWS_ASSET} payload")
    return target


def stage_update(
    release: Release,
    install: Path,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """Download and unpack the release next to the install folder.

    Returns the staged payload folder, ready for the helper to move into place.
    """
    staging = staging_directory(install)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    archive = download_release(release, staging / WINDOWS_ASSET, progress, cancel)
    payload = extract_payload(archive, staged_payload(install))
    archive.unlink(missing_ok=True)
    (staging / STAGED_MARKER).write_text(release.tag, encoding="utf-8")
    return payload


def staged_version(install: Path) -> str | None:
    """The tag of a complete staged build, or None.

    A user who downloads an update and then closes the app without restarting
    should not pay for the same 193 MB again on the next launch.
    """
    marker = staging_directory(install) / STAGED_MARKER
    try:
        tag = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not tag or not payload_is_complete(staged_payload(install)):
        return None
    try:
        version_parts(tag)
    except ValueError:
        return None
    return tag


def discard_staging(install: Path) -> None:
    shutil.rmtree(staging_directory(install), ignore_errors=True)


def clean_previous_install(install: Path) -> None:
    """Delete the build that the last swap replaced.

    The helper deletes it itself; this is the backstop for the run where it
    could not, so a leftover 386 MB does not live next to the app forever.
    """
    shutil.rmtree(backup_directory(install), ignore_errors=True)


# -- the helper that does the swap --------------------------------------------

# One literal with no formatting holes: every path arrives as a real argument,
# so no quoting mistake in this file can turn into a mangled command line that
# moves or deletes the wrong folder.
UPDATER_SCRIPT = r"""
param(
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [Parameter(Mandatory = $true)][string]$Install,
    [Parameter(Mandatory = $true)][string]$Staged,
    [Parameter(Mandatory = $true)][string]$LogPath,
    [int]$RetrySeconds = 20,
    [switch]$NoRelaunch
)

$ErrorActionPreference = "Stop"
$Backup = $Install + ".old"
$Executable = Join-Path $Install "PDFTranslate.exe"

function Note($message) {
    $line = "{0}  {1}" -f (Get-Date -Format o), $message
    try { Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8 } catch { }
}

function Relaunch {
    if ($NoRelaunch) { return }
    try { Start-Process -FilePath $Executable } catch { Note "relaunch failed: $_" }
}

# The app holds its own DLLs open, and Windows releases those handles a moment
# after the process is gone rather than at exit, so every move is retried.
function Move-WithRetry($from, $to) {
    $deadline = (Get-Date).AddSeconds($RetrySeconds)
    while ($true) {
        try {
            Move-Item -LiteralPath $from -Destination $to -ErrorAction Stop
            return $true
        } catch {
            if ((Get-Date) -ge $deadline) {
                Note ("move failed: {0} -> {1}: {2}" -f $from, $to, $_)
                return $false
            }
            Start-Sleep -Milliseconds 400
        }
    }
}

# A move that throws is not the only way to fail. Move-Item on a folder whose
# files are locked can create the destination, copy part of it, and then give
# up, so the swap is judged by what actually arrived.
function Test-Payload($root) {
    if (-not (Test-Path -LiteralPath (Join-Path $root "PDFTranslate.exe"))) { return $false }
    if (-not (Test-Path -LiteralPath (Join-Path $root "_internal\base_library.zip"))) { return $false }
    return $true
}

function Restore-Backup {
    # Whatever sits at $Install now is debris from the failed move, never the
    # user's build - that one is at $Backup - and it has to go, or moving the
    # backup back would nest it inside the debris instead of replacing it.
    if (Test-Path -LiteralPath $Install) {
        Remove-Item -LiteralPath $Install -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Move-WithRetry $Backup $Install) { return $true }
    Note "ROLLBACK FAILED: the working build is at $Backup"
    return $false
}

Note ("waiting for process {0}" -f $ProcessId)
try { Wait-Process -Id $ProcessId -Timeout 120 -ErrorAction Stop } catch { Note "wait: $_" }

if (-not (Test-Path -LiteralPath $Staged)) {
    Note "nothing staged, leaving the install alone"
    Relaunch
    exit 1
}
if (Test-Path -LiteralPath $Backup) {
    Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not (Move-WithRetry $Install $Backup)) {
    Note "could not move the old build aside; nothing was changed"
    Relaunch
    exit 1
}
$moved = Move-WithRetry $Staged $Install
if (-not $moved -or -not (Test-Payload $Install)) {
    Note "the new build did not arrive intact; rolling back"
    if (-not (Restore-Backup)) { exit 2 }
    Relaunch
    exit 1
}

Note "swapped"
Relaunch
Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue
exit 0
"""


def log_path() -> Path:
    """Outside the install folder, so the swap cannot delete the record of it."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PDFTranslate"
    base.mkdir(parents=True, exist_ok=True)
    return base / "updater.log"


def write_updater_script(directory: Path) -> Path:
    script = directory / "updater.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    # Windows PowerShell 5.1 reads a BOM-less file as ANSI, which mangles any
    # non-ASCII path the helper is asked to move.
    script.write_text(UPDATER_SCRIPT, encoding="utf-8-sig")
    return script


def updater_command(
    script: Path,
    process_id: int,
    install: Path,
    staged: Path,
    log: Path,
) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
        str(script),
        "-ProcessId",
        str(process_id),
        "-Install",
        str(install),
        "-Staged",
        str(staged),
        "-LogPath",
        str(log),
    ]


def launch_updater(install: Path, process_id: int | None = None) -> None:
    """Start the helper and return. The caller must then close the app.

    Not DETACHED_PROCESS, however much this looks like the place for it:
    powershell.exe started with it exits 0 without running the script at all,
    silently, which leaves the app closing and nothing replacing it. A child
    process outlives its parent on Windows anyway, so CREATE_NO_WINDOW - which
    keeps a console from flashing up as the app closes - is all it needs.
    """
    script = write_updater_script(staging_directory(install))
    process_id = os.getpid() if process_id is None else process_id
    command = updater_command(script, process_id, install, staged_payload(install), log_path())
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen(
        command,
        creationflags=creationflags,
        close_fds=True,
        # The app's own handles are about to disappear underneath it.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
