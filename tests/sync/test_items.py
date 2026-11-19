"""Item identity must match CrossWatch's, value for value.

The expected values below were produced by running CrossWatch's own normalize_uniqueids,
_tokens, _identity and _keep_show_ids (dev build sha256:59d8cfbb...) on these inputs.
"""

from typing import Any

import pytest

from resources.lib.sync import items
from resources.lib.sync.items import Library, normalize_ids, read_library
from tests.fakes import FakeKodi
from tests.sync.fake_library import FakeLibrary

CW_IDS: list[tuple[dict[str, str], dict[str, str]]] = [
    ({"imdb": "tt0137523", "tmdb": "550"}, {"imdb": "tt0137523", "tmdb": "550"}),
    ({"unknown": "tt0137523"}, {"imdb": "tt0137523"}),
    ({"themoviedb": "550", "tmdb": "551"}, {"tmdb": "550"}),
    ({"TheTVDB": "81189", "imdb": "None"}, {"tvdb": "81189"}),
    ({"myanimelist": "1", "anidb": "2", "kitsu": "", "anilist": "0"}, {"anidb": "2", "mal": "1"}),
    ({"imdb": "-1", "tvdb": " 123 ", "foo": "abc"}, {"tvdb": "123"}),
    ({"unknown": "12345"}, {}),
]

BREAKING_BAD = {"uniqueid": {"tvdb": "81189", "tmdb": "1396"}, "title": "Breaking Bad"}

CW_ROWS: list[tuple[items.ItemKind, dict[str, Any], dict[str, Any] | None, list[str], str]] = [
    (
        "movies",
        {"uniqueid": {"imdb": "tt0137523", "tmdb": "550"}, "title": "Fight Club", "year": 1999},
        None,
        ["movies|imdb:tt0137523", "movies|tmdb:550"],
        "movies|imdb:tt0137523|tmdb:550",
    ),
    (
        "movies",
        {"uniqueid": {}, "title": " Fight Club ", "year": 1999},
        None,
        ["movies|fight club|1999"],
        "movies|fight club|1999",
    ),
    ("movies", {"uniqueid": {"unknown": "x"}, "title": "Amélie"}, None, ["movies|amélie|None"], "movies|amélie|None"),
    (
        "episodes",
        {"uniqueid": {"tvdb": "349232"}, "season": 1, "episode": 2, "tvshowid": 7},
        BREAKING_BAD,
        ["episodes|tvdb:349232", "episodes|show:tmdb:1396|1|2", "episodes|show:tvdb:81189|1|2"],
        "episodes|tvdb:349232",
    ),
    (
        "episodes",
        {"uniqueid": {}, "season": 1, "episode": 2, "tvshowid": 7},
        {"uniqueid": {"tvdb": "81189"}, "title": "Breaking Bad"},
        ["episodes|show:tvdb:81189|1|2"],
        "episodes|show:tvdb:81189|1|2",
    ),
    (
        "episodes",
        {"uniqueid": {}, "season": 0, "episode": 1, "showtitle": "The Office", "tvshowid": 7},
        {"uniqueid": {}, "title": "The Office"},
        ["episodes|the office|0|1"],
        "episodes|the office|0|1",
    ),
    (
        "episodes",
        {"uniqueid": {}, "showtitle": "", "tvshowid": 7},
        {"uniqueid": {}, "title": "Show Title"},
        ["episodes|show title|None|None"],
        "episodes|show title|None|None",
    ),
]


@pytest.mark.parametrize(("uniqueid", "expected"), CW_IDS)
def test_normalize_ids_matches_cw(uniqueid: dict[str, str], expected: dict[str, str]) -> None:
    assert normalize_ids(uniqueid) == expected


def test_normalize_ids_sorts_keys() -> None:
    # Insertion order must not decide which of two tmdb aliases wins.
    assert normalize_ids({"tmdb": "551", "themoviedb": "550"}) == {"tmdb": "550"}


def test_normalize_ids_ignores_non_mapping() -> None:
    assert normalize_ids(None) == {}
    assert normalize_ids(["tt1"]) == {}


def test_kind_of() -> None:
    assert items.kind_of("movie") == "movies"
    assert items.kind_of("episode") == "episodes"


