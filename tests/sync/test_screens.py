from __future__ import annotations

import os
from typing import Any

from resources.lib import paths
from resources.lib.config import KEY_BASE_URL, KEY_SYNC_ENABLED, KEY_SYNC_REQUEST, KEY_TOKEN, read_settings
from resources.lib.models import Viewer
from resources.lib.storage import JsonViewerStore
from resources.lib.sync import items, screens
from resources.lib.sync.counts import Counts, count_owners
from resources.lib.sync.files import StartedFile, StatusFile, read_requests, read_started, write_started, write_status
from resources.lib.sync.items import Library, LibraryRow, RowKey
from resources.lib.sync.record import RecordStore
from resources.lib.sync.reply import connection_id
from resources.lib.sync.server_maps import ServerRows
from resources.lib.ui.sync_setup import SetupResult
from resources.lib.ui.sync_status import SEND_RATINGS
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
    counts = count_owners(library, lambda row: owners.get(row.key.kodi_id, ("kv-a",)))
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
    counts = count_owners(library, lambda row: ("kv-a",))
    assert counts.films == {"kv-a": 2}


def test_setup_without_usable_index_offers_no_start():
    request = screens.setup_request(_words, [], Counts(), again=False, trakt=True, insecure=True,
                                    unreadable=("Anna's shows",))  # fmt: skip
    assert not request.can_start and request.counts == () and "Anna's shows" in request.intro


def test_trakt_addon_and_http_warnings():
    request = screens.setup_request(_words, [], Counts(), again=True, trakt=True, insecure=True, unreadable=())
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


def test_rows_a_media_server_add_on_maps_are_left_out_of_the_counts(tmp_path, monkeypatch):
    kodi = _kodi(tmp_path)
    JsonViewerStore(paths.viewers_path(kodi)).save([Viewer(name="Anna", sync_id="kv-a")])
    mapped: frozenset[tuple[items.ItemKind, int]] = frozenset({("movies", 1)})
    monkeypatch.setattr(screens, "read_server_rows", lambda k: ServerRows(mapped))
    seen: dict = {}

    def read(*args, **kwargs):
        seen.update(kwargs)
        return Library("L1", [], [], {}, [], True)

    monkeypatch.setattr(screens, "read_library", read)
    _run(kodi, None)
    assert seen["server_rows"] == mapped and kodi.sync_setup_requests


def test_a_media_server_map_that_cannot_be_read_says_so(tmp_path, monkeypatch):
    kodi = _kodi(tmp_path)
    JsonViewerStore(paths.viewers_path(kodi)).save([Viewer(name="Anna", sync_id="kv-a")])
    monkeypatch.setattr(screens, "read_server_rows", lambda k: ServerRows(frozenset(), ("plex.db",)))
    _run(kodi, SetupResult(start=True))
    assert kodi.sync_setup_requests == [] and kodi.busy_calls == [True, False]
    assert kodi.ok_calls == [(f"#{screens.HEADING}", f"#{screens.SERVER_MAP_UNREADABLE}")]


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


def _status_words(string_id: int) -> str:
    formats = {screens.LAST_UPLOAD: "Last upload {when} · {count} changes waiting", screens.MINUTES_AGO: "{count} min ago",
               screens.SHOWS: "{count} shows", screens.FILMS: "{count} films",
               screens.PLAYLISTS_UNREADABLE: "playlists: {names}", screens.VIEWER_UNKNOWN: "{name} unknown",
               screens.VIEWER_LATE_SEED: "{name} late", screens.MARKS_DROPPED: "{count} dropped",
               screens.ASSIGN_WATCHED: "watched? {title}", screens.ASSIGN_RATED: "rated? {title}",
               screens.UNOWNED_ROW: "{count} unowned", screens.MASS_RATINGS: "{count} at once"}  # fmt: skip
    return formats.get(string_id, f"#{string_id}")


VIEWERS = [Viewer(name="Anna", sync_id="kv-a"), Viewer(name="Tom", sync_id="kv-t")]


def _request(status: StatusFile | None, **kwargs: Any) -> Any:
    args = {"now_utc": NOW, "insecure": False, "address_changed": False, **kwargs}
    return screens.status_request(_status_words, status, VIEWERS, **args)


