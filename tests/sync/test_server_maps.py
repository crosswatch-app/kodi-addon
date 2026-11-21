import sqlite3
from pathlib import Path

from resources.lib.kodi import KodiRpcError
from resources.lib.sync.server_maps import JELLYFIN_ADDON, PKC_ADDON, read_server_rows
from tests.fakes import FakeKodi

SQLITE_SETTINGS = "<advancedsettings><videolibrary/></advancedsettings>"
MYSQL_SETTINGS = (
    "<advancedsettings><videodatabase><type>mysql</type><host>nas</host><user>kodi</user>"
    "<pass>p</pass></videodatabase></advancedsettings>"
)


def _kodi(tmp_path: Path, *, installed: tuple[str, ...] = (PKC_ADDON, JELLYFIN_ADDON), settings: str = "") -> FakeKodi:
    def details(params):
        if params["addonid"] not in installed:
            raise KodiRpcError("Addons.GetAddonDetails", {"code": -32602, "message": "Invalid params."})
        return {"addon": {"addonid": params["addonid"], "enabled": True}}

    files = {"special://profile/advancedsettings.xml": settings} if settings else {}
    kodi = FakeKodi(rpc_handlers={"Addons.GetAddonDetails": details}, files=files, root=str(tmp_path))
    (tmp_path / "database").mkdir(exist_ok=True)
    return kodi


def _plex_db(tmp_path: Path, movies: list[int | None], episodes: list[int | None]) -> Path:
    path = tmp_path / "database" / "plex.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE movie(plex_id INTEGER PRIMARY KEY, kodi_id INTEGER, kodi_fileid INTEGER)")
    con.execute("CREATE TABLE episode(plex_id INTEGER PRIMARY KEY, kodi_id INTEGER, kodi_fileid INTEGER)")
    con.executemany("INSERT INTO movie(plex_id, kodi_id) VALUES (?, ?)", list(enumerate(movies, 1)))
    con.executemany("INSERT INTO episode(plex_id, kodi_id) VALUES (?, ?)", list(enumerate(episodes, 100)))
    con.commit()
    con.close()
    return path


def _jellyfin_db(tmp_path: Path, rows: list[tuple[str, int | None]]) -> Path:
    path = tmp_path / "database" / "jellyfin.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE jellyfin(jellyfin_id TEXT UNIQUE, media_folder TEXT, jellyfin_type TEXT, media_type TEXT,"
        " kodi_id INTEGER, kodi_fileid INTEGER, kodi_pathid INTEGER, parent_id INTEGER)"
    )
    con.executemany(
        "INSERT INTO jellyfin(jellyfin_id, media_type, kodi_id) VALUES (?, ?, ?)",
        [(f"j{i}", media_type, kodi_id) for i, (media_type, kodi_id) in enumerate(rows)],
    )
    con.commit()
    con.close()
    return path


def test_no_maps_means_no_server_rows(tmp_path) -> None:
    result = read_server_rows(_kodi(tmp_path))
    assert result.rows == frozenset() and result.unreadable == ()


def test_plexkodiconnects_films_and_episodes_are_server_backed(tmp_path) -> None:
    kodi = _kodi(tmp_path)
    _plex_db(tmp_path, movies=[3, 7, None], episodes=[40, None])
    result = read_server_rows(kodi)
    assert result.rows == {("movies", 3), ("movies", 7), ("episodes", 40)} and result.unreadable == ()


def test_jellyfins_films_and_episodes_are_server_backed_and_nothing_else(tmp_path) -> None:
    kodi = _kodi(tmp_path)
    _jellyfin_db(tmp_path, [("movie", 5), ("episode", 9), ("tvshow", 2), ("season", 4), ("episode", None)])
    assert read_server_rows(kodi).rows == {("movies", 5), ("episodes", 9)}


def test_both_maps_count(tmp_path) -> None:
    kodi = _kodi(tmp_path)
    _plex_db(tmp_path, movies=[1], episodes=[])
    _jellyfin_db(tmp_path, [("episode", 2)])
    assert read_server_rows(kodi).rows == {("movies", 1), ("episodes", 2)}


