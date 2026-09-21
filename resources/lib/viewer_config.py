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

from resources.lib import log as logmod
from resources.lib import paths
from resources.lib.config import read_settings
from resources.lib.kodi import KodiApi, KodiRuntime
from resources.lib.log import get_logger
from resources.lib.models import Viewer
from resources.lib.playlist_index import PLAYLIST_DIR
from resources.lib.storage import JsonViewerStore, ViewerStore

_log = get_logger("config")

ADD_LABEL = "Add viewer"
DONE_LABEL = "Done"


def available_playlists(kodi: KodiApi) -> list[str]:
    try:
        result = kodi.jsonrpc("Files.GetDirectory", {"directory": PLAYLIST_DIR, "media": "video"})
    except Exception as exc:
        _log.warning("config.playlist_listing_failed", error=str(exc))
        return []
    files = result.get("files")
    if not isinstance(files, list):
        return []
    names: list[str] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        if not str(entry.get("file") or "").endswith(".xsp"):
            continue
        label = str(entry.get("label") or "").strip()
        if label:
            names.append(label)
    return names


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


def missing_playlists(viewer: Viewer, available: list[str]) -> tuple[str, ...]:
    """Playlists the viewer is configured with that Kodi no longer has.

    An empty `available` means the listing failed rather than that nothing exists, and
    flagging every viewer during a Kodi hiccup would be a false alarm, so it reports none.
    """
    if not available:
        return ()
    known = set(available)
    return tuple(name for name in viewer.playlists if name not in known)


def viewer_labels(viewers: list[Viewer], available: list[str]) -> list[str]:
    """One row per viewer, marked when a configured playlist has vanished.

    This is where someone comes to fix it. The service cannot name the playlist in Kodi's
    shared log, by design, so without this the only pointer is a toast that fires once.
    """
    labels: list[str] = []
    for viewer in viewers:
        gone = missing_playlists(viewer, available)
        mark = f" {MISSING_MARK} {len(gone)} missing" if gone else ""
        labels.append(f"{viewer.name} ({len(viewer.playlists)} playlists){mark}")
    return labels


def _edit_playlists(kodi: KodiApi, viewer: Viewer, playlists: list[str]) -> tuple[str, ...] | None:
    """None means cancelled, so the existing mapping is kept."""
    # Vanished playlists are listed too, ticked. Without them the name is invisible here
    # and confirming silently drops it, so a removal the user never saw looks like their
    # own edit. Shown, unticking it is a choice.
    gone = missing_playlists(viewer, playlists)
    options = [*playlists, *(f"{name} ({MISSING_MARK} missing)" for name in gone)]
    preselect = [i for i, name in enumerate(playlists) if name in viewer.playlists]
    preselect += [len(playlists) + i for i in range(len(gone))]
    chosen = kodi.multiselect(f"Playlists for {viewer.name}", options, preselect=preselect)
    if chosen is None:
        return None
    kept = [playlists[i] for i in chosen if 0 <= i < len(playlists)]
    # A ticked missing playlist is kept under its real name, not the decorated label, so
    # the configuration still matches the file if the playlist comes back.
    kept += [gone[i - len(playlists)] for i in chosen if len(playlists) <= i < len(options)]
    return tuple(kept)


def _edit_profiles(kodi: KodiApi, viewer: Viewer) -> tuple[str, ...]:
    current = ", ".join(viewer.profiles)
    entered = kodi.text_input(f"Kodi profiles for {viewer.name}, comma separated", current)
    return tuple(part.strip() for part in entered.split(",") if part.strip())


def _add_viewer(kodi: KodiApi, viewers: list[Viewer], playlists: list[str]) -> list[Viewer]:
    try:
        name = validate_name(kodi.text_input("Viewer name, as CrossWatch routes it"), viewers)
    except ValueError as exc:
        _log.info("config.add_rejected", reason=str(exc))
        return viewers
    viewers = apply_edit(viewers, name, (), ())
    selected = _edit_playlists(kodi, Viewer(name=name), playlists)
    if selected is not None:
        viewers = apply_edit(viewers, name, selected, ())
    _log.info("config.viewer_added", playlists=len(selected or ()))
    return viewers


def _edit_viewer(kodi: KodiApi, viewers: list[Viewer], viewer: Viewer, playlists: list[str]) -> list[Viewer]:
    choice = kodi.select(viewer.name, ["Edit playlists", "Edit Kodi profiles", "Remove viewer", "Back"])
    if choice == 0:
        selected = _edit_playlists(kodi, viewer, playlists)
        if selected is None:
            _log.info("config.playlists_unchanged", reason="cancelled")
            return viewers
        _log.info("config.viewer_updated", playlists=len(selected))
        return apply_edit(viewers, viewer.name, selected, viewer.profiles)
    if choice == 1:
        profiles = _edit_profiles(kodi, viewer)
        _log.info("config.viewer_updated", profiles=len(profiles))
        return apply_edit(viewers, viewer.name, viewer.playlists, profiles)
    if choice == 2 and kodi.confirm("Remove viewer", f"Remove {viewer.name}?"):
        _log.info("config.viewer_removed")
        return remove_viewer(viewers, viewer.name)
    return viewers


def run_dialog(kodi: KodiApi, store: ViewerStore) -> None:
    viewers = store.viewers()
    playlists = available_playlists(kodi)
    if not playlists:
        _log.info("config.no_playlists_found")

    while True:
        labels = viewer_labels(viewers, playlists)
        choice = kodi.select("Viewers", [*labels, ADD_LABEL, DONE_LABEL])
        if choice < 0 or choice == len(labels) + 1:
            break
        if choice == len(labels):
            viewers = _add_viewer(kodi, viewers, playlists)
            continue
        viewers = _edit_viewer(kodi, viewers, viewers[choice], playlists)

    store.save(viewers)
    _log.info("config.saved", viewers=len(viewers))


def main() -> None:
    kodi = KodiRuntime()
    settings = read_settings(kodi)
    # A separate interpreter with its own module state: without this every line below is
    # written to a no-op sink and a failure here is invisible everywhere.
    logmod.configure(log_dir=paths.log_dir(kodi), debug=settings.debug_logging, sink=kodi.log)
    run_dialog(kodi, JsonViewerStore(paths.viewers_path(kodi)))
