import sqlite3
import threading
from dataclasses import replace
from typing import Any

import pytest

from resources.lib.sync import record
from resources.lib.sync.items import RowKey
from resources.lib.sync.record import (
    Acked,
    Assignment,
    Batch,
    Carried,
    Entry,
    Group,
    History,
    ItemState,
    PendingWrite,
    Rating,
    RecordMissing,
    RecordStore,
    Seen,
)

ROW = RowKey("lib1", "movies", 1, "smb://nas/a.mkv")


def _store(tmp_path, create: bool = True) -> RecordStore:
    store = RecordStore(str(tmp_path / "sync.db"))
    store.open(create=create)
    return store


def _entry(key: str = "movies|imdb:tt1", tokens=("movies|imdb:tt1",), **kw: Any) -> Entry:
    kw.setdefault("state", ItemState())
    return Entry(key=key, tokens=frozenset(tokens), meta={"title": "A"}, **kw)


def _intent(wid: str, tokens=("movies|imdb:tt1",), group: Group = "ratings") -> PendingWrite:
    return PendingWrite(id=wid, row=ROW, tokens=frozenset(tokens), group=group, created="2026-11-19T10:00:00Z")


def test_missing_file_raises_record_missing(tmp_path) -> None:
    with pytest.raises(RecordMissing):
        _store(tmp_path, create=False)


def test_unreadable_or_foreign_file_raises_record_missing(tmp_path) -> None:
    (tmp_path / "sync.db").write_text("not a database")
    with pytest.raises(RecordMissing):
        _store(tmp_path, create=False)
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE x (y)").connection.close()
    with pytest.raises(RecordMissing):
        RecordStore(str(other)).open()


