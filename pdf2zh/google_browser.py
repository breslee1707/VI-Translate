"""A visible, human-verifiable Google session isolated from the Tk event loop."""

from __future__ import annotations

import atexit
import html
import multiprocessing
import os
import sys
import threading
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

ENDPOINT = "https://translate.google.com/m"
HUMAN_VERIFICATION_TIMEOUT = 600.0
PAGE_STATE_JS = """(() => {
    const result = document.querySelector('.result-container, .t0');
    return {url: location.href, result: result ? result.textContent : null,
            body: result ? '' : (document.body ? document.body.innerText : '')};
})()"""


def page_verdict(state: dict[str, Any], params: dict[str, str]) -> tuple[str, str]:
    """Accept only the current query's visible result; leave CAPTCHA to the user."""
    url = urlparse(state.get("url", ""))
    body = str(state.get("body", "")).lower()
    if url.hostname in ("www.google.com", "google.com") and url.path.startswith("/sorry/"):
        return "verification", ""
    if state.get("result") is None and "our systems have detected unusual traffic" in body:
        return "verification", ""
    if url.hostname != "translate.google.com" or url.path != "/m":
        return "loading", ""
    query = parse_qs(url.query, keep_blank_values=True)
    if any(query.get(key) != [params[key]] for key in ("q", "sl", "tl")):
        return "loading", ""
    result = state.get("result")
    if isinstance(result, str) and result.strip():
        return "result", result
    return "loading", ""


def _serve_browser(connection: Connection, storage: str, hidden: bool) -> None:
    """Run WebView2 on its own main thread; never automate a verification control."""
    try:
        import webview

        closed = threading.Event()
        loaded = threading.Event()
        window = webview.create_window(
            "Google Dịch — xác minh trong cửa sổ này nếu được yêu cầu",
            html="<!doctype html><html><body>Google Dịch</body></html>",
            width=820, height=640, hidden=hidden,
        )
        window.events.loaded += loaded.set
        window.events.closed += closed.set

        def serve() -> None:
            try:
                if not loaded.wait(30):
                    connection.send({"kind": "error", "reason": "InitialPageTimeout"})
                    return
                connection.send({"kind": "ready"})
                while not closed.is_set():
                    if not connection.poll(0.5):
                        continue
                    job = connection.recv()
                    if job.get("kind") == "close":
                        return
                    if job.get("kind") == "show":
                        window.show()
                        continue
                    params = job["params"]
                    loaded.clear()
                    window.load_url(ENDPOINT + "?" + urlencode(params))
                    deadline = time.monotonic() + HUMAN_VERIFICATION_TIMEOUT
                    verification_reported = False
                    while not closed.is_set() and time.monotonic() < deadline:
                        if connection.poll(0):
                            control = connection.recv().get("kind")
                            if control == "close":
                                return
                            if control == "show":
                                window.show()
                        if not loaded.wait(0.5):
                            continue
                        state = window.evaluate_js(PAGE_STATE_JS)
                        if not isinstance(state, dict):
                            closed.wait(0.5)
                            continue
                        verdict, text = page_verdict(state, params)
                        if verdict == "result":
                            window.hide()
                            connection.send({"kind": "result", "text": text})
                            break
                        if verdict == "verification" and not verification_reported:
                            connection.send({"kind": "verification"})
                            verification_reported = True
                            if job.get("show_on_verification", True):
                                window.show()
                        # Only inspect this page while the user verifies it.
                        # No reloads, new translation requests, CAPTCHA clicks or answers.
                        closed.wait(0.5)
                    else:
                        connection.send({"kind": "error"})
                        return
            except (EOFError, OSError):
                pass
            except Exception:
                # A native/browser exception may include the query's document text.
                try:
                    connection.send({"kind": "error"})
                except OSError:
                    pass
            finally:
                if not closed.is_set():
                    window.destroy()

        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["ALLOW_DOWNLOADS"] = False
        engine = "edgechromium" if sys.platform == "win32" else "cocoa"
        webview.start(serve, gui=engine, private_mode=False, storage_path=storage)
    except Exception as error:
        try:
            connection.send({"kind": "error", "reason": type(error).__name__})
        except OSError:
            pass
    finally:
        connection.close()


