from dataclasses import replace

import pytest

from resources.lib.sync.client import SyncError
from resources.lib.sync.constants import (
    BACKOFF_SECONDS,
    DAILY_FULL_SECONDS,
    MAX_ROWS_PER_LIST,
    MIN_PART_ROWS,
    SETTLE_SECONDS,
)
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
    RequestRebuild,
    Reset,
    SendAcks,
    SendExistingRatings,
    TakeRequests,
    ViewerState,
    Wait,
    WriteKodi,
    active,
    arm_gate,
    daily_full_due,
    delta_resend_seq,
    first_round_settled,
    late_seed,
    local_active,
    mapping_of,
    next_action,
    on_error,
    on_full_part,
    on_library_read,
    on_mapping,
    on_ping_asked,
    on_rebuild_asked,
    on_reply,
    on_snapshot,
    on_success,
    on_token_changed,
    on_too_large,
    request_full,
    seeded,
    session_start,
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


# -- triggers -------------------------------------------------------------------------------


def test_full_triggers():
    state = _state(**{"kv-a": READY, "kv-b": READY})
    both = request_full(state)
    assert all(vs.full_due for vs in both.viewers.values())
    one = request_full(state, ["kv-b"])
    assert not one.viewers["kv-a"].full_due and one.viewers["kv-b"].full_due
    assert next_action(one, NOW) == FullUpload("kv-b")


def test_restart_never_resumes_full():
    state = _state(**{"kv-a": replace(READY, full_in_progress=True, in_flight=True)})
    after = session_start(state, NOW)
    vs = after.viewers["kv-a"]
    assert not vs.full_in_progress and not vs.in_flight and not vs.judged
    assert after.armed_at == NOW and after.start_full_pending


def test_daily_full_due():
    assert daily_full_due(None, NOW)
    assert not daily_full_due(NOW - DAILY_FULL_SECONDS + 1, NOW)
    assert daily_full_due(NOW - DAILY_FULL_SECONDS, NOW)


def _two_viewer_reply(**instances: str) -> SyncReply:
    return SyncReply(True, None, {v: ReplyViewer(instance=i, upload_seq=0) for v, i in instances.items()}, 0)


def test_mapping_change_triggers_full_for_all():
    state = _state(**{"kv-a": READY, "kv-b": READY})
    before = mapping_of("L1", _two_viewer_reply(**{"kv-a": "default", "kv-b": "default~kv-b"}))
    same = on_mapping(state, before, before, {"kv-a": "L1", "kv-b": "L1"})
    assert same == state
    moved = mapping_of("L1", _two_viewer_reply(**{"kv-a": "default", "kv-b": "other"}))
    after = on_mapping(state, moved, before, {"kv-a": "L1", "kv-b": "L1"})
    assert all(vs.full_due for vs in after.viewers.values())
    new_library = mapping_of("L2", _two_viewer_reply(**{"kv-a": "default", "kv-b": "default~kv-b"}))
    assert all(vs.full_due for vs in on_mapping(state, new_library, before, {}).viewers.values())


def test_viewer_without_a_full_under_the_mapping_gets_one():
    state = _state(**{"kv-a": READY, "kv-b": READY})
    current = mapping_of("L1", _two_viewer_reply(**{"kv-a": "default", "kv-b": "default~kv-b"}))
    after = on_mapping(state, current, current, {"kv-a": "L1", "kv-b": "L0"})
    assert not after.viewers["kv-a"].full_due and after.viewers["kv-b"].full_due


def test_show_ids_changed_requests_rebuild():
    state = replace(_state(**{"kv-a": replace(READY, full_due=True)}))
    assert on_library_read(state, frozenset({1, 2}), None) == state
    assert on_library_read(state, frozenset({1, 2}), frozenset({1, 2})) == state
    changed = on_library_read(state, frozenset({1, 2, 3}), frozenset({1, 2}))
    assert next_action(changed, NOW) == RequestRebuild()
    asked = on_rebuild_asked(changed)
    assert waiting_on(asked) == "index" and next_action(asked, NOW) == Idle()
    arrived = on_snapshot(asked, usable=True, after_scan=False)
    assert next_action(arrived, NOW) == FullUpload("kv-a")


