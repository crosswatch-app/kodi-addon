import os
import sqlite3
import threading
from dataclasses import replace

import pytest

from resources.lib.config import Settings
from resources.lib.media import clean_ids
from resources.lib.models import Viewer
from resources.lib.playlist_index import PlaylistIndex
from resources.lib.prompt import key_for_show
from resources.lib.storage import JsonViewerStore, PromptMemory
from resources.lib.sync import items, plan
from resources.lib.sync.constants import (
    BACKOFF_SECONDS,
    DAILY_FULL_SECONDS,
    QUEUE_FALLBACK_SECONDS,
    STATUS_FIGURES_SECONDS,
)
from resources.lib.sync.engine import IDLE_SECONDS, SyncEngine, SyncPaths
from resources.lib.sync.files import (
    Inbox,
    InboxRecord,
    StartedFile,
    StatusFile,
    read_started,
    read_status,
    write_started,
    write_status,
)
from resources.lib.sync.items import Library, LibraryRow, RowKey
from resources.lib.sync.local import FileIndex
from resources.lib.sync.ownership import IndexSnapshot
from resources.lib.sync.record import Batch, Entry, History, ItemState, RecordStore
from resources.lib.sync.reply import PingTicket, ReplyViewer, SyncReply, connection_id
from tests.fakes import FakeKodi
from tests.sync.fake_library import FakeLibrary

URL = "http://nas:8787/webhook/kodiwatcher"
NOW = "2026-11-20T10:00:00Z"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Rig:
    def __init__(self, tmp_path, *, record: bool = True, started: bool = True) -> None:
        self.paths = SyncPaths(
            db=str(tmp_path / "sync.db"),
            inbox=str(tmp_path / "inbox.db"),
            started=str(tmp_path / "started.json"),
            requests=str(tmp_path / "requests.json"),
            status=str(tmp_path / "status.json"),
        )
        if record:
            store = RecordStore(self.paths.db)
            store.open(create=True)
            store.close()
        if started:
            write_started(self.paths.started, StartedFile(connection=connection_id(URL), start_id="s"))
        self.lib = FakeLibrary()
        self.lib.add_movie(3, title="Film", year=2001, uniqueid={"tmdb": "3"}, file="/m/3.mkv")
        self.kodi = FakeKodi(rpc_handlers=self.lib.handlers())
        self.viewers = JsonViewerStore(str(tmp_path / "viewers.json"))
        self.viewers.save([Viewer(name="anna", sync_id="kv-a")])
        self.inbox = Inbox(self.paths.inbox)
        self.files = FileIndex()
        self.ping, self.rebuild = threading.Event(), threading.Event()
        self.clock = Clock()
        self.engine = SyncEngine(
            kodi=self.kodi,
            viewer_store=self.viewers,
            memory=PromptMemory(str(tmp_path / "prompts.json")),
            inbox=self.inbox,
            files=self.files,
            ping_requested=self.ping,
            rebuild_requested=self.rebuild,
            sync_paths=self.paths,
            clock=self.clock,
            utc_now=lambda: NOW,
        )

    def library(self) -> Library:
        raw = dict(self.lib.row("movies", 3))
        row = LibraryRow(RowKey("L1", "movies", 3, "/m/3.mkv"), tuple(items.tokens("movies", raw)), raw, None)
        return Library("L1", [row], [], {}, [], True)

    def ready(self) -> None:
        """Started, sync on, quiet, a usable index, the library read."""
        self.engine.begin()
        self.engine.on_settings(Settings(webhook_base_url=URL, webhook_token="t", sync_enabled=True))
        self.engine.on_quiet(True)
        snapshot = IndexSnapshot(index=PlaylistIndex(), built_at=1.0, invalidated_at=0.0,
                                 viewers=(Viewer(name="anna", sync_id="kv-a"),))  # fmt: skip
        self.engine.on_snapshot(snapshot)
        self.engine.library = self.library()

    def mark(self, playcount: int = 1, file: str = "/m/3.mkv", kodi_id: int = 3) -> None:
        assert self.inbox.add_many([InboxRecord(at=NOW, kind="movie", kodi_id=kodi_id, file=file, playcount=playcount)])
        self.engine.on_inbox()


