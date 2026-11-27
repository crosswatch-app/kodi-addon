from __future__ import annotations

from dataclasses import replace
from typing import Any

from resources.lib.sync.writer import WriteReport
from tests.sync.fake_cw import error, ok
from tests.sync.test_engine_uploads import CwRig, _after_first_full

FILM = {
    "type": "movie",
    "title": "Film",
    "year": 2001,
    "ids": {"tmdb": "3"},
    "uniqueid": {"tmdb": "3"},
    "file": "/m/3.mkv",
}


def _change(seq: int, **params: Any) -> dict[str, Any]:
    return {"seq": seq, "change_id": f"c{seq}", "method": "VideoLibrary.SetMovieDetails",
            "params": {"movieid": 3, **params}, "item": dict(FILM), "after": 1, "queued_at": "2026-11-20T11:00:00Z"}  # fmt: skip


def _fetch(rig: CwRig) -> None:
    """Steps until the queue fetch went out: the inbox, re-checked after the last read, may
    come first."""
    vs = rig.engine.state.viewers["kv-a"]
    rig.engine._state = replace(rig.engine.state, viewers={"kv-a": replace(vs, queue_due=True)})
    before = len(rig.cw.sent_to("queue"))
    for _ in range(3):
        rig.engine.step()
        if len(rig.cw.sent_to("queue")) > before:
            return
    raise AssertionError("no queue fetch")


def test_round_trip_settles(tmp_path):
    """A tracker watch queued by CrossWatch: fetched, recorded, acked, written to Kodi."""
    rig = _after_first_full(tmp_path)
    rig.cw.script(
        "queue",
        ok(has_more=False, next_after=None, changes=[_change(100, playcount=1, lastplayed="2026-11-19 20:00:00")]),
    )
    _fetch(rig)
    [entry] = rig.store().entries("kv-a")
    assert entry.state.history.watched
    vs = rig.engine.state.viewers["kv-a"]
    assert vs.acks_pending and not vs.queue_due and vs.queue_settled and rig.engine.state.writes_pending
    rig.engine.step()
    [ack] = rig.cw.sent_to("queue/ack")
    assert ack.body is not None and ack.body["results"] == [{"seq": 100, "change_id": "c100", "status": "applied"}]
    assert rig.store().acks_pending("kv-a") == [] and not rig.engine.state.viewers["kv-a"].acks_pending
    rig.run(2)  # the change goes up in a delta, then the write
    assert rig.lib.row("movies", 3)["playcount"] == 1
    assert not rig.engine.state.writes_pending and rig.store().pending_writes() == []


