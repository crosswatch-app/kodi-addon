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

from collections.abc import Callable, Sequence
from typing import NamedTuple

from resources.lib import log as logmod
from resources.lib import paths
from resources.lib.config import read_settings
from resources.lib.constants import (
    LABEL_BACK,
    VIEWER_NONE,
    VIEWER_SUMMARY_PLAYLISTS,
    VIEWER_SUMMARY_PROFILES,
    VIEWERS_ADD,
    VIEWERS_ALSO,
    VIEWERS_EDIT_PLAYLISTS,
    VIEWERS_EDIT_PROFILES,
    VIEWERS_HEADING,
    VIEWERS_MISSING,
    VIEWERS_MISSING_COUNT,
    VIEWERS_NAME_PROMPT,
    VIEWERS_NO_PLAYLISTS,
    VIEWERS_NOTHING,
    VIEWERS_ONE_PLAYLIST,
    VIEWERS_ONE_PROFILE,
    VIEWERS_PLAYLISTS,
    VIEWERS_PLAYLISTS_FOR,
    VIEWERS_PROFILES,
    VIEWERS_PROFILES_FOR,
    VIEWERS_REMOVE,
    VIEWERS_REMOVE_CONFIRM,
    VIEWERS_UNUSABLE_COUNT,
)
from resources.lib.kodi import KodiApi, KodiRuntime
from resources.lib.log import get_logger
from resources.lib.models import Viewer
from resources.lib.playlist_index import INDEXABLE_TYPES, PLAYLIST_DIR, declared_type
from resources.lib.storage import JsonViewerStore, ViewerStore
from resources.lib.ui.list_window import ListRequest, ListRow, ListState

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


def validate_name(name: str, existing: list[Viewer], own: str | None = None) -> str:
    """The cleaned name, or ValueError("empty") / ValueError("taken").

    Reason codes, not sentences: the reason is logged, and a sentence would carry the name.
    own is the viewer being renamed: their current name is not taken, so a change of case is
    allowed.
    """
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ValueError("empty")
    mine = own.casefold() if own else None
    if any(v.name.casefold() == cleaned.casefold() and v.name.casefold() != mine for v in existing):
        raise ValueError("taken")
    return cleaned


def rename_viewer(viewers: list[Viewer], old: str, new: str) -> list[Viewer]:
    return [Viewer(name=new, playlists=v.playlists, profiles=v.profiles) if v.name == old else v for v in viewers]


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


def _also(kodi: KodiApi, viewer: Viewer, viewers: Sequence[Viewer], has: Callable[[Viewer], bool]) -> str:
    # A shared playlist or profile credits everyone who has it, which is easy to miss.
    others = [v.name for v in viewers if v.name.casefold() != viewer.name.casefold() and has(v)]
    return _text(kodi, VIEWERS_ALSO, ", ".join(others)) if others else ""


def _count(kodi: KodiApi, n: int, one: int, many: int) -> str:
    return _text(kodi, one) if n == 1 else _text(kodi, many, n)


def setup_text(kodi: KodiApi, viewer: Viewer) -> str:
    parts = []
    if viewer.playlists:
        parts.append(_count(kodi, len(viewer.playlists), VIEWERS_ONE_PLAYLIST, VIEWERS_PLAYLISTS))
    if viewer.profiles:
        parts.append(_count(kodi, len(viewer.profiles), VIEWERS_ONE_PROFILE, VIEWERS_PROFILES))
    return ", ".join(parts) or _text(kodi, VIEWERS_NOTHING)


def flag_text(kodi: KodiApi, viewer: Viewer, listing: PlaylistListing) -> str:
    """What needs fixing, shown where someone comes to fix it: the service cannot name a
    playlist in Kodi's shared log, by design."""
    parts = []
    gone = missing_playlists(viewer, listing.matchable, listing.unusable)
    if gone:
        parts.append(_text(kodi, VIEWERS_MISSING_COUNT, len(gone)))
    wrong = unusable_playlists(viewer, listing.unusable)
    if wrong:
        parts.append(_text(kodi, VIEWERS_UNUSABLE_COUNT, len(wrong)))
    return ", ".join(parts)


def viewer_rows(kodi: KodiApi, viewers: list[Viewer], listing: PlaylistListing) -> tuple[ListRow, ...]:
    return tuple(
        ListRow(key=v.name, title=v.name, detail=setup_text(kodi, v), tag=flag_text(kodi, v, listing)) for v in viewers
    )


def summary_text(kodi: KodiApi, viewer: Viewer) -> str:
    none = _text(kodi, VIEWER_NONE)
    playlists = _text(kodi, VIEWER_SUMMARY_PLAYLISTS, ", ".join(viewer.playlists) or none)
    profiles = _text(kodi, VIEWER_SUMMARY_PROFILES, ", ".join(viewer.profiles) or none)
    # [CR] is Kodi's line break in a label.
    return f"{playlists}[CR]{profiles}"