@pytest.mark.parametrize(("kind", "row", "show", "tokens", "identity"), CW_ROWS)
def test_tokens_and_identity_match_cw(
    kind: items.ItemKind, row: dict[str, Any], show: dict[str, Any] | None, tokens: list[str], identity: str
) -> None:
    assert items.tokens(kind, row, show) == tokens
    assert items.identity(kind, row, show) == identity


def test_embedded_show_wins_over_lookup() -> None:
    row = {"uniqueid": {}, "season": 1, "episode": 2, "show": {"uniqueid": {"tvdb": "5"}}}
    assert items.tokens("episodes", row, BREAKING_BAD) == ["episodes|show:tvdb:5|1|2"]


def test_movie_and_episode_tokens_never_collide() -> None:
    ids = {"tmdb": "550"}
    movie = set(items.tokens("movies", {"uniqueid": ids}))
    episode = set(items.tokens("episodes", {"uniqueid": ids, "season": 1, "episode": 1}))
    assert movie.isdisjoint(episode)


def test_keep_show_ids_matches_cw() -> None:
    new = {
        1: {"uniqueid": {}, "title": "Lost"},
        2: {"uniqueid": {}, "title": "Other"},
        3: {"uniqueid": {"tvdb": "9"}, "title": "X"},
    }
    old = {
        1: {"uniqueid": {"tvdb": "73739"}, "title": "lost"},
        2: {"uniqueid": {"tvdb": "5"}, "title": "Different"},
        3: {"uniqueid": {"tvdb": "8"}, "title": "X"},
    }
    items.keep_show_ids(new, old)
    assert new == {
        1: {"uniqueid": {"tvdb": "73739"}, "title": "Lost"},
        2: {"uniqueid": {}, "title": "Other"},
        3: {"uniqueid": {"tvdb": "9"}, "title": "X"},
    }


def test_server_backed() -> None:
    assert items.is_server_backed("plugin://plugin.video.plexkodiconnect/movies/1")
    assert not items.is_server_backed("smb://nas/movies/x.mkv")


def test_episode_properties_have_no_year() -> None:
    assert "year" not in items.EPISODE_PROPERTIES
    assert "year" in items.MOVIE_PROPERTIES


# Library reads


def _utc(local: str) -> str:
    return f"utc({local})" if local else ""


def _library() -> FakeLibrary:
    lib = FakeLibrary()
    lib.add_movie(1, title="Fight Club", year=1999, uniqueid={"imdb": "tt0137523"}, file="smb://u:p@nas/m/fc.mkv",
                  lastplayed="2026-03-01 20:00:00")  # fmt: skip
    lib.add_movie(2, title="Plex Film", file="plugin://plugin.video.plexkodiconnect/movies/9")
    lib.add_show(7, **BREAKING_BAD)
    lib.add_episode(10, 7, 1, 2, file="nfs://nas/tv/bb/s01e02.mkv")
    lib.sources = [{"file": "smb://u:p@nas/m/", "label": "Movies"}]
    return lib


def _read(
    lib: FakeLibrary, *, may_continue: Any = lambda: True, page: int = 500, previous: Any = None
) -> Library | None:
    kodi = FakeKodi(rpc_handlers=lib.handlers())
    return read_library(kodi, "lib1", may_continue=may_continue, previous_shows=previous or {}, to_utc=_utc, page=page)


def test_read_library_rows_tokens_and_keys() -> None:
    library = _read(_library())
    assert library is not None and library.complete
    [movie] = library.movies
    assert movie.key == items.RowKey("lib1", "movies", 1, "smb://nas/m/fc.mkv")
    assert movie.tokens == ("movies|imdb:tt0137523",)
    [episode] = library.episodes
    assert episode.show_kodi_id == 7
    assert episode.tokens == ("episodes|show:tmdb:1396|1|2", "episodes|show:tvdb:81189|1|2")
    assert set(library.tvshows) == {7}


def test_read_library_skips_server_backed() -> None:
    library = _read(_library())
    assert library is not None
    assert [r.key.kodi_id for r in library.movies] == [1]
    assert library.server_backed == frozenset({("movies", 2)})
    # Filtering a server-backed row is not a change under the read.
    assert library.complete


