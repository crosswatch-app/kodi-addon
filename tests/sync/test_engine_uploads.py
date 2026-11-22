import json
from dataclasses import replace

from resources.lib.config import Settings
from resources.lib.models import Viewer
from resources.lib.playlist_index import PlaylistIndex
from resources.lib.sync import engine as engine_mod
from resources.lib.sync.client import SyncClient
from resources.lib.sync.files import InboxRecord, read_status
from resources.lib.sync.items import RowKey
from resources.lib.sync.library_id import library_id
from resources.lib.sync.ownership import IndexSnapshot
from resources.lib.sync.record import Batch, Entry, History, ItemState, RecordStore
from resources.lib.sync.reply import PingTicket, ReplyViewer, SyncReply
from tests.sync.fake_cw import Reply, ScriptedCrossWatch, error, ok
from tests.sync.test_engine import NOW, URL, Rig


class CwRig(Rig):
    def __init__(self, tmp_path, **kw) -> None:
        super().__init__(tmp_path, **kw)
        self.cw = ScriptedCrossWatch()
        cw = self.cw
        self.engine._client_factory = lambda url, token, device, abort: SyncClient(
            url, token=token, device_id=device, abort=abort, connection_factory=cw.connection_factory
        )
        self.engine._device_id = lambda: "box"

    def store(self) -> RecordStore:
        store = self.engine._store
        assert store is not None
        return store

    def judged(self, upload_seq: int = 0) -> None:
        """A ping reply judged for kv-a, built after the gate armed."""
        reply = SyncReply(
            enabled=True, reason=None, viewers={"kv-a": ReplyViewer("default", upload_seq)}, queue_pending=0
        )
        self.engine.on_ping_reply(reply, PingTicket(sent_at=self.clock.now, epochs=dict(self.engine.epochs)))

    def run(self, steps: int = 6) -> None:
        for _ in range(steps):
            self.engine.step()

    def uploads(self) -> list[dict]:
        return [s.body for s in self.cw.sent_to("upload") if s.body is not None]


def test_first_full_goes_up_and_commits(tmp_path):
    rig = CwRig(tmp_path)
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    rig.judged()
    rig.run()
    [body] = rig.uploads()
    assert body["mode"] == "full" and body["part"] == 1 and body["parts"] == 1 and "fresh" not in body
    assert [m["movieid"] for m in body["movies"]] == [3]
    store, library = rig.store(), rig.engine.library
    assert library is not None
    assert store.first_full_committed("kv-a") and store.full_library_id("kv-a") == library.library_id
    assert store.seqs("kv-a").confirmed == body["upload_seq"]
    assert store.seen(RowKey(library.library_id, "movies", 3, "/m/3.mkv")) is not None
    vs = rig.engine.state.viewers["kv-a"]
    assert vs.first_full_committed and not vs.full_due
    # A committed full is followed by a queue fetch.
    assert rig.cw.sent_to("queue")


def test_full_goes_up_one_part_per_step(tmp_path):
    rig = CwRig(tmp_path)
    rig.lib.add_movie(4, title="Two", year=2002, uniqueid={"tmdb": "4"}, file="/m/4.mkv")
    rig.cw.script("upload", ok(complete=False), ok(complete=True))
    rig.ready()
    rig.judged()
    rig.engine.step()  # the inbox check at start
    rig.engine._state = replace(
        rig.engine.state, viewers={"kv-a": replace(rig.engine.state.viewers["kv-a"], part_rows=1)}
    )
    rig.engine.step()
    assert len(rig.uploads()) == 1 and not rig.store().first_full_committed("kv-a")
    assert rig.engine.state.viewers["kv-a"].full_in_progress
    rig.run(2)  # the inbox, re-checked after the library read, then the second part
    first, second = rig.uploads()
    assert (first["part"], second["part"], first["upload_seq"]) == (1, 2, second["upload_seq"])
    assert rig.store().first_full_committed("kv-a")


def test_503_on_a_part_resends_that_part(tmp_path):
    rig = CwRig(tmp_path)
    rig.cw.script("upload", error(503, "temporarily_unavailable", retryable=True), ok(complete=True))
    rig.ready()
    rig.judged()
    rig.run(2)
    assert len(rig.uploads()) == 1
    rig.clock.now += 60
    rig.judged(upload_seq=0)
    rig.run(3)
    first, again = rig.uploads()
    assert first == again and rig.store().first_full_committed("kv-a")


