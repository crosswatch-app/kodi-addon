import pytest

from resources.lib.config import Settings
from resources.lib.kodi import WINDOW_INVALID
from resources.lib.models import MediaItem, Viewer
from resources.lib.rating import Question, RatingQueue, Watch, may_ask, questions, rateable, single_score

MOVIE = MediaItem(
    media_type="movie",
    library_id=7,
    show_library_id=None,
    title="Example film",
    year=2024,
    season=None,
    episode=None,
    episode_title=None,
    show_ids={"tmdb": "999"},
)
EPISODE = MediaItem(
    media_type="episode",
    library_id=5,
    show_library_id=42,
    title="Example show",
    year=2026,
    season=1,
    episode=2,
    episode_title=None,
    show_ids={"tvdb": "1"},
)
VIEWERS = [Viewer("Anna"), Viewer("Ben")]


def watch(viewers: tuple[str, ...] = ("Anna",), declined: bool = False, session_id: str = "s-1") -> Watch:
    return Watch(session_id=session_id, media=EPISODE, viewers=viewers, declined=declined)


def test_only_a_completed_watch_of_an_enabled_type_with_an_id_is_rateable():
    films = Settings(rating_kinds=frozenset({"movie"}))
    assert rateable(films, MOVIE, completes_watch=True, has_usable_id=True)
    assert not rateable(films, EPISODE, completes_watch=True, has_usable_id=True)
    assert not rateable(films, MOVIE, completes_watch=False, has_usable_id=True)
    assert not rateable(films, MOVIE, completes_watch=True, has_usable_id=False)
    assert not rateable(Settings(), MOVIE, completes_watch=True, has_usable_id=True)


@pytest.mark.parametrize(
    ("declined", "dialog_id", "shutting_down", "playing", "reason"),
    [
        (True, WINDOW_INVALID, False, False, "declined"),
        (False, WINDOW_INVALID, True, False, "shutting_down"),
        (False, 10100, False, False, "dialog_busy"),
        (False, WINDOW_INVALID, False, True, "playing"),
    ],
)
def test_may_ask_refuses_with_a_reason(declined, dialog_id, shutting_down, playing, reason):
    assert may_ask(watch(declined=declined), dialog_id, shutting_down, playing) == reason


def test_may_ask_allows_a_quiet_moment():
    assert may_ask(watch(), WINDOW_INVALID, shutting_down=False, playing=False) == ""


def test_each_credited_viewer_gets_their_own_question():
    got = questions(watch(("Anna", "Ben")), VIEWERS, shared=False)
    assert got == [Question("Anna", ("Anna",)), Question("Ben", ("Ben",))]


def test_shared_rating_asks_once_for_everyone():
    assert questions(watch(("Anna", "Ben")), VIEWERS, shared=True) == [Question(None, ("Anna", "Ben"))]


def test_shared_rating_with_one_viewer_still_names_nobody_but_rates_them():
    assert questions(watch(("Anna",)), VIEWERS, shared=True) == [Question(None, ("Anna",))]


def test_a_watch_nobody_was_credited_for_asks_the_household_and_rates_nobody():
    assert questions(watch(()), VIEWERS, shared=False) == [Question(None, ())]
    assert questions(watch(()), VIEWERS, shared=True) == [Question(None, ())]


def test_viewers_removed_since_the_stop_are_not_asked():
    assert questions(watch(("Anna", "Gone")), [Viewer("Anna")], shared=False) == [Question("Anna", ("Anna",))]
    assert questions(watch(("Gone",)), [Viewer("Anna")], shared=False) == []
    assert questions(watch(("Gone",)), [Viewer("Anna")], shared=True) == []


def test_names_match_case_insensitively_and_keep_the_configured_spelling():
    assert questions(watch(("anna",)), VIEWERS, shared=False) == [Question("Anna", ("Anna",))]


@pytest.mark.parametrize(
    ("scores", "single"),
    [([8], 8), ([8, 8], 8), ([0], 0), ([], None), ([8, 7], None), ([8, 0], None)],
)
def test_single_score(scores, single):
    assert single_score(scores) == single


def test_the_queue_hands_watches_out_newest_first_and_empties():
    queue = RatingQueue()
    first, second = watch(session_id="s-1"), watch(session_id="s-2")
    queue.add(first)
    queue.add(second)
    assert len(queue) == 2
    assert queue.take_all() == [second, first]
    assert len(queue) == 0 and queue.take_all() == []


def test_clearing_the_queue_reports_how_many_were_dropped():
    queue = RatingQueue()
    queue.add(watch())
    queue.add(watch(session_id="s-2"))
    assert queue.clear() == 2
    assert len(queue) == 0 and queue.clear() == 0


def test_a_rename_reaches_every_queued_watch_naming_the_viewer():
    queue = RatingQueue()
    queue.add(watch(session_id="s-1", viewers=("anna", "bob")))
    queue.add(watch(session_id="s-2", viewers=("bob",)))
    queue.rename_viewer("anna", "Annie")
    assert [w.viewers for w in queue.take_all()] == [("bob",), ("Annie", "bob")]
