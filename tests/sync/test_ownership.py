from __future__ import annotations

from typing import Any

from resources.lib.models import Viewer
from resources.lib.playlist_index import PlaylistIndex
from resources.lib.storage import RememberedAnswer
from resources.lib.sync.items import LibraryRow, RowKey
from resources.lib.sync.ownership import EMPTY_SNAPSHOT, IndexSnapshot, count, moved_viewers, owners, owners_of

ANNA = Viewer(name="anna", playlists=("Anna TV",), profiles=("Anna",), sync_id="kv-anna0001")
BOB = Viewer(name="bob", profiles=("Bob",), sync_id="kv-bob00001")
NOID = Viewer(name="carol")
SHOW = {"uniqueid": {"tvdb": "81189"}, "title": "Breaking Bad", "year": 2008}
UNSCRAPED = {"uniqueid": {}, "title": "Home Video", "year": 0}


def _snapshot(
    by_key: dict[tuple[str, int], tuple[str, ...]] | None = None, viewers=(ANNA, BOB), **kw: Any
) -> IndexSnapshot:
    index = PlaylistIndex(by_key=by_key or {}, built_at=1.0, degraded=kw.pop("degraded", frozenset()))
    return IndexSnapshot(index=index, built_at=1.0, invalidated_at=0.0, viewers=tuple(viewers), **kw)


def _episode(kodi_id: int = 10, show: int = 7) -> LibraryRow:
    return LibraryRow(RowKey("lib", "episodes", kodi_id, f"f{kodi_id}"), (f"t{kodi_id}",), {}, show)


def _movie(kodi_id: int = 1) -> LibraryRow:
    return LibraryRow(RowKey("lib", "movies", kodi_id, f"m{kodi_id}"), (f"m{kodi_id}",), {}, None)


def _owners(row: LibraryRow, snapshot: IndexSnapshot, *, shows=None, answers=None, profile="", private=False):
    return owners(row, snapshot=snapshot, shows=shows or {7: SHOW}, answers=answers or {},
                  profile_label=profile, private_profile_db=private)  # fmt: skip


def test_playlist_owner() -> None:
    snap = _snapshot({("tvshow", 7): ("anna",), ("movie", 1): ("anna", "bob")})
    assert _owners(_episode(), snap) == ("kv-anna0001",)
    assert _owners(_movie(), snap) == ("kv-anna0001", "kv-bob00001")


def test_profile_tier_only_on_private_database() -> None:
    snap = _snapshot()
    assert _owners(_movie(), snap, profile="Bob", private=True) == ("kv-bob00001",)
    assert _owners(_movie(), snap, profile="Bob", private=False) == ()


def test_remembered_answer_by_show_id() -> None:
    answers = {"show:tvdb:81189": RememberedAnswer(viewers=("bob",))}
    assert _owners(_episode(), _snapshot(), answers=answers) == ("kv-bob00001",)


def test_remembered_key_uses_add_on_ids_not_cw_ids() -> None:
    # The add-on keys on tvdb first; CrossWatch's own normaliser would order tmdb first.
    show = {"uniqueid": {"tmdb": "1396", "tvdb": "81189"}, "title": "Breaking Bad", "year": 2008}
    answers = {"show:tvdb:81189": RememberedAnswer(viewers=("bob",))}
    assert _owners(_episode(), _snapshot(), shows={7: show}, answers=answers) == ("kv-bob00001",)


def test_remembered_answer_with_show_title_year_guard() -> None:
    answers = {"tvshow:7": RememberedAnswer(viewers=("bob",), title="Home Video", year=2001)}
    assert _owners(_episode(), _snapshot(), shows={7: {**UNSCRAPED, "year": 2001}}, answers=answers) == ("kv-bob00001",)
    assert (
        _owners(_episode(), _snapshot(), shows={7: {**UNSCRAPED, "title": "Other", "year": 2001}}, answers=answers)
        == ()
    )
    assert _owners(_episode(), _snapshot(), shows={7: {**UNSCRAPED, "year": 2002}}, answers=answers) == ()
    untitled = {"tvshow:7": RememberedAnswer(viewers=("bob",))}
    assert _owners(_episode(), _snapshot(), shows={7: UNSCRAPED}, answers=untitled) == ()