def test_last_part_without_complete_is_not_a_commit(tmp_path):
    rig = CwRig(tmp_path)
    rig.cw.script("upload", ok())
    rig.ready()
    rig.judged()
    rig.run(2)
    assert not rig.store().first_full_committed("kv-a")
    assert rig.engine.state.viewers["kv-a"].full_due


def test_record_older_than_crosswatch_goes_fresh(tmp_path):
    rig = CwRig(tmp_path)
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    rig.judged(upload_seq=9)
    rig.run(2)
    assert rig.store().fresh_pending("kv-a") is False
    [body] = rig.uploads()
    assert body["fresh"] is True and body["upload_seq"] > 9


def test_seed_viewer_goes_up_with_kodis_history(tmp_path):
    rig = CwRig(tmp_path)
    rig.lib.row("movies", 3).update(playcount=2, lastplayed="2026-11-01 20:00:00")
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    rig.store().set_seed_viewers(["kv-a"], NOW)
    rig.judged()
    rig.run(2)
    [body] = rig.uploads()
    assert body["movies"][0]["playcount"] == 2
    [entry] = rig.store().entries("kv-a")
    assert entry.origin == "seed" and entry.state.history.watched


def test_viewer_not_seeded_goes_up_empty(tmp_path):
    rig = CwRig(tmp_path)
    rig.lib.row("movies", 3).update(playcount=2, lastplayed="2026-11-01 20:00:00")
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    rig.judged()
    rig.run(2)
    assert rig.uploads()[0]["movies"][0]["playcount"] == 0


def _after_first_full(tmp_path) -> CwRig:
    rig = CwRig(tmp_path)
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    rig.judged()
    rig.run(2)
    rig.cw.sent.clear()
    return rig


def _queue_settled(rig: CwRig) -> None:
    vs = rig.engine.state.viewers["kv-a"]
    rig.engine._state = replace(rig.engine.state, viewers={"kv-a": replace(vs, queue_due=False, queue_settled=True)})


