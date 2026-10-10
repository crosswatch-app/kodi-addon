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
        offer_forget=scene.get("offer_forget", False),
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


DEMO_WHO = [("Anna", "Ben"), ("Chloe",), (), ("Ben",), ("Anna",), ("Anna", "Chloe"), ("Ben",), ()]
DEMO_TAG = ["", "", "", "covered by playlist", "", "not in library", "", ""]


def _list(scene: dict, path: str) -> None:
    from resources.lib.ui import list_window as lw

    if scene.get("rows") == "playlists":
        _pick(scene, path)
        return
    if scene.get("rows") == "viewers":
        _viewers(scene, path)
        return
    if scene.get("rows") == "profiles":
        _profiles(scene, path)
        return
    with open(os.path.join(HERE, "library.json"), encoding="utf-8") as handle:
        shows = json.load(handle)["shows"]
    rows = tuple(
        lw.ListRow(
            key=str(i), title=show["title"], detail=", ".join(DEMO_WHO[i % 8]) or "will ask again",
            tag=DEMO_TAG[i % 8], thumb=show["poster"], names=DEMO_WHO[i % 8],
        )
        for i, show in enumerate(shows)
    )
    filters = (
        lw.ListFilter("All", lambda row: True),
        *(lw.ListFilter(n, lambda row, n=n: n in row.names) for n in ("Anna", "Ben", "Chloe")),
        lw.ListFilter("will ask again", lambda row: not row.names),
    )
    request = lw.ListRequest(
        heading="Remembered answers",
        rows=rows,
        count_one=TARGET.getLocalizedString(30097),
        count_all=TARGET.getLocalizedString(30090),
        filters=filters,
        bulk_all="Forget all",
        bulk_shown="Forget %s shown",
    )
    dialog = window.ListDialog(window.LIST_XML, path, "Default", "1080i")
    dialog.prepare(request, lw.ListState(search=scene["search"], filter_index=scene["filter"]), TARGET.getLocalizedString)
    try:
        dialog.doModal()
    finally:
        dialog.stop()


# name, other viewers with it, flag (string id or 0), ticked
DEMO_PLAYLISTS = [
    ("Anna's shows", "", 0, True),
    ("Cartoons", "Ben", 0, True),
    ("Documentaries", "", 0, False),
    ("Family films", "Ben, Chloe", 0, False),
    ("Sci-fi", "", 0, False),
    ("Westerns", "", 0, False),
    ("Old favourites", "", 30058, True),
]


def _pick(scene: dict, path: str) -> None:
    from resources.lib.ui import list_window as lw

    text = TARGET.getLocalizedString
    rows = tuple(
        lw.ListRow(
            key=name, title=name, detail=text(30098).replace("%s", also) if also else "", tag=text(flag) if flag else ""
        )
        for name, also, flag, _ in DEMO_PLAYLISTS
    )
    request = lw.ListRequest(
        heading=text(30052).replace("%s", "Anna"),
        rows=rows,
        count_one=text(30059),
        count_all=text(30056),
        pick=True,
        ticked=tuple(name for name, _, _, ticked in DEMO_PLAYLISTS if ticked),
    )
    dialog = window.PickListDialog(window.LIST_XML, path, "Default", "1080i")
    state = lw.ListState(search=scene["search"], position=scene.get("position", 0))
    dialog.prepare(request, state, text)
    try:
        dialog.doModal()
    finally:
        dialog.stop()


def _confirm(scene: dict, path: str) -> None:
    dialog = window.ConfirmDialog(window.CONFIRM_XML, path, "Default", "1080i")
    dialog.prepare(scene["heading"], scene["message"], TARGET.getLocalizedString)
    try:
        dialog.doModal()
    finally:
        dialog.stop()


def _viewers(scene: dict, path: str) -> None:
    from resources.lib.ui import list_window as lw

    text = TARGET.getLocalizedString
    details = {
        "Anna": text(30059),
        "Ben": text(30056).replace("%s", "3") + ", " + text(30105),
        "Chloe": text(30104),
    }
    tags = {"Ben": text(30057).replace("%s", "1")}
    rows = tuple(lw.ListRow(key=n, title=n, detail=details[n], tag=tags.get(n, "")) for n in ("Anna", "Ben", "Chloe"))
    add = text(30048)
    request = lw.ListRequest(
        heading=text(30010), rows=rows, count_one=text(30102), count_all=text(30103),
        bulk_all=add, bulk_shown=add, bulk_always=True, thumbs=False,
    )
    dialog = window.ListDialog(window.LIST_XML, path, "Default", "1080i")
    dialog.prepare(request, lw.ListState(search=scene["search"]), text)
    try:
        dialog.doModal()
    finally:
        dialog.stop()


def _profiles(scene: dict, path: str) -> None:
    from resources.lib.ui import list_window as lw

    text = TARGET.getLocalizedString
    rows = (
        lw.ListRow(key="Master user", title="Master user"),
        lw.ListRow(key="Kids", title="Kids", detail=text(30098).replace("%s", "Ben")),
        lw.ListRow(key="Old profile", title="Old profile", tag=text(30058)),
    )
    request = lw.ListRequest(
        heading=text(30114).replace("%s", "Anna"), rows=rows, count_one=text(30105), count_all=text(30106),
        pick=True, ticked=("Kids", "Old profile"),
    )
    dialog = window.PickListDialog(window.LIST_XML, path, "Default", "1080i")
    dialog.prepare(request, lw.ListState(), text)
    try:
        dialog.doModal()
    finally:
        dialog.stop()


def _viewer(scene: dict, path: str) -> None:
    dialog = window.ViewerDialog(window.VIEWER_XML, path, "Default", "1080i")
    dialog.prepare(scene["heading"], scene["message"], TARGET.getLocalizedString)
    try:
        dialog.doModal()
    finally:
        dialog.stop()


WINDOWS = {
    window.WHO_WATCHED_XML: _who_watched,
    window.LIST_XML: _list,
    window.CONFIRM_XML: _confirm,
    window.VIEWER_XML: _viewer,
}


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
