# SPDX-License-Identifier: GPL-2.0-only
"""The single boundary between this addon and Kodi.

Everything else takes a KodiApi, so the logic is testable as plain Python. The one admitted
exception is PlaybackMonitor and ServiceMonitor, which must subclass xbmc.Player and
xbmc.Monitor to receive callbacks at all; they forward immediately and hold no logic.

Keep this module free of decisions: it translates, it does not choose. topmost_dialog_id
returns a raw id rather than a "busy" verdict for exactly that reason.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

# xbmc/guilib/WindowIDs.h:12. GetTopmostModalDialog returns this when no modal is open.
WINDOW_INVALID = 9999

MAX_SETTINGS_FILE_BYTES = 256 * 1024


class KodiApi(Protocol):
    def jsonrpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def setting(self, key: str) -> str: ...
    def setting_bool(self, key: str) -> bool: ...
    def setting_int(self, key: str) -> int: ...
    def addon_version(self) -> str: ...
    def info_label(self, key: str) -> str: ...
    def translate(self, path: str) -> str: ...
    def read_text(self, path: str, max_bytes: int = MAX_SETTINGS_FILE_BYTES) -> str | None: ...
    def is_playing(self) -> bool: ...
    def player_times(self) -> tuple[int | None, int | None]: ...
    def topmost_dialog_id(self) -> int: ...
    def multiselect(
        self, heading: str, options: list[str], preselect: list[int] | None = None, autoclose: int = 0
    ) -> list[int] | None: ...
    def select(self, heading: str, options: list[str]) -> int: ...
    def text_input(self, heading: str, default: str = "") -> str: ...
    def confirm(self, heading: str, message: str) -> bool: ...
    def notify(self, heading: str, message: str) -> None: ...
    def localised(self, string_id: int) -> str: ...
    def log(self, message: str, level: int) -> None: ...


class KodiRpcError(RuntimeError):
    def __init__(self, method: str, error: Any) -> None:
        super().__init__(f"{method}: {error}")
        self.method = method
        self.error = error


class KodiRuntime:
    """KodiApi backed by the real xbmc modules.

    The player is held, not constructed per call: building an xbmc.Player registers a
    callback target on a process-global list the application thread walks under lock.
    """

    def __init__(self, player: Any = None) -> None:
        import xbmc
        import xbmcaddon
        import xbmcgui
        import xbmcvfs

        self._xbmc = xbmc
        self._xbmcgui = xbmcgui
        self._xbmcvfs = xbmcvfs
        self._addon = xbmcaddon.Addon()
        self._player = player if player is not None else xbmc.Player()
        self._levels = {10: xbmc.LOGDEBUG, 20: xbmc.LOGINFO, 30: xbmc.LOGWARNING, 40: xbmc.LOGERROR}

    def jsonrpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        body = json.loads(self._xbmc.executeJSONRPC(json.dumps(request)))
        if "error" in body:
            raise KodiRpcError(method, body["error"])
        result = body.get("result")
        return result if isinstance(result, dict) else {"result": result}

    def setting(self, key: str) -> str:
        return str(self._addon.getSetting(key) or "")

    def setting_bool(self, key: str) -> bool:
        return bool(self._addon.getSettingBool(key))

    def setting_int(self, key: str) -> int:
        return int(self._addon.getSettingInt(key))

    def addon_version(self) -> str:
        return str(self._addon.getAddonInfo("version") or "")

    def info_label(self, key: str) -> str:
        return str(self._xbmc.getInfoLabel(key) or "")

    def translate(self, path: str) -> str:
        return self._xbmcvfs.translatePath(path)

    def read_text(self, path: str, max_bytes: int = MAX_SETTINGS_FILE_BYTES) -> str | None:
        try:
            if not self._xbmcvfs.exists(path):
                return None
            # The cap is passed to Kodi rather than applied by slicing afterwards
            # (xbmcvfs File.h:110-112). Slicing reads the whole file first, and slices
            # characters rather than bytes, so the declared bound is not a bound at all.
            with self._xbmcvfs.File(path) as handle:
                return str(handle.read(max_bytes))
        except Exception:
            return None

    def is_playing(self) -> bool:
        try:
            return bool(self._player.isPlaying())
        except Exception:
            return False

    def player_times(self) -> tuple[int | None, int | None]:
        """Position and duration, read independently.

        Both raise when playback has stopped, and they can stop between the two calls. A
        failed duration read must not discard a good position: the stop event is built from
        the last sample.
        """
        position: int | None = None
        duration: int | None = None
        try:
            # Clamped: at the instant onAVStarted fires getTime() can return a small
            # negative, and a negative position is not a state the player can be in. It
            # would otherwise reach the wire and become a negative resume point.
            position = max(0, int(self._player.getTime() * 1000))
        except Exception:
            position = None
        try:
            total = int(self._player.getTotalTime() * 1000)
            duration = total if total > 0 else None
        except Exception:
            duration = None
        return position, duration

    def topmost_dialog_id(self) -> int:
        return int(self._xbmcgui.getCurrentWindowDialogId())

    def multiselect(
        self, heading: str, options: list[str], preselect: list[int] | None = None, autoclose: int = 0
    ) -> list[int] | None:
        # Kodi declares the options list as List[str | ListItem], and list is invariant, so
        # a list[str] is rejected although Kodi accepts it. Widen at the call rather than in
        # the protocol: ListItem is a Kodi type and must not leak past this boundary.
        choices: list[Any] = list(options)
        return self._xbmcgui.Dialog().multiselect(
            heading, choices, autoclose=autoclose * 1000, preselect=preselect or []
        )

    def select(self, heading: str, options: list[str]) -> int:
        choices: list[Any] = list(options)  # widened for the same reason as multiselect
        return int(self._xbmcgui.Dialog().select(heading, choices))

    def text_input(self, heading: str, default: str = "") -> str:
        return str(self._xbmcgui.Dialog().input(heading, default) or "")

    def confirm(self, heading: str, message: str) -> bool:
        return bool(self._xbmcgui.Dialog().yesno(heading, message))

    def notify(self, heading: str, message: str) -> None:
        """A toast on the household's own screen.

        This is the one channel that may carry a playlist name. Kodi's shared log gets
        attached to bug reports; the screen in the living room does not.
        """
        try:
            self._xbmcgui.Dialog().notification(heading, message)
        except Exception:
            # Cosmetic. A skin that refuses to draw a toast must not take the service down.
            pass

    def localised(self, string_id: int) -> str:
        try:
            return str(self._addon.getLocalizedString(string_id) or "")
        except Exception:
            return ""

    def log(self, message: str, level: int) -> None:
        self._xbmc.log(message, self._levels.get(level, self._xbmc.LOGINFO))
