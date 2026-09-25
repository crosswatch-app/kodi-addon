import json

import pytest

from resources.lib import log as logmod
from resources.lib.constants import OUTBOX_MAX_AGE_SECONDS, OUTBOX_MAX_ENTRIES, PING_INTERVAL_SECONDS
from resources.lib.outbox import Outbox, config_fingerprint

FINGERPRINT = config_fingerprint("http://host/webhook/kodiwatcher", "tok")
DAY = 86_400.0


class Clocks:
    """Wall and monotonic time, moved by hand. The outbox uses each for a different job."""

    def __init__(self) -> None:
        self.wall = 1_800_000_000.0
        self.mono = 0.0


@pytest.fixture
def clocks():
    return Clocks()


@pytest.fixture
def lines():
    logmod.reset()
    captured: list[str] = []
    logmod.configure(log_dir=None, debug=False, sink=lambda msg, level: captured.append(msg))
    yield captured
    logmod.reset()


def _outbox(tmp_path, clocks, fingerprint=FINGERPRINT, **kwargs) -> Outbox:
    outbox = Outbox(
        str(tmp_path / "outbox.json"),
        fingerprint,
        wall=lambda: clocks.wall,
        clock=lambda: clocks.mono,
        **kwargs,
    )
    outbox.load()
    return outbox


def _body(event_id: str) -> dict:
    return {"event": "stop", "event_id": event_id, "sent_at": "2026-09-25T07:00:00Z"}


def test_a_stored_entry_survives_a_restart(tmp_path, clocks):
    _outbox(tmp_path, clocks).add(_body("e-1"), "stop")
    reloaded = _outbox(tmp_path, clocks)
    entry = reloaded.next_due()
    assert entry is not None and entry.body == _body("e-1")


def test_the_token_itself_is_never_written(tmp_path, clocks):
    _outbox(tmp_path, clocks).add(_body("e-1"), "stop")
    assert "tok" not in (tmp_path / "outbox.json").read_text(encoding="utf-8")


def test_a_delivered_entry_is_gone_after_a_restart(tmp_path, clocks):
    outbox = _outbox(tmp_path, clocks)
    outbox.add(_body("e-1"), "stop")
    outbox.delivered("e-1")
    assert _outbox(tmp_path, clocks).pending() == 0


def test_a_refused_entry_is_dropped_with_a_warning(tmp_path, clocks, lines):
    outbox = _outbox(tmp_path, clocks)
    outbox.add(_body("e-1"), "stop")
    outbox.refused("e-1")
    assert outbox.pending() == 0
    assert any("reporter.outbox_refused" in line for line in lines)


def test_a_deferred_entry_is_due_again_after_the_interval(tmp_path, clocks):
    outbox = _outbox(tmp_path, clocks)
    outbox.add(_body("e-1"), "stop")
    clocks.mono += PING_INTERVAL_SECONDS
    outbox.defer("e-1")
    assert outbox.next_due() is None
    clocks.mono += PING_INTERVAL_SECONDS
    entry = outbox.next_due()
    assert entry is not None and entry.event_id == "e-1"


def test_a_new_entry_waits_an_interval_while_the_live_queue_tries_it(tmp_path, clocks):
    """The live queue makes the first attempts; the outbox takes over only if those fail."""
    outbox = _outbox(tmp_path, clocks)
    outbox.add(_body("e-1"), "stop")
    assert outbox.next_due() is None
    clocks.mono += PING_INTERVAL_SECONDS
    assert outbox.next_due() is not None


def test_entries_from_a_previous_run_are_due_at_once(tmp_path, clocks):
    _outbox(tmp_path, clocks).add(_body("e-1"), "stop")
    assert _outbox(tmp_path, clocks).next_due() is not None


def test_the_oldest_due_entry_comes_first(tmp_path, clocks):
    outbox = _outbox(tmp_path, clocks)
    outbox.add(_body("e-1"), "stop")
    outbox.add(_body("e-2"), "stop")
    clocks.mono += PING_INTERVAL_SECONDS
    entry = outbox.next_due()
    assert entry is not None and entry.event_id == "e-1"


