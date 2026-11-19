import json
import socket
import threading
import zlib
from typing import Any

import pytest

from resources.lib.constants import HTTP_TIMEOUT_SECONDS, SHUTDOWN_HTTP_TIMEOUT_SECONDS
from resources.lib.sync import client as client_mod
from resources.lib.sync.client import SyncClient, SyncError

PAIRED = "https://h.example/cw/webhook/kodiwatcher?x=1"


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self._headers = headers or {}

    def read(self, amt: int | None = None) -> bytes:
        return self._body

    def getheader(self, name: str) -> str | None:
        return self._headers.get(name)


class FakeConnection:
    def __init__(self, server: "FakeServer", timeout: float) -> None:
        self.server = server
        self.timeout = timeout

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> None:
        if self.server.fail_request:
            raise ConnectionResetError("reset")
        self.server.requests.append({"method": method, "path": path, "body": body, "headers": dict(headers or {})})

    def getresponse(self) -> FakeResponse:
        return self.server.responses.pop(0)

    def close(self) -> None:
        self.server.closed += 1


class FakeServer:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.opened: list[tuple[str, str, int | None, float]] = []
        self.closed = 0
        self.fail_request = False
        self.fail_connect = False

    def factory(self, scheme: str, host: str, port: int | None, timeout: float) -> FakeConnection:
        if self.fail_connect:
            raise OSError("no route")
        self.opened.append((scheme, host, port, timeout))
        return FakeConnection(self, timeout)


def _ok(**fields: Any) -> FakeResponse:
    return FakeResponse(200, json.dumps({"ok": True, **fields}).encode())


def _client(server: FakeServer, abort: threading.Event | None = None) -> SyncClient:
    return SyncClient(PAIRED, token="tok-secret", device_id="htpc-1", abort=abort or threading.Event(),
                      connection_factory=server.factory)  # fmt: skip


def test_paths_relative_to_paired_url_for_all_three() -> None:
    server = FakeServer(_ok(), _ok(changes=[]), _ok(applied=1, failed=0))
    client = _client(server)
    client.upload(b'{"version": 1}')
    client.queue("kv-1", after=0)
    client.ack("kv-1", [{"seq": 3, "status": "applied"}])
    assert [r["path"] for r in server.requests] == [
        "/cw/webhook/kodiwatcher/sync/v1/upload",
        "/cw/webhook/kodiwatcher/sync/v1/queue?viewer_id=kv-1&after=0&limit=500",
        "/cw/webhook/kodiwatcher/sync/v1/queue/ack",
    ]
    assert [r["method"] for r in server.requests] == ["POST", "GET", "POST"]
    assert server.opened[0][:3] == ("https", "h.example", None)
    assert server.closed == 3


def test_headers_token_and_device_never_query() -> None:
    server = FakeServer(_ok())
    _client(server).queue("kv-1", after=7, limit=100)
    request = server.requests[0]
    assert request["headers"]["X-CrossWatch-Token"] == "tok-secret"
    assert request["headers"]["X-CrossWatch-Device"] == "htpc-1"
    assert "tok-secret" not in request["path"] and "token" not in request["path"]
    assert request["path"].endswith("queue?viewer_id=kv-1&after=7&limit=100")


def test_bodies_decompress_with_gzip_wbits() -> None:
    server = FakeServer(_ok(), _ok())
    client = _client(server)
    client.upload(b'{"version": 1, "mode": "delta"}')
    client.ack("kv-1", [{"seq": 3, "status": "failed", "error": "not_allowed"}])
    upload, ack = server.requests
    assert upload["headers"]["Content-Encoding"] == "gzip"
    assert zlib.decompress(upload["body"], 16 + zlib.MAX_WBITS) == b'{"version": 1, "mode": "delta"}'
    assert json.loads(zlib.decompress(ack["body"], 16 + zlib.MAX_WBITS)) == {
        "version": 1,
        "viewer_id": "kv-1",
        "results": [{"seq": 3, "status": "failed", "error": "not_allowed"}],
    }


def test_reply_returned() -> None:
    server = FakeServer(_ok(mode="full", complete=True))
    assert _client(server).upload(b"{}") == {"ok": True, "mode": "full", "complete": True}


