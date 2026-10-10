import pytest

from resources.lib import remembered
from resources.lib.constants import (
    REMEMBERED_ANSWER,
    REMEMBERED_CONFIRM_FORGET_ALL,
    REMEMBERED_CONFIRM_FORGET_ONE,
    REMEMBERED_COVERED_BY,
    REMEMBERED_GONE,
    REMEMBERED_HEADING,
    REMEMBERED_LIBRARY_ID,
    REMEMBERED_NO_TITLE,
    REMEMBERED_NONE_YET,
    REMEMBERED_NOT_IN_LIBRARY_HEAD,
    REMEMBERED_NOT_STABLE_HEAD,
    REMEMBERED_PLAYLIST_OF,
    REMEMBERED_POINTS_AT,
    REMEMBERED_WILL_ASK,
    WINDOW_ALL,
    WINDOW_FORGET_ALL,
    WINDOW_FORGET_SHOWN,
)
from resources.lib.models import Viewer
from resources.lib.storage import PromptMemory, RememberedAnswer
from resources.lib.ui.list_window import ListResult, ListState
from resources.lib.ui.panel import PanelLine, slot_properties
from tests.fakes import FakeKodi

ANNA = Viewer(name="anna")
BOB = Viewer(name="bob")
VIEWERS = [ANNA, BOB]
START = ListState()

# What VideoLibrary.GetTVShows returns: one show with a scraper id, one without.
LIBRARY = {
    "tvshows": [
        {"tvshowid": 1, "title": "Alpha", "year": 2008, "uniqueid": {"tvdb": "100"}, "art": {"poster": "image://alpha/"}},
        {"tvshowid": 42, "title": "No Ids", "year": 2020, "uniqueid": {}, "art": {}},
    ]
}


class ScriptedKodi(FakeKodi):
    """Answers each window from a queue, in the order the screen opens them."""

    def __init__(self, lists=(), answers=(), confirms=(), library=None) -> None:
        super().__init__(rpc_handlers={"VideoLibrary.GetTVShows": lambda params: LIBRARY if library is None else library})
        self.list_window_results = list(lists)
        self.answers = list(answers)
        self.confirms = list(confirms)

    def who_watched(self, request):
        self.who_watched_calls.append(request)
        return self.answers.pop(0) if self.answers else None

    def confirm_window(self, heading, message):
        self.confirm_window_calls.append((heading, message))
        return self.confirms.pop(0) if self.confirms else False


@pytest.fixture
def memory(tmp_path):
    return PromptMemory(str(tmp_path / "prompts.json"))


def _open(key: str, state: ListState = START) -> ListResult:
    return ListResult("open", state, key=key)


def _bulk(*keys: str, state: ListState = START) -> ListResult:
    return ListResult("bulk", state, keys=keys)


def _rows(memory, kodi=None, viewers=VIEWERS):
    kodi = kodi or ScriptedKodi()
    return remembered.build_rows(memory.entries(), remembered.library_shows(kodi), viewers, kodi)


def _shown(memory, kodi=None):
    return [(row.name, row.tag, row.detail) for row in _rows(memory, kodi)]


# --- rows -------------------------------------------------------------------


def test_a_show_is_listed_by_its_library_title_with_its_viewers(memory):
    memory.remember("show:tvdb:100", ("anna", "bob"))
    assert _shown(memory) == [("Alpha", "", "anna, bob")]
    assert _rows(memory)[0].thumb == "image://alpha/"


def test_rows_are_sorted_by_title(memory):
    memory.remember("tvshow:42", ("bob",), title="No Ids", year=2020)
    memory.remember("show:tvdb:100", ("anna",))
    assert [row[0] for row in _shown(memory)] == ["Alpha", "No Ids"]


def test_a_removed_viewer_is_left_out_and_nobody_left_reads_as_asking_again(memory):
    memory.remember("show:tvdb:100", ("carol",))
    assert _shown(memory) == [("Alpha", "", f"#{REMEMBERED_WILL_ASK}")]


def test_a_show_no_longer_in_the_library_keeps_its_stored_title(memory):
    memory.remember("show:tvdb:999", ("anna",), title="Gone Show", year=2001)
    assert _shown(memory) == [("Gone Show", f"#{REMEMBERED_NOT_IN_LIBRARY_HEAD}", "anna")]
    assert _rows(memory)[0].thumb == ""


def test_a_show_with_neither_library_entry_nor_title_shows_its_id(memory):
    memory.remember("show:tvdb:999", ("anna",))
    assert _shown(memory) == [("tvdb 999", f"#{REMEMBERED_NOT_IN_LIBRARY_HEAD}", "anna")]


