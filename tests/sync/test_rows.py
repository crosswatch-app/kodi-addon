from __future__ import annotations

from typing import Any

from resources.lib.sync import items
from resources.lib.sync.items import Library, LibraryRow, RowKey
from resources.lib.sync.record import Entry, History, ItemState, Rating, Resume, Seen
from resources.lib.sync.rows import Mode, first_run_state, viewer_rows

NOW = "2026-11-20T10:00:00Z"
SHOW = {"tvshowid": 7, "uniqueid": {"tvdb": "81189", "tmdb": "1396"}, "title": "Breaking Bad", "year": 2008}


def _movie(kodi_id: int, ids: dict[str, str], file: str | None = None, **raw: Any) -> LibraryRow:
    file = file or f"smb://nas/{kodi_id}.mkv"
    body = {"movieid": kodi_id, "uniqueid": ids, "title": "Fight Club", "year": 1999, "file": file,
            "playcount": 0, "lastplayed": "", "userrating": 0, "resume": {"position": 0, "total": 0}, **raw}  # fmt: skip
    return LibraryRow(RowKey("lib1", "movies", kodi_id, file), tuple(items.tokens("movies", body)), body, None)


def _episode(
    kodi_id: int, n: int, file: str | None = None, ids: dict[str, str] | None = None, **raw: Any
) -> LibraryRow:
    file = file or f"smb://nas/e{n}.mkv"
    body = {"episodeid": kodi_id, "tvshowid": 7, "uniqueid": ids or {}, "title": f"Ep {n}", "showtitle": "Breaking Bad",
            "season": 1, "episode": n, "file": file, "playcount": 0, "lastplayed": "", "userrating": 0,
            "resume": {"position": 0, "total": 0}, **raw}  # fmt: skip
    return LibraryRow(RowKey("lib1", "episodes", kodi_id, file), tuple(items.tokens("episodes", body, SHOW)), body, 7)


def _library(movies=(), episodes=(), shows=None) -> Library:
    return Library("lib1", list(movies), list(episodes), {7: SHOW} if shows is None else shows, [], True)


def _entry(row: LibraryRow, state: ItemState, **kw: Any) -> Entry:
    return Entry(key=items.identity(row.key.kind, row.raw, SHOW), tokens=frozenset(row.tokens),
                 meta={"file": row.key.file, "title": "Fight Club", "uniqueid": row.raw.get("uniqueid", {})}, state=state, **kw)  # fmt: skip


class Record:
    def __init__(
        self, entries: list[Entry], seen: dict[RowKey, Seen] | None = None, before: Seen | None = None
    ) -> None:
        self.entries = entries
        self.seen = seen or {}
        self.before = before
        self.ids = iter(range(1, 1000))

    def match(self, tokens: frozenset[str]) -> list[Entry]:
        return sorted((e for e in self.entries if e.tokens & tokens), key=lambda e: e.key)

    def seen_for(self, tokens: tuple[str, ...], file: str) -> Seen | None:
        return self.before

    def new_id(self) -> str:
        return f"w{next(self.ids)}"


def _rows(
    library: Library,
    record: Record,
    *,
    owners=("kv-1",),
    seed=False,
    mode: Mode = "full",
    touched: set[str] | None = None,
) -> Any:
    return viewer_rows(library, viewer="kv-1", owners=lambda row: tuple(owners), match=record.match, seen_for=record.seen_for,
                       new_id=record.new_id, seed=seed, entries=record.entries, seen=record.seen, mode=mode,
                       touched=touched or set(), now_utc=NOW)  # fmt: skip


WATCHED = ItemState(
    history=History(watched=True, playcount=2, lastplayed="2026-11-01T20:00:00Z"), rating=Rating(value=8)
)


def test_row_carries_record_state_not_kodi_state() -> None:
    row = _movie(1, {"tmdb": "550"}, playcount=9, userrating=3)
    out = _rows(_library([row]), Record([_entry(row, WATCHED)]))
    [sent] = out.rows
    assert (sent.row["playcount"], sent.row["lastplayed"], sent.row["userrating"]) == (2, "2026-11-01T20:00:00Z", 8)
    assert sent.row["movieid"] == 1 and "unwatched" not in sent.row