def test_a_map_left_by_an_uninstalled_add_on_is_ignored(tmp_path) -> None:
    # Kodi reuses ids: the leftover map's ids may be the household's own films by now.
    kodi = _kodi(tmp_path, installed=(JELLYFIN_ADDON,))
    _plex_db(tmp_path, movies=[3], episodes=[])
    assert read_server_rows(kodi).rows == frozenset()


def test_maps_do_not_count_for_a_mysql_library(tmp_path) -> None:
    # Both add-ons write the local SQLite file; their ids say nothing about a MySQL library.
    kodi = _kodi(tmp_path, settings=MYSQL_SETTINGS)
    _plex_db(tmp_path, movies=[3], episodes=[])
    assert read_server_rows(kodi).rows == frozenset()


def test_maps_count_for_a_sqlite_library_named_in_advancedsettings(tmp_path) -> None:
    kodi = _kodi(tmp_path, settings=SQLITE_SETTINGS)
    _plex_db(tmp_path, movies=[3], episodes=[])
    assert read_server_rows(kodi).rows == {("movies", 3)}


def test_a_map_that_exists_but_cannot_be_read_is_reported(tmp_path) -> None:
    kodi = _kodi(tmp_path)
    (tmp_path / "database" / "plex.db").write_bytes(b"not a database at all, just bytes" * 10)
    _jellyfin_db(tmp_path, [("movie", 5)])
    result = read_server_rows(kodi)
    assert result.unreadable == ("plex.db",) and result.rows == {("movies", 5)}


def test_a_map_without_the_expected_table_is_reported(tmp_path) -> None:
    kodi = _kodi(tmp_path)
    sqlite3.connect(tmp_path / "database" / "jellyfin.db").close()
    assert read_server_rows(kodi).unreadable == ("jellyfin.db",)


def test_reading_a_map_never_changes_it(tmp_path) -> None:
    kodi = _kodi(tmp_path)
    path = _plex_db(tmp_path, movies=[3], episodes=[])
    before = path.read_bytes()
    read_server_rows(kodi)
    assert path.read_bytes() == before and not (tmp_path / "database" / "plex.db-journal").exists()


def test_a_wal_map_reads_its_committed_rows_while_its_add_on_writes(tmp_path) -> None:
    kodi = _kodi(tmp_path)
    path = _plex_db(tmp_path, movies=[3], episodes=[])
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("BEGIN IMMEDIATE")
    con.execute("INSERT INTO movie(plex_id, kodi_id) VALUES (50, 4)")
    try:
        result = read_server_rows(kodi)
    finally:
        con.rollback()
        con.close()
    assert result.rows == {("movies", 3)} and result.unreadable == ()


def test_a_disabled_add_on_still_counts(tmp_path) -> None:
    kodi = _kodi(tmp_path)
    kodi.rpc_handlers["Addons.GetAddonDetails"] = lambda params: {"addon": {"addonid": params["addonid"], "enabled": False}}
    _plex_db(tmp_path, movies=[3], episodes=[])
    assert read_server_rows(kodi).rows == {("movies", 3)}


def test_an_install_check_kodi_cannot_answer_holds_like_an_unreadable_map(tmp_path) -> None:
    kodi = _kodi(tmp_path)
    _plex_db(tmp_path, movies=[3], episodes=[])

    def fails(params):
        raise TimeoutError("no answer")

    kodi.rpc_handlers["Addons.GetAddonDetails"] = fails
    assert read_server_rows(kodi).unreadable == ("plex.db",)


def test_an_install_answer_without_the_add_on_counts_as_not_installed(tmp_path) -> None:
    kodi = FakeKodi(rpc_handlers={}, root=str(tmp_path))
    (tmp_path / "database").mkdir()
    _plex_db(tmp_path, movies=[3], episodes=[])
    kodi.rpc_handlers["Addons.GetAddonDetails"] = lambda params: {"addon": None}
    assert read_server_rows(kodi).rows == frozenset()
