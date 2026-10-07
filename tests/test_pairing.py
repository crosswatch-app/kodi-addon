import ssl

import pytest

from resources.lib import log as logmod
from resources.lib.pairing import (
    address_hint,
    exchange_code,
    normalise_address,
    normalise_code,
)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("192.168.1.10:8787", "http://192.168.1.10:8787"),
        ("  nas:8787/  ", "http://nas:8787"),
        ("https://cw.example", "https://cw.example"),
        ("http://nas:8787/webhook/kodiwatcher", "http://nas:8787"),
        ("http://nas/crosswatch/", "http://nas/crosswatch"),
        ("[fd00::1]:8787", "http://[fd00::1]:8787"),
    ],
)
def test_an_address_is_normalised(typed, expected):
    assert normalise_address(typed) == expected


@pytest.mark.parametrize("typed", ["", "   ", "ftp://nas", "http://", "http://nas/#x", "http://nas/?token=t"])
def test_an_address_that_cannot_be_crosswatch_is_refused(typed):
    assert normalise_address(typed) is None


def test_the_hint_is_the_base_of_the_stored_webhook_url():
    assert address_hint("http://nas:8787/webhook/kodiwatcher") == "http://nas:8787"
    assert address_hint("") == ""


def test_a_code_is_uppercased_and_loses_its_spaces():
    assert normalise_code(" abc 234\t") == "ABC234"


class Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class Connection:
    def __init__(self, response: Response | None = None, raises: Exception | None = None, on_read: Exception | None = None) -> None:
        self.response = response
        self.raises = raises
        self.on_read = on_read
        self.sent: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.closed = False

    def request(self, method, path, body=None, headers=None) -> None:
        if self.raises:
            raise self.raises
        self.sent.append((method, path, body, dict(headers or {})))

    def getresponse(self) -> Response:
        if self.on_read:
            raise self.on_read
        assert self.response is not None
        return self.response

    def close(self) -> None:
        self.closed = True


def _exchange(connection: Connection, address: str = "http://nas:8787"):
    opened: list[tuple] = []

    def connect(scheme, host, port, timeout):
        opened.append((scheme, host, port, timeout))
        return connection

    return exchange_code(address, "ABC234", connect=connect), opened


def test_a_200_pairs_and_the_connection_is_closed():
    connection = Connection(Response(200, b'{"ok": true, "url": "http://x", "token": " tok ", "instance": "Living room"}'))
    result, opened = _exchange(connection)
    assert (result.outcome, result.token, result.instance) == ("paired", "tok", "Living room")
    assert opened == [("http", "nas", 8787, 10.0)]
    assert connection.sent[0][:2] == ("POST", "/webhook/kodiwatcher/pair")
    assert connection.closed


@pytest.mark.parametrize(
    ("status", "body", "outcome"),
    [
        (401, b'{"ok": false, "error": "invalid_code"}', "invalid_code"),
        (429, b"{}", "rate_limited"),
        (404, b"{}", "failed"),
        (500, b"{}", "failed"),
        (200, b"not json", "failed"),
        (200, b'{"ok": false}', "failed"),
        (200, b'{"ok": true, "instance": "x"}', "failed"),
        (200, b"[]", "failed"),
    ],
)
def test_each_answer_has_its_outcome(status, body, outcome):
    result, _ = _exchange(Connection(Response(status, body)))
    assert result.outcome == outcome
    assert result.status == status


def test_a_switched_off_add_on_feature_has_its_own_outcome():
    """CrossWatch answers 200 with ignored: true, so the HTTP status alone says nothing useful."""
    body = b'{"ok": true, "ignored": true, "error": "addon_disabled", "crosswatch_version": "0.13.3"}'
    result, _ = _exchange(Connection(Response(200, body)))
    assert (result.outcome, result.status) == ("disabled", 200)


def test_any_other_ignored_reply_is_a_plain_failure():
    result, _ = _exchange(Connection(Response(200, b'{"ok": true, "ignored": true, "error": "other"}')))
    assert result.outcome == "failed"


def test_a_reply_without_an_instance_still_pairs():
    result, _ = _exchange(Connection(Response(200, b'{"ok": true, "token": "tok"}')))
    assert (result.outcome, result.instance) == ("paired", "")


@pytest.mark.parametrize("error", [ConnectionRefusedError(), TimeoutError(), OSError("unreachable")])
def test_a_transport_failure_is_unreachable(error):
    assert _exchange(Connection(raises=error))[0].outcome == "unreachable"


def test_a_lost_response_is_unreachable():
    assert _exchange(Connection(Response(200, b"{}"), on_read=TimeoutError()))[0].outcome == "unreachable"


def test_a_connect_failure_is_unreachable():
    def connect(*args):
        raise OSError("no route")

    assert exchange_code("http://nas", "ABC234", connect=connect).outcome == "unreachable"


def test_the_log_carries_neither_code_nor_token_nor_instance():
    logmod.reset()
    lines: list[str] = []
    logmod.configure(log_dir=None, debug=True, sink=lambda message, level: lines.append(message))
    try:
        _exchange(Connection(Response(200, b'{"ok": true, "token": "secret-tok", "instance": "Living room"}')))
    finally:
        logmod.reset()
    assert any("config.pair_result" in line and "outcome=paired" in line for line in lines)
    assert not any(word in line for line in lines for word in ("ABC234", "secret-tok", "Living room", "nas"))


def test_a_rejected_certificate_has_its_own_outcome():
    """A self-signed or otherwise untrusted certificate is not an unreachable server."""
    error = ssl.SSLCertVerificationError(1, "certificate verify failed: self-signed certificate")
    result, _ = _exchange(Connection(raises=error), address="https://nas:8443")
    assert result.outcome == "certificate"
