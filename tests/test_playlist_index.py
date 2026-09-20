import pytest

from resources.lib import log as logmod
from resources.lib.models import Viewer
from resources.lib.playlist_index import IndexBuilder, PlaylistIndex
from tests.fakes import FakeKodi

ANNA = Viewer(name="anna", playlists=("Anna TV",))
BOB = Viewer(name="bob", playlists=("Bob TV",))


def _kodi(members: dict[str, list[dict]], types: dict[str, str] | None = None):
    declared = types or {}

    def directory(params):
        path = params["directory"]
        name = path.rsplit("/", 1)[-1][: -len(".xsp")]
        return {"files": members.get(name, [])}

    def read_text(path, max_bytes=None):
        name = path.rsplit("/", 1)[-1][: -len(".xsp")]
        kind = declared.get(name, "tvshows")
        return f'<smartplaylist type="{kind}"><name>{name}</name></smartplaylist>'

    kodi = FakeKodi(rpc_handlers={"Files.GetDirectory": directory})
    kodi.read_text = read_text  # type: ignore[method-assign]
    return kodi


def _build(kodi, viewers, clock=lambda: 100.0):
    builder = IndexBuilder(kodi, viewers, clock)
    while not builder.step():
        pass
    return builder


def test_members_map_to_the_viewers_holding_the_playlist():
    kodi = _kodi({"Anna TV": [{"id": 42, "type": "tvshow"}], "Bob TV": [{"id": 7, "type": "tvshow"}]})
    index = _build(kodi, [ANNA, BOB]).result()
    assert index is not None
    assert index.viewers_for("tvshow", 42) == ("anna",)
    assert index.viewers_for("tvshow", 7) == ("bob",)


def test_one_playlist_is_expanded_per_step():
    kodi = _kodi({"Anna TV": [{"id": 42, "type": "tvshow"}], "Bob TV": [{"id": 7, "type": "tvshow"}]})
    builder = IndexBuilder(kodi, [ANNA, BOB], lambda: 0.0)
    assert builder.step() is False
    assert len([c for c in kodi.calls if c[0] == "Files.GetDirectory"]) == 1
    assert builder.result() is None
    assert builder.step() is True
    assert builder.result() is not None


def test_a_playlist_under_two_viewers_yields_both_names_and_expands_once():
    shared = ("Shared TV",)
    kodi = _kodi({"Shared TV": [{"id": 9, "type": "tvshow"}]})
    builder = _build(kodi, [Viewer(name="anna", playlists=shared), Viewer(name="bob", playlists=shared)])
    index = builder.result()
    assert index is not None
    assert index.viewers_for("tvshow", 9) == ("anna", "bob")
    assert len([c for c in kodi.calls if c[0] == "Files.GetDirectory"]) == 1


def test_movie_members_are_indexed_under_their_own_type():
    kodi = _kodi({"Anna TV": [{"id": 3, "type": "movie"}]}, types={"Anna TV": "movies"})
    index = _build(kodi, [ANNA]).result()
    assert index is not None
    assert index.viewers_for("movie", 3) == ("anna",)
    assert index.viewers_for("tvshow", 3) == ()


def test_an_episodes_playlist_is_skipped_without_being_expanded():
    kodi = _kodi({"Anna TV": [{"id": 1, "type": "episode"}]}, types={"Anna TV": "episodes"})
    builder = _build(kodi, [ANNA])
    assert [c for c in kodi.calls if c[0] == "Files.GetDirectory"] == []
    index = builder.result()
    assert index is not None and index.is_empty()


@pytest.fixture
def logs(tmp_path):
    """Both channels. A DEBUG line reaches the addon's own file and never the sink.

    That split is the privacy rule made structural in log._emit, so a test that watched only
    the sink could never see a debug line and would fail against correct code.
    """
    logmod.reset()
    sink: list[str] = []
    logmod.configure(log_dir=str(tmp_path), debug=True, sink=lambda msg, level: sink.append(msg))

    def read() -> tuple[str, list[str]]:
        path = tmp_path / "crosswatch.log"
        return (path.read_text(encoding="utf-8") if path.exists() else ""), sink

    yield read
    logmod.reset()


def _unreadable_kodi():
    kodi = _kodi({"Anna TV": []})
    kodi.read_text = lambda path, max_bytes=None: None  # type: ignore[method-assign]
    return kodi


def test_an_unreadable_playlist_is_named_in_the_addons_own_log(logs):
    """The file says names belong at DEBUG, but only the success path was honouring it.

    Without this, a viewer whose playlist was renamed gets playlists.unreadable naming a
    position in a list they cannot see, and turning debug logging on tells them no more.
    """
    _build(_unreadable_kodi(), [ANNA])
    contents, _ = logs()
    assert "playlists.unreadable_playlist" in contents
    assert "Anna TV" in contents


def test_a_failed_expansion_is_named_in_the_addons_own_log(logs):
    def directory(params):
        raise RuntimeError("database is locked")

    kodi = FakeKodi(rpc_handlers={"Files.GetDirectory": directory})
    kodi.read_text = lambda path, max_bytes=None: '<smartplaylist type="tvshows"/>'  # type: ignore[method-assign]
    _build(kodi, [ANNA])
    contents, _ = logs()
    assert "playlists.failed_playlist" in contents
    assert "Anna TV" in contents


