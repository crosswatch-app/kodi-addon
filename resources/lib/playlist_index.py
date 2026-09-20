# SPDX-License-Identifier: GPL-2.0-only
"""Smart playlist membership, resolved by asking Kodi to expand the playlist.

Files.GetDirectory on an .xsp returns the resolved members with their library ids, so no
.xsp parsing and no smart playlist logic is reimplemented. Membership is never decided by
comparing titles: a title rule can match two distinct library shows.

The build is incremental, one playlist per step, because an expansion costs a whole-library
query rather than a list read: tvshow_view INNER JOINs an aggregate over the episode table.
The controller steps it from the tick while nothing is playing, so the cost never lands on
the start of playback.

A build with any failure is discarded rather than published, so the previous index survives
and the next cycle retries. An empty index and a failed index are different states.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from resources.lib.kodi import KodiApi
from resources.lib.log import get_logger, log_timing
from resources.lib.models import Viewer

_log = get_logger("playlists")

PLAYLIST_DIR = "special://profile/playlists/video"
INDEXABLE_TYPES = {"tvshows": "tvshow", "movies": "movie"}

_TYPE = re.compile(r"<smartplaylist[^>]*\btype\s*=\s*[\"']([a-z]+)[\"']", re.IGNORECASE)


@dataclass(frozen=True)
class PlaylistIndex:
    by_key: dict[tuple[str, int], tuple[str, ...]] = field(default_factory=dict)
    built_at: float = 0.0

    def viewers_for(self, media_type: str, library_id: int | None) -> tuple[str, ...]:
        if library_id is None:
            return ()
        return self.by_key.get((media_type, int(library_id)), ())

    def is_empty(self) -> bool:
        return not self.by_key


def playlists_for(viewers: list[Viewer]) -> dict[str, list[str]]:
    """Playlist name to the viewers holding it, so a shared list is expanded once."""
    owners: dict[str, list[str]] = {}
    for viewer in viewers:
        for playlist in viewer.playlists:
            names = owners.setdefault(playlist, [])
            if viewer.name not in names:
                names.append(viewer.name)
    return owners


def _path(name: str) -> str:
    return f"{PLAYLIST_DIR}/{name}.xsp"


class IndexBuilder:
    """Builds one index, one playlist per step()."""

    def __init__(self, kodi: KodiApi, viewers: list[Viewer], clock: Callable[[], float]) -> None:
        self._kodi = kodi
        self._clock = clock
        self._owners = playlists_for(viewers)
        self._order = list(self._owners)
        self._pending = list(self._owners)
        self._by_key: dict[tuple[str, int], tuple[str, ...]] = {}
        self._result: PlaylistIndex | None = None
        self.failed = False

    def step(self) -> bool:
        """Process one playlist. Returns True when the build has finished."""
        if self._result is not None or self.failed:
            # Terminal: the build will be discarded, so expanding the rest would pay N-1
            # whole-library queries for a result that is thrown away.
            return True
        if not self._pending:
            self._finish()
            return True
        playlist = self._pending.pop(0)
        try:
            self._expand(playlist, self._owners[playlist])
        except Exception:
            # Already recorded by _expand; the build is failed and will be discarded.
            pass
        if not self._pending:
            self._finish()
            return True
        return False

    def result(self) -> PlaylistIndex | None:
        """The finished index, or None while building or after a failure."""
        return self._result

    def _finish(self) -> None:
        if self.failed:
            _log.warning("playlists.index_discarded", reason="expansion_failed")
            return
        self._result = PlaylistIndex(by_key=dict(self._by_key), built_at=self._clock())
        _log.info("playlists.index_built", playlists=len(self._owners), entries=len(self._by_key))

    def _declared_type(self, playlist: str) -> str | None:
        """None means the file could not be read, which is a failure, not a default.

        Defaulting an unreadable playlist to tvshows publishes an index that is silently
        missing a viewer's entire show set, and pays full cost for an episodes list.
        """
        text = self._kodi.read_text(_path(playlist))
        if text is None:
            return None
        match = _TYPE.search(text)
        return match.group(1).lower() if match else "tvshows"

    def _expand(self, playlist: str, names: list[str]) -> None:
        declared = self._declared_type(playlist)
        if declared is None:
            self.failed = True
            _log.warning("playlists.unreadable", index=self._index_of(playlist))
            # The position alone is not enough to act on: the viewer sees playlist names,
            # not their order in a config file. Named here at DEBUG, where the success path
            # already names it, so a rename is diagnosable without the name reaching a
            # WARNING and from there Kodi's shared log.
            _log.debug("playlists.unreadable_playlist", playlist=playlist)
            return
        media_type = INDEXABLE_TYPES.get(declared)
        if media_type is None:
            _log.warning("playlists.skipped", index=self._index_of(playlist), reason=f"type_{declared}")
            return
        # The try is inside the timing scope, so a failed expansion records outcome=error
        # rather than a success line that contradicts playlists.index_discarded.
        with log_timing(_log, "playlists.expand", index=self._index_of(playlist)):
            try:
                result = self._kodi.jsonrpc("Files.GetDirectory", {"directory": _path(playlist), "media": "video"})
            except Exception as exc:
                self.failed = True
                _log.warning("playlists.expand_failed", index=self._index_of(playlist), error=str(exc))
                _log.debug("playlists.failed_playlist", playlist=playlist)
                raise
        files = result.get("files")
        members = [f for f in files if isinstance(f, dict)] if isinstance(files, list) else []
        indexed = 0
        for member in members:
            raw_id = member.get("id")
            kind = str(member.get("type") or "").strip().lower()
            if kind not in {"tvshow", "movie"} or not isinstance(raw_id, int):
                continue
            key = (kind, raw_id)
            existing = self._by_key.get(key, ())
            self._by_key[key] = existing + tuple(n for n in names if n not in existing)
            indexed += 1
        if members and not indexed:
            _log.warning("playlists.no_indexable_members", index=self._index_of(playlist), members=len(members))
        # The name itself is household data, so it stays at DEBUG; higher levels carry the
        # position in the configured list, which is enough to identify it in a bug report.
        _log.debug("playlists.expanded", playlist=playlist, members=len(members), indexed=indexed)

    def _index_of(self, playlist: str) -> int:
        return self._order.index(playlist) if playlist in self._order else -1