def test_reopen_keeps_everything(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(Batch(entries={"kv-1": [_entry()]}))
    store.close()
    again = _store(tmp_path, create=False)
    assert again.entries("kv-1") == [_entry()]


def test_closed_store_reads_raise(tmp_path) -> None:
    store = _store(tmp_path)
    store.close()
    with pytest.raises(RecordMissing):
        store.entries("kv-1")


def test_batch_is_atomic(tmp_path) -> None:
    store = _store(tmp_path)
    bad = Batch(entries={"kv-1": [_entry()]}, acks_add={"kv-1": [{"no_seq": True}]})
    with pytest.raises(KeyError):
        store.commit(bad)
    assert store.entries("kv-1") == []


def test_match_by_any_shared_token(tmp_path) -> None:
    store = _store(tmp_path)
    a = _entry("a", ("t1", "t2"))
    b = _entry("b", ("t3",))
    store.commit(Batch(entries={"kv-1": [a, b]}))
    assert store.match("kv-1", ["t2", "t9"]) == [a]
    assert store.match("kv-1", ["t1", "t3"]) == [a, b]
    assert store.match("kv-1", []) == []
    # Tokens follow the entry when it is rewritten.
    store.commit(Batch(entries={"kv-1": [replace(a, tokens=frozenset({"t4"}))]}))
    assert store.match("kv-1", ["t1"]) == []


def test_everything_keyed_by_sync_id(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(Batch(entries={"kv-1": [_entry()]}, applied_seq={"kv-1": 5}))
    assert store.entries("kv-2") == [] and store.match("kv-2", ["movies|imdb:tt1"]) == []
    assert store.applied_seq("kv-2") == 0 and store.applied_seq("kv-1") == 5


def test_entry_round_trips_every_field(tmp_path) -> None:
    store = _store(tmp_path)
    state = ItemState(history=History(watched=True, playcount=2, lastplayed="2026-01-01T10:00:00Z", event=4, src=4),
                      rating=Rating(value=8, from_cw=True, src=3))  # fmt: skip
    full = _entry(state=state, unsent={"ratings": "2026-11-19T10:00:00Z"}, unsent_upload={"history": 7},
                  touched=True, uploaded_at="2026-11-19T09:00:00Z", origin="seed", seed_rating=6, change_count=3)  # fmt: skip
    store.commit(Batch(entries={"kv-1": [full]}))
    assert store.entry("kv-1", full.key) == full


def test_seen_keyed_per_row(tmp_path) -> None:
    store = _store(tmp_path)
    other = replace(ROW, kodi_id=2)
    store.commit(Batch(seen={ROW: Seen(playcount=1), other: Seen(userrating=7)}))
    assert store.seen(ROW) == Seen(playcount=1)
    assert store.seen_for_library("lib1") == {ROW: Seen(playcount=1), other: Seen(userrating=7)}
    store.commit(Batch(seen={ROW: None}))
    assert store.seen(ROW) is None


def test_seen_for_tokens_survives_row_removal(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(Batch(seen={ROW: Seen(playcount=2, lastplayed="2026-01-01T10:00:00Z")},
                       seen_tokens={ROW: ("movies|imdb:tt1",)}))  # fmt: skip
    store.commit(Batch(seen={ROW: None}))
    assert store.seen_for_tokens(["movies|imdb:tt1"], "smb://nas/other.mkv") == Seen(
        playcount=2, lastplayed="2026-01-01T10:00:00Z"
    )
    assert store.seen_for_tokens([], ROW.file) is not None
    assert store.seen_for_tokens(["x"], "y") is None


def test_seen_for_tokens_prefers_the_watched_copy_of_one_read(tmp_path) -> None:
    for order in (0, 1):
        store = RecordStore(str(tmp_path / f"sync{order}.db"))
        store.open(create=True)
        copy = replace(ROW, kodi_id=9, file="smb://nas/a-4k.mkv")
        rows = [(ROW, Seen(playcount=0)), (copy, Seen(playcount=1))]
        if order:
            rows.reverse()
        store.commit(Batch(seen=dict(rows), seen_tokens={ROW: ("t",), copy: ("t",)}))
        found = store.seen_for_tokens(["t"], "")
        assert found is not None and found.playcount == 1


def test_seen_for_tokens_newest_wins_so_an_unwatch_stays(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(Batch(seen={ROW: Seen(playcount=2)}, seen_tokens={ROW: ("t",)}))
    store.commit(Batch(seen={ROW: Seen(playcount=0)}, seen_tokens={ROW: ("t",)}))
    other = replace(ROW, kodi_id=5, file="smb://nas/b.mkv")
    store.commit(Batch(seen={other: Seen(playcount=3)}, seen_tokens={other: ("u",)}))
    found = store.seen_for_tokens(["t"], "smb://nas/new.mkv")
    assert found is not None and found.playcount == 0


def test_seen_for_tokens_same_file_beats_a_token(tmp_path) -> None:
    store = _store(tmp_path)
    other = replace(ROW, kodi_id=5, file="smb://nas/b.mkv")
    store.commit(Batch(seen={ROW: Seen(playcount=1)}, seen_tokens={ROW: ("t",)}))
    store.commit(Batch(seen={other: Seen(playcount=7)}, seen_tokens={other: ("t",)}))
    found = store.seen_for_tokens(["t"], ROW.file)
    assert found is not None and found.playcount == 1


def test_episode_files_are_not_catch_up_keys(tmp_path) -> None:
    # One file, two episodes: the first episode's state must not catch up the second.
    store = _store(tmp_path)
    ep1 = RowKey("lib1", "episodes", 10, "smb://nas/s01e01-e02.mkv")
    store.commit(Batch(seen={ep1: Seen(playcount=3)}, seen_tokens={ep1: ("episodes|show:tvdb:1|1|1",)}))
    assert store.seen_for_tokens(["episodes|show:tvdb:1|1|2"], ep1.file) is None


def test_newer_intent_supersedes_older(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(Batch(pending_add=[_intent("w1"), _intent("h1", group="history")]))
    store.commit(Batch(pending_add=[_intent("w2", tokens=("movies|imdb:tt1", "movies|tmdb:2"))]))
    assert [w.id for w in store.pending_writes()] == ["h1", "w2"]
    store.commit(Batch(pending_add=[_intent("w3"), _intent("w4")]))
    assert [w.id for w in store.pending_writes()] == ["h1", "w4"]


def test_a_superseding_intent_keeps_the_in_flight_write(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(Batch(pending_add=[_intent("w1")]))
    store.commit(Batch(in_flight={"w1": {"userrating": 8}}))
    store.commit(Batch(pending_add=[_intent("w2")]))
    assert [(w.id, w.in_flight) for w in store.pending_writes()] == [("w2", {"userrating": 8})]


def test_pending_write_round_trip_and_in_flight(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(Batch(pending_add=[replace(_intent("w1"), fetched_at="2026-11-19T09:00:00Z")]))
    store.commit(Batch(in_flight={"w1": {"userrating": 8}, "gone": {"x": 1}}))
    [write] = store.pending_writes()
    assert write.in_flight == {"userrating": 8} and write.fetched_at == "2026-11-19T09:00:00Z"
    assert write.row == ROW
    store.commit(Batch(pending_done=["w1"]))
    assert store.pending_writes() == []


def test_pending_assignments_round_trip(tmp_path) -> None:
    store = _store(tmp_path)
    a = Assignment(id="a1", entry_key="k", tokens=frozenset({"t"}), group="ratings",
                   value=ItemState(rating=Rating(value=9)), title="A", at="2026-11-19T10:00:00Z")  # fmt: skip
    store.commit(Batch(assignments_add=[a]))
    assert store.assignments() == [a]
    store.commit(Batch(assignments_done=["a1"]))
    assert store.assignments() == []


def test_upload_seqs_reserved_before_send_and_confirmed_after(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.reserve_upload_seqs("kv-1", floor=0, count=3) == [1, 2, 3]
    assert store.seqs("kv-1").sent == 3 and store.seqs("kv-1").confirmed == 0
    store.confirm_upload_seq("kv-1", 2)
    assert store.seqs("kv-1").confirmed == 2
    # A crash after reserving never reuses a seq.
    store.close()
    again = _store(tmp_path, create=False)
    assert again.reserve_upload_seqs("kv-1", floor=0, count=1) == [4]


def test_upload_seq_continues_above_floor(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.reserve_upload_seqs("kv-1", floor=41, count=1) == [42]
    assert store.reserve_upload_seqs("kv-1", floor=10, count=1) == [43]


def test_seq_state_never_lowered(tmp_path) -> None:
    store = _store(tmp_path)
    store.reserve_upload_seqs("kv-1", floor=0, count=5)
    store.confirm_upload_seq("kv-1", 5)
    store.confirm_upload_seq("kv-1", 2)
    assert store.seqs("kv-1").confirmed == 5
    store.reset_for_cw("kv-1", cw_seq=2)
    assert store.seqs("kv-1").sent == 5 and store.seqs("kv-1").confirmed == 2


def test_reserving_nothing_is_refused(tmp_path) -> None:
    store = _store(tmp_path)
    for count in (0, -2):
        with pytest.raises(ValueError):
            store.reserve_upload_seqs("kv-1", floor=0, count=count)
    assert store.seqs("kv-1").sent == 0


def test_upload_ended_raises_epoch(tmp_path) -> None:
    store = _store(tmp_path)
    store.reserve_upload_seqs("kv-1", floor=0, count=1)
    first = store.seqs("kv-1").epoch
    store.upload_ended("kv-1")
    assert store.seqs("kv-1").epoch == first + 1


def test_applied_seq_never_goes_down(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(Batch(applied_seq={"kv-1": 120}))
    store.commit(Batch(applied_seq={"kv-1": 50}))
    assert store.applied_seq("kv-1") == 120


def test_reset_for_cw_clears_applied_keeps_acked_sent_and_first_full(tmp_path) -> None:
    store = _store(tmp_path)
    carried = _entry(unsent={"ratings": "t"}, unsent_upload={"ratings": 9})
    store.reserve_upload_seqs("kv-1", floor=0, count=10)
    store.commit(Batch(entries={"kv-1": [carried]}, applied_seq={"kv-1": 9}, full_library_id={"kv-1": "lib1"},
                       first_full_done={"kv-1"}, acks_add={"kv-1": [{"seq": 9, "change_id": "c9", "status": "applied"}]},
                       acked_add={"kv-1": [Acked(9, "c9", carried.key, "ratings")]}))  # fmt: skip
    store.set_settled("kv-1")
    store.reset_for_cw("kv-1", cw_seq=3)
    assert store.applied_seq("kv-1") == 0 and store.full_library_id("kv-1") == ""
    assert store.acks_pending("kv-1") == []
    assert store.acked("kv-1", 9, "c9") == [Acked(9, "c9", carried.key, "ratings")]
    assert store.first_full_committed("kv-1") and store.settled("kv-1")
    entry = store.entry("kv-1", carried.key)
    assert entry is not None and entry.unsent == {"ratings": "t"} and entry.unsent_upload == {}


def test_acked_is_found_by_seq_and_change_id(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(Batch(acked_add={"kv-1": [Acked(7, "c7", "k1", "history"), Acked(7, "c7", "k1", "ratings")]}))
    assert [(a.entry_key, a.group) for a in store.acked("kv-1", 7, "c7")] == [("k1", "history"), ("k1", "ratings")]
    assert store.acked("kv-1", 8, "c7") == []


def test_same_seq_with_another_change_id_is_another_change(tmp_path) -> None:
    # A restored CrossWatch can hand out a seq again; the change id tells the changes apart.
    store = _store(tmp_path)
    store.commit(Batch(acked_add={"kv-1": [Acked(5, "old", "itemY", "ratings")]}))
    assert store.acked("kv-1", 5, "new") == []
    store.commit(Batch(acked_add={"kv-1": [Acked(5, "new", "itemZ", "ratings")]}))
    assert [a.entry_key for a in store.acked("kv-1", 5, "old")] == ["itemY"]
    assert [a.entry_key for a in store.acked("kv-1", 5, "new")] == ["itemZ"]


def test_alias_resolves_merged_key(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(Batch(entries={"kv-1": [_entry("a", ("t1",)), _entry("b", ("t2",))]},
                       acked_add={"kv-1": [Acked(5, "c5", "a", "ratings")]}))  # fmt: skip
    survivor = _entry("b", ("t1", "t2"))
    store.commit(Batch(entries={"kv-1": [survivor]}, merged={"kv-1": {"b": ["a", "b"]}}))
    assert store.entry("kv-1", "a") == survivor
    [found] = store.acked("kv-1", 5, "c5")
    assert found.entry_key == "b"
    # A later merge moves the old alias along.
    store.commit(Batch(entries={"kv-1": [_entry("c", ("t1", "t2", "t3"))]}, merged={"kv-1": {"c": ["b"]}}))
    [found] = store.acked("kv-1", 5, "c5")
    assert found.entry_key == "c"
    assert [e.key for e in store.entries("kv-1")] == ["c"]
    # An entry written again under an aliased key is that entry again, not the alias.
    store.commit(Batch(entries={"kv-1": [_entry("a", ("t9",))]}))
    assert store.entry("kv-1", "a") == _entry("a", ("t9",))
    [found] = store.acked("kv-1", 5, "c5")
    assert found.entry_key == "a"


def test_acked_seqs_bounded(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(record, "ACKED_SEQS_KEPT", 3)
    store = _store(tmp_path)
    store.commit(Batch(acked_add={"kv-1": [Acked(n, f"c{n}", "k", "ratings") for n in range(1, 6)]}))
    assert [n for n in range(1, 6) if store.acked("kv-1", n, f"c{n}")] == [3, 4, 5]


def test_acks_added_and_done(tmp_path) -> None:
    store = _store(tmp_path)
    acks = [{"seq": 2, "change_id": "a", "status": "applied"}, {"seq": 3, "change_id": "b", "status": "failed"}]
    store.commit(Batch(acks_add={"kv-1": acks}))
    assert store.acks_pending("kv-1") == acks
    # Done is per change: the same seq with another change id stays pending.
    store.commit(Batch(acks_done={"kv-1": [(2, "x"), (3, "b")]}))
    assert store.acks_pending("kv-1") == acks[:1]


def test_change_count_guards_commit(tmp_path) -> None:
    store = _store(tmp_path)
    e = _entry(unsent={"ratings": "t1"}, change_count=5)
    store.commit(Batch(entries={"kv-1": [e]}))
    store.commit(Batch(unsent_carried={"kv-1": {e.key: Carried(4, {"ratings": 11})}}))
    current = store.entry("kv-1", e.key)
    assert current is not None and current.unsent_upload == {}


def test_applied_change_cleared_when_its_seq_was_carried(tmp_path) -> None:
    store = _store(tmp_path)
    e = _entry(applied_change={"history": 10, "ratings": 12}, change_count=3)
    store.commit(Batch(entries={"kv-1": [e]}))
    # The entry changed since the upload was built (another group): the carried seq still clears.
    store.commit(Batch(entries={"kv-1": [replace(e, change_count=4)]}))
    store.commit(Batch(unsent_carried={"kv-1": {e.key: Carried(3, {}, applied={"history": 10, "ratings": 11})}}))
    current = store.entry("kv-1", e.key)
    # ratings was applied again (12) after the upload was built (11), so it stays.
    assert current is not None and current.applied_change == {"ratings": 12}


def test_unwatched_kept_until_settled(tmp_path) -> None:
    store = _store(tmp_path)
    state = ItemState(history=History(watched=False, explicit=True, unwatched=True))
    e = _entry(state=state, unsent={"history": "t1"})
    store.commit(Batch(entries={"kv-1": [e]}))
    store.commit(Batch(unsent_carried={"kv-1": {e.key: Carried(0, {"history": 3})}}))
    carried = store.entry("kv-1", e.key)
    assert carried is not None and carried.state.history.unwatched
    store.commit(Batch(unsent_cleared={"kv-1": {e.key: ["history"]}}))
    settled = store.entry("kv-1", e.key)
    assert settled is not None and not settled.state.history.unwatched and settled.state.history.explicit


def test_unsent_carried_then_cleared(tmp_path) -> None:
    store = _store(tmp_path)
    e = _entry(unsent={"ratings": "t1", "history": "t2"})
    store.commit(Batch(entries={"kv-1": [e]}))
    store.commit(Batch(unsent_carried={"kv-1": {e.key: Carried(0, {"ratings": 11, "progress": 11})}}))
    carried = store.entry("kv-1", e.key)
    assert carried is not None and carried.unsent_upload == {"ratings": 11}
    store.commit(Batch(unsent_cleared={"kv-1": {e.key: ["ratings"], "gone": ["history"]}}))
    cleared = store.entry("kv-1", e.key)
    assert cleared is not None and cleared.unsent == {"history": "t2"} and cleared.unsent_upload == {}


def test_removed_entries(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(Batch(entries={"kv-1": [_entry("a", ("t1",))]}))
    store.commit(Batch(removed_entries={"kv-1": ["a"]}))
    assert store.entries("kv-1") == [] and store.match("kv-1", ["t1"]) == []


def test_settled_first_mapped_and_last_delta_persisted(tmp_path) -> None:
    store = _store(tmp_path)
    store.set_settled("kv-1")
    store.set_first_mapped_at("kv-1", "2026-11-19T10:00:00Z")
    store.set_first_mapped_at("kv-1", "2026-11-20T10:00:00Z")
    store.set_last_delta("kv-1", [(7, b"\x1f\x8b body")])
    store.close()
    again = _store(tmp_path, create=False)
    assert again.settled("kv-1") and not again.settled("kv-2")
    assert again.first_mapped_at("kv-1") == "2026-11-19T10:00:00Z"
    assert again.last_delta("kv-1") == [(7, b"\x1f\x8b body")]


def test_seed_viewers_written_once(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.seed_viewers() == frozenset() and store.seed_started_at() == ""
    assert store.set_seed_viewers(["kv-2", "kv-1"], "2026-11-19T10:00:00Z")
    assert not store.set_seed_viewers(["kv-3"], "2026-11-20T10:00:00Z")
    assert store.seed_viewers() == frozenset({"kv-1", "kv-2"})
    assert store.seed_started_at() == "2026-11-19T10:00:00Z"


def test_a_second_connection_waits_for_a_held_lock(tmp_path) -> None:
    store = _store(tmp_path)
    other = RecordStore(str(tmp_path / "sync.db"), busy_timeout=5)
    other.open()
    holder = sqlite3.connect(str(tmp_path / "sync.db"), isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.3, lambda: holder.execute("ROLLBACK"))
    release.start()
    try:
        assert other.set_seed_viewers(["kv-1"], "t")
    finally:
        release.join()
        holder.close()
    assert store.seed_viewers() == frozenset({"kv-1"})


def test_a_held_lock_past_the_timeout_is_busy_not_missing(tmp_path) -> None:
    _store(tmp_path)
    holder = sqlite3.connect(str(tmp_path / "sync.db"), isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        busy = RecordStore(str(tmp_path / "sync.db"), busy_timeout=0.05)
        with pytest.raises(sqlite3.OperationalError):
            busy.open()
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_open_without_create_never_makes_a_file(tmp_path) -> None:
    with pytest.raises(RecordMissing):
        RecordStore(str(tmp_path / "sync.db")).open()
    assert not (tmp_path / "sync.db").exists()


@pytest.mark.parametrize("noted_key", ["a", "b"])
def test_merge_keeps_a_local_unwatch_noted_on_any_entry(noted_key) -> None:
    watched = History(watched=True, playcount=1, lastplayed="2026-11-20T09:00:00Z")
    noted = replace(watched, local_unwatch=True, local_unwatch_seq=41)
    one = _entry("a", state=ItemState(history=noted if noted_key == "a" else watched))
    two = _entry("b", state=ItemState(history=noted if noted_key == "b" else watched))
    history = record.merge([one, two]).state.history
    assert history.watched and history.local_unwatch and history.local_unwatch_seq == 41


@pytest.mark.parametrize(("after", "kept"), [(40, True), (41, False)])
def test_merge_drops_the_note_for_a_crosswatch_watch_made_after_it(after, kept) -> None:
    noted = History(watched=True, playcount=1, local_unwatch=True, local_unwatch_seq=41)
    cw = History(watched=True, playcount=2, lastplayed="2026-11-20T12:00:00Z")
    one = _entry("a", state=ItemState(history=noted))
    two = _entry("b", state=ItemState(history=cw), applied_change={"history": 900}, applied_after={"history": after})
    assert record.merge([one, two]).state.history.local_unwatch is kept


def test_assignment_keeps_its_meta(tmp_path) -> None:
    store = _store(tmp_path)
    a = Assignment(id="a1", entry_key="k", tokens=frozenset({"t"}), group="history", value=ItemState(),
                   title="A", at="2026-11-20T10:00:00Z", meta={"file": "smb://nas/a.mkv"})  # fmt: skip
    store.commit(Batch(assignments_add=[a]))
    assert store.assignments()[0].meta == {"file": "smb://nas/a.mkv"}


def test_accepted_upload_clears_touched_on_unchanged_entries(tmp_path) -> None:
    store = _store(tmp_path)
    store.commit(
        Batch(
            entries={
                "v": [
                    _entry("a", change_count=2, touched=True),
                    _entry("b", tokens=("b",), change_count=5, touched=True),
                ]
            }
        )
    )
    carried = {"a": Carried(change_count=2, groups={}), "b": Carried(change_count=4, groups={})}
    store.commit(Batch(unsent_carried={"v": carried}, uploaded_at="2026-11-20T10:00:00Z"))
    a, b = store.entry("v", "a"), store.entry("v", "b")
    assert a is not None and b is not None
    assert not a.touched and a.uploaded_at == "2026-11-20T10:00:00Z"
    # Changed after the upload was built: still to go up.
    assert b.touched and b.uploaded_at == ""