def test_unwatched_flag_only_from_the_record() -> None:
    row = _movie(1, {"tmdb": "550"})
    state = ItemState(history=History(watched=False, explicit=True, unwatched=True))
    [sent] = _rows(_library([row]), Record([_entry(row, state)])).rows
    assert sent.row["unwatched"] is True and sent.row["playcount"] == 0


def test_every_copy_goes_up_with_the_item_state() -> None:
    hd = _movie(40, {"tmdb": "550"}, file="smb://nas/fc-1080.mkv")
    uhd = _movie(12, {"tmdb": "550", "imdb": "tt0137523"}, file="smb://nas/fc-2160.mkv")
    out = _rows(_library([hd, uhd]), Record([_entry(hd, WATCHED)]))
    assert sorted(r.row["movieid"] for r in out.rows) == [12, 40]
    assert {r.entry_key for r in out.rows} == {_entry(hd, WATCHED).key}
    assert all(r.row["playcount"] == 2 for r in out.rows)


def test_a_copy_owned_by_the_viewer_makes_the_item_theirs() -> None:
    low = _movie(12, {"tmdb": "550"}, file="smb://nas/a.mkv")
    high = _movie(40, {"tmdb": "550"}, file="smb://nas/b.mkv")
    out = viewer_rows(_library([low, high]), viewer="kv-1", owners=lambda r: ("kv-1",) if r.key.kodi_id == 40 else (),
                      match=Record([]).match, seen_for=lambda t, f: None, new_id=lambda: "w", seed=False, entries=[],
                      seen={}, mode="full", touched=set(), now_utc=NOW)  # fmt: skip
    assert len(out.rows) == 2


def test_seed_takes_the_watched_copy() -> None:
    old = _movie(12, {"tmdb": "550"}, file="smb://nas/a.mkv")
    watched = _movie(
        40, {"tmdb": "550"}, file="smb://nas/b.mkv", playcount=3, lastplayed="2026-10-01T10:00:00Z", userrating=8
    )
    [entry] = _rows(_library([old, watched]), Record([]), seed=True).entries
    assert entry.state.history.playcount == 3 and entry.seed_rating == 8


def test_catch_up_also_when_entries_merge() -> None:
    a, b = _movie(1, {"tmdb": "550"}), _movie(2, {"imdb": "tt0137523"})
    both = _movie(3, {"tmdb": "550", "imdb": "tt0137523"})
    record = Record([_entry(a, WATCHED), _entry(b, ItemState())], before=Seen(playcount=2))
    out = _rows(_library([both]), record)
    assert [w.group for w in out.catch_up] == ["history"]


def test_catch_up_looks_up_each_rows_own_tokens_and_file() -> None:
    row = _movie(77, {"tmdb": "550"})
    asked: list[tuple[tuple[str, ...], str]] = []
    viewer_rows(_library([row]), viewer="kv-1", owners=lambda r: ("kv-1",), match=Record([_entry(row, WATCHED)]).match,
                seen_for=lambda t, f: asked.append((t, f)) or None, new_id=lambda: "w", seed=False, entries=[],
                seen={}, mode="full", touched=set(), now_utc=NOW)  # fmt: skip
    assert asked == [(row.tokens, row.key.file)]


def test_untouched_orphan_never_rides_along_in_a_delta() -> None:
    ep = _episode(10, 1, file="smb://nas/shared.mkv")
    gone = _movie(1, {"tmdb": "550"}, file="smb://nas/shared.mkv")
    eg = _entry(gone, WATCHED)
    out = _rows(
        _library(episodes=[ep], shows={}), Record([eg, _entry(ep, ItemState())]), mode="delta", touched={eg.key}
    )
    assert [r.removed for r in out.rows] == [True]


def test_a_watch_never_goes_up_as_playcount_zero() -> None:
    row = _movie(1, {"tmdb": "550"})
    state = ItemState(history=History(watched=True, playcount=0, lastplayed="2026-11-01T20:00:00Z"))
    [sent] = _rows(_library([row]), Record([_entry(row, state)])).rows
    assert sent.row["playcount"] == 1


def test_meta_follows_a_renamed_file() -> None:
    row = _movie(1, {"tmdb": "550"}, file="smb://nas/renamed.mkv")
    stale = _entry(_movie(1, {"tmdb": "550"}, file="smb://nas/old.mkv"), WATCHED)
    [entry] = _rows(_library([row]), Record([stale])).entries
    assert entry.meta["file"] == "smb://nas/renamed.mkv"


