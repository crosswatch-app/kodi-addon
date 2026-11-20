from dataclasses import replace

import pytest

from resources.lib.sync.constants import BACKOFF_SECONDS
from resources.lib.sync.plan import (
    PING_ANSWER_SECONDS,
    Delta,
    DrainInbox,
    EngineState,
    FetchQueue,
    FullUpload,
    Idle,
    Ping,
    RatingScan,
    Reset,
    SendAcks,
    TakeRequests,
    ViewerState,
    Wait,
    WriteKodi,
    active,
    arm_gate,
    local_active,
    next_action,
    on_ping_asked,
    on_reply,
    waiting_on,
)
from resources.lib.sync.record import SeqState
from resources.lib.sync.reply import PingTicket, ReplyViewer, SyncReply

NOW = 1000.0


def _reply(*viewers: str, upload_seq: int = 5, queue_pending: int = 0, **kw) -> SyncReply:
    return SyncReply(
        enabled=kw.pop("enabled", True),
        reason=kw.pop("reason", None),
        viewers={v: ReplyViewer(instance="default", upload_seq=upload_seq) for v in viewers},
        queue_pending=queue_pending,
    )


def _state(**viewers: ViewerState) -> EngineState:
    return EngineState(
        started=True,
        sync_enabled=True,
        reply=_reply(*viewers),
        quiet=True,
        snapshot_usable=True,
        library_complete=True,
        viewers=viewers,
    )


READY = ViewerState(first_full_committed=True, judged=True)


# -- whether sync acts at all ----------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"started": False},
        {"sync_enabled": False},
        {"paused": True},
        {"record_missing": True},
        {"unauthorized": True},
        {"reply": None},
        {"reply": _reply("kv-a", enabled=False)},
        {"reply": _reply("kv-a", reason="library_has_sync_device")},
    ],
)
def test_idle_unless_started_enabled_and_answered(change):
    state = replace(_state(**{"kv-a": replace(READY, full_due=True)}), **change)
    assert not active(state)
    assert next_action(state, NOW) == Idle()


@pytest.mark.parametrize("change", [{"unauthorized": True}, {"reply": None}, {"reply": _reply("kv-a", enabled=False)}])
def test_kodi_side_work_goes_on_without_crosswatch(change):
    state = replace(
        _state(**{"kv-a": replace(READY, full_due=True)}), writes_pending=True, inbox_waiting=True, **change
    )
    assert local_active(state)
    assert next_action(state, NOW) == DrainInbox()
    state = replace(state, inbox_waiting=False)
    assert next_action(state, NOW) == WriteKodi()
    assert next_action(replace(state, writes_pending=False, rating_scan_due=True), NOW) == RatingScan()


@pytest.mark.parametrize(
    "change", [{"started": False}, {"sync_enabled": False}, {"paused": True}, {"record_missing": True}]
)
def test_no_kodi_side_work_when_off(change):
    state = replace(_state(), writes_pending=True, inbox_waiting=True, rating_scan_due=True, **change)
    assert next_action(state, NOW) == Idle()


def test_active_state_acts():
    state = _state(**{"kv-a": replace(READY, full_due=True)})
    assert active(state) and next_action(state, NOW) == FullUpload("kv-a")


def test_inbox_and_requests_first():
    state = replace(_state(**{"kv-a": replace(READY, acks_pending=True)}), inbox_waiting=True, requests_waiting=True)
    assert next_action(state, NOW) == TakeRequests()
    assert next_action(replace(state, requests_waiting=False), NOW) == DrainInbox()


def test_requests_taken_whatever_else_holds():
    state = replace(_state(), requests_waiting=True, quiet=False, paused=True, reply=None)
    assert next_action(state, NOW) == TakeRequests()
    assert next_action(replace(state, record_missing=True), NOW) == Idle()


