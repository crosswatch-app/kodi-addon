# SPDX-License-Identifier: GPL-2.0-only
"""Library sync's worker: one thread that carries out what plan.next_action decides.

The service thread hands it inputs (settings, ping replies, playback quiet, scans, the
playlist index, the inbox having marks) through thread-safe methods; the worker folds them
into plan.EngineState between actions, so the decision always sees one consistent state. An
input that fails is logged and the others still apply; the thread never dies of one.

It owns the record (sync.db, opened on this thread, as sqlite3 connections belong to the
thread that made them), the last library read and the HTTP client. What it publishes for the
service thread is replaced whole, never changed in place: the upload epochs the ping ticket
carries, and the rows a media server add-on owns. Each row's file lives in the shared
local.FileIndex, which the service thread also updates on VideoLibrary.OnRemove.

JSON-RPC from this thread is allowed (executeJSONRPC releases the GIL, ModuleXbmc.cpp:125-128,
verified live, verification.md 2026-11-20); settings, dialogs and notifications stay on the
service thread.

Log events: sync.engine.started, sync.engine.stopped, sync.engine.action_failed (warning),
sync.engine.input_failed (warning), sync.engine.record_busy (info), sync.engine.waiting_on_action
(debug), sync.drain.applied (debug), sync.drain.dropped (info), sync.record.missing (warning).
"""

import os
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Literal

from resources.lib import paths
from resources.lib.config import Settings
from resources.lib.kodi import KodiApi
from resources.lib.log import get_logger
from resources.lib.media import strip_credentials
from resources.lib.storage import PromptMemory, ViewerStore
from resources.lib.sync import plan
from resources.lib.sync.files import (
    Inbox,
    InboxRecord,
    StatusFile,
    StatusState,
    read_requests,
    read_started,
    write_status,
)
from resources.lib.sync.items import ItemKind, Library, LibraryRow, RowKey
from resources.lib.sync.library_id import library_id, profile_has_own_database
from resources.lib.sync.local import FileIndex
from resources.lib.sync.manual import Recorded, ToAssign, watched_change
from resources.lib.sync.ownership import IndexSnapshot, owners_of
from resources.lib.sync.record import Batch, Entry, RecordMissing, RecordStore, merge
from resources.lib.sync.reply import PingTicket, SyncReply, connection_id
from resources.lib.sync.settings import SyncSettings
from resources.lib.sync.writer import read_row, seen_of

_log = get_logger("sync")

# How long the worker sleeps when there is nothing to do and no deadline: inputs wake it.
IDLE_SECONDS = 30.0
# Marks drained per commit: a long drain commits as it goes, so an exit loses nothing.
DRAIN_BATCH = 100

_KINDS: dict[str, ItemKind] = {"movie": "movies", "episode": "episodes"}
_KODI_TYPES: dict[ItemKind, str] = {"movies": "movie", "episodes": "episode"}
_DETAILS: dict[ItemKind, tuple[str, str, str]] = {
    "movies": ("VideoLibrary.GetMovieDetails", "movieid", "moviedetails"),
    "episodes": ("VideoLibrary.GetEpisodeDetails", "episodeid", "episodedetails"),
}

Drained = Literal["applied", "dropped", "wait", "retry"]


@dataclass(frozen=True)
class SyncPaths:
    db: str
    inbox: str
    started: str
    requests: str
    status: str

    @classmethod
    def of(cls, kodi: KodiApi) -> "SyncPaths":
        return cls(
            db=paths.sync_db_path(kodi),
            inbox=paths.sync_inbox_path(kodi),
            started=paths.sync_started_path(kodi),
            requests=paths.sync_requests_path(kodi),
            status=paths.sync_status_path(kodi),
        )


class _Gone(Exception):
    """Kodi has no row at that id."""