def test_a_library_id_answer_that_still_matches_is_listed_normally(memory):
    memory.remember("tvshow:42", ("anna",), title="No Ids", year=2020)
    assert [(r.name, r.tag, r.changeable) for r in _rows(memory)] == [("No Ids", "", True)]


def test_a_library_id_answer_that_no_longer_matches_is_marked_and_cannot_be_changed(memory):
    """The id now holds another show, or the answer predates stored titles."""
    memory.remember("tvshow:42", ("anna",), title="Old Name", year=2020)
    memory.remember("tvshow:7", ("bob",))
    assert sorted((r.name, r.tag, r.changeable) for r in _rows(memory)) == sorted(
        [
            (f"#{REMEMBERED_LIBRARY_ID}", f"#{REMEMBERED_NOT_STABLE_HEAD}", False),
            ("Old Name", f"#{REMEMBERED_NOT_STABLE_HEAD}", False),
        ]
    )


def test_a_failed_library_lookup_still_lists_answers_without_judging_them(memory):
    memory.remember("show:tvdb:100", ("anna",), title="Alpha", year=2008)

    def boom(params):
        raise RuntimeError("rpc down")

    kodi = ScriptedKodi()
    kodi.rpc_handlers["VideoLibrary.GetTVShows"] = boom
    assert _shown(memory, kodi) == [("Alpha", "", "anna")]


# --- the list window ----------------------------------------------------------


def test_the_list_holds_every_answer_with_viewer_filters(memory):
    memory.remember("show:tvdb:100", ("anna",))
    kodi = ScriptedKodi()
    remembered.run(kodi, memory, VIEWERS)
    request, state = kodi.list_window_calls[0]
    assert request.heading == f"#{REMEMBERED_HEADING}"
    assert [row.title for row in request.rows] == ["Alpha"]
    assert request.rows[0].names == ("anna",) and request.rows[0].thumb == "image://alpha/"
    assert [f.label for f in request.filters] == [f"#{WINDOW_ALL}", "anna", "bob", f"#{REMEMBERED_WILL_ASK}"]
    assert (request.bulk_all, request.bulk_shown) == (f"#{WINDOW_FORGET_ALL}", f"#{WINDOW_FORGET_SHOWN}")
    assert state == ListState()


def test_the_viewer_filters_match_their_rows(memory):
    memory.remember("show:tvdb:100", ("anna",))
    memory.remember("tvshow:42", ("carol",), title="No Ids", year=2020)  # carol is not configured
    kodi = ScriptedKodi()
    remembered.run(kodi, memory, VIEWERS)
    request, _ = kodi.list_window_calls[0]
    every, anna, bob, will_ask = request.filters
    assert [r.title for r in request.rows if anna.match(r)] == ["Alpha"]
    assert [r.title for r in request.rows if bob.match(r)] == []
    assert [r.title for r in request.rows if will_ask.match(r)] == ["No Ids"]
    assert all(every.match(r) for r in request.rows)


def test_a_removed_viewer_has_no_filter_and_their_rows_count_as_will_ask(memory):
    memory.remember("show:tvdb:100", ("carol",))
    kodi = ScriptedKodi()
    remembered.run(kodi, memory, VIEWERS)
    request, _ = kodi.list_window_calls[0]
    assert "carol" not in [f.label for f in request.filters]
    assert request.filters[-1].match(request.rows[0])


def test_nothing_remembered_says_so_and_opens_no_window(memory):
    kodi = ScriptedKodi()
    remembered.run(kodi, memory, VIEWERS)
    assert kodi.notifications and kodi.notifications[0][1] == f"#{REMEMBERED_NONE_YET}"
    assert kodi.list_window_calls == []


def test_opening_a_show_changes_its_answer_and_reopens_where_it_was(memory):
    memory.remember("show:tvdb:100", ("anna",), title="Alpha", year=2008)
    where = ListState(search="al", filter_index=1, position=0)
    kodi = ScriptedKodi(lists=[_open("show:tvdb:100", where)], answers=[("bob",)])
    remembered.run(kodi, memory, VIEWERS)
    assert memory.recall("show:tvdb:100") == RememberedAnswer(viewers=("bob",), title="Alpha", year=2008)
    request = kodi.who_watched_calls[0]
    assert request.offer_forget is True and request.preselect == ("anna",), "the current answer is pre-ticked"
    assert (request.title, request.subtitle, request.poster) == ("Alpha", "2008", "image://alpha/")
    assert request.autoclose_seconds == 0 and request.close_on_playback is False
    assert kodi.list_window_calls[1][1] == where


