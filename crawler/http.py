"""Synchronous HTTP policy shared by discovery and course workers."""

import hashlib
import json
import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable, Optional

import requests

from crawler.http_cache import CachedPage, HttpCache

LOGGER = logging.getLogger(__name__)
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class HttpPolicy:
    user_agent: str = (
        "course-crawler/1.0 (+https://github.com/wangyeyu1129/course-crawler)"
    )
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    interval: float = 1.0
    retries: int = 3
    backoff: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.user_agent, str) or not self.user_agent.strip():
            raise ValueError("User-Agent must be a nonempty string.")
        try:
            requests.utils.check_header_validity(("User-Agent", self.user_agent))
            self.user_agent.encode("latin-1")
        except (requests.exceptions.InvalidHeader, UnicodeEncodeError) as error:
            raise ValueError("User-Agent is not a valid HTTP header value.") from error
        if any(ord(char) < 32 or ord(char) == 127 for char in self.user_agent):
            raise ValueError("User-Agent must not contain control characters.")
        for name in ("connect_timeout", "read_timeout", "interval", "backoff"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be finite and nonnegative.")
        if self.connect_timeout == 0 or self.read_timeout == 0:
            raise ValueError("Timeouts must be positive.")
        if type(self.retries) is not int or not 0 <= self.retries <= 10:
            raise ValueError("retries must be an integer between 0 and 10.")


class HttpClient:
    """Retry idempotent GETs and space all attempts, including retries.

    Clock/sleep/session injection makes time and network failures testable.
    Only internally created sessions are owned and closed by this client.
    """

    def __init__(
        self,
        policy: HttpPolicy = HttpPolicy(),
        *,
        session: Optional[requests.Session] = None,
        cache: Optional[HttpCache] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.policy = policy
        self.cache = cache
        self.session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self._clock = clock
        self._sleep = sleep
        self._wall_clock = wall_clock
        self._next_request = 0.0
        self._rate_lock = threading.Lock()
        self._session_lock = threading.Lock()
        self._owner_thread = threading.get_ident()
        self._thread_session = threading.local()
        self._sessions = [self.session]

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        if self._owns_session:
            for session in self._sessions:
                session.close()

    def _session_for_thread(self):
        if threading.get_ident() == self._owner_thread:
            return self.session
        if not self._owns_session:
            raise RuntimeError(
                "Injected sessions may only be used by their creating thread."
            )
        if not hasattr(self._thread_session, "session"):
            with self._session_lock:
                session = requests.Session()
                self._sessions.append(session)
                self._thread_session.session = session
        return self._thread_session.session

    def _wait_for_slot(self) -> None:
        while True:
            with self._rate_lock:
                wait = max(0.0, self._next_request - self._clock())
                if not wait:
                    self._next_request = self._clock() + self.policy.interval
                    return
            self._sleep(wait)

    def _defer(self, delay: float) -> None:
        with self._rate_lock:
            self._next_request = max(self._next_request, self._clock() + delay)

    def _retry_after(self, value: str) -> float:
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = parsedate_to_datetime(value).timestamp() - self._wall_clock()
            except (ValueError, TypeError, OverflowError):
                return 0.0
        return max(0.0, seconds) if math.isfinite(seconds) else 0.0

    def _cache_context(self, url, session, headers):
        if self.cache is None:
            return None, None
        prepared = session.prepare_request(
            requests.Request("GET", url, headers=headers)
        )
        variant = hashlib.sha256(
            json.dumps(
                sorted((k.lower(), v) for k, v in prepared.headers.items())
            ).encode("utf-8")
        ).hexdigest()
        try:
            return variant, self.cache.get(url, variant)
        except (OSError, sqlite3.Error):
            LOGGER.exception("http_cache_read_failed url=%s", url)
            return variant, None

    def _update_cache(self, url, variant, body, response, cached=None):
        if self.cache is None or variant is None:
            return
        try:
            etag = response.headers.get("ETag", cached.etag if cached else None)
            modified = response.headers.get(
                "Last-Modified", cached.last_modified if cached else None
            )
            for name, value in [
                ("If-None-Match", etag),
                ("If-Modified-Since", modified),
            ]:
                if value:
                    requests.utils.check_header_validity((name, value))
                    value.encode("latin-1")
            directives = {
                item.strip().split("=", 1)[0].lower()
                for item in response.headers.get("Cache-Control", "").split(",")
            }
            if (
                "no-store" in directives
                or "*"
                in {
                    item.strip() for item in response.headers.get("Vary", "").split(",")
                }
                or not (etag or modified)
            ):
                self.cache.remove(url, variant)
            elif (
                cached is not None
                and etag == cached.etag
                and modified == cached.last_modified
            ):
                return
            else:
                self.cache.put(url, variant, CachedPage(body, etag, modified))
        except (
            OSError,
            sqlite3.Error,
            ValueError,
            UnicodeEncodeError,
            requests.exceptions.InvalidHeader,
        ):
            LOGGER.exception("http_cache_write_failed url=%s", url)

    def get_text(self, url: str) -> str:
        session = self._session_for_thread()
        headers = {"User-Agent": self.policy.user_agent}
        variant, cached = self._cache_context(url, session, headers)
        if cached:
            if cached.etag:
                headers["If-None-Match"] = cached.etag
            if cached.last_modified:
                headers["If-Modified-Since"] = cached.last_modified
        for attempt in range(self.policy.retries + 1):
            self._wait_for_slot()
            retry_after = 0.0
            try:
                response = session.get(
                    url,
                    headers=headers,
                    timeout=(self.policy.connect_timeout, self.policy.read_timeout),
                    allow_redirects=False,
                )
                try:
                    retry_after = self._retry_after(
                        response.headers.get("Retry-After", "")
                    )
                    if (
                        response.status_code == 304
                        and cached is not None
                        and (
                            "If-None-Match" in headers or "If-Modified-Since" in headers
                        )
                    ):
                        self._update_cache(url, variant, cached.body, response, cached)
                        LOGGER.info("http_not_modified url=%s", url)
                        return cached.body
                    if 300 <= response.status_code < 400:
                        raise requests.HTTPError(
                            f"Redirect response {response.status_code} refused for {url}.",
                            response=response,
                        )
                    response.raise_for_status()
                    body = response.text
                    self._update_cache(url, variant, body, response)
                    return body
                finally:
                    response.close()
            except requests.HTTPError as error:
                if (
                    error.response is None
                    or error.response.status_code not in RETRYABLE_STATUSES
                ):
                    raise
                failure = error
            except (
                requests.Timeout,
                requests.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
            ) as error:
                failure = error
            self._defer(retry_after)
            if attempt == self.policy.retries:
                raise failure
            delay = max(self.policy.backoff * 2**attempt, retry_after)
            self._defer(delay)
            LOGGER.warning(
                "request_retry url=%s attempt=%s wait_seconds=%s error=%s",
                url,
                attempt + 1,
                delay,
                failure,
            )
        raise AssertionError("Unreachable retry state")