def test_inputs_thread_safe(tmp_path):
    rig = Rig(tmp_path)
    rig.engine.begin()

    def flood() -> None:
        for _ in range(200):
            rig.engine.on_quiet(True)
            rig.engine.on_scan(False)

    threads = [threading.Thread(target=flood) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rig.engine.step()
    assert rig.engine.state.quiet and not rig.engine.state.scanning
    # Everything taken, nothing left over.
    assert rig.engine._inputs == []


def test_inbox_drained_and_removed_after_commit(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.lib.row("movies", 3)["playcount"] = 1
    rig.mark()
    assert rig.engine.step() is None
    assert rig.inbox.pending() == []
    store = RecordStore(rig.paths.db)
    store.open()
    [entry] = store.entries("kv-a")
    assert entry.state.history.watched and entry.unsent == {"history": NOW} and entry.origin == "person"
    seen = store.seen(RowKey("L1", "movies", 3, "/m/3.mkv"))
    assert seen is not None and seen.playcount == 1
    store.close()
    assert not rig.engine.state.inbox_waiting


def test_inbox_kept_when_the_commit_fails(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.ready()
    rig.mark()

    def fails(batch):
        raise RuntimeError("disk full")

    store = rig.engine._store
    assert store is not None
    monkeypatch.setattr(store, "commit", fails)
    assert rig.engine.step() == IDLE_SECONDS
    assert len(rig.inbox.pending()) == 1


def test_mark_on_a_reused_id_is_dropped(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.mark(file="/m/old.mkv")
    rig.engine.step()
    assert rig.inbox.pending() == []
    assert rig.engine.dropped_marks == 1


def test_mark_on_a_moved_item_follows_its_entry(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    store = rig.engine._store
    assert store is not None
    tokens = frozenset(rig.library().movies[0].tokens)
    store.commit(
        Batch(entries={"kv-a": [Entry(key="k", tokens=tokens, meta={"file": "/m/old.mkv"}, state=ItemState())]})
    )
    rig.mark(file="/m/old.mkv")
    rig.engine.step()
    [entry] = store.entries("kv-a")
    assert entry.state.history.watched and rig.engine.dropped_marks == 0


def test_drain_waits_for_quiet_and_a_usable_index(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.on_quiet(False)
    rig.mark()
    rig.engine.step()
    assert len(rig.inbox.pending()) == 1
    rig.engine.on_quiet(True)
    rig.engine.step()
    assert rig.inbox.pending() == []


def test_status_written_after_each_action(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.state == "waiting" and status.updated == NOW
    reply = SyncReply(enabled=False, reason="library_has_sync_device", viewers={}, queue_pending=0)
    rig.engine.on_ping_reply(reply, PingTicket(sent_at=rig.clock.now))
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.reason == "library_has_sync_device"


def test_status_when_the_record_is_missing(tmp_path):
    rig = Rig(tmp_path, record=False)
    rig.ready()
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and (status.state, status.reason) == ("stopped", "record_missing")


def test_never_set_up_writes_nothing(tmp_path):
    rig = Rig(tmp_path, record=False, started=False)
    rig.ready()
    rig.engine.step()
    assert read_status(rig.paths.status) is None and not os.path.exists(rig.paths.inbox)
    assert not os.path.exists(rig.paths.db)


def test_start_after_the_service_started_brings_sync_up(tmp_path):
    rig = Rig(tmp_path, record=False, started=False)
    rig.ready()
    rig.engine.step()
    assert rig.engine.state.record_missing
    store = RecordStore(rig.paths.db)
    store.open(create=True)
    store.close()
    write_started(rig.paths.started, StartedFile(connection=connection_id(URL), start_id="s"))
    rig.engine.on_settings(Settings(webhook_base_url=URL, webhook_token="t", sync_enabled=True, sync_request=1))
    rig.engine.step()
    assert not rig.engine.state.record_missing and rig.engine.state.started


def test_ping_and_rebuild_are_asked_through_events(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.on_ping_reply(
        SyncReply(enabled=True, reason=None, viewers={"kv-a": ReplyViewer("default", 0)}, queue_pending=0),
        PingTicket(sent_at=rig.clock.now - 10, epochs={"kv-a": 0}),
    )
    # A ticket from before this session's gate isn't judged: the full waits for a ping,
    # asked after the ping backoff that reply started.
    rig.clock.now += 10
    assert rig.engine.step() is None  # the inbox check at start, empty
    assert rig.engine.step() is None  # the rating scan due at start, while the ping waits
    wait = rig.engine.step()
    assert wait == BACKOFF_SECONDS[0] and not rig.ping.is_set()
    rig.clock.now += wait
    rig.engine.step()
    assert rig.ping.is_set()
    rig.engine._state = replace(rig.engine.state, rebuild_wanted=True)
    rig.engine.step()
    assert rig.rebuild.is_set() and not rig.engine.state.rebuild_wanted


def test_connection_change_arms_the_gate(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.step()
    before = rig.engine.state.armed_at
    rig.clock.now += 5
    rig.engine.on_settings(Settings(webhook_base_url=URL, webhook_token="other", sync_enabled=True))
    rig.engine.step()
    assert rig.engine.state.armed_at == before + 5


def test_stop_within_budget(tmp_path):
    rig = Rig(tmp_path)
    rig.engine.start()
    rig.engine.on_quiet(True)
    assert rig.engine.stop(budget=2.0)
    assert rig.engine.stop(budget=0.1)


def test_stop_before_start(tmp_path):
    assert Rig(tmp_path).engine.stop()


def test_published_epochs_swap_atomically(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    seen = rig.engine.epochs
    assert dict(seen) == {"kv-a": 0}
    reply = SyncReply(enabled=True, reason=None, viewers={"kv-a": ReplyViewer("default", 9)}, queue_pending=0)
    rig.engine.on_ping_reply(reply, PingTicket(sent_at=rig.clock.now, epochs={"kv-a": 0}))
    rig.engine.step()
    # The reset above CrossWatch's seq raised the epoch; a reader holding the old mapping
    # still sees it whole.
    assert dict(seen) == {"kv-a": 0} and dict(rig.engine.epochs) == {"kv-a": 1}
    with pytest.raises(TypeError):
        rig.engine.epochs["kv-a"] = 5  # type: ignore[index]


def test_wake_asks_for_fulls(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.on_ping_reply(
        SyncReply(enabled=True, reason=None, viewers={"kv-a": ReplyViewer("default", 0)}, queue_pending=0),
        PingTicket(sent_at=rig.clock.now, epochs={"kv-a": 0}),
    )
    rig.engine.step()
    rig.engine._state = replace(
        rig.engine.state, viewers={"kv-a": replace(rig.engine.state.viewers["kv-a"], full_due=False)}
    )
    rig.engine.on_wake()
    rig.engine._take_inputs()
    assert rig.engine.state.viewers["kv-a"].full_due


def test_watched_entry_untouched_by_a_repeated_mark(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    store = rig.engine._store
    assert store is not None
    tokens = frozenset(rig.library().movies[0].tokens)
    watched = Entry(key="k", tokens=tokens, meta={}, state=ItemState(history=History(watched=True, playcount=2)))
    store.commit(Batch(entries={"kv-a": [watched]}))
    rig.mark(playcount=3)
    rig.engine.step()
    [entry] = store.entries("kv-a")
    assert entry.unsent == {} and entry.state.history.playcount == 2


def _store(rig: Rig) -> RecordStore:
    store = rig.engine._store
    assert store is not None
    return store


def test_status_shows_the_last_accepted_upload(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.step()
    _store(rig).commit(Batch(first_full_done={"kv-a"}, uploaded_at="2026-11-20T09:30:00Z"))
    rig.clock.now += STATUS_FIGURES_SECONDS
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.last_upload == "2026-11-20T09:30:00Z"


def test_merge_kept_when_the_mark_records_nothing(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    tokens = sorted(rig.library().movies[0].tokens)
    watched = ItemState(history=History(watched=True, playcount=1))
    _store(rig).commit(Batch(entries={"kv-a": [
        Entry(key="a", tokens=frozenset(tokens[:1]), meta={}, state=watched),
        Entry(key="b", tokens=frozenset(tokens), meta={}, state=watched, unsent={"history": NOW}),
    ]}))  # fmt: skip
    rig.mark()
    rig.engine.step()
    [entry] = _store(rig).entries("kv-a")
    assert entry.key == "a" and entry.state.history.watched and entry.unsent == {"history": NOW}


def test_failing_input_is_logged_and_the_rest_still_apply(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.step()

    def busy(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(_store(rig), "seqs", busy)
    rig.engine.on_ping_reply(
        SyncReply(enabled=True, reason=None, viewers={"kv-a": ReplyViewer("default", 0)}, queue_pending=0),
        PingTicket(sent_at=rig.clock.now),
    )
    rig.engine.on_quiet(False)
    rig.engine.step()
    assert rig.engine.state.quiet is False


def test_busy_record_is_opened_on_a_later_step(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    calls = {"n": 0}
    real = RecordStore.open

    def busy_once(self, create=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(self, create)

    monkeypatch.setattr(RecordStore, "open", busy_once)
    rig.engine.begin()
    assert rig.engine._store is None and not rig.engine.state.record_missing
    rig.engine.step()
    assert rig.engine._store is not None


def test_stale_index_is_not_usable(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    stale = IndexSnapshot(index=PlaylistIndex(), built_at=1.0, invalidated_at=2.0, viewers=())
    rig.engine.on_snapshot(stale)
    rig.engine.step()
    assert not rig.engine.state.snapshot_usable


def test_index_after_a_scan_asks_for_fulls(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.on_ping_reply(
        SyncReply(enabled=True, reason=None, viewers={"kv-a": ReplyViewer("default", 0)}, queue_pending=0),
        PingTicket(sent_at=rig.clock.now, epochs={"kv-a": 0}),
    )
    rig.engine.step()
    rig.engine._state = replace(
        rig.engine.state, viewers={"kv-a": replace(rig.engine.state.viewers["kv-a"], full_due=False)}
    )
    rig.engine.on_scan(True)
    rig.clock.now += 10
    rig.engine.on_scan(False)
    rig.engine.on_snapshot(
        IndexSnapshot(index=PlaylistIndex(), built_at=rig.clock.now + 1, invalidated_at=0.0, viewers=())
    )
    rig.engine._take_inputs()
    assert rig.engine.state.viewers["kv-a"].full_due


def test_marks_left_by_an_exit_are_drained_at_start(tmp_path):
    rig = Rig(tmp_path)
    assert rig.inbox.add_many([InboxRecord(at=NOW, kind="movie", kodi_id=3, file="/m/3.mkv", playcount=1)])
    rig.ready()
    rig.engine.step()
    assert rig.inbox.pending() == []


def test_fresh_full_owed_survives_a_restart(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.on_ping_reply(
        SyncReply(enabled=True, reason=None, viewers={"kv-a": ReplyViewer("default", 9)}, queue_pending=0),
        PingTicket(sent_at=rig.clock.now, epochs={"kv-a": 0}),
    )
    rig.engine.step()
    assert rig.engine.state.viewers["kv-a"].fresh
    rig.engine.end()
    again = Rig(tmp_path, record=False, started=False)
    again.engine.begin()
    vs = again.engine.state.viewers["kv-a"]
    assert vs.fresh and vs.full_due


def test_waiting_mark_does_not_block_the_others(tmp_path):
    rig = Rig(tmp_path)
    rig.lib.add_show(7, title="Show")
    rig.lib.add_episode(41, 7, 1, 1, file="/e/1.mkv", playcount=1)
    rig.ready()
    assert rig.inbox.add_many([InboxRecord(at=NOW, kind="episode", kodi_id=41, file="/e/1.mkv", playcount=1)])
    rig.mark()
    rig.engine.step()
    assert [r.kind for r in rig.inbox.pending()] == ["episode"]
    assert _store(rig).entries("kv-a")[0].state.history.watched
    # Nothing more to do until a library read has the show.
    rig.engine.step()
    assert not rig.engine.state.inbox_waiting


def test_marks_already_applied_are_not_applied_again(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.ready()
    rig.mark()
    monkeypatch.setattr(rig.inbox, "remove", lambda upto: None)
    rig.engine.step()
    before = _store(rig).entries("kv-a")
    rig.engine.on_inbox()
    rig.engine.step()
    assert _store(rig).entries("kv-a") == before and len(rig.inbox.pending()) == 1


def test_kodi_not_answering_keeps_the_mark(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()

    def fails(params):
        raise RuntimeError("busy")

    rig.kodi.rpc_handlers["VideoLibrary.GetMovieDetails"] = fails
    rig.mark()
    rig.engine.step()
    assert len(rig.inbox.pending()) == 1 and rig.engine.dropped_marks == 0


def test_unknown_kind_dropped(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    assert rig.inbox.add_many([InboxRecord(at=NOW, kind="musicvideo", kodi_id=3, file="", playcount=1)])
    rig.engine.on_inbox()
    rig.engine.step()
    assert rig.inbox.pending() == [] and rig.engine.dropped_marks == 1


def test_rating_window_write_applied_on_the_next_step(tmp_path):
    """Not held until the next scan: a Kodi exit before it would lose the write."""
    rig = Rig(tmp_path)
    rig.ready()
    rig.lib.row("movies", 3)["userrating"] = 8
    rig.engine.on_rating_written("movie", 3, 8, ("kv-a",))
    rig.engine.step()
    store = rig.engine._store
    assert store is not None and rig.engine.rating_writes == []
    [entry] = store.entries("kv-a")
    assert entry.state.rating.value == 8 and entry.touched and rig.engine.state.viewers["kv-a"].unsent


def test_status_for_a_paused_device_and_only_when_changed(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    write_started(rig.paths.started, StartedFile(connection=connection_id(URL), start_id="s", paused=True))
    rig.ready()
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and (status.state, status.reason) == ("stopped", "paused")
    writes = []
    monkeypatch.setattr("resources.lib.sync.engine.write_status", lambda path, st: writes.append(st) or True)
    rig.engine.step()
    assert writes == []


def test_a_device_paused_for_another_sync_device_says_so(tmp_path):
    rig = Rig(tmp_path)
    write_started(rig.paths.started, StartedFile(connection=connection_id(URL), start_id="s", paused=True,
                                                 paused_reason="not_sync_device"))  # fmt: skip
    rig.ready()
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and (status.state, status.reason) == ("stopped", "not_sync_device")


def test_window_and_requests_inputs(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.on_window(True)
    rig.engine.on_request()
    rig.engine._take_inputs()
    assert rig.engine.state.window_open and not rig.engine.state.requests_waiting


def test_changed_device_id_pauses_the_worker(tmp_path):
    rig = Rig(tmp_path)
    write_started(rig.paths.started, StartedFile(connection=connection_id(URL), start_id="s", bound_device="old-id"))
    rig.engine._device_id = lambda: "new-id"
    rig.ready()
    rig.engine.step()
    assert rig.engine.state.paused


def _reply(enabled: bool = True, reason: str | None = None) -> SyncReply:
    return SyncReply(enabled=enabled, reason=reason, viewers={"kv-a": ReplyViewer("default", 0)}, queue_pending=0)


def test_sync_device_id_recorded_when_crosswatch_says_enabled(tmp_path):
    rig = Rig(tmp_path)
    rig.engine._device_id = lambda: "box-1"
    rig.ready()
    rig.engine.on_ping_reply(_reply(), PingTicket(sent_at=rig.clock.now, epochs={"kv-a": 0}, enabled=True))
    rig.engine.step()
    started = read_started(rig.paths.started)
    assert started is not None and started.bound_device == "box-1"


def test_not_sync_device_pauses(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.on_ping_reply(_reply(False, "not_sync_device"), PingTicket(sent_at=rig.clock.now, enabled=True))
    rig.engine.step()
    started = read_started(rig.paths.started)
    assert started is not None and started.paused and started.paused_reason == "not_sync_device"
    assert rig.engine.state.paused


def test_turning_sync_off_pauses_once_crosswatch_took_it(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.step()
    rig.engine.on_settings(Settings(webhook_base_url=URL, webhook_token="t", sync_enabled=False))
    rig.engine.on_ping_reply(_reply(False), PingTicket(sent_at=rig.clock.now, enabled=False))
    rig.engine.step()
    started = read_started(rig.paths.started)
    assert started is not None and started.paused_reason == "turned_off"


def test_enabled_false_right_after_start_does_not_pause(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.on_ping_reply(_reply(False), PingTicket(sent_at=rig.clock.now, enabled=False))
    rig.engine.step()
    started = read_started(rig.paths.started)
    assert started is not None and not started.paused


def test_crosswatch_syncing_none_of_the_viewers_shows_in_the_status(tmp_path):
    # CrossWatch answers, but maps no viewer to a sync: no pair, a pair switched off, or a Kodi
    # it reaches over JSON-RPC and syncs itself.
    rig = Rig(tmp_path)
    rig.ready()
    nobody = SyncReply(enabled=True, reason=None, viewers={}, queue_pending=0)
    rig.engine.on_ping_reply(nobody, PingTicket(sent_at=rig.clock.now, enabled=True))
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and (status.state, status.reason) == ("waiting", "no_viewers")
    # CrossWatch stops answering: that is the news now, not the last answer.
    rig.engine._state = replace(rig.engine.state, ping_failures=1)
    rig.engine._write_status()
    status = read_status(rig.paths.status)
    assert status is not None and (status.state, status.reason) == ("waiting", "crosswatch")
    rig.engine._state = replace(rig.engine.state, ping_failures=0)
    # Mapped on a later answer: the line goes.
    rig.clock.now += 1
    rig.engine.on_ping_reply(_reply(True), PingTicket(sent_at=rig.clock.now, enabled=True))
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.reason != "no_viewers"


def test_changed_device_id_shows_in_the_status(tmp_path):
    rig = Rig(tmp_path)
    write_started(rig.paths.started, StartedFile(connection=connection_id(URL), start_id="s", bound_device="old-id"))
    rig.engine._device_id = lambda: "new-id"
    rig.ready()
    rig.engine.on_ping_reply(_reply(False, "not_sync_device"), PingTicket(sent_at=rig.clock.now, enabled=False))
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.reason == "device_id_changed"
    # The old id set back: the next answer clears it.
    rig.engine._device_id = lambda: "old-id"
    rig.clock.now += 1
    rig.engine.on_ping_reply(_reply(True), PingTicket(sent_at=rig.clock.now, enabled=True))
    rig.engine.step()
    assert not rig.engine.device_id_changed


def test_status_carries_the_figures_for_the_status_screen(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.lib.row("movies", 3)["playcount"] = 1
    rig.mark()
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None
    assert status.waiting_changes == 1 and status.last_upload == ""
    assert status.counts == {"viewers": {"kv-a": [0, 1]}, "shared": [0, 0], "unowned": [0, 0]}
    assert status.unowned_titles == [] and status.unreadable_playlists == []


def test_status_names_unreadable_playlists_and_recounts_on_a_new_index(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.step()
    rig.viewers.save([Viewer(name="anna", sync_id="kv-a"), Viewer(name="ben", sync_id="kv-b")])
    snapshot = IndexSnapshot(index=PlaylistIndex(unreadable=frozenset({"Anna's films"})), built_at=2.0,
                             invalidated_at=0.0, viewers=(Viewer(name="anna", sync_id="kv-a"),
                                                          Viewer(name="ben", sync_id="kv-b")))  # fmt: skip
    rig.engine.on_snapshot(snapshot)
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.unreadable_playlists == ["Anna's films"]
    # Two viewers and no playlist or answer for the film: nobody owns it.
    assert status.counts["unowned"] == [0, 1] and status.unowned_titles == ["Film (2001)"]


def _status_of(rig: "Rig", **viewer: object) -> tuple[str, str, dict]:
    state = rig.engine.state
    vs = replace(plan.ViewerState(), settled=True, **viewer)  # type: ignore[arg-type]
    rig.engine._state = replace(state, viewers={"kv-a": vs})
    rig.engine._write_status()
    status = read_status(rig.paths.status)
    assert status is not None
    return status.state, status.reason, status.per_viewer


def _cw_ready(rig: "Rig") -> None:
    rig.ready()
    reply = SyncReply(enabled=True, reason=None, viewers={"kv-a": ReplyViewer("default", 0)}, queue_pending=0)
    rig.engine._state = replace(rig.engine.state, reply=reply)


def test_up_to_date_needs_the_first_round_settled_and_nothing_waiting(tmp_path):
    rig = Rig(tmp_path)
    _cw_ready(rig)
    rig.engine._take_inputs()
    assert _status_of(rig)[0] == "settled"
    assert _status_of(rig, unsent=True)[0] == "syncing"
    assert _status_of(rig, acks_pending=True)[0] == "syncing"
    assert _status_of(rig, failures=1, error="network")[:2] == ("waiting", "crosswatch")
    assert _status_of(rig, run_unknown=True)[:2] == ("waiting", "crosswatch")


def test_a_halving_too_large_is_not_reported_but_a_stop_is(tmp_path):
    rig = Rig(tmp_path)
    _cw_ready(rig)
    rig.engine._take_inputs()
    assert _status_of(rig, error="too_large")[2] == {}
    assert _status_of(rig, error="too_large", uploads_stopped="too_large")[2] == {"kv-a": "too_large"}
    assert _status_of(rig, error="certificate")[2] == {"kv-a": "certificate"}


def test_a_new_session_keeps_the_last_counts_until_it_reads_the_library(tmp_path):
    rig = Rig(tmp_path)
    counts = {"viewers": {"kv-a": [3, 4]}, "shared": [0, 0], "unowned": [1, 0]}
    write_status(rig.paths.status, StatusFile(state="settled", counts=counts, unowned_titles=["Some Show"]))
    rig.engine.begin()
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.counts == counts and status.unowned_titles == ["Some Show"]


def test_counts_follow_a_remembered_answer_without_a_new_index(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    viewers = (Viewer(name="anna", sync_id="kv-a"), Viewer(name="ben", sync_id="kv-b"))
    rig.viewers.save(list(viewers))
    rig.engine.on_snapshot(IndexSnapshot(index=PlaylistIndex(), built_at=2.0, invalidated_at=0.0, viewers=viewers))
    show = {"uniqueid": {"tvdb": "7"}, "title": "Some Show", "year": 2010}
    raw = {"uniqueid": {}, "title": "Pilot", "file": "/e/1.mkv", "tvshowid": 7, "season": 1, "episode": 1}
    row = LibraryRow(RowKey("L1", "episodes", 1, "/e/1.mkv"), tuple(items.tokens("episodes", raw, show)), raw, 7)
    rig.engine.library = Library("L1", [], [row], {7: show}, [], True)
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.counts["unowned"] == [1, 0] and status.unowned_titles == ["Some Show"]
    key = key_for_show(clean_ids(show["uniqueid"]), 7)
    assert key is not None
    rig.engine._memory.remember(key, ("anna",))
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.counts["viewers"] == {"kv-a": [1, 0]} and status.unowned_titles == []


def test_upload_figures_recounted_at_most_every_so_often(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine.step()
    store = rig.engine._store
    assert store is not None
    rig.lib.row("movies", 3)["playcount"] = 1
    rig.mark()
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.waiting_changes == 0
    rig.clock.now += STATUS_FIGURES_SECONDS
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.waiting_changes == 1


def _mapped(rig: "Rig", **viewer: object) -> None:
    reply = SyncReply(enabled=True, reason=None, viewers={"kv-a": ReplyViewer("default", 0)}, queue_pending=0)
    vs = replace(plan.ViewerState(), first_full_committed=True, **viewer)  # type: ignore[arg-type]
    rig.engine._state = replace(rig.engine.state, reply=reply, viewers={"kv-a": vs}, start_full_pending=False)


def test_a_full_once_a_day_while_kodi_keeps_running(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine._take_inputs()
    _mapped(rig)
    monkeypatch.setattr(rig.engine, "_act", lambda action: None)
    rig.clock.now += DAILY_FULL_SECONDS - 1
    rig.engine.step()
    assert not rig.engine.state.viewers["kv-a"].full_due
    rig.clock.now += 1
    rig.engine.step()
    assert rig.engine.state.viewers["kv-a"].full_due


def test_the_queue_is_fetched_now_and_then_without_being_asked(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.ready()
    rig.engine._take_inputs()
    _mapped(rig)
    monkeypatch.setattr(rig.engine, "_act", lambda action: None)
    rig.clock.now += QUEUE_FALLBACK_SECONDS - 1
    rig.engine.step()
    assert not rig.engine.state.viewers["kv-a"].queue_due
    rig.clock.now += 1
    rig.engine.step()
    assert rig.engine.state.viewers["kv-a"].queue_due


def test_a_mark_older_than_the_limit_is_dropped_and_counted(tmp_path):
    rig = Rig(tmp_path)
    rig.ready()
    rig.lib.row("movies", 3)["playcount"] = 1
    old = "2026-10-01T10:00:00Z"
    assert rig.inbox.add_many([InboxRecord(at=old, kind="movie", kodi_id=3, file="/m/3.mkv", playcount=1)])
    rig.engine.on_inbox()
    rig.engine.step()
    store = RecordStore(rig.paths.db)
    store.open()
    assert store.entries("kv-a") == []
    store.close()
    assert rig.engine.dropped_marks == 1 and rig.inbox.pending() == []
