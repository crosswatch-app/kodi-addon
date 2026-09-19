import json
import threading
from typing import Any

import pytest

from resources.lib import log as logmod
from resources.lib.models import Device, EventKind, MediaItem, PlaybackEvent
from resources.lib.reporter import (
    EventSink,
    HttpReporter,
    InvalidWebhookUrl,
    LogReporter,
    ReporterQueue,
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


def test_log_reporter_reports_success_and_keeps_names_out_of_info(lines):
    assert LogReporter().report(_event(), DEVICE) is True
    info = [line for line in lines if "reporter.event" in line]
    assert info and "anna" not in info[0]


def test_http_reporter_posts_the_built_payload():
    connection = FakeConnection()
    reporter = HttpReporter("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    assert reporter.report(_event(), DEVICE) is True
    method, path, body = connection.requests[0]
    assert method == "POST"
    assert path == "/webhook/kodi?profile=tok"
    assert json.loads(body)["event"] == "stop"


def test_http_reporter_reuses_one_connection():
    connection = FakeConnection()
    made: list[int] = []

    def factory(*args, **kwargs):
        made.append(1)
        return connection

    reporter = HttpReporter("http://host/webhook/kodi?profile=tok", connection_factory=factory)
    reporter.report(_event(), DEVICE)
    reporter.report(_event(), DEVICE)
    assert len(made) == 1


def test_an_ignored_response_is_not_delivery(lines):
    connection = FakeConnection(FakeResponse(200, b'{"ok": true, "ignored": true, "error": "invalid_profile"}'))
    reporter = HttpReporter("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    assert reporter.report(_event(), DEVICE) is False
    assert any("reporter.rejected" in line and "invalid_profile" in line for line in lines)


def test_activity_not_recorded_is_not_delivery(lines):
    connection = FakeConnection(FakeResponse(200, b'{"activity_recorded": false, "targets": []}'))
    reporter = HttpReporter("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    assert reporter.report(_event(), DEVICE) is False
    assert any("reporter.rejected" in line for line in lines)


def test_a_5xx_is_not_delivery(lines):
    connection = FakeConnection(FakeResponse(502, b"bad gateway"))
    reporter = HttpReporter("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    assert reporter.report(_event(), DEVICE) is False


def test_a_transport_failure_is_reported_and_never_logs_the_token(lines):
    connection = FakeConnection(raises=OSError("connection refused to http://host/webhook/kodi?profile=tok"))
    reporter = HttpReporter("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    assert reporter.report(_event(), DEVICE) is False
    assert any("reporter.failed" in line for line in lines)
    assert not any("tok" in line.split("<redacted>")[-1] and "profile=tok" in line for line in lines)


def test_a_failed_connection_is_dropped_so_the_next_call_reconnects():
    connections = [FakeConnection(raises=OSError("broken")), FakeConnection()]
    reporter = HttpReporter("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connections.pop(0))
    assert reporter.report(_event(), DEVICE) is False
    assert reporter.report(_event(), DEVICE) is True


def test_queue_delivers_submitted_events():
    delivered: list[PlaybackEvent] = []
    done = threading.Event()

    class Recorder:
        def report(self, event, device):
            delivered.append(event)
            done.set()
            return True

    sink: EventSink = Recorder()
    queue = ReporterQueue(sink, DEVICE)
    assert queue.submit(_event()) is True
    assert done.wait(2.0)
    queue.stop()
    assert [e.event_id for e in delivered] == ["e-1"]


def test_queue_drops_rather_than_blocking_when_full(lines):
    release = threading.Event()

    class Slow:
        def report(self, event, device):
            release.wait(1.0)
            return True

    queue = ReporterQueue(Slow(), DEVICE, maxsize=1)
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

        def report(self, event, device):
            if self.first:
                self.first = False
                raise RuntimeError("boom")
            delivered.append(event.event_id)
            done.set()
            return True

    queue = ReporterQueue(Flaky(), DEVICE)
    queue.submit(_event())
    queue.submit(_event())
    assert done.wait(2.0)
    queue.stop()
    assert delivered == ["e-1"]


def test_submit_after_stop_reports_failure_rather_than_pretending(lines):
    queue = ReporterQueue(LogReporter(), DEVICE)
    queue.stop()
    assert queue.submit(_event()) is False
    assert any("reporter.dropped" in line and "stopping" in line for line in lines)


def test_stop_drains_what_is_queued_before_returning():
    delivered: list[str] = []

    class Recorder:
        def report(self, event, device):
            delivered.append(event.event_id)
            return True

    queue = ReporterQueue(Recorder(), DEVICE, maxsize=10)
    for _ in range(5):
        queue.submit(_event())
    assert queue.stop(deadline=2.0) == 0
    assert len(delivered) == 5


def test_stop_is_idempotent():
    queue = ReporterQueue(LogReporter(), DEVICE)
    assert queue.stop() == 0
    assert queue.stop() == 0


def test_stop_reports_what_it_could_not_deliver(lines):
    release = threading.Event()

    class Slow:
        def report(self, event, device):
            release.wait(5.0)
            return True

    queue = ReporterQueue(Slow(), DEVICE, maxsize=10)
    for _ in range(5):
        queue.submit(_event())
    undelivered = queue.stop(deadline=0.2)
    release.set()
    assert undelivered > 0
    assert any("reporter.undelivered" in line for line in lines)


def test_stop_closes_a_sink_that_holds_a_connection():
    connection = FakeConnection()
    reporter = HttpReporter("http://host/webhook/kodi?profile=tok", connection_factory=lambda *a, **k: connection)
    reporter.report(_event(), DEVICE)
    ReporterQueue(reporter, DEVICE).stop()
    assert connection.closed is True
