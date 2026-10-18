# test_telegram_api.py — the stdlib Bot API client, with a fake urlopen (no network).

import io
import json
import logging
import traceback
import urllib.error

import pytest

from pizzabot.channels.telegram.__main__ import RedactFilter
from pizzabot.channels.telegram.api import TelegramAPI, TelegramError

TOKEN = "123456789:AAHsecretSECRETsecretSECRETsecret_xyz"


class Response:
    def __init__(self, payload, status=200):
        self.status = status
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code, payload):
    return urllib.error.HTTPError(url, code, f"HTTP {code} for {url}", {},
                                  io.BytesIO(json.dumps(payload).encode()))


class FakeOpen:
    """Plays a script: each entry is a payload dict (200 OK), an HTTP error
    (code, payload) or an exception class/instance to raise."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        step = self.script.pop(0)
        if isinstance(step, tuple):
            raise http_error(request.full_url, *step)
        if isinstance(step, BaseException) or (isinstance(step, type) and issubclass(step, BaseException)):
            raise step
        return Response(step)


def api(opener, **kw):
    sleeps = []
    return TelegramAPI(TOKEN, urlopen=opener, sleep=sleeps.append, **kw), sleeps


def test_success_posts_json_and_returns_result():
    opener = FakeOpen({"ok": True, "result": {"message_id": 7}})
    client, sleeps = api(opener)
    assert client.call("sendMessage", {"chat_id": 1, "text": "Ciao è"}) == {"message_id": 7}
    request, timeout = opener.requests[0]
    assert request.full_url.endswith("/sendMessage") and request.get_method() == "POST"
    assert json.loads(request.data) == {"chat_id": 1, "text": "Ciao è"}
    assert timeout == 10.0 and sleeps == []


def test_api_error_is_raised_without_retry():
    opener = FakeOpen((400, {"ok": False, "error_code": 400,
                             "description": "Bad Request: message is not modified"}))
    client, sleeps = api(opener)
    with pytest.raises(TelegramError) as info:
        client.call("editMessageText", {})
    assert info.value.error_code == 400 and "not modified" in info.value.description
    assert len(opener.requests) == 1 and sleeps == []


def test_429_waits_retry_after_then_succeeds():
    opener = FakeOpen((429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                             "parameters": {"retry_after": 3}}),
                      {"ok": True, "result": True})
    client, sleeps = api(opener)
    assert client.call("sendMessage", {}) is True
    assert sleeps == [3.0]


def test_network_errors_back_off_then_give_up():
    opener = FakeOpen(urllib.error.URLError(OSError("no route")), TimeoutError(),
                      ConnectionResetError(), {"ok": True, "result": 1})
    client, sleeps = api(opener)
    assert client.call("getMe") == 1
    assert sleeps == [1.0, 2.0, 4.0]

    opener = FakeOpen(*[TimeoutError()] * 3)
    client, sleeps = api(opener, max_retries=2)
    with pytest.raises(TelegramError) as info:
        client.call("getMe")
    assert info.value.is_network and sleeps == [1.0, 2.0]


def test_5xx_is_retried():
    opener = FakeOpen((502, {"ok": False, "error_code": 502, "description": "Bad Gateway"}),
                      {"ok": True, "result": 1})
    client, sleeps = api(opener)
    assert client.call("getMe") == 1 and sleeps == [1.0]


def test_garbage_response_is_an_error():
    class Garbage(Response):
        def read(self):
            return b"<html>proxy error</html>"

    client, _ = api(lambda request, timeout: Garbage({}), max_retries=0)
    with pytest.raises(TelegramError, match="bad response"):
        client.call("getMe")


# --- the token never leaks -------------------------------------------------------------

def _all_text(exc: BaseException) -> str:
    return "\n".join([str(exc), repr(exc), *traceback.format_exception(exc), repr(exc.__dict__)])


@pytest.mark.parametrize("step", [
    (401, {"ok": False, "error_code": 401, "description": "Unauthorized"}),
    (400, {"ok": False, "error_code": 400, "description": f"echo bot{TOKEN}/sendMessage"}),
    urllib.error.URLError(f"cannot reach https://api.telegram.org/bot{TOKEN}/getMe"),
    OSError(f"socket error for bot{TOKEN}"),
])
def test_token_never_in_exceptions_or_tracebacks(step):
    client, _ = api(FakeOpen(*[step] * 5), max_retries=0)
    with pytest.raises(TelegramError) as info:
        client.call("getMe")
    exc = info.value
    assert exc.__context__ is None and exc.__cause__ is None  # no urllib error chained
    assert TOKEN not in _all_text(exc)
    assert "secret" not in _all_text(exc)
    assert TOKEN not in repr(client)


def test_token_never_in_logs(caplog):
    client, _ = api(FakeOpen((404, {"ok": False, "error_code": 404, "description": "Not Found"})),
                    max_retries=0)
    log = logging.getLogger("pizzabot.test")
    with caplog.at_level(logging.DEBUG):
        try:
            client.call("getMe")
        except TelegramError:
            log.exception("call failed")
    assert caplog.records and TOKEN not in caplog.text


def test_redact_filter_scrubs_any_record():
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "url=%s", (f"/bot{TOKEN}/getMe",),
                               None)
    RedactFilter(TOKEN).filter(record)
    assert record.getMessage() == "url=/bot<redacted>/getMe"


def test_empty_token_is_refused():
    with pytest.raises(ValueError):
        TelegramAPI("  ")
