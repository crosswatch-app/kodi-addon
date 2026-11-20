# SPDX-License-Identifier: GPL-2.0-only
"""What library sync does next, decided from its state alone.

Pure: the worker (engine.py) gathers the state, asks next_action, does that one thing, and
feeds the result back through on_reply and friends. Nothing here reads a clock, Kodi or the
network, so every rule is a plain test. Times are the monotonic clock the ping ticket uses.

The gates, in order:
- nothing at all before Start ran for this pairing, or while the record is missing; waiting
  requests are taken then, whatever else holds;
- everything else needs sync on and this device not paused. The inbox is drained, Kodi is
  written and ratings are scanned without CrossWatch, so an outage loses none of them, but
  only while Kodi plays nothing (and didn't just), isn't scanning, and the playlist index is
  usable and current;
- talking to CrossWatch also needs its last answer enabled with no reason, and no 401;
- per viewer, only viewers CrossWatch mapped; an upload, a queue fetch or an ack waits for a
  ping reply judged for that viewer since the gate last armed (session start, a failed
  request to CrossWatch, a changed pairing), from a ping built after it armed. That catches
  a CrossWatch restored while unreachable before an upload hides it (an accepted upload
  raises CrossWatch's seq again); a restore while reachable, between two judged pings, is
  not caught (a state id in the ping reply would, asked on #72).

Judging a reply compares CrossWatch's upload_seq with the record's: between confirmed and
sent is normal (an upload refused, lost, or whose answer was lost); below confirmed means
CrossWatch lost state, and the record's uploads go up again in a new full; above sent means
the record is older than what CrossWatch saw (a restored or copied sync.db, or a record Start
created again), and the viewer starts over at CrossWatch with a "fresh" full (contract,
Upload). The reset that follows is stored with its fresh flag in one transaction, because it
erases the evidence. A reply is judged only when no upload for the viewer started or ended
since its ping was built and none is in flight: one that crossed an upload says nothing.

Log events: none; the engine logs what it does.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

from resources.lib.sync.clock import parse_utc
from resources.lib.sync.constants import BACKOFF_SECONDS, DAILY_FULL_SECONDS, SEED_WINDOW_SECONDS, SETTLE_SECONDS
from resources.lib.sync.record import SeqState
from resources.lib.sync.reply import PingTicket, SyncReply

# How long an asked ping may go unanswered before it is asked again: the reporter delivers
# a ping within its two-minute window or drops it.
PING_ANSWER_SECONDS = 150.0


@dataclass(frozen=True)
class ViewerState:
    first_full_committed: bool = False
    # A ping reply was judged for this viewer since the gate last armed.
    judged: bool = False
    # A full is wanted, by any trigger; fresh asks CrossWatch to start the viewer over and is
    # kept in the record until a fresh full is accepted.
    full_due: bool = False
    fresh: bool = False
    # Parts of a full have gone up and more follow.
    full_in_progress: bool = False
    # An upload request for this viewer is out; nothing else goes for it meanwhile.
    in_flight: bool = False
    # Person changes not yet carried by an accepted upload.
    unsent: bool = False
    # The queue was fetched and applied since the last person change arrived.
    queue_settled: bool = False
    acks_pending: bool = False
    queue_due: bool = False
    backoff_until: float = 0.0
    # The first round with CrossWatch has settled (stored, so a restart never runs it again),
    # and "Send existing ratings" was chosen at Start and hasn't run for this viewer.
    settled: bool = False
    existing_ratings_due: bool = False
    # A full committed and no ping has been judged since: CrossWatch's pair run may be
    # pending although the last reply said not.
    run_unknown: bool = False
    # Another full was asked for while one was going up: it goes up next.
    full_again: bool = False


@dataclass(frozen=True)
class EngineState:
    started: bool = False
    sync_enabled: bool = False
    paused: bool = False
    record_missing: bool = False
    # A 401 stops talking to CrossWatch until the token changes.
    unauthorized: bool = False
    reply: SyncReply | None = None
    # When the judged-ping gate last armed; only tickets built after it are judged.
    armed_at: float = 0.0
    # The session's first full goes to every viewer the next reply maps: at a session start
    # the mapping isn't known yet.
    start_full_pending: bool = False
    # An asked ping not yet answered (0 when none); asked again after PING_ANSWER_SECONDS.
    ping_asked_at: float = 0.0
    ping_failures: int = 0
    ping_backoff_until: float = 0.0
    quiet: bool = False
    scanning: bool = False
    snapshot_usable: bool = False
    # The last library read's show set changed: a rebuild is to be asked for (wanted), then
    # the index is being rebuilt (pending), and nothing goes up until the new one arrives.
    rebuild_wanted: bool = False
    rebuild_pending: bool = False
    library_complete: bool = False
    # A rating window or the who-watched question is open.
    window_open: bool = False
    inbox_waiting: bool = False
    requests_waiting: bool = False
    writes_pending: bool = False
    rating_scan_due: bool = False
    # The viewer the last per-viewer action went to: the next turn starts after it, so one
    # busy viewer can't starve the others.
    last_viewer: str = ""
    viewers: Mapping[str, ViewerState] = field(default_factory=dict)


@dataclass(frozen=True)
class Reset:
    """A viewer's record goes above CrossWatch's seq (RecordStore.reset_for_cw) and a full
    follows; fresh is stored with it."""

    viewer: str
    cw_seq: int
    fresh: bool


@dataclass(frozen=True)
class Idle:
    pass


@dataclass(frozen=True)
class Ping:
    pass


@dataclass(frozen=True)
class RequestRebuild:
    pass


@dataclass(frozen=True)
class SendExistingRatings:
    viewer: str


@dataclass(frozen=True)
class DrainInbox:
    pass


@dataclass(frozen=True)
class TakeRequests:
    pass


@dataclass(frozen=True)
class FullUpload:
    viewer: str
    fresh: bool = False
    # The queue was fetched and applied first; without that the upload doesn't settle the
    # person changes it carries.
    queue_settled: bool = False


@dataclass(frozen=True)
class Delta:
    viewer: str


@dataclass(frozen=True)
class FetchQueue:
    viewer: str


@dataclass(frozen=True)
class SendAcks:
    viewer: str


@dataclass(frozen=True)
class WriteKodi:
    pass


@dataclass(frozen=True)
class RatingScan:
    pass


@dataclass(frozen=True)
class Wait:
    until: float


Action = (
    Idle
    | Ping
    | RequestRebuild
    | DrainInbox
    | TakeRequests
    | FullUpload
    | Delta
    | FetchQueue
    | SendAcks
    | SendExistingRatings
    | WriteKodi
    | RatingScan
    | Wait
)


def local_active(state: EngineState) -> bool:
    """Sync may act on Kodi: drain the inbox, write, scan ratings."""
    return state.started and state.sync_enabled and not state.paused and not state.record_missing


def active(state: EngineState) -> bool:
    """Sync may also talk to CrossWatch."""
    reply = state.reply
    return (
        local_active(state) and not state.unauthorized and reply is not None and reply.enabled and reply.reason is None
    )


def waiting_on(state: EngineState) -> str | None:
    """What Kodi-side work waits for, for the status screen; None when it may run."""
    if not state.quiet:
        return "playback"
    if state.scanning:
        return "scan"
    if state.rebuild_pending or not state.snapshot_usable:
        return "index"
    return None


def _viewer_action(viewer: str, vs: ViewerState, library_complete: bool, run_pending: bool) -> Action | None:
    if vs.in_flight:
        return None
    if vs.acks_pending:
        return SendAcks(viewer)
    can_fetch = vs.first_full_committed and library_complete
    if can_fetch and (vs.queue_due or (vs.unsent and not vs.queue_settled)):
        return FetchQueue(viewer)
    if vs.full_due or vs.full_in_progress:
        return FullUpload(viewer, fresh=vs.fresh, queue_settled=can_fetch and vs.queue_settled)
    if vs.unsent and vs.first_full_committed and vs.queue_settled:
        # Never before the queue was fetched and applied: CrossWatch's change may be newer.
        return Delta(viewer)
    if (
        vs.existing_ratings_due
        and vs.settled
        and not (run_pending or vs.run_unknown)
        and not (vs.unsent or vs.queue_due or vs.full_due or vs.full_in_progress)
    ):
        # Only once the services' own ratings had their chance to arrive and were applied
        # (contract, Ping: wait for run_pending to turn false): the service wins a clash.
        return SendExistingRatings(viewer)
    return None


def _turn(viewers: list[str], last: str) -> list[str]:
    later = [v for v in viewers if v > last]
    return later + [v for v in viewers if v <= last]


def _may_ping(state: EngineState, now: float) -> bool:
    asked = state.ping_asked_at and now - state.ping_asked_at < PING_ANSWER_SECONDS
    return not asked and now >= state.ping_backoff_until


def next_action(state: EngineState, now: float) -> Action:
    if not state.started or state.record_missing:
        return Idle()
    if state.requests_waiting:
        return TakeRequests()
    if not local_active(state):
        return Idle()
    if state.rebuild_wanted:
        return RequestRebuild()
    if waiting_on(state) is not None:
        return Idle()
    if state.inbox_waiting:
        return DrainInbox()
    deadlines: list[float] = []
    if active(state):
        reply = state.reply
        assert reply is not None
        need_ping = False
        for viewer in _turn(sorted(reply.viewers), state.last_viewer):
            vs = state.viewers.get(viewer, ViewerState())
            action = _viewer_action(viewer, vs, state.library_complete, reply.viewers[viewer].run_pending)
            if action is None:
                continue
            if vs.backoff_until > now:
                deadlines.append(vs.backoff_until)
            elif not vs.judged:
                need_ping = True
            else:
                return action
        if need_ping:
            if _may_ping(state, now):
                return Ping()
            deadlines.append(max(state.ping_backoff_until, state.ping_asked_at + PING_ANSWER_SECONDS))
    if not state.window_open:
        if state.writes_pending:
            return WriteKodi()
        if state.rating_scan_due:
            return RatingScan()
    return Wait(min(deadlines)) if deadlines else Idle()


def arm_gate(state: EngineState, now: float) -> EngineState:
    """A session start, a failed request to CrossWatch, or a changed pairing: every viewer
    waits for a reply to a ping built from now on before its next upload, fetch or ack."""
    viewers = {v: replace(vs, judged=False) for v, vs in state.viewers.items()}
    return replace(state, viewers=viewers, armed_at=now)


def on_ping_asked(state: EngineState, now: float) -> EngineState:
    return replace(state, ping_asked_at=now)


def on_reply(
    state: EngineState,
    reply: SyncReply | None,
    ticket: PingTicket | None,
    seqs: Mapping[str, SeqState],
    now: float,
) -> tuple[EngineState, list[Reset]]:
    """Take a ping reply (None: a 4xx or a reply without a block). Returns the new state and
    the viewers whose record must be reset above CrossWatch's seq before their next full.

    A reply that leaves a mapped viewer unjudged backs further pings off, so a CrossWatch
    that never answers, or a ticket that can't be judged, isn't pinged in a loop. The ticket
    must list every mapped viewer's epoch, 0 included, or that viewer is never judged.
    """
    state = replace(state, ping_asked_at=0.0)
    if reply is None or ticket is None:
        return _ping_backoff(state, now), []
    viewers = dict(state.viewers)
    resets: list[Reset] = []
    unjudged = False
    for viewer, seen in reply.viewers.items():
        vs = viewers.get(viewer, ViewerState())
        if reply.queue_pending > 0 and vs.first_full_committed:
            vs = replace(vs, queue_due=True)
        record = seqs.get(viewer, SeqState())
        if vs.in_flight or ticket.sent_at < state.armed_at or ticket.epochs.get(viewer) != record.epoch:
            viewers[viewer] = vs
            unjudged = unjudged or not vs.judged
            continue
        cw = seen.upload_seq
        if cw < record.confirmed or cw > record.sent:
            # Below: CrossWatch lost the uploads it confirmed. Above: the record is older
            # than CrossWatch, and only a fresh full can start the viewer over there. A
            # fresh still pending stays fresh.
            fresh = vs.fresh or cw > record.sent
            resets.append(Reset(viewer, cw, fresh))
            vs = replace(vs, full_due=True, fresh=fresh, full_in_progress=False)
        viewers[viewer] = replace(vs, judged=True, run_unknown=False)
    state = replace(state, reply=reply, viewers=viewers)
    if state.start_full_pending and reply.viewers:
        state = replace(request_full(state, reply.viewers), start_full_pending=False)
    if unjudged:
        return _ping_backoff(state, now), resets
    return replace(state, ping_failures=0, ping_backoff_until=0.0), resets


def _ping_backoff(state: EngineState, now: float) -> EngineState:
    step = BACKOFF_SECONDS[min(state.ping_failures, len(BACKOFF_SECONDS) - 1)]
    return replace(state, ping_failures=state.ping_failures + 1, ping_backoff_until=now + step)


# -- triggers -------------------------------------------------------------------------------


def request_full(state: EngineState, viewers: Iterable[str] | None = None) -> EngineState:
    """A full for these viewers, or every mapped one: after a wake (System.OnWake), after a
    scan or clean once the index is rebuilt, daily, after Start, on a mapping change. One
    asked for while a full is going up follows it: the running one was built before the
    reason for asking."""
    mapped = state.reply.viewers if state.reply is not None else {}
    wanted = list(viewers) if viewers is not None else list(mapped)
    out = dict(state.viewers)
    for viewer in wanted:
        vs = out.get(viewer, ViewerState())
        busy = vs.full_in_progress or (vs.full_due and vs.in_flight)
        out[viewer] = replace(vs, full_due=True, full_again=vs.full_again or busy)
    return replace(state, viewers=out)


def session_start(state: EngineState, now: float) -> EngineState:
    """What a new session starts from: no full in progress or in flight, the gate armed, and
    a full for every viewer the next reply maps. A full that was going up when the last
    session ended is never resumed: a new one goes with a higher seq."""
    viewers = {
        v: replace(vs, full_in_progress=False, in_flight=False, full_again=False) for v, vs in state.viewers.items()
    }
    return replace(arm_gate(replace(state, viewers=viewers), now), start_full_pending=True)


def daily_full_due(last_full_at: float | None, now: float) -> bool:
    return last_full_at is None or now - last_full_at >= DAILY_FULL_SECONDS


ViewerMapping = tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]


def mapping_of(library_id: str, reply: SyncReply) -> ViewerMapping:
    """What a full is uploaded under: the library, the viewers in order, and where each is."""
    ids = tuple(sorted(reply.viewers))
    return library_id, ids, tuple((v, reply.viewers[v].instance) for v in ids)


def on_mapping(
    state: EngineState, current: ViewerMapping, previous: ViewerMapping | None, full_library_ids: Mapping[str, str]
) -> EngineState:
    """A changed mapping sends a full for every listed viewer; so does a listed viewer whose
    last complete full went up under another library."""
    if current != previous:
        return request_full(state, current[1])
    stale = [v for v in current[1] if full_library_ids.get(v, "") != current[0]]
    return request_full(state, stale) if stale else state


def on_library_read(state: EngineState, show_ids: frozenset[int], previous: frozenset[int] | None) -> EngineState:
    """A read whose show set differs from the last one asks for a new index, and nothing goes
    up until it arrives: ownership by playlist follows shows."""
    if previous is not None and show_ids != previous:
        return replace(state, rebuild_wanted=True, rebuild_pending=True)
    return state


def on_rebuild_asked(state: EngineState) -> EngineState:
    return replace(state, rebuild_wanted=False)


def on_snapshot(state: EngineState, usable: bool, after_scan: bool) -> EngineState:
    """A new index arrived. After a scan or clean, every mapped viewer gets a full. The
    caller passes usable False for an index built from the show set before the change."""
    state = replace(state, snapshot_usable=usable, rebuild_pending=False)
    return request_full(state) if usable and after_scan else state


def on_full_part(state: EngineState, viewer: str, *, complete: bool, fresh: bool) -> EngineState:
    """A part of a full was accepted. CrossWatch's complete: true on the last part commits it
    (an ok on an earlier part only stages it); then the queue is fetched, and a fresh full
    clears the fresh request."""
    vs = state.viewers.get(viewer, ViewerState())
    if not complete:
        vs = replace(vs, full_in_progress=True)
    else:
        vs = replace(
            vs,
            full_due=vs.full_again,
            full_again=False,
            full_in_progress=False,
            first_full_committed=True,
            fresh=vs.fresh and not fresh,
            queue_due=True,
            # CrossWatch starts a pair run for a committed full (run_pending in the next ping).
            run_unknown=True,
        )
    return replace(state, viewers={**state.viewers, viewer: vs})


# -- first run --------------------------------------------------------------------------


def _utc(text: str) -> float | None:
    when = parse_utc(text)
    return when.timestamp() if when is not None else None


def seeded(
    viewer: str,
    *,
    first_full_committed: bool,
    seed_viewers: frozenset[str],
    seed_started_at: str,
    first_mapped_at: str,
    now_utc: str,
) -> bool:
    """Whether a fresh viewer's first full carries Kodi's own state for its single-owner
    items. Only a viewer the first Start listed, and only while its first full starts within
    SEED_WINDOW_SECONDS of the later of that Start and the first reply that mapped it: Kodi's
    state after that may hold other viewers' plays. Every other fresh viewer, and every viewer
    after its first committed full, goes up from its record."""
    if first_full_committed or viewer not in seed_viewers:
        return False
    starts = [t for t in (_utc(seed_started_at), _utc(first_mapped_at)) if t is not None]
    now = _utc(now_utc)
    return bool(starts) and now is not None and now - max(starts) <= SEED_WINDOW_SECONDS


def late_seed(
    viewer: str,
    *,
    first_full_committed: bool,
    seed_viewers: frozenset[str],
    seed_started_at: str,
    first_mapped_at: str,
    now_utc: str,
) -> bool:
    """A seed viewer whose window passed before its first full: that full goes up empty. The
    engine asks this when the first full starts and keeps the status line ("started without
    this device's history") once it commits, since this turns false then."""
    return (
        viewer in seed_viewers
        and not first_full_committed
        and bool(first_mapped_at)
        and not seeded(
            viewer,
            first_full_committed=False,
            seed_viewers=seed_viewers,
            seed_started_at=seed_started_at,
            first_mapped_at=first_mapped_at,
            now_utc=now_utc,
        )
    )


def first_round_settled(
    *, first_full_at: str, now_utc: str, queue_pending: int, run_pending: bool, unsent: bool, unapplied: bool
) -> bool:
    """A heuristic, stored once true: a quiet ping (no queued change, no pair run pending) at
    least SETTLE_SECONDS after the first complete full, with every fetched change applied and
    nothing of the person's waiting to go up. Times are UTC, as stored, so a restart or a
    suspend doesn't restart the wait."""
    since, now = _utc(first_full_at), _utc(now_utc)
    return (
        since is not None
        and now is not None
        and now - since >= SETTLE_SECONDS
        and queue_pending == 0
        and not run_pending
        and not unsent
        and not unapplied
    )
