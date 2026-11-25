from itertools import count
from typing import Any

import pytest

from resources.lib.sync import items
from resources.lib.sync.apply import apply_changes, settle_unsent
from resources.lib.sync.items import Library, LibraryRow, RowKey
from resources.lib.sync.record import Acked, Batch, Entry, History, ItemState, Rating, RecordStore

FETCHED = "2026-11-20T12:00:00Z"
FIGHT = {"type": "movie", "uniqueid": {"tmdb": "550"}, "title": "Fight Club", "year": 1999, "file": "smb://nas/fc.mkv"}
TOKENS = frozenset({"movies|tmdb:550"})
WATCH = {"playcount": 1, "lastplayed": "2026-11-11T19:10:00Z"}
_IDS = count(1)


def _change(seq: int, *, after: int = 0, queued_at: str = "2026-11-20T11:00:00Z", **params: Any) -> dict[str, Any]:
    return {"seq": seq, "change_id": f"c{seq}", "method": "VideoLibrary.SetMovieDetails",
            "params": {"movieid": 12, **params}, "item": dict(FIGHT), "after": after, "queued_at": queued_at}  # fmt: skip


def _ack(seq: int, status: str = "applied", **extra: str) -> dict[str, Any]:
    return {"seq": seq, "change_id": f"c{seq}", "status": status, **extra}


def _row(kodi_id: int = 12, uniqueid: dict[str, str] | None = None) -> LibraryRow:
    raw = {"movieid": kodi_id, "uniqueid": uniqueid or {"tmdb": "550"}, "title": "Fight Club", "year": 1999,
           "file": "smb://nas/fc.mkv"}  # fmt: skip
    return LibraryRow(RowKey("lib1", "movies", kodi_id, raw["file"]), tuple(items.tokens("movies", raw)), raw, None)


@pytest.fixture
def store(tmp_path) -> RecordStore:
    s = RecordStore(str(tmp_path / "sync.db"))
    s.open(create=True)
    return s


def _apply(store: RecordStore, *changes: dict[str, Any], rows: list[LibraryRow] | None = None) -> Any:
    library = Library("lib1", list(rows if rows is not None else [_row()]), [], {}, [], True)
    applied = apply_changes("kv-1", list(changes), store=store, library=library, fetched_at=FETCHED,
                            new_id=lambda: f"w{next(_IDS)}")  # fmt: skip
    store.commit(applied.batch)
    return applied


def _entry(store: RecordStore) -> Entry:
    [entry] = store.match("kv-1", TOKENS)
    return entry


def _store_entry(store: RecordStore, entry: Entry) -> None:
    store.commit(Batch(entries={"kv-1": [entry]}))


def test_watch_recorded_acked_and_intent_queued(store) -> None:
    applied = _apply(store, _change(5, after=3, **WATCH))
    assert applied.acks == [_ack(5)]
    entry = _entry(store)
    assert entry.state.history == History(watched=True, playcount=1, lastplayed="2026-11-11T19:10:00Z")
    assert entry.applied_change == {"history": 5} and entry.applied_after == {"history": 3}
    assert entry.origin == "cw" and entry.touched
    [intent] = store.pending_writes()
    assert (intent.group, intent.row.kodi_id, intent.fetched_at) == ("history", 12, FETCHED)
    assert store.applied_seq("kv-1") == 5


def test_ack_carries_seq_and_change_id_and_is_stored(store) -> None:
    _apply(store, _change(5, userrating=8))
    assert store.acks_pending("kv-1") == [_ack(5)]
    assert store.acked("kv-1", 5, "c5") == [Acked(5, "c5", "movies|tmdb:550", "ratings")]


def test_change_without_seq_or_change_id_is_not_acked(store) -> None:
    no_id = {**_change(5, userrating=8), "change_id": ""}
    no_seq = {**_change(6, userrating=8), "seq": "6"}
    applied = _apply(store, no_id, no_seq)
    assert applied.acks == [] and store.match("kv-1", TOKENS) == []


