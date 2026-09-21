# SPDX-License-Identifier: GPL-2.0-only
"""Domain objects. These are what the addon reasons about; payload.py turns them into JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MediaType = Literal["episode", "movie"]
EventKind = Literal["start", "pause", "resume", "progress", "stop"]
ViewersSource = Literal["playlist", "profile", "prompt"]
# The contract's vocabulary. Typed rather than a bare str so a value outside it cannot
# reach the wire, and defaulted to the value the overwhelming majority of items carry.
Source = Literal["library", "plexkodiconnect"]


@dataclass(frozen=True)
class Viewer:
    name: str
    playlists: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    # Lifted to the top level of the payload by payload.py rather than sent inside device,
    # which is what the contract asks for. It lives here because Device is already "what
    # this installation is" and is built once, at the composition root.
    addon_version: str = ""


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
    source: Source = "library"
    # The domain fact behind source. Kept so the skip policy branches on what the addon
    # observed rather than on a wire string whose vocabulary the receiver owns.
    is_pkc: bool = False
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


@dataclass(frozen=True)
class PingEvent:
    """The heartbeat that tells CrossWatch this Kodi is reporting and need not be polled.

    A separate type rather than a PlaybackEvent with optional fields: nothing that handles
    playback can ever receive one, so an optional media would be a None that every consumer
    has to defend against and none can actually see.
    """

    event_id: str
    sent_at: str
    viewers: tuple[str, ...]
    # Running count of PlexKodiConnect playbacks declined. It rides on the ping because the
    # addon has no status UI and CrossWatch does, so the number surfaces where someone can
    # act on it. Proposed to the CrossWatch maintainer, not yet agreed: if declined, drop
    # this field and the single line in payload.py that emits it. Nothing else reads it.
    pkc_skipped: int = 0
