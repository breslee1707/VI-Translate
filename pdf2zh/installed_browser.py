"""Use installed desktop browsers with an isolated, persistent app profile."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse

import websocket


@dataclass(frozen=True)
class InstalledBrowser:
    key: str
    name: str
    family: str
    executable: Path


_BROWSERS = (
    ("chrome", "Google Chrome", "chromium", "chrome.exe", "Google/Chrome/Application/chrome.exe", "Google Chrome.app/Contents/MacOS/Google Chrome"),
    ("edge", "Microsoft Edge", "chromium", "msedge.exe", "Microsoft/Edge/Application/msedge.exe", "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ("brave", "Brave", "chromium", "brave.exe", "BraveSoftware/Brave-Browser/Application/brave.exe", "Brave Browser.app/Contents/MacOS/Brave Browser"),
    ("coccoc", "Cốc Cốc", "chromium", "browser.exe", "CocCoc/Browser/Application/browser.exe", "CocCoc.app/Contents/MacOS/CocCoc"),
    ("firefox", "Firefox", "firefox", "firefox.exe", "Mozilla Firefox/firefox.exe", "Firefox.app/Contents/MacOS/firefox"),
)


def _command_executable(command: str) -> Path | None:
    match = re.match(r'^\s*(?:"([^"]+\.exe)"|(.+?\.exe)(?:\s|$))', command, re.IGNORECASE)
    return Path(os.path.expandvars(match.group(1) or match.group(2))) if match else None


def _windows_registry_paths() -> tuple[list[Path], Path | None]:
    import winreg

    def read(root: int, key: str, value: str = "", view: int = 0) -> str:
        try:
            with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as handle:
                return str(winreg.QueryValueEx(handle, value)[0])
        except OSError:
            return ""

    paths = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for _, _, _, exe, _, _ in _BROWSERS:
                value = read(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}", view=view)
                if value:
                    paths.append(Path(os.path.expandvars(value.strip('"'))))
            key = r"SOFTWARE\Clients\StartMenuInternet"
            try:
                with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as handle:
                    index = 0
                    while True:
                        client = winreg.EnumKey(handle, index)
                        command = read(root, key + "\\" + client + r"\shell\open\command", view=view)
                        path = _command_executable(command)
                        if path is not None:
                            paths.append(path)
                        index += 1
            except OSError:
                pass
    progid = read(winreg.HKEY_CURRENT_USER,
                  r"SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice", "ProgId")
    default = _command_executable(read(winreg.HKEY_CLASSES_ROOT, progid + r"\shell\open\command")) if progid else None
    return paths, default


def discover_browsers() -> list[InstalledBrowser]:
    """Prefer the HTTPS default; only recognize browsers with a supported adapter."""
    paths: list[Path] = []
    default: Path | None = None
    if sys.platform == "win32":
        paths, default = _windows_registry_paths()
        for env in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            root = os.environ.get(env)
            if root:
                paths.extend(Path(root) / item[4] for item in _BROWSERS)
    elif sys.platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            paths.extend(root / item[5] for item in _BROWSERS)
        try:
            from AppKit import NSWorkspace
            from Foundation import NSURL

            application = NSWorkspace.sharedWorkspace().URLForApplicationToOpenURL_(NSURL.URLWithString_("https://translate.google.com"))
            if application is not None:
                app_path = Path(str(application.path()))
                for item in _BROWSERS:
                    executable = app_path.joinpath(*Path(item[5]).parts[1:])
                    if executable.is_file():
                        default = executable
                        paths.insert(0, executable)
                        break
        except (ImportError, AttributeError):
            pass
    for item in _BROWSERS:
        path = shutil.which(item[3] if sys.platform == "win32" else item[0])
        if path:
            paths.append(Path(path))
    if default is not None:
        paths.insert(0, default)
    found: dict[str, InstalledBrowser] = {}
    for path in paths:
        if not path.is_file():
            continue
        for key, name, family, exe, _, mac_exe in _BROWSERS:
            matches = path.name.casefold() == (exe if sys.platform == "win32" else Path(mac_exe).name).casefold()
            if key == "coccoc":
                matches = matches and "coccoc" in str(path).casefold()
            if matches and key not in found:
                found[key] = InstalledBrowser(key, name, family, path.resolve())
                break
    return list(found.values())


class BrowserConnectionError(RuntimeError):
    """Sanitized protocol failures must not disclose a document query."""


class ProfileLock:
    def __init__(self, directory: Path) -> None:
        self.file: BinaryIO | None = None
        directory.mkdir(parents=True, exist_ok=True)
        handle = (directory / "pdftranslate.lock").open("a+b")
        try:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise BrowserConnectionError("AppBrowserProfileBusy") from None
        self.file = handle

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None


class Protocol:
    def __init__(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1":
            raise BrowserConnectionError("InvalidLocalBrowserEndpoint")
        self.socket = websocket.create_connection(
            url, timeout=3, suppress_origin=True, http_no_proxy=["127.0.0.1", "localhost"],
        )
        self.sequence = 0

    def call(self, method: str, params: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        self.sequence += 1
        request_id = self.sequence
        self.socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}, **extra}))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            message = json.loads(self.socket.recv())
            if message.get("id") != request_id:
                continue
            if "error" in message or message.get("type") == "error":
                raise BrowserConnectionError("BrowserCommandFailed:" + method)
            return message.get("result", {})
        raise BrowserConnectionError("BrowserCommandTimeout:" + method)

    def close(self) -> None:
        self.socket.close()


class InstalledBrowserWindow:
    """Control only a browser instance launched with our own profile."""

    def __init__(self, browser: InstalledBrowser, storage: Path, hidden: bool = True) -> None:
        self.browser = browser
        self.profile = (storage / "installed" / browser.key).resolve()
        self.lock: ProfileLock | None = None
        self.process: subprocess.Popen | None = None
        self.protocol: Protocol | None = None
        self.context = ""
        self.session = ""
        self.window: Any = None
        self.hidden = hidden

    def start(self, timeout: float = 10) -> None:
        self.lock = ProfileLock(self.profile)
        options: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 7 if self.hidden else 1  # SW_SHOWMINNOACTIVE / SW_SHOWNORMAL
            options["startupinfo"] = startup
        if self.browser.family == "chromium":
            (self.profile / "DevToolsActivePort").unlink(missing_ok=True)
            args = [str(self.browser.executable), f"--user-data-dir={self.profile}",
                    "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                    "--no-first-run", "--no-default-browser-check", "--disable-background-mode",
                    "--disable-session-crashed-bubble", "about:blank"]
            if self.hidden:
                args.insert(-1, "--start-minimized")
            endpoint = ""
        else:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            args = [str(self.browser.executable), "-no-remote", "-profile", str(self.profile),
                    "--remote-debugging-port", str(port), "about:blank"]
            endpoint = f"ws://127.0.0.1:{port}/session"
        self.process = subprocess.Popen(args, **options)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise BrowserConnectionError("AppBrowserExited")
            try:
                if self.browser.family == "chromium":
                    lines = (self.profile / "DevToolsActivePort").read_text().splitlines()
                    port = int(lines[0])
                    if not lines[1].startswith("/devtools/browser/"):
                        raise BrowserConnectionError("InvalidLocalBrowserEndpoint")
                    endpoint = f"ws://127.0.0.1:{port}{lines[1]}"
                self.protocol = Protocol(endpoint)
                break
            except (OSError, ValueError, IndexError, websocket.WebSocketException):
                time.sleep(0.2)
        else:
            raise BrowserConnectionError("AppBrowserStartupTimeout")
        if self.browser.family == "chromium":
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                targets = self.protocol.call("Target.getTargets")["targetInfos"]
                pages = sorted((target for target in targets if target["type"] == "page"),
                               key=lambda target: target.get("url") != "about:blank")
                for target in pages:
                    try:
                        window = self.protocol.call("Browser.getWindowForTarget", {"targetId": target["targetId"]})
                    except BrowserConnectionError:
                        continue  # Startup/onboarding can expose pages without a window.
                    self.context, self.window = target["targetId"], window["windowId"]
                    break
                if self.context:
                    break
                time.sleep(0.2)
            else:
                raise BrowserConnectionError("AppBrowserWindowUnavailable")
            self.session = self.protocol.call("Target.attachToTarget", {"targetId": self.context, "flatten": True})["sessionId"]
            self.protocol.call("Browser.setDownloadBehavior", {"behavior": "deny"})
        else:
            self.protocol.call("session.new", {"capabilities": {"alwaysMatch": {"acceptInsecureCerts": False}}})
            contexts = self.protocol.call("browsingContext.getTree")["contexts"]
            self.context = contexts[0]["context"]
            self.window = contexts[0].get("clientWindow")
            if self.window is None:
                self.window = self.protocol.call("browser.getClientWindows")["clientWindows"][0]["clientWindow"]
            self.protocol.call("browser.setDownloadBehavior", {"downloadBehavior": {"type": "denied"}})
        self.navigate("data:text/html,<title>Google Translate - PDF Translate</title><p>PDF Translate</p>")
        # Probe both evaluation and window controls before sending document text.
        self.page_state("JSON.stringify({url:location.href})")
        self.hide() if self.hidden else self.show()

    def navigate(self, url: str) -> None:
        assert self.protocol is not None
        if self.browser.family == "chromium":
            result = self.protocol.call("Page.navigate", {"url": url}, sessionId=self.session)
            if result.get("errorText"):
                raise BrowserConnectionError("AppBrowserNavigationFailed")
        else:
            self.protocol.call("browsingContext.navigate", {"context": self.context, "url": url, "wait": "none"})

    def page_state(self, expression: str) -> dict[str, Any]:
        assert self.protocol is not None
        if self.browser.family == "chromium":
            response = self.protocol.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, sessionId=self.session)
            if "exceptionDetails" in response:
                return {}
        else:
            response = self.protocol.call("script.evaluate", {"expression": expression, "target": {"context": self.context}, "awaitPromise": False})
            if response.get("type") == "exception":
                return {}
        value = response.get("result", {}).get("value")
        return json.loads(value) if isinstance(value, str) else {}

    def show(self) -> None:
        assert self.protocol is not None
        if self.browser.family == "chromium":
            self.protocol.call("Browser.setWindowBounds", {"windowId": self.window, "bounds": {"windowState": "normal"}})
            self.protocol.call("Target.activateTarget", {"targetId": self.context})
        else:
            self.protocol.call("browser.setClientWindowState", {"clientWindow": self.window, "state": "normal"})
            self.protocol.call("browsingContext.activate", {"context": self.context})

    def hide(self) -> None:
        assert self.protocol is not None
        if self.browser.family == "chromium":
            self.protocol.call("Browser.setWindowBounds", {"windowId": self.window, "bounds": {"windowState": "minimized"}})
        else:
            self.protocol.call("browser.setClientWindowState", {"clientWindow": self.window, "state": "minimized"})

    def is_open(self) -> bool:
        """Distinguish navigation's replaced script context from a closed tab."""
        assert self.protocol is not None
        if self.browser.family == "chromium":
            targets = self.protocol.call("Target.getTargets")["targetInfos"]
            return any(target["targetId"] == self.context for target in targets)
        contexts = self.protocol.call("browsingContext.getTree")["contexts"]
        return any(context["context"] == self.context for context in contexts)

    def close(self) -> None:
        if self.protocol is not None:
            try:
                self.protocol.call("Browser.close" if self.browser.family == "chromium" else "browser.close")
            except (OSError, ValueError, BrowserConnectionError, websocket.WebSocketException):
                pass
            try:
                self.protocol.close()
            except (OSError, websocket.WebSocketException):
                pass
            self.protocol = None
        if self.process is not None:
            try:
                self.process.wait(3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None
        if self.lock is not None:
            self.lock.close()
            self.lock = None


def start_installed_browser(storage: Path, hidden: bool) -> InstalledBrowserWindow | None:
    deadline = time.monotonic() + 30
    for browser in discover_browsers():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        window = InstalledBrowserWindow(browser, storage, hidden)
        try:
            window.start(timeout=min(10, remaining))
            return window
        except Exception:
            # Startup only: never switch browsers in response to a Google refusal.
            window.close()
    return None
