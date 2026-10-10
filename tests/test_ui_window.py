import time
from typing import Any

from resources.lib.constants import (
    PROMPT_EVERYONE,
    WHO_WATCHED_QUESTION,
    WINDOW_CANCEL,
    WINDOW_CLOSE,
    WINDOW_CLOSES_IN,
    WINDOW_COUNT_SOME,
    WINDOW_NO,
    WINDOW_SEARCH,
    WINDOW_VIEWER,
    WINDOW_YES,
)
from resources.lib.ui.list_window import ListFilter, ListRequest, ListRow, ListState
from resources.lib.ui.who_watched import WhoWatchedRequest
from resources.lib.ui.window import (
    BUTTON_BULK,
    BUTTON_CLOSE,
    BUTTON_DONE,
    BUTTON_FORGET,
    BUTTON_NO,
    BUTTON_PICK_DONE,
    BUTTON_PLAYLISTS,
    BUTTON_PROFILES,
    BUTTON_REMOVE,
    BUTTON_RENAME,
    BUTTON_SKIP,
    BUTTON_VIEWER_BACK,
    BUTTON_YES,
    LIST_FILTER,
    LIST_ROWS,
    LIST_SEARCH,
    LIST_VIEWERS,
    ConfirmDialog,
    ListDialog,
    PickListDialog,
    ViewerDialog,
    WhoWatchedDialog,
)

TEXTS = {PROMPT_EVERYONE: "Everyone", WHO_WATCHED_QUESTION: "Who watched it?", WINDOW_CLOSES_IN: "Closes in %s s"}


class FakeList:
    def __init__(self) -> None:
        self.items: list = []
        self.position = 0

    def addItem(self, item) -> None:
        self.items.append(item)

    def getListItem(self, index: int):
        return self.items[index]

    def getSelectedPosition(self) -> int:
        return self.position


class Action:
    def __init__(self, action_id: int) -> None:
        self.action_id = action_id

    def getId(self) -> int:
        return self.action_id


def _dialog(
    names=("anna", "bob"), preselect=(), autoclose=0, on_playback=False, playing=False, aborts=(), offer_forget=False
) -> tuple[Any, FakeList]:
    """The dialog is returned as Any: it is built on the stub window (tests/stubs.py), whose
    recording helpers the Kodistubs type the checker reads does not have.

    aborts scripts wait_for_abort's answers; once used up it sleeps briefly and says no,
    so a started watch thread ticks fast without spinning."""
    request = WhoWatchedRequest(
        title="Example", subtitle="Season 2, episode 5", poster="image://p/", names=names,
        preselect=preselect, autoclose_seconds=autoclose, close_on_playback=on_playback, offer_forget=offer_forget,
    )
    abort_answers = list(aborts)

    def wait_for_abort(seconds: float) -> bool:
        if abort_answers:
            return abort_answers.pop(0)
        time.sleep(0.01)
        return False

    dialog: Any = WhoWatchedDialog("crosswatch-who.xml", "/addon", "Default", "1080i")
    dialog.prepare(
        request,
        localised=lambda string_id: TEXTS.get(string_id, ""),
        is_playing=lambda: playing,
        wait_for_abort=wait_for_abort,
    )
    rows = FakeList()
    dialog.set_control(LIST_VIEWERS, rows)
    return dialog, rows


def _chosen(rows) -> list[str]:
    return [item.getProperty("chosen") for item in rows.items]


def test_init_sets_the_header_and_question():
    dialog, _ = _dialog()
    dialog.onInit()
    assert dialog.getProperty("CW.Title") == "Example"
    assert dialog.getProperty("CW.Subtitle") == "Season 2, episode 5"
    assert dialog.getProperty("CW.Poster") == "image://p/"
    assert dialog.getProperty("CW.Question") == "Who watched it?"


def test_init_lists_everyone_then_the_viewers_and_focuses_the_list():
    dialog, rows = _dialog()
    dialog.onInit()
    assert [item.getLabel() for item in rows.items] == ["Everyone", "anna", "bob"]
    assert dialog.focused == LIST_VIEWERS


def test_init_ticks_the_preselected_viewers():
    dialog, rows = _dialog(preselect=("bob",))
    dialog.onInit()
    assert _chosen(rows) == ["", "", "true"]


def test_a_second_init_does_not_add_the_rows_again():
    dialog, rows = _dialog()
    dialog.onInit()
    dialog.onInit()
    assert len(rows.items) == 3


