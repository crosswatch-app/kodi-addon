import itertools
from dataclasses import replace
from typing import Any

import pytest

from resources.lib.sync import items
from resources.lib.sync.items import LibraryRow, RowKey
from resources.lib.sync.manual import Ignored, Recorded, ToAssign, apply_assignment, watched_change
from resources.lib.sync.record import Batch, Entry, History, ItemState, RecordStore, fresh_state
from resources.lib.sync.settings import SyncSettings

AT = "2026-11-20T10:00:00Z"
LAST = "2026-11-20T09:58:00Z"
SHOW = {"tvshowid": 7, "uniqueid": {"tvdb": "81189"}, "title": "Breaking Bad", "year": 2008}
OFF, ON = SyncSettings(), SyncSettings(send_removals=True)


def _movie(**raw: Any) -> LibraryRow:
    body = {"movieid": 3, "uniqueid": {"tmdb": "550"}, "title": "Fight Club", "year": 1999, "file": "smb://nas/fc.mkv",
            "playcount": 1, "lastplayed": LAST, **raw}  # fmt: skip
    return LibraryRow(RowKey("lib1", "movies", 3, body["file"]), tuple(items.tokens("movies", body)), body, None)


def _episode() -> LibraryRow:
    body = {"episodeid": 9, "tvshowid": 7, "uniqueid": {}, "title": "Pilot", "showtitle": "Breaking Bad",
            "season": 1, "episode": 1, "file": "smb://nas/e1.mkv", "playcount": 1, "lastplayed": LAST}  # fmt: skip
    return LibraryRow(RowKey("lib1", "episodes", 9, body["file"]), tuple(items.tokens("episodes", body, SHOW)), body, 7)


def _entry(history: History | None = None, **fields: Any) -> Entry:
    row = _movie()
    return Entry(key=items.identity("movies", row.raw), tokens=frozenset(row.tokens), meta={"title": "Fight Club"},
                 state=ItemState(history=history or History()), **fields)  # fmt: skip


def _marks(entries, playcount: int = 1, owners=("kv-a",), settings=OFF, row=None, show=None):
    ids = itertools.count(1)
    return watched_change(entries, row or _movie(playcount=playcount), show, playcount=playcount, lastplayed=LAST,
                          owners=owners, settings=settings, made_at=AT, next_upload_seq=41,
                          new_id=lambda: f"a-{next(ids)}")  # fmt: skip


def _mark(entry: Entry | None, playcount: int = 1, owners=("kv-a",), settings=OFF, row=None, show=None):
    """The single outcome of a mark; entry is the first owner's."""
    out = _marks({owners[0]: entry} if owners and entry is not None else {}, playcount, owners, settings, row, show)
    assert len(out) == 1, out
    return out[0]


def _assign(assignment, viewers, store, settings=ON):
    return apply_assignment(assignment, viewers, store, settings, next_upload_seq=77)


def test_manual_watched_single_owner_recorded_max_count():
    # Unwatched in the record after plays on the services: the count they had is kept.
    out = _mark(_entry(History(watched=False, playcount=3)), playcount=1)
    assert isinstance(out, Recorded) and out.sent and out.viewer == "kv-a"
    history = out.entry.state.history
    assert (history.watched, history.playcount, history.lastplayed) == (True, 3, LAST)
    out = _mark(_entry(), playcount=2)
    assert isinstance(out, Recorded) and out.entry.state.history.playcount == 2


def test_unsent_stamped_with_inbox_time():
    out = _mark(_entry(unsent_upload={"history": 12, "ratings": 9}))
    assert isinstance(out, Recorded)
    assert out.entry.unsent == {"history": AT}
    # A new change waits for a new upload; the rating's carrying upload stays.
    assert out.entry.unsent_upload == {"ratings": 9}
    assert out.entry.touched and out.entry.change_count == 1


def test_manual_watched_on_watched_entry_keeps_history():
    out = _mark(_entry(History(watched=True, playcount=2, lastplayed="2026-01-01T00:00:00Z")), playcount=3)
    assert out == Ignored("already_watched")


def test_mark_on_item_without_entry_creates_it():
    out = _mark(None, row=_episode(), show=SHOW)
    assert isinstance(out, Recorded) and out.sent
    assert out.entry.origin == "person" and out.entry.meta["showtitle"] == "Breaking Bad"
    assert out.entry.key == items.identity("episodes", _episode().raw, SHOW)


def test_mark_without_owner_ignored():
    assert _mark(_entry(), owners=()) == Ignored("no_owner")


@pytest.mark.parametrize("settings", [OFF, ON])
def test_manual_unwatch_needs_setting(settings):
    watched = _entry(History(watched=True, playcount=2, lastplayed=LAST))
    out = _mark(watched, playcount=0, settings=settings)
    assert isinstance(out, Recorded)
    history = out.entry.state.history
    if settings.send_removals:
        assert out.sent and (history.watched, history.explicit, history.unwatched) == (False, True, True)
        assert out.entry.unsent == {"history": AT}
    else:
        # Kept from CrossWatch: the record keeps the watch and notes the unwatch.
        assert not out.sent and history.watched and history.local_unwatch and history.local_unwatch_seq == 41
        assert out.entry.unsent == {} and not out.entry.touched and out.entry.change_count == 1


