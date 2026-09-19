# SPDX-License-Identifier: GPL-2.0-only
"""Domain objects. These are what the addon reasons about; payload.py turns them into JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MediaType = Literal["episode", "movie"]
EventKind = Literal["start", "pause", "resume", "progress", "stop"]
ViewersSource = Literal["playlist", "profile", "prompt"]


@dataclass(frozen=True)
class Viewer:
    name: str
    playlists: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Device:
    id: str
    name: str


@dataclass(frozen=True)
class MediaItem:
    media_type: MediaType
    library_id: int | None
    show_library_id: int | None
    title: str | None
    year: int | None
    season: int | None
    episode: int | None
    episode_title: str | None
    show_ids: dict[str, str] = field(default_factory=dict)
    episode_ids: dict[str, str] = field(default_factory=dict)
    file: str | None = None
    source: str = "kodi"
    plex_rating_key: str | None = None


@dataclass(frozen=True)
class PlaybackEvent:
    kind: EventKind
    event_id: str
    session_id: str
    sent_at: str
    media: MediaItem
    viewers: tuple[str, ...]
    viewers_source: ViewersSource | None
    position_ms: int | None
    duration_ms: int | None
    percent: float | None
    completed: bool = False
