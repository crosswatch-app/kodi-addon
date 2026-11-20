# SPDX-License-Identifier: GPL-2.0-only
"""The library sync setup screen ("Set up library sync"), run as a settings script.

Scripts never call the engine: this one reads the library and the playlists itself to show
who owns what, and on Start writes what the engine starts from, then bumps the
sync_request setting so the service hears of it. In this order, so a Kodi exit part way never
leaves a started file without a record or seed list behind it:
1. sync.db, created when missing;
2. on the first Start ever, the viewers to seed with Kodi's history (a later Start, or a
   viewer added later, is never seeded);
3. the sync_enabled setting turned on;
4. the started file, for the current pairing, with a new start_id, not paused, and the
   existing-ratings choice;
5. sync_request bumped.
Start never changes a viewer's sync id and sends nothing to CrossWatch itself.

When a playlist can't be read there is no Start: ownership decided without it would be wrong.

Log events: sync.setup.started, sync.setup.unreadable (info); sync.setup.library_unreadable,
sync.setup.not_saved (warning).
"""

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from resources.lib import log as logmod
from resources.lib import paths
from resources.lib.config import KEY_SYNC_ENABLED, KEY_SYNC_REQUEST, Settings, read_settings
from resources.lib.kodi import KodiApi, KodiRuntime
from resources.lib.log import get_logger
from resources.lib.models import Viewer
from resources.lib.playlist_index import IndexBuilder, PlaylistIndex
from resources.lib.storage import JsonViewerStore, PromptMemory
from resources.lib.sync.clock import kodi_to_utc
from resources.lib.sync.files import StartedFile, new_start_id, read_started, write_started
from resources.lib.sync.ids import ensure_sync_ids
from resources.lib.sync.items import Library, LibraryRow, read_library
from resources.lib.sync.library_id import library_id, profile_has_own_database
from resources.lib.sync.ownership import IndexSnapshot, owners_of
from resources.lib.sync.record import RecordStore
from resources.lib.sync.reply import connection_id
from resources.lib.ui.sync_setup import SetupRequest

_log = get_logger("sync")

TRAKT_ADDON = "script.trakt"

HEADING, HEADING_AGAIN = 30169, 30170
INTRO, INTRO_AGAIN = 30171, 30172
HISTORY_NOTE = 30173
SHARED, NO_OWNER, LEFT_ALONE = 30174, 30175, 30176
SHOWS, FILMS = 30177, 30178
RATINGS_TICK = 30179
DEVICE_ID_NOTE = 30180
TRAKT_WARNING = 30181
HTTP_WARNING = 30182
UNREADABLE, UNREADABLE_WHY = 30183, 30184
NOT_PAIRED = 30186
NO_LIBRARY = 30187
STARTED = 30188
NO_VIEWERS = 30189
NOT_SAVED = 30190


@dataclass
class Counts:
    """Shows and films per owner (sync id), shared between owners, and owned by nobody."""

    shows: dict[str, set[int]] = field(default_factory=dict)
    films: dict[str, int] = field(default_factory=dict)
    shared_shows: set[int] = field(default_factory=set)
    shared_films: int = 0
    unowned_shows: set[int] = field(default_factory=set)
    unowned_films: int = 0


def count_owners(library: Library, owners: Callable[[LibraryRow], Sequence[str]]) -> Counts:
    """A film counts once however many copies Kodi holds (the same ids), a show once in
    each group its episodes fall into."""
    counts = Counts()
    seen_films: set[tuple[str, ...]] = set()
    for row in library.movies:
        # The same keys CrossWatch matches on, so copies count as the one film they are.
        key = row.tokens
        if key in seen_films:
            continue
        seen_films.add(key)
        who = list(dict.fromkeys(owners(row)))
        if not who:
            counts.unowned_films += 1
        elif len(who) > 1:
            counts.shared_films += 1
        else:
            counts.films[who[0]] = counts.films.get(who[0], 0) + 1
    for row in library.episodes:
        show = row.show_kodi_id
        if show is None:
            continue
        who = list(dict.fromkeys(owners(row)))
        if not who:
            counts.unowned_shows.add(show)
        elif len(who) > 1:
            counts.shared_shows.add(show)
        else:
            counts.shows.setdefault(who[0], set()).add(show)
    return counts


