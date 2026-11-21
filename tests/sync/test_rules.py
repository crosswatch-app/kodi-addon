from typing import Any

import pytest

from resources.lib.sync.record import History, ItemState, Rating, Resume, Seen
from resources.lib.sync.rules import kodi_fields
from resources.lib.sync.settings import SyncSettings

NOW = "2026-11-19T12:00:00Z"
OFF = SyncSettings()
REMOVALS = SyncSettings(send_removals=True)
ANYONE = SyncSettings(shared_rule="anyone")


def _watched(count: int = 1, at: str = "2026-11-10T20:00:00Z") -> ItemState:
    return ItemState(history=History(watched=True, playcount=count, lastplayed=at))


UNWATCHED = ItemState()
EXPLICIT = ItemState(history=History(watched=False, explicit=True))


def _fields(group: Any, owners: list[ItemState], kodi: Seen, **kw: Any) -> dict[str, Any]:
    kw.setdefault("seen", kodi)
    kw.setdefault("siblings_watched", False)
    kw.setdefault("settings", OFF)
    return kodi_fields(group, owners=owners, kodi=kodi, now_utc=NOW, **kw)


def test_history_raise_on_rule_met() -> None:
    assert _fields("history", [_watched(2)], Seen()) == {"playcount": 2, "lastplayed": "2026-11-10T20:00:00Z"}


def test_shared_rule_everyone_or_anyone() -> None:
    owners = [_watched(3), UNWATCHED]
    assert _fields("history", owners, Seen()) == {}
    assert _fields("history", owners, Seen(), settings=ANYONE) == {"playcount": 3, "lastplayed": "2026-11-10T20:00:00Z"}
    both = [_watched(3, "2026-11-01T10:00:00Z"), _watched(1, "2026-11-12T10:00:00Z")]
    assert _fields("history", both, Seen()) == {"playcount": 1, "lastplayed": "2026-11-12T10:00:00Z"}


def test_catch_up_raise_for_returning_row() -> None:
    # A rescanned row lost its count: raised back from the record, seen or not.
    assert _fields("history", [_watched(2)], Seen(), seen=None) == {
        "playcount": 2,
        "lastplayed": "2026-11-10T20:00:00Z",
    }


def test_playcount_and_lastplayed_never_move_back() -> None:
    kodi = Seen(playcount=5, lastplayed="2026-11-15T09:00:00Z")
    assert _fields("history", [_watched(2, "2026-11-10T20:00:00Z")], kodi) == {}
    newer = _fields("history", [_watched(2, "2026-11-16T20:00:00Z")], kodi)
    assert newer == {"playcount": 5, "lastplayed": "2026-11-16T20:00:00Z"}


def test_dates_compared_as_time_across_offsets() -> None:
    # 22:30 at +02:00 is 20:30 UTC, later than the owner's 20:00: nothing to raise.
    later = Seen(playcount=1, lastplayed="2026-11-10T22:30:00+02:00")
    assert _fields("history", [_watched(1, "2026-11-10T20:00:00Z")], later) == {}
    # 21:30 at +02:00 is 19:30 UTC, earlier: the owner's date goes up.
    earlier = Seen(playcount=1, lastplayed="2026-11-10T21:30:00+02:00")
    assert _fields("history", [_watched(1, "2026-11-10T20:00:00Z")], earlier) == {
        "playcount": 1,
        "lastplayed": "2026-11-10T20:00:00Z",
    }


def test_empty_lastplayed_never_sent() -> None:
    assert _fields("history", [_watched(1, "")], Seen()) == {"playcount": 1, "lastplayed": NOW}


def test_never_lowers_without_explicit_unwatch_and_setting() -> None:
    kodi = Seen(playcount=2, lastplayed="2026-11-10T20:00:00Z")
    assert _fields("history", [UNWATCHED], kodi, settings=REMOVALS) == {}
    assert _fields("history", [EXPLICIT], kodi) == {}
    assert _fields("history", [EXPLICIT], kodi, settings=REMOVALS) == {"playcount": 0}


def test_shared_unwatch_only_when_rule_not_met() -> None:
    kodi = Seen(playcount=1, lastplayed="2026-11-10T20:00:00Z")
    assert _fields("history", [EXPLICIT, _watched()], kodi, settings=REMOVALS) == {"playcount": 0}
    anyone = SyncSettings(send_removals=True, shared_rule="anyone")
    assert _fields("history", [EXPLICIT, _watched()], kodi, settings=anyone) == {}