def test_change_without_kodi_id_found_by_item(store) -> None:
    change = {**_change(5, userrating=8), "params": {"userrating": 8}}
    applied = _apply(store, change, rows=[_row(40)])
    assert applied.acks == [_ack(5)] and _entry(store).state.rating.value == 8
    [intent] = store.pending_writes()
    assert intent.row.kodi_id == 40


def test_item_not_in_library_recorded(store) -> None:
    applied = _apply(store, {**_change(5, userrating=8), "params": {"userrating": 8}}, rows=[])
    assert applied.acks == [_ack(5)]
    assert _entry(store).state.rating.value == 8 and store.pending_writes() == []


def test_moved_kodi_id_resolved_by_tokens(store) -> None:
    _apply(store, _change(5, userrating=8), rows=[_row(40)])
    [intent] = store.pending_writes()
    assert intent.row.kodi_id == 40


def test_disallowed_change_failed_not_allowed(store) -> None:
    bad = [
        {**_change(5), "params": {"movieid": 12, "title": "x"}},
        {**_change(6), "method": "VideoLibrary.RemoveMovie"},
        {**_change(7), "params": "nonsense"},
        {**_change(8), "params": {"movieid": 12}},
        {**_change(9), "item": None},
    ]
    applied = _apply(store, *bad)
    assert applied.acks == [_ack(n, "failed", error="not_allowed") for n in range(5, 10)]
    assert store.applied_seq("kv-1") == 0 and store.match("kv-1", TOKENS) == []


@pytest.mark.parametrize(
    "params",
    [
        {"playcount": -1},
        {"playcount": "1"},
        {"playcount": True},
        {"lastplayed": 5},
        {"userrating": 11},
        {"userrating": -2},
        {"userrating": 7.5},
        {"resume": "abc"},
        {"resume": {"position": "abc", "total": 100}},
        {"resume": {"position": 10, "total": -1}},
    ],
)
def test_invalid_value_failed_not_allowed(store, params) -> None:
    applied = _apply(store, _change(5, **params))
    assert applied.acks == [_ack(5, "failed", error="not_allowed")] and store.match("kv-1", TOKENS) == []


def test_failed_ack_does_not_raise_applied_seq(store) -> None:
    _apply(store, _change(9, userrating=3), {**_change(12), "method": "nope"})
    assert store.applied_seq("kv-1") == 9
    assert store.acks_pending("kv-1") == [_ack(9), _ack(12, "failed", error="not_allowed")]


def test_cw_unwatch_sets_explicit_not_unwatched_and_queues_intent(store) -> None:
    _apply(store, _change(5, **WATCH))
    _apply(store, _change(6, playcount=0, lastplayed=""))
    history = _entry(store).state.history
    assert (history.watched, history.explicit, history.unwatched) == (False, True, False)
    # The unwatch's intent supersedes the watch's: the rules then decide what Kodi gets.
    [intent] = store.pending_writes()
    assert intent.group == "history"


def test_watch_clears_unwatched(store) -> None:
    state = ItemState(history=History(unwatched=True, explicit=True))
    _store_entry(store, Entry(key="movies|tmdb:550", tokens=TOKENS, meta={}, state=state))
    _apply(store, _change(5, **WATCH))
    history = _entry(store).state.history
    assert history.watched and not history.unwatched and not history.explicit


def test_local_unwatch_lifted_only_by_a_watch_made_after_it(store) -> None:
    # The unwatch was made before upload 8: a watch queued before CrossWatch saw 8 is older.
    state = ItemState(history=History(local_unwatch=True, local_unwatch_seq=8))
    _store_entry(store, Entry(key="movies|tmdb:550", tokens=TOKENS, meta={}, state=state))
    _apply(store, _change(5, after=7, queued_at="2026-11-20T23:00:00Z", **WATCH))
    assert _entry(store).state.history.local_unwatch
    _apply(store, _change(6, after=8, queued_at="2026-11-20T01:00:00Z", **WATCH))
    history = _entry(store).state.history
    assert not history.local_unwatch and history.local_unwatch_seq == 0


def test_rating_sets_from_cw_and_removal_flag(store) -> None:
    _apply(store, _change(5, userrating=8))
    assert _entry(store).state.rating == Rating(value=8, from_cw=True)
    _apply(store, _change(6, userrating=0))
    assert _entry(store).state.rating == Rating(value=0, from_cw=True, removed=True)


