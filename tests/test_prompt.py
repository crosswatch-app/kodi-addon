from dataclasses import replace

from resources.lib.advanced_settings import Thresholds
from resources.lib.constants import PROMPT_EVERYONE, PROMPT_HEADING, PROMPT_HEADING_UNTITLED
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


EVERYONE = 0  # the Everyone row sits above the viewer names


def test_ask_offers_everyone_above_the_viewers_in_configuration_order():
    kodi = FakeKodi(multiselect_answer=None)
    ask(kodi, [ANNA, BOB], _media(), autoclose=120)
    assert kodi.multiselect_calls[0][1] == [f"#{PROMPT_EVERYONE}", "anna", "bob"]


def test_ask_names_the_show_in_a_localised_heading():
    kodi = FakeKodi(multiselect_answer=None)
    ask(kodi, [ANNA, BOB], _media(), autoclose=120)
    assert kodi.multiselect_calls[0][0] == f"#{PROMPT_HEADING}"


def test_ask_falls_back_to_a_heading_without_a_title():
    kodi = FakeKodi(multiselect_answer=None)
    ask(kodi, [ANNA, BOB], replace(_media(), title=""), autoclose=120)
    assert kodi.multiselect_calls[0][0] == f"#{PROMPT_HEADING_UNTITLED}"


def test_ask_returns_the_selected_viewers():
    assert ask(FakeKodi(multiselect_answer=[2]), [ANNA, BOB], _media(), autoclose=120) == ("bob",)


def test_ask_supports_two_people_watching_together():
    assert ask(FakeKodi(multiselect_answer=[1, 2]), [ANNA, BOB], _media(), autoclose=120) == ("anna", "bob")


def test_everyone_means_every_configured_viewer():
    carol = Viewer(name="carol")
    answer = ask(FakeKodi(multiselect_answer=[EVERYONE]), [ANNA, BOB, carol], _media(), autoclose=120)
    assert answer == ("anna", "bob", "carol")


def test_everyone_wins_over_individual_ticks():
    carol = Viewer(name="carol")
    answer = ask(FakeKodi(multiselect_answer=[EVERYONE, 2]), [ANNA, BOB, carol], _media(), autoclose=120)
    assert answer == ("anna", "bob", "carol")


def test_ask_passes_the_autoclose_so_an_abandoned_dialog_cannot_park_the_thread():
    kodi = FakeKodi(multiselect_answer=[1])
    ask(kodi, [ANNA, BOB], _media(), autoclose=90)
    assert kodi.multiselect_calls[0][3] == 90


def test_a_dismissed_dialog_returns_no_viewers():
    assert ask(FakeKodi(multiselect_answer=None), [ANNA, BOB], _media(), autoclose=120) == ()


def test_confirming_with_nothing_ticked_returns_no_viewers():
    assert ask(FakeKodi(multiselect_answer=[]), [ANNA, BOB], _media(), autoclose=120) == ()


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


def test_the_picker_preticks_the_given_viewers_below_the_everyone_row():
    kodi = FakeKodi(multiselect_answer=None)
    choose_viewers(kodi, "heading", [ANNA, BOB], preselect=("bob",))
    assert kodi.multiselect_calls[0][2] == [2]


def test_the_picker_tells_cancel_apart_from_nobody_ticked():
    """Cancel leaves an answer alone; OK with nobody ticked is a deliberate 'forget'."""
    assert choose_viewers(FakeKodi(multiselect_answer=None), "h", [ANNA, BOB]) is None
    assert choose_viewers(FakeKodi(multiselect_answer=[]), "h", [ANNA, BOB]) == ()


def test_the_picker_expands_everyone_like_the_prompt():
    assert choose_viewers(FakeKodi(multiselect_answer=[EVERYONE]), "h", [ANNA, BOB]) == ("anna", "bob")


def test_key_for_show_matches_show_key():
    """The settings screen builds keys from the library; they must equal the prompt's."""
    assert key_for_show({"tmdb": "1", "tvdb": "83462"}, 42) == show_key(_media()) == "show:tvdb:83462"
    assert key_for_show({}, 42) == show_key(_media(show_ids={})) == "tvshow:42"
    assert key_for_show({}, None) is None
