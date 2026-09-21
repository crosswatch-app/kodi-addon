# SPDX-License-Identifier: GPL-2.0-only
"""A CrossWatch stand-in that records requests and answers like the contract says.

Used to check the bytes the addon actually puts on a socket. Everything else in the suite
asserts that the addon agrees with itself; this asserts it agrees with docs/contract.md.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class StubCrossWatch:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self._status = 200
        self._body: dict[str, Any] = {"ok": True, "crosswatch_version": "0.13.0"}
        self._fail_times = 0
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/webhook/kodiwatcher?token=stub-token"

    def respond(self, status: int = 200, body: dict[str, Any] | None = None, fail_times: int = 0) -> None:
        """Set the next response. fail_times makes that many requests fail first."""
        with self._lock:
            self._status = status
            self._body = body if body is not None else {"ok": True, "crosswatch_version": "0.13.0"}
            self._fail_times = fail_times

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> StubCrossWatch:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            # The addon reuses one connection across retries, so the stub has to speak a
            # protocol version that keeps it open.
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                with stub._lock:
                    stub.received.append(
                        {
                            "path": self.path,
                            "headers": dict(self.headers),
                            "body": json.loads(raw.decode("utf-8")),
                        }
                    )
                    if stub._fail_times > 0:
                        stub._fail_times -= 1
                        status, body = 503, {"error": "busy"}
                    else:
                        status, body = stub._status, stub._body
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:
                """Silence the per-request line. The base signature names its first
                parameter `format`, so it cannot be renamed or made position-only."""

        return Handler