def test_an_entry_older_than_the_age_limit_is_dropped_at_load(tmp_path, clocks, lines):
    _outbox(tmp_path, clocks).add(_body("e-1"), "stop")
    clocks.wall += OUTBOX_MAX_AGE_SECONDS + 1
    assert _outbox(tmp_path, clocks).pending() == 0
    assert any("reporter.outbox_dropped" in line and "reason=age" in line for line in lines)


def test_an_entry_just_inside_the_age_limit_is_kept(tmp_path, clocks):
    _outbox(tmp_path, clocks).add(_body("e-1"), "stop")
    clocks.wall += OUTBOX_MAX_AGE_SECONDS - 1
    assert _outbox(tmp_path, clocks).pending() == 1


def test_an_entry_from_the_future_is_kept_rather_than_judged_on_a_wrong_clock(tmp_path, clocks):
    """A box with no real-time clock can store an entry before NTP sync, or load one before it."""
    _outbox(tmp_path, clocks).add(_body("e-1"), "stop")
    clocks.wall -= 30 * DAY
    assert _outbox(tmp_path, clocks).pending() == 1


def test_an_entry_stored_for_a_different_webhook_is_dropped_at_load(tmp_path, clocks, lines):
    _outbox(tmp_path, clocks).add(_body("e-1"), "stop")
    other = config_fingerprint("http://host/webhook/kodiwatcher", "new-token")
    assert _outbox(tmp_path, clocks, fingerprint=other).pending() == 0
    assert any("reporter.outbox_dropped" in line and "reason=config" in line for line in lines)


def test_the_cap_drops_the_oldest_entry(tmp_path, clocks, lines):
    outbox = _outbox(tmp_path, clocks, max_entries=2)
    for event_id in ("e-1", "e-2", "e-3"):
        outbox.add(_body(event_id), "stop")
    assert outbox.pending() == 2
    clocks.mono += PING_INTERVAL_SECONDS
    entry = outbox.next_due()
    assert entry is not None and entry.event_id == "e-2"
    assert any("reporter.outbox_dropped" in line and "reason=cap" in line for line in lines)


def test_the_default_cap_is_the_specified_one():
    assert OUTBOX_MAX_ENTRIES == 200
    assert OUTBOX_MAX_AGE_SECONDS == 7 * DAY


def test_a_corrupt_file_is_set_aside_not_overwritten(tmp_path, clocks, lines):
    path = tmp_path / "outbox.json"
    path.write_text("{not json", encoding="utf-8")
    outbox = _outbox(tmp_path, clocks)
    assert outbox.pending() == 0
    assert (tmp_path / "outbox.json.corrupt").read_text(encoding="utf-8") == "{not json"
    assert any("reporter.outbox_corrupt" in line for line in lines)


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"version": 1},
        {"version": 1, "entries": {}},
        {"version": 1, "entries": [{"event_id": "e-1"}]},
        {"version": 1, "entries": [{"event_id": "e-1", "kind": "stop", "body": [], "stored_at": 1.0, "fingerprint": "x"}]},
    ],
)
def test_a_file_of_the_wrong_shape_is_treated_as_corrupt(tmp_path, clocks, document):
    (tmp_path / "outbox.json").write_text(json.dumps(document), encoding="utf-8")
    assert _outbox(tmp_path, clocks).pending() == 0
    assert (tmp_path / "outbox.json.corrupt").exists()


def test_a_missing_file_is_simply_empty(tmp_path, clocks, lines):
    assert _outbox(tmp_path, clocks).pending() == 0
    assert not any("outbox_corrupt" in line for line in lines)


def test_a_failed_write_keeps_the_entry_in_memory_and_says_so(tmp_path, clocks, lines):
    """Degrades to delivery without persistence rather than to losing the entry now."""
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("", encoding="utf-8")
    outbox = Outbox(str(blocked / "outbox.json"), FINGERPRINT, wall=lambda: clocks.wall, clock=lambda: clocks.mono)
    outbox.load()
    assert outbox.add(_body("e-1"), "stop") is False
    assert outbox.pending() == 1
    assert any("reporter.outbox_write_failed" in line for line in lines)


def test_log_lines_carry_no_names_or_titles(tmp_path, clocks, lines):
    body = dict(_body("e-1"), viewers=["anna"], media={"title": "Example Show"})
    outbox = _outbox(tmp_path, clocks)
    outbox.add(body, "stop")
    outbox.refused("e-1")
    assert lines
    assert not any("anna" in line or "Example Show" in line for line in lines)
