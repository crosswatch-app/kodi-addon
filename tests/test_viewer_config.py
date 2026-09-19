import pytest

from resources.lib.models import Viewer
from resources.lib.storage import JsonViewerStore
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
    assert available_playlists(_listing(["Anna TV", "Bob TV"])) == ["Anna TV", "Bob TV"]


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
    assert available_playlists(kodi) == ["Anna TV"]


def test_a_failing_listing_yields_nothing_rather_than_raising():
    def boom(params):
        raise RuntimeError("gone")

    assert available_playlists(FakeKodi(rpc_handlers={"Files.GetDirectory": boom})) == []


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
    """Answers a scripted sequence of select/input/multiselect calls."""

    def __init__(self, playlists, selects, inputs=None, multiselects=None, confirms=None) -> None:
        files = [{"label": n, "file": f"special://profile/playlists/video/{n}.xsp"} for n in playlists]
        super().__init__(rpc_handlers={"Files.GetDirectory": lambda params: {"files": files}})
        self._selects = list(selects)
        self._inputs = list(inputs or [])
        self._multiselects = list(multiselects or [])
        self._confirms = list(confirms or [])
        self.select_headings: list[str] = []

    def select(self, heading, options):
        self.select_headings.append(heading)
        # No silent fallback: an exhausted script means the test's flow is wrong, and a
        # default of -1 would rescue it by quietly backing out of the menu.
        assert self._selects, f"unscripted select: {heading} {options}"
        return self._selects.pop(0)

    def text_input(self, heading, default=""):
        return self._inputs.pop(0) if self._inputs else ""

    def multiselect(self, heading, options, preselect=None, autoclose=0):
        self.multiselect_calls.append((heading, list(options), preselect, autoclose))
        return self._multiselects.pop(0) if self._multiselects else None

    def confirm(self, heading, message):
        return self._confirms.pop(0) if self._confirms else False


def test_a_viewer_can_be_added_on_a_fresh_install(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    # menu is [Add viewer, Done]; after the add it is [anna, Add viewer, Done]
    kodi = ScriptedKodi(["Anna TV"], selects=[0, 2], inputs=["anna"], multiselects=[[0]])
    run_dialog(kodi, store)
    assert store.viewers() == [Viewer(name="anna", playlists=("Anna TV",))]


def test_editing_preselects_the_current_playlists(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna", playlists=("Bob TV",))])
    # pick viewer 0 -> Edit playlists -> keep -> Done
    kodi = ScriptedKodi(["Anna TV", "Bob TV"], selects=[0, 0, 2], multiselects=[[1]])
    run_dialog(kodi, store)
    assert kodi.multiselect_calls[0][2] == [1]
    assert store.viewers()[0].playlists == ("Bob TV",)


def test_cancelling_the_playlist_dialog_leaves_the_mapping_alone(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna", playlists=("Bob TV",))])
    kodi = ScriptedKodi(["Anna TV", "Bob TV"], selects=[0, 0, 2], multiselects=[None])
    run_dialog(kodi, store)
    assert store.viewers()[0].playlists == ("Bob TV",)


def test_a_viewer_can_be_removed(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna"), Viewer(name="bob")])
    # [anna, bob, Add, Done] -> pick anna -> Remove -> confirm -> [bob, Add, Done] -> Done
    kodi = ScriptedKodi([], selects=[0, 2, 2], confirms=[True])
    run_dialog(kodi, store)
    assert [v.name for v in store.viewers()] == ["bob"]


def test_a_duplicate_name_does_not_replace_the_existing_viewer(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna", playlists=("Anna TV",))])
    kodi = ScriptedKodi(["Anna TV"], selects=[1, 2], inputs=["ANNA"])
    run_dialog(kodi, store)
    assert store.viewers() == [Viewer(name="anna", playlists=("Anna TV",))]


def test_backing_out_of_the_menu_saves_nothing_new(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna")])
    run_dialog(ScriptedKodi([], selects=[-1]), store)
    assert [v.name for v in store.viewers()] == ["anna"]
