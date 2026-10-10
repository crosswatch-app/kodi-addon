import pytest

from resources.lib import paths, viewer_config
from resources.lib.constants import (
    VIEWER_MORE,
    VIEWER_NONE,
    VIEWER_PLAYLISTS,
    VIEWER_PROFILES,
    VIEWER_ROUTE_MISSING,
    VIEWER_ROUTE_OK,
    VIEWER_UNUSABLE,
    VIEWERS_ALSO,
    VIEWERS_COUNT,
    VIEWERS_HEADING,
    VIEWERS_MISSING,
    VIEWERS_NO_PLAYLISTS,
    VIEWERS_ONE_PLAYLIST,
    VIEWERS_ONE_PROFILE,
    VIEWERS_ONE_VIEWER,
    VIEWERS_PLAYLISTS,
    VIEWERS_PLAYLISTS_FOR,
    VIEWERS_PROFILES,
    VIEWERS_PROFILES_FAILED,
    VIEWERS_REMOVE_CONFIRM,
    VIEWERS_REMOVE_MESSAGE,
)
from resources.lib.models import Viewer
from resources.lib.outbox import config_fingerprint
from resources.lib.playlist_index import PLAYLIST_DIR
from resources.lib.routes import RouteFacts
from resources.lib.storage import JsonViewerStore, PromptMemory, RouteStore
from resources.lib.ui.list_window import ListResult, ListState
from resources.lib.ui.panel import PanelLine
from resources.lib.ui.viewers_window import ROUTE_ACCEPTED, ROUTE_REFUSED, ViewersResult
from resources.lib.viewer_config import (
    PlaylistListing,
    apply_edit,
    available_playlists,
    available_profiles,
    current_routes,
    panel_lines,
    profile_rows,
    remove_viewer,
    rename_viewer,
    route_state,
    run_dialog,
    validate_name,
    viewers_request,
)
from tests.fakes import FakeKodi


def _listing(names):
    files = [{"label": n, "file": f"special://profile/playlists/video/{n}.xsp"} for n in names]
    return FakeKodi(rpc_handlers={"Files.GetDirectory": lambda params: {"files": files}})


def test_lists_playlists_from_the_profile_folder():
    assert available_playlists(_listing(["Anna TV", "Bob TV"])).matchable == ["Anna TV", "Bob TV"]


def test_non_xsp_entries_are_ignored():
    kodi = FakeKodi(
        rpc_handlers={
            "Files.GetDirectory": lambda params: {
                "files": [
                    {"label": "notes", "file": "special://profile/playlists/video/notes.txt"},
                    {"label": "Anna TV", "file": "special://profile/playlists/video/Anna TV.xsp"},
                ]
            }
        }
    )
    assert available_playlists(kodi).matchable == ["Anna TV"]


def test_a_failing_listing_yields_nothing_rather_than_raising():
    def boom(params):
        raise RuntimeError("gone")

    assert available_playlists(FakeKodi(rpc_handlers={"Files.GetDirectory": boom})) == ([], [])


def _typed_listing(types):
    """A listing whose .xsp files declare the given type; None leaves a file unreadable."""
    files = [{"label": n, "file": f"{PLAYLIST_DIR}/{n}.xsp"} for n in types]
    contents = {
        f"{PLAYLIST_DIR}/{n}.xsp": f'<smartplaylist type="{t}"><name>{n}</name></smartplaylist>'
        for n, t in types.items()
        if t is not None
    }
    return FakeKodi(rpc_handlers={"Files.GetDirectory": lambda params: {"files": files}}, files=contents)


def test_only_show_and_film_playlists_are_offered():
    """The index matches tvshows and movies playlists only. Offering an episodes playlist
    lets a household pick one that never credits anybody, with nothing on screen saying so."""
    listing = available_playlists(_typed_listing({"Anna TV": "tvshows", "Films": "movies", "Eps": "episodes"}))
    assert listing.matchable == ["Anna TV", "Films"]
    assert listing.unusable == ["Eps"]