def test_multi_episode_sibling_blocks_lowering() -> None:
    assert _fields("history", [EXPLICIT], Seen(playcount=1), settings=REMOVALS, siblings_watched=True) == {}


def test_rating_change_writes_only_rating() -> None:
    owner = ItemState(history=History(watched=True, playcount=3), rating=Rating(value=8))
    assert _fields("ratings", [owner], Seen()) == {"userrating": 8}


def test_shared_rating_needs_agreement() -> None:
    eight, seven = ItemState(rating=Rating(value=8)), ItemState(rating=Rating(value=7))
    assert _fields("ratings", [eight, seven], Seen()) == {}
    assert _fields("ratings", [eight, eight], Seen()) == {"userrating": 8}


def test_rating_removal_only_explicit_with_setting() -> None:
    removed = ItemState(rating=Rating(value=0, from_cw=True, removed=True))
    kodi = Seen(userrating=6)
    assert _fields("ratings", [removed], kodi) == {}
    assert _fields("ratings", [removed], kodi, settings=REMOVALS) == {"userrating": 0}
    # Never rated by the owner is not a removal.
    assert _fields("ratings", [UNWATCHED], kodi, settings=REMOVALS) == {}


def test_rating_skipped_when_kodi_differs_from_seen() -> None:
    owner = ItemState(rating=Rating(value=8))
    assert _fields("ratings", [owner], Seen(userrating=9), seen=Seen(userrating=6)) == {}


def test_rating_without_seen_only_fills_zero() -> None:
    owner = ItemState(rating=Rating(value=8))
    assert _fields("ratings", [owner], Seen(userrating=0), seen=None) == {"userrating": 8}
    assert _fields("ratings", [owner], Seen(userrating=5), seen=None) == {}


def test_resume_never_on_shared_item() -> None:
    owner = ItemState(resume=Resume(position=600.0, total=5400.0))
    assert _fields("progress", [owner, owner], Seen()) == {}


def test_resume_written_for_single_owner() -> None:
    owner = ItemState(resume=Resume(position=600.0, total=5400.0))
    assert _fields("progress", [owner], Seen()) == {"resume": {"position": 600.0, "total": 5400.0}}


def test_older_cw_resume_does_not_overwrite_newer_local() -> None:
    owner = ItemState(resume=Resume(position=600.0, total=5400.0))
    played_after = Seen(lastplayed="2026-11-19T11:00:00Z", resume={"position": 3000.0, "total": 5400.0})
    assert _fields("progress", [owner], played_after, fetched_at="2026-11-19T10:00:00Z") == {}
    played_recently = Seen(lastplayed="2026-11-15T10:00:00Z", resume={"position": 3000.0})
    assert _fields("progress", [owner], played_recently, fetched_at="2026-11-19T10:00:00Z") == {}
    played_long_ago = Seen(lastplayed="2026-10-01T10:00:00Z", resume={"position": 3000.0})
    assert _fields("progress", [owner], played_long_ago, fetched_at="2026-11-19T10:00:00Z") == {
        "resume": {"position": 600.0, "total": 5400.0}
    }


def test_resume_cleared_only_after_a_newer_watch() -> None:
    finished = ItemState(history=History(watched=True, playcount=1, lastplayed="2026-11-18T10:00:00Z"))
    kodi = Seen(lastplayed="2026-11-10T10:00:00Z", resume={"position": 1200.0})
    assert _fields("progress", [finished], kodi) == {"resume": {"position": 0, "total": 0}}
    later_here = Seen(lastplayed="2026-11-19T10:00:00Z", resume={"position": 1200.0})
    assert _fields("progress", [finished], later_here) == {}


def test_no_change_gives_empty_fields() -> None:
    owner = ItemState(
        history=History(watched=True, playcount=2, lastplayed="2026-11-10T20:00:00Z"), rating=Rating(value=7)
    )
    kodi = Seen(playcount=2, lastplayed="2026-11-10T20:00:00Z", userrating=7)
    assert _fields("history", [owner], kodi) == {}
    assert _fields("ratings", [owner], kodi) == {}
    assert _fields("progress", [owner], kodi) == {}
    assert (
        kodi_fields("history", owners=[], kodi=kodi, seen=kodi, siblings_watched=False, settings=OFF, now_utc=NOW) == {}
    )


def test_a_person_removal_spreads_like_a_cw_one() -> None:
    by_person = ItemState(rating=Rating(value=0, removed=True))
    assert _fields("ratings", [by_person], Seen(userrating=7), settings=REMOVALS) == {"userrating": 0}
    # Mixed: one owner removed it, the other never rated it.
    assert _fields("ratings", [by_person, UNWATCHED], Seen(userrating=7), settings=REMOVALS) == {}


