import pytest

from resources.lib import remembered
from resources.lib.constants import (
    REMEMBERED_CHANGE,
    REMEMBERED_CONFIRM_FORGET_ALL,
    REMEMBERED_CONFIRM_FORGET_ONE,
    REMEMBERED_COVERED,
    REMEMBERED_FORGET,
    REMEMBERED_FORGET_ALL,
    REMEMBERED_LIBRARY_ID,
    REMEMBERED_NONE_YET,
    REMEMBERED_NOT_IN_LIBRARY,
    REMEMBERED_NOT_STABLE,
    REMEMBERED_WILL_ASK,
)
from resources.lib.models import Viewer
from resources.lib.storage import PromptMemory, RememberedAnswer
from tests.fakes import FakeKodi

ANNA = Viewer(name="anna")
BOB = Viewer(name="bob")
VIEWERS = [ANNA, BOB]

# What VideoLibrary.GetTVShows returns: one show with a scraper id, one without.
LIBRARY = {
    "tvshows": [
        {"tvshowid": 1, "title": "Alpha", "year": 2008, "uniqueid": {"tvdb": "100"}},
        {"tvshowid": 42, "title": "No Ids", "year": 2020, "uniqueid": {}},
    ]
}


class ScriptedKodi(FakeKodi):
    """Answers each dialog from a queue, in the order the screen asks."""

    def __init__(self, selects=(), answers=(), confirms=(), library=None) -> None:
        super().__init__(rpc_handlers={
            "VideoLibrary.GetTVShows": lambda params: LIBRARY if library is None else library,
            "VideoLibrary.GetTVShowDetails": lambda params: {"tvshowdetails": {"art": {"poster": "image://alpha/"}}},
        })
        self.selects = list(selects)
        self.answers = list(answers)
        self.confirms = list(confirms)
        self.select_calls: list[tuple[str, list[str]]] = []

    def who_watched(self, request):
        self.who_watched_calls.append(request)
        return self.answers.pop(0) if self.answers else None

    def select(self, heading, options):
        self.select_calls.append((heading, list(options)))
        return self.selects.pop(0) if self.selects else -1

    def confirm(self, heading, message, autoclose=0):
        return self.confirms.pop(0) if self.confirms else False


@pytest.fixture
def memory(tmp_path):
    return PromptMemory(str(tmp_path / "prompts.json"))


def _labels(memory, kodi=None):
    kodi = kodi or ScriptedKodi()
    return [row.label for row in remembered.build_rows(memory.entries(), remembered.library_shows(kodi), VIEWERS, kodi)]


def test_a_show_is_listed_by_its_library_title_with_its_viewers(memory):
    memory.remember("show:tvdb:100", ("anna", "bob"))
    assert _labels(memory) == ["Alpha: anna, bob"]


def test_rows_are_sorted_by_title(memory):
    memory.remember("tvshow:42", ("bob",), title="No Ids", year=2020)
    memory.remember("show:tvdb:100", ("anna",))
    assert _labels(memory) == ["Alpha: anna", "No Ids: bob"]


def test_a_removed_viewer_is_left_out_and_nobody_left_reads_as_asking_again(memory):
    memory.remember("show:tvdb:100", ("carol",))
    assert _labels(memory) == [f"Alpha: #{REMEMBERED_WILL_ASK}"]


def test_a_show_no_longer_in_the_library_keeps_its_stored_title(memory):
    memory.remember("show:tvdb:999", ("anna",), title="Gone Show", year=2001)
    assert _labels(memory) == [f"Gone Show (#{REMEMBERED_NOT_IN_LIBRARY}): anna"]


def test_a_show_with_neither_library_entry_nor_title_shows_its_id(memory):
    memory.remember("show:tvdb:999", ("anna",))
    assert _labels(memory) == [f"tvdb 999 (#{REMEMBERED_NOT_IN_LIBRARY}): anna"]


def test_a_library_id_answer_that_still_matches_is_listed_normally(memory):
    memory.remember("tvshow:42", ("anna",), title="No Ids", year=2020)
    rows = remembered.build_rows(memory.entries(), remembered.library_shows(ScriptedKodi()), VIEWERS, ScriptedKodi())
    assert [(r.label, r.changeable) for r in rows] == [("No Ids: anna", True)]


def test_a_library_id_answer_that_no_longer_matches_is_marked_and_cannot_be_changed(memory):
    """The id now holds another show, or the answer predates stored titles."""
    memory.remember("tvshow:42", ("anna",), title="Old Name", year=2020)
    memory.remember("tvshow:7", ("bob",))
    rows = remembered.build_rows(memory.entries(), remembered.library_shows(ScriptedKodi()), VIEWERS, ScriptedKodi())
    assert sorted((r.label, r.changeable) for r in rows) == sorted(
        [
            (f"#{REMEMBERED_LIBRARY_ID} (#{REMEMBERED_NOT_STABLE}): bob", False),
            (f"Old Name (#{REMEMBERED_NOT_STABLE}): anna", False),
        ]
    )