def test_index_after_a_scan_sends_fulls():
    state = _state(**{"kv-a": READY})
    assert on_snapshot(state, usable=True, after_scan=True).viewers["kv-a"].full_due
    assert not on_snapshot(state, usable=False, after_scan=True).viewers["kv-a"].full_due


def test_first_full_committed_on_complete_not_ok():
    state = _state(**{"kv-a": ViewerState(judged=True, full_due=True, fresh=True)})
    part = on_full_part(state, "kv-a", complete=False, fresh=True)
    vs = part.viewers["kv-a"]
    assert vs.full_in_progress and not vs.first_full_committed and vs.fresh
    done = on_full_part(part, "kv-a", complete=True, fresh=True).viewers["kv-a"]
    assert done.first_full_committed and not done.full_due and not done.full_in_progress
    assert not done.fresh and done.queue_due


def test_fresh_kept_when_a_normal_full_completed_first():
    state = _state(**{"kv-a": ViewerState(judged=True, full_due=True, fresh=True)})
    assert on_full_part(state, "kv-a", complete=True, fresh=False).viewers["kv-a"].fresh


def test_empty_record_after_first_full_still_uploads():
    state = request_full(_state(**{"kv-a": READY}))
    assert next_action(state, NOW) == FullUpload("kv-a")


# -- first run ------------------------------------------------------------------------------

START = "2026-11-20T10:00:00Z"


def _seeded(now_utc: str, *, viewer="kv-a", committed=False, seed=frozenset({"kv-a"}), mapped="2026-11-20T11:00:00Z"):
    def ask(rule):
        return rule(viewer, first_full_committed=committed, seed_viewers=seed, seed_started_at=START,
                    first_mapped_at=mapped, now_utc=now_utc)  # fmt: skip

    return ask(seeded), ask(late_seed)


def test_seed_window_from_first_mapping():
    # The window runs from the later of Start and the first mapping.
    assert _seeded("2026-11-21T10:59:00Z") == (True, False)
    assert _seeded("2026-11-21T11:00:00Z") == (True, False)
    assert _seeded("2026-11-21T11:00:01Z") == (False, True)
    assert _seeded("2026-11-21T09:00:00Z", mapped="") == (True, False)


def test_late_seed_status_line():
    assert _seeded("2026-11-22T10:00:00Z")[1] is True
    # Not before the viewer was mapped at all: nothing has failed yet.
    assert _seeded("2026-11-22T10:00:00Z", mapped="")[1] is False


def test_fresh_until_first_full_committed():
    assert _seeded("2026-11-20T12:00:00Z", committed=True) == (False, False)


def test_interrupted_first_full_continues_seeding():
    """A first full cut off by a restart is sent again seeded while the window lasts."""
    assert _seeded("2026-11-20T18:00:00Z") == (True, False)


def test_viewer_added_later_starts_empty():
    assert _seeded("2026-11-20T12:00:00Z", viewer="kv-new") == (False, False)


def test_start_again_never_reseeds():
    """seed_viewers is written by the first Start only; a later Start leaves it."""
    assert _seeded("2026-11-20T12:00:00Z", seed=frozenset()) == (False, False)


FIRST_FULL = "2026-11-20T10:00:00Z"
SETTLED_AT = "2026-11-20T10:40:00Z"


def _settled(first_full_at: str = FIRST_FULL, now_utc: str = SETTLED_AT, queue_pending: int = 0,
             run_pending: bool = False, unsent: bool = False, unapplied: bool = False) -> bool:  # fmt: skip
    return first_round_settled(first_full_at=first_full_at, now_utc=now_utc, queue_pending=queue_pending,
                               run_pending=run_pending, unsent=unsent, unapplied=unapplied)  # fmt: skip


def test_first_round_settled_needs_quiet_ping_after_settle_seconds():
    assert SETTLE_SECONDS == 2400 and _settled()
    assert not _settled(now_utc="2026-11-20T10:39:59Z")
    assert not _settled(first_full_at="")
    assert not _settled(queue_pending=1)
    assert not _settled(run_pending=True)
    assert not _settled(unsent=True)
    assert not _settled(unapplied=True)


