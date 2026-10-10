# SPDX-License-Identifier: GPL-2.0-only
"""The settings screen for remembered who-watched answers: change one, forget one, forget all.

Shows are matched to their library titles by the same key rule the prompt uses, so a row
here always refers to the answer playback would use. Titles and names appear on screen
only; the log gets counts and actions, as everywhere that reaches Kodi's shared log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import NamedTuple

from resources.lib import log as logmod
from resources.lib import paths
from resources.lib.config import read_settings
from resources.lib.constants import (
    NOTIFY_HEADING,
    REMEMBERED_CONFIRM_FORGET_ALL,
    REMEMBERED_CONFIRM_FORGET_ONE,
    REMEMBERED_COVERED,
    REMEMBERED_HEADING,
    REMEMBERED_LIBRARY_ID,
    REMEMBERED_NONE_YET,
    REMEMBERED_NOT_IN_LIBRARY,
    REMEMBERED_NOT_STABLE,
    REMEMBERED_WILL_ASK,
    WINDOW_ALL,
    WINDOW_FORGET_ALL,
    WINDOW_FORGET_SHOWN,
)
from resources.lib.kodi import KodiApi, KodiRuntime
from resources.lib.media import clean_ids
from resources.lib.models import Viewer
from resources.lib.playlist_index import IndexBuilder
from resources.lib.prompt import LIBRARY_KEY_PREFIX, choose_viewers, key_for_show
from resources.lib.storage import JsonViewerStore, PromptMemory, RememberedAnswer
from resources.lib.ui.list_window import ListFilter, ListRequest, ListRow, ListState
from resources.lib.ui.who_watched import year_subtitle

_log = logmod.get_logger("config")

class Show(NamedTuple):
    title: str
    year: int | None
    library_id: int | None
    poster: str


# Library shows by answer key.
Library = dict[str, Show]


@dataclass(frozen=True)
class Row:
    key: str
    name: str  # what the list shows: the title, or a readable stand-in for the key
    title: str | None
    detail: str  # who the answer is for, or "will ask again"
    tag: str  # covered by playlist, not in library, or not stable
    thumb: str
    viewers: tuple[str, ...]
    # False for an answer keyed on Kodi's database id that can no longer be checked against
    # its show. Changing it would only re-store an answer playback will not use.
    changeable: bool


def library_shows(kodi: KodiApi) -> Library | None:
    """None when the library could not be read, so rows are not wrongly marked missing."""
    try:
        result = kodi.jsonrpc("VideoLibrary.GetTVShows", {"properties": ["title", "year", "uniqueid", "art"]})
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
        art = show.get("art")
        poster = art.get("poster") if isinstance(art, dict) else None
        if key:
            shows[key] = Show(
                str(show.get("title") or ""),
                year if isinstance(year, int) and year else None,
                library_id if isinstance(library_id, int) else None,
                poster if isinstance(poster, str) else "",
            )
    return shows


def covered_by_playlist(kodi: KodiApi, viewers: list[Viewer], library: Library | None) -> frozenset[str]:
    """Answer keys of shows a viewer's playlist holds, so playback never asks or recalls them.

    Built with the same expansion playback uses, so the screen and playback cannot disagree.
    An unreadable playlist marks nothing, as it decides nothing during playback either.
    """
    if not library:
        return frozenset()
    builder = IndexBuilder(kodi, viewers, time.monotonic)
    while not builder.step():
        pass
    index = builder.result()
    if index is None:
        return frozenset()
    return frozenset(key for key, show in library.items() if index.viewers_for("tvshow", show.library_id))


def _fallback_name(kodi: KodiApi, key: str) -> str:
    if key.startswith(LIBRARY_KEY_PREFIX):
        return kodi.localised(REMEMBERED_LIBRARY_ID).replace("%s", key[len(LIBRARY_KEY_PREFIX) :])
    # "show:tvdb:100" reads as "tvdb 100".
    return " ".join(key.split(":")[1:]) or key


def _row(
    kodi: KodiApi,
    key: str,
    answer: RememberedAnswer,
    library: Library | None,
    viewers: list[Viewer],
    covered: frozenset[str],
) -> Row:
    found = library.get(key) if library is not None else None
    title = answer.title
    changeable = True
    tag = ""
    if key.startswith(LIBRARY_KEY_PREFIX):
        # Trusted only while the show at that id is still the one answered for.
        changeable = answer.title is not None and (found is None or (answer.title, answer.year) == (found.title, found.year))
        if not changeable:
            tag = kodi.localised(REMEMBERED_NOT_STABLE)
        elif found is None and library is not None:
            tag = kodi.localised(REMEMBERED_NOT_IN_LIBRARY)
    elif found is not None:
        title = found.title or title
    elif library is not None:
        tag = kodi.localised(REMEMBERED_NOT_IN_LIBRARY)
    if not tag and key in covered:
        # Changing the answer is still allowed: it applies again if the show leaves the
        # playlist.
        tag = kodi.localised(REMEMBERED_COVERED)
    known = {v.name for v in viewers}
    current = tuple(n for n in answer.viewers if n in known)
    return Row(
        key=key,
        name=title or _fallback_name(kodi, key),
        title=title,
        detail=", ".join(current) if current else kodi.localised(REMEMBERED_WILL_ASK),
        tag=tag,
        thumb=found.poster if found is not None else "",
        viewers=current,
        changeable=changeable,
    )


def build_rows(
    entries: dict[str, RememberedAnswer],
    library: Library | None,
    viewers: list[Viewer],
    kodi: KodiApi,
    covered: frozenset[str] = frozenset(),
) -> list[Row]:
    rows = [_row(kodi, key, answer, library, viewers, covered) for key, answer in entries.items()]
    return sorted(rows, key=lambda row: row.name.casefold())


def _change(kodi: KodiApi, memory: PromptMemory, row: Row, library: Library | None, viewers: list[Viewer]) -> None:
    stored = memory.recall(row.key) or RememberedAnswer(viewers=())
    found = library.get(row.key) if library is not None else None
    year = stored.year if stored.title else (found.year if found else None)
    # No countdown and no closing on playback: this screen was opened on purpose.
    picked = choose_viewers(
        kodi,
        viewers,
        title=row.name,
        subtitle=year_subtitle(year),
        # From the library lookup the list was built from: no second request per show.
        poster=row.thumb,
        preselect=row.viewers,
        offer_forget=True,
    )
    if picked is None:
        return
    if not picked:
        memory.forget(row.key)
        _log.info("config.remembered_forgotten")
        return
    # Keep the stored identity; fill it in from the library only where an older answer
    # had none. A library-id answer reaches here only when it still matches its show.
    title = stored.title or (found.title if found else None)
    memory.remember(row.key, picked, title=title, year=year)
    _log.info("config.remembered_changed", viewers_count=len(picked))


def viewer_filters(kodi: KodiApi, viewers: list[Viewer]) -> tuple[ListFilter, ...]:
    """All, each configured viewer, then the shows that will be asked about again."""
    each = tuple(ListFilter(v.name, lambda row, name=v.name: name in row.names) for v in viewers)
    return (
        ListFilter(kodi.localised(WINDOW_ALL), lambda row: True),
        *each,
        ListFilter(kodi.localised(REMEMBERED_WILL_ASK), lambda row: not row.names),
    )


def _list_row(row: Row) -> ListRow:
    return ListRow(key=row.key, title=row.name, detail=row.detail, tag=row.tag, thumb=row.thumb, names=row.viewers)


def _forget_confirmed(kodi: KodiApi, count: int) -> bool:
    if count == 1:
        message = kodi.localised(REMEMBERED_CONFIRM_FORGET_ONE)
    else:
        message = kodi.localised(REMEMBERED_CONFIRM_FORGET_ALL).replace("%s", str(count))
    return kodi.confirm_window(kodi.localised(REMEMBERED_HEADING), message)


def run(kodi: KodiApi, memory: PromptMemory, viewers: list[Viewer]) -> None:
    library = library_shows(kodi)
    # Once per screen: the expansion is a whole-library query per playlist.
    covered = covered_by_playlist(kodi, viewers, library)
    filters = viewer_filters(kodi, viewers)
    state = ListState()
    while True:
        entries = memory.entries()
        if not entries:
            kodi.notify(kodi.localised(NOTIFY_HEADING), kodi.localised(REMEMBERED_NONE_YET))
            return
        rows = {row.key: row for row in build_rows(entries, library, viewers, kodi, covered)}
        request = ListRequest(
            heading=kodi.localised(REMEMBERED_HEADING),
            rows=tuple(_list_row(row) for row in rows.values()),
            filters=filters,
            bulk_all=kodi.localised(WINDOW_FORGET_ALL),
            bulk_shown=kodi.localised(WINDOW_FORGET_SHOWN),
        )
        result = kodi.list_window(request, state)
        state = result.state
        if result.action == "open" and result.key in rows:
            row = rows[result.key]
            if row.changeable:
                _change(kodi, memory, row, library, viewers)
            elif _forget_confirmed(kodi, 1):
                memory.forget(row.key)
                _log.info("config.remembered_forgotten")
        elif result.action == "bulk":
            keys = [key for key in result.keys if key in rows]
            if keys and _forget_confirmed(kodi, len(keys)):
                for key in keys:
                    memory.forget(key)
                _log.info("config.remembered_forgot_all", count=len(keys), shown=len(keys) != len(rows))
        elif result.action == "close":
            return


def main() -> None:
    kodi = KodiRuntime()
    settings = read_settings(kodi)
    # A separate interpreter with its own module state, as for the viewer dialog.
    logmod.configure(log_dir=paths.log_dir(kodi), debug=settings.debug_logging, sink=kodi.log)
    viewers = JsonViewerStore(paths.viewers_path(kodi)).viewers()
    run(kodi, PromptMemory(paths.prompts_path(kodi)), viewers)
