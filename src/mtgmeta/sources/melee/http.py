"""A deliberately slow HTTP client for Melee's public pages.

Rules it enforces:

* one request at a time, at least ``interval`` seconds apart (2 s floor);
* exponential backoff on HTTP 429, and a hard stop after ``max_429`` in a row;
* a short retry on transient 5xx/network errors;
* an identifiable User-Agent carrying a contact address.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlencode

import httpx

from mtgmeta import __version__
from mtgmeta.config import MIN_MELEE_INTERVAL

from . import BASE_URL

log = logging.getLogger(__name__)


class RateLimitedError(RuntimeError):
    """Melee kept answering 429. The run stops rather than pushing harder."""


class PoliteClient:
    def __init__(
        self,
        contact_email: str,
        interval: float = 2.5,
        max_429: int = 3,
        max_retries: int = 3,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ) -> None:
        if not contact_email:
            raise ValueError("Set MELEE_CONTACT_EMAIL so requests identify who is making them.")
        self.interval = max(interval, MIN_MELEE_INTERVAL)
        self.max_429 = max_429
        self.max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.requests_made = 0
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers={
                "User-Agent": (
                    f"mtg-modern-meta-engine/{__version__} "
                    f"(personal research; contact: {contact_email})"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    # -- pacing -------------------------------------------------------------
    def _wait_turn(self) -> None:
        if self._last is not None:
            remaining = self.interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()

    # -- core ---------------------------------------------------------------
    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        consecutive_429 = 0
        failures = 0
        while True:
            self._wait_turn()
            try:
                response = self._client.request(method, path, **kwargs)
                self.requests_made += 1
            except httpx.TransportError as exc:
                failures += 1
                if failures > self.max_retries:
                    raise
                log.warning("network error on %s %s (%s); retrying", method, path, exc)
                self._sleep(self.interval * 2**failures)
                continue

            if response.status_code == 429:
                consecutive_429 += 1
                if consecutive_429 >= self.max_429:
                    raise RateLimitedError(
                        f"{consecutive_429} consecutive 429s on {path}; stopping the run"
                    )
                backoff = self.interval * 2 ** (consecutive_429 + 1)
                log.warning("429 on %s; backing off %.0fs", path, backoff)
                self._sleep(backoff)
                continue

            if response.status_code >= 500:
                failures += 1
                if failures > self.max_retries:
                    response.raise_for_status()
                self._sleep(self.interval * 2**failures)
                continue

            response.raise_for_status()
            return response

    def get_text(self, path: str, referer: str | None = None) -> str:
        headers = {"Referer": referer or f"{BASE_URL}/"}
        return self._send("GET", path, headers=headers).text

    def post_form_json(self, path: str, form: list[tuple[str, str]], referer: str) -> dict:
        headers = {
            "Referer": referer,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        body = urlencode(form).encode()
        return self._send("POST", path, content=body, headers=headers).json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
