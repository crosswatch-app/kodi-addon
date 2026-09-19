# SPDX-License-Identifier: GPL-2.0-only
"""One playback, from onAVStarted to stop.

Player.getTime and getTotalTime raise once playback stops, so the final position cannot be
read at onPlayBackStopped. The tick samples while playback runs and the stop event uses the
last sample.

An unknown duration is a real state, not an error: Kodi reports zero for live TV, for
streams and at the instant onAVStarted fires. percent() therefore returns None rather than
0.0, because the receiver discards a stop below one percent.
"""

from __future__ import annotations

from resources.lib.identity import Identity
from resources.lib.models import MediaItem


class PlaybackSession:
    def __init__(self, session_id: str, media: MediaItem, identity: Identity) -> None:
        self.session_id = session_id
        self.media = media
        self.identity = identity
        self.position_ms: int | None = None
        self.duration_ms: int | None = None
        self.completed = False
        self._emitted_bucket = 0

    def sample(self, position_ms: int | None, duration_ms: int | None) -> None:
        if position_ms is not None:
            self.position_ms = int(position_ms)
        if duration_ms:
            self.duration_ms = int(duration_ms)

    def percent(self) -> float | None:
        if not self.duration_ms or self.position_ms is None:
            return None
        raw = (float(self.position_ms) / float(self.duration_ms)) * 100.0
        return round(max(0.0, min(100.0, raw)), 1)

    def mark_complete(self) -> None:
        """Played to the end. The flag survives an unknown duration; position cannot."""
        self.completed = True
        if self.duration_ms:
            self.position_ms = self.duration_ms

    def reset_cadence(self) -> None:
        """Forget the emitted bucket. The bucket is in units of the configured step, so a
        step change mid-session would otherwise silence progress or skip a block of it."""
        self._emitted_bucket = 0

    def should_emit_progress(self, step: int) -> bool:
        """True once per step-sized bucket, so cadence does not depend on the tick rate."""
        percent = self.percent()
        if step <= 0 or percent is None:
            return False
        bucket = int(percent // step)
        if bucket > self._emitted_bucket:
            self._emitted_bucket = bucket
            return True
        return False