def test_a_failed_library_lookup_still_lists_answers_without_judging_them(memory):
    memory.remember("show:tvdb:100", ("anna",), title="Alpha", year=2008)

    def boom(params):
        raise RuntimeError("rpc down")

    kodi = ScriptedKodi()
    kodi.rpc_handlers["VideoLibrary.GetTVShows"] = boom
    assert _labels(memory, kodi) == ["Alpha: anna"]


def test_the_list_ends_with_forget_all_and_has_no_done_row(memory):
    """Kodi's select dialog has its own Cancel; a Done row would only repeat it."""
    memory.remember("show:tvdb:100", ("anna",))
    kodi = ScriptedKodi()
    remembered.run(kodi, memory, VIEWERS)
    assert kodi.select_calls[0][1] == ["Alpha: anna", f"#{REMEMBERED_FORGET_ALL}"]


def test_nothing_remembered_says_so_and_closes(memory):
    kodi = ScriptedKodi()
    remembered.run(kodi, memory, VIEWERS)
    assert kodi.notifications and kodi.notifications[0][1] == f"#{REMEMBERED_NONE_YET}"
    assert kodi.select_calls == []


def test_change_saves_the_new_answer_and_keeps_the_title(memory):
    memory.remember("show:tvdb:100", ("anna",), title="Alpha", year=2008)
    # Pick the show, then Change, then tick bob (row 0 is Everyone), then leave the list.
    kodi = ScriptedKodi(selects=[0, 0, -1], answers=[("bob",)])
    remembered.run(kodi, memory, VIEWERS)
    assert memory.recall("show:tvdb:100") == RememberedAnswer(viewers=("bob",), title="Alpha", year=2008)
    request = kodi.who_watched_calls[0]
    assert request.preselect == ("anna",), "the current answer is pre-ticked"
    assert (request.title, request.subtitle, request.poster) == ("Alpha", "2008", "image://alpha/")
    assert request.autoclose_seconds == 0 and request.close_on_playback is False


def test_change_fills_in_a_title_an_older_answer_lacked(memory):
    memory.remember("show:tvdb:100", ("anna",))
    kodi = ScriptedKodi(selects=[0, 0, -1], answers=[("anna", "bob")])
    remembered.run(kodi, memory, VIEWERS)
    assert memory.recall("show:tvdb:100") == RememberedAnswer(viewers=("anna", "bob"), title="Alpha", year=2008)


def test_change_with_nobody_ticked_forgets(memory):
    memory.remember("show:tvdb:100", ("anna",))
    remembered.run(ScriptedKodi(selects=[0, 0], answers=[()]), memory, VIEWERS)
    assert memory.recall("show:tvdb:100") is None


def test_cancelling_the_change_leaves_the_answer_alone(memory):
    memory.remember("show:tvdb:100", ("anna",))
    remembered.run(ScriptedKodi(selects=[0, 0, -1], answers=[None]), memory, VIEWERS)
    answer = memory.recall("show:tvdb:100")
    assert answer is not None and answer.viewers == ("anna",)


def test_change_for_a_show_missing_from_the_library_has_no_poster(memory):
    memory.remember("show:tvdb:999", ("anna",), title="Gone", year=2001)
    kodi = ScriptedKodi(selects=[0, 0, -1], answers=[("bob",)])
    remembered.run(kodi, memory, VIEWERS)
    request = kodi.who_watched_calls[0]
    assert (request.title, request.subtitle, request.poster) == ("Gone", "2001", "")


def test_forget_removes_only_that_show(memory):
    memory.remember("show:tvdb:100", ("anna",))
    memory.remember("tvshow:42", ("bob",), title="No Ids", year=2020)
    # Rows are sorted: Alpha first. Pick it, then Forget, then leave.
    remembered.run(ScriptedKodi(selects=[0, 1, -1]), memory, VIEWERS)
    assert list(memory.entries()) == ["tvshow:42"]


def test_a_row_that_cannot_be_checked_offers_only_forget(memory):
    memory.remember("tvshow:7", ("bob",))
    kodi = ScriptedKodi(selects=[0, 0])
    remembered.run(kodi, memory, VIEWERS)
    assert kodi.select_calls[1][1][0] == f"#{REMEMBERED_FORGET}"
    assert f"#{REMEMBERED_CHANGE}" not in kodi.select_calls[1][1]
    assert memory.entries() == {}