def test_existing_ratings_only_when_chosen_and_settled():
    due = replace(READY, existing_ratings_due=True, settled=True)
    assert next_action(_state(**{"kv-a": due}), NOW) == SendExistingRatings("kv-a")
    assert next_action(_state(**{"kv-a": replace(due, settled=False)}), NOW) == Idle()
    assert next_action(_state(**{"kv-a": replace(due, existing_ratings_due=False)}), NOW) == Idle()
    # After the person's own changes, and never while the pair run may still bring ratings.
    assert next_action(_state(**{"kv-a": replace(due, unsent=True, queue_settled=True)}), NOW) == Delta("kv-a")
    running = replace(
        _state(**{"kv-a": due}), reply=SyncReply(True, None, {"kv-a": ReplyViewer("default", 5, run_pending=True)}, 0)
    )
    assert next_action(running, NOW) == Idle()


def test_session_start_full_goes_to_every_viewer_the_first_reply_maps():
    """A new process has no reply yet; the first one names the viewers."""
    state = session_start(replace(_state(), reply=None, viewers={}), NOW)
    ticket = PingTicket(sent_at=NOW, epochs={"kv-a": 0, "kv-b": 0})
    after, _ = on_reply(state, _reply("kv-a", "kv-b", upload_seq=0), ticket, {}, NOW)
    assert all(vs.full_due for vs in after.viewers.values()) and not after.start_full_pending
    # Only once: the next reply asks for nothing more.
    done = replace(after, viewers={v: replace(vs, full_due=False) for v, vs in after.viewers.items()})
    again, _ = on_reply(done, _reply("kv-a", "kv-b", upload_seq=0), ticket, {}, NOW)
    assert not any(vs.full_due for vs in again.viewers.values())


def test_full_asked_during_a_full_follows_it():
    state = _state(**{"kv-a": replace(READY, full_due=True, full_in_progress=True)})
    asked = on_snapshot(state, usable=True, after_scan=True)
    done = on_full_part(asked, "kv-a", complete=True, fresh=False).viewers["kv-a"]
    assert done.full_due and not done.full_again
    plain = on_full_part(state, "kv-a", complete=True, fresh=False).viewers["kv-a"]
    assert not plain.full_due


def test_existing_ratings_wait_for_a_judged_ping_after_a_full():
    """CrossWatch starts a pair run for a committed full; the reply from before it says none."""
    due = replace(READY, existing_ratings_due=True, settled=True, full_due=True)
    after_full = on_full_part(_state(**{"kv-a": due}), "kv-a", complete=True, fresh=False)
    fetched = replace(after_full, viewers={"kv-a": replace(after_full.viewers["kv-a"], queue_due=False)})
    assert next_action(fetched, NOW) == Idle()
    ticket = PingTicket(sent_at=NOW, epochs={"kv-a": 0})
    judged, _ = on_reply(fetched, _reply("kv-a"), ticket, {"kv-a": SeqState(sent=5, confirmed=5)}, NOW)
    assert next_action(judged, NOW) == SendExistingRatings("kv-a")


def test_existing_ratings_wait_for_a_due_queue_fetch():
    due = replace(READY, existing_ratings_due=True, settled=True, queue_due=True)
    assert next_action(replace(_state(**{"kv-a": due}), library_complete=False), NOW) == Idle()


# -- errors ---------------------------------------------------------------------------------


def _err(status: int, code: str = "", *, cw: bool = True, retry_after: float | None = None) -> SyncError:
    return SyncError(status, code, retryable=False, retry_after=retry_after, from_crosswatch=cw)


def _fail(vs: ViewerState, request, err: SyncError):
    state = _state(**{"kv-a": replace(vs, in_flight=True)})
    after, outcome = on_error(state, "kv-a", request, err, NOW)
    return after, after.viewers["kv-a"], outcome


