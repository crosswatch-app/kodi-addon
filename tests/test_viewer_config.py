import pytest

from resources.lib import viewer_config
from resources.lib.constants import (
    LABEL_BACK,
    VIEWERS_ADD,
    VIEWERS_EDIT_PLAYLISTS,
    VIEWERS_EDIT_PROFILES,
    VIEWERS_HEADING,
    VIEWERS_MISSING_COUNT,
    VIEWERS_ONE_PLAYLIST,
    VIEWERS_PLAYLISTS,
    VIEWERS_REMOVE,
    VIEWERS_UNUSABLE,
    VIEWERS_UNUSABLE_COUNT,
)
from resources.lib.models import Viewer
from resources.lib.playlist_index import PLAYLIST_DIR
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


def test_an_unusable_playlist_is_offered_only_to_the_viewer_who_has_it():
    kodi = FakeKodi()
    kodi.multiselect_answer = []
    viewer_config._edit_playlists(kodi, Viewer(name="bob"), ["Anna TV"], ["Eps"])
    _heading, options, _preselect, _autoclose = kodi.multiselect_calls[0]
    assert options == ["Anna TV"]


def test_an_unusable_playlist_is_shown_ticked_and_kept_under_its_real_name():
    """Like a missing one: dropping it unseen would look like the user's own edit."""
    kodi = FakeKodi()
    kodi.multiselect_answer = [0, 1]
    viewer = Viewer(name="anna", playlists=("Eps", "Anna TV"))
    kept = viewer_config._edit_playlists(kodi, viewer, ["Anna TV"], ["Eps"])
    _heading, options, preselect, _autoclose = kodi.multiselect_calls[0]
    assert options[1] == f"Eps (! #{VIEWERS_UNUSABLE})"
    assert preselect == [0, 1]
    assert kept == ("Anna TV", "Eps")


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

    def multiselect(self, heading, options, preselect=None, autoclose=0):
        self.multiselect_calls.append((heading, list(options), preselect, autoclose))
        return self._multiselects.pop(0) if self._multiselects else None

    def confirm(self, heading, message, autoclose=0):
        return self._confirms.pop(0) if self._confirms else False


def test_a_viewer_can_be_added_on_a_fresh_install(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    # menu is [Add viewer]; after the add it is [anna, Add viewer]; Cancel closes it
    kodi = ScriptedKodi(["Anna TV"], selects=[0, -1], inputs=["anna"], multiselects=[[0]])
    run_dialog(kodi, store)
    assert store.viewers() == [Viewer(name="anna", playlists=("Anna TV",))]


def test_editing_preselects_the_current_playlists(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna", playlists=("Bob TV",))])
    # pick viewer 0 -> Edit playlists -> keep -> Cancel
    kodi = ScriptedKodi(["Anna TV", "Bob TV"], selects=[0, 0, -1], multiselects=[[1]])
    run_dialog(kodi, store)
    assert kodi.multiselect_calls[0][2] == [1]
    assert store.viewers()[0].playlists == ("Bob TV",)


def test_cancelling_the_playlist_dialog_leaves_the_mapping_alone(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save([Viewer(name="anna", playlists=("Bob TV",))])
    kodi = ScriptedKodi(["Anna TV", "Bob TV"], selects=[0, 0, -1], multiselects=[None])
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


def test_editing_a_viewer_shows_the_playlist_that_no_longer_exists():
    """Preselect intersects with what exists, so a vanished name is invisible and silently
    dropped on confirm. Listing it, ticked, makes the removal something the user chose."""
    kodi = FakeKodi()
    kodi.multiselect_answer = []
    viewer = Viewer(name="anna", playlists=("Gone", "Anna TV"))
    viewer_config._edit_playlists(kodi, viewer, ["Anna TV"])
    heading, options, preselect, _autoclose = kodi.multiselect_calls[0]
    assert any("Gone" in option for option in options)
    assert preselect is not None and len(preselect) == 2


def test_keeping_a_missing_playlist_stores_its_real_name_not_the_decorated_label():
    """It may come back: a share can be offline, or a profile not yet loaded. Forcing the
    removal would be worse than keeping it. But storing "Gone (! missing)" would mean the
    configuration never matches the file again even once it returns."""
    kodi = FakeKodi()
    kodi.multiselect_answer = [0, 1]
    viewer = Viewer(name="anna", playlists=("Gone", "Anna TV"))
    kept = viewer_config._edit_playlists(kodi, viewer, ["Anna TV"])
    assert kept == ("Anna TV", "Gone")


def test_unticking_a_missing_playlist_removes_it():
    kodi = FakeKodi()
    kodi.multiselect_answer = [0]
    viewer = Viewer(name="anna", playlists=("Gone", "Anna TV"))
    assert viewer_config._edit_playlists(kodi, viewer, ["Anna TV"]) == ("Anna TV",)


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