@pytest.mark.parametrize("settings", [OFF, ON])
def test_unwatch_of_unwatched_entry_ignored(settings):
    assert _mark(_entry(), playcount=0, settings=settings) == Ignored("not_watched")


def test_second_local_unwatch_ignored():
    entry = _entry(History(watched=True, playcount=1, local_unwatch=True, local_unwatch_seq=41))
    assert _mark(entry, playcount=0) == Ignored("already_unwatched")


def test_rewatch_after_local_unwatch_lifts_the_note_only():
    entry = _entry(History(watched=True, playcount=2, lastplayed=LAST, local_unwatch=True, local_unwatch_seq=41))
    out = _mark(entry, playcount=1)
    assert isinstance(out, Recorded) and not out.sent
    history = out.entry.state.history
    assert history.watched and not history.local_unwatch and history.local_unwatch_seq == 0 and history.playcount == 2


def test_shared_mark_goes_to_assignment_list():
    out = _mark(None, owners=("kv-a", "kv-b"), row=_episode(), show=SHOW)
    assert isinstance(out, ToAssign)
    a = out.assignment
    assert a.id == "a-1" and a.group == "history" and a.at == AT and a.title == "Breaking Bad S01E01"
    assert a.value.history.watched and a.value.history.lastplayed == LAST
    assert a.entry_key == items.identity("episodes", _episode().raw, SHOW) and a.tokens == frozenset(_episode().tokens)


def test_shared_unwatch_needs_setting():
    assert _mark(None, playcount=0, owners=("kv-a", "kv-b")) == Ignored("removals_off")
    out = _mark(None, playcount=0, owners=("kv-a", "kv-b"), settings=ON)
    assert isinstance(out, ToAssign) and out.assignment.value.history.unwatched
    assert out.assignment.title == "Fight Club (1999)" and out.assignment.meta["file"] == "smb://nas/fc.mkv"


@pytest.mark.parametrize("settings", [OFF, ON])
def test_shared_unwatch_noted_for_every_watched_owner(settings):
    """Kodi keeps the person's unwatch while nobody has said whose it was, and for good with
    removals off: without the notes the writer would raise Kodi again on the next change."""
    watched = _entry(History(watched=True, playcount=1, lastplayed=LAST))
    out = _marks({"kv-a": watched, "kv-b": _entry()}, playcount=0, owners=("kv-a", "kv-b", "kv-a"), settings=settings)
    notes = [o for o in out if isinstance(o, Recorded)]
    assert [(o.viewer, o.sent) for o in notes] == [("kv-a", False)]
    history = notes[0].entry.state.history
    assert history.watched and history.local_unwatch and history.local_unwatch_seq == 41
    assert notes[0].entry.change_count == 1
    assert any(isinstance(o, ToAssign) for o in out) is settings.send_removals


def test_shared_unwatch_after_assignment_to_one_keeps_the_others_note(store):
    watched = History(watched=True, playcount=1, lastplayed=LAST)
    a, b = replace(_entry(watched), key="ka"), replace(_entry(watched), key="kb")
    out = _marks({"kv-a": a, "kv-b": b}, playcount=0, owners=("kv-a", "kv-b"), settings=ON)
    store.commit(Batch(entries={o.viewer: [o.entry] for o in out if isinstance(o, Recorded)}))
    assignment = next(o.assignment for o in out if isinstance(o, ToAssign))
    store.commit(_assign(assignment, ["kv-a"], store))
    history_a = store.entries("kv-a")[0].state.history
    history_b = store.entries("kv-b")[0].state.history
    assert (history_a.watched, history_a.unwatched, history_a.local_unwatch) == (False, True, False)
    assert history_b.watched and history_b.local_unwatch


@pytest.fixture
def store(tmp_path) -> RecordStore:
    s = RecordStore(str(tmp_path / "sync.db"))
    s.open(create=True)
    return s


def test_apply_assignment(store):
    out = _mark(None, owners=("kv-a", "kv-b"), playcount=4)
    assert isinstance(out, ToAssign)
    store.commit(Batch(assignments_add=[out.assignment]))
    # kv-b already has the item, split over two entries that share a token.
    row = _movie()
    one = Entry(key="b-1", tokens=frozenset(row.tokens), meta={}, state=ItemState(history=History(playcount=6)))
    two = Entry(key="b-2", tokens=frozenset([row.tokens[0]]), meta={}, state=fresh_state())
    store.commit(Batch(entries={"kv-b": [one, two]}))

    batch = _assign(out.assignment, ["kv-a", "kv-b", "kv-b"], store)
    store.commit(batch)
    assert store.assignments() == []
    a = store.entries("kv-a")
    assert len(a) == 1 and a[0].key == out.assignment.entry_key and a[0].origin == "person"
    assert a[0].state.history.watched and a[0].state.history.playcount == 4 and a[0].unsent == {"history": AT}
    b = store.entries("kv-b")
    assert [e.key for e in b] == ["b-1"]
    assert b[0].state.history.playcount == 6 and b[0].unsent == {"history": AT} and b[0].touched


