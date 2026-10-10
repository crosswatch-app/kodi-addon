from dataclasses import replace

from resources.lib.advanced_settings import Thresholds
from resources.lib.constants import WHO_WATCHED_EPISODE
from resources.lib.identity import UNRESOLVED, Identity
from resources.lib.kodi import WINDOW_INVALID
from resources.lib.models import MediaItem, MediaType, Viewer
from resources.lib.prompt import GateDecision, ask, choose_viewers, gate, key_for_show, recall, remember, show_key
from resources.lib.storage import PromptMemory, RememberedAnswer
from tests.fakes import FakeKodi

ANNA = Viewer(name="anna")
BOB = Viewer(name="bob")
THRESHOLDS = Thresholds()
PAST_START = 200_000


def _media(
    media_type: MediaType = "episode",
    show_library_id: int | None = 42,
    show_ids: dict[str, str] | None = None,
    title: str = "Example",
    show_year: int | None = 2008,
    year: int | None = 2026,
) -> MediaItem:
    return MediaItem(
        media_type=media_type,
        library_id=5,
        show_library_id=show_library_id if media_type == "episode" else None,
        title=title,
        year=year,
        season=1,
        episode=1,
        episode_title=None,
        show_ids=show_ids if show_ids is not None else {"tvdb": "83462"},
        show_year=show_year,
    )


def _gate(
    tmp_path,
    identity: Identity = UNRESOLVED,
    viewers: list[Viewer] | None = None,
    media: MediaItem | None = None,
    position_ms: int | None = PAST_START,
    thresholds: Thresholds = THRESHOLDS,
    memory: PromptMemory | None = None,
    movie_prompts_enabled: bool = True,
    dialog_id: int = WINDOW_INVALID,
    shutting_down: bool = False,
) -> GateDecision:
    # Explicit parameters rather than a dict of defaults unpacked into gate(): unpacking a
    # heterogeneous dict erases every argument's type, so the checker can no longer tell a
    # malformed call from a real one.
    return gate(
        identity=identity,
        viewers=[ANNA, BOB] if viewers is None else viewers,
        media=_media() if media is None else media,
        position_ms=position_ms,
        thresholds=thresholds,
        memory=PromptMemory(str(tmp_path / "prompts.json")) if memory is None else memory,
        movie_prompts_enabled=movie_prompts_enabled,
        dialog_id=dialog_id,
        shutting_down=shutting_down,
    )


def test_show_key_prefers_a_stable_unique_id():
    assert show_key(_media()) == "show:tvdb:83462"


def test_show_key_is_stable_across_id_ordering():
    assert show_key(_media(show_ids={"tvdb": "83462", "imdb": "tt1"})) == show_key(_media(show_ids={"imdb": "tt1", "tvdb": "83462"}))


def test_show_key_falls_back_to_the_library_id():
    assert show_key(_media(show_ids={})) == "tvshow:42"


def test_show_key_is_none_for_a_movie():
    assert show_key(_media(media_type="movie")) is None


def test_asks_when_unresolved_and_past_the_start_threshold(tmp_path):
    assert _gate(tmp_path).ask is True


def test_does_not_ask_when_identity_is_resolved(tmp_path):
    assert _gate(tmp_path, identity=Identity(("anna",), "playlist")).reason == "resolved"


def test_does_not_ask_with_no_viewers_configured(tmp_path):
    assert _gate(tmp_path, viewers=[]).reason == "no_viewers_configured"


def test_does_not_ask_with_a_single_viewer(tmp_path):
    assert _gate(tmp_path, viewers=[ANNA]).reason == "single_viewer"


def test_does_not_ask_below_kodis_start_threshold(tmp_path):
    assert _gate(tmp_path, position_ms=60_000).reason == "below_ignore_seconds_at_start"


def test_honours_a_changed_start_threshold(tmp_path):
    assert _gate(tmp_path, position_ms=60_000, thresholds=Thresholds(ignore_seconds_at_start=30)).ask is True