def test_clicking_a_row_toggles_it_and_everyone_follows():
    dialog, rows = _dialog()
    dialog.onInit()
    rows.position = 1
    dialog.onClick(LIST_VIEWERS)
    rows.position = 2
    dialog.onClick(LIST_VIEWERS)
    assert _chosen(rows) == ["true", "true", "true"]
    rows.position = 0
    dialog.onClick(LIST_VIEWERS)
    assert _chosen(rows) == ["", "", ""]


def test_done_returns_the_ticked_names_in_configuration_order():
    dialog, rows = _dialog(names=("anna", "bob", "carol"), preselect=("carol", "anna"))
    dialog.onInit()
    dialog.onClick(BUTTON_DONE)
    assert dialog.result == ("anna", "carol")
    assert dialog.close_reason == "done"
    assert dialog.closed == 1


def test_done_with_nobody_ticked_returns_an_empty_answer():
    dialog, _ = _dialog()
    dialog.onInit()
    dialog.onClick(BUTTON_DONE)
    assert dialog.result == ()


def test_skip_is_a_cancel():
    dialog, _ = _dialog(preselect=("anna",))
    dialog.onInit()
    dialog.onClick(BUTTON_SKIP)
    assert dialog.result is None
    assert dialog.close_reason == "skip"


def test_back_is_a_cancel():
    for action_id in (10, 92):
        dialog, _ = _dialog(preselect=("anna",))
        dialog.onInit()
        dialog.onAction(Action(action_id))
        assert dialog.result is None and dialog.close_reason == "back"


def test_other_actions_do_not_close():
    dialog, _ = _dialog()
    dialog.onInit()
    dialog.onAction(Action(7))  # select
    assert dialog.closed == 0


def test_finish_is_first_wins():
    """Done and the countdown can land in the same second; only the first one counts."""
    dialog, _ = _dialog(preselect=("anna",))
    dialog.onInit()
    assert dialog.finish("timeout") is True
    dialog.onClick(BUTTON_DONE)
    assert dialog.result is None
    assert dialog.close_reason == "timeout"
    assert dialog.closed == 1


def test_the_countdown_shows_then_closes_as_a_cancel():
    # _show_countdown and _step are driven directly: onInit would start the watch thread.
    dialog, _ = _dialog(autoclose=3)
    dialog._show_countdown()
    assert dialog.getProperty("CW.Footer") == "Closes in 3 s"
    assert dialog._step() == ""
    assert dialog.getProperty("CW.Footer") == "Closes in 2 s"
    assert dialog._step() == ""
    assert dialog._step() == "timeout"


def test_no_countdown_means_no_footer():
    dialog, _ = _dialog(autoclose=0)
    dialog.onInit()
    assert dialog.getProperty("CW.Footer") == ""


def test_playback_starting_closes_the_window():
    dialog, _ = _dialog(on_playback=True, playing=True)
    assert dialog._step() == "playback"


def test_playback_is_ignored_when_not_asked_for():
    dialog, _ = _dialog(on_playback=False, playing=True)
    assert dialog._step() == ""


def test_kodi_shutting_down_closes_the_window():
    dialog, _ = _dialog(aborts=[True])
    assert dialog._step() == "shutdown"


def test_the_watch_thread_closes_on_timeout_and_stop_joins_it():
    dialog, _ = _dialog(autoclose=1)
    dialog.onInit()
    assert dialog._thread is not None
    dialog._thread.join(timeout=2)
    assert dialog.close_reason == "timeout" and dialog.result is None
    dialog.stop()
    assert not dialog._thread.is_alive()


def test_no_thread_without_countdown_or_playback_watch():
    dialog, _ = _dialog()
    dialog.onInit()
    assert dialog._thread is None


def test_stop_after_an_outside_close_logs_and_ends_the_watch():
    dialog, _ = _dialog(on_playback=True)
    dialog.onInit()
    dialog.stop()
    assert dialog.close_reason == "closed" and dialog.result is None
    assert dialog._thread is not None and not dialog._thread.is_alive()


def test_a_window_that_fails_to_fill_closes_as_a_logged_cancel():
    """Kodi logs an exception raised in onInit and keeps the window open, so without this
    the service thread would wait on a window nobody may be there to close."""
    from resources.lib import log as logmod

    captured: list[str] = []
    logmod.configure(log_dir=None, debug=False, sink=lambda message, level: captured.append(message))
    dialog, _ = _dialog(autoclose=3)
    dialog.set_control(LIST_VIEWERS, None)  # addItem on None raises
    dialog.onInit()
    assert dialog.close_reason == "error" and dialog.result is None and dialog.closed == 1
    assert any("ui.window_failed" in line and "AttributeError" in line for line in captured)
    assert dialog._thread is None