def _amounts(localised: Callable[[int], str], shows: int, films: int) -> str:
    parts = []
    if shows:
        parts.append(localised(SHOWS).format(count=shows))
    if films:
        parts.append(localised(FILMS).format(count=films))
    return " · ".join(parts) or "0"


def setup_request(
    localised: Callable[[int], str],
    viewers: Sequence[Viewer],
    counts: Counts,
    *,
    again: bool,
    trakt: bool,
    insecure: bool,
    unreadable: Sequence[str],
) -> SetupRequest:
    lines = [
        f"{v.name}: {_amounts(localised, len(counts.shows.get(v.sync_id, ())), counts.films.get(v.sync_id, 0))}"
        for v in viewers
        if v.sync_id
    ]
    lines.append(f"{localised(SHARED)}: {_amounts(localised, len(counts.shared_shows), counts.shared_films)}")
    unowned = _amounts(localised, len(counts.unowned_shows), counts.unowned_films)
    lines.append(f"{localised(NO_OWNER)}: {unowned} {localised(LEFT_ALONE)}")
    heading = localised(HEADING_AGAIN if again else HEADING)
    if unreadable:
        why = localised(UNREADABLE).format(names=", ".join(unreadable))
        return SetupRequest(
            heading=heading,
            intro=f"{why}\n{localised(UNREADABLE_WHY)}",
            counts=(),
            note="",
            warnings=(),
            ratings_label="",
            can_start=False,
        )
    warnings = [localised(DEVICE_ID_NOTE)]
    if trakt:
        warnings.append(localised(TRAKT_WARNING))
    if insecure:
        warnings.append(localised(HTTP_WARNING))
    return SetupRequest(
        heading=heading,
        intro=localised(INTRO_AGAIN if again else INTRO),
        counts=tuple(lines),
        note="" if again else localised(HISTORY_NOTE),
        warnings=tuple(warnings),
        ratings_label=localised(RATINGS_TICK),
    )


def start(
    kodi: KodiApi,
    settings: Settings,
    viewers: Sequence[Viewer],
    *,
    db_path: str,
    started_path: str,
    existing_ratings: bool,
    now_utc: str,
) -> bool:
    """Start, in the order the module docstring gives. False when the record or the started
    file couldn't be written: then sync_request is not bumped, so the service starts nothing."""
    store = RecordStore(db_path, busy_timeout=10.0)
    store.open(create=True)
    try:
        # Kept by the record once set, so only the first Start ever writes it.
        store.set_seed_viewers([v.sync_id for v in viewers if v.sync_id], now_utc)
    finally:
        store.close()
    kodi.set_setting(KEY_SYNC_ENABLED, "true")
    written = write_started(
        started_path,
        StartedFile(
            connection=connection_id(settings.webhook_base_url),
            start_id=new_start_id(),
            existing_ratings=existing_ratings,
        ),
    )
    if not written:
        return False
    kodi.set_setting(KEY_SYNC_REQUEST, str(settings.sync_request + 1))
    _log.info("sync.setup.started", viewers=len(viewers), existing_ratings=existing_ratings)
    return True


def started_before(db_path: str, started_path: str, settings: Settings) -> bool:
    """Whether Start ran before for this record or this pairing: then nothing is seeded."""
    started = read_started(started_path)
    if started is not None and started.connection == connection_id(settings.webhook_base_url):
        return True
    if not os.path.exists(db_path):
        return False
    store = RecordStore(db_path)
    try:
        store.open()
    except Exception:
        return False
    try:
        return bool(store.seed_viewers() or store.seed_started_at())
    finally:
        store.close()


def _trakt_installed(kodi: KodiApi) -> bool:
    try:
        details = kodi.jsonrpc("Addons.GetAddonDetails", {"addonid": TRAKT_ADDON, "properties": ["enabled"]})
    except Exception:
        return False
    addon = details.get("addon")
    return isinstance(addon, dict) and addon.get("enabled") is not False


