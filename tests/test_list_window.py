from resources.lib.constants import WINDOW_COUNT_ALL, WINDOW_COUNT_ONE, WINDOW_COUNT_SOME
from resources.lib.ui.list_window import ListFilter, ListRequest, ListRow, bulk_label, count_text, visible

ALPHA = ListRow(key="a", title="Alpha", names=("anna",))
BETA = ListRow(key="b", title="Beta Annals", names=("bob",))
GAMMA = ListRow(key="g", title="Gamma", names=())
ROWS = [ALPHA, BETA, GAMMA]
EVERYONE = ListFilter("All", lambda row: True)
BOBS = ListFilter("bob", lambda row: "bob" in row.names)

TEXTS = {WINDOW_COUNT_ALL: "%s shows", WINDOW_COUNT_ONE: "1 show", WINDOW_COUNT_SOME: "%s of %s"}


def test_no_search_and_no_filter_shows_everything_in_order():
    assert visible(ROWS, "", None) == ROWS


def test_search_matches_titles_case_insensitively_and_trimmed():
    assert visible(ROWS, "  gAm ", None) == [GAMMA]


def test_search_matches_viewer_names():
    assert visible(ROWS, "ann", None) == [ALPHA, BETA]  # anna, and "Annals" in a title


def test_a_filter_narrows_the_rows():
    assert visible(ROWS, "", BOBS) == [BETA]


def test_search_and_filter_combine():
    assert visible(ROWS, "alpha", BOBS) == []
    assert visible(ROWS, "beta", BOBS) == [BETA]


def test_count_text_without_narrowing():
    assert count_text(lambda i: TEXTS[i], 3, 3) == "3 shows"


def test_count_text_when_narrowed():
    assert count_text(lambda i: TEXTS[i], 1, 3) == "1 of 3"


def test_count_text_survives_a_translation_without_placeholders():
    assert count_text(lambda i: "Shows", 1, 3) == "Shows"


def test_bulk_label_reads_all_until_the_rows_are_narrowed():
    request = ListRequest("Remembered", tuple(ROWS), (EVERYONE, BOBS), "Forget all", "Forget %s shown")
    assert bulk_label(request, 3) == "Forget all"
    assert bulk_label(request, 1) == "Forget 1 shown"


def test_count_text_for_a_single_show_is_singular():
    """'1 shows' reads wrongly; the same fix the forget confirmation needed on a real Kodi."""
    assert count_text(lambda i: TEXTS[i], 1, 1) == "1 show"