def _confirm() -> Any:
    dialog: Any = ConfirmDialog("crosswatch-confirm.xml", "/addon", "Default", "1080i")
    dialog.prepare("Remembered answers", "Forget who watched 3 shows?", lambda i: {WINDOW_YES: "Yes", WINDOW_NO: "No"}.get(i, ""))
    return dialog


def test_confirm_shows_its_heading_and_message_and_starts_on_no():
    dialog = _confirm()
    dialog.onInit()
    assert dialog.getProperty("CW.Heading") == "Remembered answers"
    assert dialog.getProperty("CW.Message") == "Forget who watched 3 shows?"
    assert dialog.focused == BUTTON_NO


def test_confirm_yes_is_true_and_no_or_back_is_not():
    yes = _confirm()
    yes.onInit()
    yes.onClick(BUTTON_YES)
    assert yes.result is True and yes.close_reason == "yes"
    no = _confirm()
    no.onInit()
    no.onClick(BUTTON_NO)
    assert no.result is False
    back = _confirm()
    back.onInit()
    back.onAction(Action(92))
    assert back.result is None
    assert back._thread is None  # no countdown, no playback watch

LIST_TEXTS = {WINDOW_COUNT_SOME: "%s of %s", WINDOW_VIEWER: "Viewer: %s", WINDOW_SEARCH: "Search", WINDOW_CLOSE: "Close"}
SHOWS = tuple(
    ListRow(key=k, title=t, detail=", ".join(n) or "will ask again", thumb=f"image://{k}/", names=n)
    for k, t, n in [("a", "Alpha", ("anna",)), ("b", "Beta", ("bob",)), ("c", "Gamma", ("anna", "bob")), ("d", "Delta", ())]
)
FILTERS = (
    ListFilter("All", lambda row: True),
    ListFilter("anna", lambda row: "anna" in row.names),
    ListFilter("bob", lambda row: "bob" in row.names),
)


class FakeRows(FakeList):
    def reset(self) -> None:
        self.items = []
        self.position = 0

    def size(self) -> int:
        return len(self.items)

    def selectItem(self, index: int) -> None:
        self.position = index


class FakeEdit:
    def __init__(self) -> None:
        self.text = ""
        self.input_type: tuple[int, str] | None = None

    def getText(self) -> str:
        return self.text

    def setText(self, text: str) -> None:
        self.text = text

    def setType(self, input_type: int, heading: str) -> None:
        self.input_type = (input_type, heading)


START = ListState()


def _list(state=START, filters=FILTERS) -> tuple[Any, FakeRows, FakeEdit]:
    request = ListRequest(
        heading="Remembered answers",
        rows=SHOWS,
        count_one="1 show",
        count_all="%s shows",
        filters=filters,
        bulk_all="Forget all",
        bulk_shown="Forget %s shown",
    )
    dialog: Any = ListDialog("crosswatch-list.xml", "/addon", "Default", "1080i")
    dialog.prepare(request, state, lambda i: LIST_TEXTS.get(i, ""))
    rows, edit = FakeRows(), FakeEdit()
    dialog.set_control(LIST_ROWS, rows)
    dialog.set_control(LIST_SEARCH, edit)
    return dialog, rows, edit


def _titles(rows: FakeRows) -> list[str]:
    return [item.getLabel() for item in rows.items]


def test_the_list_opens_with_every_row_and_focus_on_the_list():
    dialog, rows, _ = _list()
    dialog.onInit()
    assert _titles(rows) == ["Alpha", "Beta", "Gamma", "Delta"]
    assert rows.items[0].getProperty("detail") == "anna" and rows.items[0].getArt("thumb") == "image://a/"
    assert dialog.getProperty("CW.Count") == "4 shows"
    assert dialog.getProperty("CW.Bulk") == "Forget all"
    assert dialog.getProperty("CW.Filter") == "Viewer: All"
    assert dialog.focused == LIST_ROWS


def test_typing_in_the_search_refilters_after_each_key():
    dialog, rows, edit = _list()
    dialog.onInit()
    dialog.setFocusId(LIST_SEARCH)
    edit.text = "a"
    dialog.onAction(Action(0))
    assert _titles(rows) == ["Alpha", "Beta", "Gamma", "Delta"]  # every title or name has an "a"
    edit.text = "alp"
    dialog.onAction(Action(0))
    assert _titles(rows) == ["Alpha"]
    assert dialog.getProperty("CW.Count") == "1 of 4"


