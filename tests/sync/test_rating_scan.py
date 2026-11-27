from __future__ import annotations

from typing import Any

import pytest

from resources.lib.sync import items
from resources.lib.sync import rating_scan as rating_scan_mod
from resources.lib.sync.clock import kodi_to_utc
from resources.lib.sync.constants import MASS_RATING_CHANGE
from resources.lib.sync.items import Library, LibraryRow, RowKey
from resources.lib.sync.rating_scan import ScanResult, rating_scan
from resources.lib.sync.record import Seen
from resources.lib.sync.writer import read_row
from tests.fakes import FakeKodi
from tests.sync.fake_library import FakeLibrary

ADDED = "2026-01-01 10:00:00"


class Rig:
    def __init__(self) -> None:
        self.lib = FakeLibrary()
        self.kodi = FakeKodi(rpc_handlers=self.lib.handlers())
        self.rows: list[LibraryRow] = []
        self.seen: dict[RowKey, Seen] = {}

    def movie(self, kodi_id: int, ids: dict[str, str], rating: int = 0, *, seen: bool = True) -> RowKey:
        file = f"/m/{kodi_id}.mkv"
        raw = self.lib.add_movie(kodi_id, title="Film", year=2001, uniqueid=ids, file=file, userrating=rating)
        key = RowKey("L1", "movies", kodi_id, file)
        # In UTC, as read_library gives the last full read's rows.
        full = {**raw, "dateadded": kodi_to_utc(str(raw["dateadded"]))}
        self.rows.append(LibraryRow(key, tuple(items.tokens("movies", full)), full, None))
        if seen:
            self.seen[key] = Seen(playcount=1, userrating=rating, dateadded=kodi_to_utc(ADDED))
        return key

    def rate(self, key: RowKey, value: int) -> None:
        self.lib.row("movies", key.kodi_id)["userrating"] = value

    def scan(self, in_flight: Any = None, held: Any = None, may_continue=lambda: True):
        library = Library("L1", list(self.rows), [], {}, [], True)
        return rating_scan(
            self.kodi,
            library,
            self.seen,
            in_flight or {},
            lambda key: read_row(self.kodi, key, {}),
            held=held or {},
            may_continue=may_continue,
        )


def test_rating_scan_reads_few_properties():
    rig = Rig()
    rig.movie(1, {"tmdb": "1"})
    rig.scan()
    lists = [p for m, p in rig.kodi.calls if m in ("VideoLibrary.GetMovies", "VideoLibrary.GetEpisodes")]
    assert lists and all(p["properties"] == ["file", "userrating", "dateadded"] for p in lists)


def test_rating_scan_finds_and_rereads_changed_row():
    rig = Rig()
    key = rig.movie(1, {"tmdb": "1"})
    rig.movie(2, {"tmdb": "2"}, rating=4)
    rig.rate(key, 7)
    out = rig.scan()
    [found] = out.found
    assert found.row.key == key and found.value == 7 and found.copies == () and found.rated == (key,)
    assert ("VideoLibrary.GetMovieDetails", {"movieid": 1, "properties": read_props()}) in rig.kodi.calls
    assert out.seen == {key: Seen(playcount=1, userrating=7, dateadded=kodi_to_utc(ADDED))}


def _rating(out: ScanResult, key: RowKey) -> int:
    seen = out.seen[key]
    assert seen is not None
    return seen.userrating


def read_props() -> list[str]:
    return ["uniqueid", "title", "year", "file", "playcount", "lastplayed", "userrating", "resume", "dateadded"]


def test_rating_scan_in_flight_write_is_own():
    rig = Rig()
    key = rig.movie(1, {"tmdb": "1"})
    rig.rate(key, 7)
    out = rig.scan(in_flight={key: {"userrating": 7}})
    assert out.found == [] and _rating(out, key) == 7
    # Another value than the one in flight is a person's.
    rig.rate(key, 9)
    assert [f.value for f in rig.scan(in_flight={key: {"userrating": 7}}).found] == [9]


