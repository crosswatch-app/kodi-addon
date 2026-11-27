"""A scripted stand-in for CrossWatch's sync endpoints: replies a test sets up, requests it
records. It holds none of CrossWatch's rules on purpose.

CrossWatch's behaviour (seqs, staging, queue coalescing, applied_seq, merging, bindings) is
checked against the real CrossWatch, offline, in the scenario harness (plan Task 20): a
hand-written copy of those rules drifted from the real ones on most scenarios and would
again with every CrossWatch change. Tests here script the replies they need, as CrossWatch
would send them, and assert on what the add-on sent.
"""
from __future__ import annotations

import json
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit


@dataclass
class Sent:
    method: str
    endpoint: str
    query: dict[str, str]
    body: dict[str, Any] | None
    headers: dict[str, str]


@dataclass
class Reply:
    status: int = 200
    body: dict[str, Any] = field(default_factory=lambda: {"ok": True})
    headers: dict[str, str] = field(default_factory=dict)
    # The request reaches CrossWatch and the answer is lost on the way back.
    lost: bool = False


def ok(**fields: Any) -> Reply:
    return Reply(body={"ok": True, **fields})


def error(status: int, code: str, *, retryable: bool = False, retry_after: str | None = None) -> Reply:
    headers = {"Retry-After": retry_after} if retry_after else {}
    body = {"ok": False, "error": {"code": code, "message": code, "retryable": retryable}}
    return Reply(status=status, body=body, headers=headers)


class ScriptedCrossWatch:
    """Replies per endpoint ("upload", "queue", "queue/ack"), used in order; an endpoint with
    nothing scripted answers {"ok": true}."""

    def __init__(self) -> None:
        self.sent: list[Sent] = []
        self._script: dict[str, list[Reply]] = {}

    def script(self, endpoint: str, *replies: Reply) -> None:
        self._script.setdefault(endpoint, []).extend(replies)

    def sent_to(self, endpoint: str) -> list[Sent]:
        return [s for s in self.sent if s.endpoint == endpoint]

    def connection_factory(self, scheme: str, host: str, port: int | None, timeout: float) -> _Connection:
        return _Connection(self)

    def answer(self, method: str, path: str, body: bytes | None, headers: Mapping[str, str]) -> Reply:
        endpoint = path.split("/sync/v1/", 1)[-1].split("?", 1)[0]
        query = {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}
        payload = None
        if body:
            raw = zlib.decompress(body, 16 + zlib.MAX_WBITS) if headers.get("Content-Encoding") == "gzip" else body
            payload = json.loads(raw)
        self.sent.append(Sent(method, endpoint, query, payload, dict(headers)))
        queued = self._script.get(endpoint)
        return queued.pop(0) if queued else ok()


class _Response:
    def __init__(self, reply: Reply) -> None:
        self.status = reply.status
        self._body = json.dumps(reply.body).encode()
        self._headers = reply.headers

    def read(self, amt: int | None = None) -> bytes:
        return self._body

    def getheader(self, name: str) -> str | None:
        return self._headers.get(name)


class _Socket:
    def settimeout(self, value: float) -> None:
        pass

    def shutdown(self, how: int) -> None:
        pass


class _Connection:
    """http.client's surface, as SyncClient uses it."""

    def __init__(self, cw: ScriptedCrossWatch) -> None:
        self._cw = cw
        self._reply: Reply | None = None
        self.sock: _Socket | None = None

    def connect(self) -> None:
        self.sock = _Socket()

    def request(
        self, method: str, path: str, body: bytes | None = None, headers: Mapping[str, str] | None = None
    ) -> None:
        self._reply = self._cw.answer(method, path, body, headers or {})

    def getresponse(self) -> _Response:
        if self._reply is None or self._reply.lost:
            raise ConnectionResetError("reply lost")
        return _Response(self._reply)

    def close(self) -> None:
        pass
