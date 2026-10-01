from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pdf2zh.installed_browser import (
    BrowserConnectionError, InstalledBrowser, InstalledBrowserWindow, ProfileLock,
    Protocol, _command_executable, discover_browsers, start_installed_browser,
)
from pdf2zh.google_browser import ENDPOINT, _serve_auto_browser
from urllib.parse import urlencode


class BrowserDiscoveryTests(unittest.TestCase):
    def test_macos_default_firefox_in_a_custom_folder_is_detected(self):
        executable = Path("/Volumes/Tools/Firefox.app/Contents/MacOS/firefox")
        workspace = SimpleNamespace(URLForApplicationToOpenURL_=lambda _url: SimpleNamespace(path=lambda: "/Volumes/Tools/Firefox.app"))
        appkit = SimpleNamespace(NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace))
        foundation = SimpleNamespace(NSURL=SimpleNamespace(URLWithString_=lambda url: url))
        with (
            patch("pdf2zh.installed_browser.sys.platform", "darwin"),
            patch.dict("sys.modules", {"AppKit": appkit, "Foundation": foundation}),
            patch.object(Path, "is_file", lambda path: path == executable),
            patch("pdf2zh.installed_browser.shutil.which", return_value=None),
        ):
            browsers = discover_browsers()
        self.assertEqual([browser.key for browser in browsers], ["firefox"])
        self.assertEqual(browsers[0].executable, executable.resolve())

    def test_default_firefox_wins_over_chromium_and_duplicates_are_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chrome, firefox = root / "chrome.exe", root / "firefox.exe"
            chrome.touch()
            firefox.touch()
            with (
                patch("pdf2zh.installed_browser.sys.platform", "win32"),
                patch("pdf2zh.installed_browser._windows_registry_paths", return_value=([chrome, firefox, chrome], firefox)),
                patch("pdf2zh.installed_browser.shutil.which", return_value=None),
                patch.dict("os.environ", {"LOCALAPPDATA": "", "PROGRAMFILES": "", "PROGRAMFILES(X86)": ""}),
            ):
                self.assertEqual([item.key for item in discover_browsers()], ["firefox", "chrome"])

    def test_brave_and_coccoc_are_recognized_but_unknown_browser_exes_are_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / "brave.exe", root / "CocCoc" / "browser.exe", root / "Unknown" / "browser.exe"]
            for path in paths:
                path.parent.mkdir(exist_ok=True)
                path.touch()
            with (
                patch("pdf2zh.installed_browser.sys.platform", "win32"),
                patch("pdf2zh.installed_browser._windows_registry_paths", return_value=(paths, None)),
                patch("pdf2zh.installed_browser.shutil.which", return_value=None),
                patch.dict("os.environ", {"LOCALAPPDATA": "", "PROGRAMFILES": "", "PROGRAMFILES(X86)": ""}),
            ):
                browsers = discover_browsers()
                self.assertEqual([item.key for item in browsers], ["brave", "coccoc"])
                self.assertTrue(all(item.family == "chromium" for item in browsers))

    def test_default_browser_command_is_a_path_not_a_shell_command(self):
        self.assertEqual(_command_executable('"C:\\Program Files\\Mozilla Firefox\\firefox.exe" -osint -url "%1"'),
                         Path(r"C:\Program Files\Mozilla Firefox\firefox.exe"))
        self.assertIsNone(_command_executable("https://example.com"))

    def test_profile_can_be_reopened_but_cannot_be_shared_by_two_app_instances(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary)
            first = ProfileLock(profile)
            try:
                with self.assertRaises(BrowserConnectionError):
                    ProfileLock(profile)
            finally:
                first.close()
            ProfileLock(profile).close()

    def test_startup_failure_closes_only_failed_candidate_and_tries_next(self):
        browsers = [InstalledBrowser("firefox", "Firefox", "firefox", Path("firefox.exe")),
                    InstalledBrowser("brave", "Brave", "chromium", Path("brave.exe"))]
        failed, working = Mock(), Mock()
        failed.start.side_effect = BrowserConnectionError("BrowserUnsupported")
        with (
            patch("pdf2zh.installed_browser.discover_browsers", return_value=browsers),
            patch("pdf2zh.installed_browser.InstalledBrowserWindow", side_effect=[failed, working]),
        ):
            self.assertIs(start_installed_browser(Path("app-profile"), True), working)
        failed.close.assert_called_once()
        working.close.assert_not_called()


