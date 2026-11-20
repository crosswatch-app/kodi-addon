import os

from resources.lib import paths
from resources.lib.config import KEY_BASE_URL, KEY_SYNC_ENABLED, KEY_SYNC_REQUEST, KEY_TOKEN, read_settings
from resources.lib.models import Viewer
from resources.lib.storage import JsonViewerStore
from resources.lib.sync import items, screens
from resources.lib.sync.files import StartedFile, read_started, write_started
from resources.lib.sync.items import Library, LibraryRow, RowKey
from resources.lib.sync.record import RecordStore
from resources.lib.sync.reply import connection_id
from resources.lib.ui.sync_setup import SetupResult
from tests.fakes import FakeKodi
from tests.sync.fake_library import FakeLibrary

URL = "https://nas:8787/webhook/kodiwatcher"
NOW = "2026-11-20T10:00:00Z"


def _words(string_id: int) -> str:
    return {screens.SHOWS: "{count} shows", screens.FILMS: "{count} films",
            screens.UNREADABLE: "Couldn't read the playlists: {names}"}.get(string_id, f"#{string_id}")  # fmt: skip


def _row(kind: items.ItemKind, kodi_id: int, show: int | None = None, ids: dict[str, str] | None = None) -> LibraryRow:
    raw = {
        "uniqueid": ids if ids is not None else {"tmdb": str(kodi_id)},
        "title": f"T{kodi_id}",
        "file": f"/{kind}/{kodi_id}.mkv",
    }
    if kind == "episodes":
        raw.update(tvshowid=show, season=1, episode=kodi_id, showtitle="S")
    key = RowKey("L1", kind, kodi_id, raw["file"])
    return LibraryRow(
        key, tuple(items.tokens(kind, raw, {"uniqueid": {"tvdb": str(show)}} if show else None)), raw, show
    )


def test_setup_counts():
    library = Library("L1", [_row("movies", 1), _row("movies", 2), _row("movies", 3), _row("movies", 4, ids={"tmdb": "1"})],
                      [_row("episodes", 10, show=7), _row("episodes", 11, show=7), _row("episodes", 12, show=8),
                       _row("episodes", 13, show=9)], {}, [], True)  # fmt: skip
    owners = {1: ("kv-a",), 2: ("kv-a", "kv-b"), 3: (), 10: ("kv-a",), 11: ("kv-a",), 12: ("kv-a", "kv-b"), 13: ()}
    counts = screens.count_owners(library, lambda row: owners.get(row.key.kodi_id, ("kv-a",)))
    assert counts.films == {"kv-a": 1} and counts.shared_films == 1 and counts.unowned_films == 1
    assert counts.shows == {"kv-a": {7}} and counts.shared_shows == {8} and counts.unowned_shows == {9}
    request = screens.setup_request(_words, [Viewer(name="Anna", sync_id="kv-a"), Viewer(name="Ben", sync_id="kv-b")],
                                    counts, again=False, trakt=False, insecure=False, unreadable=())  # fmt: skip
    assert request.counts == (
        "Anna: 1 shows · 1 films",
        "Ben: 0",
        f"#{screens.SHARED}: 1 shows · 1 films",
        f"#{screens.NO_OWNER}: 1 shows · 1 films #{screens.LEFT_ALONE}",
    )
    assert request.can_start and request.note == f"#{screens.HISTORY_NOTE}"
    assert request.warnings == (f"#{screens.DEVICE_ID_NOTE}",)


def test_films_without_ids_count_by_title():
    library = Library("L1", [_row("movies", 1, ids={}), _row("movies", 2, ids={})], [], {}, [], True)
    counts = screens.count_owners(library, lambda row: ("kv-a",))
    assert counts.films == {"kv-a": 2}


def test_setup_without_usable_index_offers_no_start():
    request = screens.setup_request(_words, [], screens.Counts(), again=False, trakt=True, insecure=True,
                                    unreadable=("Anna's shows",))  # fmt: skip
    assert not request.can_start and request.counts == () and "Anna's shows" in request.intro