def test_two_items_on_one_entry_share_one_version() -> None:
    a = _movie(1, {"tmdb": "550"}, file="smb://nas/a.mkv")
    b = _movie(2, {"imdb": "tt0137523"}, file="smb://nas/b.mkv")
    entry = Entry(key="movies|imdb:tt0137523|tmdb:550", tokens=frozenset(a.tokens) | frozenset(b.tokens),
                  meta={"file": "smb://nas/a.mkv"}, state=WATCHED)  # fmt: skip
    out = _rows(_library([a, b]), Record([entry]))
    assert len(out.entries) <= 1 and {r.entry_key for r in out.rows} == {entry.key}


def test_orphan_carries_the_show_block_from_meta() -> None:
    ep = _episode(10, 1)
    entry = Entry(key="e", tokens=frozenset(ep.tokens), meta={"show": {"title": "Breaking Bad", "uniqueid": {"tvdb": "81189"}}},
                  state=WATCHED)  # fmt: skip
    [sent] = _rows(_library(episodes=[ep], shows={}), Record([entry])).rows
    assert sent.row["show"]["title"] == "Breaking Bad"


def test_two_episodes_in_one_file_stay_two_items() -> None:
    a = _episode(10, 1, file="smb://nas/s01e01-e02.mkv")
    b = _episode(11, 2, file="smb://nas/s01e01-e02.mkv")
    out = _rows(_library(episodes=[a, b]), Record([]))
    assert sorted(r.row["episodeid"] for r in out.rows) == [10, 11]
    assert out.tvshows == {
        7: {"tvshowid": 7, "uniqueid": {"tmdb": "1396", "tvdb": "81189"}, "title": "Breaking Bad", "year": 2008}
    }


def test_refresh_adds_episode_own_id_keeps_state() -> None:
    before = _episode(10, 1)
    entry = _entry(before, WATCHED)
    after = _episode(55, 1, ids={"tvdb": "349232"})
    out = _rows(_library(episodes=[after]), Record([entry]))
    [sent] = out.rows
    assert sent.row["playcount"] == 2 and sent.entry_key == entry.key
    [grown] = out.entries
    assert set(after.tokens) <= grown.tokens and grown.change_count == entry.change_count + 1


def test_entries_merged_when_tokens_overlap() -> None:
    a = _movie(1, {"tmdb": "550"})
    b = _movie(2, {"imdb": "tt0137523"})
    both = _movie(3, {"tmdb": "550", "imdb": "tt0137523"})
    ea, eb = _entry(a, WATCHED), _entry(b, ItemState(rating=Rating(value=6, src=1)))
    out = _rows(_library([both]), Record([ea, eb]))
    [merged] = out.entries
    assert out.merged == {merged.key: sorted([ea.key, eb.key])}
    assert merged.tokens >= set(both.tokens)


def test_new_item_for_settled_viewer_starts_empty() -> None:
    row = _movie(1, {"tmdb": "550"}, playcount=4, lastplayed="2026-10-01T10:00:00Z", userrating=9)
    out = _rows(_library([row]), Record([]))
    [entry] = out.entries
    assert entry.state == ItemState() and entry.origin == "empty"
    assert out.rows[0].row["playcount"] == 0


def test_new_item_for_seed_viewer_single_owner_from_kodi() -> None:
    row = _movie(1, {"tmdb": "550"}, playcount=4, lastplayed="2026-10-01T10:00:00Z", userrating=9,
                 resume={"position": 300, "total": 5400})  # fmt: skip
    [entry] = _rows(_library([row]), Record([]), seed=True).entries
    assert entry.state.history.playcount == 4 and entry.state.rating.value == 0
    assert (entry.origin, entry.seed_rating) == ("seed", 9)
    shared = _rows(_library([row]), Record([]), seed=True, owners=("kv-1", "kv-2")).entries[0]
    assert shared.state == ItemState() and shared.origin == "empty"


def test_first_run_state_has_no_rating() -> None:
    row = _movie(1, {"tmdb": "550"}, playcount=1, userrating=7, resume={"position": 12.5, "total": 100})
    state = first_run_state(row)
    assert state.rating == Rating() and state.resume == Resume(position=12.5, total=100.0) and state.history.watched