@pytest.mark.parametrize("change", [{"quiet": False}, {"scanning": True}, {"snapshot_usable": False}])
def test_inbox_waits_for_quiet_and_a_usable_snapshot(change):
    state = replace(_state(), inbox_waiting=True, **change)
    assert next_action(state, NOW) == Idle()


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"quiet": False}, "playback"),
        ({"scanning": True}, "scan"),
        ({"snapshot_usable": False}, "index"),
        ({"rebuild_pending": True}, "index"),
    ],
)
def test_nothing_while_not_quiet_scanning_or_unusable(change, why):
    state = replace(_state(**{"kv-a": replace(READY, full_due=True)}), writes_pending=True, **change)
    assert waiting_on(state) == why
    assert next_action(state, NOW) == Idle()


def test_status_says_nothing_when_free():
    assert waiting_on(_state()) is None


def test_unmapped_viewer_skipped():
    state = replace(_state(**{"kv-a": READY}), viewers={"kv-a": READY, "kv-b": replace(READY, full_due=True)})
    assert next_action(state, NOW) == Idle()


# -- the judged-ping gate ----------------------------------------------------------------


def test_no_upload_before_judged_ping_at_start_and_after_failure():
    state = _state(**{"kv-a": ViewerState(full_due=True)})
    assert next_action(state, NOW) == Ping()
    # Asked once; asked again only when no answer came within the reporter's window.
    asked = on_ping_asked(state, NOW)
    assert next_action(asked, NOW + 1) == Wait(NOW + PING_ANSWER_SECONDS)
    assert next_action(asked, NOW + PING_ANSWER_SECONDS) == Ping()
    judged = _state(**{"kv-a": replace(READY, full_due=True)})
    assert next_action(arm_gate(judged, NOW), NOW) == Ping()


def test_reply_to_a_ping_built_before_the_gate_armed_is_not_judged():
    state = arm_gate(_state(**{"kv-a": replace(READY, full_due=True)}), NOW)
    old = PingTicket(sent_at=NOW - 1, epochs={"kv-a": 0})
    after, resets = on_reply(state, _reply("kv-a"), old, {"kv-a": SeqState(sent=5, confirmed=5)}, NOW + 1)
    assert resets == [] and not after.viewers["kv-a"].judged
    fresh = PingTicket(sent_at=NOW, epochs={"kv-a": 0})
    after, _ = on_reply(state, _reply("kv-a"), fresh, {"kv-a": SeqState(sent=5, confirmed=5)}, NOW + 1)
    assert after.viewers["kv-a"].judged


def test_unjudged_viewer_does_not_block_others_or_kodi_work():
    state = replace(
        _state(**{"kv-a": ViewerState(full_due=True), "kv-b": replace(READY, full_due=True)}), writes_pending=True
    )
    assert next_action(state, NOW) == FullUpload("kv-b")
    only_a = replace(state, viewers={"kv-a": ViewerState(full_due=True)})
    assert next_action(on_ping_asked(only_a, NOW), NOW + 1) == WriteKodi()


def test_unanswered_or_unjudgeable_pings_back_off():
    state = _state(**{"kv-a": ViewerState(full_due=True)})
    after, _ = on_reply(on_ping_asked(state, NOW), None, None, {}, NOW + 1)
    assert after.ping_asked_at == 0 and after.ping_backoff_until == NOW + 1 + BACKOFF_SECONDS[0]
    assert next_action(after, NOW + 2) == Wait(NOW + 1 + BACKOFF_SECONDS[0])
    again, _ = on_reply(after, None, None, {}, NOW + 10)
    assert again.ping_backoff_until == NOW + 10 + BACKOFF_SECONDS[1]
    # A ticket without the viewer's epoch can't be judged: backed off too.
    crossed, _ = on_reply(again, _reply("kv-a"), PingTicket(sent_at=NOW), {"kv-a": SeqState()}, NOW + 20)
    assert crossed.ping_failures == 3
    judged, _ = on_reply(crossed, _reply("kv-a", upload_seq=0), PingTicket(sent_at=NOW, epochs={"kv-a": 0}),
                         {"kv-a": SeqState()}, NOW + 30)  # fmt: skip
    assert judged.ping_failures == 0 and judged.ping_backoff_until == 0


@pytest.mark.parametrize("pending", ["acks_pending", "queue_due"])
def test_fetch_and_acks_wait_for_the_gate(pending):
    state = _state(**{"kv-a": replace(READY, judged=False, **{pending: True})})
    assert next_action(state, NOW) == Ping()