def test_trakt_addon_and_http_warnings():
    request = screens.setup_request(_words, [], screens.Counts(), again=True, trakt=True, insecure=True, unreadable=())
    assert f"#{screens.TRAKT_WARNING}" in request.warnings and f"#{screens.HTTP_WARNING}" in request.warnings
    # Again: nothing is sent from Kodi's history, so no history note.
    assert request.note == "" and request.heading == f"#{screens.HEADING_AGAIN}"


def _kodi(tmp_path) -> FakeKodi:
    lib = FakeLibrary()
    lib.add_movie(1, title="Film", year=2001, uniqueid={"tmdb": "1"}, file="/m/1.mkv")
    kodi = FakeKodi(settings={KEY_BASE_URL: URL, KEY_TOKEN: "tok"}, rpc_handlers=lib.handlers(), root=str(tmp_path))
    return kodi


def test_start_writes_everything_in_order_and_bumps_setting(tmp_path):
    kodi = _kodi(tmp_path)
    viewers = [Viewer(name="Anna", sync_id="kv-a")]
    db, started = str(tmp_path / "sync.db"), str(tmp_path / "started.json")
    order: list[str] = []
    real_set = kodi.set_setting

    def watch(key: str, value: str) -> None:
        order.append(f"{key}={value}" + (" (started file present)" if os.path.exists(started) else ""))
        real_set(key, value)

    kodi.set_setting = watch  # type: ignore[method-assign]
    screens.start(
        kodi, read_settings(kodi), viewers, db_path=db, started_path=started, existing_ratings=True, now_utc=NOW
    )
    assert order == [f"{KEY_SYNC_ENABLED}=true", f"{KEY_SYNC_REQUEST}=1 (started file present)"]
    file = read_started(started)
    assert file is not None and file.connection == connection_id(URL) and file.existing_ratings and not file.paused
    store = RecordStore(db)
    store.open()
    assert store.seed_viewers() == frozenset({"kv-a"}) and store.seed_started_at() == NOW
    store.close()


def test_start_clears_pause(tmp_path):
    kodi = _kodi(tmp_path)
    started = str(tmp_path / "started.json")
    write_started(started, StartedFile(connection=connection_id(URL), start_id="old", paused=True, paused_reason="turned_off",
                                       bound_device="box"))  # fmt: skip
    screens.start(kodi, read_settings(kodi), [], db_path=str(tmp_path / "sync.db"), started_path=started,
                  existing_ratings=False, now_utc=NOW)  # fmt: skip
    file = read_started(started)
    assert file is not None and not file.paused and file.start_id != "old" and file.bound_device == ""


def test_start_again_leaves_seed_viewers(tmp_path):
    kodi = _kodi(tmp_path)
    db, started = str(tmp_path / "sync.db"), str(tmp_path / "started.json")
    screens.start(kodi, read_settings(kodi), [Viewer(name="Anna", sync_id="kv-a")], db_path=db, started_path=started,
                  existing_ratings=False, now_utc=NOW)  # fmt: skip
    screens.start(kodi, read_settings(kodi), [Viewer(name="Anna", sync_id="kv-a"), Viewer(name="Ben", sync_id="kv-b")],
                  db_path=db, started_path=started, existing_ratings=False, now_utc="2026-12-01T10:00:00Z")  # fmt: skip
    store = RecordStore(db)
    store.open()
    assert store.seed_viewers() == frozenset({"kv-a"}) and store.seed_started_at() == NOW
    store.close()
    assert screens.started_before(db, started, read_settings(kodi))


def _run(kodi: FakeKodi, answer: SetupResult | None) -> None:
    kodi.sync_setup_answer = answer
    screens.run(kodi, lambda: NOW, lambda: 1.0)


def test_setup_ensures_ids_before_counting(tmp_path):
    kodi = _kodi(tmp_path)
    JsonViewerStore(paths.viewers_path(kodi)).save([Viewer(name="Anna")])
    _run(kodi, None)
    [viewer] = JsonViewerStore(paths.viewers_path(kodi)).viewers()
    assert viewer.sync_id
    [request] = kodi.sync_setup_requests
    assert request.counts[0].startswith("Anna")
    assert kodi.busy_calls == [True, False]


