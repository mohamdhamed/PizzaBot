"""Minimal Telegram Bot API client — stdlib only (urllib + json).

The token is part of every request URL (``/bot<token>/<method>``), so it
must never leak through an exception: urllib errors carry the URL. Every
failure is turned into a ``TelegramError`` raised *outside* the ``except``
block (no ``__context__`` pointing at the urllib error) and any message is
scrubbed of the token as a second line of defence.

Retries: network errors, HTTP 5xx and 429 (honouring ``retry_after``) are
retried with exponential backoff. Other API errors (400 "message is not
modified", 403 "bot was blocked", …) are raised at once.
"""

from __future__ import annotations

import http.client
import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable

API_ROOT = "https://api.telegram.org"


class TelegramError(Exception):
    """A Bot API call failed. Never contains the token."""

    def __init__(self, method: str, description: str, *, error_code: int | None = None,
                 retry_after: float | None = None, migrate_to_chat_id: int | None = None):
        super().__init__(f"{method}: {description}"
                         + (f" (code {error_code})" if error_code is not None else ""))
        self.method = method
        self.description = description
        self.error_code = error_code
        self.retry_after = retry_after
        self.migrate_to_chat_id = migrate_to_chat_id

    @property
    def is_network(self) -> bool:
        return self.error_code is None


class TelegramAPI:
    """``call(method, params)`` → the ``result`` field of the API response."""

    def __init__(self, token: str, *, timeout: float = 10.0, max_retries: int = 4,
                 backoff: float = 1.0, max_backoff: float = 30.0,
                 urlopen: Callable[..., Any] = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep):
        if not token or not token.strip():
            raise ValueError("Telegram bot token is empty")
        self._token = token.strip()
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.max_backoff = max_backoff
        self._urlopen = urlopen
        self._sleep = sleep

    def __repr__(self) -> str:  # never show the token
        return "TelegramAPI(token=<redacted>)"

    def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
        delay = self.backoff
        for attempt in range(self.max_retries + 1):
            error = self._call_once(method, params or {}, timeout or self.timeout)
            if not isinstance(error, TelegramError):
                return error  # the result
            retryable = error.is_network or error.error_code == 429 or error.error_code >= 500
            if not retryable or attempt == self.max_retries:
                raise error
            # 429 says how long to wait; otherwise back off exponentially.
            self._sleep(float(error.retry_after) if error.retry_after is not None else delay)
            delay = min(delay * 2, self.max_backoff)
        raise AssertionError("unreachable")

    def _call_once(self, method: str, params: dict, timeout: float) -> Any:
        """The result, or a TelegramError to raise/retry (returned, not raised,
        so it is created outside any ``except`` block)."""
        request = urllib.request.Request(
            f"{API_ROOT}/bot{self._token}/{method}",
            data=json.dumps(params, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        status: int | None = None
        body: bytes | None = None
        failure: str | None = None
        try:
            with self._urlopen(request, timeout=timeout) as response:
                status = response.status
                body = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                body = exc.read()
            except Exception:  # noqa: BLE001 — body is optional
                body = b""
            finally:
                exc.close()
        except urllib.error.URLError as exc:
            failure = f"network error: {type(exc.reason).__name__}"
        except (TimeoutError, socket.timeout):
            failure = "network error: timeout"
        except (ConnectionError, http.client.HTTPException, OSError) as exc:
            failure = f"network error: {type(exc).__name__}"

        if failure is not None:
            return TelegramError(method, self._scrub(failure))
        try:
            payload = json.loads(body or b"")
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return TelegramError(method, f"bad response (HTTP {status})", error_code=status or 0)
        if payload.get("ok"):
            return payload.get("result")
        parameters = payload.get("parameters") or {}
        return TelegramError(
            method,
            self._scrub(str(payload.get("description") or "unknown error")),
            error_code=int(payload.get("error_code") or status or 0),
            retry_after=parameters.get("retry_after"),
            migrate_to_chat_id=parameters.get("migrate_to_chat_id"),
        )

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "<redacted>")
