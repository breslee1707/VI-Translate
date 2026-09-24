"""Translation adapters for the preservation-focused PDF core."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, ClassVar
from urllib.parse import quote_plus, urlparse

import requests

from pdf2zh.cache import TranslationCache

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(r"</?b\d+>")
INTERNAL_PLACEHOLDER_PATTERN = re.compile(r"\{\s*v([\d\s]+)\}", re.IGNORECASE)
PAIRED_PLACEHOLDER_PATTERN = re.compile(r"<b(\d+)></b\1>")
STYLE_TAG_PATTERN = re.compile(r"<(/?)s([123])>", re.IGNORECASE)
COMMON_PUNCTUATION_MOJIBAKE = (
    ("\u00e2\u20ac\u201c", "\u2013"),  # UTF-8 en dash decoded as Windows-1252
    ("\u00e2\u20ac\u201d", "\u2014"),  # UTF-8 em dash decoded as Windows-1252
    ("\u00c2\u00a9", "\u00a9"),
    ("\u00c2\u00ae", "\u00ae"),
    ("\u00c2\u00b0", "\u00b0"),
    ("\u00c2\u00b1", "\u00b1"),
    ("\u00c2\u00b5", "\u00b5"),
)


class FormulaPlaceholderError(ValueError):
    """Raised when a translator damages or reorders protected formula tags."""


class SegmentTooLongError(ValueError):
    """Raised when a segment exceeds what the translation service accepts.

    Upstream truncates to the limit and returns the short answer as if it were
    the whole translation, so the tail of a long paragraph disappears with
    nothing said. A segment carrying formula or style markers is caught later
    by the marker check, but plain prose is silently cut in half. Refusing the
    segment keeps the source text and lets the caller say what happened.
    """


class SegmentRejectedError(ValueError):
    """Raised when the service refuses one segment outright (HTTP 400).

    The same text gets the same answer however often it is sent, so waiting
    to send it again only holds up the page.
    """


class RateLimitedError(RuntimeError):
    """Raised when Google refuses this network rather than this segment.

    A heavy run is redirected to Google's CAPTCHA page and answered HTTP 429.
    That verdict is on the address, so every later segment meets it too.
    """


class BatchMismatchError(RuntimeError):
    """Raised when a batch does not come back one translated line per segment.

    Sending the same batch again would only repeat the answer; halving it finds
    the segment that did not keep to its own line.
    """


class ServiceUnavailableError(RuntimeError):
    """Raised when the service gives no usable answer: no connection, a timeout, a 5xx."""


class InvalidResponseError(RuntimeError):
    """A successful HTTP response without usable translated text."""


# Sending these again repeats a verdict, or a wait the whole document already paid.
UNRETRYABLE_ERRORS = (
    SegmentTooLongError,
    SegmentRejectedError,
    RateLimitedError,
    ServiceUnavailableError,
    BatchMismatchError,
)


class OutageBackoff:
    """Wait out an unreachable service once per document, not per segment.

    Each segment used to retry on its own: eight attempts and about two minutes
    of backoff, on four threads at once, so a 1944-segment book whose network
    dropped would have spent some sixteen hours retrying. Here the workers hold
    back together for a doubling pause. After PATIENCE seconds without an
    answer each remaining segment is refused at once, and one request per pause
    still checks whether the service is back. A block is not an outage and is
    never waited out this way: see NetworkBlock.
    """

    FIRST_PAUSE = 5.0
    LONGEST_PAUSE = 60.0
    PATIENCE = 120.0

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._pause = self.FIRST_PAUSE
        self._resume_at = float("-inf")
        self._outage_began: float | None = None
        # Counts recorded failures. A request carries the count it started
        # under, so an answer to something sent before the latest failure says
        # nothing new: four workers hitting one block pause once, not four
        # times, and a reply that was already on its way does not end it.
        self._failures = 0
        self._failure: RateLimitedError | ServiceUnavailableError | None = None

    def before_request(self) -> int:
        """Hold a request while a pause is in force, then return the count it starts under.

        Once patience has run out the outage is raised instead, except for the
        single request per pause that checks whether it is over.
        """
        while True:
            with self._lock:
                now = self._clock()
                exhausted = (
                    self._outage_began is not None
                    and now - self._outage_began >= self.PATIENCE
                )
                if now >= self._resume_at:
                    if exhausted:
                        self._resume_at = now + self._pause
                    return self._failures
                if exhausted:
                    raise type(self._failure)(str(self._failure))
                delay = self._resume_at - now
            self._sleep(delay)

    def failed(self, started: int, error: RateLimitedError | ServiceUnavailableError) -> None:
        """Record a request that met the outage, and pause every worker."""
        with self._lock:
            self._failure = error
            if started < self._failures:
                return
            now = self._clock()
            if self._outage_began is None:
                self._outage_began = now
            self._failures += 1
            self._resume_at = now + self._pause
            self._pause = min(self._pause * 2, self.LONGEST_PAUSE)

    def succeeded(self, started: int) -> None:
        """Record an answer to a request sent since the latest failure."""
        with self._lock:
            if started < self._failures:
                return
            self._outage_began = None
            self._failure = None
            self._pause = self.FIRST_PAUSE
            self._resume_at = float("-inf")


class NetworkBlock:
    """Remember that Google refused this network, and stop asking while it does.

    A block does not lift while requests keep arriving, and version 0.3.0 kept
    them arriving: each blocked segment was retried eight times on four
    threads for as long as the document ran. Once refused, requests stop at
    once - for the rest of the document, the rest of the queue and the next
    run, since the moment is written to disk - and a single request per
    RECHECK_AFTER seconds asks whether the block has lifted.
    """

    RECHECK_AFTER = 600.0

    def __init__(
        self,
        path: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._loaded = path is None
        self._since: float | None = None

    def before_request(self) -> None:
        """Refuse a request while a block is fresh, letting one through per interval."""
        with self._lock:
            self._load()
            if self._since is None:
                return
            now = self._clock()
            # A clock set back would otherwise hold requests back indefinitely.
            if 0 <= now - self._since < self.RECHECK_AFTER:
                raise RateLimitedError(
                    "Google Translate refused this network moments ago; no request is "
                    "sent until the block has had time to lift"
                )
            # This request is the check; any other waits for its answer.
            self._since = now
            self._save()

    def check_available(self) -> None:
        """Check a cooldown without reserving its single recovery request."""
        with self._lock:
            self._load()
            if self._since is not None and 0 <= self._clock() - self._since < self.RECHECK_AFTER:
                raise RateLimitedError("Google Translate is cooling down after refusing this network")

    def refused(self) -> None:
        """Record that Google has just refused this network."""
        with self._lock:
            self._loaded = True
            self._since = self._clock()
            self._save()

    def answered(self) -> None:
        """Record a real answer: the block, if there was one, is over."""
        with self._lock:
            if self._since is None:
                return
            self._since = None
            self._save()

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(self._path, encoding="utf-8") as stream:
                self._since = float(json.load(stream)["blocked_at"])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as error:
            logger.debug("Ignoring unreadable Google block record: %s", error)

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            if self._since is None:
                if os.path.exists(self._path):
                    os.remove(self._path)
                return
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as stream:
                json.dump({"blocked_at": self._since}, stream)
        except OSError as error:
            logger.debug("Could not record the Google block: %s", error)


class RequestPace:
    """Send one request at a time, at least GAP seconds after the last answer.

    Worker threads used to fire four requests at once, one per segment, which
    is the traffic Google's abuse check looks for. Every translator in the
    process shares one pace, so a queue of documents cannot overlap either.
    """

    GAP = 1.0

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._ready_at = float("-inf")

    @contextmanager
    def turn(self) -> Iterator[None]:
        """Hold the only request slot, after waiting out the gap."""
        with self._lock:
            delay = self._ready_at - self._clock()
            if delay > 0:
                self._sleep(delay)
            try:
                yield
            finally:
                self._ready_at = self._clock() + self.GAP


GOOGLE_BLOCK = NetworkBlock(
    os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh", "google-block.json")
)
GOOGLE_PACE = RequestPace()


def is_google_block(response: requests.Response) -> bool:
    """Whether Google answered with its verdict on the network, not a translation."""
    if response.status_code == 429:
        return True
    if urlparse(response.url or "").path.startswith("/sorry/"):
        return True
    body = response.text.lower()
    return not BATCH_RESULT_PATTERN.search(response.text) and (
        "our systems have detected unusual traffic" in body or "g-recaptcha" in body
    )


BATCH_RESULT_PATTERN = re.compile(
    r'(?s)class="(?:t0|result-container)">((?:[^<]|<br\s*/?>)*)', re.IGNORECASE
)
# Every character str.splitlines() breaks at.
LINE_BREAK_PATTERN = re.compile("[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def result_lines(page: str) -> list[str]:
    """The non-empty translated lines of an answer, whether broken by newline or <br>."""
    match = BATCH_RESULT_PATTERN.search(page)
    if match is None:
        raise InvalidResponseError("Google Translate response did not contain a translation result")
    text = html.unescape(re.sub(r"(?i)<br\s*/?>", "\n", match.group(1)))
    lines = (remove_control_characters(line).strip() for line in text.splitlines())
    result = [line for line in lines if line]
    if not result:
        raise InvalidResponseError("Google Translate returned an empty translation")
    return result


def remove_control_characters(value: str) -> str:
    """Remove control characters that cannot be emitted safely into PDF text."""
    return "".join(character for character in value if unicodedata.category(character)[0] != "C")


def repair_common_punctuation_mojibake(value: str) -> str:
    """Repair only unambiguous punctuation damaged while moving JSONL text."""
    for damaged, repaired in COMMON_PUNCTUATION_MOJIBAKE:
        value = value.replace(damaged, repaired)
    return value


def _decoded_as_windows_1252(value: str) -> str | None:
    """Return the UTF-8 text this string would be, if it is mojibake at all."""
    raw = bytearray()
    for character in value:
        try:
            raw += character.encode("cp1252")
        except UnicodeEncodeError:
            # cp1252 leaves 0x81, 0x8d, 0x8f, 0x90 and 0x9d undefined. A lenient
            # decoder passes those bytes through, so they arrive as C1 controls.
            if ord(character) >= 0x100:
                return None
            raw.append(ord(character))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def looks_like_mojibake(value: str) -> bool:
    """Report text that is UTF-8 read as Windows-1252, without repairing it.

    Searching for marker characters is wrong here: Â and Ã are ordinary
    Vietnamese letters, so PHÂN would be condemned. Re-encoding the whole
    string succeeds only when every character came from that one mistake, which
    real Vietnamese does not survive because its tone marks live outside
    Latin-1. Repairing is still refused: guessing a letter sequence is how a
    wrong word reaches the page looking correct.
    """
    decoded = _decoded_as_windows_1252(value)
    return decoded is not None and decoded != value


def has_unrepairable_mojibake(value: str) -> bool:
    """Damage that survives the punctuation repair, so whole letters are wrong.

    Testing the repaired text is not enough. Turning one damaged dash back into
    an en dash restores a byte that cannot begin a UTF-8 sequence, which hides
    the damaged letters standing around it; ten of the records that first
    exposed this defect were masked exactly that way. Compare what the record
    really said against what the safe repair managed to recover instead.
    """
    decoded = _decoded_as_windows_1252(value)
    return decoded is not None and decoded != repair_common_punctuation_mojibake(value)


NUMBER_ABBREVIATION_PATTERN = re.compile(r"(?<![A-Za-z])no\.(?=\s*\d)")


def normalise_number_abbreviation(text: str) -> str:
    """Capitalise the ``no.`` that means "number" so it is not read as "not".

    "ref. no. 305" came back as "ref. KHONG. 305": lowercase "no." mid-sentence
    reads as the negation, and every engine we can reach makes the same choice.
    The same string capitalised is unambiguous -- "No. 305" translates to
    "So 305" -- and capitalising an abbreviation that already stands for a
    proper noun changes nothing else about the sentence.

    Only ``no.`` directly in front of a number is touched, so ordinary prose
    ("there is no. Then...") is left alone.
    """
    return NUMBER_ABBREVIATION_PATTERN.sub("No.", text)


class BaseTranslator:
    """Cache-aware translator interface consumed by the PDF converter."""

    name = "base"
    lang_map: ClassVar[dict[str, str]] = {}

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str | None = None,
        *,
        ignore_cache: bool = False,
        **_: Any,
    ) -> None:
        self.lang_in = self.lang_map.get(lang_in.lower(), lang_in)
        self.lang_out = self.lang_map.get(lang_out.lower(), lang_out)
        self.model = model
        self.ignore_cache = ignore_cache
        self.cache = TranslationCache(
            self.name,
            {
                "lang_in": self.lang_in,
                "lang_out": self.lang_out,
                "model": model,
            },
        )

    def translate(self, text: str, ignore_cache: bool = False) -> str:
        """Translate text, consulting the persistent cache unless bypassed."""
        text = normalise_number_abbreviation(text)
        if not (self.ignore_cache or ignore_cache):
            cached = self.cache.get(text)
            if cached is not None:
                return cached
        translated = self.do_translate(text)
        if not (self.ignore_cache or ignore_cache):
            self.cache.set(text, translated)
        return translated

    def do_translate(self, text: str) -> str:
        """Translate one engine-sized text segment."""
        raise NotImplementedError

    def get_rich_text_left_placeholder(self, identifier: int) -> str:
        return f"<b{identifier}>"

    def get_rich_text_right_placeholder(self, identifier: int) -> str:
        return f"</b{identifier}>"

    def get_formular_placeholder(self, identifier: int) -> str:
        return self.get_rich_text_left_placeholder(identifier) + self.get_rich_text_right_placeholder(identifier)


class GoogleTranslator(BaseTranslator):
    """Translate through Google's mobile web endpoint without an API key.

    The endpoint is meant for people, and Google blocks a network that uses it
    like a batch service. Up to 0.3.0 every segment was its own request, four
    at a time, retried eight times once refused: a 500-page book was thousands
    of requests, and the retries kept knocking on a network already blocked.
    A page's segments now travel together, one request at a time, and a
    refusal stops requests instead of repeating them.
    """

    name = "google"
    lang_map: ClassVar[dict[str, str]] = {"zh": "zh-CN"}

    # The /m endpoint carries the text in the query string and rejects more
    # than this; it is the service's limit, not a preference.
    MAXIMUM_SEGMENT_CHARACTERS = 5000
    # A batch is measured as it travels, percent-encoded: a Vietnamese letter
    # takes up to nine bytes of the URL, an English one a single byte.
    BATCH_QUERY_BYTES = 5000
    BATCH_SEGMENTS = 50
    # Google translates each line on its own and keeps the line breaks, so a
    # blank line keeps segments apart without any marker it could translate.
    BATCH_SEPARATOR = "\n\n"
    # Batches that may come back with the wrong number of lines, while none has
    # come back right, before a document sends its segments one by one.
    BATCH_MISSES_ALLOWED = 3
    GLITCH_ATTEMPTS = 3

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str | None = None,
        *,
        ignore_cache: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            lang_in,
            lang_out,
            model,
            ignore_cache=ignore_cache,
            **kwargs,
        )
        self.session = requests.Session()
        self.endpoint = "https://translate.google.com/m"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            )
        }
        # One translator serves every worker thread of a document, so an
        # outage seen by one of them holds back all of them.
        self.outage = OutageBackoff()
        # Shared by every translator in the process, and the block also by the
        # next process: they describe this network, not this document.
        self.pace = GOOGLE_PACE
        self.block = GOOGLE_BLOCK
        self.sleep: Callable[[float], None] = time.sleep
        self._batch_hits = 0
        self._batch_misses = 0
        self._terminal_error: RateLimitedError | ServiceUnavailableError | None = None
        self.stats: Counter[str] = Counter()
        self.on_status: Callable[[str, int, int], None] | None = None

    def _status(self, stage: str) -> None:
        if self.on_status is not None:
            self.on_status(stage, 0, 0)

    def do_translate(self, text: str) -> str:
        if len(text) > self.MAXIMUM_SEGMENT_CHARACTERS:
            raise SegmentTooLongError(
                f"segment of {len(text)} characters exceeds the "
                f"{self.MAXIMUM_SEGMENT_CHARACTERS} the service accepts"
            )
        response = self._fetch(text)
        # HTML line breaks must not silently truncate a paragraph at its first line.
        return " ".join(result_lines(response.text))

    def translate_many(self, texts: Sequence[str]) -> list[str | Exception]:
        """Translate a page's segments in as few requests as the service allows.

        Returns one answer per text, in order: its translation, or the exception
        that kept it from being translated. Cached and repeated segments are
        not sent at all.
        """
        keys = [normalise_number_abbreviation(text) for text in texts]
        answers: dict[str, str | Exception] = {}
        waiting: list[str] = []
        for key in dict.fromkeys(keys):
            cached = None if self.ignore_cache else self.cache.get(key)
            if cached is not None:
                try:
                    restore_formula_placeholders(key, cached)
                except FormulaPlaceholderError:
                    cached = None
            if cached is None:
                waiting.append(key)
            else:
                answers[key] = cached
                self.stats["cache_hits"] += 1
        self.stats["segments"] += len(keys)
        self.stats["duplicates"] += len(keys) - len(set(keys))
        for batch in self._batches(waiting):
            self._translate_batch(batch, answers)
        logger.info("Google translation counters: %s", dict(self.stats))
        return [answers[key] for key in keys]

    @property
    def batching(self) -> bool:
        return self._batch_hits > 0 or self._batch_misses < self.BATCH_MISSES_ALLOWED

    def _batches(self, texts: Sequence[str]) -> Iterator[list[str]]:
        """Group texts into requests, sending alone any that cannot share one."""
        separator = len(quote_plus(self.BATCH_SEPARATOR))
        batch: list[str] = []
        used = 0
        for text in texts:
            size = len(quote_plus(text))
            if size > self.BATCH_QUERY_BYTES or LINE_BREAK_PATTERN.search(text):
                yield [text]
                continue
            if batch and (
                used + separator + size > self.BATCH_QUERY_BYTES
                or len(batch) >= self.BATCH_SEGMENTS
            ):
                yield batch
                batch, used = [], 0
            used = size if not batch else used + separator + size
            batch.append(text)
        if batch:
            yield batch

    def _translate_batch(self, batch: list[str], answers: dict[str, str | Exception]) -> None:
        if self._terminal_error is not None:
            answers.update((text, self._terminal_error) for text in batch)
            return
        if len(batch) == 1 or not self.batching:
            for text in batch:
                answers[text] = self._translate_alone(text)
            return
        try:
            lines = self._retrying(lambda: self._translate_joined(batch))
        except (BatchMismatchError, SegmentRejectedError):
            # A segment that did not keep to its own line, or one the service
            # refused, spoils only its own half.
            self._batch_misses += 1
            self.stats["batch_misses"] += 1
            middle = len(batch) // 2
            self._translate_batch(batch[:middle], answers)
            self._translate_batch(batch[middle:], answers)
            return
        except Exception as error:  # noqa: BLE001 - reported per segment by the caller
            if isinstance(error, (RateLimitedError, ServiceUnavailableError)):
                self._terminal_error = error
            for text in batch:
                answers[text] = error
            return
        self._batch_hits += 1
        self.stats["batch_hits"] += 1
        for text, line in zip(batch, lines):
            try:
                restore_formula_placeholders(text, line)
            except FormulaPlaceholderError as error:
                answers[text] = error
                continue
            answers[text] = line
            if not self.ignore_cache:
                self.cache.set(text, line)

    def _translate_alone(self, text: str) -> str | Exception:
        if self._terminal_error is not None:
            return self._terminal_error
        try:
            translated = self._retrying(lambda: self.do_translate(text))
            restore_formula_placeholders(text, translated)
        except Exception as error:  # noqa: BLE001 - reported per segment by the caller
            if isinstance(error, (RateLimitedError, ServiceUnavailableError)):
                self._terminal_error = error
            return error
        if not self.ignore_cache:
            self.cache.set(text, translated)
        return translated

    def _translate_joined(self, batch: list[str]) -> list[str]:
        response = self._fetch(self.BATCH_SEPARATOR.join(batch))
        lines = result_lines(response.text)
        if len(lines) != len(batch):
            raise BatchMismatchError(
                f"{len(batch)} segments came back as {len(lines)} lines"
            )
        return lines

    def _retrying(self, attempt: Callable[[], Any]) -> Any:
        """Repeat a request only for a glitch in one answer, a couple of times."""
        for number in range(1, self.GLITCH_ATTEMPTS + 1):
            try:
                return attempt()
            except UNRETRYABLE_ERRORS:
                raise
            except InvalidResponseError:
                if number == self.GLITCH_ATTEMPTS:
                    raise ServiceUnavailableError(
                        "Google Translate repeatedly returned no usable translation"
                    ) from None
                self.sleep(float(2 ** (number - 1)))
            except Exception:
                if number == self.GLITCH_ATTEMPTS:
                    raise
                self.sleep(float(2 ** (number - 1)))
        raise AssertionError("unreachable")

    def _fetch(self, text: str) -> requests.Response:
        """Send one request: paced, held back while the service is out, never into a block."""
        while True:
            self.block.check_available()
            started = self.outage.before_request()
            failure: ServiceUnavailableError | None = None
            with self.pace.turn():
                # Both the last check and the refusal verdict belong inside
                # the request lock: a waiting worker must see the first 429.
                self.block.before_request()
                self._status("request")
                self.stats["requests"] += 1
                try:
                    response = self.session.get(
                        self.endpoint,
                        params={"tl": self.lang_out, "sl": self.lang_in, "q": text},
                        headers=self.headers,
                        timeout=30,
                    )
                except (requests.ConnectionError, requests.Timeout) as error:
                    # The exception text carries the request URL, which holds the
                    # document's own words; only its kind is kept.
                    failure = ServiceUnavailableError(
                        f"Google Translate could not be reached ({type(error).__name__})"
                    )
                if failure is None and is_google_block(response):
                    self.block.refused()
                    self.stats["blocks"] += 1
                    raise RateLimitedError(
                        "Google Translate is refusing requests from this network (HTTP 429 or CAPTCHA)"
                    )
                if failure is None and response.status_code == 200 and BATCH_RESULT_PATTERN.search(response.text):
                    self.block.answered()
            if failure is not None:
                self._status("waiting")
                self.outage.failed(started, failure)
                continue
            if response.status_code >= 500:
                self._status("waiting")
                self.outage.failed(
                    started,
                    ServiceUnavailableError(
                        f"Google Translate answered HTTP {response.status_code}"
                    ),
                )
                continue
            self.outage.succeeded(started)
            if response.status_code in (400, 413, 414):
                raise SegmentRejectedError("Google Translate rejected the text segment")
            if response.status_code >= 400:
                # requests.HTTPError includes q= and would expose document text
                # in the support log. An access error must not repeat per segment.
                raise ServiceUnavailableError(f"Google Translate answered HTTP {response.status_code}")
            return response


def placeholders(text: str) -> list[str]:
    """Return the formula placeholder tags in order, e.g. ['<b0>', '</b0>']."""
    return PLACEHOLDER_PATTERN.findall(text)


def encode_formula_placeholders(text: str) -> str:
    """Turn converter-internal ``{vN}`` markers into translator-safe tag pairs."""
    return INTERNAL_PLACEHOLDER_PATTERN.sub(
        lambda match: f"<b{int(match.group(1).replace(' ', ''))}></b{int(match.group(1).replace(' ', ''))}>",
        text,
    )


def restore_formula_placeholders(source: str, translated: str) -> str:
    """Validate translator output and restore its tags to converter markers."""
    encoded_source = encode_formula_placeholders(source)
    if placeholders(encoded_source) != placeholders(translated):
        raise FormulaPlaceholderError("formula placeholders changed during translation")
    validate_style_tags(encoded_source, translated)
    restored = PAIRED_PLACEHOLDER_PATTERN.sub(
        lambda match: f"{{v{match.group(1)}}}", translated
    )
    if PLACEHOLDER_PATTERN.search(restored):
        raise FormulaPlaceholderError("formula placeholder pair is malformed")
    return restored


def _style_tag_counts(text: str) -> Counter[str]:
    """Return balanced style-pair counts, allowing complete pairs to reorder."""
    stack: list[str] = []
    pairs: Counter[str] = Counter()
    for match in STYLE_TAG_PATTERN.finditer(text):
        closing, identifier = match.groups()
        if not closing:
            stack.append(identifier)
            continue
        if not stack or stack[-1] != identifier:
            raise FormulaPlaceholderError("style tags are malformed or cross-nested")
        stack.pop()
        pairs[identifier] += 1
    if stack:
        raise FormulaPlaceholderError("style tags are not closed")
    return pairs


def validate_style_tags(source: str, translated: str) -> None:
    """Require the same balanced bold/italic runs after translation."""
    if _style_tag_counts(source) != _style_tag_counts(translated):
        raise FormulaPlaceholderError("style tags changed during translation")


def load_segment_table(path: str | None) -> dict[str, str]:
    """Load a source-to-translation table from a JSONL file of {"src", "dst"} records.

    Entries whose translation dropped or reordered a formula placeholder are
    skipped, so the next pass re-emits them instead of silently losing a formula.
    """
    if not path:
        return {}
    table: dict[str, str] = {}
    with open(path, encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                source, translation = record["src"], record["dst"]
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError(
                    f"{path} line {number}: expected a JSON object with 'src' and 'dst'"
                ) from error
            if not isinstance(source, str) or not isinstance(translation, str):
                raise ValueError(f"{path} line {number}: 'src' and 'dst' must be strings")
            if not translation:
                continue
            if has_unrepairable_mojibake(translation):
                logger.warning(
                    "%s line %d: 'dst' is UTF-8 text decoded as Windows-1252; "
                    "segment left untranslated",
                    path,
                    number,
                )
                continue
            # Old converter versions emitted {vN}; normalise those records so
            # existing handoff files remain usable with the documented tags.
            source = encode_formula_placeholders(repair_common_punctuation_mojibake(source))
            translation = encode_formula_placeholders(
                repair_common_punctuation_mojibake(translation)
            )
            if placeholders(source) != placeholders(translation):
                logger.warning(
                    "%s line %d: formula placeholders differ between src and dst; "
                    "segment left untranslated",
                    path,
                    number,
                )
                continue
            try:
                validate_style_tags(source, translation)
            except FormulaPlaceholderError:
                logger.warning(
                    "%s line %d: style tags differ between src and dst; "
                    "segment left untranslated",
                    path,
                    number,
                )
                continue
            table[source] = translation
    return table


class HandoffTranslator(BaseTranslator):
    """Translate from a table produced outside the pipeline, such as by an agent.

    Two passes: the first runs with no table and records every segment it could
    not translate, the caller fills those in, and the second runs with the filled
    table to emit the real document.
    """

    name = "handoff"

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str | None = None,
        *,
        ignore_cache: bool = False,
        envs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        # Misses fall through untranslated, so the shared cache must never see them
        # or "translation == original" is memoised for every later run.
        super().__init__(lang_in, lang_out, model, ignore_cache=True, **kwargs)
        envs = envs or {}
        self.table = load_segment_table(envs.get("segments_in"))
        self.misses_path = envs.get("segments_out")
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        if self.misses_path:
            open(self.misses_path, "w", encoding="utf-8").close()

    def do_translate(self, text: str) -> str:
        translation = self.table.get(text)
        if translation is not None:
            return translation
        self._record_miss(text)
        return text

    def _record_miss(self, text: str) -> None:
        """Append one untranslated segment, deduplicated, for the caller to fill in."""
        if not self.misses_path:
            return
        with self._lock:
            if text in self._seen:
                return
            self._seen.add(text)
            with open(self.misses_path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps({"src": text}, ensure_ascii=False) + "\n")


class OpenAITranslator(BaseTranslator):
    """Translate via OpenAI-compatible Chat Completions API (OpenAI, OpenRouter, DeepSeek, Ollama)."""

    name = "openai"

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str | None = None,
        *,
        ignore_cache: bool = False,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        model_name = model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        super().__init__(lang_in, lang_out, model_name, ignore_cache=ignore_cache, **kwargs)
        self.api_key = (
            api_key
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or ""
        )
        self.base_url = (
            base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        ).rstrip("/")
        self.session = requests.Session()

    def do_translate(self, text: str) -> str:
        if not text.strip():
            return text

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        system_prompt = (
            f"You are a professional document translator. Translate the text from {self.lang_in} to {self.lang_out}.\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Preserve ALL tags such as <b0></b0>, <b1></b1>, <s1></s1>, <s2></s2> in their exact position.\n"
            "2. Do NOT translate or alter tag IDs or placeholders.\n"
            "3. Do NOT add extra explanations or Markdown code blocks. Output ONLY the raw translated text."
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.1,
        }

        response = self.session.post(url, headers=headers, json=payload, timeout=40)
        response.raise_for_status()
        data = response.json()

        try:
            translated_content = data["choices"][0]["message"]["content"].strip()
            return remove_control_characters(translated_content)
        except (KeyError, IndexError, TypeError) as err:
            raise RuntimeError(f"OpenAI-compatible translation response format invalid: {data}") from err


ENGINES: dict[str, type[BaseTranslator]] = {
    engine.name: engine for engine in (GoogleTranslator, HandoffTranslator, OpenAITranslator)
}

