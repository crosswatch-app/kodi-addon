# SPDX-License-Identifier: GPL-2.0-only
"""Where a finished event goes.

The port is domain-typed: a reporter takes a PlaybackEvent and builds the payload itself, so
wire keys never leave payload.py and the two reporters. It returns a delivery verdict,
because HTTP 200 is not delivery: CrossWatch answers 200 for a rejected token, for a
disabled webhook source and for a route with no sink configured.

There is deliberately no retry and no durable queue. Whether events should survive a
CrossWatch outage is question D2 in crosswatch-app/kodi-addon#1, and building a queue
before that is answered would mean building it twice.
"""

from __future__ import annotations

import http.client
import json
import queue
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

from resources.lib.constants import DEFAULT_HTTP_TIMEOUT_SECONDS, DEFAULT_QUEUE_SIZE, SHUTDOWN_DRAIN_SECONDS
from resources.lib.log import get_logger, is_debug, redact
from resources.lib.models import Device, PlaybackEvent
from resources.lib.payload import build_payload

_log = get_logger("reporter")

ALLOWED_SCHEMES = ("http", "https")


class InvalidWebhookUrl(ValueError):
    pass


def validate_webhook_url(url: str) -> str:
    """Accept only http and https.

    urlopen also handles file:, ftp: and data:, so a mistyped URL would otherwise read a
    local file and report as posted. The error message carries only the redacted URL,
    because the token lives in the query string.
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise InvalidWebhookUrl(f"unparseable webhook URL: {redact(url)}") from exc
    if parts.scheme.lower() not in ALLOWED_SCHEMES or not parts.netloc:
        raise InvalidWebhookUrl(f"webhook URL must be http or https: {redact(url)}")
    if parts.fragment:
        # The request is built from path + query, so a fragment would be dropped silently,
        # and a token containing '#' would be truncated to something the receiver rejects.
        raise InvalidWebhookUrl(f"webhook URL must not contain a fragment: {redact(url)}")
    return url


class EventSink(Protocol):
    def report(self, event: PlaybackEvent, device: Device) -> bool: ...


class LogReporter:
    """The default when no webhook is configured. Mechanism at INFO, identity at DEBUG."""

    def report(self, event: PlaybackEvent, device: Device) -> bool:
        _log.info(
            "reporter.event",
            event=event.kind,
            media_type=event.media.media_type,
            viewers_count=len(event.viewers),
            viewers_source=event.viewers_source or "none",
            percent=event.percent if event.percent is not None else "unknown",
        )
        if is_debug():
            # Guarded because building the payload and serialising it is not free, and this
            # runs for every progress event.
            _log.debug("reporter.payload", body=json.dumps(build_payload(event, device), sort_keys=True))
        return True


def _has_explicit_port(authority: str) -> bool:
    tail = authority.rsplit("]", 1)[-1]
    return ":" in tail


def _default_connection(scheme: str, host: str, port: int | None, timeout: float) -> Any:
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout)
    return http.client.HTTPConnection(host, port, timeout=timeout)


class HttpReporter:
    def __init__(
        self,
        url: str,
        timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        connection_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._url = validate_webhook_url(url)
        parts = urlsplit(self._url)
        self._scheme = parts.scheme.lower()
        # Keep the brackets: urlsplit().hostname strips them, and HTTPConnection then
        # re-parses a bare IPv6 address and mis-splits it into host and port.
        authority = parts.netloc.rsplit("@", 1)[-1]
        self._host = authority.rsplit(":", 1)[0] if _has_explicit_port(authority) else authority
        self._port = parts.port
        self._path = parts.path or "/"
        if parts.query:
            self._path = f"{self._path}?{parts.query}"
        self._timeout = timeout
        self._factory = connection_factory or _default_connection
        self._connection: Any = None
        self._safe_url = redact(self._url)

    def _connect(self) -> Any:
        if self._connection is None:
            self._connection = self._factory(self._scheme, self._host, self._port, self._timeout)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def report(self, event: PlaybackEvent, device: Device) -> bool:
        body = json.dumps(build_payload(event, device)).encode("utf-8")
        try:
            connection = self._connect()
            connection.request("POST", self._path, body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            status = int(getattr(response, "status", 0) or 0)
            raw = response.read()
        except Exception as exc:
            # Drop the connection so the next event reconnects rather than reusing a broken socket.
            self.close()
            _log.warning("reporter.failed", url=self._safe_url, event=event.kind, error=str(exc))
            return False

        if status < 200 or status >= 300:
            _log.warning("reporter.failed", url=self._safe_url, event=event.kind, status=status)
            return False
        return self._accepted(event, raw)

    def _accepted(self, event: PlaybackEvent, raw: bytes) -> bool:
        try:
            parsed = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        if parsed.get("ignored") is True or parsed.get("activity_recorded") is False:
            _log.warning(
                "reporter.rejected",
                url=self._safe_url,
                event=event.kind,
                reason=str(parsed.get("error") or "activity_not_recorded"),
            )
            return False
        _log.info("reporter.posted", url=self._safe_url, event=event.kind)
        return True


class ReporterQueue:
    """Keeps the network off the service thread.

    A full queue drops with a warning: blocking here would park the one thread that delivers
    callbacks and observes abort, which is worse than losing a progress event.
    """

    def __init__(self, sink: EventSink, device: Device, maxsize: int = DEFAULT_QUEUE_SIZE) -> None:
        self._sink = sink
        self._device = device
        self._queue: queue.Queue[PlaybackEvent] = queue.Queue(maxsize=maxsize)
        self._stopping = threading.Event()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="crosswatch-reporter", daemon=True)
        self._thread.start()

    def submit(self, event: PlaybackEvent) -> bool:
        if self._stopping.is_set():
            _log.warning("reporter.dropped", event=event.kind, reason="stopping")
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            _log.warning("reporter.dropped", event=event.kind, reason="queue_full")
            return False

    def _run(self) -> None:
        while True:
            try:
                event = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._stopping.is_set():
                    return
                continue
            try:
                self._sink.report(event, self._device)
            except Exception as exc:
                _log.error("reporter.crashed", error=str(exc))
            finally:
                self._queue.task_done()

    def stop(self, deadline: float = SHUTDOWN_DRAIN_SECONDS) -> int:
        """Drain within a deadline and report what could not be delivered.

        Kodi allows the script 5000ms after abort before killing the interpreter, so a fixed
        join plus a full HTTP timeout cannot both fit.
        """
        if self._stopped:
            return 0
        self._stopped = True
        self._stopping.set()
        limit = time.monotonic() + deadline
        while not self._queue.empty() and time.monotonic() < limit:
            time.sleep(0.02)
        self._thread.join(max(0.0, limit - time.monotonic()))
        undelivered = self._queue.qsize()
        if undelivered:
            _log.warning("reporter.undelivered", count=undelivered)
        if self._thread.is_alive():
            # The worker may still be inside request()/getresponse(). http.client holds no
            # lock, so closing the connection underneath it would corrupt the socket. The
            # worker is a daemon thread; leave it and its socket to process exit.
            _log.warning("reporter.sink_left_open", reason="worker_still_running")
        else:
            close = getattr(self._sink, "close", None)
            if callable(close):
                close()
        return undelivered
