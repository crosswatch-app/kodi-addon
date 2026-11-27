"""The record merges entries as CrossWatch does.

data/cw_merge_wins.json holds CrossWatch's own _merge_wins answer (dev build
sha256:59d8cfbb...) for every combination of watched, explicit and event or src it was run on.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from resources.lib.sync.record import Entry, History, ItemState, Rating, Resume, _merge_wins, merge

CASES = json.loads((Path(__file__).parent / "data" / "cw_merge_wins.json").read_text())


def _as_state(group: str, raw: dict[str, Any]) -> History | Rating | Resume:
    if group == "history":
        return History(
            watched=raw["watched"], explicit=raw["explicit"], event=raw.get("event", 0), src=raw.get("src", 0)
        )
    return (Rating if group == "ratings" else Resume)(src=raw["src"])


@pytest.mark.parametrize(("group", "theirs", "mine", "wins"), CASES)
def test_merge_wins_matches_cw(group: Any, theirs: dict[str, Any], mine: dict[str, Any], wins: bool) -> None:
    assert _merge_wins(group, _as_state(group, theirs), _as_state(group, mine)) is wins


def _entry(key: str, state: ItemState, **kw: Any) -> Entry:
    return Entry(key=key, tokens=frozenset({key}), meta={"title": key}, state=state, **kw)


def test_lowest_key_survives_and_tokens_union() -> None:
    merged = merge([_entry("b", ItemState()), _entry("a", ItemState())])
    assert merged.key == "a" and merged.tokens == {"a", "b"}
    assert merged.meta == {"title": "a"}


def test_explicit_unwatch_wins_like_cw() -> None:
    watched = _entry("a", ItemState(history=History(watched=True, playcount=1, event=3, src=3)))
    unwatched = _entry("b", ItemState(history=History(watched=False, explicit=True, event=5, src=5)))
    assert merge([watched, unwatched]).state.history.watched is False
    older_unwatch = _entry("b", ItemState(history=History(watched=False, explicit=True, event=2, src=2)))
    assert merge([watched, older_unwatch]).state.history.watched is True


def test_ratings_and_progress_by_src() -> None:
    a = _entry("a", ItemState(rating=Rating(value=6, src=2), resume=Resume(position=30.0, src=5)))
    b = _entry("b", ItemState(rating=Rating(value=9, src=4), resume=Resume(position=60.0, src=3)))
    merged = merge([a, b])
    assert merged.state.rating.value == 9 and merged.state.resume.position == 30.0


def test_a_target_without_a_group_takes_the_others() -> None:
    a = _entry("a", ItemState())
    b = _entry("b", ItemState(rating=Rating(value=7, src=3)))
    assert merge([a, b]).state.rating.value == 7


def test_merge_ties_broken_like_cw() -> None:
    # Equal src: the target (lowest key) keeps its value, as _merge_wins needs strictly newer.
    a = _entry("a", ItemState(rating=Rating(value=6, src=3)))
    b = _entry("b", ItemState(rating=Rating(value=9, src=3)))
    assert merge([b, a]).state.rating.value == 6


def test_an_unsent_person_rating_beats_an_uploaded_one() -> None:
    a = _entry("a", ItemState(rating=Rating(value=6, src=9)))
    b = _entry("b", ItemState(rating=Rating(value=8, src=2)), unsent={"ratings": "2026-11-19T10:00:00Z"})
    merged = merge([a, b])
    assert merged.state.rating.value == 8 and merged.unsent == {"ratings": "2026-11-19T10:00:00Z"}


def test_an_unsent_unwatch_survives_with_its_flag() -> None:
    a = _entry("a", ItemState(history=History(watched=True, playcount=2, event=9, src=9)))
    unwatch = History(watched=False, explicit=True, unwatched=True, event=1, src=1)
    b = _entry("b", ItemState(history=unwatch), unsent={"history": "2026-11-19T10:00:00Z"})
    assert merge([a, b]).state.history == unwatch


def test_the_newest_unsent_change_wins_with_its_own_upload() -> None:
    a = _entry("a", ItemState(rating=Rating(value=9)), unsent={"ratings": "2026-11-19T12:00:00Z"})
    b = _entry("b", ItemState(rating=Rating(value=5)), unsent={"ratings": "2026-11-18T12:00:00Z"},
               unsent_upload={"ratings": 7})  # fmt: skip
    merged = merge([a, b])
    assert merged.state.rating.value == 9
    # Upload 7 carried b's older change, not a's 9, so it must not settle the merged group.
    assert merged.unsent_upload == {}


def test_an_applied_cw_change_beats_uploaded_states_by_seq() -> None:
    a = _entry("a", ItemState(history=History(watched=True, playcount=1, event=9, src=9)))
    b = _entry(
        "b", ItemState(history=History(watched=False, explicit=True, event=3, src=3)), applied_change={"history": 10}
    )
    c = _entry(
        "c", ItemState(history=History(watched=True, playcount=4, event=2, src=2)), applied_change={"history": 6}
    )
    merged = merge([a, b, c])
    assert merged.state.history.watched is False and merged.applied_change == {"history": 10}


def test_an_unsent_person_change_beats_an_applied_cw_change_on_another_copy() -> None:
    a = _entry("a", ItemState(rating=Rating(value=8, src=2)), unsent={"ratings": "2026-11-19T10:00:00Z"})
    b = _entry("b", ItemState(rating=Rating(value=3, src=1)), applied_change={"ratings": 10})
    assert merge([a, b]).state.rating.value == 8


def test_a_carried_person_change_answered_by_a_later_cw_change_loses() -> None:
    # Upload 7 carried the 8; the tracker then set 3, queued after CrossWatch saw upload 7.
    a = _entry("a", ItemState(rating=Rating(value=8, src=7)), unsent={"ratings": "2026-11-19T10:00:00Z"},
               unsent_upload={"ratings": 7})  # fmt: skip
    b = _entry("b", ItemState(rating=Rating(value=3, src=1)), applied_change={"ratings": 10}, applied_after={"ratings": 7})
    merged = merge([a, b])
    assert merged.state.rating.value == 3 and "ratings" not in merged.unsent
    assert merged.applied_change == {"ratings": 10} and merged.applied_after == {"ratings": 7}


def test_a_cw_change_queued_before_the_carrying_upload_loses() -> None:
    # The 3 was queued when CrossWatch had seen upload 6, before the upload that carried the 8.
    a = _entry("a", ItemState(rating=Rating(value=8, src=7)), unsent={"ratings": "2026-11-19T10:00:00Z"},
               unsent_upload={"ratings": 7})  # fmt: skip
    b = _entry("b", ItemState(rating=Rating(value=3, src=1)), applied_change={"ratings": 10}, applied_after={"ratings": 6})
    merged = merge([a, b])
    assert merged.state.rating.value == 8 and merged.unsent == {"ratings": "2026-11-19T10:00:00Z"}


def test_equal_unsent_times_keep_the_lowest_key() -> None:
    at = "2026-11-19T10:00:00Z"
    a = _entry("a", ItemState(rating=Rating(value=6)), unsent={"ratings": at})
    b = _entry("b", ItemState(rating=Rating(value=9)), unsent={"ratings": at})
    assert merge([b, a]).state.rating.value == 6


def test_from_cw_kept_if_any_copy_had_it() -> None:
    a = _entry("a", ItemState(rating=Rating(value=0, from_cw=True, src=2)), origin="seed")
    b = _entry("b", ItemState(rating=Rating(value=7, src=5)), origin="seed", seed_rating=7)
    merged = merge([a, b])
    assert merged.state.rating.from_cw and merged.state.rating.value == 7


def test_meta_keys_held_only_by_another_entry_are_kept() -> None:
    a = Entry(key="a", tokens=frozenset({"a"}), meta={"title": "A"}, state=ItemState())
    b = Entry(key="b", tokens=frozenset({"b"}), meta={"title": "B", "year": 2001}, state=ItemState())
    assert merge([a, b]).meta == {"title": "A", "year": 2001}


def test_merge_unions_bookkeeping() -> None:
    a = _entry("a", ItemState(), unsent={"ratings": "2026-01-01T00:00:00Z"}, unsent_upload={"ratings": 3},
               origin="seed", seed_rating=6, change_count=4)  # fmt: skip
    b = _entry("b", ItemState(), unsent={"ratings": "2026-02-01T00:00:00Z", "history": "2026-01-05T00:00:00Z"},
               unsent_upload={"ratings": 7}, touched=True, origin="seed", seed_rating=8, change_count=2)  # fmt: skip
    merged = merge([a, b])
    assert merged.unsent == {"ratings": "2026-02-01T00:00:00Z", "history": "2026-01-05T00:00:00Z"}
    # b's change is the newer one, so its own upload stays with it.
    assert merged.unsent_upload == {"ratings": 7}
    assert merged.touched
    assert (merged.origin, merged.seed_rating, merged.change_count) == ("seed", 8, 5)


def test_origin_seed_only_when_all_are() -> None:
    a = _entry("a", ItemState(), origin="seed", seed_rating=6)
    b = _entry("b", ItemState(), origin="cw")
    merged = merge([a, b])
    assert (merged.origin, merged.seed_rating) == ("cw", 0)
    assert merge([a, _entry("c", ItemState(), origin="person")]).origin == "person"


def test_merge_of_nothing_is_an_error() -> None:
    with pytest.raises(ValueError):
        merge([])