def test_a_local_unwatch_is_not_raised_back() -> None:
    # A person unwatched it here with removals off; a CrossWatch unwatch for another owner
    # under the anyone rule must not put the watch back.
    kept = ItemState(history=History(watched=True, playcount=2, lastplayed="2026-11-10T20:00:00Z", local_unwatch=True))
    assert _fields("history", [kept, EXPLICIT], Seen(playcount=0), settings=ANYONE) == {}


def test_already_watched_without_a_date_is_left_alone() -> None:
    assert _fields("history", [_watched(1, "")], Seen(playcount=3)) == {}


def test_resume_decided_from_what_was_seen_before_a_history_write() -> None:
    finished = ItemState(history=History(watched=True, playcount=1, lastplayed="2026-11-18T10:00:00Z"))
    seen = Seen(lastplayed="2026-11-10T10:00:00Z", resume={"position": 1200.0})
    # The writer's own history write already moved Kodi's date to the watch's.
    kodi = Seen(playcount=1, lastplayed="2026-11-18T10:00:00Z", resume={"position": 1200.0})
    assert _fields("progress", [finished], kodi, seen=seen) == {"resume": {"position": 0, "total": 0}}


def test_resume_clear_needs_a_watch_not_a_later_play() -> None:
    played = ItemState(history=History(watched=False, lastplayed="2026-11-18T10:00:00Z"))
    kodi = Seen(lastplayed="2026-11-10T10:00:00Z", resume={"position": 1200.0})
    assert _fields("progress", [played], kodi) == {}


def test_resume_at_or_past_the_end_is_not_written() -> None:
    for position in (5400.0, 6000.0):
        owner = ItemState(resume=Resume(position=position, total=5400.0))
        assert _fields("progress", [owner], Seen()) == {}


def test_resume_guard_boundaries() -> None:
    owner = ItemState(resume=Resume(position=600.0, total=5400.0))
    never_played = Seen(resume={"position": 0})
    assert _fields("progress", [owner], never_played, fetched_at="2026-11-19T10:00:00Z") == {
        "resume": {"position": 600.0, "total": 5400.0}
    }
    exactly_a_week = Seen(lastplayed="2026-11-12T10:00:00Z")
    assert _fields("progress", [owner], exactly_a_week, fetched_at="2026-11-19T10:00:00Z") != {}
    same_position = Seen(lastplayed="2026-10-01T10:00:00Z", resume={"position": 600.4})
    assert _fields("progress", [owner], same_position, fetched_at="2026-11-19T10:00:00Z") == {}


def test_a_play_the_add_on_has_not_seen_still_guards_kodis_resume() -> None:
    # A play stopped below the watched threshold moves Kodi's date and bookmark but announces
    # no play count, so what the add-on last saw predates it.
    owner = ItemState(resume=Resume(position=3036.0, total=6060.0))
    seen = Seen(lastplayed="", resume={"position": 0.0})
    kodi = Seen(lastplayed="2026-11-19T09:58:00Z", resume={"position": 3112.0, "total": 6224.0})
    assert _fields("progress", [owner], kodi, seen=seen, fetched_at="2026-11-19T10:00:00Z") == {}


def test_the_add_ons_own_history_write_does_not_guard_a_later_resume() -> None:
    # Watched elsewhere on Monday and written into Kodi, which moved Kodi's date to that watch;
    # a rewatch elsewhere stopped half way comes in on Wednesday.
    owner = ItemState(history=History(watched=True, playcount=1, lastplayed="2026-11-16T20:00:00Z"),
                      resume=Resume(position=1800.0, total=5400.0))  # fmt: skip
    seen = Seen(playcount=0, lastplayed="2026-09-01T20:00:00Z")
    kodi = Seen(playcount=1, lastplayed="2026-11-16T20:00:00Z")
    assert _fields("progress", [owner], kodi, seen=seen, fetched_at="2026-11-18T21:00:00Z") == {
        "resume": {"position": 1800.0, "total": 5400.0}
    }


def test_kodi_has_a_date_the_owners_lack() -> None:
    kodi = Seen(playcount=0, lastplayed="2026-11-01T10:00:00Z")
    assert _fields("history", [_watched(1, "")], kodi) == {"playcount": 1, "lastplayed": "2026-11-01T10:00:00Z"}


def test_unknown_group_is_an_error() -> None:
    with pytest.raises(ValueError):
        _fields("collection", [_watched()], Seen())