def test_change_fills_in_a_title_an_older_answer_lacked(memory):
    memory.remember("show:tvdb:100", ("anna",))
    remembered.run(ScriptedKodi(lists=[_open("show:tvdb:100")], answers=[("anna", "bob")]), memory, VIEWERS)
    assert memory.recall("show:tvdb:100") == RememberedAnswer(viewers=("anna", "bob"), title="Alpha", year=2008)


def test_change_for_a_show_missing_from_the_library_has_no_poster(memory):
    memory.remember("show:tvdb:999", ("anna",), title="Gone", year=2001)
    kodi = ScriptedKodi(lists=[_open("show:tvdb:999")], answers=[("bob",)])
    remembered.run(kodi, memory, VIEWERS)
    request = kodi.who_watched_calls[0]
    assert (request.title, request.subtitle, request.poster) == ("Gone", "2001", "")


def test_forget_in_the_window_forgets_the_answer(memory):
    memory.remember("show:tvdb:100", ("anna",))
    memory.remember("tvshow:42", ("bob",), title="No Ids", year=2020)
    remembered.run(ScriptedKodi(lists=[_open("show:tvdb:100")], answers=[()]), memory, VIEWERS)
    assert list(memory.entries()) == ["tvshow:42"]


def test_skip_in_the_window_leaves_the_answer_alone(memory):
    memory.remember("show:tvdb:100", ("anna",))
    remembered.run(ScriptedKodi(lists=[_open("show:tvdb:100")], answers=[None]), memory, VIEWERS)
    answer = memory.recall("show:tvdb:100")
    assert answer is not None and answer.viewers == ("anna",)


def test_a_row_that_cannot_be_checked_asks_to_forget_instead(memory):
    memory.remember("tvshow:7", ("bob",))
    kodi = ScriptedKodi(lists=[_open("tvshow:7")], confirms=[True])
    remembered.run(kodi, memory, VIEWERS)
    assert kodi.who_watched_calls == []
    assert kodi.confirm_window_calls == [(f"#{REMEMBERED_HEADING}", f"#{REMEMBERED_CONFIRM_FORGET_ONE}")]
    assert memory.entries() == {}


def test_a_row_that_cannot_be_checked_stays_on_no(memory):
    memory.remember("tvshow:7", ("bob",))
    remembered.run(ScriptedKodi(lists=[_open("tvshow:7")], confirms=[False]), memory, VIEWERS)
    assert list(memory.entries()) == ["tvshow:7"]


def test_forget_shown_forgets_only_the_rows_shown(memory):
    memory.remember("show:tvdb:100", ("anna",))
    memory.remember("tvshow:42", ("bob",), title="No Ids", year=2020)
    memory.remember("show:tvdb:999", ("bob",), title="Gone Show", year=2001)
    kodi = ScriptedKodi(lists=[_bulk("show:tvdb:100", "show:tvdb:999")], confirms=[True])
    remembered.run(kodi, memory, VIEWERS)
    assert list(memory.entries()) == ["tvshow:42"]
    assert kodi.confirm_window_calls[0][1] == f"#{REMEMBERED_CONFIRM_FORGET_ALL}"


def test_forget_all_needs_a_yes(memory):
    memory.remember("show:tvdb:100", ("anna",))
    memory.remember("tvshow:42", ("bob",), title="No Ids", year=2020)
    remembered.run(ScriptedKodi(lists=[_bulk("show:tvdb:100", "tvshow:42")], confirms=[False]), memory, VIEWERS)
    assert len(memory.entries()) == 2
    remembered.run(ScriptedKodi(lists=[_bulk("show:tvdb:100", "tvshow:42")], confirms=[True]), memory, VIEWERS)
    assert memory.entries() == {}


def test_forget_wording_follows_the_count(memory):
    """'Forget who watched 1 shows?' read wrongly on a real Kodi."""
    memory.remember("show:tvdb:100", ("anna",))
    memory.remember("tvshow:42", ("bob",), title="No Ids", year=2020)
    kodi = ScriptedKodi(lists=[_bulk("show:tvdb:100"), _bulk("show:tvdb:100", "tvshow:42")])
    remembered.run(kodi, memory, VIEWERS)
    assert [message for _, message in kodi.confirm_window_calls] == [
        f"#{REMEMBERED_CONFIRM_FORGET_ONE}",
        f"#{REMEMBERED_CONFIRM_FORGET_ALL}",
    ]