def test_nested_error_read() -> None:
    body = {"ok": False, "error": {"code": "viewer_unknown", "message": "Unknown viewer.", "retryable": False}}
    server = FakeServer(FakeResponse(409, json.dumps(body).encode()))
    with pytest.raises(SyncError) as caught:
        _client(server).upload(b"{}")
    assert (caught.value.status, caught.value.code, caught.value.retryable) == (409, "viewer_unknown", False)


def test_retry_after_parsed() -> None:
    body = {"ok": False, "error": {"code": "temporarily_unavailable", "message": "", "retryable": True}}
    server = FakeServer(FakeResponse(503, json.dumps(body).encode(), {"Retry-After": "30"}))
    with pytest.raises(SyncError) as caught:
        _client(server).upload(b"{}")
    assert caught.value.retryable and caught.value.retry_after == 30.0
    server = FakeServer(FakeResponse(429, b'{"ok": false}', {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}))
    with pytest.raises(SyncError) as caught:
        _client(server).upload(b"{}")
    assert caught.value.retryable and caught.value.retry_after is None
    for value in ("inf", "1e12", "nan", "-5"):
        server = FakeServer(FakeResponse(503, b'{"ok": false}', {"Retry-After": value}))
        with pytest.raises(SyncError) as caught:
            _client(server).upload(b"{}")
        assert caught.value.retry_after in (None, 900.0)


def test_reply_without_ok_is_failure_worth_retrying() -> None:
    # A captive portal or proxy login page: nothing reached CrossWatch, try again later.
    server = FakeServer(FakeResponse(200, b"<html>login</html>"), FakeResponse(200, b'{"status": "fine"}'))
    for _ in range(2):
        with pytest.raises(SyncError) as caught:
            _client(server).upload(b"{}")
        assert caught.value.code == "not_crosswatch" and caught.value.retryable
        assert not caught.value.from_crosswatch


def test_proxy_401_is_not_crosswatchs_401() -> None:
    server = FakeServer(FakeResponse(401, b"<html>Authorization Required</html>"))
    with pytest.raises(SyncError) as caught:
        _client(server).upload(b"{}")
    assert caught.value.status == 401 and not caught.value.from_crosswatch and caught.value.retryable
    body = {"ok": False, "error": {"code": "invalid_token", "message": "", "retryable": False}}
    server = FakeServer(FakeResponse(401, json.dumps(body).encode()))
    with pytest.raises(SyncError) as caught:
        _client(server).upload(b"{}")
    assert caught.value.from_crosswatch and caught.value.code == "invalid_token"


def test_no_sync_endpoints() -> None:
    server = FakeServer(FakeResponse(404, b'{"detail": "Not Found"}'))
    with pytest.raises(SyncError) as caught:
        _client(server).queue("kv-1", after=0)
    assert (caught.value.code, caught.value.retryable) == ("no_sync", False)


def test_ok_false_on_200_is_failure() -> None:
    server = FakeServer(FakeResponse(200, b'{"ok": false}'))
    with pytest.raises(SyncError):
        _client(server).upload(b"{}")


def test_redirect_is_not_followed() -> None:
    server = FakeServer(FakeResponse(302, b"", {"Location": "https://elsewhere/"}))
    with pytest.raises(SyncError) as caught:
        _client(server).upload(b"{}")
    assert caught.value.status == 302 and caught.value.code == "not_crosswatch"
    assert len(server.requests) == 1 and len(server.opened) == 1


def test_proxy_413_classified_by_status() -> None:
    server = FakeServer(FakeResponse(413, b"<html>Request Entity Too Large</html>"))
    with pytest.raises(SyncError) as caught:
        _client(server).upload(b"{}")
    assert (caught.value.status, caught.value.code, caught.value.retryable) == (413, "not_crosswatch", False)
    assert not caught.value.from_crosswatch


def test_proxy_502_is_retryable() -> None:
    server = FakeServer(FakeResponse(502, b"<html>Bad Gateway</html>"))
    with pytest.raises(SyncError) as caught:
        _client(server).queue("kv-1", after=0)
    assert caught.value.retryable