def test_unowned_left_out() -> None:
    row = _movie(1, {"tmdb": "550"})
    out = _rows(_library([row]), Record([]), owners=("kv-2",))
    assert out.rows == [] and out.entries == []


def test_returning_file_queues_catch_up_intent() -> None:
    row = _movie(77, {"tmdb": "550"})
    out = _rows(_library([row]), Record([_entry(row, WATCHED)], before=Seen(playcount=2, userrating=8)))
    assert sorted(w.group for w in out.catch_up) == ["history", "ratings"]
    assert all(w.row == row.key for w in out.catch_up)


def test_catch_up_never_undoes_person_unwatch() -> None:
    # The person's unwatch was stored as Seen, so the newest Seen shows it unwatched.
    row = _movie(77, {"tmdb": "550"})
    out = _rows(_library([row]), Record([_entry(row, WATCHED)], before=Seen(playcount=0)))
    assert out.catch_up == []


def test_no_catch_up_for_a_row_already_seen_or_shared_rating() -> None:
    row = _movie(77, {"tmdb": "550"})
    seen = {row.key: Seen()}
    assert _rows(_library([row]), Record([_entry(row, WATCHED)], seen=seen, before=Seen(playcount=2))).catch_up == []
    shared = _rows(_library([row]), Record([_entry(row, WATCHED)], before=Seen(userrating=8)), owners=("kv-1", "kv-2"))
    assert shared.catch_up == []


def test_orphaned_episode_sent_as_not_in_library() -> None:
    ep = _episode(10, 1)
    out = _rows(_library(episodes=[ep], shows={}), Record([_entry(ep, WATCHED)]))
    [sent] = out.rows
    assert sent.removed and sent.row["in_library"] is False and "episodeid" not in sent.row
    assert out.orphans == 1


def test_removed_rows_carry_record_state() -> None:
    gone = _movie(1, {"tmdb": "550"})
    entry = _entry(gone, WATCHED, touched=True)
    [sent] = _rows(_library(), Record([entry])).rows
    assert (
        sent.removed and sent.row["in_library"] is False and sent.row["playcount"] == 2 and sent.row["userrating"] == 8
    )
    assert sent.row["file"] == gone.key.file and sent.together == gone.key.file


def test_full_leaves_out_untouched_removed_rows() -> None:
    gone = _movie(1, {"tmdb": "550"})
    assert _rows(_library(), Record([_entry(gone, WATCHED)])).rows == []
    unsent = _entry(gone, WATCHED, unsent={"ratings": NOW})
    assert len(_rows(_library(), Record([unsent])).rows) == 1


def test_uniqueid_keys_sorted_on_wire() -> None:
    row = _movie(1, {"tmdb": "550", "imdb": "tt0137523"})
    [sent] = _rows(_library([row]), Record([])).rows
    assert list(sent.row["uniqueid"]) == ["imdb", "tmdb"]


def test_delta_sends_only_touched_entries() -> None:
    a, b = _movie(1, {"tmdb": "1"}), _movie(2, {"tmdb": "2"})
    ea, eb = _entry(a, WATCHED), _entry(b, WATCHED)
    out = _rows(_library([a, b]), Record([ea, eb]), mode="delta", touched={eb.key})
    assert [r.entry_key for r in out.rows] == [eb.key]
    # A new item waits for the next full.
    new = _rows(_library([_movie(3, {"tmdb": "3"})]), Record([]), mode="delta")
    assert new.rows == [] and new.entries == []


def test_delta_with_removed_row_carries_live_rows_on_same_file() -> None:
    old = _movie(1, {"tmdb": "550"}, file="smb://nas/same.mkv")
    replacement = _movie(2, {"tmdb": "999"}, file="smb://nas/same.mkv")
    gone, live = _entry(old, WATCHED), _entry(replacement, ItemState())
    out = _rows(_library([replacement]), Record([gone, live]), mode="delta", touched={gone.key})
    assert [(r.entry_key, r.removed) for r in out.rows] == [(gone.key, True), (live.key, False)]
    assert {r.together for r in out.rows} == {"smb://nas/same.mkv"}