@pytest.mark.parametrize(
    ("err", "kind", "check"),
    [
        (_err(400, "invalid_request"), "delta", lambda st, vs, out: vs.error == "rejected" and out.drop_unsent),
        (_err(401, "unauthorized"), "full", lambda st, vs, out: st.unauthorized and not active(st)),
        (_err(403, "sync_disabled"), "queue", lambda st, vs, out: vs.error == "refused" and vs.failures == 1),
        (_err(409, "upload_incomplete"), "delta", lambda st, vs, out: vs.full_due),
        (_err(409, "upload_stale"), "delta", lambda st, vs, out: not vs.full_due),
        (_err(429, "rate_limited", retry_after=40), "delta", lambda st, vs, out: vs.backoff_until == NOW + 40),
        (
            _err(503, "temporarily_unavailable"),
            "delta",
            lambda st, vs, out: vs.backoff_until == NOW + BACKOFF_SECONDS[0],
        ),
        (_err(502, "bad_gateway"), "ack", lambda st, vs, out: vs.error == "busy" and vs.failures == 1),
        (_err(0, "network", cw=False), "full", lambda st, vs, out: vs.error == "network" and vs.failures == 1),
        (_err(0, "lost_reply", cw=False), "delta", lambda st, vs, out: vs.error == "lost_reply" and vs.failures == 1),
        (_err(0, "certificate", cw=False), "queue", lambda st, vs, out: vs.error == "certificate"),
        (_err(401, "not_crosswatch", cw=False), "full", lambda st, vs, out: not st.unauthorized and vs.failures == 1),
        (_err(404, "no_sync", cw=False), "full", lambda st, vs, out: st.no_sync and not active(st)),
    ],
)
def test_error_table(err, kind, check):
    state, vs, outcome = _fail(READY, kind, err)
    assert check(state, vs, outcome)
    # Every failure arms the gate, and nothing stays in flight.
    assert not vs.judged and not vs.in_flight and state.armed_at == NOW


def test_upload_incomplete_keeps_full_in_flight():
    """A queue fetch or ack between the parts of a full gets upload_incomplete: the full goes on."""
    _, vs, _ = _fail(replace(READY, full_in_progress=True), "queue", _err(409, "upload_incomplete"))
    assert vs.full_in_progress and not vs.full_due


def test_503_on_a_part_keeps_the_full_going():
    """The same part goes again after the backoff; the full isn't started over."""
    _, vs, _ = _fail(replace(READY, full_in_progress=True), "full", _err(503, "temporarily_unavailable"))
    assert vs.full_in_progress and vs.backoff_until == NOW + BACKOFF_SECONDS[0]
    state = _state(**{"kv-a": replace(vs, judged=True)})
    assert next_action(state, vs.backoff_until) == FullUpload("kv-a", queue_settled=False)


@pytest.mark.parametrize(
    ("err", "kind"),
    [
        (_err(400, "invalid_request"), "queue"),
        (_err(400, "invalid_request"), "ack"),
        (_err(403, "sync_disabled"), "delta"),
        (_err(409, "viewer_unknown"), "delta"),
        (_err(409, "upload_stale"), "delta"),
        (_err(409, "upload_incomplete"), "delta"),
    ],
)
def test_refusals_never_repeat_without_a_backoff(err, kind):
    """CrossWatch answering the same request the same way must not make a tight loop."""
    _, vs, _ = _fail(replace(READY, acks_pending=True, queue_due=True, unsent=True, queue_settled=True), kind, err)
    assert vs.backoff_until > NOW


def test_400_on_a_fetch_or_ack_drops_it_and_keeps_the_full():
    base = replace(READY, full_in_progress=True, acks_pending=True, queue_due=True)
    _, vs, out = _fail(base, "ack", _err(400, "invalid_request"))
    assert out.drop_acks and not vs.acks_pending and vs.full_in_progress
    _, vs, _ = _fail(base, "queue", _err(400, "invalid_request"))
    assert not vs.queue_due and vs.full_in_progress


def test_400_on_a_full_drops_it():
    _, vs, out = _fail(
        replace(READY, full_due=True, full_in_progress=True, full_again=True), "full", _err(400, "invalid_request")
    )
    assert not vs.full_due and not vs.full_in_progress and not vs.full_again and not out.drop_unsent


def test_stale_full_starts_over_once():
    _, vs, _ = _fail(
        replace(READY, full_due=True, full_in_progress=True, full_again=True), "full", _err(409, "upload_stale")
    )
    assert vs.full_due and not vs.full_in_progress and not vs.full_again


