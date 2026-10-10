"""Minimal xbmc module stubs, installed before any addon import.

The adapters import xbmc at module scope, so without these the modules that own the
addon's failure behaviour cannot be imported under pytest at all.
"""

from __future__ import annotations

import sys
import types
from typing import Any


def install_kodi_stubs() -> None:
    """Install stand-in xbmc modules.

    Each module is annotated Any: a ModuleType built at runtime has no declared attributes,
    so a type checker rejects every assignment onto it.
    """
    if "xbmc" in sys.modules:
        return

    xbmc: Any = types.ModuleType("xbmc")
    xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3

    class _Player:
        def __init__(self, *args, **kwargs) -> None: ...
        def isPlaying(self) -> bool: return False
        def getTime(self) -> float: raise RuntimeError("not playing")
        def getTotalTime(self) -> float: raise RuntimeError("not playing")

    class _Monitor:
        def __init__(self, *args, **kwargs) -> None: ...
        def abortRequested(self) -> bool: return False
        def waitForAbort(self, timeout: float = 0.0) -> bool: return True

    xbmc.Player = _Player
    xbmc.Monitor = _Monitor
    xbmc.log = lambda message, level=1: None
    xbmc.getInfoLabel = lambda key: ""
    xbmc.executeJSONRPC = lambda request: '{"result":{}}'
    xbmc.getSkinDir = lambda: "skin.estuary"

    xbmcgui: Any = types.ModuleType("xbmcgui")

    class _Dialog:
        def multiselect(self, heading, options, autoclose=0, preselect=None, useDetails=False): return None
        def select(self, heading, options): return -1
        def input(self, heading, defaultt=""): return ""
        def yesno(self, heading, message): return False

    xbmcgui.Dialog = _Dialog
    xbmcgui.getCurrentWindowDialogId = lambda: 9999

    class _ListItem:
        def __init__(self, label: str = "", *args, **kwargs) -> None:
            self._label = label
            self._properties: dict[str, str] = {}
        def getLabel(self) -> str: return self._label
        def setProperty(self, key: str, value: str) -> None: self._properties[key.lower()] = value
        def getProperty(self, key: str) -> str: return self._properties.get(key.lower(), "")

    class _WindowXMLDialog:
        """Records properties and closes; tests attach controls with set_control."""
        def __init__(self, *args, **kwargs) -> None:
            self._properties: dict[str, str] = {}
            self._controls: dict[int, Any] = {}
            self.closed = 0
            self.focused: int | None = None
        def set_control(self, control_id: int, control: Any) -> None: self._controls[control_id] = control
        def getControl(self, control_id: int) -> Any: return self._controls[control_id]
        def setProperty(self, key: str, value: str) -> None: self._properties[key.lower()] = value
        def getProperty(self, key: str) -> str: return self._properties.get(key.lower(), "")
        def setFocusId(self, control_id: int) -> None: self.focused = control_id
        def close(self) -> None: self.closed += 1
        def doModal(self) -> None: ...

    xbmcgui.ListItem = _ListItem
    xbmcgui.WindowXMLDialog = _WindowXMLDialog

    xbmcvfs: Any = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda path: path
    xbmcvfs.exists = lambda path: False

    class _File:
        def __init__(self, path, mode="r") -> None: ...
        def read(self, count=0) -> str: return ""
        def __enter__(self): return self
        def __exit__(self, *args): return False

    xbmcvfs.File = _File

    xbmcaddon: Any = types.ModuleType("xbmcaddon")

    class _Addon:
        def __init__(self, id: str | None = None) -> None: ...
        def getSetting(self, key: str) -> str: return ""
        def getSettingBool(self, key: str) -> bool: return False
        def getSettingInt(self, key: str) -> int: return 0
        def getAddonInfo(self, key: str) -> str: return ""

    xbmcaddon.Addon = _Addon

    for name, module in (("xbmc", xbmc), ("xbmcgui", xbmcgui), ("xbmcvfs", xbmcvfs), ("xbmcaddon", xbmcaddon)):
        sys.modules[name] = module
