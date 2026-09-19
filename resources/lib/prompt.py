# SPDX-License-Identifier: GPL-2.0-only
"""Ask who watched, but only when it is worth asking, and never before the stop is out.

The gate is separate from the dialog so every suppression is unit testable and logged with
a reason code. A silent branch here is indistinguishable from a bug.

A dismissed dialog returns no viewers, nothing is remembered, and the same show is asked
about next time. Never lose a watch, never guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from resources.lib.advanced_settings import Thresholds
from resources.lib.identity import Identity
from resources.lib.kodi import WINDOW_INVALID, KodiApi
from resources.lib.log import get_logger
from resources.lib.models import MediaItem, Viewer
from resources.lib.storage import PromptMemory

_log = get_logger("prompt")

# Ordered by preference: the first present wins, so the key is stable across id ordering.
_ID_PREFERENCE = ("tvdb", "tmdb", "imdb")


def show_key(media: MediaItem) -> str | None:
    """The key a remembered answer hangs on.

    Prefers the show's unique id: tvshowid is a database row id that a library clean
    reassigns, so a key built on it can start attributing a different show to whoever
    answered for the old one. A movie returns None; there is no second showing to benefit.
    """
    if media.media_type != "episode":
        return None
    for namespace in _ID_PREFERENCE:
        value = media.show_ids.get(namespace)
        if value:
            return f"show:{namespace}:{value}"
    if media.show_library_id is not None:
        return f"tvshow:{media.show_library_id}"
    return None


@dataclass(frozen=True)
class GateDecision:
    ask: bool
    reason: str = ""
    remembered: tuple[str, ...] = ()


def gate(
    identity: Identity,
    viewers: list[Viewer],
    media: MediaItem,
    position_ms: int | None,
    thresholds: Thresholds,
    memory: PromptMemory,
    movie_prompts_enabled: bool,
    dialog_id: int,
    shutting_down: bool,
) -> GateDecision:
    def refuse(reason: str, remembered: tuple[str, ...] = ()) -> GateDecision:
        _log.info(
            "prompt.suppressed", reason=reason, media_type=media.media_type, library_id=media.library_id
        )
        return GateDecision(ask=False, reason=reason, remembered=remembered)

    if identity.viewers:
        return refuse("resolved")
    if not viewers:
        return refuse("no_viewers_configured")
    if len(viewers) < 2:
        return refuse("single_viewer")
    if media.media_type == "movie" and not movie_prompts_enabled:
        return refuse("movie_prompt_disabled")
    if (position_ms or 0) < thresholds.ignore_seconds_at_start * 1000:
        return refuse("below_ignore_seconds_at_start")

    key = show_key(media)
    if key:
        known = {v.name for v in viewers}
        recalled = memory.recall(key) or ()
        # Reconcile: a viewer removed from the configuration must stop being reported.
        surviving = tuple(name for name in recalled if name in known)
        if surviving:
            return refuse("remembered", surviving)
        if recalled:
            _log.info("prompt.memory_stale", key=key, dropped=len(recalled))

    if shutting_down:
        # Must be checked before the dialog: during shutdown Kodi refuses to draw one and
        # returns None without saying so, which would otherwise be recorded as a dismissal.
        return refuse("shutting_down")
    if dialog_id != WINDOW_INVALID:
        return refuse("dialog_busy")

    _log.info("prompt.asking", media_type=media.media_type, library_id=media.library_id)
    return GateDecision(ask=True)


def ask(kodi: KodiApi, viewers: list[Viewer], media: MediaItem, autoclose: int) -> tuple[str, ...]:
    options = [v.name for v in viewers]
    heading = f"Who watched {media.title or 'this'}?"
    chosen = kodi.multiselect(heading, options, autoclose=autoclose)
    if not chosen:
        _log.info("prompt.dismissed", media_type=media.media_type, library_id=media.library_id)
        return ()
    names = tuple(options[i] for i in chosen if 0 <= i < len(options))
    _log.info("prompt.answered", media_type=media.media_type, viewers_count=len(names))
    _log.debug("prompt.answer", viewers=",".join(names))
    return names


def remember(memory: PromptMemory, media: MediaItem, names: tuple[str, ...]) -> None:
    key = show_key(media)
    if key and names:
        memory.remember(key, names)