def test_resume_recorded_with_kodis_fetch_time(store) -> None:
    # Kodi's clock, like the play times the rules compare it with; never CrossWatch's.
    _apply(store, _change(5, queued_at="2026-11-20T11:30:00Z", resume={"position": 600, "total": 8340}))
    resume = _entry(store).state.resume
    assert (resume.position, resume.total, resume.at) == (600.0, 8340.0, FETCHED)


def test_rating_change_queues_rating_intent_only(store) -> None:
    _apply(store, _change(5, userrating=8))
    assert [w.group for w in store.pending_writes()] == ["ratings"]


def test_one_change_two_groups_acked_once_recorded_per_group(store) -> None:
    applied = _apply(store, _change(5, userrating=8, **WATCH))
    assert applied.acks == [_ack(5)]
    assert {a.group for a in store.acked("kv-1", 5, "c5")} == {"history", "ratings"}


def test_two_changes_for_a_new_item_in_one_batch_both_kept(store) -> None:
    _apply(store, _change(5, **WATCH), _change(6, userrating=8), rows=[])
    entry = _entry(store)
    assert entry.state.history.watched and entry.state.rating.value == 8
    assert entry.applied_change == {"history": 5, "ratings": 6}


def test_redelivered_change_acked_not_reapplied(store) -> None:
    _apply(store, _change(5, userrating=8))
    store.commit(Batch(pending_done=[w.id for w in store.pending_writes()]))
    # The ack was lost; the same change comes again with the item's new Kodi id.
    again = _apply(store, {**_change(5, userrating=8), "params": {"movieid": 77, "userrating": 8}})
    assert again.acks == [_ack(5)] and not again.touched
    assert store.pending_writes() == [] and _entry(store).change_count == 1


def test_same_seq_with_a_new_change_id_is_applied(store) -> None:
    # A restored CrossWatch hands a seq out again: the change id makes it a new change.
    store.commit(Batch(acked_add={"kv-1": [Acked(5, "old", "movies|tmdb:999", "ratings")]}))
    applied = _apply(store, _change(5, userrating=8))
    assert applied.acks == [_ack(5)] and _entry(store).state.rating.value == 8


def test_held_back_change_arriving_late_is_applied(store) -> None:
    _apply(store, _change(120, userrating=8))
    late = _apply(store, _change(50, **WATCH))
    assert late.touched and _entry(store).state.history.watched
    assert store.applied_seq("kv-1") == 120


def _with_unsent(store: RecordStore, state: ItemState, group: str, at: str, carried: int | None = None) -> None:
    _store_entry(store, Entry(key="movies|tmdb:550", tokens=TOKENS, meta={}, state=state, unsent={group: at},
                              unsent_upload={group: carried} if carried is not None else {}))  # fmt: skip


PERSON_AT = "2026-11-20T10:00:00Z"


def test_uncarried_person_change_beats_an_older_queued_change(store) -> None:
    _with_unsent(store, ItemState(rating=Rating(value=9)), "ratings", PERSON_AT)
    applied = _apply(store, _change(5, queued_at="2026-11-20T09:00:00Z", userrating=3))
    entry = _entry(store)
    assert entry.state.rating.value == 9 and entry.unsent == {"ratings": PERSON_AT}
    assert applied.acks == [_ack(5)] and store.pending_writes() == []


def test_uncarried_person_change_wins_whatever_the_clocks_say(store) -> None:
    # Upload order decides, never Kodi's clock against CrossWatch's.
    watched = ItemState(history=History(watched=True, playcount=1, lastplayed=PERSON_AT))
    _with_unsent(store, watched, "history", PERSON_AT)
    _apply(store, _change(5, after=99, queued_at="2026-11-21T11:00:00Z", playcount=0, lastplayed=""))
    entry = _entry(store)
    assert entry.state.history.watched and entry.unsent == {"history": PERSON_AT} and entry.touched


