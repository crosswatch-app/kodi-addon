# SPDX-License-Identifier: GPL-2.0-only
"""What a searchable CrossWatch list shows and returns, free of Kodi windows.

A screen hands the window rows and optional filters; the window hands back what the
household did and where they were, so the screen can act and reopen the list at the same
search, filter and row.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from resources.lib.constants import WINDOW_COUNT_ALL, WINDOW_COUNT_ONE, WINDOW_COUNT_SOME


@dataclass(frozen=True)
class ListRow:
    key: str
    title: str
    detail: str = ""
    tag: str = ""
    thumb: str = ""
    # Matched by the search alongside the title, and by the screen's filters.
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ListFilter:
    label: str
    match: Callable[[ListRow], bool]


@dataclass(frozen=True)
class ListState:
    search: str = ""
    filter_index: int = 0
    position: int = 0


@dataclass(frozen=True)
class ListRequest:
    heading: str
    rows: tuple[ListRow, ...]
    # Empty hides the filter button. The first one is the unfiltered view.
    filters: tuple[ListFilter, ...]
    bulk_all: str
    bulk_shown: str


@dataclass(frozen=True)
class ListResult:
    action: str  # "open" (OK on a row), "bulk" (the bulk button) or "close"
    state: ListState
    key: str = ""
    keys: tuple[str, ...] = ()


def visible(rows: Sequence[ListRow], search: str, row_filter: ListFilter | None) -> list[ListRow]:
    needle = search.strip().casefold()

    def found(row: ListRow) -> bool:
        return not needle or any(needle in text.casefold() for text in (row.title, *row.names))

    return [row for row in rows if found(row) and (row_filter is None or row_filter.match(row))]


def count_text(localised: Callable[[int], str], shown: int, total: int) -> str:
    # replace rather than %: a translation that drops a placeholder must not raise.
    if shown == total == 1:
        return localised(WINDOW_COUNT_ONE)
    if shown == total:
        return localised(WINDOW_COUNT_ALL).replace("%s", str(total), 1)
    return localised(WINDOW_COUNT_SOME).replace("%s", str(shown), 1).replace("%s", str(total), 1)


def bulk_label(request: ListRequest, shown: int) -> str:
    if shown == len(request.rows):
        return request.bulk_all
    return request.bulk_shown.replace("%s", str(shown), 1)