def test_read_library_strips_files_and_sources() -> None:
    library = _read(_library())
    assert library is not None
    assert library.movies[0].raw["file"] == "smb://nas/m/fc.mkv"
    assert library.sources == [{"file": "smb://nas/m/", "label": "Movies"}]


def test_read_library_dates_utc() -> None:
    library = _read(_library())
    assert library is not None
    assert library.movies[0].raw["lastplayed"] == "utc(2026-03-01 20:00:00)"
    assert library.movies[0].raw["dateadded"] == "utc(2026-01-01 10:00:00)"


def test_read_library_pages() -> None:
    lib = _library()
    for n in range(3, 8):
        lib.add_movie(n, title=f"M{n}", file=f"smb://nas/m/{n}.mkv")
    library = _read(lib, page=2)
    assert library is not None and library.complete
    assert [r.key.kodi_id for r in library.movies] == [1, 3, 4, 5, 6, 7]


def test_read_library_stops() -> None:
    calls = iter([True, True, False])
    assert _read(_library(), may_continue=lambda: next(calls, False)) is None


def test_read_library_incomplete_when_total_changes() -> None:
    lib = _library()
    for n in range(3, 6):
        lib.add_movie(n, file=f"smb://nas/m/{n}.mkv")

    def grow(method: str, start: int) -> None:
        if method == "VideoLibrary.GetMovies" and start == 2:
            lib.add_movie(99, file="smb://nas/m/99.mkv")

    lib.before_page = grow
    library = _read(lib, page=2)
    assert library is not None and not library.complete


def test_read_library_incomplete_when_id_set_differs() -> None:
    lib = _library()
    for n in range(3, 6):
        lib.add_movie(n, file=f"smb://nas/m/{n}.mkv")

    def swap(method: str, start: int) -> None:
        # Another box removes a row already read and adds one: the total holds still.
        if method == "VideoLibrary.GetMovies" and start == 2:
            lib.movies.pop(0)
            lib.add_movie(99, file="smb://nas/m/99.mkv")

    lib.before_page = swap
    library = _read(lib, page=2)
    assert library is not None and not library.complete


def test_read_library_keeps_show_ids() -> None:
    lib = _library()
    lib.tvshows[0]["uniqueid"] = {}
    library = _read(lib, previous={7: BREAKING_BAD})
    assert library is not None
    assert library.tvshows[7]["uniqueid"] == BREAKING_BAD["uniqueid"]
    assert "episodes|show:tvdb:81189|1|2" in library.episodes[0].tokens


def test_fake_pages_like_kodi() -> None:
    lib = _library()
    reply = lib.handlers()["VideoLibrary.GetMovies"]({"properties": ["title"], "limits": {"start": 1, "end": 5}})
    assert reply["limits"] == {"start": 1, "end": 2, "total": 2}
    assert reply["movies"] == [{"movieid": 2, "label": "Plex Film", "title": "Plex Film"}]
    ids = lib.handlers()["VideoLibrary.GetMovies"]({"properties": []})
    assert ids["movies"] == [{"movieid": 1, "label": "Fight Club"}, {"movieid": 2, "label": "Plex Film"}]


def test_read_library_continues_past_an_empty_locked_page() -> None:
    lib = _library()
    for n in range(3, 8):
        lib.add_movie(n, file=f"smb://nas/m/{n}.mkv")
    # Movies 3 and 4 make up the second page of two; both sit in a locked source.
    lib.locked = {3, 4}
    library = _read(lib, page=2)
    assert library is not None and library.complete
    assert [r.key.kodi_id for r in library.movies] == [1, 5, 6, 7]


def test_read_library_incomplete_when_an_episode_s_show_was_not_read() -> None:
    lib = _library()

    def add_show(method: str, start: int) -> None:
        # A show and its episode arrive after the shows were read.
        if method == "VideoLibrary.GetEpisodes" and start == 0 and len(lib.tvshows) == 1:
            lib.add_show(8, uniqueid={"tvdb": "73739"}, title="Lost")
            lib.add_episode(11, 8, 1, 1, file="nfs://nas/tv/lost/s01e01.mkv")

    lib.before_page = add_show
    library = _read(lib)
    assert library is not None and not library.complete