def test_status_lines_per_state():
    cases = {
        ("settled", ""): (f"#{screens.STATE_SETTLED}", "good"),
        ("syncing", ""): (f"#{screens.STATE_SYNCING}", "busy"),
        ("waiting", "playback"): (f"#{screens.WAIT_PLAYBACK}", "busy"),
        ("waiting", "scan"): (f"#{screens.WAIT_SCAN}", "busy"),
        ("waiting", "index"): (f"#{screens.WAIT_INDEX}", "busy"),
        ("waiting", ""): (f"#{screens.WAIT_CROSSWATCH}", "busy"),
        ("waiting", "not_sync_device"): (f"#{screens.WAIT_CROSSWATCH}", "busy"),
        ("waiting", "no_viewers"): (f"#{screens.WAIT_NO_VIEWERS}", "busy"),
        ("waiting", "server_map"): (f"#{screens.WAIT_SERVER_MAP}", "busy"),
        ("stopped", "record_missing"): (f"#{screens.STOP_RECORD}", "stopped"),
        ("stopped", "unauthorized"): (f"#{screens.STOP_TOKEN}", "stopped"),
        ("stopped", "no_sync"): (f"#{screens.STOP_NO_SYNC}", "stopped"),
        ("stopped", "paused"): (f"#{screens.STOP_PAUSED}", "stopped"),
        ("stopped", "device_id_changed"): (f"#{screens.STOP_DEVICE_ID}", "stopped"),
        ("stopped", "not_sync_device"): (f"#{screens.STOP_OTHER_DEVICE}", "stopped"),
        ("off", ""): (f"#{screens.STATE_OFF}", "busy"),
    }
    for (state, reason), expected in cases.items():
        request = _request(StatusFile(state=state, reason=reason))  # type: ignore[arg-type]
        assert (request.state, request.tone) == expected, (state, reason)
    off = _request(None)
    assert off.state == f"#{screens.STATE_OFF}" and off.summary == "" and off.send_ratings == ""


def test_status_summary_counts_and_rows():
    status = StatusFile(
        state="waiting", reason="library_has_sync_device", last_upload="2026-11-20T09:55:00Z", waiting_changes=3,
        counts={"viewers": {"kv-a": [2, 1]}, "shared": [1, 0], "unowned": [1, 2]},
        unowned_titles=["Old Film (1990)", "Some Show"], unreadable_playlists=["Tom's shows"],
        per_viewer={"kv-t": "viewer_unknown", "kv-gone": "late_seed"}, dropped_marks=4, mass_ratings=142,
        assignments=[{"id": "a1", "title": "The Office S02E03", "group": "history", "at": NOW},
                     {"id": "a2", "title": "Dune (2021)", "group": "ratings", "at": NOW}],
    )  # fmt: skip
    request = _request(status, insecure=True, address_changed=True)
    assert request.summary == "Last upload 5 min ago · 3 changes waiting"
    assert request.counts == ("Anna: 2 shows · 1 films", "Tom: 0", f"#{screens.SHARED}: 1 shows",
                              f"#{screens.NO_OWNER}: 1 shows · 2 films")  # fmt: skip
    assert [(r.key, r.text, r.problem) for r in request.rows] == [
        ("problem:0", f"#{screens.ADDRESS_CHANGED}", True),
        ("problem:1", f"#{screens.OTHER_SYNC_DEVICE}", True),
        ("problem:2", "playlists: Tom's shows", True),
        ("problem:3", f"#{screens.REMOVED_VIEWER} late", True),
        ("problem:4", "Tom unknown", True),
        ("problem:5", "4 dropped", True),
        ("problem:6", f"#{screens.HTTP_WARNING}", True),
        ("mass", f"142 at once. #{screens.MASS_ROW_ASK}", False),
        ("assign:a1", "watched? The Office S02E03", False),
        ("assign:a2", "rated? Dune (2021)", False),
        ("unowned", "3 unowned", False),
    ]


def _status_kodi(tmp_path, status: StatusFile) -> FakeKodi:
    kodi = _kodi(tmp_path)
    JsonViewerStore(paths.viewers_path(kodi)).save(VIEWERS)
    write_status(paths.sync_status_path(kodi), status)
    return kodi


def _requests(kodi: FakeKodi) -> list[tuple[str, dict]]:
    return [(r.kind, r.args) for r in read_requests(paths.sync_requests_path(kodi))]


def test_assign_writes_request(tmp_path):
    status = StatusFile(state="settled", assignments=[{"id": "a1", "title": "The Office S02E03", "group": "history"}])
    kodi = _status_kodi(tmp_path, status)
    kodi.who_watched_answer = ("Tom",)
    kodi.sync_status_answers = ["assign:a1"]
    screens.run_status(kodi, lambda: NOW)
    assert _requests(kodi) == [("assign", {"assignment": "a1", "viewers": ["kv-t"]})]
    assert kodi.settings[KEY_SYNC_REQUEST] == "1" and len(kodi.sync_status_requests) == 2


def test_assign_skipped_writes_nothing(tmp_path):
    status = StatusFile(state="settled", assignments=[{"id": "a1", "title": "The Office S02E03", "group": "history"}])
    kodi = _status_kodi(tmp_path, status)
    kodi.who_watched_answer = None
    kodi.sync_status_answers = ["assign:a1"]
    screens.run_status(kodi, lambda: NOW)
    assert _requests(kodi) == [] and KEY_SYNC_REQUEST not in kodi.settings