def available_profiles(kodi: KodiApi) -> list[str] | None:
    """Kodi's profile labels; None when they could not be read, which is not "none"."""
    try:
        result = kodi.jsonrpc("Profiles.GetProfiles")
    except Exception as exc:
        _log.warning("config.profile_listing_failed", error=type(exc).__name__)
        return None
    profiles = result.get("profiles")
    if not isinstance(profiles, list):
        return None
    labels = (str(p.get("label") or "").strip() for p in profiles if isinstance(p, dict))
    return [label for label in labels if label]


def profile_rows(
    kodi: KodiApi, viewer: Viewer, profiles: Sequence[str], viewers: Sequence[Viewer]
) -> tuple[tuple[ListRow, ...], tuple[str, ...]]:
    """Kodi's profiles, then the viewer's ones Kodi no longer has; and what starts ticked.

    Matched without case, as playback matches the active profile, and ticked under Kodi's
    own label so the stored name follows Kodi once picked.
    """
    kodi_labels = {p.casefold(): p for p in profiles}

    def also(label: str) -> str:
        return _also(kodi, viewer, viewers, lambda v: any(p.casefold() == label.casefold() for p in v.profiles))

    missing = [p for p in viewer.profiles if p.casefold() not in kodi_labels]
    rows = (
        *(ListRow(key=p, title=p, detail=also(p)) for p in profiles),
        *(ListRow(key=p, title=p, detail=also(p), tag=_text(kodi, VIEWERS_MISSING)) for p in missing),
    )
    ticked = tuple(kodi_labels.get(p.casefold(), p) for p in viewer.profiles)
    return rows, ticked


def playlist_rows(
    kodi: KodiApi, viewer: Viewer, playlists: Sequence[str], unusable: Sequence[str], viewers: Sequence[Viewer]
) -> tuple[ListRow, ...]:
    """Every matchable playlist, then the viewer's own vanished ones.

    A vanished playlist may come back (a share offline, a rename), so it is listed under its
    real name: without it Done would drop it silently, and a removal the household never saw
    would look like their own edit. An unusable one is not listed and Done drops it: it can
    never credit anyone, so there is nothing to choose, and the viewer list already marks it.
    """

    def also(name: str) -> str:
        return _also(kodi, viewer, viewers, lambda v: name in v.playlists)

    missing = _text(kodi, VIEWERS_MISSING)
    return (
        *(ListRow(key=name, title=name, detail=also(name)) for name in playlists),
        *(
            ListRow(key=name, title=name, detail=also(name), tag=missing)
            for name in missing_playlists(viewer, list(playlists), unusable)
        ),
    )


def _edit_playlists(
    kodi: KodiApi,
    viewer: Viewer,
    playlists: list[str],
    unusable: Sequence[str] = (),
    viewers: Sequence[Viewer] = (),
) -> tuple[str, ...] | None:
    """None means cancelled, so the existing mapping is kept."""
    heading = _text(kodi, VIEWERS_PLAYLISTS_FOR, viewer.name)
    rows = playlist_rows(kodi, viewer, playlists, unusable, viewers)
    if not rows:
        # Also what a failed listing looks like, and an empty window would then drop every
        # playlist the viewer has on Done.
        _log.info("config.no_playlists_to_pick", unusable=len(unusable))
        kodi.ok(heading, _text(kodi, VIEWERS_NO_PLAYLISTS))
        return None
    request = ListRequest(
        heading=heading,
        rows=rows,
        count_one=_text(kodi, VIEWERS_ONE_PLAYLIST),
        count_all=_text(kodi, VIEWERS_PLAYLISTS),
        pick=True,
        ticked=viewer.playlists,
    )
    result = kodi.list_window(request, ListState())
    return result.keys if result.action == "done" else None


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
    selected = _edit_playlists(kodi, Viewer(name=name), playlists, viewers=viewers)
    if selected is not None:
        viewers = apply_edit(viewers, name, selected, ())
    _log.info("config.viewer_added", playlists=len(selected or ()))
    return viewers


def _edit_viewer(kodi: KodiApi, viewers: list[Viewer], viewer: Viewer, listing: PlaylistListing) -> list[Viewer]:
    actions = [VIEWERS_EDIT_PLAYLISTS, VIEWERS_EDIT_PROFILES, VIEWERS_REMOVE, LABEL_BACK]
    choice = kodi.select(viewer.name, [_text(kodi, a) for a in actions])
    if choice == 0:
        selected = _edit_playlists(kodi, viewer, listing.matchable, listing.unusable, viewers)
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
