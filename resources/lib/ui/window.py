# SPDX-License-Identifier: GPL-2.0-only
"""CrossWatch's own windows, drawn from the add-on's skin files rather than the active skin's.

The only module that touches Kodi window objects. A window closes as a cancel on Back, on
its countdown, when Kodi shuts down, and, when asked, when playback starts. Every close is
logged with its reason, so a question that closed itself can be told apart from one the
household skipped.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import xbmcgui

from resources.lib.constants import PROMPT_EVERYONE, WHO_WATCHED_QUESTION, WINDOW_CLOSES_IN, WINDOW_VIEWER
from resources.lib.log import get_logger
from resources.lib.ui import list_window as lw
from resources.lib.ui import who_watched as ww

_log = get_logger("ui")

# xbmc/input/actions/ActionIDs.h
ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92

WHO_WATCHED_XML = "crosswatch-who.xml"
LIST_VIEWERS = 200
BUTTON_DONE = 20
BUTTON_SKIP = 21
BUTTON_FORGET = 22

Localised = Callable[[int], str]


class CrossWatchDialog(xbmcgui.WindowXMLDialog):
    """Set up with configure() before doModal(), and call stop() once doModal() returns.

    Configured through a method rather than __init__: Kodi builds the window from the
    constructor arguments, so the constructor keeps Kodi's own signature.
    """

    name = "dialog"

    def configure(
        self,
        localised: Localised,
        properties: dict[str, str],
        autoclose_seconds: int,
        close_on_playback: bool,
        is_playing: Callable[[], bool],
        wait_for_abort: Callable[[float], bool],
    ) -> None:
        self.result: Any = None
        self.close_reason = ""
        self._localised = localised
        self._window_properties = properties
        self._remaining = autoclose_seconds
        self._close_on_playback = close_on_playback
        self._is_playing = is_playing
        self._wait_for_abort = wait_for_abort
        self._lock = threading.Lock()
        self._finished = threading.Event()
        self._filled = False
        self._thread: threading.Thread | None = None

    def fill(self) -> None:
        """Populate the window's controls. Called once, from the first onInit."""

    def onInit(self) -> None:
        for key, value in self._window_properties.items():
            self.setProperty(key, value)
        if self._filled:
            return
        self._filled = True
        try:
            self.fill()
        except Exception as exc:
            # Kodi logs an exception raised here and keeps the window open, and doModal never
            # sees it: without this the caller waits on a window with nothing in it.
            _log.warning("ui.window_failed", window=self.name, error_type=type(exc).__name__)
            self.finish("error")
            return
        self._show_countdown()
        if self._remaining > 0 or self._close_on_playback:
            self._thread = threading.Thread(target=self._watch, name="crosswatch-window", daemon=True)
            self._thread.start()

    def onAction(self, action: Any) -> None:
        if action.getId() in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self.finish("back")

    def finish(self, reason: str, result: Any = None) -> bool:
        """Close once, whoever asks first: the household, the countdown, or Kodi."""
        with self._lock:
            if self._finished.is_set():
                return False
            self._finished.set()
            self.close_reason = reason
            self.result = result
        _log.info("ui.window_closed", window=self.name, reason=reason)
        self.close()
        return True

    def stop(self) -> None:
        """End the watch, also for a window Kodi closed by itself."""
        with self._lock:
            outside = not self._finished.is_set()
            self._finished.set()
            if outside:
                self.close_reason = "closed"
        if outside:
            _log.info("ui.window_closed", window=self.name, reason="closed")
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _watch(self) -> None:
        while not self._finished.is_set():
            reason = self._step()
            if reason:
                self.finish(reason)
                return

    def _step(self) -> str:
        """One second of the watch: the reason to close, or "" to keep waiting."""
        if self._wait_for_abort(1):
            return "shutdown"
        if self._finished.is_set():
            return ""
        if self._close_on_playback and self._is_playing():
            return "playback"
        if self._remaining > 0:
            self._remaining -= 1
            if self._remaining == 0:
                return "timeout"
            self._show_countdown()
        return ""

    def _show_countdown(self) -> None:
        if self._remaining > 0:
            # replace rather than %: a translation that drops the placeholder must not raise.
            self.setProperty("CW.Footer", self._localised(WINDOW_CLOSES_IN).replace("%s", str(self._remaining)))


class WhoWatchedDialog(CrossWatchDialog):
    name = "who_watched"

    def prepare(
        self,
        request: ww.WhoWatchedRequest,
        localised: Localised,
        is_playing: Callable[[], bool],
        wait_for_abort: Callable[[float], bool],
    ) -> None:
        self._request = request
        self._ticked = ww.initial(request)
        self.configure(
            localised,
            {
                "CW.Title": request.title,
                "CW.Subtitle": request.subtitle,
                "CW.Poster": request.poster,
                "CW.Question": localised(WHO_WATCHED_QUESTION),
                "CW.OfferForget": "true" if request.offer_forget else "",
            },
            request.autoclose_seconds,
            request.close_on_playback,
            is_playing,
            wait_for_abort,
        )

    def fill(self) -> None:
        rows: Any = self.getControl(LIST_VIEWERS)
        for label in (self._localised(PROMPT_EVERYONE), *self._request.names):
            rows.addItem(xbmcgui.ListItem(label))
        self._show_ticks()
        # The first row, Everyone, only toggles: a stray OK sends nothing.
        self.setFocusId(LIST_VIEWERS)

    def onClick(self, controlId: int) -> None:
        names = self._request.names
        if controlId == LIST_VIEWERS:
            rows: Any = self.getControl(LIST_VIEWERS)
            self._ticked = ww.toggle(names, self._ticked, rows.getSelectedPosition())
            self._show_ticks()
        elif controlId == BUTTON_DONE:
            self.finish("done", ww.answer(names, self._ticked))
        elif controlId == BUTTON_FORGET and self._request.offer_forget:
            # The same answer as Done with nobody ticked, which the screen treats as forget.
            self.finish("forget", ())
        elif controlId == BUTTON_SKIP:
            self.finish("skip")

    def _show_ticks(self) -> None:
        rows: Any = self.getControl(LIST_VIEWERS)
        names = self._request.names
        rows.getListItem(ww.EVERYONE_ROW).setProperty("chosen", "true" if ww.everyone(names, self._ticked) else "")
        for row, name in enumerate(names, start=1):
            rows.getListItem(row).setProperty("chosen", "true" if name in self._ticked else "")