def test_rating_scan_rereads_changed_row():
    """The re-read is what counts: a rating set back between the list and the re-read sends nothing."""
    rig = Rig()
    key = rig.movie(1, {"tmdb": "1"}, rating=5)
    rig.rate(key, 7)
    real = rig.kodi.rpc_handlers["VideoLibrary.GetMovieDetails"]

    def back(params):
        rig.rate(key, 5)
        return real(params)

    rig.kodi.rpc_handlers["VideoLibrary.GetMovieDetails"] = back
    out = rig.scan()
    assert out.found == [] and _rating(out, key) == 5


def test_rating_on_one_copy_counts_and_spreads():
    rig = Rig()
    one = rig.movie(1, {"tmdb": "1"}, rating=3)
    two = rig.movie(2, {"tmdb": "1"}, rating=3)
    rig.rate(two, 8)
    [found] = rig.scan().found
    assert found.row.key == two and found.value == 8 and found.copies == (one,)


def test_rating_copies_changed_differently_send_nothing():
    rig = Rig()
    one = rig.movie(1, {"tmdb": "1"}, rating=3)
    two = rig.movie(2, {"tmdb": "1"}, rating=3)
    rig.rate(one, 6)
    rig.rate(two, 8)
    out = rig.scan()
    assert out.found == []
    assert (_rating(out, one), _rating(out, two)) == (6, 8)


def test_rating_copies_changed_to_the_same_value_count_once():
    rig = Rig()
    one = rig.movie(1, {"tmdb": "1"})
    two = rig.movie(2, {"tmdb": "1"})
    rig.rate(one, 6)
    rig.rate(two, 6)
    [found] = rig.scan().found
    assert found.value == 6 and found.copies == () and set(found.rated) == {one, two}


def test_cleared_rating_is_found_as_zero():
    rig = Rig()
    key = rig.movie(1, {"tmdb": "1"}, rating=7)
    rig.rate(key, 0)
    assert [f.value for f in rig.scan().found] == [0]


def test_seen_dropped_for_rows_gone_from_a_complete_read():
    rig = Rig()
    rig.movie(1, {"tmdb": "1"})
    gone = RowKey("L1", "movies", 9, "/m/9.mkv")
    rig.seen[gone] = Seen()
    out = rig.scan()
    assert out.complete and out.seen == {gone: None}


def test_stopped_scan_changes_nothing():
    rig = Rig()
    key = rig.movie(1, {"tmdb": "1"})
    rig.rate(key, 7)
    out = rig.scan(may_continue=lambda: False)
    assert not out.complete and out.found == [] and out.seen == {}


def test_incomplete_read_keeps_seen_of_rows_it_missed(monkeypatch):
    """A list whose total moved while paging (a scan adding rows) isn't complete: rows it
    didn't see may just be on a page it missed."""
    monkeypatch.setattr(rating_scan_mod, "_PAGE", 1)
    rig = Rig()
    rig.movie(1, {"tmdb": "1"})
    rig.movie(2, {"tmdb": "2"})
    gone = RowKey("L1", "movies", 9, "/m/9.mkv")
    rig.seen[gone] = Seen()

    def grow(method: str, start: int) -> None:
        if method == "VideoLibrary.GetMovies" and start == 1 and len(rig.lib.movies) == 2:
            rig.lib.add_movie(3, title="New", file="/m/3.mkv")

    rig.lib.before_page = grow
    out = rig.scan()
    assert not out.complete and gone not in out.seen


def test_mass_rating_change_asks_first():
    rig = Rig()
    keys = [rig.movie(n, {"tmdb": str(n)}) for n in range(1, MASS_RATING_CHANGE + 2)]
    for key in keys:
        rig.rate(key, 5)
    out = rig.scan()
    assert out.found == [] and len(out.held) == MASS_RATING_CHANGE + 1
    assert out.seen == {}


