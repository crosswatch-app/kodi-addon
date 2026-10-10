import time
from typing import Any

from resources.lib.constants import PROMPT_EVERYONE, WHO_WATCHED_QUESTION, WINDOW_CLOSES_IN
from resources.lib.ui.who_watched import WhoWatchedRequest
from resources.lib.ui.window import BUTTON_DONE, BUTTON_SKIP, LIST_VIEWERS, WhoWatchedDialog

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
    names=("anna", "bob"), preselect=(), autoclose=0, on_playback=False, playing=False, aborts=()
) -> tuple[Any, FakeList]:
    """The dialog is returned as Any: it is built on the stub window (tests/stubs.py), whose
    recording helpers the Kodistubs type the checker reads does not have.

    aborts scripts wait_for_abort's answers; once used up it sleeps briefly and says no,
    so a started watch thread ticks fast without spinning."""
    request = WhoWatchedRequest(
        title="Example", subtitle="Season 2, episode 5", poster="image://p/", names=names,
        preselect=preselect, autoclose_seconds=autoclose, close_on_playback=on_playback,
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
