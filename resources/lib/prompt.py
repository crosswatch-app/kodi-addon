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
from resources.lib.constants import PROMPT_EVERYONE, PROMPT_HEADING, PROMPT_HEADING_UNTITLED
from resources.lib.identity import Identity
from resources.lib.kodi import WINDOW_INVALID, KodiApi
from resources.lib.log import get_logger
from resources.lib.models import MediaItem, Viewer
from resources.lib.storage import PromptMemory

_log = get_logger("prompt")

# Ordered by preference: the first present wins, so the key is stable across id ordering.
_ID_PREFERENCE = ("tvdb", "tmdb", "imdb")

_EVERYONE_ROW = 0


def show_key(media: MediaItem) -> str | None:
    """The key a remembered answer hangs on.

    Prefers the show's unique id: tvshowid is a database row id that a library clean
    reassigns, so a key built on it can start attributing a different show to whoever
    answered for the old one. A movie returns None; there is no second showing to benefit.
    """
    if media.media_type != "episode":
        return None
    return key_for_show(media.show_ids, media.show_library_id)


def key_for_show(show_ids: dict[str, str], show_library_id: int | None) -> str | None:
    """The one rule for a show's key, shared by the prompt and the settings screen."""
    for namespace in _ID_PREFERENCE:
        value = show_ids.get(namespace)
        if value:
            return f"show:{namespace}:{value}"
    if show_library_id is not None:
        return f"{LIBRARY_KEY_PREFIX}{show_library_id}"
    return None


# A key on Kodi's own database id. Used only for a show the scraper could not identify, and
# never trusted on its own: a library clean can hand the id to a different show.
LIBRARY_KEY_PREFIX = "tvshow:"


def recall(memory: PromptMemory, media: MediaItem) -> tuple[str, ...]:
    """The remembered answer for this show, or nothing.

    An answer keyed on Kodi's database id counts only while the show at that id still has
    the title and year it had when the answer was given.
    """
    key = show_key(media)
    answer = memory.recall(key) if key else None
    if answer is None:
        return ()
    if key and key.startswith(LIBRARY_KEY_PREFIX):
        if answer.title is None or (answer.title, answer.year) != (media.title, media.show_year):
            _log.info("prompt.memory_unverified", media_type=media.media_type, library_id=media.show_library_id)
            return ()
    return answer.viewers


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

    recalled = recall(memory, media)
    # Reconcile: a viewer removed from the configuration must stop being reported.
    known = {v.name for v in viewers}
    surviving = tuple(name for name in recalled if name in known)
    if surviving:
        return refuse("remembered", surviving)
    if recalled:
        _log.info("prompt.memory_stale", dropped=len(recalled))

    if shutting_down:
        # Must be checked before the dialog: during shutdown Kodi refuses to draw one and
        # returns None without saying so, which would otherwise be recorded as a dismissal.
        return refuse("shutting_down")
    if dialog_id != WINDOW_INVALID:
        return refuse("dialog_busy")

    _log.info("prompt.asking", media_type=media.media_type, library_id=media.library_id)
    return GateDecision(ask=True)


def heading_for(kodi: KodiApi, title: str | None) -> str:
    if title:
        # replace rather than %: a translation that drops the placeholder must not raise.
        return kodi.localised(PROMPT_HEADING).replace("%s", title)
    return kodi.localised(PROMPT_HEADING_UNTITLED)


def choose_viewers(
    kodi: KodiApi, heading: str, viewers: list[Viewer], preselect: tuple[str, ...] = (), autoclose: int = 0
) -> tuple[str, ...] | None:
    """The Everyone row, then each viewer. None when cancelled, () when nobody was ticked.

    The two differ for the settings screen: cancel leaves an answer alone, while confirming
    with nobody ticked is a deliberate request to forget it.
    """
    names = [v.name for v in viewers]
    options = [kodi.localised(PROMPT_EVERYONE), *names]
    ticked = [i + 1 for i, name in enumerate(names) if name in preselect]
    chosen = kodi.multiselect(heading, options, preselect=ticked or None, autoclose=autoclose)
    if chosen is None:
        return None
    if _EVERYONE_ROW in chosen:
        # Expanded to names now rather than remembered as "everyone", so a viewer added
        # later is not credited with shows the household watched before they existed.
        return tuple(names)
    return tuple(names[i - 1] for i in chosen if 1 <= i <= len(names))


def ask(kodi: KodiApi, viewers: list[Viewer], media: MediaItem, autoclose: int) -> tuple[str, ...]:
    answer = choose_viewers(kodi, heading_for(kodi, media.title), viewers, autoclose=autoclose)
    if not answer:
        _log.info("prompt.dismissed", media_type=media.media_type, library_id=media.library_id)
        return ()
    _log.info(
        "prompt.answered", media_type=media.media_type, viewers_count=len(answer), everyone=len(answer) == len(viewers)
    )
    _log.debug("prompt.answer", viewers=",".join(answer))
    return answer


def remember(memory: PromptMemory, media: MediaItem, names: tuple[str, ...]) -> None:
    key = show_key(media)
    if key and names:
        memory.remember(key, names, title=media.title, year=media.show_year)
