# SPDX-License-Identifier: GPL-2.0-only
"""Where a finished event goes.

The port is domain-typed: a reporter takes a domain event and builds the payload itself, so
wire keys never leave payload.py and the two reporters. It returns a delivery verdict,
because HTTP 200 is not delivery: CrossWatch answers 200 for a rejected token, for a
disabled webhook source and for a route with no sink configured.

Delivery is retried within a bounded budget but is never persisted. Events do not survive a
restart, and a CrossWatch outage longer than the budget loses them; that is the contract's
position, and a durable queue is a separate piece of work with its own spec.
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

from resources.lib.constants import (
    DEFAULT_QUEUE_SIZE,
    HTTP_TIMEOUT_SECONDS,
    MIN_CROSSWATCH_VERSION,
    RETRY_BACKOFF_SECONDS,
    RETRY_BUDGET_SECONDS,
    SHUTDOWN_DRAIN_SECONDS,
    SHUTDOWN_HTTP_TIMEOUT_SECONDS,
)
from resources.lib.log import get_logger, is_debug, redact
from resources.lib.models import Device, PingEvent, PlaybackEvent
from resources.lib.payload import build_payload

_log = get_logger("reporter")

ALLOWED_SCHEMES = ("http", "https")

Event = PlaybackEvent | PingEvent


def event_kind(event: Event) -> str:
    """The wire name of an event, for logging and for policy.

    PingEvent has no kind field of its own so that nothing handling playback can be handed
    one by accident; this is the single place that maps either shape to a name.
    """
    return "ping" if isinstance(event, PingEvent) else event.kind


def _version_tuple(text: str) -> tuple[int, ...] | None:
    """None when it is not a plain dotted number. A nightly or a git describe is not old."""
    parts = text.strip().split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


_MIN_VERSION = _version_tuple(MIN_CROSSWATCH_VERSION)


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
    def report(self, event: Event, device: Device) -> bool: ...


class EventQueue(Protocol):
    def submit(self, event: Event) -> bool: ...


class LogReporter:
    """The default when no webhook is configured. Mechanism at INFO, identity at DEBUG."""

    def report(self, event: Event, device: Device) -> bool:
        if isinstance(event, PingEvent):
            _log.info(
                "reporter.ping",
                viewers_count=len(event.viewers),
                pkc_skipped=event.pkc_skipped,
            )
            return True
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
        *,
        token: str,
        abort: threading.Event,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        connection_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], bool] | None = None,
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
        self._abort = abort
        self._clock = clock
        # Returns True when the wait was cut short by abort, matching Event.wait.
        self._sleep = sleeper if sleeper is not None else abort.wait
        self._warned_old_server = False
        self._headers = {"Content-Type": "application/json"}
        if token:
            # The contract accepts the token in the query string, in this header, or both.
            # Sent here as well so the query parameter can be dropped once the server stops
            # requiring it. While both are sent the query-string exposure is unchanged.
            self._headers["X-CrossWatch-Token"] = token

    def _connect(self) -> Any:
        if self._connection is None:
            # A connection opened after abort gets the short timeout. Kodi kills the
            # interpreter 5000ms after abort and a ten second socket cannot finish inside
            # that; the contract's timeout applies to normal operation.
            timeout = SHUTDOWN_HTTP_TIMEOUT_SECONDS if self._abort.is_set() else self._timeout
            self._connection = self._factory(self._scheme, self._host, self._port, timeout)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def report(self, event: Event, device: Device) -> bool:
        """Deliver one event, retrying within the contract's budget.

        Retry lives here rather than in the queue because ordering matters: the receiver
        drives a now-playing card from event order, so a retried stop must never land after
        the next playback's start. Blocking the worker is harmless; nothing waits on it and
        the service thread is a different thread.
        """
        if self._abort.is_set():
            # Drop any live keep-alive socket: it was opened with the long timeout, and
            # reusing it would carry that timeout past abort.
            self.close()
        body = json.dumps(build_payload(event, device)).encode("utf-8")
        kind = event_kind(event)
        deadline = self._clock() + RETRY_BUDGET_SECONDS
        attempts = 0
        while True:
            verdict = self._attempt(body, kind)
            attempts += 1
            if verdict is not None:
                return verdict

            remaining = deadline - self._clock()
            # Refuse to start an attempt the budget cannot pay for. A socket timeout applies
            # per operation, so one attempt against a black-holing peer costs up to three of
            # them; without this check "two minutes" silently becomes two and a half.
            if remaining <= self._timeout:
                _log.warning("reporter.gave_up", event=kind, reason="budget_spent", attempts=attempts)
                return False
            backoff = RETRY_BACKOFF_SECONDS[min(attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            if self._sleep(min(backoff, remaining)):
                _log.warning("reporter.gave_up", event=kind, reason="aborted", attempts=attempts)
                return False

    def _attempt(self, body: bytes, kind: str) -> bool | None:
        """True delivered, False refused for good, None worth retrying."""
        try:
            connection = self._connect()
            connection.request("POST", self._path, body=body, headers=dict(self._headers))
        except Exception as exc:
            # Nothing was necessarily written, so this is safe to retry. Drop the connection
            # so the next attempt reconnects rather than reusing a broken socket.
            self.close()
            _log.warning("reporter.failed", url=self._safe_url, event=kind, error=str(exc))
            return None

        try:
            response = connection.getresponse()
            status = int(getattr(response, "status", 0) or 0)
            raw = response.read()
        except Exception as exc:
            # Retried, on the CrossWatch maintainer's decision (issue #1, 2026-09-20): the
            # sinks absorb a duplicate. Everything goes through Trakt and Simkl's
            # /scrobble/* endpoints, which dedupe server side and answer 409, and the media
            # sinks set a watched flag, which is idempotent. So losing the response is worth
            # another attempt, where previously it cost the event.
            #
            # This also covers the commoner case, which is not a lost response at all: the
            # server closed an idle keep-alive, uvicorn does so after about five seconds,
            # and the write landed on a socket whose peer had already gone.
            self.close()
            _log.warning("reporter.lost_response", url=self._safe_url, event=kind, error=str(exc))
            return None

        if 500 <= status < 600:
            # Close rather than reuse: a 5xx often carries Connection: close, and reusing the
            # socket spends a whole backoff interval discovering that.
            self.close()
            _log.warning("reporter.failed", url=self._safe_url, event=kind, status=status)
            return None
        if status < 200 or status >= 300:
            # A 4xx is a decision, not a hiccup. 401 is a bad token; retrying for two minutes
            # helps nobody and delays every event behind it.
            _log.warning("reporter.rejected", url=self._safe_url, event=kind, status=status)
            return False
        return self._accepted(kind, raw)

    def _accepted(self, kind: str, raw: bytes) -> bool:
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
                event=kind,
                reason=str(parsed.get("error") or "activity_not_recorded"),
            )
            return False
        self._check_server_version(parsed.get("crosswatch_version"))
        _log.info("reporter.posted", url=self._safe_url, event=kind)
        return True

    def _check_server_version(self, reported: Any) -> None:
        """Warn once. A server older than this coerces a missing percent to zero, which
        overwrites the viewer's real resume point in the downstream sink."""
        if self._warned_old_server or _MIN_VERSION is None or not isinstance(reported, str):
            return
        found = _version_tuple(reported)
        if found is not None and found < _MIN_VERSION:
            self._warned_old_server = True
            _log.warning("reporter.server_too_old", found=reported, expected=MIN_CROSSWATCH_VERSION)


class ReporterQueue:
    """Keeps the network off the service thread.

    A full queue drops with a warning: blocking here would park the one thread that delivers
    callbacks and observes abort, which is worse than losing a progress event.
    """

    def __init__(
        self,
        sink: EventSink,
        device: Device,
        abort: threading.Event,
        maxsize: int = DEFAULT_QUEUE_SIZE,
    ) -> None:
        self._sink = sink
        self._device = device
        self._queue: queue.Queue[Event] = queue.Queue(maxsize=maxsize)
        self._abort = abort
        self._stopping = threading.Event()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="crosswatch-reporter", daemon=True)
        self._thread.start()

    def submit(self, event: Event) -> bool:
        if self._stopping.is_set():
            _log.warning("reporter.dropped", event=event_kind(event), reason="stopping")
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            _log.warning("reporter.dropped", event=event_kind(event), reason="queue_full")
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
        # Set first: a reporter mid-backoff gives up rather than spending the budget on
        # work the contract says to abandon.
        self._abort.set()
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