def test_a_playlist_without_a_type_is_offered_as_the_index_reads_it_as_shows():
    kodi = _typed_listing({})
    kodi.rpc_handlers["Files.GetDirectory"] = lambda params: {
        "files": [{"label": "Old", "file": f"{PLAYLIST_DIR}/Old.xsp"}]
    }
    kodi.files[f"{PLAYLIST_DIR}/Old.xsp"] = "<smartplaylist><name>Old</name></smartplaylist>"
    assert available_playlists(kodi).matchable == ["Old"]


def test_an_unreadable_playlist_is_still_offered():
    """Its type is unknown, not wrong. Hiding it would make a configured playlist look
    deleted during a read hiccup; the index already reports one it cannot read."""
    assert available_playlists(_typed_listing({"Anna TV": None})).matchable == ["Anna TV"]


def test_adding_a_viewer_appends_it():
    assert apply_edit([], "anna", ("Anna TV",), ("Anna",)) == [
        Viewer(name="anna", playlists=("Anna TV",), profiles=("Anna",))
    ]


def test_editing_an_existing_viewer_replaces_it_in_place():
    existing = [Viewer(name="anna", playlists=("Old",)), Viewer(name="bob")]
    got = apply_edit(existing, "anna", ("New",), ())
    assert got[0] == Viewer(name="anna", playlists=("New",), profiles=())
    assert [v.name for v in got] == ["anna", "bob"]


def test_removing_a_viewer_leaves_the_others():
    assert [v.name for v in remove_viewer([Viewer(name="anna"), Viewer(name="bob")], "anna")] == ["bob"]


def test_a_blank_name_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        validate_name("  ", [])


def test_a_duplicate_name_is_rejected_case_insensitively():
    with pytest.raises(ValueError, match="taken"):
        validate_name("Anna", [Viewer(name="anna")])


def test_a_valid_name_is_returned_trimmed():
    assert validate_name("  anna  ", []) == "anna"


# --- the playlist picker ---------------------------------------------------

def _picking(keys=None) -> FakeKodi:
    """A Kodi whose pick window answers Done with these keys, or Cancel for None."""
    kodi = FakeKodi()
    if keys is not None:
        kodi.list_window_results.append(ListResult("done", ListState(), keys=tuple(keys)))
    return kodi


class WordedKodi(FakeKodi):
    """FakeKodi's '#id' strings have no placeholder, which would hide the names."""

    def localised(self, string_id: int) -> str:
        return "Also %s" if string_id == VIEWERS_ALSO else super().localised(string_id)


def test_the_picker_is_a_pick_list_with_the_viewers_playlists_ticked():
    kodi = _picking(["Anna TV"])
    viewer = Viewer(name="anna", playlists=("Anna TV",))
    viewer_config._edit_playlists(kodi, viewer, ["Anna TV", "Bob TV"], (), [viewer])
    request, state = kodi.list_window_calls[0]
    assert request.pick and request.ticked == ("Anna TV",)
    assert request.heading == f"#{VIEWERS_PLAYLISTS_FOR}".replace("%s", "anna")
    assert request.filters == () and request.bulk_all == ""
    assert [row.key for row in request.rows] == ["Anna TV", "Bob TV"]
    assert state == ListState()


def test_done_returns_the_picked_playlists():
    viewer = Viewer(name="anna", playlists=("Anna TV",))
    kept = viewer_config._edit_playlists(_picking(["Bob TV"]), viewer, ["Anna TV", "Bob TV"])
    assert kept == ("Bob TV",)


def test_cancel_keeps_the_mapping():
    viewer = Viewer(name="anna", playlists=("Anna TV",))
    assert viewer_config._edit_playlists(_picking(None), viewer, ["Anna TV"]) is None


def test_an_unusable_playlist_is_offered_to_nobody():
    rows = viewer_config.playlist_rows(FakeKodi(), Viewer(name="bob"), ["Anna TV"], ["Eps"], [])
    assert [row.key for row in rows] == ["Anna TV"]


