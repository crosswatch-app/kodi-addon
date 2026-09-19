# SPDX-License-Identifier: GPL-2.0-only
"""What is playing, resolved once at start.

Episode-level unique ids are mostly absent: measured on a real library, 361 of 400 sampled
episodes carried only an "unknown" key, which CrossWatch discards. So the usable key is
show-level ids plus season and episode number, with episode ids sent when they are real.

Keys under "unknown" are skipped deliberately: there is no safe way to tell what they are.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from resources.lib.kodi import KodiApi
from resources.lib.log import get_logger, log_timing, redact
from resources.lib.models import MediaItem

_log = get_logger("media")

PKC_PREFIX = "plugin://plugin.video.plexkodiconnect"
_RATING_KEY = re.compile(r"/(\d+)/?$")

_ITEM_PROPERTIES = ["title", "showtitle", "season", "episode", "year", "tvshowid", "file", "uniqueid"]


def strip_credentials(path: str) -> str:
    """Remove user:password@ and redact any query string from a VFS path.

    Kodi carries share credentials inline and strips them in FileOperations but not on the
    Player.GetItem path, so without this they reach the payload and the log.

    The query string goes through the logging layer's own redaction rather than a second
    rule here. A token in a stream URL is a credential too, and payload.py copies this
    value verbatim into the POST body, so the log being safe is not enough: one rule has to
    cover both places or the two will drift apart.
    """
    text = str(path or "")
    if "://" not in text:
        return text
    if "@" in text:
        try:
            parts = urlsplit(text)
        except ValueError:
            return text
        if "@" in parts.netloc:
            host = parts.netloc.rsplit("@", 1)[-1]
            text = urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    return redact(text)


def _clean_ids(uniqueid: Any) -> dict[str, str]:
    if not isinstance(uniqueid, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in uniqueid.items():
        name = str(key or "").strip().lower()
        text = str(value or "").strip()
        if not text or name == "unknown":
            continue
        out[name] = text
    return out


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class MediaResolver:
    def __init__(self, kodi: KodiApi) -> None:
        self._kodi = kodi
        self._show_ids: dict[int, dict[str, str]] = {}

    def forget_shows(self) -> None:
        """Drop memoised show ids; a library scan can change them."""
        self._show_ids.clear()

    def resolve(self) -> MediaItem | None:
        with log_timing(_log, "media.resolve") as timer:
            player_id = self._active_player()
            if player_id is None:
                return None
            timer.mark("active_player")

            payload = self._kodi.jsonrpc("Player.GetItem", {"playerid": player_id, "properties": _ITEM_PROPERTIES})
            item = payload.get("item")
            if not isinstance(item, dict):
                return None
            timer.mark("get_item")

            media_type = str(item.get("type") or "").strip().lower()
            if media_type not in {"episode", "movie"}:
                _log.info("media.ignored", media_type=media_type or "unknown")
                return None

            file_path = strip_credentials(str(item.get("file") or ""))
            is_pkc = file_path.startswith(PKC_PREFIX)
            own_ids = _clean_ids(item.get("uniqueid"))
            raw_id = _int_or_none(item.get("id"))
            library_id = raw_id if raw_id is not None and raw_id >= 0 else None
            source = "plexkodiconnect" if is_pkc else "kodi"
            rating_key = self._rating_key(file_path) if is_pkc else None

            if media_type == "episode":
                tvshowid = _int_or_none(item.get("tvshowid"))
                tvshowid = tvshowid if tvshowid and tvshowid > 0 else None
                show_ids = self._lookup_show_ids(tvshowid) if tvshowid else {}
                timer.mark("show_ids")
                media = MediaItem(
                    media_type="episode",
                    library_id=library_id,
                    show_library_id=tvshowid,
                    title=str(item.get("showtitle") or "") or None,
                    year=_int_or_none(item.get("year")),
                    season=_int_or_none(item.get("season")),
                    episode=_int_or_none(item.get("episode")),
                    episode_title=str(item.get("title") or "") or None,
                    show_ids=show_ids,
                    episode_ids=own_ids,
                    file=file_path or None,
                    source=source,
                    plex_rating_key=rating_key,
                )
            else:
                media = MediaItem(
                    media_type="movie",
                    library_id=library_id,
                    show_library_id=None,
                    title=str(item.get("title") or "") or None,
                    year=_int_or_none(item.get("year")),
                    season=None,
                    episode=None,
                    episode_title=None,
                    show_ids=own_ids,
                    episode_ids={},
                    file=file_path or None,
                    source=source,
                    plex_rating_key=rating_key,
                )

        _log.info(
            "media.resolved",
            media_type=media.media_type,
            library_id=media.library_id,
            show_library_id=media.show_library_id,
            source=media.source,
            ids=",".join(sorted(media.show_ids)) or "none",
        )
        return media

    def _active_player(self) -> int | None:
        result = self._kodi.jsonrpc("Player.GetActivePlayers")
        # Bound once: a guard written over a second .get() call says nothing about the
        # value the first one returned.
        players = result.get("result")
        if not isinstance(players, list):
            return None
        for player in players:
            if isinstance(player, dict) and player.get("type") == "video":
                return int(player.get("playerid", 1))
        return None

    def _lookup_show_ids(self, tvshowid: int) -> dict[str, str]:
        cached = self._show_ids.get(tvshowid)
        if cached is not None:
            return dict(cached)
        try:
            details = self._kodi.jsonrpc(
                "VideoLibrary.GetTVShowDetails", {"tvshowid": tvshowid, "properties": ["uniqueid"]}
            )
        except Exception as exc:
            _log.warning("media.show_lookup_failed", tvshowid=tvshowid, error=str(exc))
            return {}
        show = details.get("tvshowdetails")
        ids = _clean_ids(show.get("uniqueid")) if isinstance(show, dict) else {}
        # Negative results are cached too, so a show with no ids is not re-queried per episode.
        self._show_ids[tvshowid] = ids
        return dict(ids)

    @staticmethod
    def _rating_key(file_path: str) -> str | None:
        match = _RATING_KEY.search(file_path)
        return match.group(1) if match else None
