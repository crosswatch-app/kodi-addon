# SPDX-License-Identifier: GPL-2.0-only
"""What the viewers window shows and returns, free of Kodi windows.

Each row carries its viewer's whole panel as list item properties, so the panel follows the
highlighted row inside Kodi and no Python runs on a focus move.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Text slots between the viewer's name and the route line. The XML has one set of controls
# per slot; tests/test_skin_xml.py checks the two agree.
PANEL_SLOTS = 8

ROUTE_ACCEPTED = "accepted"
ROUTE_REFUSED = "refused"


@dataclass(frozen=True)
class PanelLine:
    text: str
    heading: bool = False
    tag: str = ""
    warn: bool = False


@dataclass(frozen=True)
class ViewerRow:
    key: str
    lines: tuple[PanelLine, ...] = ()
    # "" when CrossWatch made no claim about this name: unpaired, not asked yet, no reply.
    route: str = ""

    @property
    def warn(self) -> bool:
        return self.route == ROUTE_REFUSED or any(line.warn for line in self.lines)


@dataclass(frozen=True, kw_only=True)
class ViewersRequest:
    heading: str
    count: str
    rows: tuple[ViewerRow, ...]
    more: str
    route_ok: str
    route_missing: str


@dataclass(frozen=True)
class ViewersResult:
    # "playlists", "profiles", "rename", "remove", "add" or "close"
    action: str
    # The highlighted viewer, also for add and close, so the screen can reopen on it.
    key: str = ""


def fit(lines: Sequence[PanelLine], more: str) -> tuple[PanelLine, ...]:
    """At most PANEL_SLOTS lines. Cut lines end in "and N more", N counting what was left out
    except headings, and a heading is not left dangling above it. The cut line carries the
    warning of any line it hides, since the row's icon promises the panel shows the reason."""
    if len(lines) <= PANEL_SLOTS:
        return tuple(lines)
    kept = list(lines[: PANEL_SLOTS - 1])
    while kept and kept[-1].heading:
        kept.pop()
    hidden = lines[len(kept):]
    left_out = sum(1 for line in hidden if not line.heading)
    # replace rather than %: a translation that drops the placeholder must not raise.
    return (*kept, PanelLine(more.replace("%s", str(left_out), 1), warn=any(line.warn for line in hidden)))


def properties(row: ViewerRow, request: ViewersRequest) -> dict[str, str]:
    route_text = {ROUTE_ACCEPTED: request.route_ok, ROUTE_REFUSED: request.route_missing}
    props = {
        "warn": "true" if row.warn else "",
        "route": row.route,
        "route_text": route_text.get(row.route, ""),
    }
    shown = fit(row.lines, request.more)
    for n in range(1, PANEL_SLOTS + 1):
        line = shown[n - 1] if n <= len(shown) else PanelLine("")
        props[f"slot{n}_head"] = line.text if line.heading else ""
        props[f"slot{n}"] = "" if line.heading else line.text
        props[f"slot{n}_tag"] = line.tag
        props[f"slot{n}_warn"] = "true" if line.warn else ""
    return props


def start_position(rows: Sequence[ViewerRow], key: str) -> int:
    return next((index for index, row in enumerate(rows) if row.key == key), 0)
