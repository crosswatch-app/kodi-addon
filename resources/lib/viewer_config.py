# SPDX-License-Identifier: GPL-2.0-only
"""Viewer configuration.

settings.xml is static and cannot express a variable number of viewers, so the list is
edited through a dialog and stored as JSON. The list arithmetic is pure so it is testable
without a GUI; only run_dialog touches Kodi.

A viewer's name must match the name CrossWatch routes on, which is why it is free text
rather than a picker: these names come from playlists and prompts, not from Kodi profiles.

This runs as a separate script with its own module state, so main() configures logging.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from resources.lib import log as logmod
from resources.lib import paths
from resources.lib.config import read_settings
from resources.lib.constants import (
    LABEL_BACK,
    VIEWERS_ADD,
    VIEWERS_EDIT_PLAYLISTS,
    VIEWERS_EDIT_PROFILES,
    VIEWERS_HEADING,
    VIEWERS_MISSING,
    VIEWERS_MISSING_COUNT,
    VIEWERS_NAME_PROMPT,
    VIEWERS_ONE_PLAYLIST,
    VIEWERS_PLAYLISTS,
    VIEWERS_PLAYLISTS_FOR,
    VIEWERS_PROFILES_FOR,
    VIEWERS_REMOVE,
    VIEWERS_REMOVE_CONFIRM,
    VIEWERS_UNUSABLE,
    VIEWERS_UNUSABLE_COUNT,
)
from resources.lib.kodi import KodiApi, KodiRuntime
from resources.lib.log import get_logger
from resources.lib.models import Viewer
from resources.lib.playlist_index import INDEXABLE_TYPES, PLAYLIST_DIR, declared_type
from resources.lib.storage import JsonViewerStore, ViewerStore

_log = get_logger("config")


def _text(kodi: KodiApi, string_id: int, value: object = None) -> str:
    """A translated label. replace rather than %, so a translation that drops the
    placeholder cannot raise."""
    text = kodi.localised(string_id)
    return text if value is None else text.replace("%s", str(value))


class PlaylistListing(NamedTuple):
    """Kodi's video smart playlists, split by whether the index can match them.

    Only show and film playlists are matched. A playlist whose file cannot be read counts
    as matchable: its type is unknown rather than wrong, and the index reports it.
    """

    matchable: list[str]
    unusable: list[str]


def available_playlists(kodi: KodiApi) -> PlaylistListing:
    try:
        result = kodi.jsonrpc("Files.GetDirectory", {"directory": PLAYLIST_DIR, "media": "video"})
    except Exception as exc:
        _log.warning("config.playlist_listing_failed", error=str(exc))
        return PlaylistListing([], [])
    files = result.get("files")
    if not isinstance(files, list):
        return PlaylistListing([], [])
    listing = PlaylistListing([], [])
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("file") or "")
        if not path.endswith(".xsp"):
            continue
        label = str(entry.get("label") or "").strip()
        if not label:
            continue
        text = kodi.read_text(path)
        if text is None or declared_type(text) in INDEXABLE_TYPES:
            listing.matchable.append(label)
        else:
            listing.unusable.append(label)
    return listing


def validate_name(name: str, existing: list[Viewer]) -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ValueError("a viewer needs a name")
    if any(v.name.casefold() == cleaned.casefold() for v in existing):
        raise ValueError(f"a viewer named {cleaned} already exists")
    return cleaned


def apply_edit(
    viewers: list[Viewer], name: str, playlists: tuple[str, ...], profiles: tuple[str, ...]
) -> list[Viewer]:
    updated = Viewer(name=name, playlists=tuple(playlists), profiles=tuple(profiles))
    out = list(viewers)
    for index, viewer in enumerate(out):
        if viewer.name.casefold() == name.casefold():
            out[index] = updated
            return out
    out.append(updated)
    return out


def remove_viewer(viewers: list[Viewer], name: str) -> list[Viewer]:
    return [v for v in viewers if v.name.casefold() != name.casefold()]


MISSING_MARK = "!"


def missing_playlists(viewer: Viewer, available: list[str], unusable: Sequence[str] = ()) -> tuple[str, ...]:
    """Playlists the viewer is configured with that Kodi no longer has.

    An empty listing means it failed rather than that nothing exists, and flagging every
    viewer during a Kodi hiccup would be a false alarm, so it reports none.
    """
    if not available and not unusable:
        return ()
    known = {*available, *unusable}
    return tuple(name for name in viewer.playlists if name not in known)


def unusable_playlists(viewer: Viewer, unusable: Sequence[str]) -> tuple[str, ...]:
    """Playlists the viewer is configured with that the index cannot match."""
    return tuple(name for name in viewer.playlists if name in unusable)


def viewer_labels(
    kodi: KodiApi, viewers: list[Viewer], available: list[str], unusable: Sequence[str] = ()
) -> list[str]:
    """One row per viewer, marked when a configured playlist has vanished or cannot be used.

    This is where someone comes to fix it. The service cannot name the playlist in Kodi's
    shared log, by design, so without this the only pointer is a toast that fires once.
    """
    labels: list[str] = []
    for viewer in viewers:
        mark = ""
        gone = missing_playlists(viewer, available, unusable)
        if gone:
            mark += f" {MISSING_MARK} {_text(kodi, VIEWERS_MISSING_COUNT, len(gone))}"
        wrong = unusable_playlists(viewer, unusable)
        if wrong:
            mark += f" {MISSING_MARK} {_text(kodi, VIEWERS_UNUSABLE_COUNT, len(wrong))}"
        count = len(viewer.playlists)
        playlists = _text(kodi, VIEWERS_ONE_PLAYLIST) if count == 1 else _text(kodi, VIEWERS_PLAYLISTS, count)
        labels.append(f"{viewer.name} ({playlists}){mark}")
    return labels


def _edit_playlists(
    kodi: KodiApi, viewer: Viewer, playlists: list[str], unusable: Sequence[str] = ()
) -> tuple[str, ...] | None:
    """None means cancelled, so the existing mapping is kept."""
    # The viewer's vanished and unusable playlists are listed too, ticked. Without them the
    # name is invisible here and confirming silently drops it, so a removal the user never
    # saw looks like their own edit. Shown, unticking it is a choice. An unusable playlist
    # nobody has is not offered: picking it would credit nobody.
    flagged = [
        *((name, _text(kodi, VIEWERS_UNUSABLE)) for name in unusable_playlists(viewer, unusable)),
        *((name, _text(kodi, VIEWERS_MISSING)) for name in missing_playlists(viewer, playlists, unusable)),
    ]
    options = [*playlists, *(f"{name} ({MISSING_MARK} {reason})" for name, reason in flagged)]
    preselect = [i for i, name in enumerate(playlists) if name in viewer.playlists]
    preselect += [len(playlists) + i for i in range(len(flagged))]
    chosen = kodi.multiselect(_text(kodi, VIEWERS_PLAYLISTS_FOR, viewer.name), options, preselect=preselect)
    if chosen is None:
        return None
    kept = [playlists[i] for i in chosen if 0 <= i < len(playlists)]
    # A ticked flagged playlist is kept under its real name, not the decorated label, so
    # the configuration still matches the file if the playlist comes back or is fixed.
    kept += [flagged[i - len(playlists)][0] for i in chosen if len(playlists) <= i < len(options)]
    return tuple(kept)


def _edit_profiles(kodi: KodiApi, viewer: Viewer) -> tuple[str, ...]:
    current = ", ".join(viewer.profiles)
    entered = kodi.text_input(_text(kodi, VIEWERS_PROFILES_FOR, viewer.name), current)
    return tuple(part.strip() for part in entered.split(",") if part.strip())


def _add_viewer(kodi: KodiApi, viewers: list[Viewer], playlists: list[str]) -> list[Viewer]:
    try:
        name = validate_name(kodi.text_input(_text(kodi, VIEWERS_NAME_PROMPT)), viewers)
    except ValueError as exc:
        _log.info("config.add_rejected", reason=str(exc))
        return viewers
    viewers = apply_edit(viewers, name, (), ())
    selected = _edit_playlists(kodi, Viewer(name=name), playlists)
    if selected is not None:
        viewers = apply_edit(viewers, name, selected, ())
    _log.info("config.viewer_added", playlists=len(selected or ()))
    return viewers


def _edit_viewer(kodi: KodiApi, viewers: list[Viewer], viewer: Viewer, listing: PlaylistListing) -> list[Viewer]:
    actions = [VIEWERS_EDIT_PLAYLISTS, VIEWERS_EDIT_PROFILES, VIEWERS_REMOVE, LABEL_BACK]
    choice = kodi.select(viewer.name, [_text(kodi, a) for a in actions])
    if choice == 0:
        selected = _edit_playlists(kodi, viewer, listing.matchable, listing.unusable)
        if selected is None:
            _log.info("config.playlists_unchanged", reason="cancelled")
            return viewers
        _log.info("config.viewer_updated", playlists=len(selected))
        return apply_edit(viewers, viewer.name, selected, viewer.profiles)
    if choice == 1:
        profiles = _edit_profiles(kodi, viewer)
        _log.info("config.viewer_updated", profiles=len(profiles))
        return apply_edit(viewers, viewer.name, viewer.playlists, profiles)
    if choice == 2 and kodi.confirm(_text(kodi, VIEWERS_REMOVE), _text(kodi, VIEWERS_REMOVE_CONFIRM, viewer.name)):
        _log.info("config.viewer_removed")
        return remove_viewer(viewers, viewer.name)
    return viewers


def run_dialog(kodi: KodiApi, store: ViewerStore) -> None:
    viewers = store.viewers()
    listing = available_playlists(kodi)
    if not listing.matchable:
        _log.info("config.no_playlists_found", unusable=len(listing.unusable))

    while True:
        labels = viewer_labels(kodi, viewers, listing.matchable, listing.unusable)
        # No Done row: Kodi's select dialog has its own Cancel, and leaving either way saves.
        choice = kodi.select(_text(kodi, VIEWERS_HEADING), [*labels, _text(kodi, VIEWERS_ADD)])
        if choice < 0 or choice > len(labels):
            break
        if choice == len(labels):
            viewers = _add_viewer(kodi, viewers, listing.matchable)
            continue
        viewers = _edit_viewer(kodi, viewers, viewers[choice], listing)

    store.save(viewers)
    _log.info("config.saved", viewers=len(viewers))


def main() -> None:
    kodi = KodiRuntime()
    settings = read_settings(kodi)
    # A separate interpreter with its own module state: without this every line below is
    # written to a no-op sink and a failure here is invisible everywhere.
    logmod.configure(log_dir=paths.log_dir(kodi), debug=settings.debug_logging, sink=kodi.log)
    run_dialog(kodi, JsonViewerStore(paths.viewers_path(kodi)))