def test_search_and_filter_combine_and_the_bulk_label_follows():
    dialog, rows, edit = _list()
    dialog.onInit()
    dialog.onClick(LIST_FILTER)  # All -> anna
    assert _titles(rows) == ["Alpha", "Gamma"]
    assert dialog.getProperty("CW.Filter") == "Viewer: anna"
    assert dialog.getProperty("CW.Bulk") == "Forget 2 shown"
    dialog.setFocusId(LIST_SEARCH)
    edit.text = "gam"
    dialog.onAction(Action(0))
    assert _titles(rows) == ["Gamma"]
    dialog.onClick(LIST_FILTER)  # anna -> bob, search kept
    assert _titles(rows) == ["Gamma"]
    dialog.onClick(LIST_FILTER)  # bob -> All
    assert dialog.getProperty("CW.Filter") == "Viewer: All"


def test_nothing_matching_shows_the_empty_line():
    dialog, rows, edit = _list()
    dialog.onInit()
    dialog.setFocusId(LIST_SEARCH)
    edit.text = "zzz"
    dialog.onAction(Action(0))
    assert rows.items == []
    assert dialog.getProperty("CW.Empty") == "true"
    assert dialog.getProperty("CW.Count") == "0 of 4"


def test_ok_on_a_row_returns_it_with_the_state():
    dialog, rows, _ = _list()
    dialog.onInit()
    dialog.onClick(LIST_FILTER)  # anna: Alpha, Gamma
    rows.position = 1
    dialog.onClick(LIST_ROWS)
    assert dialog.result.action == "open" and dialog.result.key == "c"
    assert dialog.result.state == ListState(search="", filter_index=1, position=1)


def test_the_bulk_button_returns_the_keys_shown():
    dialog, _, _ = _list()
    dialog.onInit()
    dialog.onClick(LIST_FILTER)
    dialog.onClick(BUTTON_BULK)
    assert dialog.result.action == "bulk" and dialog.result.keys == ("a", "c")


def test_close_returns_close_and_back_is_a_cancel():
    dialog, _, _ = _list()
    dialog.onInit()
    dialog.onClick(BUTTON_CLOSE)
    assert dialog.result.action == "close"
    back, _, _ = _list()
    back.onInit()
    back.onAction(Action(10))
    assert back.result is None and back.close_reason == "back"


def test_a_kept_state_reopens_where_the_household_was():
    dialog, rows, edit = _list(ListState(search="a", filter_index=2, position=1))
    dialog.onInit()
    assert edit.text == "a"
    assert _titles(rows) == ["Beta", "Gamma"]
    assert rows.position == 1


def test_a_kept_position_past_the_end_selects_the_last_row():
    dialog, rows, _ = _list(ListState(position=9))
    dialog.onInit()
    assert rows.position == 3


def test_a_filter_index_out_of_range_falls_back_to_all():
    dialog, rows, _ = _list(ListState(filter_index=7))
    dialog.onInit()
    assert dialog.getProperty("CW.Filter") == "Viewer: All"


def test_without_filters_the_viewer_button_is_hidden():
    dialog, rows, _ = _list(filters=())
    dialog.onInit()
    assert dialog.getProperty("CW.Filter") == ""
    dialog.onClick(LIST_FILTER)
    assert _titles(rows) == ["Alpha", "Beta", "Gamma", "Delta"]


def test_forget_is_offered_only_when_asked():
    plain, _ = _dialog()
    plain.onInit()
    assert plain.getProperty("CW.OfferForget") == ""
    offered, _ = _dialog(offer_forget=True)
    offered.onInit()
    assert offered.getProperty("CW.OfferForget") == "true"


def test_forget_returns_the_nobody_answer():
    dialog, _ = _dialog(preselect=("anna",), offer_forget=True)
    dialog.onInit()
    dialog.onClick(BUTTON_FORGET)
    assert dialog.result == () and dialog.close_reason == "forget"


def test_forget_does_nothing_when_not_offered():
    dialog, _ = _dialog(preselect=("anna",))
    dialog.onInit()
    dialog.onClick(BUTTON_FORGET)
    assert dialog.closed == 0