def test_an_unknown_position_does_not_ask(tmp_path):
    assert _gate(tmp_path, position_ms=None).reason == "below_ignore_seconds_at_start"


def test_uses_a_remembered_answer_without_asking(tmp_path):
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    memory.remember("show:tvdb:83462", ("bob",))
    decision = _gate(tmp_path, memory=memory)
    assert (decision.ask, decision.reason, decision.remembered) == (False, "remembered", ("bob",))


def test_a_remembered_name_that_is_no_longer_a_viewer_is_discarded(tmp_path):
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    memory.remember("show:tvdb:83462", ("carol",))
    decision = _gate(tmp_path, memory=memory)
    assert decision.ask is True
    assert decision.remembered == ()


def test_a_partially_stale_memory_keeps_the_names_that_still_exist(tmp_path):
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    memory.remember("show:tvdb:83462", ("anna", "carol"))
    decision = _gate(tmp_path, memory=memory)
    assert (decision.ask, decision.remembered) == (False, ("anna",))


def test_does_not_ask_while_another_dialog_is_open(tmp_path):
    assert _gate(tmp_path, dialog_id=10138).reason == "dialog_busy"


def test_does_not_ask_while_shutting_down(tmp_path):
    assert _gate(tmp_path, shutting_down=True).reason == "shutting_down"


def test_does_not_ask_for_a_movie_when_movie_prompts_are_off(tmp_path):
    assert _gate(tmp_path, media=_media("movie"), movie_prompts_enabled=False).reason == "movie_prompt_disabled"


def test_asks_for_a_movie_when_movie_prompts_are_on(tmp_path):
    assert _gate(tmp_path, media=_media("movie")).ask is True


def _poster_kodi(answer=None) -> FakeKodi:
    return FakeKodi(
        who_watched_answer=answer,
        rpc_handlers={
            "VideoLibrary.GetTVShowDetails": lambda p: {"tvshowdetails": {"art": {"poster": "image://show/"}}},
            "VideoLibrary.GetMovieDetails": lambda p: {"moviedetails": {"art": {"poster": "image://film/"}}},
        },
    )


def test_ask_offers_the_viewers_in_configuration_order():
    kodi = _poster_kodi()
    ask(kodi, [ANNA, BOB], _media(), autoclose=120)
    assert kodi.who_watched_calls[0].names == ("anna", "bob")


def test_ask_for_an_episode_shows_the_show_its_numbers_and_poster():
    kodi = _poster_kodi()
    ask(kodi, [ANNA, BOB], _media(), autoclose=120)
    request = kodi.who_watched_calls[0]
    assert request.title == "Example"
    assert request.subtitle == f"#{WHO_WATCHED_EPISODE}"  # FakeKodi.localised has no placeholders
    assert request.poster == "image://show/"
    assert kodi.calls[0] == ("VideoLibrary.GetTVShowDetails", {"tvshowid": 42, "properties": ["art"]})


def test_ask_for_a_film_shows_its_year_and_poster():
    kodi = _poster_kodi()
    ask(kodi, [ANNA, BOB], _media("movie"), autoclose=120)
    request = kodi.who_watched_calls[0]
    assert request.subtitle == "2026"
    assert request.poster == "image://film/"


def test_ask_without_a_title_shows_an_empty_title():
    kodi = _poster_kodi()
    ask(kodi, [ANNA, BOB], replace(_media(), title=None), autoclose=120)
    assert kodi.who_watched_calls[0].title == ""


def test_ask_counts_down_and_closes_when_playback_starts():
    kodi = _poster_kodi()
    ask(kodi, [ANNA, BOB], _media(), autoclose=90)
    request = kodi.who_watched_calls[0]
    assert request.autoclose_seconds == 90
    assert request.close_on_playback is True
    assert request.preselect == ()


def test_ask_returns_the_chosen_viewers():
    assert ask(_poster_kodi(("bob",)), [ANNA, BOB], _media(), autoclose=120) == ("bob",)


def test_ask_supports_two_people_watching_together():
    assert ask(_poster_kodi(("anna", "bob")), [ANNA, BOB], _media(), autoclose=120) == ("anna", "bob")