def test_bulk_with_nothing_shown_asks_nothing(memory):
    memory.remember("show:tvdb:100", ("anna",))
    kodi = ScriptedKodi(lists=[_bulk()])
    remembered.run(kodi, memory, VIEWERS)
    assert kodi.confirm_window_calls == []
    assert len(memory.entries()) == 1


def test_forgetting_the_last_answer_closes_with_the_notice(memory):
    memory.remember("show:tvdb:100", ("anna",))
    kodi = ScriptedKodi(lists=[_open("show:tvdb:100")], answers=[()])
    remembered.run(kodi, memory, VIEWERS)
    assert len(kodi.list_window_calls) == 1
    assert kodi.notifications[-1][1] == f"#{REMEMBERED_NONE_YET}"


def test_log_lines_carry_no_titles_or_names(memory):
    from resources.lib import log as logmod

    logmod.reset()
    captured: list[str] = []
    logmod.configure(log_dir=None, debug=False, sink=lambda msg, level: captured.append(msg))
    try:
        memory.remember("show:tvdb:100", ("anna",))
        memory.remember("tvshow:42", ("bob",), title="No Ids", year=2020)
        kodi = ScriptedKodi(
            lists=[_open("show:tvdb:100"), _bulk("tvshow:42", state=ListState(search="No"))],
            answers=[("bob",)],
            confirms=[True],
        )
        remembered.run(kodi, memory, VIEWERS)
    finally:
        logmod.reset()
    assert captured
    assert not any(word in line for line in captured for word in ("Alpha", "No Ids", "anna", "bob"))


# --- playlist cover ------------------------------------------------------------

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
    covers = remembered.covering_playlists(kodi, [ANNA_LISTED, BOB], library)
    assert covers == {"show:tvdb:100": (("anna", "Anna TV"),)}
    rows = remembered.build_rows(memory.entries(), library, [ANNA_LISTED, BOB], kodi, covers)
    assert [(row.name, row.tag, row.detail) for row in rows] == [("Alpha", f"#{REMEMBERED_COVERED_BY}", "bob")]
    # Still changeable: the answer applies again if the show leaves the playlist.
    assert rows[0].changeable


def test_a_show_no_playlist_covers_is_not_marked(memory):
    memory.remember("show:tvdb:100", ("bob",))
    kodi = PlaylistKodi(members=({"id": 42, "type": "tvshow"},))
    library = remembered.library_shows(kodi)
    covers = remembered.covering_playlists(kodi, [ANNA_LISTED, BOB], library)
    rows = remembered.build_rows(memory.entries(), library, [ANNA_LISTED, BOB], kodi, covers)
    assert [(row.name, row.tag, row.detail) for row in rows] == [("Alpha", "", "bob")]


def test_an_unreadable_playlist_marks_nothing(memory):
    kodi = PlaylistKodi()
    del kodi.files["special://profile/playlists/video/Anna TV.xsp"]
    assert remembered.covering_playlists(kodi, [ANNA_LISTED], remembered.library_shows(kodi)) == {}


def test_without_a_library_nothing_is_marked():
    assert remembered.covering_playlists(PlaylistKodi(), [ANNA_LISTED], None) == {}


def test_the_screen_marks_covered_shows(memory):
    memory.remember("show:tvdb:100", ("bob",))
    kodi = PlaylistKodi()
    remembered.run(kodi, memory, [ANNA_LISTED, BOB])
    assert kodi.list_window_calls[0][0].rows[0].tag == f"#{REMEMBERED_COVERED_BY}"


def test_a_failed_bulk_write_is_not_logged_as_forgotten(memory, monkeypatch):
    from resources.lib import log as logmod

    captured: list[str] = []
    logmod.configure(log_dir=None, debug=False, sink=lambda msg, level: captured.append(msg))
    memory.remember("show:tvdb:100", ("anna",))
    monkeypatch.setattr(memory, "forget_many", lambda keys: False)
    remembered.run(ScriptedKodi(lists=[_bulk("show:tvdb:100")], confirms=[True]), memory, VIEWERS)
    assert not any("config.remembered_forgot_all" in line for line in captured)


# --- the panel ------------------------------------------------------------------


def _lines(memory, kodi=None, viewers=VIEWERS, covers=None):
    kodi = kodi or ScriptedKodi()
    rows = remembered.build_rows(memory.entries(), remembered.library_shows(kodi), viewers, kodi, covers or {})
    return {row.key: row for row in rows}


def test_the_panel_names_the_year_and_the_answer(memory):
    memory.remember("show:tvdb:100", ("anna", "bob"))
    row = _lines(memory)["show:tvdb:100"]
    assert row.lines == (
        PanelLine("2008", heading=True),
        PanelLine(f"#{REMEMBERED_ANSWER}", heading=True),
        PanelLine("anna, bob"),
    )
    assert not row.warn