def test_a_list_that_opens_empty_focuses_the_search_so_the_household_is_not_stuck():
    """An empty list cannot take focus, and no direction leads out of nothing. The search box
    has no visibility condition, so it can take focus in the same call that fills the
    window; the viewer button only becomes visible on Kodi's next frame."""
    dialog, _, _ = _list(ListState(search="zzz"))
    dialog.onInit()
    assert dialog.focused == LIST_SEARCH


def test_the_bulk_button_does_nothing_when_nothing_is_shown():
    """It would read 'Forget 0 shown', close and reopen for nothing."""
    dialog, rows, edit = _list()
    dialog.onInit()
    dialog.setFocusId(LIST_SEARCH)
    edit.text = "zzz"
    dialog.onAction(Action(0))
    dialog.onClick(BUTTON_BULK)
    assert dialog.closed == 0 and dialog.result is None


def test_nothing_shown_empties_the_bulk_label_so_the_button_hides():
    dialog, _, edit = _list()
    dialog.onInit()
    dialog.setFocusId(LIST_SEARCH)
    edit.text = "zzz"
    dialog.onAction(Action(0))
    assert dialog.getProperty("CW.Bulk") == ""


def test_the_search_keyboard_is_headed_search_not_kodis_enter_value():
    dialog, _, edit = _list()
    dialog.onInit()
    assert edit.input_type == (0, "Search")  # xbmcgui.INPUT_TYPE_TEXT


def test_the_close_button_reads_close_on_a_plain_list():
    dialog, _, _ = _list()
    dialog.onInit()
    assert dialog.getProperty("CW.Close") == "Close"


PICK_TEXTS = {**LIST_TEXTS, WINDOW_CANCEL: "Cancel"}
PLAYLISTS = tuple(
    ListRow(key=name, title=name, detail=detail, tag=tag)
    for name, detail, tag in [
        ("Anna TV", "", ""),
        ("Cartoons", "Also bob", ""),
        ("Films", "", ""),
        ("Eps", "", "not a TV show or film playlist"),
    ]
)


def _pick(ticked=("Cartoons", "Eps"), state=START) -> tuple[Any, FakeRows, FakeEdit]:
    request = ListRequest(
        heading="Playlists for anna",
        rows=PLAYLISTS,
        count_one="1 playlist",
        count_all="%s playlists",
        pick=True,
        ticked=ticked,
    )
    dialog: Any = PickListDialog("crosswatch-list.xml", "/addon", "Default", "1080i")
    dialog.prepare(request, state, lambda i: PICK_TEXTS.get(i, ""))
    rows, edit = FakeRows(), FakeEdit()
    dialog.set_control(LIST_ROWS, rows)
    dialog.set_control(LIST_SEARCH, edit)
    return dialog, rows, edit


def test_a_pick_list_opens_with_its_ticks_and_says_cancel():
    dialog, rows, _ = _pick()
    dialog.onInit()
    assert _chosen(rows) == ["", "true", "", "true"]
    assert dialog.getProperty("CW.Pick") == "true"
    assert dialog.getProperty("CW.Close") == "Cancel"
    assert dialog.getProperty("CW.Bulk") == ""
    assert dialog.getProperty("CW.Filter") == ""
    assert dialog.getProperty("CW.Count") == "4 playlists"
    assert dialog.focused == LIST_ROWS


def test_ok_on_a_row_ticks_it_in_place_without_closing():
    dialog, rows, _ = _pick()
    dialog.onInit()
    rows.position = 2
    first = rows.items[0]
    dialog.onClick(LIST_ROWS)
    assert _chosen(rows) == ["", "true", "true", "true"]
    assert rows.items[0] is first and rows.position == 2  # not refilled, not moved
    assert dialog.closed == 0
    dialog.onClick(LIST_ROWS)
    assert _chosen(rows) == ["", "true", "", "true"]


def test_ticks_survive_a_search():
    dialog, rows, edit = _pick()
    dialog.onInit()
    dialog.setFocusId(LIST_SEARCH)
    edit.text = "a"  # Anna TV, Cartoons
    dialog.onAction(Action(0))
    assert _titles(rows) == ["Anna TV", "Cartoons"]
    assert _chosen(rows) == ["", "true"]


def test_done_returns_ticked_rows_the_search_hides():
    dialog, rows, edit = _pick()
    dialog.onInit()
    dialog.setFocusId(LIST_SEARCH)
    edit.text = "anna"
    dialog.onAction(Action(0))
    rows.position = 0
    dialog.onClick(LIST_ROWS)  # tick Anna TV
    dialog.onClick(BUTTON_PICK_DONE)
    assert dialog.result.action == "done"
    assert dialog.result.keys == ("Anna TV", "Cartoons", "Eps")  # row order, hidden ones kept
    assert dialog.close_reason == "done"


