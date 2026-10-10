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

from resources.lib.constants import PROMPT_EVERYONE, WHO_WATCHED_QUESTION, WINDOW_CLOSES_IN
from resources.lib.log import get_logger
from resources.lib.ui import who_watched as ww

_log = get_logger("ui")

# xbmc/input/actions/ActionIDs.h
ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92

WHO_WATCHED_XML = "crosswatch-who.xml"
LIST_VIEWERS = 200
BUTTON_DONE = 20
BUTTON_SKIP = 21

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
        elif controlId == BUTTON_SKIP:
            self.finish("skip")

    def _show_ticks(self) -> None:
        rows: Any = self.getControl(LIST_VIEWERS)
        names = self._request.names
        rows.getListItem(ww.EVERYONE_ROW).setProperty("chosen", "true" if ww.everyone(names, self._ticked) else "")
        for row, name in enumerate(names, start=1):
            rows.getListItem(row).setProperty("chosen", "true" if name in self._ticked else "")