def test_an_answer_for_nobody_configured_reads_will_ask_again(memory):
    memory.remember("show:tvdb:100", ("carol",))
    assert _lines(memory)["show:tvdb:100"].lines[-1] == PanelLine(f"#{REMEMBERED_WILL_ASK}")


class Worded(ScriptedKodi):
    TEXTS = {REMEMBERED_PLAYLIST_OF: "%s's playlist '%s'", REMEMBERED_POINTS_AT: "now points at %s"}

    def localised(self, string_id: int) -> str:
        return self.TEXTS.get(string_id, super().localised(string_id))


def test_a_covered_show_names_each_viewer_and_playlist(memory):
    memory.remember("show:tvdb:100", ("bob",))
    covers = {"show:tvdb:100": (("anna", "Anna TV"), ("bob", "Shared"))}
    row = _lines(memory, Worded(), covers=covers)["show:tvdb:100"]
    assert row.lines[-3:] == (
        PanelLine(f"#{REMEMBERED_COVERED_BY}", heading=True),
        PanelLine("anna's playlist 'Anna TV'"),
        PanelLine("bob's playlist 'Shared'"),
    )
    assert not row.warn  # nothing is wrong: the playlist takes precedence


def test_many_covering_playlists_are_cut_with_and_n_more(memory):
    memory.remember("show:tvdb:100", ("bob",))
    covers = {"show:tvdb:100": tuple(("anna", f"List {n}") for n in range(12))}
    row = _lines(memory, Worded(), covers=covers)["show:tvdb:100"]
    props = slot_properties(row.lines, "and %s more", remembered.REMEMBERED_SLOTS)
    assert props[f"slot{remembered.REMEMBERED_SLOTS}"] == "and 7 more"  # year, Answer, bob, Covered by, 5 of 12


def test_a_show_gone_from_the_library_says_so_and_warns(memory):
    memory.remember("show:tvdb:999", ("anna",), title="Gone Show", year=2001)
    row = _lines(memory)["show:tvdb:999"]
    assert row.lines[-2:] == (
        PanelLine(f"#{REMEMBERED_NOT_IN_LIBRARY_HEAD}", heading=True),
        PanelLine(f"#{REMEMBERED_GONE}", warn=True),
    )
    assert row.warn and row.lines[0] == PanelLine("2001", heading=True)


def test_a_not_stable_answer_names_the_show_its_id_now_points_at(memory):
    memory.remember("tvshow:42", ("anna",), title="Old Name", year=2019)
    row = _lines(memory, Worded())["tvshow:42"]
    assert row.lines[-2:] == (
        PanelLine(f"#{REMEMBERED_NOT_STABLE_HEAD}", heading=True),
        PanelLine("now points at No Ids", warn=True),
    )
    assert row.warn and row.lines[0] == PanelLine("2019", heading=True), "the answer's year, not the other show's"


def test_a_library_id_answer_whose_show_is_gone_is_not_in_library_not_unstable(memory):
    """Not stable means the id now holds another show or cannot be checked; an id with
    nothing at it is a show that left the library."""
    memory.remember("tvshow:7", ("bob",), title="Lost", year=2010)
    row = _lines(memory)["tvshow:7"]
    assert row.lines[-1] == PanelLine(f"#{REMEMBERED_GONE}", warn=True) and row.changeable


def test_a_not_stable_answer_without_a_title_says_it_cannot_be_checked(memory):
    memory.remember("tvshow:7", ("bob",))
    row = _lines(memory)["tvshow:7"]
    assert row.lines[-1] == PanelLine(f"#{REMEMBERED_NO_TITLE}", warn=True) and not row.changeable


def test_an_unreadable_library_judges_nothing(memory):
    memory.remember("show:tvdb:100", ("anna",), title="Alpha", year=2008)

    def boom(params):
        raise RuntimeError("rpc down")

    kodi = ScriptedKodi()
    kodi.rpc_handlers["VideoLibrary.GetTVShows"] = boom
    row = _lines(memory, kodi)["show:tvdb:100"]
    assert not row.warn and all(not line.warn for line in row.lines)


def test_a_rows_panel_and_flags_ride_on_its_list_row(memory):
    memory.remember("tvshow:7", ("bob",))
    row = _lines(memory)["tvshow:7"]
    props = dict(remembered._list_row(row, "and %s more").properties)
    assert props["warn"] == "true" and props["changeable"] == ""
    assert props["slot1_head"] == f"#{REMEMBERED_ANSWER}"