def test_apply_assignment_to_nobody_drops_it(store):
    out = _mark(None, owners=("kv-a", "kv-b"))
    assert isinstance(out, ToAssign)
    store.commit(Batch(assignments_add=[out.assignment]))
    store.commit(_assign(out.assignment, [], store))
    assert store.assignments() == [] and store.entries("kv-a") == []


def test_assigned_unwatch_follows_the_setting_now(store):
    out = _mark(None, playcount=0, owners=("kv-a", "kv-b"), settings=ON)
    assert isinstance(out, ToAssign)
    store.commit(Batch(entries={"kv-a": [replace(_entry(History(watched=True, playcount=2)), key="k")]}))
    store.commit(_assign(out.assignment, ["kv-a"], store, settings=OFF))
    entry = store.entries("kv-a")[0]
    assert entry.state.history.watched and entry.state.history.local_unwatch_seq == 77 and entry.unsent == {}


def test_older_assignment_keeps_a_newer_person_change(store):
    out = _mark(None, owners=("kv-a", "kv-b"))
    assert isinstance(out, ToAssign)
    newer = replace(_entry(History(watched=False, explicit=True, unwatched=True)), key="k",
                    unsent={"history": "2026-11-20T11:00:00Z"})  # fmt: skip
    store.commit(Batch(entries={"kv-a": [newer]}))
    batch = _assign(out.assignment, ["kv-a"], store)
    assert batch.entries == {}


def test_assignment_to_a_found_entry_adds_its_tokens(store):
    out = _mark(None, owners=("kv-a", "kv-b"))
    assert isinstance(out, ToAssign)
    row = _movie()
    watched = History(watched=True, playcount=1, lastplayed=LAST)
    store.commit(Batch(entries={"kv-a": [Entry(key="k", tokens=frozenset([row.tokens[0]]), meta={},
                                               state=ItemState(history=watched))]}))  # fmt: skip
    store.commit(_assign(out.assignment, ["kv-a"], store))
    assert store.entries("kv-a")[0].tokens == frozenset(row.tokens)


def test_assigned_watch_over_a_noted_unwatch_lifts_the_note(store):
    out = _mark(None, owners=("kv-a", "kv-b"))
    assert isinstance(out, ToAssign)
    noted = History(watched=True, playcount=1, lastplayed=LAST, local_unwatch=True, local_unwatch_seq=41)
    store.commit(Batch(entries={"kv-a": [replace(_entry(noted), key="k")]}))
    store.commit(_assign(out.assignment, ["kv-a"], store))
    history = store.entries("kv-a")[0].state.history
    assert history.watched and not history.local_unwatch and store.entries("kv-a")[0].unsent == {}


def test_apply_assigned_unwatch(store):
    out = _mark(None, playcount=0, owners=("kv-a", "kv-b"), settings=ON)
    assert isinstance(out, ToAssign)
    store.commit(Batch(entries={"kv-a": [replace(_entry(History(watched=True, playcount=2)), key="k")]}))
    store.commit(_assign(out.assignment, ["kv-a"], store))
    history = store.entries("kv-a")[0].state.history
    assert (history.watched, history.unwatched, history.explicit) == (False, True, True)


def test_assigned_watch_on_watched_entry_changes_nothing(store):
    out = _mark(None, owners=("kv-a", "kv-b"))
    assert isinstance(out, ToAssign)
    watched = replace(_entry(History(watched=True, playcount=2, lastplayed="2026-01-01T00:00:00Z")), key="k")
    store.commit(Batch(entries={"kv-a": [watched]}))
    batch = _assign(out.assignment, ["kv-a"], store)
    assert batch.entries == {} and batch.assignments_done == [out.assignment.id]


def test_assignment_keeps_a_merge_with_nothing_to_record(store):
    out = _mark(None, owners=("kv-a", "kv-b"))
    assert isinstance(out, ToAssign)
    row = _movie()
    watched = History(watched=True, playcount=1, lastplayed=LAST)
    one = Entry(key="b-1", tokens=frozenset(row.tokens), meta={}, state=ItemState(history=watched))
    two = Entry(key="b-2", tokens=frozenset([row.tokens[0]]), meta={}, state=ItemState(history=watched))
    store.commit(Batch(entries={"kv-a": [one, two]}))
    store.commit(_assign(out.assignment, ["kv-a"], store))
    assert [e.key for e in store.entries("kv-a")] == ["b-1"]


def test_episode_title_without_numbers():
    row = _episode()
    raw = {**row.raw, "season": None}
    out = _mark(None, owners=("kv-a", "kv-b"), row=replace(row, raw=raw), show=SHOW)
    assert isinstance(out, ToAssign) and out.assignment.title == "Breaking Bad"