def test_change_made_after_the_carrying_upload_wins(store) -> None:
    _with_unsent(store, ItemState(rating=Rating(value=9)), "ratings", PERSON_AT, carried=41)
    _apply(store, _change(5, after=41, queued_at="2026-11-20T09:00:00Z", userrating=3))
    entry = _entry(store)
    assert entry.state.rating.value == 3 and entry.unsent == {} and entry.unsent_upload == {}


def test_change_made_before_the_carrying_upload_loses(store) -> None:
    _with_unsent(store, ItemState(rating=Rating(value=9)), "ratings", PERSON_AT, carried=41)
    _apply(store, _change(5, after=40, queued_at="2026-11-20T11:00:00Z", userrating=3))
    entry = _entry(store)
    # CrossWatch skipped the carrying upload's row while its change was pending: the person's
    # 9 goes up again and is not settled by the next fetch.
    assert entry.state.rating.value == 9 and entry.unsent == {"ratings": PERSON_AT} and entry.unsent_upload == {}
    assert entry.touched and entry.change_count == 1
    assert settle_unsent("kv-1", set(), store).unsent_cleared == {}


def test_person_win_marks_the_entry_for_the_next_upload(store) -> None:
    _with_unsent(store, ItemState(rating=Rating(value=9)), "ratings", PERSON_AT)
    applied = _apply(store, _change(5, queued_at="2026-11-20T09:00:00Z", userrating=3))
    assert applied.touched == {"movies|tmdb:550"} and _entry(store).touched


def test_lastplayed_without_playcount_failed(store) -> None:
    applied = _apply(store, _change(5, lastplayed="2026-11-11T19:10:00Z"))
    assert applied.acks == [_ack(5, "failed", error="not_allowed")]


def test_redelivery_for_an_item_not_in_the_record_makes_no_entry(store) -> None:
    store.commit(Batch(acked_add={"kv-1": [Acked(5, "c5", "movies|tmdb:550", "ratings")]}))
    applied = _apply(store, _change(5, userrating=8))
    assert applied.acks == [_ack(5)] and store.entries("kv-1") == []


def _two_copies(store: RecordStore) -> None:
    # Two entries of one item, each known by one id; CrossWatch's item carries both.
    a = Entry(key="movies|imdb:tt1", tokens=frozenset({"movies|imdb:tt1"}), meta={}, origin="person",
              state=ItemState(history=History(watched=True, playcount=2, lastplayed="2026-11-01T10:00:00Z")),
              unsent={"history": "2026-11-01T10:00:00Z"}, unsent_upload={"history": 3})  # fmt: skip
    b = Entry(key="movies|tmdb:550", tokens=TOKENS, meta={}, state=ItemState(rating=Rating(value=6)))
    store.commit(Batch(entries={"kv-1": [a, b]}))


BOTH = {**FIGHT, "uniqueid": {"tmdb": "550", "imdb": "tt1"}}


def test_merge_in_a_batch_is_recorded_and_later_changes_land_on_the_survivor(store) -> None:
    _two_copies(store)
    first = {**_change(5, after=3, playcount=0, lastplayed=""), "item": BOTH}
    second = {**_change(6, userrating=8), "item": BOTH}
    applied = _apply(store, first, second)
    [entry] = store.entries("kv-1")
    assert entry.key == "movies|imdb:tt1" and applied.touched == {"movies|imdb:tt1"}
    # The first change beat the person's watch; the second must not bring it back.
    assert not entry.state.history.watched and entry.unsent == {}
    assert entry.state.rating.value == 8 and entry.applied_change == {"history": 5, "ratings": 6}
    assert store.entry("kv-1", "movies|tmdb:550") == entry


def test_redelivery_that_merges_keeps_the_merge(store) -> None:
    _two_copies(store)
    store.commit(Batch(acked_add={"kv-1": [Acked(5, "c5", "movies|tmdb:550", "ratings")]}))
    applied = _apply(store, {**_change(5, userrating=8), "item": BOTH})
    assert applied.acks == [_ack(5)]
    [entry] = store.entries("kv-1")
    assert entry.state.history.watched and entry.state.rating.value == 6
    assert entry.tokens == {"movies|imdb:tt1", "movies|tmdb:550"} and applied.touched == {entry.key}


