import hashlib

from resources.lib.sync.library_id import kodi_major, kodi_version, library_id, profile_has_own_database
from tests.fakes import FakeKodi

BUILD = {"System.BuildVersion": "21.2 (21.2.0) Git:20250105-abc"}
PROFILE = "special://profile/advancedsettings.xml"
MASTER = "special://masterprofile/advancedsettings.xml"
MYSQL = (
    "<advancedsettings><videodatabase><type>mysql</type><host>nas</host><port>3307</port>"
    "<user>kodi</user><pass>secret</pass></videodatabase></advancedsettings>"
)


def _kodi(files: dict[str, str] | None = None, build: dict[str, str] | None = None) -> FakeKodi:
    return FakeKodi(files=files or {}, info_labels=BUILD if build is None else build)


def _hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def test_kodi_version_and_major() -> None:
    kodi = _kodi()
    assert kodi_version(kodi) == "21.2"
    assert kodi_major(kodi) == 21
    assert kodi_version(_kodi(build={})) == ""
    assert kodi_major(_kodi(build={})) == 0


def test_sqlite_id_folder_and_versioned_name() -> None:
    assert library_id(_kodi()) == _hash("sqlite3", "/kodi/database", "MyVideos131")


def test_unknown_major_stands_in_for_itself() -> None:
    kodi = _kodi(build={"System.BuildVersion": "23.0"})
    assert library_id(kodi) == _hash("sqlite3", "/kodi/database", "MyVideos23")


def test_sqlite_host_and_name_overrides() -> None:
    xml = "<advancedsettings><videodatabase><host>special://home/db</host><name>Shared</name></videodatabase></advancedsettings>"
    assert library_id(_kodi({MASTER: xml})) == _hash("sqlite3", "/kodi/home/db", "Shared131")


def test_mysql_id_type_host_port_versioned_name() -> None:
    assert library_id(_kodi({MASTER: MYSQL})) == _hash("mysql", "nas", "3307", "MyVideos131")


def test_mysql_default_port() -> None:
    xml = MYSQL.replace("<port>3307</port>", "")
    assert library_id(_kodi({MASTER: xml})) == _hash("mysql", "nas", "3306", "MyVideos131")


def _without(tag: str) -> str:
    return MYSQL.replace(f"<{tag}>", f"<x{tag}>").replace(f"</{tag}>", f"</x{tag}>")


def test_mysql_block_missing_host_user_or_pass_is_sqlite() -> None:
    # Kodi falls back to SQLite and keeps a configured host as the SQLite folder
    # (CDatabase::InitSettings), so a block missing user or pass opens "nas" as a folder.
    assert library_id(_kodi({MASTER: _without("host")})) == _hash("sqlite3", "/kodi/database", "MyVideos131")
    for tag in ("user", "pass"):
        assert library_id(_kodi({MASTER: _without(tag)})) == _hash("sqlite3", "nas", "MyVideos131"), tag


def test_profile_advancedsettings_file_wins_even_without_video_block() -> None:
    kodi = _kodi({PROFILE: "<advancedsettings/>", MASTER: MYSQL})
    assert library_id(kodi) == _hash("sqlite3", "/kodi/database", "MyVideos131")
    assert library_id(kodi, master=True) == _hash("mysql", "nas", "3307", "MyVideos131")


def test_id_is_hash_only() -> None:
    value = library_id(_kodi({MASTER: MYSQL}))
    assert len(value) == 64 and "nas" not in value and "secret" not in value


def test_profile_private_on_own_sqlite() -> None:
    # The fake translates special://database and special://masterprofile/Database to
    # different folders, as Kodi does for a profile with its own databases.
    assert profile_has_own_database(_kodi())


def test_profile_shared_on_mysql_without_own_advancedsettings() -> None:
    assert not profile_has_own_database(_kodi({MASTER: MYSQL}))


def test_bare_ampersand_read_as_kodi_does() -> None:
    xml = MYSQL.replace("<pass>secret</pass>", "<pass>a&b</pass>")
    assert library_id(_kodi({MASTER: xml})) == _hash("mysql", "nas", "3307", "MyVideos131")


def test_unparseable_settings_give_no_id() -> None:
    assert library_id(_kodi({MASTER: "<advancedsettings><videodatabase>"})) == ""
