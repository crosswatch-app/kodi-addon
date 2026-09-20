# SPDX-License-Identifier: GPL-2.0-only
"""All playback logic, with no Kodi classes in sight.

The adapters forward to this, which is what makes the whole lifecycle unit testable. The
clock, the monotonic source and the id generator are injected for the same reason.

Everything here runs on the service thread, including the callbacks: Kodi queues them and
executes them from Monitor.waitForAbort. There is therefore no concurrency to guard, and no
locks. The reporter worker is the only other thread and it touches only its own queue.
"""

from __future__ import annotations

from collections.abc import Callable

from resources.lib import identity as identity_mod
from resources.lib import prompt as prompt_mod
from resources.lib.advanced_settings import Thresholds
from resources.lib.config import Settings
from resources.lib.constants import (
    FAILURE_BACKOFF_SECONDS,
    MAX_FAILURE_BACKOFF_SECONDS,
    PING_INTERVAL_SECONDS,
    PROMPT_AUTOCLOSE_SECONDS,
)
from resources.lib.kodi import KodiApi
from resources.lib.log import get_logger
from resources.lib.media import MediaResolver
from resources.lib.models import EventKind, MediaItem, PingEvent, PlaybackEvent, Viewer
from resources.lib.playlist_index import IndexBuilder, PlaylistIndex
from resources.lib.reporter import EventQueue
from resources.lib.service.session import PlaybackSession
from resources.lib.storage import PromptMemory, ViewerStore

_log = get_logger("service")