def test_connection_change_arms_gate():
    state = arm_gate(_state(**{"kv-a": replace(READY, unsent=True, queue_settled=True)}), NOW)
    assert next_action(state, NOW) == Ping()


def test_no_delta_while_full_waits_for_ping():
    state = arm_gate(_state(**{"kv-a": replace(READY, full_due=True, unsent=True, queue_settled=True)}), NOW)
    assert next_action(state, NOW) == Ping()
    ticket = PingTicket(sent_at=NOW, epochs={"kv-a": 0})
    judged, _ = on_reply(state, _reply("kv-a", upload_seq=5), ticket, {"kv-a": SeqState(sent=5, confirmed=5)}, NOW)
    assert next_action(judged, NOW) == FullUpload("kv-a", queue_settled=True)


# -- judging a reply ---------------------------------------------------------------------


def _judge(vs: ViewerState, cw: int, record: SeqState, epoch: int | None = 0, queue_pending: int = 0):
    state = _state(**{"kv-a": vs})
    ticket = PingTicket(sent_at=1, epochs={} if epoch is None else {"kv-a": epoch})
    return on_reply(state, _reply("kv-a", upload_seq=cw, queue_pending=queue_pending), ticket, {"kv-a": record}, NOW)


@pytest.mark.parametrize("cw", [5, 6, 7])
def test_refused_or_lost_upload_is_normal(cw):
    """Between confirmed and sent: an upload refused, never arrived, or its answer lost."""
    state, resets = _judge(ViewerState(first_full_committed=True), cw, SeqState(sent=7, confirmed=5))
    assert resets == [] and state.viewers["kv-a"].judged and not state.viewers["kv-a"].full_due


def test_cw_lost_state_resets_and_sends_a_full():
    state, resets = _judge(ViewerState(first_full_committed=True), 3, SeqState(sent=7, confirmed=5))
    assert resets == [Reset("kv-a", 3, fresh=False)]
    vs = state.viewers["kv-a"]
    assert vs.full_due and not vs.fresh and vs.judged
    assert next_action(state, NOW) == FullUpload("kv-a")


def test_record_older_than_cw_starts_over_fresh():
    """A restored or copied sync.db, or one Start created again: CrossWatch saw more."""
    state, resets = _judge(ViewerState(), 9, SeqState(sent=7, confirmed=5))
    assert resets == [Reset("kv-a", 9, fresh=True)]
    assert next_action(state, NOW) == FullUpload("kv-a", fresh=True)


def test_fresh_still_pending_stays_fresh():
    state, resets = _judge(ViewerState(fresh=True, full_due=True), 3, SeqState(sent=9, confirmed=9))
    assert resets == [Reset("kv-a", 3, fresh=True)] and state.viewers["kv-a"].fresh


def test_start_after_lost_record_is_fresh():
    state, resets = _judge(ViewerState(), 12, SeqState())
    assert resets == [Reset("kv-a", 12, fresh=True)] and state.viewers["kv-a"].fresh


@pytest.mark.parametrize("epoch", [None, 1])
def test_reply_crossing_an_upload_not_judged(epoch):
    state, resets = _judge(ViewerState(), 3, SeqState(sent=7, confirmed=5, epoch=0), epoch=epoch)
    assert resets == [] and not state.viewers["kv-a"].judged


def test_upload_in_flight_now_blocks_judging():
    state, resets = _judge(ViewerState(in_flight=True), 3, SeqState(sent=7, confirmed=5))
    assert resets == [] and not state.viewers["kv-a"].judged


def test_none_reply_changes_nothing_but_the_ping_backoff():
    state = on_ping_asked(_state(**{"kv-a": READY}), NOW)
    after, resets = on_reply(state, None, None, {}, NOW)
    assert resets == []
    assert after == replace(state, ping_asked_at=0.0, ping_failures=1, ping_backoff_until=NOW + BACKOFF_SECONDS[0])


