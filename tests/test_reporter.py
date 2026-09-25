import itertools
import json
import threading
import time
from typing import Any

import pytest

from resources.lib import log as logmod
from resources.lib.constants import (
    HTTP_TIMEOUT_SECONDS,
    RETRY_BUDGET_SECONDS,
    SHUTDOWN_HTTP_TIMEOUT_SECONDS,
)
from resources.lib.models import Device, EventKind, MediaItem, PingEvent, PlaybackEvent
from resources.lib.reporter import (
    EventSink,
    HttpReporter,
    InvalidWebhookUrl,
    LogReporter,
    ReporterQueue,
    event_kind,
    validate_webhook_url,
)

DEVICE = Device(id="htpc-1", name="Living room")

MEDIA = MediaItem(
    media_type="episode",
    library_id=5,
    show_library_id=42,
    title="Example",
    year=2026,
    season=1,
    episode=1,
    episode_title=None,
)


def _event(kind: EventKind = "stop") -> PlaybackEvent:
    return PlaybackEvent(
        kind=kind,
        event_id="e-1",
        session_id="s-1",
        sent_at="2026-09-19T20:00:00Z",
        media=MEDIA,
        viewers=("anna",),
        viewers_source="playlist",
        position_ms=1_000,
        duration_ms=2_000,
        percent=50.0,
    )


@pytest.fixture
def lines():
    logmod.reset()
    captured: list[str] = []
    logmod.configure(log_dir=None, debug=True, sink=lambda msg, level: captured.append(msg))
    yield captured
    logmod.reset()


@pytest.fixture
def reporter_factory():
    """Build reporters on a virtual clock that the fake sleeper advances.

    Both halves matter. A real sleeper makes a retrying test block for the whole budget. But
    a no-op sleeper on a REAL clock is worse than it looks: the budget is then consumed only
    by wall time, so a permanently failing peer spins at full speed for two minutes instead
    of failing fast. Sleeping has to move the clock, exactly as it does in production.
    """
    abort = threading.Event()
    now = [0.0]

    def build(url: str, **kwargs) -> HttpReporter:
        def sleeper(seconds: float) -> bool:
            now[0] += seconds
            return False

        kwargs.setdefault("token", "tok")
        kwargs.setdefault("abort", abort)
        kwargs.setdefault("clock", lambda: now[0])
        kwargs.setdefault("sleeper", sleeper)
        return HttpReporter(url, **kwargs)

    return build


class FakeResponse:
    def __init__(self, status=200, body=b'{"ok": true}') -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class FakeConnection:
    def __init__(self, response: FakeResponse | None = None, raises: Exception | None = None) -> None:
        self.response = response or FakeResponse()
        self.raises = raises
        self.requests: list[tuple[str, str, Any]] = []
        self.closed = False

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> None:
        if self.raises:
            raise self.raises
        self.requests.append((method, path, body))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def test_validate_accepts_http_and_https():
    assert validate_webhook_url("http://host/webhook/kodi?profile=t").startswith("http://")
    assert validate_webhook_url("https://host/webhook/kodi?profile=t").startswith("https://")


def test_validate_rejects_a_file_url():
    with pytest.raises(InvalidWebhookUrl):
        validate_webhook_url("file:///etc/passwd")


def test_validate_rejects_a_scheme_less_url():
    with pytest.raises(InvalidWebhookUrl):
        validate_webhook_url("crosswatch.local/webhook/kodi?profile=t")


def test_validate_error_never_contains_the_token():
    try:
        validate_webhook_url("crosswatch.local/webhook/kodi?profile=secrettoken")
    except InvalidWebhookUrl as exc:
        assert "secrettoken" not in str(exc)
    else:
        raise AssertionError("expected InvalidWebhookUrl")


def test_log_reporter_reports_success_and_keeps_names_out_of_info(lines, reporter_factory):
    assert LogReporter().report(_event(), DEVICE) is True
    info = [line for line in lines if "reporter.event" in line]
    assert info and "anna" not in info[0]


