# SPDX-License-Identifier: GPL-2.0-only
"""What a ping's reply says about CrossWatch's routes for this Kodi.

CrossWatch answers a ping with its routes and, per route, which of the viewers the ping
named it accepts (docs/contract.md, the ping reply). The viewer list uses that to mark a
viewer no route takes. Only the count and our own names are kept: route labels name the
destination accounts and are of no use here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RouteFacts:
    count: int
    accepted: frozenset[str]
    # The names the ping asked about. CrossWatch answers only for those, so a viewer added or
    # renamed since is not known to be refused: no claim is made about them.
    asked: frozenset[str]


def parse_routes(reply: dict[str, Any], asked: Sequence[str]) -> RouteFacts | None:
    """None when the reply has no routes list: an older CrossWatch makes no claim."""
    routes = reply.get("routes")
    if not isinstance(routes, list):
        return None
    count = 0
    accepted: set[str] = set()
    for route in routes:
        if not isinstance(route, dict):
            continue
        count += 1
        names = route.get("viewers")
        if isinstance(names, list):
            accepted |= {name for name in names if isinstance(name, str) and name}
    return RouteFacts(count, frozenset(accepted), frozenset(asked))
