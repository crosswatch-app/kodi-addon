# SPDX-License-Identifier: GPL-2.0-only
"""The settings screen for remembered who-watched answers: change one, forget one, forget all.

Shows are matched to their library titles by the same key rule the prompt uses, so a row
here always refers to the answer playback would use. Titles and names appear on screen
only; the log gets counts and actions, as everywhere that reaches Kodi's shared log.
"""

from __future__ import annotations

from dataclasses import dataclass

from resources.lib import log as logmod
from resources.lib import paths
from resources.lib.config import read_settings
from resources.lib.constants import (
    LABEL_BACK,
    NOTIFY_HEADING,
    REMEMBERED_CHANGE,
    REMEMBERED_CONFIRM_FORGET_ALL,
    REMEMBERED_CONFIRM_FORGET_ONE,
    REMEMBERED_FORGET,
    REMEMBERED_FORGET_ALL,
    REMEMBERED_HEADING,
    REMEMBERED_LIBRARY_ID,
    REMEMBERED_NONE_YET,
    REMEMBERED_NOT_IN_LIBRARY,
    REMEMBERED_NOT_STABLE,
    REMEMBERED_WILL_ASK,
)
from resources.lib.kodi import KodiApi, KodiRuntime
from resources.lib.media import clean_ids
from resources.lib.models import Viewer
from resources.lib.prompt import LIBRARY_KEY_PREFIX, choose_viewers, heading_for, key_for_show
from resources.lib.storage import JsonViewerStore, PromptMemory, RememberedAnswer

_log = logmod.get_logger("config")

# Library shows by answer key: (title, year).
Library = dict[str, tuple[str, int | None]]


@dataclass(frozen=True)
class Row:
    key: str
    label: str
    title: str | None
    viewers: tuple[str, ...]
    # False for an answer keyed on Kodi's database id that can no longer be checked against
    # its show. Changing it would only re-store an answer playback will not use.
    changeable: bool


def library_shows(kodi: KodiApi) -> Library | None:
    """None when the library could not be read, so rows are not wrongly marked missing."""
    try:
        result = kodi.jsonrpc("VideoLibrary.GetTVShows", {"properties": ["title", "year", "uniqueid"]})
    except Exception as exc:
        _log.warning("config.library_lookup_failed", error=str(exc))
        return None
    shows: Library = {}
    for show in result.get("tvshows") or []:
        if not isinstance(show, dict):
            continue
        library_id = show.get("tvshowid")
        key = key_for_show(clean_ids(show.get("uniqueid")), library_id if isinstance(library_id, int) else None)
        year = show.get("year")
        if key:
            shows[key] = (str(show.get("title") or ""), year if isinstance(year, int) and year else None)
    return shows


def _fallback_name(kodi: KodiApi, key: str) -> str:
    if key.startswith(LIBRARY_KEY_PREFIX):
        return kodi.localised(REMEMBERED_LIBRARY_ID).replace("%s", key[len(LIBRARY_KEY_PREFIX) :])
    # "show:tvdb:100" reads as "tvdb 100".
    return " ".join(key.split(":")[1:]) or key


def _row(kodi: KodiApi, key: str, answer: RememberedAnswer, library: Library | None, viewers: list[Viewer]) -> Row:
    found = library.get(key) if library is not None else None
    title = answer.title
    changeable = True
    suffix = ""
    if key.startswith(LIBRARY_KEY_PREFIX):
        # Trusted only while the show at that id is still the one answered for.
        changeable = answer.title is not None and (found is None or (answer.title, answer.year) == found)
        if not changeable:
            suffix = kodi.localised(REMEMBERED_NOT_STABLE)
        elif found is None and library is not None:
            suffix = kodi.localised(REMEMBERED_NOT_IN_LIBRARY)
    elif found is not None:
        title = found[0] or title
    elif library is not None:
        suffix = kodi.localised(REMEMBERED_NOT_IN_LIBRARY)
    name = title or _fallback_name(kodi, key)
    if suffix:
        name = f"{name} ({suffix})"
    known = {v.name for v in viewers}
    current = tuple(n for n in answer.viewers if n in known)
    who = ", ".join(current) if current else kodi.localised(REMEMBERED_WILL_ASK)
    return Row(key=key, label=f"{name}: {who}", title=title, viewers=current, changeable=changeable)


def build_rows(
    entries: dict[str, RememberedAnswer], library: Library | None, viewers: list[Viewer], kodi: KodiApi
) -> list[Row]:
    rows = [_row(kodi, key, answer, library, viewers) for key, answer in entries.items()]
    return sorted(rows, key=lambda row: row.label.casefold())


def _change(kodi: KodiApi, memory: PromptMemory, row: Row, library: Library | None, viewers: list[Viewer]) -> None:
    picked = choose_viewers(kodi, heading_for(kodi, row.title), viewers, preselect=row.viewers)
    if picked is None:
        return
    if not picked:
        memory.forget(row.key)
        _log.info("config.remembered_forgotten")
        return
    stored = memory.recall(row.key) or RememberedAnswer(viewers=())
    found = library.get(row.key) if library is not None else None
    # Keep the stored identity; fill it in from the library only where an older answer
    # had none. A library-id answer reaches here only when it still matches its show.
    title = stored.title or (found[0] if found else None)
    year = stored.year if stored.title else (found[1] if found else None)
    memory.remember(row.key, picked, title=title, year=year)
    _log.info("config.remembered_changed", viewers_count=len(picked))


def run(kodi: KodiApi, memory: PromptMemory, viewers: list[Viewer]) -> None:
    library = library_shows(kodi)
    while True:
        entries = memory.entries()
        if not entries:
            kodi.notify(kodi.localised(NOTIFY_HEADING), kodi.localised(REMEMBERED_NONE_YET))
            return
        rows = build_rows(entries, library, viewers, kodi)
        # No Done row: Kodi's select dialog has its own Cancel, and Back closes it too.
        options = [*(row.label for row in rows), kodi.localised(REMEMBERED_FORGET_ALL)]
        choice = kodi.select(kodi.localised(REMEMBERED_HEADING), options)
        if choice < 0 or choice > len(rows):
            return
        if choice == len(rows):
            if len(rows) == 1:
                message = kodi.localised(REMEMBERED_CONFIRM_FORGET_ONE)
            else:
                message = kodi.localised(REMEMBERED_CONFIRM_FORGET_ALL).replace("%s", str(len(rows)))
            if kodi.confirm(kodi.localised(REMEMBERED_HEADING), message):
                memory.forget_all()
                _log.info("config.remembered_forgot_all", count=len(rows))
            continue
        row = rows[choice]
        actions = [REMEMBERED_CHANGE, REMEMBERED_FORGET, LABEL_BACK] if row.changeable else [REMEMBERED_FORGET, LABEL_BACK]
        picked = kodi.select(row.label, [kodi.localised(a) for a in actions])
        action = actions[picked] if 0 <= picked < len(actions) else LABEL_BACK
        if action == REMEMBERED_CHANGE:
            _change(kodi, memory, row, library, viewers)
        elif action == REMEMBERED_FORGET:
            memory.forget(row.key)
            _log.info("config.remembered_forgotten")


def main() -> None:
    kodi = KodiRuntime()
    settings = read_settings(kodi)
    # A separate interpreter with its own module state, as for the viewer dialog.
    logmod.configure(log_dir=paths.log_dir(kodi), debug=settings.debug_logging, sink=kodi.log)
    viewers = JsonViewerStore(paths.viewers_path(kodi)).viewers()
    run(kodi, PromptMemory(paths.prompts_path(kodi)), viewers)