def test_done_with_nothing_ticked_returns_no_keys():
    dialog, _, _ = _pick(ticked=())
    dialog.onInit()
    dialog.onClick(BUTTON_PICK_DONE)
    assert dialog.result.action == "done" and dialog.result.keys == ()


def test_cancel_and_back_return_no_choice():
    dialog, _, _ = _pick()
    dialog.onInit()
    dialog.onClick(BUTTON_CLOSE)
    assert dialog.result.action == "close" and dialog.result.keys == ()
    back, _, _ = _pick()
    back.onInit()
    back.onAction(Action(92))
    assert back.result is None and back.close_reason == "back"


def test_the_bulk_button_does_nothing_on_a_pick_list():
    dialog, _, _ = _pick()
    dialog.onInit()
    dialog.onClick(BUTTON_BULK)
    assert dialog.closed == 0


def test_ok_on_a_row_of_a_plain_list_still_opens_it():
    """The pick override must not leak into the list it extends."""
    dialog, rows, _ = _list()
    dialog.onInit()
    dialog.onClick(LIST_ROWS)
    assert dialog.result.action == "open" and dialog.getProperty("CW.Pick") == ""


def test_a_bulk_always_button_acts_with_nothing_shown():
    request = ListRequest(
        heading="Viewers", rows=SHOWS, count_one="1 viewer", count_all="%s viewers",
        bulk_all="Add viewer", bulk_shown="Add viewer", bulk_always=True,
    )
    dialog: Any = ListDialog("crosswatch-list.xml", "/addon", "Default", "1080i")
    dialog.prepare(request, ListState(search="zzz"), lambda i: LIST_TEXTS.get(i, ""))
    rows, edit = FakeRows(), FakeEdit()
    dialog.set_control(LIST_ROWS, rows)
    dialog.set_control(LIST_SEARCH, edit)
    dialog.onInit()
    assert rows.items == [] and dialog.getProperty("CW.Bulk") == "Add viewer"
    dialog.onClick(BUTTON_BULK)
    assert dialog.result.action == "bulk" and dialog.result.keys == ()


def _viewer() -> Any:
    dialog: Any = ViewerDialog("crosswatch-viewer.xml", "/addon", "Default", "1080i")
    dialog.prepare("anna", "Playlists: Anna TV[CR]Profiles: none", lambda i: "")
    return dialog


def test_the_viewer_window_shows_the_name_and_summary_and_starts_on_playlists():
    dialog = _viewer()
    dialog.onInit()
    assert dialog.getProperty("CW.Heading") == "anna"
    assert dialog.getProperty("CW.Line1") == "Playlists: Anna TV"
    assert dialog.getProperty("CW.Line2") == "Profiles: none"
    assert dialog.focused == BUTTON_PLAYLISTS


def test_each_viewer_button_returns_its_action():
    for button, action in [
        (BUTTON_PLAYLISTS, "playlists"), (BUTTON_PROFILES, "profiles"),
        (BUTTON_RENAME, "rename"), (BUTTON_REMOVE, "remove"), (BUTTON_VIEWER_BACK, "back"),
    ]:
        dialog = _viewer()
        dialog.onInit()
        dialog.onClick(button)
        assert dialog.result == action and dialog.close_reason == action


def test_back_leaves_the_viewer_window_with_no_action():
    dialog = _viewer()
    dialog.onInit()
    dialog.onAction(Action(92))
    assert dialog.result is None and dialog.close_reason == "back"


def test_a_list_without_artwork_hides_the_thumbnail_column():
    request = ListRequest(heading="Viewers", rows=SHOWS, count_one="1", count_all="%s", thumbs=False)
    dialog: Any = ListDialog("crosswatch-list.xml", "/addon", "Default", "1080i")
    dialog.prepare(request, START, lambda i: LIST_TEXTS.get(i, ""))
    dialog.set_control(LIST_ROWS, FakeRows())
    dialog.set_control(LIST_SEARCH, FakeEdit())
    dialog.onInit()
    assert dialog.getProperty("CW.NoThumbs") == "true"


def test_a_list_with_artwork_keeps_the_thumbnail_column():
    dialog, _, _ = _list()
    dialog.onInit()
    assert dialog.getProperty("CW.NoThumbs") == ""