def test_http_reporter_posts_the_built_payload(reporter_factory):
    connection = FakeConnection()
    reporter = reporter_factory("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    assert reporter.report(_event(), DEVICE) is True
    method, path, body = connection.requests[0]
    assert method == "POST"
    assert path == "/webhook/kodi?profile=tok"
    assert json.loads(body)["event"] == "stop"


def test_http_reporter_reuses_one_connection(reporter_factory):
    connection = FakeConnection()
    made: list[int] = []

    def factory(*args, **kwargs):
        made.append(1)
        return connection

    reporter = reporter_factory("http://host/webhook/kodi?profile=tok", connection_factory=factory)
    reporter.report(_event(), DEVICE)
    reporter.report(_event(), DEVICE)
    assert len(made) == 1


def test_an_ignored_response_is_not_delivery(lines, reporter_factory):
    connection = FakeConnection(FakeResponse(200, b'{"ok": true, "ignored": true, "error": "invalid_profile"}'))
    reporter = reporter_factory("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    assert reporter.report(_event(), DEVICE) is False
    assert any("reporter.rejected" in line and "invalid_profile" in line for line in lines)


def test_activity_not_recorded_is_not_delivery(lines, reporter_factory):
    connection = FakeConnection(FakeResponse(200, b'{"activity_recorded": false, "targets": []}'))
    reporter = reporter_factory("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    assert reporter.report(_event(), DEVICE) is False
    assert any("reporter.rejected" in line for line in lines)


def test_a_persistent_5xx_is_retried_then_given_up_on(lines, reporter_factory):
    connection = FakeConnection(FakeResponse(502, b"bad gateway"))
    reporter = reporter_factory("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    assert reporter.report(_event(), DEVICE) is False


def test_a_persistent_transport_failure_never_logs_the_token(lines, reporter_factory):
    connection = FakeConnection(raises=OSError("connection refused to http://host/webhook/kodi?profile=tok"))
    reporter = reporter_factory("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    assert reporter.report(_event(), DEVICE) is False
    assert any("reporter.failed" in line for line in lines)
    assert not any("tok" in line.split("<redacted>")[-1] and "profile=tok" in line for line in lines)


def test_a_failed_connection_is_dropped_so_the_retry_reconnects(reporter_factory):
    """Retry makes this one call, not two: the broken socket is dropped and the retry wins."""
    made: list[int] = []
    connections = [FakeConnection(raises=OSError("broken")), FakeConnection()]

    def factory(*args, **kwargs):
        made.append(1)
        return connections.pop(0)

    reporter = reporter_factory("http://host/webhook/kodi?profile=tok", connection_factory=factory)
    assert reporter.report(_event(), DEVICE) is True
    assert len(made) == 2, "the failed connection must be dropped, not reused"


def test_queue_delivers_submitted_events():
    delivered: list[PlaybackEvent] = []
    done = threading.Event()

    class Recorder:
        def report(self, event, device, deadline=None):
            delivered.append(event)
            done.set()
            return True

    sink: EventSink = Recorder()
    queue = ReporterQueue(sink, DEVICE, abort=threading.Event())
    assert queue.submit(_event()) is True
    assert done.wait(2.0)
    queue.stop()
    assert [e.event_id for e in delivered] == ["e-1"]


def test_queue_drops_rather_than_blocking_when_full(lines, reporter_factory):
    release = threading.Event()

    class Slow:
        def report(self, event, device, deadline=None):
            release.wait(1.0)
            return True

    queue = ReporterQueue(Slow(), DEVICE, abort=threading.Event(), maxsize=1)
    results = [queue.submit(_event()) for _ in range(6)]
    release.set()
    queue.stop()
    assert False in results
    assert any("reporter.dropped" in line for line in lines)


def test_a_sink_that_raises_does_not_kill_the_worker():
    delivered: list[str] = []
    done = threading.Event()

    class Flaky:
        def __init__(self):
            self.first = True

        def report(self, event, device, deadline=None):
            if self.first:
                self.first = False
                raise RuntimeError("boom")
            delivered.append(event.event_id)
            done.set()
            return True

    queue = ReporterQueue(Flaky(), DEVICE, abort=threading.Event())
    queue.submit(_event())
    queue.submit(_event())
    assert done.wait(2.0)
    queue.stop()
    assert delivered == ["e-1"]


def test_submit_after_stop_reports_failure_rather_than_pretending(lines, reporter_factory):
    queue = ReporterQueue(LogReporter(), DEVICE, abort=threading.Event())
    queue.stop()
    assert queue.submit(_event()) is False
    assert any("reporter.dropped" in line and "stopping" in line for line in lines)


def test_stop_drains_what_is_queued_before_returning():
    delivered: list[str] = []

    class Recorder:
        def report(self, event, device, deadline=None):
            delivered.append(event.event_id)
            return True

    queue = ReporterQueue(Recorder(), DEVICE, abort=threading.Event(), maxsize=10)
    for _ in range(5):
        queue.submit(_event())
    assert queue.stop(deadline=2.0) == 0
    assert len(delivered) == 5


def test_stop_is_idempotent():
    queue = ReporterQueue(LogReporter(), DEVICE, abort=threading.Event())
    assert queue.stop() == 0
    assert queue.stop() == 0


def test_stop_reports_what_it_could_not_deliver(lines, reporter_factory):
    release = threading.Event()

    class Slow:
        def report(self, event, device, deadline=None):
            release.wait(5.0)
            return True

    queue = ReporterQueue(Slow(), DEVICE, abort=threading.Event(), maxsize=10)
    for _ in range(5):
        queue.submit(_event())
    undelivered = queue.stop(deadline=0.2)
    release.set()
    assert undelivered > 0
    assert any("reporter.undelivered" in line for line in lines)


def test_stop_closes_a_sink_that_holds_a_connection(reporter_factory):
    connection = FakeConnection()
    reporter = reporter_factory("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    reporter.report(_event(), DEVICE)
    ReporterQueue(reporter, DEVICE, abort=threading.Event()).stop()
    assert connection.closed is True


def test_a_4xx_is_never_retried(lines, reporter_factory):
    made: list[int] = []

    def factory(*args, **kwargs):
        made.append(1)
        return FakeConnection(FakeResponse(401, b"nope"))

    assert reporter_factory("http://host/hook?token=tok", connection_factory=factory).report(_event(), DEVICE) is False
    assert len(made) == 1
    assert any("reporter.rejected" in line and "401" in line for line in lines)


def test_a_5xx_is_retried_until_it_succeeds(reporter_factory):
    attempts = [FakeConnection(FakeResponse(503, b"busy")), FakeConnection()]
    reporter = reporter_factory("http://host/hook?token=tok", connection_factory=lambda *a, **k: attempts.pop(0))
    assert reporter.report(_event(), DEVICE) is True
    assert attempts == []


def test_a_lost_response_is_retried_rather_than_costing_the_event(lines, reporter_factory):
    """Retried on the CrossWatch maintainer's decision: his sinks absorb a duplicate.

    Scrobbles go to Trakt and Simkl's /scrobble/* endpoints, which dedupe server side and
    answer 409, and the media sinks set a watched flag, which is idempotent. So a second
    delivery is cheap and losing the event is not.
    """
    made: list[int] = []
    first = [True]

    class LostThenFine(FakeConnection):
        def getresponse(self):
            if first[0]:
                first[0] = False
                raise TimeoutError("read timed out")
            return FakeResponse()

    def factory(*args, **kwargs):
        made.append(1)
        return LostThenFine()

    assert reporter_factory("http://host/hook?token=tok", connection_factory=factory).report(_event(), DEVICE) is True
    assert len(made) == 2, "the retry must reconnect rather than reuse the broken socket"
    assert any("reporter.lost_response" in line for line in lines)


def test_a_lost_response_still_gives_up_inside_the_budget(lines, reporter_factory):
    """Retrying a lost response must not become unbounded."""

    class AlwaysLost(FakeConnection):
        def getresponse(self):
            raise TimeoutError("read timed out")

    reporter = reporter_factory("http://host/hook?token=tok", connection_factory=lambda *a, **k: AlwaysLost())
    assert reporter.report(_event(), DEVICE) is False
    assert any("reporter.gave_up" in line for line in lines)


def test_retry_gives_up_when_the_budget_cannot_pay_for_another_attempt(lines, reporter_factory):
    """The clock advances by the timeout per attempt, so the budget genuinely runs out."""
    attempts: list[int] = []

    def factory(*args, **kwargs):
        attempts.append(1)
        return FakeConnection(raises=OSError("refused"))

    reporter = reporter_factory(
        "http://host/hook?token=tok",
        connection_factory=factory,
        clock=itertools.count(0.0, 10.0).__next__,
    )
    assert reporter.report(_event(), DEVICE) is False
    assert 1 < len(attempts) < 20, f"bounded by the budget, got {len(attempts)} attempts"
    assert any("reporter.gave_up" in line and "budget_spent" in line for line in lines)


def test_the_backoff_never_sleeps_past_the_remaining_budget(reporter_factory):
    slept: list[float] = []

    def factory(*args, **kwargs):
        return FakeConnection(raises=OSError("refused"))

    reporter = reporter_factory(
        "http://host/hook?token=tok",
        connection_factory=factory,
        clock=itertools.count(0.0, 10.0).__next__,
        sleeper=lambda seconds: slept.append(seconds) or False,
    )
    reporter.report(_event(), DEVICE)
    assert slept, "at least one backoff"
    assert all(delay <= RETRY_BUDGET_SECONDS for delay in slept)


def test_an_abort_during_the_backoff_stops_the_retry_immediately(lines, reporter_factory):
    def factory(*args, **kwargs):
        return FakeConnection(raises=OSError("refused"))

    reporter = reporter_factory(
        "http://host/hook?token=tok", connection_factory=factory, sleeper=lambda seconds: True
    )
    assert reporter.report(_event(), DEVICE) is False
    assert any("reporter.gave_up" in line and "aborted" in line for line in lines)


def test_the_real_abort_event_cuts_a_retry_short():
    """No injected sleeper: this is the only test that exercises the production wiring.

    self._sleep defaults to abort.wait, so a pre-set event makes every backoff return
    immediately. Deleting that wiring, or ReporterQueue.stop()'s set(), must fail here.
    """
    abort = threading.Event()
    abort.set()
    attempts: list[int] = []

    def factory(*args, **kwargs):
        attempts.append(1)
        return FakeConnection(raises=OSError("refused"))

    reporter = HttpReporter("http://host/hook?token=tok", token="tok", abort=abort, connection_factory=factory)
    started = time.monotonic()
    assert reporter.report(_event(), DEVICE) is False
    assert time.monotonic() - started < 1.0, "a set abort must not sleep"
    assert len(attempts) == 1


def test_stop_sets_the_abort_event_the_reporter_waits_on():
    abort = threading.Event()
    queue = ReporterQueue(LogReporter(), DEVICE, abort=abort)
    assert abort.is_set() is False
    queue.stop()
    assert abort.is_set() is True


def test_the_token_is_sent_as_a_header(reporter_factory):
    connection = FakeConnection()
    captured: dict[str, Any] = {}

    def request(method, path, body=None, headers=None):
        captured["headers"] = headers or {}

    connection.request = request  # type: ignore[method-assign]
    reporter_factory("http://host/hook?token=tok", connection_factory=lambda *a, **k: connection).report(_event(), DEVICE)
    assert captured["headers"]["X-CrossWatch-Token"] == "tok"


def test_the_header_comes_from_the_token_argument_not_the_query_string(reporter_factory):
    """The credential is passed in, never recovered by parsing the URL.

    The two values differ on purpose. Production always builds the query from the same token,
    so a test that used one value either way would pass whether the header was taken from the
    argument or scraped back out of the query string, and would prove nothing. A URL carrying
    an unrelated parameter first is the case a positional parser gets wrong.
    """
    connection = FakeConnection()
    captured: dict[str, Any] = {}

    def request(method, path, body=None, headers=None):
        captured["headers"] = headers or {}

    connection.request = request  # type: ignore[method-assign]
    reporter = reporter_factory(
        "http://host/hook?instance=living-room&token=from-the-url",
        token="from-the-argument",
        connection_factory=lambda *a, **k: connection,
    )
    reporter.report(_event(), DEVICE)
    assert captured["headers"]["X-CrossWatch-Token"] == "from-the-argument"


def test_a_reporter_cannot_be_built_without_an_abort_event():
    """The shutdown guarantee must not be optional.

    The type checker already enforces it; the runtime assertion is here so the property is
    visible to a reader of the tests rather than only to pyright.
    """
    with pytest.raises(TypeError):
        HttpReporter("http://host/hook")  # type: ignore[call-arg]


def test_no_token_means_no_token_header(reporter_factory):
    connection = FakeConnection()
    captured: dict[str, Any] = {}

    def request(method, path, body=None, headers=None):
        captured["headers"] = headers or {}

    connection.request = request  # type: ignore[method-assign]
    reporter_factory("http://host/hook", token="", connection_factory=lambda *a, **k: connection).report(_event(), DEVICE)
    assert "X-CrossWatch-Token" not in captured["headers"]


def test_the_log_reporter_accepts_a_ping(lines):
    ping = PingEvent(event_id="p-1", sent_at="2026-09-19T20:00:00Z", viewers=("anna", "bob"), pkc_skipped=2)
    assert LogReporter().report(ping, DEVICE) is True
    assert any("reporter.ping" in line and "viewers_count=2" in line for line in lines)


def test_the_log_reporter_keeps_names_out_of_a_ping_line(lines):
    ping = PingEvent(event_id="p-1", sent_at="2026-09-19T20:00:00Z", viewers=("anna",))
    LogReporter().report(ping, DEVICE)
    assert not any("anna" in line for line in lines)


def test_a_dropped_ping_logs_its_kind_rather_than_crashing(lines):
    queue = ReporterQueue(LogReporter(), DEVICE, abort=threading.Event())
    queue.stop()
    ping = PingEvent(event_id="p-1", sent_at="2026-09-19T20:00:00Z", viewers=())
    assert queue.submit(ping) is False
    assert any("reporter.dropped" in line and "event=ping" in line for line in lines)


def test_event_kind_names_both_shapes():
    assert event_kind(_event("stop")) == "stop"
    assert event_kind(PingEvent(event_id="p", sent_at="t", viewers=())) == "ping"


def test_an_old_server_is_warned_about_once_not_every_event(lines, reporter_factory):
    body = b'{"ok": true, "crosswatch_version": "0.12.0"}'
    connection = FakeConnection(FakeResponse(200, body))
    reporter = reporter_factory(
        "http://host/webhook/kodiwatcher?token=tok", connection_factory=lambda *a, **k: connection
    )
    reporter.report(_event(), DEVICE)
    reporter.report(_event(), DEVICE)
    warnings = [line for line in lines if "reporter.server_too_old" in line]
    assert len(warnings) == 1
    assert "0.12.0" in warnings[0]


def test_a_current_server_produces_no_warning(lines, reporter_factory):
    body = b'{"ok": true, "crosswatch_version": "0.13.0"}'
    connection = FakeConnection(FakeResponse(200, body))
    reporter = reporter_factory(
        "http://host/webhook/kodiwatcher?token=tok", connection_factory=lambda *a, **k: connection
    )
    reporter.report(_event(), DEVICE)
    assert not any("reporter.server_too_old" in line for line in lines)


def test_an_unparseable_server_version_is_not_warned_about(lines, reporter_factory):
    body = b'{"ok": true, "crosswatch_version": "nightly"}'
    connection = FakeConnection(FakeResponse(200, body))
    reporter = reporter_factory(
        "http://host/webhook/kodiwatcher?token=tok", connection_factory=lambda *a, **k: connection
    )
    reporter.report(_event(), DEVICE)
    assert not any("reporter.server_too_old" in line for line in lines)


def test_a_connection_opened_after_abort_uses_the_short_timeout():
    """Kodi kills the interpreter 5000ms after abort; a ten second socket cannot fit."""
    abort = threading.Event()
    abort.set()
    seen: list[float] = []

    def factory(scheme, host, port, timeout):
        seen.append(timeout)
        return FakeConnection()

    HttpReporter("http://host/hook", token="t", abort=abort, connection_factory=factory).report(
        _event(), DEVICE
    )
    assert seen == [SHUTDOWN_HTTP_TIMEOUT_SECONDS]


def test_a_keep_alive_socket_is_dropped_once_abort_is_observed():
    """A socket opened before abort carries the long timeout, so it must not be reused."""
    abort = threading.Event()
    seen: list[float] = []

    def factory(scheme, host, port, timeout):
        seen.append(timeout)
        return FakeConnection()

    reporter = HttpReporter("http://host/hook", token="t", abort=abort, connection_factory=factory)
    reporter.report(_event(), DEVICE)
    abort.set()
    reporter.report(_event(), DEVICE)
    assert seen == [HTTP_TIMEOUT_SECONDS, SHUTDOWN_HTTP_TIMEOUT_SECONDS]


class ClosingConnection:
    """A server that closes an idle keep-alive connection, like uvicorn after ~5 seconds.

    The first request on a connection works. Any later one raises on getresponse, which is
    what http.client does when the peer closed the socket before the write.
    """

    def __init__(self) -> None:
        self.requests = 0
        self.used = False
        self.closed = 0

    def request(self, method, path, body=None, headers=None):
        self.requests += 1

    def getresponse(self):
        if self.used:
            raise Exception("Remote end closed connection without response")
        self.used = True
        return FakeResponse()

    def close(self):
        self.closed += 1


def test_a_server_closing_an_idle_keepalive_is_retried_on_a_fresh_connection(lines, reporter_factory):
    """Found against a real CrossWatch: every event after the first was being dropped.

    uvicorn closes an idle keep-alive after about five seconds. Progress is sixty seconds
    apart and the ping five minutes, so the connection is always past that. The write lands
    on a closed socket, getresponse raises, and treating that as a delivery the server may
    have processed loses an event it never saw.
    """
    made: list[ClosingConnection] = []

    def factory(*a, **k):
        made.append(ClosingConnection())
        return made[-1]

    reporter = reporter_factory("http://host/hook?token=t", connection_factory=factory)
    assert reporter.report(_event(), DEVICE) is True
    assert reporter.report(_event(), DEVICE) is True, "the second event must not be dropped"
    assert len(made) == 2, "the retry must use a fresh connection, not the closed one"


def test_a_caller_deadline_bounds_the_retry(reporter_factory):
    """The queue passes how much of the event's freshness window is left, not a fresh budget."""
    now = [0.0]
    attempts: list[float] = []

    def factory(*args, **kwargs):
        attempts.append(now[0])
        return FakeConnection(raises=OSError("refused"))

    def sleeper(seconds: float) -> bool:
        now[0] += seconds
        return False

    reporter = reporter_factory(
        "http://host/hook", connection_factory=factory, clock=lambda: now[0], sleeper=sleeper
    )
    assert reporter.report(_event(), DEVICE, deadline=30.0) is False
    assert attempts and max(attempts) <= 30.0


def test_a_deadline_already_passed_still_makes_one_attempt(reporter_factory):
    """An event handed over just inside its window deserves the one attempt it was queued for."""
    attempts: list[int] = []

    def factory(*args, **kwargs):
        attempts.append(1)
        return FakeConnection(raises=OSError("refused"))

    reporter = reporter_factory("http://host/hook", connection_factory=factory)
    assert reporter.report(_event(), DEVICE, deadline=-1.0) is False
    assert len(attempts) == 1


@pytest.mark.parametrize(
    ("response", "raises", "verdict"),
    [
        (FakeResponse(200), None, "accepted"),
        (FakeResponse(200, b'{"ok": true, "ignored": true}'), None, "refused"),
        (FakeResponse(401, b"{}"), None, "refused"),
        (FakeResponse(503, b"{}"), None, "unreachable"),
        (None, OSError("refused"), "unreachable"),
    ],
)
def test_send_once_reports_a_three_way_verdict(reporter_factory, response, raises, verdict):
    """The outbox has to tell a refusal, which ends an entry, from an outage, which defers it."""
    connection = FakeConnection(response, raises=raises)
    reporter = reporter_factory("http://host/hook", connection_factory=lambda *a, **k: connection)
    assert reporter.send_once({"event": "stop", "event_id": "e-1"}, "stop") == verdict


def test_send_once_never_retries(reporter_factory):
    attempts: list[int] = []

    def factory(*args, **kwargs):
        attempts.append(1)
        return FakeConnection(FakeResponse(503, b"{}"))

    reporter = reporter_factory("http://host/hook", connection_factory=factory)
    reporter.send_once({"event": "stop"}, "stop")
    assert len(attempts) == 1


def test_send_once_posts_the_stored_body_unchanged(reporter_factory):
    """A replay must be byte-identical to the first send, event_id and sent_at included."""
    connection = FakeConnection()
    reporter = reporter_factory("http://host/hook", connection_factory=lambda *a, **k: connection)
    body = {"event": "stop", "event_id": "e-1", "sent_at": "2026-09-25T07:00:00Z"}
    reporter.send_once(body, "stop")
    assert json.loads(connection.requests[0][2]) == body
