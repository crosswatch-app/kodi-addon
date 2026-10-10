import pytest

from resources.lib import viewer_config
from resources.lib.constants import (
    LABEL_BACK,
    VIEWERS_ADD,
    VIEWERS_ALSO,
    VIEWERS_EDIT_PLAYLISTS,
    VIEWERS_EDIT_PROFILES,
    VIEWERS_HEADING,
    VIEWERS_MISSING,
    VIEWERS_MISSING_COUNT,
    VIEWERS_NO_PLAYLISTS,
    VIEWERS_ONE_PLAYLIST,
    VIEWERS_PLAYLISTS,
    VIEWERS_PLAYLISTS_FOR,
    VIEWERS_REMOVE,
    VIEWERS_UNUSABLE_COUNT,
)
from resources.lib.models import Viewer
from resources.lib.playlist_index import PLAYLIST_DIR
from resources.lib.storage import JsonViewerStore
from resources.lib.ui.list_window import ListResult, ListState
from resources.lib.viewer_config import (
    apply_edit,
    available_playlists,
    remove_viewer,
    run_dialog,
    validate_name,
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


def test_a_viewer_with_an_unusable_playlist_is_flagged_but_not_as_missing():
    labels = viewer_config.viewer_labels(
        FakeKodi(), [Viewer(name="anna", playlists=("Eps", "Anna TV"))], ["Anna TV"], ["Eps"]
    )
    assert f"#{VIEWERS_UNUSABLE_COUNT}" in labels[0]
    assert f"#{VIEWERS_MISSING_COUNT}" not in labels[0]


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
    with pytest.raises(ValueError, match="name"):
        validate_name("  ", [])


def test_a_duplicate_name_is_rejected_case_insensitively():
    with pytest.raises(ValueError, match="already"):
        validate_name("Anna", [Viewer(name="anna")])


def test_a_valid_name_is_returned_trimmed():
    assert validate_name("  anna  ", []) == "anna"


# --- the dialog flow -------------------------------------------------------

class ScriptedKodi(FakeKodi):
    """Answers a scripted sequence of select, input and pick-window calls."""

    def __init__(self, playlists, selects, inputs=None, picks=None, confirms=None) -> None:
        files = [{"label": n, "file": f"special://profile/playlists/video/{n}.xsp"} for n in playlists]
        super().__init__(rpc_handlers={"Files.GetDirectory": lambda params: {"files": files}})
        self._selects = list(selects)
        self._inputs = list(inputs or [])
        self._picks = list(picks or [])
        self._confirms = list(confirms or [])
        self.select_headings: list[str] = []
        self.select_options: list[list[str]] = []

    def select(self, heading, options):
        self.select_headings.append(heading)
        self.select_options.append(list(options))
        # No silent fallback: an exhausted script means the test's flow is wrong, and a
        # default of -1 would rescue it by quietly backing out of the menu.
        assert self._selects, f"unscripted select: {heading} {options}"
        return self._selects.pop(0)

    def text_input(self, heading, default=""):
        return self._inputs.pop(0) if self._inputs else ""

    def list_window(self, request, state):
        self.list_window_calls.append((request, state))
        keys = self._picks.pop(0) if self._picks else None
        if keys is None:
            return ListResult("close", state)
        return ListResult("done", state, keys=tuple(keys))

    def confirm(self, heading, message, autoclose=0):
        return self._confirms.pop(0) if self._confirms else False


def test_a_viewer_can_be_added_on_a_fresh_install(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    # menu is [Add viewer]; after the add it is [anna, Add viewer]; Cancel closes it
    kodi = ScriptedKodi(["Anna TV"], selects=[0, -1], inputs=["anna"], picks=[["Anna TV"]])
    run_dialog(kodi, store)
    assert store.viewers() == [Viewer(name="anna", playlists=("Anna TV",))]


def test_editing_preselects_the_current_playlists(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna", playlists=("Bob TV",))])
    # pick viewer 0 -> Edit playlists -> keep -> Cancel
    kodi = ScriptedKodi(["Anna TV", "Bob TV"], selects=[0, 0, -1], picks=[["Bob TV"]])
    run_dialog(kodi, store)
    assert kodi.list_window_calls[0][0].ticked == ("Bob TV",)
    assert store.viewers()[0].playlists == ("Bob TV",)


def test_cancelling_the_playlist_dialog_leaves_the_mapping_alone(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna", playlists=("Bob TV",))])
    kodi = ScriptedKodi(["Anna TV", "Bob TV"], selects=[0, 0, -1], picks=[None])
    run_dialog(kodi, store)
    assert store.viewers()[0].playlists == ("Bob TV",)


def test_a_viewer_can_be_removed(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna"), Viewer(name="bob")])
    # [anna, bob, Add] -> pick anna -> Remove -> confirm -> [bob, Add] -> Cancel
    kodi = ScriptedKodi([], selects=[0, 2, -1], confirms=[True])
    run_dialog(kodi, store)
    assert [v.name for v in store.viewers()] == ["bob"]


def test_a_duplicate_name_does_not_replace_the_existing_viewer(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna", playlists=("Anna TV",))])
    kodi = ScriptedKodi(["Anna TV"], selects=[1, -1], inputs=["ANNA"])
    run_dialog(kodi, store)
    assert store.viewers() == [Viewer(name="anna", playlists=("Anna TV",))]


def test_backing_out_of_the_menu_saves_nothing_new(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna")])
    run_dialog(ScriptedKodi([], selects=[-1]), store)
    assert [v.name for v in store.viewers()] == ["anna"]


def test_a_viewer_whose_playlist_has_vanished_is_flagged_in_the_list():
    """The whole failure starts here: a playlist is renamed and nothing says so.

    The service cannot name it in the shared log, and the toast only fires once the build
    runs. The configuration screen is where someone goes to fix it, so it has to say which
    viewer is affected.
    """
    labels = viewer_config.viewer_labels(
        FakeKodi(),
        [Viewer(name="anna", playlists=("Gone", "Anna TV")), Viewer(name="bob", playlists=("Anna TV",))],
        ["Anna TV"],
    )
    assert labels[0].startswith("anna")
    assert "!" in labels[0]
    assert "!" not in labels[1]


def test_nothing_is_flagged_when_every_playlist_exists():
    labels = viewer_config.viewer_labels(FakeKodi(), [Viewer(name="anna", playlists=("Anna TV",))], ["Anna TV"])
    assert "!" not in labels[0]


def test_no_viewer_is_flagged_when_the_playlist_listing_failed():
    """available_playlists returns [] on an RPC failure, which is not the same as none
    existing. Flagging every viewer there would be a false alarm during a Kodi hiccup."""
    labels = viewer_config.viewer_labels(FakeKodi(), [Viewer(name="anna", playlists=("Anna TV",))], [])
    assert "!" not in labels[0]


def test_a_viewer_with_no_playlists_is_not_flagged():
    labels = viewer_config.viewer_labels(FakeKodi(), [Viewer(name="anna")], ["Anna TV"])
    assert "!" not in labels[0]


# --- translated labels ----------------------------------------------------


def test_the_viewer_list_is_translated_and_has_no_done_row(tmp_path):
    """Kodi's select dialog has its own Cancel, which closes and saves exactly as Done did."""
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna")])
    kodi = ScriptedKodi([], selects=[-1])
    run_dialog(kodi, store)
    assert kodi.select_headings == [f"#{VIEWERS_HEADING}"]
    assert kodi.select_options[0][-1] == f"#{VIEWERS_ADD}"
    assert len(kodi.select_options[0]) == 2


def test_the_viewer_menu_is_translated(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna")])
    kodi = ScriptedKodi([], selects=[0, 3, -1])
    run_dialog(kodi, store)
    assert kodi.select_options[1] == [
        f"#{VIEWERS_EDIT_PLAYLISTS}",
        f"#{VIEWERS_EDIT_PROFILES}",
        f"#{VIEWERS_REMOVE}",
        f"#{LABEL_BACK}",
    ]


def test_one_playlist_reads_in_the_singular():
    """'anna (1 playlists)' read wrongly."""
    one = viewer_config.viewer_labels(FakeKodi(), [Viewer(name="anna", playlists=("A",))], ["A"])
    two = viewer_config.viewer_labels(FakeKodi(), [Viewer(name="anna", playlists=("A", "B"))], ["A", "B"])
    assert one == [f"anna (#{VIEWERS_ONE_PLAYLIST})"]
    assert two == [f"anna (#{VIEWERS_PLAYLISTS})"]


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


def test_adding_a_viewer_shows_who_else_has_each_playlist(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="bob", playlists=("Shared",))])
    # [bob, Add viewer] -> Add -> name -> pick -> [bob, chloe, Add] -> Cancel
    kodi = ScriptedKodi(["Shared"], selects=[1, -1], inputs=["chloe"], picks=[["Shared"]])
    run_dialog(kodi, store)
    request, _ = kodi.list_window_calls[0]
    assert request.rows[0].detail == f"#{VIEWERS_ALSO}"
    assert store.viewers()[1] == Viewer(name="chloe", playlists=("Shared",))


def test_the_picker_logs_no_names(tmp_path):
    from resources.lib import log as logmod

    captured: list[str] = []
    logmod.configure(log_dir=None, debug=False, sink=lambda message, level: captured.append(message))
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna", playlists=("Secret list",))])
    # [anna, Add] -> anna -> Edit playlists -> Done -> Cancel
    run_dialog(ScriptedKodi(["Secret list"], selects=[0, 0, -1], picks=[["Secret list"]]), store)
    viewer_config._edit_playlists(FakeKodi(), Viewer(name="anna"), [])
    assert captured
    assert not any(word in line for line in captured for word in ("Secret list", "anna"))