def test_a_dismissed_window_returns_no_viewers():
    assert ask(_poster_kodi(None), [ANNA, BOB], _media(), autoclose=120) == ()


def test_done_with_nobody_ticked_returns_no_viewers():
    assert ask(_poster_kodi(()), [ANNA, BOB], _media(), autoclose=120) == ()


def test_choose_viewers_passes_preselect_and_no_countdown_by_default():
    kodi = FakeKodi(who_watched_answer=None)
    choose_viewers(kodi, [ANNA, BOB], title="Example", subtitle="2008", poster="", preselect=("bob",))
    request = kodi.who_watched_calls[0]
    assert request.preselect == ("bob",)
    assert request.autoclose_seconds == 0
    assert request.close_on_playback is False


def test_choose_viewers_tells_cancel_apart_from_nobody_ticked():
    """Cancel leaves an answer alone; Done with nobody ticked is a deliberate 'forget'."""
    assert choose_viewers(FakeKodi(who_watched_answer=None), [ANNA, BOB], "t", "", "") is None
    assert choose_viewers(FakeKodi(who_watched_answer=()), [ANNA, BOB], "t", "", "") == ()


def test_remember_stores_under_the_stable_key_with_the_shows_title_and_year(tmp_path):
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    remember(memory, _media(), ("anna",))
    assert memory.recall("show:tvdb:83462") == RememberedAnswer(viewers=("anna",), title="Example", year=2008)


def test_recall_returns_the_answer_for_a_show_with_a_scraper_id(tmp_path):
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    remember(memory, _media(), ("anna",))
    assert recall(memory, _media()) == ("anna",)


def _no_ids(**kwargs) -> MediaItem:
    return _media(show_ids={}, **kwargs)


def test_a_show_without_scraper_ids_is_remembered_by_library_id_and_checked(tmp_path):
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    remember(memory, _no_ids(), ("anna",))
    assert recall(memory, _no_ids()) == ("anna",)


def test_a_library_id_now_holding_another_title_is_not_trusted(tmp_path):
    """A library clean can hand a show's database id to a different show."""
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    remember(memory, _no_ids(), ("anna",))
    assert recall(memory, _no_ids(title="Another Show")) == ()


def test_a_library_id_now_holding_another_year_is_not_trusted(tmp_path):
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    remember(memory, _no_ids(), ("anna",))
    assert recall(memory, _no_ids(show_year=1999)) == ()


def test_a_later_season_still_matches_because_the_check_uses_the_shows_year(tmp_path):
    """Kodi reports an episode's own year during playback, which changes per season."""
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    remember(memory, _no_ids(year=2008), ("anna",))
    assert recall(memory, _no_ids(year=2011)) == ("anna",)


def test_a_library_id_answer_without_a_title_cannot_be_checked_and_is_ignored(tmp_path):
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    memory.remember("tvshow:42", ("anna",))
    assert recall(memory, _no_ids()) == ()


def test_remember_does_nothing_for_a_movie(tmp_path):
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    remember(memory, _media("movie"), ("anna",))
    assert memory.recall("tvshow:42") is None


def test_key_for_show_matches_show_key():
    """The settings screen builds keys from the library; they must equal the prompt's."""
    assert key_for_show({"tmdb": "1", "tvdb": "83462"}, 42) == show_key(_media()) == "show:tvdb:83462"
    assert key_for_show({}, 42) == show_key(_media(show_ids={})) == "tvshow:42"
    assert key_for_show({}, None) is None


def test_the_question_at_the_end_never_offers_forget():
    kodi = _poster_kodi()
    ask(kodi, [ANNA, BOB], _media(), autoclose=120)
    assert kodi.who_watched_calls[0].offer_forget is False


def test_choose_viewers_passes_offer_forget():
    kodi = FakeKodi(who_watched_answer=None)
    choose_viewers(kodi, [ANNA, BOB], "t", "", "", offer_forget=True)
    assert kodi.who_watched_calls[0].offer_forget is True