def test_missing_playlists_come_last_ticked_under_their_real_names_with_a_tag():
    """Listed, because it may come back (a share offline, a rename): a removal is then
    something the household chose. The real name keeps the configuration matching the file
    when it returns."""
    kodi = _picking(["Anna TV", "Gone"])
    viewer = Viewer(name="anna", playlists=("Gone", "Anna TV"))
    kept = viewer_config._edit_playlists(kodi, viewer, ["Anna TV"], [], [viewer])
    request, _ = kodi.list_window_calls[0]
    assert [(row.key, row.title, row.tag) for row in request.rows] == [
        ("Anna TV", "Anna TV", ""),
        ("Gone", "Gone", f"#{VIEWERS_MISSING}"),
    ]
    assert kept == ("Anna TV", "Gone")


def test_an_unusable_playlist_is_not_listed_and_done_drops_it():
    """It can never credit anyone, so there is nothing to choose; the viewer list already
    marks the viewer unusable before the picker opens."""
    kodi = _picking(["Anna TV"])
    viewer = Viewer(name="anna", playlists=("Eps", "Anna TV"))
    kept = viewer_config._edit_playlists(kodi, viewer, ["Anna TV"], ["Eps"], [viewer])
    request, _ = kodi.list_window_calls[0]
    assert [row.key for row in request.rows] == ["Anna TV"]
    assert kept == ("Anna TV",)


def test_cancel_keeps_an_unusable_playlist():
    viewer = Viewer(name="anna", playlists=("Eps", "Anna TV"))
    assert viewer_config._edit_playlists(_picking(None), viewer, ["Anna TV"], ["Eps"]) is None


def test_an_unusable_playlist_is_not_mistaken_for_a_missing_one():
    viewer = Viewer(name="anna", playlists=("Eps",))
    rows = viewer_config.playlist_rows(FakeKodi(), viewer, ["Anna TV"], ["Eps"], [viewer])
    assert [row.key for row in rows] == ["Anna TV"]


def test_a_viewer_with_only_missing_playlists_still_gets_the_window_to_untick_them():
    kodi = _picking([])
    viewer = Viewer(name="anna", playlists=("Gone",))
    assert viewer_config._edit_playlists(kodi, viewer, [], ["Eps"]) == ()
    assert kodi.ok_calls == [] and len(kodi.list_window_calls) == 1


def test_unticking_a_missing_playlist_removes_it():
    viewer = Viewer(name="anna", playlists=("Gone", "Anna TV"))
    assert viewer_config._edit_playlists(_picking(["Anna TV"]), viewer, ["Anna TV"]) == ("Anna TV",)


def test_also_names_the_other_viewers_with_that_playlist_in_configuration_order():
    anna = Viewer(name="anna", playlists=("Shared",))
    viewers = [Viewer(name="chloe", playlists=("Shared",)), anna, Viewer(name="bob", playlists=("Shared", "Bob TV"))]
    rows = viewer_config.playlist_rows(WordedKodi(), anna, ["Shared", "Bob TV", "Free"], [], viewers)
    assert [row.detail for row in rows] == ["Also chloe, bob", "Also bob", ""]


def test_also_leaves_out_the_viewer_being_edited_whatever_the_case():
    anna = Viewer(name="anna", playlists=("Shared",))
    rows = viewer_config.playlist_rows(WordedKodi(), anna, ["Shared"], [], [Viewer(name="ANNA", playlists=("Shared",))])
    assert rows[0].detail == ""


def test_no_playlists_at_all_shows_a_notice_and_no_window():
    kodi = FakeKodi()
    assert viewer_config._edit_playlists(kodi, Viewer(name="anna"), []) is None
    assert kodi.list_window_calls == []
    assert kodi.ok_calls == [(f"#{VIEWERS_PLAYLISTS_FOR}".replace("%s", "anna"), f"#{VIEWERS_NO_PLAYLISTS}")]


