from resources.lib.constants import WHO_WATCHED_EPISODE
from resources.lib.kodi import KodiRpcError
from resources.lib.ui.who_watched import (
    EVERYONE_ROW,
    WhoWatchedRequest,
    answer,
    episode_subtitle,
    everyone,
    initial,
    movie_poster,
    show_poster,
    toggle,
    year_subtitle,
)
from tests.fakes import FakeKodi

NAMES = ("anna", "bob", "carol")


def _request(**kwargs) -> WhoWatchedRequest:
    return WhoWatchedRequest(title="Example", subtitle="", poster="", names=NAMES, **kwargs)


def test_initial_ticks_the_preselected_names():
    assert initial(_request(preselect=("bob",))) == {"bob"}


def test_initial_drops_names_not_configured():
    """A remembered answer can name a viewer since removed from the configuration."""
    assert initial(_request(preselect=("bob", "dave"))) == {"bob"}


def test_toggle_flips_one_viewer():
    assert toggle(NAMES, frozenset(), 2) == {"bob"}
    assert toggle(NAMES, frozenset({"bob"}), 2) == frozenset()


def test_toggle_everyone_ticks_all_then_none():
    all_ticked = toggle(NAMES, frozenset({"anna"}), EVERYONE_ROW)
    assert all_ticked == set(NAMES)
    assert toggle(NAMES, all_ticked, EVERYONE_ROW) == frozenset()


def test_toggle_ignores_a_row_out_of_range():
    assert toggle(NAMES, frozenset({"anna"}), 9) == {"anna"}
    assert toggle(NAMES, frozenset({"anna"}), -1) == {"anna"}


def test_everyone_shows_ticked_only_when_every_viewer_is():
    assert everyone(NAMES, frozenset(NAMES)) is True
    assert everyone(NAMES, frozenset({"anna", "bob"})) is False
    assert everyone((), frozenset()) is False


def test_answer_is_in_configuration_order():
    assert answer(NAMES, frozenset({"carol", "anna"})) == ("anna", "carol")
    assert answer(NAMES, frozenset()) == ()


def test_episode_subtitle_fills_season_then_episode():
    class Localised(FakeKodi):
        def localised(self, string_id: int) -> str:
            return "Season %s, episode %s" if string_id == WHO_WATCHED_EPISODE else ""

    assert episode_subtitle(Localised(), 2, 5) == "Season 2, episode 5"


def test_episode_subtitle_is_empty_without_numbers():
    assert episode_subtitle(FakeKodi(), None, 5) == ""
    assert episode_subtitle(FakeKodi(), 2, None) == ""


def test_episode_subtitle_survives_a_translation_without_placeholders():
    class Localised(FakeKodi):
        def localised(self, string_id: int) -> str:
            return "Aflevering"

    assert episode_subtitle(Localised(), 2, 5) == "Aflevering"


def test_year_subtitle():
    assert year_subtitle(2008) == "2008"
    assert year_subtitle(None) == ""
    assert year_subtitle(0) == ""


def test_show_poster_reads_the_shows_art():
    kodi = FakeKodi(rpc_handlers={
        "VideoLibrary.GetTVShowDetails": lambda p: {"tvshowdetails": {"art": {"poster": "image://p/"}}}
    })
    assert show_poster(kodi, 7) == "image://p/"
    assert kodi.calls == [("VideoLibrary.GetTVShowDetails", {"tvshowid": 7, "properties": ["art"]})]


def test_movie_poster_reads_the_films_art():
    kodi = FakeKodi(rpc_handlers={
        "VideoLibrary.GetMovieDetails": lambda p: {"moviedetails": {"art": {"poster": "image://m/"}}}
    })
    assert movie_poster(kodi, 3) == "image://m/"
    assert kodi.calls[0] == ("VideoLibrary.GetMovieDetails", {"movieid": 3, "properties": ["art"]})


def test_show_poster_without_id_is_empty():
    kodi = FakeKodi()
    assert show_poster(kodi, None) == ""
    assert kodi.calls == []


def test_a_poster_lookup_error_gives_an_empty_poster():
    def fail(params):
        raise KodiRpcError("VideoLibrary.GetTVShowDetails", {"code": -32602})

    assert show_poster(FakeKodi(rpc_handlers={"VideoLibrary.GetTVShowDetails": fail}), 7) == ""


def test_no_poster_art_gives_an_empty_poster():
    kodi = FakeKodi(rpc_handlers={"VideoLibrary.GetTVShowDetails": lambda p: {"tvshowdetails": {"art": {}}}})
    assert show_poster(kodi, 7) == ""
