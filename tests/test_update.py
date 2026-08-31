from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app.update import (
    APP_VERSION,
    REQUIRED_PAYLOAD,
    STAGED_MARKER,
    Release,
    backup_directory,
    check_for_update,
    download_release,
    extract_payload,
    is_newer,
    latest_release,
    launch_updater,
    payload_is_complete,
    stage_update,
    staged_payload,
    staged_version,
    staging_directory,
    updater_command,
    version_parts,
    write_updater_script,
)

WINDOWS_ONLY = unittest.skipUnless(sys.platform == "win32", "the updater is Windows only")


def write_payload(directory: Path, marker: bytes = b"new") -> Path:
    """A folder that passes the payload check, at about 0.1% of the real size."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_PAYLOAD:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(marker)
    return directory


def build_archive(path: Path, complete: bool = True) -> bytes:
    with zipfile.ZipFile(path, "w") as bundle:
        names = REQUIRED_PAYLOAD if complete else REQUIRED_PAYLOAD[1:]
        for name in names:
            bundle.writestr(name, "payload")
    return path.read_bytes()


def fake_requests(body: bytes, headers: dict | None = None, json_payload: dict | None = None):
    """Stand in for the requests module across both call shapes we use."""

    class Response:
        def __init__(self) -> None:
            self.headers = headers or {"Content-Length": str(len(body))}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return json_payload or {}

        def iter_content(self, size: int):
            for start in range(0, len(body), size):
                yield body[start : start + size]

        def __enter__(self):
            return self

        def __exit__(self, *_exception) -> bool:
            return False

    return types.SimpleNamespace(get=lambda *_a, **_k: Response())


class VersionCompareTests(unittest.TestCase):
    def test_a_higher_patch_is_newer(self):
        self.assertTrue(is_newer("0.2.1", "0.2.0"))

    def test_ten_beats_nine(self):
        """String comparison gets this backwards, which is why we parse ints."""
        self.assertTrue(is_newer("0.10.0", "0.9.0"))
        self.assertFalse(is_newer("0.9.0", "0.10.0"))

    def test_the_same_version_is_not_newer(self):
        self.assertFalse(is_newer("0.2.0", "0.2.0"))

    def test_an_older_version_is_not_newer(self):
        self.assertFalse(is_newer("0.1.0", "0.2.0"))

    def test_shorter_and_longer_tags_compare_by_padding(self):
        self.assertFalse(is_newer("0.2", "0.2.0"))
        self.assertTrue(is_newer("0.2.1", "0.2"))

    def test_the_v_prefix_is_optional(self):
        self.assertEqual(version_parts("v0.2.0"), version_parts("0.2.0"))

    def test_a_prerelease_suffix_is_ignored(self):
        self.assertEqual(version_parts("v0.2.0-rc1"), (0, 2, 0))

    def test_a_non_version_tag_is_rejected(self):
        for tag in ("nightly", "", "v", "1.x"):
            with self.subTest(tag=tag):
                with self.assertRaises(ValueError):
                    version_parts(tag)


class CheckForUpdateTests(unittest.TestCase):
    """The check runs at startup, so no failure of it may reach the user."""

    def _with_release(self, payload: dict, current: str):
        fake = fake_requests(b"", json_payload=payload)
        with mock.patch.dict(sys.modules, {"requests": fake}):
            return latest_release(current)

    def _with_tag(self, tag: str, current: str) -> str | None:
        fake = fake_requests(b"", json_payload={"tag_name": tag})
        with mock.patch.dict(sys.modules, {"requests": fake}):
            return check_for_update(current)

    def test_a_newer_tag_is_returned_verbatim(self):
        self.assertEqual(self._with_tag("v0.3.0", "0.2.0"), "v0.3.0")

    def test_the_current_tag_reports_no_update(self):
        self.assertIsNone(self._with_tag("v0.2.0", "0.2.0"))

    def test_a_junk_tag_is_no_update_rather_than_a_crash(self):
        self.assertIsNone(self._with_tag("nightly", "0.2.0"))

    def test_a_network_failure_is_no_update_rather_than_a_crash(self):
        def explode(*_a, **_k):
            raise OSError("no network")

        with mock.patch.dict(sys.modules, {"requests": types.SimpleNamespace(get=explode)}):
            self.assertIsNone(check_for_update("0.2.0"))

    def test_the_shipped_version_is_a_parsable_tag(self):
        self.assertGreaterEqual(len(version_parts(APP_VERSION)), 2)

    def test_the_windows_asset_carries_the_download(self):
        release = self._with_release(
            {
                "tag_name": "v0.3.0",
                "assets": [
                    {"name": "PDFTranslate-macos-intel.dmg", "browser_download_url": "mac", "size": 1},
                    {
                        "name": "PDFTranslate-windows.zip",
                        "browser_download_url": "https://example/windows.zip",
                        "size": 42,
                    },
                ],
            },
            "0.2.0",
        )
        self.assertEqual(release, Release("v0.3.0", "https://example/windows.zip", 42))

    def test_a_release_without_the_asset_still_reports_the_tag(self):
        """Assets upload after the tag exists, and the release page still works."""
        release = self._with_release({"tag_name": "v0.3.0", "assets": []}, "0.2.0")
        self.assertEqual(release.tag, "v0.3.0")
        self.assertEqual(release.url, "")


class StagingLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.install = Path(self.temp.name) / "PDFTranslate"
        self.install.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_every_working_folder_is_a_sibling_of_the_install(self):
        """The swap is a rename, which only works within one volume and parent."""
        for path in (staging_directory(self.install), backup_directory(self.install)):
            self.assertEqual(path.parent, self.install.parent)
        self.assertEqual(staged_payload(self.install).parent, staging_directory(self.install))

    def test_a_folder_missing_a_required_file_is_not_a_payload(self):
        payload = write_payload(staged_payload(self.install))
        self.assertTrue(payload_is_complete(payload))
        (payload / REQUIRED_PAYLOAD[0]).unlink()
        self.assertFalse(payload_is_complete(payload))

    def test_no_marker_means_nothing_is_staged(self):
        self.assertIsNone(staged_version(self.install))

    def test_a_marker_without_the_payload_is_not_staged(self):
        staging_directory(self.install).mkdir(parents=True)
        (staging_directory(self.install) / STAGED_MARKER).write_text("v9.0.0", encoding="utf-8")
        self.assertIsNone(staged_version(self.install))

    def test_a_marker_with_the_payload_reports_the_tag(self):
        write_payload(staged_payload(self.install))
        (staging_directory(self.install) / STAGED_MARKER).write_text("v9.0.0\n", encoding="utf-8")
        self.assertEqual(staged_version(self.install), "v9.0.0")

    def test_a_junk_marker_is_not_staged(self):
        write_payload(staged_payload(self.install))
        (staging_directory(self.install) / STAGED_MARKER).write_text("nightly", encoding="utf-8")
        self.assertIsNone(staged_version(self.install))


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.destination = Path(self.temp.name) / "PDFTranslate-windows.zip"
        self.body = b"x" * (3 << 20)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _download(self, release: Release, **kwargs) -> Path:
        with mock.patch.dict(sys.modules, {"requests": fake_requests(self.body)}):
            return download_release(release, self.destination, **kwargs)

    def test_the_asset_lands_whole_and_reports_progress(self):
        seen: list[tuple[int, int]] = []
        self._download(Release("v1.0.0", "u", len(self.body)), progress=lambda a, b: seen.append((a, b)))
        self.assertEqual(self.destination.read_bytes(), self.body)
        self.assertEqual(seen[-1], (len(self.body), len(self.body)))
        self.assertTrue(all(total == len(self.body) for _received, total in seen))

    def test_a_truncated_download_is_refused_and_leaves_nothing(self):
        """A short file that kept the archive name would stage as a broken build."""
        with self.assertRaises(RuntimeError):
            self._download(Release("v1.0.0", "u", len(self.body) + 1))
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.iterdir()), [])

    def test_cancelling_stops_the_download_and_leaves_nothing(self):
        with self.assertRaises(RuntimeError):
            self._download(Release("v1.0.0", "u", 0), cancel=lambda: True)
        self.assertEqual(list(self.destination.parent.iterdir()), [])


class ExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_a_real_payload_extracts(self):
        archive = self.root / "build.zip"
        build_archive(archive)
        target = extract_payload(archive, self.root / "new")
        self.assertTrue(payload_is_complete(target))

    def test_an_archive_of_the_wrong_thing_is_refused_and_removed(self):
        archive = self.root / "build.zip"
        build_archive(archive, complete=False)
        with self.assertRaises(RuntimeError):
            extract_payload(archive, self.root / "new")
        self.assertFalse((self.root / "new").exists())


class StageUpdateTests(unittest.TestCase):
    """Download and unpack together, which is what the GUI thread calls."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.install = self.root / "PDFTranslate"
        self.install.mkdir()
        self.body = build_archive(self.root / "source.zip")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _stage(self, tag: str = "v9.0.0") -> Path:
        release = Release(tag, "https://example/windows.zip", len(self.body))
        with mock.patch.dict(sys.modules, {"requests": fake_requests(self.body)}):
            return stage_update(release, self.install)

    def test_staging_leaves_a_payload_a_marker_and_no_archive(self):
        payload = self._stage()
        self.assertTrue(payload_is_complete(payload))
        self.assertEqual(staged_version(self.install), "v9.0.0")
        self.assertEqual(list(staging_directory(self.install).glob("*.zip")), [])
        self.assertEqual(list(staging_directory(self.install).glob("*.part")), [])

    def test_staging_again_replaces_what_was_there(self):
        self._stage("v9.0.0")
        (staging_directory(self.install) / "leftover.txt").write_text("old", encoding="utf-8")
        self._stage("v9.1.0")
        self.assertEqual(staged_version(self.install), "v9.1.0")
        self.assertFalse((staging_directory(self.install) / "leftover.txt").exists())

    def test_the_install_folder_is_never_touched_by_staging(self):
        (self.install / "PDFTranslate.exe").write_bytes(b"old")
        self._stage()
        self.assertEqual((self.install / "PDFTranslate.exe").read_bytes(), b"old")