def test_queue_pages_follow_next_after(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script(
        "queue",
        ok(has_more=True, next_after=100, changes=[_change(100, userrating=7)]),
        ok(has_more=False, next_after=None, changes=[_change(101, playcount=1, lastplayed="2026-11-19 20:00:00")]),
    )
    _fetch(rig)
    first, second = rig.cw.sent_to("queue")
    assert (first.query["after"], second.query["after"]) == ("0", "100")
    [entry] = rig.store().entries("kv-a")
    assert entry.state.history.watched and entry.state.rating.value == 7
    assert [a["seq"] for a in rig.store().acks_pending("kv-a")] == [100, 101]


def test_queue_403_does_not_pause(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script("queue", error(403, "sync_disabled"))
    _fetch(rig)
    state = rig.engine.state
    assert not state.paused and state.viewers["kv-a"].error == "refused" and state.viewers["kv-a"].queue_due


def test_lost_ack_resent(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script("queue", ok(has_more=False, next_after=None, changes=[_change(100, userrating=7)]))
    rig.cw.script("queue/ack", error(503, "temporarily_unavailable", retryable=True), ok())
    _fetch(rig)
    rig.engine.step()
    assert len(rig.store().acks_pending("kv-a")) == 1
    rig.clock.now += 60
    rig.judged(upload_seq=rig.store().seqs("kv-a").sent)
    for _ in range(4):
        rig.engine.step()
    first, again = rig.cw.sent_to("queue/ack")
    assert first.body == again.body and rig.store().acks_pending("kv-a") == []


def test_refused_acks_dropped(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script("queue", ok(has_more=False, next_after=None, changes=[_change(100, userrating=7)]))
    rig.cw.script("queue/ack", error(400, "invalid_request"))
    _fetch(rig)
    rig.engine.step()
    assert rig.store().acks_pending("kv-a") == [] and not rig.engine.state.viewers["kv-a"].acks_pending


def test_no_writes_while_playing(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script(
        "queue",
        ok(has_more=False, next_after=None, changes=[_change(100, playcount=1, lastplayed="2026-11-19 20:00:00")]),
    )
    _fetch(rig)
    rig.engine.step()  # the ack
    rig.engine.on_quiet(False)
    rig.run(3)
    assert rig.lib.row("movies", 3)["playcount"] == 0
    rig.engine.on_quiet(True)
    rig.run(3)
    assert rig.lib.row("movies", 3)["playcount"] == 1


def test_writes_stop_when_playback_starts_mid_run(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script(
        "queue",
        ok(has_more=False, next_after=None, changes=[_change(100, playcount=1, lastplayed="2026-11-19 20:00:00")]),
    )
    _fetch(rig)
    rig.engine.step()  # the ack
    rig.engine.on_quiet(False)
    rig.engine._write_kodi()
    assert rig.lib.row("movies", 3)["playcount"] == 0 and rig.engine.state.writes_pending


def test_mark_during_a_write_run_is_committed_between_batches(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script("queue", ok(has_more=False, next_after=None, changes=[_change(100, userrating=7)]))
    _fetch(rig)
    rig.engine.step()  # the ack
    rig.mark()
    rig.engine.step()
    assert rig.inbox.pending() == []
    assert rig.store().entries("kv-a")[0].state.history.watched


def test_acks_go_up_in_batches_that_shrink_after_a_413(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script(
        "queue", ok(has_more=False, next_after=None, changes=[_change(100 + n, userrating=n % 10) for n in range(4)])
    )
    rig.cw.script("queue/ack", error(413, "payload_too_large"))
    _fetch(rig)
    vs = rig.engine.state.viewers["kv-a"]
    rig.engine._state = replace(rig.engine.state, viewers={"kv-a": replace(vs, ack_batch=4)})
    rig.engine.step()
    assert rig.engine.state.viewers["kv-a"].ack_batch == 2 and not rig.engine.state.viewers["kv-a"].uploads_stopped
    rig.clock.now += 60
    rig.judged(upload_seq=rig.store().seqs("kv-a").sent)
    rig.run(4)
    sizes = [len(s.body["results"]) for s in rig.cw.sent_to("queue/ack") if s.body]
    assert sizes == [4, 2, 2] and rig.store().acks_pending("kv-a") == []


def test_more_pages_without_a_next_after_settle_nothing(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script("queue", ok(has_more=True, next_after=None, changes=[_change(100, userrating=7)]))
    _fetch(rig)
    vs = rig.engine.state.viewers["kv-a"]
    assert vs.queue_due and not vs.queue_settled and vs.acks_pending


def test_failed_second_page_keeps_what_the_first_owed(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script(
        "queue",
        ok(has_more=True, next_after=100, changes=[_change(100, userrating=7)]),
        error(400, "invalid_request"),
    )
    _fetch(rig)
    vs = rig.engine.state.viewers["kv-a"]
    assert vs.acks_pending and rig.store().acks_pending("kv-a")


def test_drain_between_write_batches_keeps_its_checks(tmp_path, monkeypatch):
    rig = _after_first_full(tmp_path)
    rig.cw.script("queue", ok(has_more=False, next_after=None, changes=[_change(100, userrating=7)]))
    _fetch(rig)
    rig.mark()
    calls = []

    class Between:
        def __init__(self, *args, between_batches, **kwargs):
            self.between = between_batches

        def run(self):
            rig.engine.on_quiet(False)
            self.between()
            calls.append(len(rig.inbox.pending()))
            return WriteReport()

    monkeypatch.setattr("resources.lib.sync.engine.Writer", Between)
    rig.engine._write_kodi()
    assert calls == [1]
