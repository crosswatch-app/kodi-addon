import pytest

from resources.lib.constants import WINDOW_COUNT_SOME
from resources.lib.ui.list_window import (
    ListFilter,
    ListRequest,
    ListRow,
    bulk_label,
    count_text,
    picked,
    toggle,
    visible,
)

ALPHA = ListRow(key="a", title="Alpha", names=("anna",))
BETA = ListRow(key="b", title="Beta Annals", names=("bob",))
GAMMA = ListRow(key="g", title="Gamma", names=())
ROWS = [ALPHA, BETA, GAMMA]
EVERYONE = ListFilter("All", lambda row: True)
BOBS = ListFilter("bob", lambda row: "bob" in row.names)


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


TEXTS = {WINDOW_COUNT_SOME: "%s of %s"}
REQUEST = ListRequest(
    heading="Remembered",
    rows=tuple(ROWS),
    count_one="1 show",
    count_all="%s shows",
    filters=(EVERYONE, BOBS),
    bulk_all="Forget all",
    bulk_shown="Forget %s shown",
)


def test_count_text_without_narrowing():
    assert count_text(REQUEST, lambda i: TEXTS[i], 3) == "3 shows"


def test_count_text_when_narrowed():
    assert count_text(REQUEST, lambda i: TEXTS[i], 1) == "1 of 3"


def test_count_text_survives_a_translation_without_placeholders():
    assert count_text(REQUEST, lambda i: "Shows", 1) == "Shows"


def test_count_text_for_a_single_row_is_singular():
    """'1 shows' reads wrongly; the same fix the forget confirmation needed on a real Kodi."""
    one = ListRequest(heading="h", rows=(ALPHA,), count_one="1 show", count_all="%s shows")
    assert count_text(one, lambda i: TEXTS[i], 1) == "1 show"


def test_count_text_uses_the_screens_own_wording():
    playlists = ListRequest(heading="h", rows=tuple(ROWS), count_one="1 playlist", count_all="%s playlists")
    assert count_text(playlists, lambda i: TEXTS[i], 3) == "3 playlists"


def test_bulk_label_reads_all_until_the_rows_are_narrowed():
    assert bulk_label(REQUEST, 3) == "Forget all"
    assert bulk_label(REQUEST, 1) == "Forget 1 shown"


def test_bulk_label_is_empty_when_nothing_is_shown():
    """An empty label hides the button: 'Forget 0 shown' would offer to act on nothing."""
    assert bulk_label(REQUEST, 0) == ""


def test_a_request_without_bulk_labels_has_no_bulk_button():
    plain = ListRequest(heading="h", rows=tuple(ROWS), count_one="1", count_all="%s")
    assert bulk_label(plain, 3) == "" and bulk_label(plain, 1) == ""


def test_a_request_takes_keywords_only():
    """The fields' order changed: a positional call would put 'Forget all' in the count."""
    with pytest.raises(TypeError):
        ListRequest("h", (), (), "Forget all", "Forget %s shown")  # type: ignore[misc]


def test_toggle_adds_and_removes_a_key():
    assert toggle(frozenset(), "a") == {"a"}
    assert toggle(frozenset({"a", "b"}), "a") == {"b"}


def test_picked_keeps_row_order_and_rows_a_search_hides():
    assert picked(ROWS, {"g", "a"}) == ("a", "g")


def test_picked_drops_a_ticked_key_with_no_row():
    assert picked(ROWS, {"a", "gone"}) == ("a",)


