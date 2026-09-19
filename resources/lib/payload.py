# SPDX-License-Identifier: GPL-2.0-only
"""Domain event to wire format.

The only file that knows the payload shape. When CrossWatch publishes the authoritative
contract, this file absorbs the spelling change. Note that it is not a firewall against
every contract change: a change to what the addon must resolve, such as one event per
viewer, reaches the event model too.

Episodes are keyed on show ids plus season and episode number, because most episodes carry
no usable episode id.
"""

from __future__ import annotations

from typing import Any

from resources.lib.models import Device, PlaybackEvent

PAYLOAD_VERSION = 1


def build_payload(event: PlaybackEvent, device: Device) -> dict[str, Any]:
    body: dict[str, Any] = {
        "version": PAYLOAD_VERSION,
        "event": event.kind,
        "event_id": event.event_id,
        "session_id": event.session_id,
        "sent_at": event.sent_at,
        "device": {"id": device.id, "name": device.name},
        "viewers": list(event.viewers),
        "media": _media(event),
        "progress": _progress(event),
    }
    if event.viewers_source:
        body["viewers_source"] = event.viewers_source
    return body


def _media(event: PlaybackEvent) -> dict[str, Any]:
    media = event.media
    out: dict[str, Any] = {
        "source": media.source,
        "type": media.media_type,
        "title": media.title,
        "year": media.year,
    }
    if media.media_type == "episode":
        out["season"] = media.season
        out["episode"] = media.episode
        if media.episode_title:
            out["episode_title"] = media.episode_title
        if media.show_ids:
            out["show_ids"] = dict(media.show_ids)
        if media.episode_ids:
            out["episode_ids"] = dict(media.episode_ids)
    elif media.show_ids:
        out["ids"] = dict(media.show_ids)
    if media.file:
        out["file"] = media.file
    if media.plex_rating_key:
        out["plex_rating_key"] = media.plex_rating_key
    return out


def _progress(event: PlaybackEvent) -> dict[str, Any]:
    """Omit percent when duration was never known.

    Sending 0.0 for an unknown duration is not harmless: the receiver discards a stop below
    one percent without sending anything, so a fully watched item would disappear.
    """
    out: dict[str, Any] = {}
    if event.percent is not None:
        out["percent"] = event.percent
    if event.position_ms is not None:
        out["position_ms"] = event.position_ms
    if event.duration_ms is not None:
        out["duration_ms"] = event.duration_ms
    if event.completed:
        out["completed"] = True
    return out