def test_new_tokens_on_an_entry_mark_it_touched(store) -> None:
    _store_entry(store, Entry(key="movies|tmdb:550", tokens=TOKENS, meta={}, state=ItemState()))
    applied = _apply(store, {**_change(5, userrating=8), "item": BOTH})
    assert _entry(store).tokens == {"movies|tmdb:550", "movies|imdb:tt1"} and applied.touched == {"movies|tmdb:550"}


def test_fetched_includes_redeliveries_and_follows_merges(store) -> None:
    _two_copies(store)
    store.commit(Batch(acked_add={"kv-1": [Acked(4, "c4", "movies|tmdb:550", "ratings")]}))
    page1 = _apply(store, _change(4, userrating=6))
    assert page1.fetched == {("movies|tmdb:550", "ratings")}
    page2 = _apply(store, {**_change(5, userrating=8), "item": BOTH})
    assert page2.fetched == {("movies|imdb:tt1", "ratings")}


def test_settle_unsent_clears_landed_person_changes(store) -> None:
    _with_unsent(store, ItemState(rating=Rating(value=9)), "ratings", PERSON_AT, carried=4)
    store.commit(settle_unsent("kv-1", set(), store))
    assert _entry(store).unsent == {}


def test_settle_unsent_keeps_groups_with_a_queued_change(store) -> None:
    _with_unsent(store, ItemState(rating=Rating(value=9)), "ratings", PERSON_AT, carried=4)
    assert settle_unsent("kv-1", {("movies|tmdb:550", "ratings")}, store).unsent_cleared == {}
    # A person change not yet carried is never settled by an empty fetch.
    _with_unsent(store, ItemState(rating=Rating(value=9)), "ratings", PERSON_AT)
    assert settle_unsent("kv-1", set(), store).unsent_cleared == {}


def test_settle_unsent_resolves_keys_merged_on_a_later_page(store) -> None:
    _with_unsent(store, ItemState(rating=Rating(value=9)), "ratings", PERSON_AT, carried=4)
    other = Entry(key="movies|imdb:tt0", tokens=frozenset({"movies|imdb:tt0"}), meta={}, state=ItemState())
    survivor = Entry(key="movies|imdb:tt0", tokens=TOKENS | {"movies|imdb:tt0"}, meta={}, state=ItemState(),
                     unsent={"ratings": PERSON_AT}, unsent_upload={"ratings": 4})  # fmt: skip
    store.commit(Batch(entries={"kv-1": [other]}))
    store.commit(Batch(entries={"kv-1": [survivor]}, merged={"kv-1": {"movies|imdb:tt0": ["movies|tmdb:550"]}}))
    assert settle_unsent("kv-1", {("movies|tmdb:550", "ratings")}, store).unsent_cleared == {}


def test_episode_change_matched_by_show_tokens(store) -> None:
    raw = {"episodeid": 41, "uniqueid": {"unknown": "x"}, "title": "Pilot", "showtitle": "BB", "season": 1,
           "episode": 2, "tvshowid": 3, "file": "/e.mkv"}  # fmt: skip
    show = {"tvshowid": 3, "uniqueid": {"tvdb": "81189"}, "title": "BB"}
    row = LibraryRow(RowKey("lib1", "episodes", 41, "/e.mkv"), tuple(items.tokens("episodes", raw, show)), raw, None)
    change = {"seq": 5, "change_id": "c5", "method": "VideoLibrary.SetEpisodeDetails",
              "params": {"episodeid": 41, "userrating": 7}, "after": 0, "queued_at": "2026-11-20T11:00:00Z",
              "item": {"type": "episode", "uniqueid": {}, "title": "Pilot", "showtitle": "BB", "season": 1,
                       "episode": 2, "show_uniqueid": {"tvdb": "81189"}}}  # fmt: skip
    library = Library("lib1", [], [row], {}, [], True)
    applied = apply_changes("kv-1", [change], store=store, library=library, fetched_at=FETCHED, new_id=lambda: "w1")
    store.commit(applied.batch)
    [entry] = store.match("kv-1", frozenset({"episodes|show:tvdb:81189|1|2"}))
    assert entry.state.rating.value == 7
    [intent] = store.pending_writes()
    assert intent.row.kodi_id == 41