def run(kodi: KodiApi, now_utc: Callable[[], str], clock: Callable[[], float]) -> None:
    """The settings action: count, ask, and on Start write what the engine starts from."""
    settings = read_settings(kodi)
    if not settings.webhook_base_url or not settings.webhook_token:
        kodi.ok(kodi.localised(HEADING), kodi.localised(NOT_PAIRED))
        return
    store = JsonViewerStore(paths.viewers_path(kodi))
    viewers = ensure_sync_ids(store)
    if not viewers:
        # Nothing would be owned, and the first Start's seed list would be empty for good.
        kodi.ok(kodi.localised(HEADING), kodi.localised(NO_VIEWERS))
        return
    kodi.busy(True)
    try:
        library = _read_library(kodi)
        builder = IndexBuilder(kodi, viewers, clock)
        while not builder.step():
            pass
        index = builder.result()
    finally:
        kodi.busy(False)
    if library is None:
        kodi.ok(kodi.localised(HEADING), kodi.localised(NO_LIBRARY))
        return
    unreadable = _unreadable(index)
    if unreadable:
        _log.info("sync.setup.unreadable", count=len(unreadable))
    snapshot = IndexSnapshot(index=index, built_at=clock(), invalidated_at=0.0, viewers=tuple(viewers))
    owners = owners_of(
        snapshot,
        library.tvshows,
        PromptMemory(paths.prompts_path(kodi)).entries(),
        _profile(kodi),
        profile_has_own_database(kodi),
    )
    db_path, started_path = paths.sync_db_path(kodi), paths.sync_started_path(kodi)
    request = setup_request(
        kodi.localised,
        viewers,
        count_owners(library, owners),
        again=started_before(db_path, started_path, settings),
        trakt=_trakt_installed(kodi),
        insecure=settings.webhook_base_url.lower().startswith("http://"),
        unreadable=unreadable,
    )
    result = kodi.sync_setup_window(request)
    if result is None or not result.start:
        return
    try:
        started = start(kodi, settings, viewers, db_path=db_path, started_path=started_path,
                        existing_ratings=result.existing_ratings, now_utc=now_utc())  # fmt: skip
    except Exception as exc:
        # A busy or unusable sync.db; the order in start() leaves nothing half-started.
        _log.warning("sync.setup.not_saved", error_type=type(exc).__name__)
        started = False
    if not started:
        kodi.ok(kodi.localised(HEADING), kodi.localised(NOT_SAVED))
        return
    kodi.notify(kodi.localised(HEADING), kodi.localised(STARTED))


def _read_library(kodi: KodiApi) -> Library | None:
    """The whole library, or None when Kodi couldn't give it in one consistent read (a scan
    changed it underneath, or JSON-RPC failed): counts from part of it would mislead."""
    try:
        library = read_library(kodi, library_id(kodi), may_continue=lambda: True, previous_shows={}, to_utc=kodi_to_utc)
    except Exception as exc:
        _log.warning("sync.setup.library_unreadable", error_type=type(exc).__name__)
        return None
    if library is None or not library.complete:
        return None
    return library


def _unreadable(index: PlaylistIndex | None) -> tuple[str, ...]:
    """The playlists that couldn't be read. A finished build always has an index; without
    one, ownership is unknown and no Start is offered."""
    if index is None:
        return ("?",)
    return tuple(sorted(index.unreadable))


def _profile(kodi: KodiApi) -> str:
    try:
        return str(kodi.jsonrpc("Profiles.GetCurrentProfile").get("label") or "").strip()
    except Exception:
        return ""


def main() -> None:
    """The settings action's entry point, in its own interpreter as every settings script."""
    kodi = KodiRuntime()
    settings = read_settings(kodi)
    logmod.configure(log_dir=paths.log_dir(kodi), debug=settings.debug_logging, sink=kodi.log)
    logmod.set_secret(settings.webhook_token)
    run(kodi, lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), time.monotonic)
