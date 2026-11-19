# SPDX-License-Identifier: GPL-2.0-only
"""Kodi's local timestamps and the UTC ones library sync uses.

Kodi stores lastplayed and dateadded as local wall-clock text ("2026-11-11 20:10:00") with no
zone, and the sync contract carries UTC ("2026-11-11T19:10:00Z"). Everything in the sync
record is UTC; Kodi's strings are converted when read and back only when written, with the
OS's zone rules, which are the rules Kodi wrote them with.

In the hour a clock falls back, one local string names two instants. The earlier one is
taken, and converting back gives the same string, so a value read from Kodi and written
back unchanged stays unchanged.

Log events: none.
"""

from __future__ import annotations

from datetime import UTC, datetime

KODI_FORMAT = "%Y-%m-%d %H:%M:%S"
UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def kodi_to_utc(local: str) -> str:
    text = (local or "").strip()
    if not text or text.startswith("0000"):
        return ""
    try:
        naive = datetime.strptime(text[:19], KODI_FORMAT)
    except ValueError:
        return ""
    return naive.astimezone().astimezone(UTC).strftime(UTC_FORMAT)


def utc_to_kodi(utc: str) -> str:
    moment = parse_utc(utc)
    if moment is None:
        return ""
    return moment.astimezone().strftime(KODI_FORMAT)


def parse_utc(text: str) -> datetime | None:
    """A UTC contract timestamp as an aware datetime, for comparing; None when empty or bad."""
    value = (text or "").strip()
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