@WINDOWS_ONLY
class UpdaterScriptTests(unittest.TestCase):
    """The helper runs after the app is gone, so it is tested on its own."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.install = write_payload(self.root / "PDFTranslate", b"old")
        write_payload(staged_payload(self.install), b"new")
        self.log = self.root / "updater.log"
        self.script = write_updater_script(staging_directory(self.install))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self, staged: Path | None = None, retry_seconds: int = 1) -> subprocess.CompletedProcess:
        command = updater_command(
            self.script,
            _exited_process_id(),
            self.install,
            staged if staged is not None else staged_payload(self.install),
            self.log,
        )
        command += ["-RetrySeconds", str(retry_seconds), "-NoRelaunch"]
        return subprocess.run(command, capture_output=True, text=True, timeout=120)

    def test_the_script_is_valid_powershell(self):
        check = (
            "$errors = $null; "
            f"$null = [System.Management.Automation.PSParser]::Tokenize("
            f"(Get-Content -Raw -LiteralPath '{self.script}'), [ref]$errors); "
            "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
        )
        parsed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", check],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(parsed.returncode, 0, parsed.stdout + parsed.stderr)

    def test_the_new_build_replaces_the_old_one_and_the_old_one_is_deleted(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, self.log.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual((self.install / "PDFTranslate.exe").read_bytes(), b"new")
        self.assertFalse(backup_directory(self.install).exists())

    def test_nothing_staged_leaves_the_install_alone(self):
        result = self._run(staged=self.root / "missing")
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.install / "PDFTranslate.exe").read_bytes(), b"old")

    def test_the_launcher_really_starts_the_helper_and_the_swap_happens(self):
        """The app is gone by the time this matters, so it is proven here.

        powershell.exe started with DETACHED_PROCESS exits 0 without running
        the script, which looks like a working launch from every angle except
        the folder never changing.
        """
        launch_updater(self.install, process_id=_exited_process_id())
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                # The install folder is briefly absent, between the two moves.
                swapped = (self.install / "PDFTranslate.exe").read_bytes() == b"new"
            except OSError:
                swapped = False
            if swapped and not backup_directory(self.install).exists():
                return
            time.sleep(0.5)
        self.fail("the helper never swapped the folders")

    def test_a_failed_swap_rolls_the_old_build_back(self):
        """Holding a staged file open is what a virus scanner does to the real one."""
        locked = (staged_payload(self.install) / "PDFTranslate.exe").open("ab")
        try:
            result = self._run()
        finally:
            locked.close()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(self.install.is_dir())
        self.assertEqual((self.install / "PDFTranslate.exe").read_bytes(), b"old")
        self.assertFalse(backup_directory(self.install).exists())


def _exited_process_id() -> int:
    """The PID of a process that has already finished.

    The helper's first act is to wait for the app to exit; an already dead PID
    is the same situation one moment later, and keeps the test instant.
    """
    finished = subprocess.Popen(["cmd.exe", "/c", "exit"])
    finished.wait()
    return finished.pid


if __name__ == "__main__":
    unittest.main()
