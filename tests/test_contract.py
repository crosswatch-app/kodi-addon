# SPDX-License-Identifier: GPL-2.0-only
"""The addon against a socket, checked against docs/contract.md."""

import threading
from dataclasses import replace

import pytest

from resources.lib.models import Device, MediaItem, PingEvent, PlaybackEvent
from resources.lib.reporter import HttpReporter
from tests.stub_receiver import StubCrossWatch

DEVICE = Device(id="device-1", name="Living room", addon_version="1.0.0")

EPISODE = MediaItem(
    media_type="episode",
    library_id=5,
    show_library_id=42,
    title="Example show",
    year=2022,
    season=1,
    episode=1,
    episode_title="Example episode",
    show_ids={"tvdb": "416491", "tmdb": "157744", "imdb": "tt18335752"},
    episode_ids={"tvdb": "9032340"},
    file="smb://nas/tv/Example/S01E01.mkv",
    source="library",
)

BASE_EVENT = PlaybackEvent(
    kind="stop",
    event_id="e-1",
    session_id="s-1",
    sent_at="2026-09-19T20:00:00Z",
    media=EPISODE,
    viewers=("anna",),
    viewers_source="playlist",
    position_ms=1_000,
    duration_ms=2_000,
    percent=50.0,
)


def _event(**overrides) -> PlaybackEvent:
    return replace(BASE_EVENT, **overrides)


@pytest.fixture
def stub():
    with StubCrossWatch() as server:
        yield server


@pytest.fixture
def reporter(stub):
    """A reporter pointed at the stub, never sleeping for real, always closed.

    Every contract test goes through this. Without the fake sleeper a transient stub failure
    under CI load turns a red gate into a multi-minute hang that reads as a stuck job; without
    the close the stub's keep-alive handler thread outlives the test.
    """
    built: list[HttpReporter] = []

    def build(**kwargs) -> HttpReporter:
        kwargs.setdefault("token", "stub-token")
        kwargs.setdefault("abort", threading.Event())
        kwargs.setdefault("sleeper", lambda seconds: False)
        made = HttpReporter(stub.url, **kwargs)
        built.append(made)
        return made

    yield build
    for made in built:
        made.close()


def test_the_request_line_and_headers_match_the_contract(stub, reporter):
    assert reporter().report(_event(), DEVICE) is True
    sent = stub.received[0]
    assert sent["path"] == "/webhook/kodiwatcher?token=stub-token"
    assert sent["headers"]["Content-Type"] == "application/json"
    assert sent["headers"]["X-CrossWatch-Token"] == "stub-token"


def test_the_body_matches_the_documented_shape(stub, reporter):
    reporter().report(_event(), DEVICE)
    body = stub.received[0]["body"]
    assert body["version"] == 1
    assert body["event"] == "stop"
    assert body["addon_version"] == "1.0.0"
    assert body["device"] == {"id": "device-1", "name": "Living room"}
    assert body["viewers"] == ["anna"]
    assert "progress" not in body
    media = body["media"]
    assert media["type"] == "episode"
    assert media["source"] == "library"
    assert media["percent"] == 50.0
    assert media["position_ms"] == 1_000
    assert media["duration_ms"] == 2_000
    assert "cover" not in media
    assert media["ids"] == {
        "tvdb_show": "416491",
        "tmdb_show": "157744",
        "imdb_show": "tt18335752",
        "tvdb": "9032340",
    }


def test_an_unknown_duration_omits_percent_on_the_wire(stub, reporter):
    reporter().report(_event(percent=None, duration_ms=None), DEVICE)
    media = stub.received[0]["body"]["media"]
    assert "percent" not in media
    assert "duration_ms" not in media
    assert media["position_ms"] == 1_000


def test_a_ping_has_no_media_and_no_session(stub, reporter):
    ping = PingEvent(event_id="p-1", sent_at="2026-09-19T20:00:00Z", viewers=("anna", "bob"), pkc_skipped=2)
    assert reporter().report(ping, DEVICE) is True
    body = stub.received[0]["body"]
    assert body["event"] == "ping"
    assert body["viewers"] == ["anna", "bob"]
    assert body["pkc_skipped"] == 2
    assert "media" not in body
    assert "session_id" not in body


def test_an_ignored_response_is_not_delivery_and_is_not_retried(stub, reporter):
    stub.respond(200, {"ok": True, "ignored": True, "error": "invalid_token"})
    assert reporter().report(_event(), DEVICE) is False
    assert len(stub.received) == 1


def test_a_401_is_not_retried(stub, reporter):
    stub.respond(401, {"error": "bad token"})
    assert reporter().report(_event(), DEVICE) is False
    assert len(stub.received) == 1


def test_a_5xx_is_retried_and_then_succeeds(stub, reporter):
    stub.respond(fail_times=2)
    assert reporter().report(_event(), DEVICE) is True
    assert len(stub.received) == 3


def test_a_refused_connection_is_reported_as_undelivered():
    """The budget has to be spent on a virtual clock, or the retry loop never terminates.

    A refused connection fails instantly, so wall time alone would never exhaust the budget
    and a no-op sleeper would spin. The sleeper advances the clock, exactly as sleeping does
    in production.
    """
    # Bind and immediately close, so the port is almost certainly free and refusing.
    stub = StubCrossWatch()
    url = stub.url
    stub.close()
    now = [0.0]

    def sleeper(seconds: float) -> bool:
        now[0] += seconds
        return False

    made = HttpReporter(
        url, token="stub-token", abort=threading.Event(), clock=lambda: now[0], sleeper=sleeper
    )
    assert made.report(_event(), DEVICE) is False


def test_no_token_appears_in_any_log_line(stub, reporter):
    from resources.lib import log as logmod

    logmod.reset()
    captured: list[str] = []
    logmod.configure(log_dir=None, debug=True, sink=lambda msg, level: captured.append(msg))
    try:
        reporter().report(_event(), DEVICE)
    finally:
        logmod.reset()
    assert captured
    assert not any("stub-token" in line for line in captured)