def test_person_mark_goes_up_in_a_delta(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.mark()
    rig.engine.step()
    _queue_settled(rig)
    rig.engine.step()
    [body] = rig.uploads()
    assert body["mode"] == "delta" and body["movies"][0]["playcount"] >= 1
    [entry] = rig.store().entries("kv-a")
    assert not entry.touched and entry.unsent_upload == {"history": body["upload_seq"]}
    assert not rig.engine.state.viewers["kv-a"].unsent


def test_a_delta_crosswatch_says_changed_something_waits_for_its_pair_run(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.judged(upload_seq=rig.store().seqs("kv-a").sent)
    rig.engine.step()
    assert not rig.engine.state.viewers["kv-a"].run_unknown
    rig.cw.script("upload", ok(mode="delta", complete=True, changed=True))
    rig.mark()
    rig.engine.step()
    _queue_settled(rig)
    rig.engine.step()
    assert rig.uploads()[0]["mode"] == "delta"
    assert rig.engine.state.viewers["kv-a"].run_unknown
    rig.engine._write_status()
    status = read_status(rig.paths.status)
    assert status is not None and (status.state, status.reason) == ("waiting", "crosswatch")
    rig.judged(upload_seq=rig.store().seqs("kv-a").sent)
    rig.engine.step()
    assert not rig.engine.state.viewers["kv-a"].run_unknown


def _two_bodies(monkeypatch) -> None:
    """Every delta goes up as two requests, as a large one does."""
    real = engine_mod.deltas

    def split(rows, tvshows, *, first_seq, **kw):
        first = real(rows, tvshows, first_seq=first_seq, **kw)
        return first + real(rows, tvshows, first_seq=first_seq + len(first), **kw)

    monkeypatch.setattr(engine_mod, "deltas", split)


def test_a_changed_body_counts_although_a_later_one_changed_nothing(tmp_path, monkeypatch):
    rig = _after_first_full(tmp_path)
    rig.judged(upload_seq=rig.store().seqs("kv-a").sent)
    rig.engine.step()
    _two_bodies(monkeypatch)
    rig.cw.script("upload", ok(mode="delta", complete=True, changed=True), ok(mode="delta", complete=True, changed=False))
    rig.mark()
    rig.engine.step()
    _queue_settled(rig)
    rig.engine.step()
    assert len(rig.uploads()) == 2
    assert rig.engine.state.viewers["kv-a"].run_unknown


def test_a_changed_body_counts_although_a_later_one_failed(tmp_path, monkeypatch):
    rig = _after_first_full(tmp_path)
    rig.judged(upload_seq=rig.store().seqs("kv-a").sent)
    rig.engine.step()
    _two_bodies(monkeypatch)
    rig.cw.script("upload", ok(mode="delta", complete=True, changed=True), error(503, "temporarily_unavailable"))
    rig.mark()
    rig.engine.step()
    _queue_settled(rig)
    rig.engine.step()
    assert len(rig.uploads()) == 2
    assert rig.engine.state.viewers["kv-a"].run_unknown


def test_a_delta_without_changed_starts_no_wait(tmp_path):
    """A CrossWatch without changed in its reply starts no run after a delta."""
    rig = _after_first_full(tmp_path)
    rig.judged(upload_seq=rig.store().seqs("kv-a").sent)
    rig.engine.step()
    rig.cw.script("upload", ok(mode="delta", complete=True))
    rig.mark()
    rig.engine.step()
    _queue_settled(rig)
    rig.engine.step()
    assert rig.uploads()[0]["mode"] == "delta"
    assert not rig.engine.state.viewers["kv-a"].run_unknown


def test_lost_delta_reply_resends_the_identical_body(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script("upload", Reply(lost=True), ok())
    rig.mark()
    rig.engine.step()
    _queue_settled(rig)
    rig.engine.step()
    rig.clock.now += 60
    rig.judged(upload_seq=rig.store().seqs("kv-a").sent)
    rig.engine.step()
    _queue_settled(rig)
    rig.engine.step()
    first, again = rig.uploads()
    assert first == again


def test_refused_delta_drops_its_person_changes(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script("upload", error(400, "invalid_request"))
    rig.mark()
    rig.engine.step()
    _queue_settled(rig)
    rig.engine.step()
    [entry] = rig.store().entries("kv-a")
    assert entry.unsent == {} and not entry.touched


def test_library_read_publishes_files_and_server_rows(tmp_path):
    rig = CwRig(tmp_path)
    rig.lib.add_movie(8, title="Plex", file="plugin://plugin.video.plexkodiconnect/movies/8/")
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    rig.judged()
    rig.run(2)
    assert rig.files.file("movie", 3) == "/m/3.mkv"
    assert ("movie", 8) in rig.engine.server_backed_rows


def test_seen_dropped_for_rows_gone_from_the_library(tmp_path):
    rig = CwRig(tmp_path)
    rig.ready()
    gone = RowKey(library_id(rig.kodi), "movies", 99, "/m/99.mkv")
    rig.store().commit(Batch(seen={gone: engine_mod.seen_of({"playcount": 1})}))
    rig.cw.script("upload", ok(complete=True))
    rig.judged()
    rig.run(2)
    assert rig.store().seen(gone) is None


def test_inbox_mark_waits_for_a_library_read(tmp_path):
    rig = CwRig(tmp_path)
    rig.lib.add_show(7, title="Show", uniqueid={"tvdb": "7"})
    rig.lib.add_episode(41, 7, 1, 1, file="/e/1.mkv", playcount=1)
    rig.ready()
    # No read yet: the first one's show set is no change, so no new index is waited for.
    rig.engine.library = None
    assert rig.inbox.add_many([InboxRecord(at=NOW, kind="episode", kodi_id=41, file="/e/1.mkv", playcount=1)])
    rig.engine.step()
    assert len(rig.inbox.pending()) == 1
    rig.cw.script("upload", ok(complete=True))
    rig.judged()
    rig.run(4)
    assert rig.inbox.pending() == []


def test_new_token_builds_a_new_client(tmp_path):
    rig = _after_first_full(tmp_path)
    before = rig.engine._client_cache
    rig.engine.on_settings(Settings(webhook_base_url=URL, webhook_token="new", sync_enabled=True))
    rig.engine.step()
    rig.engine._sync_client()
    assert rig.engine._client_cache is not before
    assert json.loads(json.dumps(rig.uploads())) == []


def test_merge_in_a_full_keeps_the_watch(tmp_path):
    rig = CwRig(tmp_path)
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    tokens = sorted(rig.library().movies[0].tokens)
    watched = ItemState(history=History(watched=True, playcount=1, lastplayed=NOW))
    rig.store().commit(Batch(entries={"kv-a": [
        Entry(key="b", tokens=frozenset(tokens), meta={}, state=watched),
        Entry(key="c", tokens=frozenset(tokens[:1]), meta={}, state=ItemState()),
    ]}))  # fmt: skip
    rig.judged()
    rig.run(2)
    [entry] = rig.store().entries("kv-a")
    assert entry.key == "b" and entry.state.history.watched


def test_mark_drained_between_parts_survives_the_commit(tmp_path):
    rig = CwRig(tmp_path)
    rig.lib.add_movie(4, title="Two", year=2002, uniqueid={"tmdb": "4"}, file="/m/4.mkv")
    tokens = frozenset(rig.library().movies[0].tokens)
    rig.cw.script("upload", ok(complete=False), ok(complete=True))
    rig.ready()
    rig.store().commit(Batch(entries={"kv-a": [Entry(key="k", tokens=tokens, meta={}, state=ItemState())]}))
    rig.judged()
    rig.engine.step()
    rig.engine._state = replace(
        rig.engine.state, viewers={"kv-a": replace(rig.engine.state.viewers["kv-a"], part_rows=1)}
    )
    rig.engine.step()  # part 1
    rig.mark()
    rig.run(3)  # the drained mark, then part 2 and the commit
    entry = next(e for e in rig.store().entries("kv-a") if e.key == "k")
    assert entry.state.history.watched and entry.touched and entry.unsent


def test_full_without_complete_goes_again_as_a_new_full(tmp_path):
    rig = CwRig(tmp_path)
    rig.cw.script("upload", ok(), ok(complete=True))
    rig.ready()
    rig.judged()
    rig.run(2)
    rig.clock.now += 60
    rig.judged(upload_seq=rig.store().seqs("kv-a").sent)
    rig.run(3)
    first, second = rig.uploads()
    assert second["upload_seq"] > first["upload_seq"] and second["upload_id"] != first["upload_id"]
    assert rig.store().first_full_committed("kv-a")


def test_stale_delta_goes_again_under_a_new_seq(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.cw.script("upload", error(409, "upload_stale"), ok())
    rig.mark()
    rig.engine.step()
    _queue_settled(rig)
    rig.engine.step()
    rig.clock.now += 60
    rig.judged(upload_seq=rig.store().seqs("kv-a").sent)
    rig.engine.step()
    _queue_settled(rig)
    rig.engine.step()
    first, again = rig.uploads()
    assert again["upload_seq"] > first["upload_seq"]


def test_delta_already_confirmed_settles_without_resending(tmp_path, monkeypatch):
    rig = _after_first_full(tmp_path)
    rig.mark()
    rig.engine.step()
    _queue_settled(rig)
    store = rig.store()
    real = store.commit
    calls = {"n": 0}

    def fail_carried_once(batch):
        if batch.unsent_carried and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("disk full")
        return real(batch)

    monkeypatch.setattr(store, "commit", fail_carried_once)
    rig.engine.step()
    _queue_settled(rig)
    assert rig.engine.step() is None
    assert len(rig.uploads()) == 1
    [entry] = store.entries("kv-a")
    assert not entry.touched


def test_first_full_before_the_queue_keeps_person_changes_touched(tmp_path):
    rig = CwRig(tmp_path)
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    tokens = frozenset(rig.library().movies[0].tokens)
    watched = ItemState(history=History(watched=True, playcount=1, lastplayed=NOW))
    rig.store().commit(Batch(entries={"kv-a": [
        Entry(key="k", tokens=tokens, meta={}, state=watched, unsent={"history": NOW}, touched=True)
    ]}))  # fmt: skip
    rig.judged()
    rig.run(2)
    [entry] = rig.store().entries("kv-a")
    assert entry.touched and entry.unsent_upload == {}


def test_full_writes_seen_only_for_its_own_rows(tmp_path):
    rig = CwRig(tmp_path)
    both = (Viewer(name="anna", sync_id="kv-a"), Viewer(name="ben", sync_id="kv-b"))
    rig.viewers.save(list(both))
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    # Two viewers and no playlist naming either: kv-a owns no row.
    rig.engine.on_snapshot(IndexSnapshot(index=PlaylistIndex(), built_at=1.0, invalidated_at=0.0, viewers=both))
    rig.judged()
    rig.run(2)
    assert rig.store().seen(RowKey(library_id(rig.kodi), "movies", 3, "/m/3.mkv")) is None