def test_forget_all_needs_a_yes(memory):
    memory.remember("show:tvdb:100", ("anna",))
    memory.remember("tvshow:42", ("bob",), title="No Ids", year=2020)
    # The row after the shows is Forget all.
    remembered.run(ScriptedKodi(selects=[2, -1], confirms=[False]), memory, VIEWERS)
    assert len(memory.entries()) == 2
    remembered.run(ScriptedKodi(selects=[2], confirms=[True]), memory, VIEWERS)
    assert memory.entries() == {}


def test_log_lines_carry_no_titles_or_names(memory, tmp_path):
    from resources.lib import log as logmod

    logmod.reset()
    captured: list[str] = []
    logmod.configure(log_dir=None, debug=False, sink=lambda msg, level: captured.append(msg))
    try:
        memory.remember("show:tvdb:100", ("anna",))
        remembered.run(ScriptedKodi(selects=[0, 0, -1], answers=[("bob",)]), memory, VIEWERS)
    finally:
        logmod.reset()
    assert captured
    assert not any("Alpha" in line or "anna" in line or "bob" in line for line in captured)


def test_forget_all_wording_follows_the_count(memory):
    """'Forget who watched 1 shows?' read wrongly on a real Kodi."""
    seen: list[str] = []

    class Recording(ScriptedKodi):
        def confirm(self, heading, message, autoclose=0):
            seen.append(message)
            return False

    memory.remember("show:tvdb:100", ("anna",))
    remembered.run(Recording(selects=[1, -1]), memory, VIEWERS)
    memory.remember("tvshow:42", ("bob",), title="No Ids", year=2020)
    remembered.run(Recording(selects=[2, -1]), memory, VIEWERS)
    assert seen == [f"#{REMEMBERED_CONFIRM_FORGET_ONE}", f"#{REMEMBERED_CONFIRM_FORGET_ALL}"]


# Anna's playlist holds Alpha (library id 1), so playback resolves Alpha by playlist and the
# remembered answer for it is never used.
ANNA_LISTED = Viewer(name="anna", playlists=("Anna TV",))
PLAYLIST_FILE = '<smartplaylist type="tvshows"><name>Anna TV</name></smartplaylist>'


class PlaylistKodi(ScriptedKodi):
    def __init__(self, members=({"id": 1, "type": "tvshow"},), **kwargs) -> None:
        super().__init__(**kwargs)
        self.files["special://profile/playlists/video/Anna TV.xsp"] = PLAYLIST_FILE
        self.rpc_handlers["Files.GetDirectory"] = lambda params: {"files": [dict(m) for m in members]}


def test_a_show_a_playlist_covers_is_marked_so(memory):
    memory.remember("show:tvdb:100", ("bob",))
    kodi = PlaylistKodi()
    library = remembered.library_shows(kodi)
    covered = remembered.covered_by_playlist(kodi, [ANNA_LISTED, BOB], library)
    rows = remembered.build_rows(memory.entries(), library, [ANNA_LISTED, BOB], kodi, covered)
    assert [row.label for row in rows] == [f"Alpha (#{REMEMBERED_COVERED}): bob"]
    # Still changeable: the answer applies again if the show leaves the playlist.
    assert rows[0].changeable


def test_a_show_no_playlist_covers_is_not_marked(memory):
    memory.remember("show:tvdb:100", ("bob",))
    kodi = PlaylistKodi(members=({"id": 42, "type": "tvshow"},))
    library = remembered.library_shows(kodi)
    covered = remembered.covered_by_playlist(kodi, [ANNA_LISTED, BOB], library)
    assert [row.label for row in remembered.build_rows(memory.entries(), library, [ANNA_LISTED, BOB], kodi, covered)] == [
        "Alpha: bob"
    ]


def test_an_unreadable_playlist_marks_nothing(memory):
    kodi = PlaylistKodi()
    del kodi.files["special://profile/playlists/video/Anna TV.xsp"]
    assert remembered.covered_by_playlist(kodi, [ANNA_LISTED], remembered.library_shows(kodi)) == frozenset()


def test_without_a_library_nothing_is_marked():
    assert remembered.covered_by_playlist(PlaylistKodi(), [ANNA_LISTED], None) == frozenset()


def test_the_screen_marks_covered_shows(memory):
    memory.remember("show:tvdb:100", ("bob",))
    kodi = PlaylistKodi()
    remembered.run(kodi, memory, [ANNA_LISTED, BOB])
    assert kodi.select_calls[0][1][0] == f"Alpha (#{REMEMBERED_COVERED}): bob"