def test_send_existing_ratings_writes_request(tmp_path):
    kodi = _status_kodi(tmp_path, StatusFile(state="settled"))
    kodi.sync_status_answers = [SEND_RATINGS]
    screens.run_status(kodi, lambda: NOW)
    assert _requests(kodi) == [("send_existing_ratings", {})] and kodi.notifications


def test_mass_ratings_send_and_dont_send_write_requests(tmp_path):
    kodi = _status_kodi(tmp_path, StatusFile(state="settled", mass_ratings=142))
    for answer in (True, False, None):
        kodi.choice_answer = answer
        kodi.sync_status_answers = ["mass"]
        screens.run_status(kodi, lambda: NOW)
    assert [kind for kind, _ in _requests(kodi)] == ["mass_ratings_send", "mass_ratings_drop"]
    assert len(kodi.choices) == 3


def test_a_problem_row_shows_its_full_text_and_unowned_opens_the_titles(tmp_path):
    status = StatusFile(state="stopped", reason="paused", counts={"unowned": [0, 1]}, unowned_titles=["Old Film (1990)"],
                        dropped_marks=2)  # fmt: skip
    kodi = _status_kodi(tmp_path, status)
    kodi.sync_status_answers = ["problem:0", "unowned"]
    screens.run_status(kodi, lambda: NOW)
    assert len(kodi.ok_calls) == 1
    [(request, _)] = kodi.list_window_calls
    assert [r.title for r in request.rows] == ["Old Film (1990)"]


def test_status_flags_a_changed_pairing(tmp_path):
    kodi = _status_kodi(tmp_path, StatusFile(state="off"))
    write_started(
        paths.sync_started_path(kodi),
        StartedFile(connection=connection_id("https://old:8787/webhook/kodiwatcher"), start_id="s"),
    )
    screens.run_status(kodi, lambda: NOW)
    [request] = kodi.sync_status_requests
    assert request.rows[0].key == "problem:0" and request.rows[0].problem


def test_an_unmapped_viewer_error_still_gets_a_row():
    request = _request(StatusFile(state="waiting", reason="crosswatch", per_viewer={"kv-a": "certificate"}))
    assert request.state == f"#{screens.WAIT_CROSSWATCH}"
    assert [r.text for r in request.rows] == [f"#{screens.VIEWER_PROBLEM}"]


def test_answered_rows_are_left_off_until_the_engine_takes_them(tmp_path):
    status = StatusFile(state="settled", mass_ratings=5,
                        assignments=[{"id": "a1", "title": "T", "group": "history"},
                                     {"id": "a2", "title": "U", "group": "history"}])  # fmt: skip
    kodi = _status_kodi(tmp_path, status)
    kodi.who_watched_answer = ("Anna",)
    kodi.choice_answer = False
    kodi.sync_status_answers = ["assign:a1", "mass"]
    screens.run_status(kodi, lambda: NOW)
    first, second, third = kodi.sync_status_requests
    assert [r.key for r in first.rows] == ["mass", "assign:a1", "assign:a2"]
    assert [r.key for r in second.rows] == ["mass", "assign:a2"]
    assert [r.key for r in third.rows] == ["assign:a2"]


def test_assign_names_the_item_and_asks_whose_rating(tmp_path):
    status = StatusFile(state="settled", assignments=[{"id": "a2", "title": "Dune (2021)", "group": "ratings"}])
    kodi = _status_kodi(tmp_path, status)
    kodi.sync_status_answers = ["assign:a2"]
    screens.run_status(kodi, lambda: NOW)
    [request] = kodi.who_watched_calls
    assert request.title == "Dune (2021)"
    assert kodi.localised(screens.ASSIGN_RATED_HERE) == request.subtitle
    assert request.question == kodi.localised(screens.ASSIGN_RATING_QUESTION)


def test_status_when_off_shows_only_the_state():
    request = _request(StatusFile(state="off"))
    assert (request.needs_you, request.empty, request.note, request.send_ratings) == ("", "", "", "")


def test_start_sets_a_damaged_record_aside_and_makes_a_new_one(tmp_path):
    kodi = _kodi(tmp_path)
    db, started = tmp_path / "sync.db", str(tmp_path / "started.json")
    db.write_bytes(b"not a database at all, not even close" * 100)
    assert screens.start(kodi, read_settings(kodi), [Viewer(name="Anna", sync_id="kv-a")], db_path=str(db),
                         started_path=started, existing_ratings=False, now_utc=NOW)  # fmt: skip
    assert (tmp_path / "sync.db.bad").read_bytes().startswith(b"not a database")
    store = RecordStore(str(db))
    store.open()
    assert store.seed_viewers() == frozenset({"kv-a"})
    store.close()