def test_the_playlist_name_never_reaches_the_shared_log(logs):
    """The sink is Kodi's log, which gets attached to bug reports. Names must not be in it."""
    _build(_unreadable_kodi(), [ANNA])
    _, sink = logs()
    assert sink, "the failure must still be reported at WARNING"
    assert any("playlists.unreadable" in line for line in sink)
    assert not any("Anna TV" in line for line in sink)


def _mixed_kodi(bad: set[str], members: dict[str, list[dict]]):
    """Expands normally, except the named playlists cannot be read."""
    kodi = _kodi(members)
    plain = kodi.read_text

    def read_text(path, max_bytes=None):
        name = path.rsplit("/", 1)[-1][: -len(".xsp")]
        # max_bytes is not forwarded: the fake ignores it and KodiApi types it as int, so
        # passing the None default through fails the type check for no behavioural gain.
        return None if name in bad else plain(path)

    kodi.read_text = read_text  # type: ignore[method-assign]
    return kodi


def test_one_unreadable_playlist_does_not_cost_the_other_viewer_their_index():
    """The whole point. Before this, anna's rename took bob's identity down with it."""
    kodi = _mixed_kodi({"Anna TV"}, {"Anna TV": [{"id": 42, "type": "tvshow"}], "Bob TV": [{"id": 7, "type": "tvshow"}]})
    index = _build(kodi, [ANNA, BOB]).result()
    assert index is not None
    assert index.viewers_for("tvshow", 7) == ("bob",)
    assert index.degraded == frozenset({"anna"})


def test_a_degraded_viewer_gets_no_entries_at_all_rather_than_a_partial_set():
    """A half-indexed viewer is the harm the all-or-nothing build existed to prevent.

    anna holds two lists and one is unreadable. Publishing the readable half would attribute
    the shows it happens to contain and silently miss the rest, which reads as a deliberate
    answer rather than a failure.
    """
    anna = Viewer(name="anna", playlists=("Anna TV", "Anna Films"))
    kodi = _mixed_kodi({"Anna Films"}, {"Anna TV": [{"id": 42, "type": "tvshow"}]})
    index = _build(kodi, [anna]).result()
    assert index is not None
    assert index.viewers_for("tvshow", 42) == ()
    assert index.is_empty()
    assert index.degraded == frozenset({"anna"})


def test_a_shared_playlist_that_fails_degrades_every_viewer_holding_it():
    shared = "Household TV"
    anna = Viewer(name="anna", playlists=(shared,))
    bob = Viewer(name="bob", playlists=(shared, "Bob TV"))
    kodi = _mixed_kodi({shared}, {"Bob TV": [{"id": 7, "type": "tvshow"}]})
    index = _build(kodi, [anna, bob]).result()
    assert index is not None
    assert index.degraded == frozenset({"anna", "bob"})
    assert index.viewers_for("tvshow", 7) == ()


def test_the_healthy_playlists_are_still_expanded_after_one_fails():
    """The build no longer stops at the first failure, because the rest is no longer wasted."""
    seen: list[str] = []
    kodi = _mixed_kodi({"Anna TV"}, {"Bob TV": [{"id": 7, "type": "tvshow"}]})
    plain = kodi.rpc_handlers["Files.GetDirectory"]
    kodi.rpc_handlers["Files.GetDirectory"] = lambda params: (
        seen.append(params["directory"].rsplit("/", 1)[-1]), plain(params)
    )[1]
    _build(kodi, [ANNA, BOB])
    assert "Bob TV.xsp" in seen


def test_an_index_with_nothing_wrong_reports_no_degraded_viewers():
    kodi = _kodi({"Anna TV": [{"id": 42, "type": "tvshow"}]})
    index = _build(kodi, [ANNA]).result()
    assert index is not None
    assert index.degraded == frozenset()


def test_an_expansion_that_raises_degrades_the_owner_rather_than_the_build():
    """A raising Files.GetDirectory is the transport failure, not a missing file.

    It is handled the same way: the viewers holding that playlist lose their membership for
    this build, and the index is still published for everyone else.
    """
    def directory(params):
        raise RuntimeError("database is locked")

    kodi = FakeKodi(rpc_handlers={"Files.GetDirectory": directory})
    kodi.read_text = lambda path, max_bytes=None: '<smartplaylist type="tvshows"/>'  # type: ignore[method-assign]
    index = _build(kodi, [ANNA]).result()
    assert index is not None
    assert index.degraded == frozenset({"anna"})
    assert index.is_empty()


def test_unknown_item_and_none_id_return_no_viewers():
    index = PlaylistIndex(by_key={("tvshow", 42): ("anna",)}, built_at=0.0)
    assert index.viewers_for("tvshow", 999) == ()
    assert index.viewers_for("tvshow", None) == ()


def test_the_index_records_when_it_was_built():
    kodi = _kodi({"Anna TV": [{"id": 42, "type": "tvshow"}]})
    index = _build(kodi, [ANNA], clock=lambda: 1234.0).result()
    assert index is not None and index.built_at == 1234.0


def test_viewers_with_no_playlists_build_an_empty_index_immediately():
    builder = IndexBuilder(FakeKodi(), [Viewer(name="anna")], lambda: 0.0)
    assert builder.step() is True
    index = builder.result()
    assert index is not None and index.is_empty()