def test_start_keeps_sync_ids(tmp_path):
    kodi = _kodi(tmp_path)
    JsonViewerStore(paths.viewers_path(kodi)).save([Viewer(name="Anna", sync_id="kv-0123456789ab")])
    _run(kodi, SetupResult(start=True))
    assert [v.sync_id for v in JsonViewerStore(paths.viewers_path(kodi)).viewers()] == ["kv-0123456789ab"]
    assert read_started(paths.sync_started_path(kodi)) is not None


def test_cancel_writes_nothing(tmp_path):
    kodi = _kodi(tmp_path)
    JsonViewerStore(paths.viewers_path(kodi)).save([Viewer(name="Anna", sync_id="kv-a")])
    _run(kodi, SetupResult(start=False))
    _run(kodi, None)
    assert not os.path.exists(paths.sync_started_path(kodi)) and not os.path.exists(paths.sync_db_path(kodi))
    assert KEY_SYNC_ENABLED not in kodi.settings


def test_not_paired_says_so(tmp_path):
    kodi = FakeKodi(root=str(tmp_path))
    _run(kodi, SetupResult(start=True))
    assert kodi.sync_setup_requests == [] and kodi.ok_calls


def test_trakt_addon_warning(tmp_path):
    kodi = _kodi(tmp_path)
    JsonViewerStore(paths.viewers_path(kodi)).save([Viewer(name="Anna", sync_id="kv-a")])
    kodi.rpc_handlers["Addons.GetAddonDetails"] = lambda p: {"addon": {"addonid": "script.trakt", "enabled": True}}
    _run(kodi, None)
    [request] = kodi.sync_setup_requests
    assert kodi.localised(screens.TRAKT_WARNING) in request.warnings


def test_no_viewers_offers_no_start(tmp_path):
    kodi = _kodi(tmp_path)
    _run(kodi, SetupResult(start=True))
    assert kodi.sync_setup_requests == [] and kodi.ok_calls
    assert not os.path.exists(paths.sync_db_path(kodi))


def test_a_library_read_error_says_so(tmp_path):
    kodi = _kodi(tmp_path)
    JsonViewerStore(paths.viewers_path(kodi)).save([Viewer(name="Anna", sync_id="kv-a")])

    def broken(params):
        raise RuntimeError("timed out")

    kodi.rpc_handlers["VideoLibrary.GetMovies"] = broken
    _run(kodi, SetupResult(start=True))
    assert kodi.sync_setup_requests == [] and kodi.ok_calls and kodi.busy_calls == [True, False]


def test_an_incomplete_library_read_says_so(tmp_path, monkeypatch):
    kodi = _kodi(tmp_path)
    JsonViewerStore(paths.viewers_path(kodi)).save([Viewer(name="Anna", sync_id="kv-a")])
    monkeypatch.setattr(screens, "read_library", lambda *a, **k: Library("L1", [], [], {}, [], False))
    _run(kodi, SetupResult(start=True))
    assert kodi.sync_setup_requests == [] and kodi.ok_calls


def test_a_failed_started_file_write_is_not_reported_as_started(tmp_path, monkeypatch):
    kodi = _kodi(tmp_path)
    JsonViewerStore(paths.viewers_path(kodi)).save([Viewer(name="Anna", sync_id="kv-a")])
    monkeypatch.setattr(screens, "write_started", lambda *a, **k: False)
    _run(kodi, SetupResult(start=True))
    assert KEY_SYNC_REQUEST not in kodi.settings and kodi.ok_calls and not kodi.notifications


def test_an_unusable_record_is_not_reported_as_started(tmp_path, monkeypatch):
    kodi = _kodi(tmp_path)
    JsonViewerStore(paths.viewers_path(kodi)).save([Viewer(name="Anna", sync_id="kv-a")])

    def busy(self, create=False):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(RecordStore, "open", busy)
    _run(kodi, SetupResult(start=True))
    assert KEY_SYNC_ENABLED not in kodi.settings and kodi.ok_calls and not kodi.notifications