def test_queue_pending_marks_fetch_due_after_first_full_only():
    state, _ = _judge(ViewerState(first_full_committed=True), 5, SeqState(sent=5, confirmed=5), queue_pending=2)
    assert state.viewers["kv-a"].queue_due
    state, _ = _judge(ViewerState(), 5, SeqState(sent=5, confirmed=5), queue_pending=2)
    assert not state.viewers["kv-a"].queue_due
    # Also from a reply that crossed an upload.
    state, _ = _judge(
        ViewerState(first_full_committed=True), 5, SeqState(sent=5, confirmed=5), epoch=3, queue_pending=1
    )
    assert state.viewers["kv-a"].queue_due


# -- what runs, in order -----------------------------------------------------------------


def test_acks_before_fetch():
    state = _state(**{"kv-a": replace(READY, acks_pending=True, queue_due=True)})
    assert next_action(state, NOW) == SendAcks("kv-a")


def test_queue_settled_before_person_delta():
    state = _state(**{"kv-a": replace(READY, unsent=True)})
    assert next_action(state, NOW) == FetchQueue("kv-a")
    settled = _state(**{"kv-a": replace(READY, unsent=True, queue_settled=True)})
    assert next_action(settled, NOW) == Delta("kv-a")


def test_no_queue_before_first_full_committed():
    state = _state(**{"kv-a": replace(READY, first_full_committed=False, queue_due=True, full_due=True)})
    assert next_action(state, NOW) == FullUpload("kv-a")


def test_no_queue_with_incomplete_read():
    state = replace(_state(**{"kv-a": replace(READY, queue_due=True)}), library_complete=False)
    assert next_action(state, NOW) == Idle()


def test_no_person_delta_when_the_queue_couldnt_be_fetched():
    state = replace(_state(**{"kv-a": replace(READY, unsent=True)}), library_complete=False)
    assert next_action(state, NOW) == Idle()
    full = replace(state, viewers={"kv-a": replace(READY, unsent=True, full_due=True)})
    assert next_action(full, NOW) == FullUpload("kv-a", queue_settled=False)


def test_no_delta_during_full():
    state = _state(**{"kv-a": replace(READY, full_in_progress=True, unsent=True, queue_settled=True)})
    assert next_action(state, NOW) == FullUpload("kv-a", queue_settled=True)


def test_one_request_per_viewer_at_a_time():
    state = _state(**{"kv-a": replace(READY, in_flight=True, full_due=True)})
    assert next_action(state, NOW) == Idle()


def test_viewers_take_turns():
    state = _state(**{"kv-a": replace(READY, full_due=True), "kv-b": replace(READY, full_due=True)})
    assert next_action(state, NOW) == FullUpload("kv-a")
    assert next_action(replace(state, last_viewer="kv-a"), NOW) == FullUpload("kv-b")
    assert next_action(replace(state, last_viewer="kv-b"), NOW) == FullUpload("kv-a")


def test_backoff_waits_for_the_soonest():
    state = _state(
        **{
            "kv-a": replace(READY, full_due=True, backoff_until=NOW + 30),
            "kv-b": replace(READY, full_due=True, backoff_until=NOW + 10),
        }
    )
    assert next_action(state, NOW) == Wait(NOW + 10)
    assert next_action(state, NOW + 11) == FullUpload("kv-b")


def test_rating_scan_and_writes_wait_for_windows():
    state = replace(_state(**{"kv-a": READY}), writes_pending=True, rating_scan_due=True)
    assert next_action(state, NOW) == WriteKodi()
    assert next_action(replace(state, writes_pending=False), NOW) == RatingScan()
    assert next_action(replace(state, window_open=True), NOW) == Idle()
    backing_off = replace(
        state, window_open=True, viewers={"kv-a": replace(READY, full_due=True, backoff_until=NOW + 5)}
    )
    assert next_action(backing_off, NOW) == Wait(NOW + 5)


def test_viewer_uploads_before_kodi_writes():
    state = replace(_state(**{"kv-a": replace(READY, full_due=True)}), writes_pending=True)
    assert next_action(state, NOW) == FullUpload("kv-a")


def test_idle_when_nothing_to_do():
    assert next_action(_state(**{"kv-a": READY}), NOW) == Idle()