def test_a_failed_listing_keeps_the_mapping():
    """available_playlists returns nothing on an RPC failure. A window then would list none
    of the viewer's playlists, and Done would drop them all."""
    kodi = FakeKodi()
    viewer = Viewer(name="anna", playlists=("Anna TV",))
    assert viewer_config._edit_playlists(kodi, viewer, [], []) is None
    assert kodi.list_window_calls == []


# --- the viewer screens' pieces --------------------------------------------

class Worded(FakeKodi):
    """Real templates for the strings these helpers fill, so the names show in assertions."""

    TEXTS = {
        VIEWERS_ONE_PLAYLIST: "1 playlist", VIEWERS_PLAYLISTS: "%s playlists",
        VIEWERS_ONE_PROFILE: "1 profile", VIEWERS_PROFILES: "%s profiles",
        VIEWERS_MISSING: "missing", VIEWERS_ALSO: "Also %s", VIEWER_NONE: "none",
        VIEWER_PLAYLISTS: "Playlists", VIEWER_PROFILES: "Profiles", VIEWER_UNUSABLE: "unusable",
        VIEWER_MORE: "and %s more", VIEWER_ROUTE_OK: "CrossWatch route", VIEWER_ROUTE_MISSING: "No CrossWatch route",
        VIEWERS_ONE_VIEWER: "1 viewer", VIEWERS_COUNT: "%s viewers",
    }

    def localised(self, string_id: int) -> str:
        return self.TEXTS.get(string_id, super().localised(string_id))


def test_a_rename_may_keep_the_viewers_own_name_in_another_case():
    assert validate_name("Anna", [Viewer(name="anna")], own="anna") == "Anna"


def test_a_rename_to_another_viewers_name_is_taken():
    with pytest.raises(ValueError, match="taken"):
        validate_name("BOB", [Viewer(name="anna"), Viewer(name="bob")], own="anna")


def test_rename_viewer_keeps_playlists_and_profiles():
    viewers = [Viewer(name="anna", playlists=("A",), profiles=("P",)), Viewer(name="bob")]
    assert rename_viewer(viewers, "anna", "annie") == [
        Viewer(name="annie", playlists=("A",), profiles=("P",)),
        Viewer(name="bob"),
    ]


def test_available_profiles_reads_kodis_labels():
    profiles = {"profiles": [{"label": "Master user"}, {"label": " Kids "}, {}]}
    kodi = FakeKodi(rpc_handlers={"Profiles.GetProfiles": lambda p: profiles})
    assert available_profiles(kodi) == ["Master user", "Kids"]


def test_a_failed_profile_listing_is_none_not_empty():
    def boom(params):
        raise RuntimeError("rpc down")

    assert available_profiles(FakeKodi(rpc_handlers={"Profiles.GetProfiles": boom})) is None


def test_profile_rows_tick_the_viewers_profiles_and_list_missing_ones_last():
    viewer = Viewer(name="anna", profiles=("Kids", "Old"))
    others = [viewer, Viewer(name="bob", profiles=("Kids",))]
    rows, ticked = profile_rows(Worded(), viewer, ["Master user", "Kids"], others)
    assert [(r.key, r.detail, r.tag) for r in rows] == [
        ("Master user", "", ""),
        ("Kids", "Also bob", ""),
        ("Old", "", "missing"),
    ]
    assert ticked == ("Kids", "Old")


def test_a_profile_matches_kodis_label_whatever_the_case():
    viewer = Viewer(name="anna", profiles=("master user",))
    rows, ticked = profile_rows(Worded(), viewer, ["Master user"], [viewer])
    assert [r.key for r in rows] == ["Master user"]
    assert ticked == ("Master user",)


# --- the viewer screen -----------------------------------------------------