def test_remembered_year_zero_matches_none() -> None:
    answers = {"tvshow:7": RememberedAnswer(viewers=("bob",), title="Home Video", year=None)}
    assert _owners(_episode(), _snapshot(), shows={7: UNSCRAPED}, answers=answers) == ("kv-bob00001",)


def test_remembered_tier_ignores_movies_and_missing_shows() -> None:
    answers = {"show:tvdb:81189": RememberedAnswer(viewers=("bob",))}
    assert _owners(_movie(), _snapshot(), answers=answers) == ()
    assert _owners(_episode(show=99), _snapshot(), answers=answers) == ()


def test_single_viewer() -> None:
    assert _owners(_movie(), _snapshot(viewers=(ANNA,))) == ("kv-anna0001",)


def test_no_owner() -> None:
    assert _owners(_movie(), _snapshot()) == ()


def test_owners_are_sync_ids_and_unknown_names_dropped() -> None:
    # "zoe" left the household after the index was built; the profile tier still answers.
    snap = _snapshot({("movie", 1): ("zoe",)}, viewers=(ANNA, BOB, NOID))
    assert _owners(_movie(), snap, profile="Anna", private=True) == ("kv-anna0001",)
    # A viewer without a sync id yet owns nothing.
    snap = _snapshot({("movie", 1): ("carol",)}, viewers=(ANNA, NOID))
    assert _owners(_movie(), snap) == ()


def test_unusable_snapshot_gives_no_owners() -> None:
    assert _owners(_movie(), EMPTY_SNAPSHOT) == ()
    assert _owners(_movie(), _snapshot({("movie", 1): ("anna",)}, degraded=frozenset({"bob"}))) == ()
    assert _owners(_movie(), _snapshot({("movie", 1): ("anna",)}, unreadable=("Bob TV",))) == ()


def test_usable() -> None:
    snap = _snapshot()
    assert snap.usable(last_scan_end=1.0, last_viewer_change=0.5, show_ids_changed_at=0.0)
    assert not snap.usable(last_scan_end=1.5, last_viewer_change=0.0, show_ids_changed_at=0.0)
    assert not EMPTY_SNAPSHOT.usable(last_scan_end=0.0, last_viewer_change=0.0, show_ids_changed_at=0.0)


def test_counts() -> None:
    snap = _snapshot(
        {("movie", 1): ("anna",), ("movie", 2): ("anna", "bob"), ("movie", 3): ("bob",), ("movie", 4): ("anna",)}
    )
    decide = owners_of(snap, {}, {}, "", False)
    result = count([_movie(n) for n in range(1, 6)], decide)
    assert result.per_viewer == {"kv-anna0001": 2, "kv-bob00001": 1}
    assert (result.shared, result.unowned) == (1, 1)


def _decide(snapshot: IndexSnapshot, answers=None):
    return owners_of(snapshot, {7: SHOW}, answers or {}, "", False)


def test_moved_viewers_names_who_gained_and_who_lost() -> None:
    rows = [_episode(), _movie()]
    before = _decide(_snapshot({("tvshow", 7): ("anna",), ("movie", 1): ("bob",)}))
    after = _decide(_snapshot({("tvshow", 7): ("bob",), ("movie", 1): ("bob",)}))
    assert moved_viewers(rows, before, after) == {"kv-anna0001", "kv-bob00001"}


def test_moved_viewers_ignores_a_new_index_that_moves_nothing() -> None:
    rows = [_episode(), _movie()]
    snap = {("tvshow", 7): ("anna",), ("movie", 1): ("anna", "bob")}
    assert moved_viewers(rows, _decide(_snapshot(snap)), _decide(_snapshot(dict(snap)))) == frozenset()


def test_a_new_remembered_answer_moves_the_show() -> None:
    answers = {"show:tvdb:81189": RememberedAnswer(viewers=("bob",))}
    assert moved_viewers([_episode()], _decide(_snapshot()), _decide(_snapshot(), answers)) == {"kv-bob00001"}