def test_mass_rating_set_frozen():
    """Held rows are skipped until answered; a later change elsewhere is handled normally,
    and a held row a person rates again leaves the held set."""
    rig = Rig()
    held = rig.movie(1, {"tmdb": "1"})
    again = rig.movie(2, {"tmdb": "2"})
    other = rig.movie(3, {"tmdb": "3"})
    rig.rate(held, 5)
    rig.rate(again, 9)
    rig.rate(other, 4)
    out = rig.scan(held={held: 5, again: 5})
    assert out.released == {again}
    assert sorted(f.row.key.kodi_id for f in out.found) == [2, 3]
    assert held not in out.seen


@pytest.mark.parametrize("value", [0, 7])
def test_unchanged_rows_find_nothing(value):
    rig = Rig()
    rig.movie(1, {"tmdb": "1"}, rating=value)
    out = rig.scan()
    assert out.found == [] and out.seen == {} and out.held == []


def test_rating_scan_new_or_readded_row_noted_not_sent():
    rig = Rig()
    new = rig.movie(1, {"tmdb": "1"}, seen=False)
    readded = rig.movie(2, {"tmdb": "2"})
    rig.rate(new, 7)
    rig.rate(readded, 8)
    rig.lib.row("movies", 2)["dateadded"] = "2026-11-20 09:00:00"
    out = rig.scan()
    assert out.found == []
    # Noted from the last full read, with the rating Kodi has now; a later rating counts.
    noted = out.seen[new]
    assert noted is not None and noted.userrating == 7 and noted.playcount == 0
    # Added again by Kodi: the next full read's catch-up gets the row, without a Seen.
    assert out.seen[readded] is None
    rig.seen[new] = noted
    rig.rate(new, 9)
    assert [f.value for f in rig.scan().found] == [9]


def test_row_not_in_the_last_full_read_is_not_noted():
    rig = Rig()
    rig.movie(1, {"tmdb": "1"})
    rig.lib.add_movie(2, title="Later", file="/m/2.mkv", userrating=6)
    assert RowKey("L1", "movies", 2, "/m/2.mkv") not in rig.scan().seen


def test_rating_with_an_unknown_copy_is_tried_again():
    rig = Rig()
    one = rig.movie(1, {"tmdb": "1"})
    two = rig.movie(2, {"tmdb": "1"}, seen=False)
    rig.rate(one, 6)
    out = rig.scan()
    assert out.found == [] and one not in out.seen
    noted = out.seen[two]
    assert noted is not None
    rig.seen[two] = noted
    [found] = rig.scan().found
    assert found.row.key == one and found.copies == (two,)


def test_failed_reread_is_tried_again():
    rig = Rig()
    key = rig.movie(1, {"tmdb": "1"})
    rig.rate(key, 7)
    real = rig.kodi.rpc_handlers["VideoLibrary.GetMovieDetails"]

    def fails(params):
        raise RuntimeError("busy")

    rig.kodi.rpc_handlers["VideoLibrary.GetMovieDetails"] = fails
    out = rig.scan()
    assert out.found == [] and key not in out.seen
    rig.kodi.rpc_handlers["VideoLibrary.GetMovieDetails"] = real
    assert [f.value for f in rig.scan().found] == [7]


def test_own_write_on_one_copy_does_not_hide_a_person_on_the_other():
    rig = Rig()
    one = rig.movie(1, {"tmdb": "1"}, rating=3)
    two = rig.movie(2, {"tmdb": "1"}, rating=3)
    rig.rate(one, 5)
    rig.rate(two, 8)
    out = rig.scan(in_flight={one: {"userrating": 5}})
    [found] = out.found
    assert found.row.key == two and found.value == 8 and found.copies == (one,)
    assert _rating(out, one) == 5


def test_rereads_stop_when_told():
    rig = Rig()
    key = rig.movie(1, {"tmdb": "1"})
    rig.rate(key, 7)
    calls = {"n": 0}

    def until_rereads() -> bool:
        calls["n"] += 1
        # Paging the two lists asks twice per list; the re-read is the next ask.
        return calls["n"] <= 2

    out = rig.scan(may_continue=until_rereads)
    assert not out.complete and out.found == [] and out.seen == {}
