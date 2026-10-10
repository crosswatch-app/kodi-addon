from resources.lib.constants import RATING_QUESTION_HOUSEHOLD, RATING_QUESTION_NAMED, WINDOW_COUNT_SOME
from resources.lib.ui.rating import RATE_FAILED, SCORE_WORDS, counter_text, question_text

TEXTS = {RATING_QUESTION_NAMED: "%s, how was it?", RATING_QUESTION_HOUSEHOLD: "How was it?", WINDOW_COUNT_SOME: "%s of %s"}


def loc(string_id: int) -> str:
    return TEXTS.get(string_id, "")


def test_a_named_question_puts_the_viewer_in_front():
    assert question_text(loc, "Anna") == "Anna, how was it?"


def test_a_household_question_has_no_name():
    assert question_text(loc, None) == "How was it?"


def test_the_counter_shows_only_for_a_run_of_questions():
    assert counter_text(loc, 1, 1) == ""
    assert counter_text(loc, 2, 3) == "2 of 3"


def test_there_is_one_word_for_every_score():
    assert len(SCORE_WORDS) == 10 and len(set(SCORE_WORDS)) == 10


def test_a_translation_without_placeholders_does_not_raise():
    assert question_text(lambda _: "Rate it", "Anna") == "Rate it"
    assert counter_text(lambda _: "Next", 2, 3) == "Next"


def test_a_failed_window_is_not_a_score():
    assert RATE_FAILED not in range(0, 11)