class BrowserBackend:
    """Keep one private app profile across documents without reading Edge's profile."""

    def __init__(self, *, storage: Path | None = None, hidden: bool = True) -> None:
        self.storage = storage or Path(os.path.expanduser("~")) / ".cache/pdf2zh/google-browser"
        self.hidden = hidden
        self._process: Any = None
        self._connection: Connection | None = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._verification_deferred = False

    def _unavailable(self, reason: str = "") -> Exception:
        from pdf2zh.translator import ServiceUnavailableError, VerificationDeferredError

        if self._verification_deferred:
            return VerificationDeferredError("Google verification was deferred by the user")

        return ServiceUnavailableError(
            "Google browser session was closed, could not start, or verification did not finish. "
            + (f" ({reason})" if reason else "")
        )

    def start(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self.close()
        self._verification_deferred = False
        self.storage.mkdir(parents=True, exist_ok=True)
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        self._connection = parent
        self._process = context.Process(
            target=_serve_browser, args=(child, str(self.storage), self.hidden), daemon=True,
        )
        self._process.start()
        child.close()
        try:
            answer = parent.recv() if parent.poll(45) else {"kind": "error", "reason": "StartupTimeout"}
            if answer.get("kind") != "ready":
                self.close()
                from pdf2zh.translator import BrowserRuntimeError

                raise BrowserRuntimeError(
                    "Could not start the Google verification browser "
                    f"({answer.get('reason', 'StartupError')})"
                )
        except (EOFError, OSError):
            self.close()
            raise self._unavailable() from None

    def send_control(self, kind: str) -> bool:
        """Let the GUI show or close a browser while its worker waits for the user."""
        with self._send_lock:
            if self._connection is None:
                return False
            was_deferred = self._verification_deferred
            if kind == "close":
                self._verification_deferred = True
            try:
                self._connection.send({"kind": kind})
                return True
            except (EOFError, OSError):
                self._verification_deferred = was_deferred
                return False

    def fetch(
        self, params: dict[str, str], on_verification: Callable[[], None],
        *, show_on_verification: bool = True,
    ) -> str:
        with self._lock:
            self.start()
            assert self._connection is not None
            try:
                with self._send_lock:
                    self._connection.send({"kind": "translate", "params": params,
                                           "show_on_verification": show_on_verification})
                deadline = time.monotonic() + HUMAN_VERIFICATION_TIMEOUT + 15
                while time.monotonic() < deadline:
                    if not self._connection.poll(0.5):
                        if not self._process.is_alive():
                            raise self._unavailable()
                        continue
                    answer = self._connection.recv()
                    if self._verification_deferred:
                        raise self._unavailable()
                    if answer.get("kind") == "result":
                        return answer["text"]
                    if answer.get("kind") == "verification":
                        on_verification()
                        continue
                    raise self._unavailable()
            except (EOFError, OSError):
                raise self._unavailable() from None
            raise self._unavailable()

    def close(self) -> None:
        process, connection = self._process, self._connection
        self._process = self._connection = None
        if connection is not None:
            try:
                connection.send({"kind": "close"})
            except (EOFError, OSError):
                pass
            connection.close()
        if process is not None:
            process.join(3)
            if process.is_alive():
                process.terminate()
                process.join(3)


BACKEND = BrowserBackend()
atexit.register(BACKEND.close)


class BrowserSession:
    """Adapt a human-verified browser result to the existing guarded translator."""

    def __init__(
        self, on_verification: Callable[[], None], *, show_on_verification: bool = True,
    ) -> None:
        self.on_verification = on_verification
        self.show_on_verification = show_on_verification

    def get(self, endpoint: str, *, params: dict[str, str], **_: Any) -> requests.Response:
        if endpoint != ENDPOINT:
            raise ValueError("The Google browser transport accepts only the translation endpoint")
        text = BACKEND.fetch(params, self.on_verification,
                             show_on_verification=self.show_on_verification)
        response = requests.Response()
        response.status_code = 200
        response.url = ENDPOINT
        response.encoding = "utf-8"
        response._content = ('<div class="result-container">' + html.escape(text) + '</div>').encode("utf-8")
        return response


def verify_browser_runtime() -> None:
    backend = BrowserBackend(hidden=True)
    try:
        backend.start()
    finally:
        backend.close()