class Controller:
    def __init__(
        self,
        kodi: KodiApi,
        viewer_store: ViewerStore,
        memory: PromptMemory,
        media_resolver: MediaResolver,
        queue: EventQueue,
        settings: Settings,
        thresholds: Thresholds,
        clock: Callable[[], str],
        monotonic: Callable[[], float],
        ids: Callable[[], str],
    ) -> None:
        self._kodi = kodi
        self._store = viewer_store
        self._memory = memory
        self._media = media_resolver
        self._queue = queue
        self._settings = settings
        self._thresholds = thresholds
        self._clock = clock
        self._monotonic = monotonic
        self._ids = ids

        self._session: PlaybackSession | None = None
        self._pending_stop: PlaybackSession | None = None
        self._paused = False
        self._index: PlaylistIndex | None = None
        self._builder: IndexBuilder | None = None
        self._index_dirty = True
        self._failures = 0
        self._failed_at: float | None = None
        # Read by the ping, which is the only place the user can see it: the addon has no
        # status UI of its own.
        self.pkc_skipped = 0
        self._last_ping_at: float | None = None
        self._shutting_down = False

    # -- lifecycle ---------------------------------------------------------

    def on_settings_changed(self, settings: Settings, thresholds: Thresholds) -> None:
        self._settings = settings
        self._thresholds = thresholds
        self.invalidate_index()

    def invalidate_index(self) -> None:
        self._index_dirty = True
        self._builder = None
        self._failures = 0
        self._failed_at = None
        self._media.forget_shows()
        _log.info("service.index_invalidated")

    # -- playback callbacks ------------------------------------------------

    def on_av_started(self) -> None:
        # A parked stop goes out unattributed rather than delaying the next item: in a binge
        # nobody would answer a prompt about the episode that just finished.
        self._flush_pending(allow_prompt=False)
        if self._session is not None:
            self._park(self._session, reason="superseded")
            self._flush_pending(allow_prompt=False)
        # Cleared here, not after a successful resolve. An early return below must not leave
        # a stopped session installed: the tick would then sample the new item into it and
        # emit a second stop under the same session_id.
        self._session = None

        try:
            media = self._media.resolve()
        except Exception as exc:
            _log.warning("service.resolve_failed", error=str(exc))
            return
        if media is None:
            return
        if media.is_pkc and self._settings.skip_pkc:
            # Skipped before a session exists. A session that never emits would still hold
            # the index build off the idle tick for the whole playback.
            self.pkc_skipped += 1
            _log.info("service.playback_skipped", reason="plexkodiconnect", count=self.pkc_skipped)
            return

        viewers = self._viewers()
        who = identity_mod.resolve(
            self._index, viewers, media, self._profile_label(), self._recall(media, viewers)
        )
        session = PlaybackSession(session_id=self._ids(), media=media, identity=who)
        session.sample(*self._kodi.player_times())
        self._paused = False
        self._session = session
        self._emit("start", session)

    def on_paused(self) -> None:
        self._paused = True
        self._emit_for_live("pause")

    def on_resumed(self) -> None:
        self._paused = False
        self._emit_for_live("resume")

    def on_seek(self) -> None:
        """Record the jump. The tick emits, because a callback must not submit."""
        session = self._session
        if session is None:
            _log.info("service.event_dropped", kind="seek", reason="no_session")
            return
        session.note_seek()

    def on_stopped(self, completed: bool) -> None:
        """Park only. Emission happens from the tick, so the callback stays trivial."""
        session = self._session
        self._session = None
        if session is None:
            _log.info("service.event_dropped", kind="stop", reason="no_session")
            return
        self._park(session, reason="callback", completed=completed)

    def on_abort(self) -> None:
        self._shutting_down = True
        if self._session is not None:
            self._park(self._session, reason="abort")
            self._session = None
        self._flush_pending(allow_prompt=False)

    # -- tick --------------------------------------------------------------

    def on_tick(self, shutting_down: bool = False) -> None:
        # Sampled from the monitor, not from on_abort: no tick runs after abort, so a flag
        # set there could never reach the gate.
        self._shutting_down = shutting_down
        self._flush_pending(allow_prompt=True)
        # After the flush, never before: the queue is FIFO and the worker blocks, so a ping
        # submitted first sits in front of the parked stop and can cost it at shutdown.
        self._maybe_ping()
        session = self._session
        if session is not None:
            self._tick_playing(session)
            if not self._paused:
                return
            # A paused session decodes nothing, so the rebuild may proceed. Without this an
            # overnight pause blocks every refresh until playback resumes and ends.
        elif self._kodi.is_playing():
            # "No session I own" is a resolution fact, not a playback fact. Live TV, PVR,
            # music and unsupported streams all leave _session None while the decoder runs,
            # and the index build is the most expensive thing the addon does.
            return
        self._tick_idle()

    def _tick_playing(self, session: PlaybackSession) -> None:
        if not self._kodi.is_playing():
            # Kodi discards queued playback messages when new playback starts, so a stop
            # callback is not guaranteed. Reconcile rather than wait for one.
            _log.info("service.stop_reconciled", session_id=session.session_id)
            self._session = None
            self._park(session, reason="liveness")
            return
        session.sample(*self._kodi.player_times())
        if session.should_emit_progress(
            self._monotonic(), self._settings.progress_interval_seconds, self._paused
        ):
            self._emit("progress", session)

    def _tick_idle(self) -> None:
        """Heavy work happens here and only here, one playlist per tick.

        A .xsp expansion costs a whole-library query, so it must never land on the start of
        playback, and it must not run long enough in one tick to delay a callback.
        """
        if self._builder is None:
            if not self._needs_index():
                return
            self._builder = IndexBuilder(self._kodi, self._viewers(), self._monotonic)
        if not self._builder.step():
            return
        built = self._builder.result()
        self._builder = None
        if built is not None:
            self._index = built
            if built.degraded:
                # Published, but not current. A degraded viewer has no membership at all
                # until a build succeeds, so the index stays dirty and the failure backoff
                # governs the retry. Treating this as success would make them wait a full
                # TTL, which is a worse recovery than the all-or-nothing build had.
                self._failures += 1
                self._failed_at = self._monotonic()
                _log.warning("service.index_degraded", viewers=len(built.degraded), consecutive=self._failures)
                return
            self._index_dirty = False
            self._failures = 0
            self._failed_at = None
            return
        # Only reachable if a builder reports finished without a result, which it does not
        # do today. Kept so a future builder change cannot silently drop the retry.
        self._failures += 1
        self._failed_at = self._monotonic()
        _log.warning("service.index_build_failed", consecutive=self._failures)

    def _needs_index(self) -> bool:
        if self._failed_at is not None:
            # Backoff, so a permanently unexpandable playlist cannot pin the addon into a
            # continuous whole-library rebuild loop.
            backoff = min(FAILURE_BACKOFF_SECONDS * (2 ** min(self._failures - 1, 5)), MAX_FAILURE_BACKOFF_SECONDS)
            if self._monotonic() - self._failed_at < backoff:
                return False
        if self._index_dirty or self._index is None:
            return True
        return (self._monotonic() - self._index.built_at) >= self._settings.index_ttl_seconds

    # -- stop handling -----------------------------------------------------

    def _park(self, session: PlaybackSession, reason: str, completed: bool | None = None) -> None:
        """Park a finished session for the tick to emit.

        A reconciled stop has no callback to tell it whether the item finished, so completion
        is derived from the last sample against Kodi's own end threshold. That is what
        ignore_percent_at_end is for.
        """
        self._paused = False
        if completed is None:
            percent = session.percent()
            completed = percent is not None and percent >= (100.0 - self._thresholds.ignore_percent_at_end)
        if completed:
            session.mark_complete()
        self._pending_stop = session
        _log.debug("service.stop_parked", session_id=session.session_id, reason=reason, completed=completed)

    def _flush_pending(self, allow_prompt: bool) -> None:
        session = self._pending_stop
        if session is None:
            return
        try:
            if allow_prompt and not session.identity.viewers:
                self._attribute_by_prompt(session)
        finally:
            # The slot is cleared only once the emission has returned. Clearing it first
            # would mean an exception from _emit loses the stop with no state to recover it.
            try:
                queued = self._emit("stop", session)
                if not queued:
                    _log.warning("service.stop_not_queued", session_id=session.session_id)
            except Exception as exc:
                _log.error("service.stop_emit_failed", session_id=session.session_id, error=str(exc))
            finally:
                self._pending_stop = None

    def _attribute_by_prompt(self, session: PlaybackSession) -> None:
        try:
            viewers = self._viewers()
            decision = prompt_mod.gate(
                identity=session.identity,
                viewers=viewers,
                media=session.media,
                position_ms=session.position_ms,
                thresholds=self._thresholds,
                memory=self._memory,
                movie_prompts_enabled=self._settings.movie_prompts,
                dialog_id=self._kodi.topmost_dialog_id(),
                shutting_down=self._shutting_down,
            )
            if decision.remembered:
                session.identity = identity_mod.Identity(decision.remembered, "prompt")
                return
            if not decision.ask:
                return
            names = prompt_mod.ask(self._kodi, viewers, session.media, PROMPT_AUTOCLOSE_SECONDS)
            if not names:
                return
            session.identity = identity_mod.Identity(names, "prompt")
            prompt_mod.remember(self._memory, session.media, names)
        except Exception as exc:
            _log.warning("service.prompt_failed", error=str(exc))

    # -- helpers -----------------------------------------------------------

    def _viewers(self) -> list[Viewer]:
        return self._store.viewers()

    def _maybe_ping(self) -> None:
        """Tell CrossWatch this Kodi is reporting, so it stops polling.

        Sent whether or not something is playing: the server falls back to polling after 15
        minutes of silence, and going quiet during a long film would have it start polling
        mid-playback.
        """
        if self._shutting_down:
            return
        now = self._monotonic()
        if self._last_ping_at is not None and (now - self._last_ping_at) < PING_INTERVAL_SECONDS:
            return
        names = tuple(v.name for v in self._viewers())
        queued = self._queue.submit(
            PingEvent(
                event_id=self._ids(),
                sent_at=self._clock(),
                viewers=names,
                pkc_skipped=self.pkc_skipped,
            )
        )
        if not queued:
            # Do not advance the interval. A ping refused by a full queue would otherwise be
            # suppressed for another five minutes, during exactly the conditions it reports.
            _log.warning("service.ping_not_queued")
            return
        self._last_ping_at = now
        _log.info("service.ping", viewers_count=len(names), pkc_skipped=self.pkc_skipped)

    @staticmethod
    def _has_usable_id(media: MediaItem) -> bool:
        """The receiver routes on ids. Without one there is nothing for it to match."""
        return bool(media.show_ids or media.episode_ids or media.plex_rating_key)

    def _recall(self, media: MediaItem, viewers: list[Viewer]) -> tuple[str, ...]:
        """Remembered names for this show, reconciled against the configured viewers.

        Consulted at start as well as at stop, so a show answered once resolves its next
        episode before playback begins.
        """
        key = prompt_mod.show_key(media)
        if not key:
            return ()
        known = {v.name for v in viewers}
        return tuple(name for name in (self._memory.recall(key) or ()) if name in known)

    def _profile_label(self) -> str:
        try:
            return str(self._kodi.jsonrpc("Profiles.GetCurrentProfile").get("label") or "").strip()
        except Exception as exc:
            # Profiles.GetCurrentProfile returns label at the top level, not nested.
            _log.warning("service.profile_lookup_failed", error=str(exc))
            return ""

    def _emit_for_live(self, kind: EventKind) -> None:
        session = self._session
        if session is None:
            _log.info("service.event_dropped", kind=kind, reason="no_session")
            return
        session.sample(*self._kodi.player_times())
        self._emit(kind, session)

    def _emit(self, kind: EventKind, session: PlaybackSession) -> bool:
        if not self._has_usable_id(session.media):
            _log.info(
                "service.event_dropped",
                kind=kind,
                reason="no_usable_id",
                media_type=session.media.media_type,
                library_id=session.media.library_id,
            )
            return False
        event = PlaybackEvent(
            kind=kind,
            event_id=self._ids(),
            session_id=session.session_id,
            sent_at=self._clock(),
            media=session.media,
            viewers=session.identity.viewers,
            viewers_source=session.identity.source,
            position_ms=session.position_ms,
            duration_ms=session.duration_ms,
            percent=session.percent(),
            completed=session.completed,
        )
        return self._queue.submit(event)
