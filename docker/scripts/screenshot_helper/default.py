# SPDX-License-Identifier: GPL-2.0-only
"""Open one CrossWatch window with demo data, for docker/scripts/screenshots.py.

Development only, never shipped. It imports the installed add-on's own window classes, so a
screenshot shows exactly what the household sees, with the same font mapping.
"""

import json
import os
import sys

import xbmc
import xbmcaddon
import xbmcgui

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = xbmcaddon.Addon("service.crosswatch")
sys.path.insert(0, TARGET.getAddonInfo("path"))

from resources.lib.constants import ADDON_ID  # noqa: E402
from resources.lib.ui import skin_fonts, window  # noqa: E402
from resources.lib.ui.who_watched import WhoWatchedRequest  # noqa: E402


def _who_watched(scene: dict, path: str) -> None:
    if scene.get("library"):
        # Written by screenshots.py: a show and a film from this Kodi's library, so the
        # poster is real art.
        with open(os.path.join(HERE, "library.json"), encoding="utf-8") as handle:
            item = json.load(handle)[scene["library"]]
        title, poster = item["title"], item["poster"]
        subtitle = scene["subtitle"].replace("{year}", str(item["year"] or ""))
    else:
        title, poster, subtitle = scene["title"], scene["poster"], scene["subtitle"]
    request = WhoWatchedRequest(
        title=title,
        subtitle=subtitle,
        poster=poster,
        names=tuple(scene["names"]),
        preselect=tuple(scene["preselect"]),
        autoclose_seconds=scene["autoclose_seconds"],
    )
    monitor = xbmc.Monitor()
    dialog = window.WhoWatchedDialog(window.WHO_WATCHED_XML, path, "Default", "1080i")
    # A watch step that waits an hour holds the countdown at its first value, so the
    # screenshot is the same on every run; Kodi shutting down still ends it.
    dialog.prepare(request, TARGET.getLocalizedString, lambda: False, lambda seconds: monitor.waitForAbort(3600))
    try:
        dialog.doModal()
    finally:
        dialog.stop()


WINDOWS = {window.WHO_WATCHED_XML: _who_watched}


def main() -> None:
    with open(os.path.join(HERE, "scenes.json"), encoding="utf-8") as handle:
        scene = json.load(handle)[sys.argv[1]]
    backdrop = xbmcgui.WindowXML("screenshot-backdrop.xml", HERE, "Default", "1080i")
    backdrop.show()
    try:
        WINDOWS[scene["window"]](scene, skin_fonts.ensure_generated(ADDON_ID))
    finally:
        backdrop.close()


main()
