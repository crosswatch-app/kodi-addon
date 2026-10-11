# SPDX-License-Identifier: GPL-2.0-only
"""Viewers and the people CrossWatch lists (docs/contract.md, People).

A viewer linked to a person keeps that person's id and takes over the person's name when it
changes, because the profile name is what CrossWatch routes on. Pure, so the service and the
settings script share it without either importing the other.
"""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace

from resources.lib.models import Viewer
from resources.lib.routes import Person


def crosswatch_name(name: str) -> str:
    """The name as CrossWatch compares it: lower case, everything but a to z and 0 to 9 dropped
    (normalize_media_account_name in CrossWatch's cw_platform/account_match.py)."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def match_person(name: str, people: Sequence[Person], taken: Collection[str]) -> Person | None:
    """The person a viewer name links to, as CrossWatch would route it.

    CrossWatch compares loosely, except between two profiles that differ only in spaces or
    punctuation: each of those takes only its own spelling, apart from case. A name with
    nothing CrossWatch can read compares by case only, so it is not linked to every other
    such name.
    """
    free = [p for p in people if p.id not in taken]
    key = crosswatch_name(name)
    if not key:
        return next((p for p in free if p.name.casefold() == name.casefold()), None)
    loose = [p for p in free if crosswatch_name(p.name) == key]
    if len(loose) == 1:
        return loose[0]
    return next((p for p in loose if p.name.casefold() == name.casefold()), None)


@dataclass(frozen=True)
class Reconciled:
    viewers: list[Viewer]
    renamed: tuple[tuple[str, str], ...]
    linked: int
    skipped: int

    @property
    def changed(self) -> bool:
        return bool(self.renamed or self.linked)


def reconcile(viewers: Sequence[Viewer], people: Sequence[Person]) -> Reconciled:
    by_id = {p.id: p for p in people}
    out = list(viewers)
    renamed: list[tuple[str, str]] = []
    skipped = 0
    for index, viewer in enumerate(out):
        person = by_id.get(viewer.person_id or "")
        if person is None or person.name == viewer.name:
            continue
        # A second viewer under the same name would merge two people's playlists and answers.
        clash = any(v.name.casefold() == person.name.casefold() for i, v in enumerate(out) if i != index)
        if clash:
            skipped += 1
            continue
        renamed.append((viewer.name, person.name))
        out[index] = replace(viewer, name=person.name)
    linked = 0
    taken = {v.person_id for v in out if v.person_id}
    for index, viewer in enumerate(out):
        if viewer.person_id:
            continue
        person = match_person(viewer.name, people, taken)
        if person is not None:
            out[index] = replace(viewer, person_id=person.id)
            taken.add(person.id)
            linked += 1
    return Reconciled(out, tuple(renamed), linked, skipped)
