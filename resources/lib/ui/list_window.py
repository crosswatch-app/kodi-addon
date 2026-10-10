# SPDX-License-Identifier: GPL-2.0-only
"""What a searchable CrossWatch list shows and returns, free of Kodi windows.

A screen hands the window rows and optional filters; the window hands back what the
household did and where they were, so the screen can act and reopen the list at the same
search, filter and row.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from resources.lib.constants import WINDOW_COUNT_SOME


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


# Keyword-only: the fields grew, and a positional call written for the old order would put
# a button label into the count without any error.
@dataclass(frozen=True, kw_only=True)
class ListRequest:
    heading: str
    rows: tuple[ListRow, ...]
    # The screen's own count wording: "1 show" and "%s shows", "1 playlist" and "%s playlists".
    count_one: str
    count_all: str
    # Empty hides the filter button. The first one is the unfiltered view.
    filters: tuple[ListFilter, ...] = ()
    # Empty hides the bulk button.
    bulk_all: str = ""
    bulk_shown: str = ""
    # The bulk button acts on the screen, not on the rows shown, so it stays with none.
    bulk_always: bool = False
    # False for rows that never have artwork: the thumbnail column is dropped, not left empty.
    thumbs: bool = True
    # A pick list: OK ticks a row in place, Done returns every ticked key.
    pick: bool = False
    ticked: tuple[str, ...] = ()


@dataclass(frozen=True)
class ListResult:
    # "open" (OK on a row), "bulk" (the bulk button), "done" (Done on a pick list) or "close"
    action: str
    state: ListState
    key: str = ""
    keys: tuple[str, ...] = ()


def visible(rows: Sequence[ListRow], search: str, row_filter: ListFilter | None) -> list[ListRow]:
    needle = search.strip().casefold()

    def found(row: ListRow) -> bool:
        return not needle or any(needle in text.casefold() for text in (row.title, *row.names))

    return [row for row in rows if found(row) and (row_filter is None or row_filter.match(row))]


def count_text(request: ListRequest, localised: Callable[[int], str], shown: int) -> str:
    # replace rather than %: a translation that drops a placeholder must not raise.
    total = len(request.rows)
    if shown == total == 1:
        return request.count_one
    if shown == total:
        return request.count_all.replace("%s", str(total), 1)
    return localised(WINDOW_COUNT_SOME).replace("%s", str(shown), 1).replace("%s", str(total), 1)


def bulk_label(request: ListRequest, shown: int) -> str:
    """The bulk button's label; empty hides the button."""
    if request.bulk_always:
        return request.bulk_all
    if not shown:
        return ""
    if shown == len(request.rows):
        return request.bulk_all
    return request.bulk_shown.replace("%s", str(shown), 1)


def toggle(ticked: frozenset[str], key: str) -> frozenset[str]:
    return ticked - {key} if key in ticked else ticked | {key}


def picked(rows: Sequence[ListRow], ticked: Collection[str]) -> tuple[str, ...]:
    """The ticked keys in row order. A search narrows what is shown, not what is chosen, so
    rows it hides count too."""
    return tuple(row.key for row in rows if row.key in ticked)