def test_413_on_a_delta_halves_its_requests():
    _, vs, _ = _fail(replace(READY, unsent=True), "delta", _err(413, "payload_too_large"))
    assert vs.part_rows == MAX_ROWS_PER_LIST // 2 and not vs.full_due and vs.backoff_until > NOW


def test_backoff_grows_and_success_clears_it():
    state = _state(**{"kv-a": READY})
    for step in BACKOFF_SECONDS + (BACKOFF_SECONDS[-1],):
        state, _ = on_error(state, "kv-a", "delta", _err(0, "network", cw=False), NOW)
        assert state.viewers["kv-a"].backoff_until == NOW + step
    cleared = on_success(state, "kv-a").viewers["kv-a"]
    assert cleared.failures == 0 and cleared.backoff_until == 0 and cleared.error == ""


def test_viewer_unknown_pings_and_names_the_viewer_after_two():
    state, vs, _ = _fail(READY, "delta", _err(409, "viewer_unknown"))
    assert vs.viewer_unknown == 1 and vs.error == "" and next_action(state, NOW) == Ping()
    state, _ = on_error(state, "kv-a", "delta", _err(409, "viewer_unknown"), NOW)
    assert state.viewers["kv-a"].error == "viewer_unknown"


def test_401_resumes_on_new_token():
    state, _, _ = _fail(replace(READY, full_due=True), "full", _err(401, "unauthorized"))
    assert next_action(state, NOW) == Idle()
    resumed = on_token_changed(state, NOW + 1)
    assert not resumed.unauthorized and resumed.armed_at == NOW + 1 and next_action(resumed, NOW + 1) == Ping()


def test_413_new_full_with_half_parts_down_to_the_floor():
    _, vs, _ = _fail(replace(READY, full_in_progress=True), "full", _err(413, "payload_too_large"))
    assert vs.full_due and not vs.full_in_progress and vs.part_rows == MAX_ROWS_PER_LIST // 2
    _, vs, _ = _fail(replace(READY, part_rows=MIN_PART_ROWS + 1), "full", _err(413, "payload_too_large"))
    assert vs.part_rows == MIN_PART_ROWS
    # A proxy's 413 and the add-on's own size check halve the same way.
    _, vs, _ = _fail(READY, "full", _err(413, "not_crosswatch", cw=False))
    assert vs.part_rows == MAX_ROWS_PER_LIST // 2
    _, vs, _ = _fail(READY, "full", _err(0, "request_too_large", cw=False))
    assert vs.part_rows == MAX_ROWS_PER_LIST // 2


def test_413_at_floor_stops_with_status():
    state, vs, _ = _fail(replace(READY, part_rows=MIN_PART_ROWS, full_due=True), "full", _err(413, "payload_too_large"))
    assert vs.uploads_stopped == "too_large" and not vs.full_due
    assert (
        next_action(replace(state, viewers={"kv-a": replace(vs, judged=True, unsent=True, queue_settled=True)}), NOW)
        == Idle()
    )
    # Until the next session.
    restarted = session_start(state, NOW).viewers["kv-a"]
    assert restarted.uploads_stopped == "" and restarted.part_rows == MIN_PART_ROWS


def test_upload_too_large_stops_the_viewer():
    state = on_too_large(_state(**{"kv-a": replace(READY, full_due=True)}), "kv-a")
    assert state.viewers["kv-a"].uploads_stopped == "library_too_large" and next_action(state, NOW) == Idle()


def test_stopped_uploads_still_fetch_and_ack():
    vs = replace(READY, uploads_stopped="too_large", acks_pending=True)
    assert next_action(_state(**{"kv-a": vs}), NOW) == SendAcks("kv-a")


def test_refused_delta_drops_unsent():
    _, vs, outcome = _fail(replace(READY, unsent=True), "delta", _err(400, "invalid_request"))
    assert outcome.drop_unsent and not vs.unsent


def test_delta_resend_identical_or_new_seq():
    assert delta_resend_seq((42, b"b"), b"b") == 42
    assert delta_resend_seq((42, b"b"), b"c") is None
    assert delta_resend_seq(None, b"a") is None