class BrowserProtocolTests(unittest.TestCase):
    def test_events_and_other_replies_cannot_answer_a_command(self):
        socket = Mock()
        socket.recv.side_effect = [json.dumps({"method": "load", "params": {}}),
                                  json.dumps({"id": 8, "result": {"wrong": True}}),
                                  json.dumps({"id": 1, "result": {"right": True}})]
        with patch("pdf2zh.installed_browser.websocket.create_connection", return_value=socket):
            protocol = Protocol("ws://127.0.0.1:9000/session")
            self.assertEqual(protocol.call("session.new"), {"right": True})

    def test_protocol_cannot_connect_to_remote_hosts(self):
        for url in ("ws://example.org:9000/session", "wss://127.0.0.1:9000/session", "ws://127.0.0.1.evil:9000/session"):
            with self.subTest(url=url), self.assertRaises(BrowserConnectionError):
                Protocol(url)

    def test_protocol_errors_do_not_disclose_document_text(self):
        socket = Mock()
        socket.recv.return_value = json.dumps({"id": 1, "error": {"message": "private document words"}})
        with patch("pdf2zh.installed_browser.websocket.create_connection", return_value=socket):
            protocol = Protocol("ws://127.0.0.1:9000/session")
            with self.assertRaises(BrowserConnectionError) as error:
                protocol.call("Page.navigate", {"url": "private document words"})
            self.assertNotIn("private document", str(error.exception))

    def test_firefox_evaluates_bidi_string_values_and_uses_its_own_window(self):
        browser = InstalledBrowser("firefox", "Firefox", "firefox", Path("firefox.exe"))
        window = InstalledBrowserWindow(browser, Path("tmp/test-profiles"))
        window.protocol = Mock()
        window.context, window.window = "own-tab", "own-window"
        window.protocol.call.return_value = {"type": "success", "result": {"type": "string", "value": '{"result":"Answer"}'}}
        self.assertEqual(window.page_state("JSON.stringify({result:'Answer'})"), {"result": "Answer"})
        window.show()
        self.assertIn(unittest.mock.call("browser.setClientWindowState", {"clientWindow": "own-window", "state": "normal"}),
                      window.protocol.call.call_args_list)
        window.hide()
        window.protocol.call.assert_called_with("browser.setClientWindowState", {"clientWindow": "own-window", "state": "minimized"})


class BrowserWorkerTests(unittest.TestCase):
    def test_verification_waits_in_same_browser_and_resumes_without_reloading(self):
        params = {"q": "Hello", "sl": "en", "tl": "vi"}
        connection = Mock()
        # Job, verification page, manual show, result, close.
        connection.poll.side_effect = [True, False, True, True]
        connection.recv.side_effect = [{"kind": "translate", "params": params}, {"kind": "show"}, {"kind": "close"}]
        window = Mock()
        window.browser.name = "Firefox"
        window.page_state.side_effect = [{"url": "https://www.google.com/sorry/index"},
                                        {"url": ENDPOINT + "?" + urlencode(params), "result": "Xin chào"}]
        with patch("pdf2zh.installed_browser.start_installed_browser", return_value=window) as startup:
            _serve_auto_browser(connection, "app-profile", True)
        startup.assert_called_once()
        window.navigate.assert_called_once_with(ENDPOINT + "?" + urlencode(params))
        self.assertEqual(connection.send.call_args_list,
                         [unittest.mock.call({"kind": "ready", "browser": "Firefox"}),
                          unittest.mock.call({"kind": "verification"}),
                          unittest.mock.call({"kind": "result", "text": "Xin chào"})])
        self.assertEqual(window.show.call_count, 2)
        window.hide.assert_called_once()
        window.close.assert_called_once()

    def test_missing_installed_browsers_uses_embedded_fallback(self):
        connection = Mock()
        with (
            patch("pdf2zh.installed_browser.start_installed_browser", return_value=None),
            patch("pdf2zh.google_browser._serve_browser") as fallback,
        ):
            _serve_auto_browser(connection, "app-profile", True)
        fallback.assert_called_once_with(connection, "app-profile", True)

    def test_closed_verification_tab_stops_without_another_navigation(self):
        connection = Mock()
        connection.poll.side_effect = [True, False]
        connection.recv.return_value = {"kind": "translate", "params": {"q": "Hello", "sl": "en", "tl": "vi"}}
        window = Mock()
        window.page_state.side_effect = BrowserConnectionError("BrowserCommandFailed:Runtime.evaluate")
        window.is_open.return_value = False
        with patch("pdf2zh.installed_browser.start_installed_browser", return_value=window):
            _serve_auto_browser(connection, "app-profile", True)
        window.navigate.assert_called_once()
        connection.send.assert_called_with({"kind": "error"})
        window.close.assert_called_once()

    def test_replaced_script_context_waits_for_current_tab_without_reloading(self):
        params = {"q": "Hello", "sl": "en", "tl": "vi"}
        connection = Mock()
        connection.poll.side_effect = [True, False, False, True]
        connection.recv.side_effect = [{"kind": "translate", "params": params}, {"kind": "close"}]
        window = Mock()
        window.is_open.return_value = True
        window.page_state.side_effect = [BrowserConnectionError("BrowserCommandFailed:Runtime.evaluate"),
                                        {"url": ENDPOINT + "?" + urlencode(params), "result": "Xin chào"}]
        with patch("pdf2zh.installed_browser.start_installed_browser", return_value=window):
            _serve_auto_browser(connection, "app-profile", True)
        window.navigate.assert_called_once()
        self.assertIn(unittest.mock.call({"kind": "result", "text": "Xin chào"}), connection.send.call_args_list)


if __name__ == "__main__":
    unittest.main()