CONFIRM_XML = "crosswatch-confirm.xml"
BUTTON_YES = 10
BUTTON_NO = 11


class ConfirmDialog(CrossWatchDialog):
    name = "confirm"

    def prepare(self, heading: str, message: str, localised: Localised) -> None:
        self.configure(
            localised,
            {"CW.Heading": heading, "CW.Message": message},
            autoclose_seconds=0,
            close_on_playback=False,
            is_playing=lambda: False,
            wait_for_abort=lambda _: False,
        )

    def fill(self) -> None:
        # No first: the question always guards something that cannot be undone.
        self.setFocusId(BUTTON_NO)

    def onClick(self, controlId: int) -> None:
        if controlId == BUTTON_YES:
            self.finish("yes", True)
        elif controlId == BUTTON_NO:
            self.finish("no", False)


LIST_XML = "crosswatch-list.xml"
LIST_SEARCH = 2
LIST_FILTER = 3
LIST_ROWS = 100
BUTTON_BULK = 20
BUTTON_CLOSE = 21


class ListDialog(CrossWatchDialog):
    """A searchable list: the screen acts on what comes back, then reopens it."""

    name = "list"

    def prepare(self, request: lw.ListRequest, state: lw.ListState, localised: Localised) -> None:
        self._request = request
        self._search = state.search
        in_range = 0 <= state.filter_index < len(request.filters)
        self._filter_index = state.filter_index if in_range else 0
        self._position = state.position
        self._shown: list[lw.ListRow] = []
        self.configure(
            localised,
            {"CW.Heading": request.heading},
            autoclose_seconds=0,
            close_on_playback=False,
            is_playing=lambda: False,
            wait_for_abort=lambda _: False,
        )

    def state(self) -> lw.ListState:
        rows: Any = self.getControl(LIST_ROWS)
        return lw.ListState(self._search, self._filter_index, int(rows.getSelectedPosition()))

    def fill(self) -> None:
        search: Any = self.getControl(LIST_SEARCH)
        search.setText(self._search)
        self._show(self._position)
        self.setFocusId(LIST_ROWS)

    def onAction(self, action: Any) -> None:
        super().onAction(action)
        # Kodi reports no text change, so the search box is read after every key press
        # while it has focus.
        try:
            focused = self.getFocusId()
        except RuntimeError:
            return
        if focused == LIST_SEARCH:
            self._read_search()

    def onClick(self, controlId: int) -> None:
        if controlId == LIST_SEARCH:
            self._read_search()  # after Kodi's keyboard closes
        elif controlId == LIST_FILTER and self._request.filters:
            self._filter_index = (self._filter_index + 1) % len(self._request.filters)
            self._show(0)
        elif controlId == LIST_ROWS:
            position = self.state().position
            if 0 <= position < len(self._shown):
                self.finish("open", lw.ListResult("open", self.state(), key=self._shown[position].key))
        elif controlId == BUTTON_BULK:
            keys = tuple(row.key for row in self._shown)
            self.finish("bulk", lw.ListResult("bulk", self.state(), keys=keys))
        elif controlId == BUTTON_CLOSE:
            self.finish("close", lw.ListResult("close", self.state()))

    def _read_search(self) -> None:
        search: Any = self.getControl(LIST_SEARCH)
        text = str(search.getText())
        if text != self._search:
            self._search = text
            self._show(0)

    def _show(self, position: int) -> None:
        request = self._request
        row_filter = request.filters[self._filter_index] if request.filters else None
        self._shown = lw.visible(request.rows, self._search, row_filter)
        rows: Any = self.getControl(LIST_ROWS)
        rows.reset()
        for row in self._shown:
            item = xbmcgui.ListItem(row.title)
            item.setArt({"thumb": row.thumb})
            item.setProperty("detail", row.detail)
            item.setProperty("tag", row.tag)
            rows.addItem(item)
        if self._shown:
            rows.selectItem(min(max(position, 0), len(self._shown) - 1))
        label = self._localised(WINDOW_VIEWER).replace("%s", row_filter.label) if row_filter else ""
        self.setProperty("CW.Filter", label)
        self.setProperty("CW.Count", lw.count_text(self._localised, len(self._shown), len(request.rows)))
        self.setProperty("CW.Bulk", lw.bulk_label(request, len(self._shown)))
        self.setProperty("CW.Empty", "" if self._shown else "true")