class ScriptedKodi(FakeKodi):
    """Scripted viewers-window, pick, keyboard and confirm answers. Running out of window
    answers fails the test: a default would hide a wrong flow."""

    def __init__(self, playlists, windows=(), picks=(), inputs=(), confirms=(), profiles=("Master user",)):
        files = [{"label": n, "file": f"special://profile/playlists/video/{n}.xsp"} for n in playlists]
        super().__init__(rpc_handlers={
            "Files.GetDirectory": lambda params: {"files": files},
            "Profiles.GetProfiles": lambda params: {"profiles": [{"label": p} for p in profiles]},
        })
        self._windows, self._picks = list(windows), list(picks)
        self._inputs, self._confirms = list(inputs), list(confirms)
        self.windows_shown: list = []  # (request, key) per opening
        self.pick_requests: list = []

    def viewers_window(self, request, key):
        self.windows_shown.append((request, key))
        assert self._windows, "unscripted viewers window"
        action, chosen = self._windows.pop(0)
        return ViewersResult(action, chosen)

    def list_window(self, request, state):
        assert request.pick, "only the pickers use the list window"
        self.pick_requests.append(request)
        keys = self._picks.pop(0) if self._picks else None
        return ListResult("close", state) if keys is None else ListResult("done", state, keys=tuple(keys))

    def text_input(self, heading, default=""):
        self.input_calls.append((heading, default))
        return self._inputs.pop(0) if self._inputs else ""

    def confirm_window(self, heading, message):
        self.confirm_window_calls.append((heading, message))
        return self._confirms.pop(0) if self._confirms else False


def _stores(tmp_path, viewers, answers=None):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save(viewers)
    memory = PromptMemory(str(tmp_path / "prompts.json"))
    for key, names in (answers or {}).items():
        memory.remember(key, tuple(names))
    return store, memory