def test_lost_reply_is_told_apart() -> None:
    class Lost(FakeResponse):
        def read(self, amt: int | None = None) -> bytes:
            raise ConnectionResetError("reset")

    server = FakeServer(Lost(200, b""))
    with pytest.raises(SyncError) as caught:
        _client(server).upload(b"{}")
    assert (caught.value.code, caught.value.retryable) == ("lost_reply", True)


def test_bad_replies_never_escape_as_other_errors() -> None:
    deep = b"[" * 100_000 + b"]" * 100_000
    server = FakeServer(FakeResponse(200, deep))
    with pytest.raises(SyncError):
        _client(server).upload(b"{}")


def test_reply_size_capped(monkeypatch) -> None:
    monkeypatch.setattr(client_mod, "MAX_REPLY_BYTES", 10)
    server = FakeServer(FakeResponse(200, b'{"ok": true, "padding": "xxxxxxxx"}'))
    with pytest.raises(SyncError) as caught:
        _client(server).upload(b"{}")
    assert caught.value.code == "reply_too_large"


def test_request_size_checked_after_gzip(monkeypatch) -> None:
    monkeypatch.setattr(client_mod, "MAX_REQUEST_BYTES", 50)
    server = FakeServer()
    with pytest.raises(SyncError) as caught:
        _client(server).upload(bytes(range(256)) * 4)
    assert caught.value.code == "request_too_large" and server.requests == []


def test_a_header_that_cant_be_sent_is_not_a_network_error() -> None:
    class Strict(FakeConnection):
        def request(
            self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None
        ) -> None:
            for value in (headers or {}).values():
                value.encode("latin-1")

    server = FakeServer()
    client = SyncClient(PAIRED, token="tok\u20ac", device_id="htpc-1", abort=threading.Event(),
                        connection_factory=lambda *a: Strict(server, 1.0))  # fmt: skip
    with pytest.raises(SyncError) as caught:
        client.upload(b"{}")
    assert (caught.value.code, caught.value.retryable) == ("invalid_request", False)


def test_cancel_shuts_the_live_socket() -> None:
    shut: list[int] = []

    class Sock:
        def shutdown(self, how: int) -> None:
            shut.append(how)

    class Live(FakeConnection):
        sock = Sock()

        def getresponse(self) -> FakeResponse:
            client.cancel()
            return super().getresponse()

    server = FakeServer(_ok())
    client = SyncClient(PAIRED, token="t", device_id="d", abort=threading.Event(),
                        connection_factory=lambda *a: Live(server, 1.0))  # fmt: skip
    client.upload(b"{}")
    assert shut == [socket.SHUT_RDWR]
    client.cancel()  # nothing in flight: nothing to do
    assert shut == [socket.SHUT_RDWR]


@pytest.mark.parametrize(
    ("paired", "path"),
    [
        ("http://h/webhook/kodiwatcher", "/webhook/kodiwatcher/sync/v1/upload"),
        ("http://h:8787/webhook/kodiwatcher/", "/webhook/kodiwatcher/sync/v1/upload"),
        ("https://[fe80::1]:8443/cw/webhook/kodiwatcher?token=old", "/cw/webhook/kodiwatcher/sync/v1/upload"),
    ],
)
def test_paths_for_url_shapes(paired: str, path: str) -> None:
    server = FakeServer(_ok())
    SyncClient(paired, token="t", device_id="d", abort=threading.Event(), connection_factory=server.factory).upload(
        b"{}"
    )
    assert server.requests[0]["path"] == path


def test_network_error_retryable() -> None:
    server = FakeServer()
    server.fail_connect = True
    with pytest.raises(SyncError) as caught:
        _client(server).upload(b"{}")
    assert (caught.value.status, caught.value.code, caught.value.retryable) == (0, "network", True)
    server = FakeServer()
    server.fail_request = True
    with pytest.raises(SyncError) as caught:
        _client(server).upload(b"{}")
    assert caught.value.code == "network" and server.closed == 1


def test_short_timeout_after_abort() -> None:
    abort = threading.Event()
    server = FakeServer(_ok(), _ok())
    client = _client(server, abort)
    client.queue("kv-1", after=0)
    abort.set()
    client.queue("kv-1", after=0)
    assert server.opened[0][3] == HTTP_TIMEOUT_SECONDS and server.opened[1][3] == SHUTDOWN_HTTP_TIMEOUT_SECONDS