class SyncEngine:
    def __init__(
        self,
        *,
        kodi: KodiApi,
        viewer_store: ViewerStore,
        memory: PromptMemory,
        inbox: Inbox,
        files: FileIndex,
        ping_requested: threading.Event,
        rebuild_requested: threading.Event,
        sync_paths: SyncPaths,
        utc_now: Callable[[], str],
        clock: Callable[[], float] = time.monotonic,
        store_factory: Callable[[str], RecordStore] = RecordStore,
    ) -> None:
        self._kodi = kodi
        self._viewers = viewer_store
        self._memory = memory
        self._inbox = inbox
        self._files = files
        self._ping_requested = ping_requested
        self._rebuild_requested = rebuild_requested
        self._paths = sync_paths
        self._clock = clock
        self._utc_now = utc_now
        self._store_factory = store_factory

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._inputs: list[tuple[str, Any]] = []
        self._thread: threading.Thread | None = None

        # Worker-only from here on.
        self._store: RecordStore | None = None
        self._state = plan.EngineState()
        self._settings: Settings | None = None
        self._snapshot: IndexSnapshot | None = None
        self._scan_ended_at = 0.0
        self._show_ids_changed_at = 0.0
        self._last_status: StatusFile | None = None
        # Rating window writes, applied by the rating step before the next scan.
        self.rating_writes: list[tuple[str, int, int, tuple[str, ...]]] = []
        self.library: Library | None = None
        self.dropped_marks = 0

        # Published for the service thread; replaced whole.
        self.server_backed_rows: frozenset[tuple[str, int]] = frozenset()
        self.epochs: Mapping[str, int] = MappingProxyType({})

    # -- lifecycle ----------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="crosswatch-sync", daemon=True)
        self._thread.start()

    def stop(self, budget: float = 2.0) -> bool:
        """Ask the worker to stop and wait up to budget seconds; True when it did. Kodi waits
        for every thread after the service's main returns (PythonInvoker.cpp:384-405), so the
        worker stops at its next step or drained mark, never in the middle of a commit."""
        self._stopping.set()
        self._wake.set()
        thread = self._thread
        if thread is None:
            return True
        thread.join(budget)
        return not thread.is_alive()

    def _run(self) -> None:
        _log.info("sync.engine.started")
        try:
            self.begin()
            while not self._stopping.is_set():
                wait = self.step()
                if wait is not None and not self._stopping.is_set():
                    self._wake.wait(max(wait, 0.0))
                    self._wake.clear()
        finally:
            self.end()
            _log.info("sync.engine.stopped")

    def begin(self) -> None:
        """Start a session: open the record, and look at what a Kodi exit left behind (marks in
        the inbox, requests waiting)."""
        self._state = replace(
            plan.session_start(self._state, self._clock()),
            inbox_waiting=True,
            requests_waiting=bool(read_requests(self._paths.requests)),
        )
        self._open_store()

    def step(self) -> float | None:
        """Take the inputs, do what next_action says, write the status. The seconds to wait
        before the next step, or None for at once."""
        self._take_inputs()
        if self._stopping.is_set():
            return None
        if self._store is None and not self._state.record_missing:
            self._open_store()
            if self._store is None:
                return IDLE_SECONDS
        wait = self._act(plan.next_action(self._state, self._clock()))
        self._write_status()
        return wait

    def end(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    @property
    def state(self) -> plan.EngineState:
        return self._state

    def _open_store(self) -> None:
        store = self._store_factory(self._paths.db)
        try:
            store.open()
        except RecordMissing:
            _log.warning("sync.record.missing")
            self._state = replace(self._state, record_missing=True)
            return
        except sqlite3.OperationalError as exc:
            # Busy, for example while Start writes it: tried again on the next step.
            _log.info("sync.engine.record_busy", error=str(exc))
            return
        self._store = store
        self._load_record()

    def _load_record(self) -> None:
        """What the record knows that the plan needs: per viewer, a fresh full still owed and
        whether the first full is committed; and the files of the rows it has seen."""
        store = self._store
        assert store is not None
        viewers = dict(self._state.viewers)
        for viewer in self._known_viewers():
            vs = viewers.get(viewer, plan.ViewerState())
            fresh = store.fresh_pending(viewer)
            viewers[viewer] = replace(
                vs,
                fresh=fresh,
                full_due=vs.full_due or fresh,
                first_full_committed=store.first_full_committed(viewer),
                settled=store.settled(viewer),
            )
        self._state = replace(self._state, viewers=viewers)
        generation = self._files.generation()
        seen = store.seen_for_library(library_id(self._kodi))
        self._files.replace({(_KODI_TYPES[k.kind], k.kodi_id): k.file for k in seen}, generation)
        self._publish_epochs()

    def _publish_epochs(self) -> None:
        store = self._store
        if store is None:
            return
        reply = self._state.reply
        viewers = {*self._known_viewers(), *(reply.viewers if reply is not None else ())}
        self.epochs = MappingProxyType({v: store.seqs(v).epoch for v in viewers})

    # -- inputs, from any thread ---------------------------------------------------------

    def _put(self, name: str, value: Any = None) -> None:
        with self._lock:
            self._inputs.append((name, value))
        self._wake.set()

    def on_settings(self, settings: Settings) -> None:
        self._put("settings", settings)

    def on_ping_reply(self, reply: SyncReply | None, ticket: PingTicket | None) -> None:
        self._put("reply", (reply, ticket))

    def on_inbox(self) -> None:
        self._put("inbox")

    def on_remove(self, data: Mapping[str, Any]) -> None:
        self._put("remove", dict(data))

    def on_scan(self, active: bool) -> None:
        self._put("scan", active)

    def on_quiet(self, quiet: bool) -> None:
        self._put("quiet", quiet)

    def on_window(self, open_: bool) -> None:
        self._put("window", open_)

    def on_wake(self) -> None:
        self._put("wake")

    def on_request(self) -> None:
        self._put("request")

    def on_snapshot(self, snapshot: IndexSnapshot) -> None:
        self._put("snapshot", snapshot)

    def on_rating_written(self, kind: str, kodi_id: int, value: int, sync_ids: tuple[str, ...]) -> None:
        self._put("rating_written", (kind, kodi_id, value, sync_ids))

    # -- folding inputs into the state -------------------------------------------------------

    def _take_inputs(self) -> None:
        with self._lock:
            inputs, self._inputs = self._inputs, []
        for name, value in inputs:
            try:
                self._apply_input(name, value)
            except Exception as exc:
                _log.warning("sync.engine.input_failed", input=name, error=str(exc))

    def _apply_input(self, name: str, value: Any) -> None:
        state, now = self._state, self._clock()
        if name == "settings":
            self._apply_settings(value)
            return
        if name == "reply":
            self._apply_reply(*value)
            return
        if name == "inbox":
            state = replace(state, inbox_waiting=True)
        elif name == "remove":
            # The shared FileIndex drops the row on the service thread; the Seen it had goes
            # with the next library read.
            pass
        elif name == "scan":
            if state.scanning and not value:
                self._scan_ended_at = now
            state = replace(state, scanning=bool(value))
        elif name == "quiet":
            state = replace(state, quiet=bool(value))
        elif name == "window":
            state = replace(state, window_open=bool(value))
        elif name == "wake":
            state = plan.request_full(state)
        elif name == "request":
            state = replace(state, requests_waiting=bool(read_requests(self._paths.requests)))
        elif name == "rating_written":
            self.rating_writes.append(value)
        elif name == "snapshot":
            previous, self._snapshot = self._snapshot, value
            usable = value.usable(
                last_scan_end=self._scan_ended_at,
                last_viewer_change=0.0,
                show_ids_changed_at=self._show_ids_changed_at,
            )
            after_scan = self._scan_ended_at > (previous.built_at if previous is not None else 0.0)
            state = plan.on_snapshot(state, usable=usable, after_scan=usable and after_scan)
        self._state = state

    def _apply_reply(self, reply: SyncReply | None, ticket: PingTicket | None) -> None:
        store = self._store
        seqs = {v: store.seqs(v) for v in reply.viewers} if store is not None and reply is not None else {}
        state, resets = plan.on_reply(self._state, reply, ticket, seqs, self._clock())
        if store is not None:
            for reset in resets:
                store.reset_for_cw(reset.viewer, reset.cw_seq, fresh=reset.fresh)
        self._state = state
        self._publish_epochs()

    def _apply_settings(self, settings: Settings) -> None:
        previous, self._settings = self._settings, settings
        started = read_started(self._paths.started)
        on_this = started is not None and started.connection == connection_id(settings.webhook_base_url)
        state = replace(
            self._state,
            started=on_this,
            sync_enabled=settings.sync_enabled,
            paused=bool(on_this and started is not None and started.paused),
        )
        moved = previous is not None and (
            previous.webhook_base_url != settings.webhook_base_url or previous.webhook_token != settings.webhook_token
        )
        if moved:
            state = plan.on_token_changed(state, self._clock())
        self._state = state

    # -- actions ------------------------------------------------------------------------

    def _act(self, action: plan.Action) -> float | None:
        """Carry out one action; the seconds to wait before the next, or None for at once."""
        now = self._clock()
        if isinstance(action, plan.Idle):
            return IDLE_SECONDS
        if isinstance(action, plan.Wait):
            return action.until - now
        try:
            if isinstance(action, plan.Ping):
                self._ping_requested.set()
                self._state = plan.on_ping_asked(self._state, now)
            elif isinstance(action, plan.RequestRebuild):
                self._rebuild_requested.set()
                self._state = plan.on_rebuild_asked(self._state)
            elif isinstance(action, plan.DrainInbox):
                self._drain_inbox()
            else:
                return self._waiting_on_action(action)
        except Exception as exc:
            _log.warning("sync.engine.action_failed", action=type(action).__name__, error=str(exc))
            return IDLE_SECONDS
        return None

    def _waiting_on_action(self, action: plan.Action) -> float:
        """Actions this worker doesn't carry out yet. Requests are left for later without
        blocking the drain behind them; everything else waits instead of spinning."""
        if isinstance(action, plan.TakeRequests):
            self._state = replace(self._state, requests_waiting=False)
        _log.debug("sync.engine.waiting_on_action", action=type(action).__name__)
        return IDLE_SECONDS

    # -- the inbox ----------------------------------------------------------------------

    def _drain_inbox(self) -> None:
        """A person's marks, from the inbox file into the record, a batch per commit; a record
        leaves the inbox only after the commit that took it, and the record keeps the highest
        id it applied, so a crash in between never applies a mark twice.

        A mark is applied to the row it was made on: a fresh read whose file is the recorded
        one, or the entry the recorded file and the fresh row's tokens agree on (the same item
        under a new id); a mark that matches neither is dropped and counted. A mark Kodi can't
        be asked about right now stays for the next drain. A mark on an episode whose show the
        last library read didn't have goes to the back of the inbox and waits for a read.
        """
        store, library = self._store, self.library
        if store is None or library is None:
            self._state = replace(self._state, inbox_waiting=False)
            return
        records = self._inbox.pending(DRAIN_BATCH)
        applied_upto = store.inbox_applied()
        owners = self._owners(library)
        batch = Batch()
        waiting: list[InboxRecord] = []
        dropped = 0
        last = 0
        progress = False
        for record in records:
            if self._stopping.is_set():
                break
            if record.id <= applied_upto:
                last = record.id
                continue
            outcome = self._drain_one(store, library, owners, record, batch)
            if outcome == "retry":
                break
            last = record.id
            if outcome == "wait":
                waiting.append(record)
                continue
            progress = True
            dropped += outcome == "dropped"
        if last:
            batch.inbox_applied = last
            store.commit(batch)
            self.dropped_marks += dropped
            if waiting:
                # Back of the queue, so the marks behind them aren't held up.
                self._inbox.add_many([replace(r, id=0) for r in waiting])
            self._inbox.remove(last)
        more = len(records) == DRAIN_BATCH or (bool(waiting) and progress)
        self._state = replace(self._state, inbox_waiting=more and last > 0)

    def _drain_one(
        self,
        store: RecordStore,
        library: Library,
        owners_of_row: Callable[[LibraryRow], tuple[str, ...]],
        record: InboxRecord,
        batch: Batch,
    ) -> Drained:
        kind = _KINDS.get(record.kind)
        if kind is None:
            _log.info("sync.drain.dropped", kind=record.kind, kodi_id=record.kodi_id, reason="kind")
            return "dropped"
        try:
            fresh = self._fresh_row(library, kind, record.kodi_id, record.file)
        except _Gone:
            fresh = None
        except Exception:
            return "retry"
        if fresh is None:
            _log.info("sync.drain.dropped", kind=record.kind, kodi_id=record.kodi_id, reason="other_item")
            return "dropped"
        show = library.tvshows.get(fresh.show_kodi_id) if fresh.show_kodi_id is not None else None
        if kind == "episodes" and show is None:
            return "wait"
        owners = owners_of_row(fresh)
        entries: dict[str, Entry] = {}
        for viewer in owners:
            found = [_pending(batch, viewer, e) for e in store.match(viewer, fresh.tokens)]
            if len(found) > 1:
                entry = merge(found)
                batch.merged.setdefault(viewer, {})[entry.key] = sorted({e.key for e in found})
                # Stored whatever the mark does: the commit drops the merged-away keys.
                _put_entry(batch, viewer, entry)
                entries[viewer] = entry
            elif found:
                entries[viewer] = found[0]
        settings = self._settings.sync_settings if self._settings is not None else SyncSettings()
        outcomes = watched_change(
            entries,
            fresh,
            show,
            playcount=record.playcount,
            lastplayed=str(fresh.raw.get("lastplayed") or ""),
            owners=owners,
            settings=settings,
            made_at=record.at,
            next_upload_seq=self._next_upload_seq(store, owners),
            new_id=_new_id,
        )
        for out in outcomes:
            if isinstance(out, Recorded):
                _put_entry(batch, out.viewer, out.entry)
            elif isinstance(out, ToAssign):
                batch.assignments_add.append(out.assignment)
        # Kodi announces nothing for a rating, so the rating and date the scan compares keep
        # their stored values; the rest is what Kodi has now.
        stored = store.seen(fresh.key)
        now_seen = seen_of(fresh.raw)
        if stored is not None:
            now_seen = replace(now_seen, userrating=stored.userrating, dateadded=stored.dateadded)
        batch.seen[fresh.key] = now_seen
        batch.seen_tokens[fresh.key] = fresh.tokens
        _log.debug("sync.drain.applied", kind=record.kind, kodi_id=record.kodi_id, outcomes=len(outcomes))
        return "applied"

    def _fresh_row(self, library: Library, kind: ItemKind, kodi_id: int, file: str) -> LibraryRow | None:
        """The row the mark was made on, read now; None when the id holds another item.
        Raises _Gone when Kodi has no such row, and what Kodi raises when it can't answer."""
        method, id_field, key = _DETAILS[kind]
        details = self._kodi.jsonrpc(method, {id_field: kodi_id, "properties": ["file"]}).get(key)
        if not isinstance(details, dict):
            raise _Gone
        current = strip_credentials(str(details.get("file") or ""))
        row = read_row(self._kodi, RowKey(library.library_id, kind, kodi_id, current), library.tvshows)
        if row is None:
            raise _Gone
        if not file or file == current:
            return row
        store = self._store
        if store is not None:
            for viewer in self._known_viewers():
                if any(entry.meta.get("file") == file for entry in store.match(viewer, row.tokens)):
                    return row
        return None

    def _known_viewers(self) -> list[str]:
        return [v.sync_id for v in self._viewers.viewers() if v.sync_id]

    def _owners(self, library: Library) -> Callable[[LibraryRow], tuple[str, ...]]:
        snapshot = self._snapshot
        if snapshot is None:
            return lambda row: ()
        return owners_of(
            snapshot,
            library.tvshows,
            self._memory.entries(),
            _profile_label(self._kodi),
            profile_has_own_database(self._kodi),
        )

    def _next_upload_seq(self, store: RecordStore, owners: tuple[str, ...]) -> int:
        reply = self._state.reply
        highest = 0
        for viewer in owners:
            highest = max(highest, store.seqs(viewer).sent)
            if reply is not None and viewer in reply.viewers:
                highest = max(highest, reply.viewers[viewer].upload_seq)
        return highest + 1

    # -- status -------------------------------------------------------------------------

    def _write_status(self) -> None:
        """The status screen's file, written when what it says changed."""
        state = self._state
        status: StatusState
        reason = ""
        if not state.started or not state.sync_enabled:
            status = "off"
        elif state.record_missing or state.unauthorized or state.no_sync:
            status = "stopped"
            reason = "record_missing" if state.record_missing else "unauthorized" if state.unauthorized else "no_sync"
        elif state.paused:
            status, reason = "stopped", "paused"
        elif state.reply is not None and state.reply.reason:
            status, reason = "waiting", state.reply.reason
        elif not plan.active(state) or plan.waiting_on(state) is not None:
            status, reason = "waiting", plan.waiting_on(state) or ""
        elif state.viewers and all(vs.settled for vs in state.viewers.values()):
            status = "settled"
        else:
            status = "syncing"
        per_viewer = {
            v: vs.uploads_stopped or vs.error for v, vs in state.viewers.items() if vs.uploads_stopped or vs.error
        }
        try:
            dropped = self._inbox.dropped() + self.dropped_marks
        except Exception:
            dropped = self.dropped_marks
        current = StatusFile(state=status, reason=reason, per_viewer=per_viewer, dropped_marks=dropped)
        if current == self._last_status:
            return
        if write_status(self._paths.status, replace(current, updated=self._utc_now())):
            self._last_status = current


def _new_id() -> str:
    return os.urandom(8).hex()


def _profile_label(kodi: KodiApi) -> str:
    try:
        return str(kodi.jsonrpc("Profiles.GetCurrentProfile").get("label") or "").strip()
    except Exception:
        return ""


def _pending(batch: Batch, viewer: str, entry: Entry) -> Entry:
    """The entry as this batch already changed it, so two marks on one item build on each
    other."""
    for e in batch.entries.get(viewer, []):
        if e.key == entry.key:
            return e
    return entry


def _put_entry(batch: Batch, viewer: str, entry: Entry) -> None:
    entries = batch.entries.setdefault(viewer, [])
    entries[:] = [e for e in entries if e.key != entry.key] + [entry]