def test_the_window_shows_every_viewer_and_opens_on_the_first(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna"), Viewer(name="bob")])
    kodi = ScriptedKodi([], windows=[("close", "anna")])
    run_dialog(kodi, store, memory)
    request, key = kodi.windows_shown[0]
    assert [r.key for r in request.rows] == ["anna", "bob"] and key == ""
    assert request.heading == f"#{VIEWERS_HEADING}"


def test_playlists_are_saved_at_once_and_the_window_reopens_on_the_viewer(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna"), Viewer(name="bob")])
    saved_during = []
    kodi = ScriptedKodi(["Anna TV"], windows=[("playlists", "bob"), ("close", "bob")], picks=[["Anna TV"]])
    real = kodi.viewers_window

    def window(request, key):
        saved_during.append(store.viewers())
        return real(request, key)

    kodi.viewers_window = window
    run_dialog(kodi, store, memory)
    assert saved_during[1] == [Viewer(name="anna"), Viewer(name="bob", playlists=("Anna TV",))]
    assert kodi.windows_shown[1][1] == "bob"


def test_a_cancelled_playlist_pick_changes_nothing(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna", playlists=("A",))])
    kodi = ScriptedKodi(["A", "B"], windows=[("playlists", "anna"), ("close", "anna")], picks=[None])
    run_dialog(kodi, store, memory)
    assert store.viewers() == [Viewer(name="anna", playlists=("A",))]
    assert kodi.windows_shown[1][1] == "anna"


def test_profiles_are_picked_from_kodi_and_saved(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna")])
    kodi = ScriptedKodi(
        [], windows=[("profiles", "anna"), ("close", "anna")], picks=[["Kids"]], profiles=("Master user", "Kids"),
    )
    run_dialog(kodi, store, memory)
    assert [r.key for r in kodi.pick_requests[0].rows] == ["Master user", "Kids"]
    assert store.viewers() == [Viewer(name="anna", profiles=("Kids",))]


def test_a_failed_profile_listing_shows_a_notice_and_changes_nothing(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna", profiles=("Kids",))])
    kodi = ScriptedKodi([], windows=[("profiles", "anna"), ("close", "anna")])

    def boom(params):
        raise RuntimeError("down")

    kodi.rpc_handlers["Profiles.GetProfiles"] = boom
    run_dialog(kodi, store, memory)
    assert kodi.ok_calls and kodi.ok_calls[0][1] == f"#{VIEWERS_PROFILES_FAILED}"
    assert store.viewers() == [Viewer(name="anna", profiles=("Kids",))]


def test_rename_carries_the_answers_over_and_reopens_on_the_new_name(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna", playlists=("A",))], {"show:tvdb:1": ["anna"]})
    kodi = ScriptedKodi(["A"], windows=[("rename", "anna"), ("close", "annie")], inputs=["annie"])
    run_dialog(kodi, store, memory)
    assert kodi.input_calls[0][1] == "anna"  # prefilled
    assert store.viewers() == [Viewer(name="annie", playlists=("A",))]
    answer = memory.recall("show:tvdb:1")
    assert answer is not None and answer.viewers == ("annie",)
    assert kodi.windows_shown[1][1] == "annie"


def test_a_case_only_rename_is_accepted_and_answers_follow(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna")], {"show:tvdb:1": ["anna"]})
    kodi = ScriptedKodi([], windows=[("rename", "anna"), ("close", "Anna")], inputs=["Anna"])
    run_dialog(kodi, store, memory)
    assert store.viewers() == [Viewer(name="Anna")]
    answer = memory.recall("show:tvdb:1")
    assert answer is not None and answer.viewers == ("Anna",)


def test_a_rename_to_another_viewers_name_changes_nothing(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna"), Viewer(name="bob")])
    kodi = ScriptedKodi([], windows=[("rename", "anna"), ("close", "anna")], inputs=["BOB"])
    run_dialog(kodi, store, memory)
    assert [v.name for v in store.viewers()] == ["anna", "bob"]
    assert kodi.windows_shown[1][1] == "anna"


def test_remove_asks_then_strips_the_name_from_remembered_answers(tmp_path):
    store, memory = _stores(
        tmp_path, [Viewer(name="anna"), Viewer(name="bob")], {"show:tvdb:1": ["anna"], "show:tvdb:2": ["anna", "bob"]}
    )
    kodi = ScriptedKodi([], windows=[("remove", "anna"), ("close", "bob")], confirms=[True])
    run_dialog(kodi, store, memory)
    assert kodi.confirm_window_calls[0] == (f"#{VIEWERS_REMOVE_CONFIRM}", f"#{VIEWERS_REMOVE_MESSAGE}")
    assert [v.name for v in store.viewers()] == ["bob"]
    assert memory.recall("show:tvdb:1") is None
    shared = memory.recall("show:tvdb:2")
    assert shared is not None and shared.viewers == ("bob",)
    assert kodi.windows_shown[1][1] == "bob"  # the row that took its place


def test_removing_the_last_row_reopens_on_the_one_before(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna"), Viewer(name="bob")])
    kodi = ScriptedKodi([], windows=[("remove", "bob"), ("close", "anna")], confirms=[True])
    run_dialog(kodi, store, memory)
    assert kodi.windows_shown[1][1] == "anna"


def test_remove_answered_no_changes_nothing(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna")], {"show:tvdb:1": ["anna"]})
    kodi = ScriptedKodi([], windows=[("remove", "anna"), ("close", "anna")], confirms=[False])
    run_dialog(kodi, store, memory)
    assert store.viewers() == [Viewer(name="anna")]
    answer = memory.recall("show:tvdb:1")
    assert answer is not None and answer.viewers == ("anna",)


def test_removing_the_only_viewer_shows_the_empty_window(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna")])
    kodi = ScriptedKodi([], windows=[("remove", "anna"), ("close", "")], confirms=[True])
    run_dialog(kodi, store, memory)
    empty, key = kodi.windows_shown[1]
    assert empty.rows == () and key == ""


def test_add_viewer_asks_the_name_then_playlists_then_reopens_on_them(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="bob", playlists=("Shared",))])
    kodi = ScriptedKodi(["Shared"], windows=[("add", "bob"), ("close", "chloe")], inputs=["chloe"], picks=[["Shared"]])
    run_dialog(kodi, store, memory)
    assert kodi.pick_requests[0].rows[0].detail == f"#{VIEWERS_ALSO}"
    assert store.viewers()[1] == Viewer(name="chloe", playlists=("Shared",))
    assert kodi.windows_shown[1][1] == "chloe"


def test_a_cancelled_add_reopens_on_the_viewer_it_started_from(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna"), Viewer(name="bob")])
    kodi = ScriptedKodi([], windows=[("add", "bob"), ("close", "bob")], inputs=[""])
    run_dialog(kodi, store, memory)
    assert [v.name for v in store.viewers()] == ["anna", "bob"]
    assert kodi.windows_shown[1][1] == "bob"


def test_a_fresh_install_goes_straight_to_add_viewer(tmp_path):
    store, memory = _stores(tmp_path, [])
    kodi = ScriptedKodi(["Anna TV"], windows=[("close", "anna")], inputs=["anna"], picks=[["Anna TV"]])
    run_dialog(kodi, store, memory)
    assert store.viewers() == [Viewer(name="anna", playlists=("Anna TV",))]
    assert kodi.windows_shown[0][1] == "anna"


def test_a_fresh_install_that_cancels_the_name_closes_without_a_window(tmp_path):
    store, memory = _stores(tmp_path, [])
    kodi = ScriptedKodi([], inputs=[""])
    run_dialog(kodi, store, memory)
    assert kodi.windows_shown == [] and store.viewers() == []


def test_an_action_on_a_viewer_that_is_gone_just_reopens(tmp_path):
    """The window's key comes from rows built before the action; never act on a stranger."""
    store, memory = _stores(tmp_path, [Viewer(name="anna")])
    kodi = ScriptedKodi([], windows=[("remove", "zed"), ("close", "")])
    run_dialog(kodi, store, memory)
    assert store.viewers() == [Viewer(name="anna")] and kodi.confirm_window_calls == []


def test_the_screens_log_no_names(tmp_path):
    from resources.lib import log as logmod

    captured: list[str] = []
    logmod.configure(log_dir=None, debug=False, sink=lambda message, level: captured.append(message))
    store, memory = _stores(tmp_path, [Viewer(name="anna"), Viewer(name="bob")], {"show:tvdb:1": ["anna"]})
    kodi = ScriptedKodi(
        ["Secret list"],
        windows=[("playlists", "anna"), ("rename", "anna"), ("remove", "bob"), ("close", "annie")],
        picks=[["Secret list"]], inputs=["annie"], confirms=[True],
    )
    run_dialog(kodi, store, memory)
    assert captured
    assert not any(word in line for line in captured for word in ("Secret list", "anna", "annie", "bob"))


# --- CrossWatch routes -----------------------------------------------------

def _paired(tmp_path, token="tok") -> FakeKodi:
    return FakeKodi(
        root=str(tmp_path), settings={"webhook_base_url": "http://cw:8787/webhook/kodiwatcher", "webhook_token": token}
    )


def test_current_routes_reads_the_facts_of_this_pairing(tmp_path):
    kodi = _paired(tmp_path)
    facts = RouteFacts(1, frozenset({"anna"}), frozenset({"anna"}))
    RouteStore(paths.routes_path(kodi)).save(config_fingerprint("tok"), facts)
    assert current_routes(kodi) == facts


def test_current_routes_ignores_an_earlier_pairing(tmp_path):
    kodi = _paired(tmp_path, token="new")
    RouteStore(paths.routes_path(kodi)).save(config_fingerprint("old"), RouteFacts(1, frozenset({"anna"}), frozenset({"anna"})))
    assert current_routes(kodi) is None


def test_current_routes_is_nothing_when_not_paired(tmp_path):
    assert current_routes(FakeKodi(root=str(tmp_path))) is None


def test_the_window_carries_the_route_state(tmp_path):
    store, memory = _stores(tmp_path, [Viewer(name="anna"), Viewer(name="bob")])
    kodi = ScriptedKodi([], windows=[("close", "anna")])
    run_dialog(kodi, store, memory, lambda: RouteFacts(1, frozenset({"anna"}), frozenset({"anna", "bob"})))
    assert [r.route for r in kodi.windows_shown[0][0].rows] == [ROUTE_ACCEPTED, ROUTE_REFUSED]


def test_the_window_rereads_the_route_facts_on_every_pass(tmp_path):
    """The service pings within a tick of a change; the open screen should catch up."""
    store, memory = _stores(tmp_path, [Viewer(name="anna"), Viewer(name="bob")])
    answers = iter([
        RouteFacts(1, frozenset({"anna"}), frozenset({"anna", "bob"})),
        RouteFacts(1, frozenset({"anna", "bob"}), frozenset({"anna", "bob"})),
    ])
    kodi = ScriptedKodi([], windows=[("rename", "anna"), ("close", "anna")], inputs=[""])
    run_dialog(kodi, store, memory, lambda: next(answers))
    assert [r.route for r in kodi.windows_shown[0][0].rows] == [ROUTE_ACCEPTED, ROUTE_REFUSED]
    assert [r.route for r in kodi.windows_shown[1][0].rows] == [ROUTE_ACCEPTED, ROUTE_ACCEPTED]


# --- The split window's rows ------------------------------------------------

def test_panel_lines_list_playlists_then_profiles_with_their_tags():
    listing = PlaylistListing(["Anna TV"], ["Eps"])
    viewer = Viewer(name="anna", playlists=("Anna TV", "Gone", "Eps"), profiles=("Kids", "Old"))
    assert panel_lines(Worded(), viewer, listing, ["Master user", "kids"]) == (
        PanelLine("Playlists", heading=True),
        PanelLine("Anna TV"),
        PanelLine("Gone", tag="missing", warn=True),
        PanelLine("Eps", tag="unusable", warn=True),
        PanelLine("Profiles", heading=True),
        PanelLine("Kids"),  # matched without case, as playback matches the profile
        PanelLine("Old", tag="missing", warn=True),
    )


def test_panel_lines_say_none_for_an_empty_section():
    lines = panel_lines(Worded(), Viewer(name="anna"), PlaylistListing(["X"], []), [])
    assert lines == (
        PanelLine("Playlists", heading=True), PanelLine("none"),
        PanelLine("Profiles", heading=True), PanelLine("none"),
    )


def test_a_failed_playlist_listing_tags_no_playlist_missing():
    lines = panel_lines(Worded(), Viewer(name="a", playlists=("Anna TV",)), PlaylistListing([], []), [])
    assert PanelLine("Anna TV") in lines


def test_a_failed_profile_listing_tags_no_profile_missing():
    """None is "could not be read", not "Kodi has no profiles"."""
    lines = panel_lines(Worded(), Viewer(name="a", profiles=("Kids",)), PlaylistListing(["X"], []), None)
    assert PanelLine("Kids") in lines


def test_route_state_claims_only_what_crosswatch_was_asked():
    routes = RouteFacts(1, frozenset({"anna"}), frozenset({"anna", "bob"}))
    assert route_state(Viewer(name="anna"), routes) == ROUTE_ACCEPTED
    assert route_state(Viewer(name="bob"), routes) == ROUTE_REFUSED
    assert route_state(Viewer(name="dan"), routes) == ""  # added since the last ping
    assert route_state(Viewer(name="anna"), None) == ""  # unpaired or no reply yet


def test_viewers_request_has_a_row_per_viewer_in_order_and_the_count():
    viewers = [Viewer(name="bob"), Viewer(name="anna", playlists=("Gone",))]
    request = viewers_request(Worded(), viewers, PlaylistListing(["X"], []), [], None)
    assert [r.key for r in request.rows] == ["bob", "anna"]
    assert [r.warn for r in request.rows] == [False, True]
    assert request.count == "2 viewers"
    assert (request.more, request.route_ok, request.route_missing) == (
        "and %s more", "CrossWatch route", "No CrossWatch route",
    )


def test_viewers_request_counts_one_viewer_and_none():
    one = viewers_request(Worded(), [Viewer(name="anna")], PlaylistListing(["X"], []), [], None)
    assert one.count == "1 viewer"
    assert viewers_request(Worded(), [], PlaylistListing(["X"], []), [], None).count == ""
