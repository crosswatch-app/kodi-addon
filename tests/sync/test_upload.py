import gzip
import json
import os
from typing import Any

import pytest

from resources.lib.sync import upload
from resources.lib.sync.upload import UploadRow, UploadTooLarge, deltas, full_parts

SHOWS = {7: {"tvshowid": 7, "uniqueid": {"tvdb": "81189"}, "title": "Breaking Bad", "year": 2008}}
STATE = {"playcount": 0, "lastplayed": "", "userrating": 0, "resume": {"position": 0, "total": 0}}


def _movie(n: int, **kw: Any) -> UploadRow:
    return UploadRow(f"m{n}", "movies", {"movieid": n, "file": f"smb://nas/{n}.mkv", **STATE}, **kw)


def _episode(n: int, show: int = 7) -> UploadRow:
    return UploadRow(f"e{n}", "episodes", {"episodeid": n, "tvshowid": show, "season": 1, "episode": n, **STATE})


def _removed(n: int, file: str) -> UploadRow:
    row = {"in_library": False, "file": file, "title": f"Gone {n}", **STATE}
    return UploadRow(f"r{n}", "movies", row, removed=True, together=file)


def _full(rows: list[UploadRow], **kw: Any) -> list[dict[str, Any]]:
    kw.setdefault("max_rows", 10_000)
    parts = full_parts(rows, SHOWS, viewer_id="kv-1", upload_id="u1", upload_seq=5, applied_seq=12,
                       sources=[{"file": "smb://nas/", "label": "Movies"}], **kw)  # fmt: skip
    return [json.loads(p.body) for p in parts]


def test_empty_full_is_one_empty_part() -> None:
    [part] = _full([])
    assert (part["part"], part["parts"], part["movies"], part["episodes"], part["tvshows"]) == (1, 1, [], [], [])
    assert part["sources"] == [{"file": "smb://nas/", "label": "Movies"}]


def test_every_part_has_three_lists_and_sources_only_in_part_one() -> None:
    parts = _full([_movie(n) for n in range(1, 6)] + [_episode(10)], max_rows=2)
    assert len(parts) > 1
    for part in parts:
        assert {"movies", "episodes", "tvshows"} <= set(part)
        assert (part["upload_seq"], part["upload_id"], part["applied_seq"], part["parts"]) == (5, "u1", 12, len(parts))
    assert "sources" in parts[0] and all("sources" not in p for p in parts[1:])
    assert [p["part"] for p in parts] == list(range(1, len(parts) + 1))


def test_parts_within_row_limit_and_show_rows_travel() -> None:
    rows = [_episode(n) for n in range(1, 6)]
    parts = _full(rows, max_rows=2)
    for part in parts:
        assert len(part["episodes"]) <= 2
        assert part["tvshows"] == [SHOWS[7]]


def test_parts_within_byte_limit_after_gzip() -> None:
    rows = [UploadRow(f"m{n}", "movies", {"movieid": n, "title": os.urandom(800).hex(), **STATE}) for n in range(1, 40)]
    raw_parts = full_parts(rows, SHOWS, viewer_id="kv-1", upload_id="u", upload_seq=1, applied_seq=0, sources=[],
                           max_bytes=4000)  # fmt: skip
    assert len(raw_parts) > 1
    assert all(len(gzip.compress(p.body)) <= 4000 for p in raw_parts)
    assert sorted(m["movieid"] for p in raw_parts for m in json.loads(p.body)["movies"]) == list(range(1, 40))


def test_removed_rows_come_first_and_stay_with_their_file() -> None:
    live_on_file = UploadRow(
        "m9", "movies", {"movieid": 9, "file": "smb://nas/x.mkv", **STATE}, together="smb://nas/x.mkv"
    )
    rows = [_movie(1), live_on_file, _removed(2, "smb://nas/x.mkv"), _movie(3)]
    [body] = deltas(rows, SHOWS, viewer_id="kv-1", first_seq=8, applied_seq=3, max_rows=10)
    movies = json.loads(body.body)["movies"]
    assert movies[0]["in_library"] is False
    # Requests of two rows each still keep them together.
    split = [
        json.loads(d.body)["movies"]
        for d in deltas(rows, SHOWS, viewer_id="kv-1", first_seq=8, applied_seq=3, max_rows=2)
    ]
    together = next(m for m in split if any(r.get("in_library") is False for r in m))
    assert {r.get("movieid") for r in together} == {None, 9}


def test_total_counted_like_cw(monkeypatch) -> None:
    monkeypatch.setattr(upload, "MAX_FULL_ROWS", 6)
    # Five episodes in parts of two repeat the show row in each of three parts: 5 + 3 = 8 rows.
    with pytest.raises(UploadTooLarge):
        _full([_episode(n) for n in range(1, 6)], max_rows=2)
    assert len(_full([_episode(n) for n in range(1, 4)], max_rows=3)) == 1


def test_too_many_parts_raises(monkeypatch) -> None:
    monkeypatch.setattr(upload, "MAX_PARTS", 2)
    with pytest.raises(UploadTooLarge):
        _full([_movie(n) for n in range(1, 6)], max_rows=2)


def test_one_item_too_big_for_any_request() -> None:
    huge = UploadRow("m1", "movies", {"movieid": 1, "title": os.urandom(60_000).hex(), **STATE})
    with pytest.raises(UploadTooLarge):
        full_parts(
            [huge], SHOWS, viewer_id="kv-1", upload_id="u", upload_seq=1, applied_seq=0, sources=[], max_bytes=1000
        )


def test_deltas_consecutive_seqs_lists_and_carried() -> None:
    bodies = deltas(
        [_movie(1), _movie(2), _episode(3)], SHOWS, viewer_id="kv-1", first_seq=20, applied_seq=4, max_rows=1
    )
    assert [b.seq for b in bodies] == [20, 21]
    first, second = (json.loads(b.body) for b in bodies)
    assert first["mode"] == "delta" and first["applied_seq"] == 4 and "upload_id" not in first
    assert "episodes" in second and second["tvshows"] == [SHOWS[7]]
    assert bodies[0].carried == {"m1": upload.ALL_GROUPS}
    assert bodies[1].carried == {"m2": upload.ALL_GROUPS, "e3": upload.ALL_GROUPS}
    assert deltas([], SHOWS, viewer_id="kv-1", first_seq=1, applied_seq=0) == []


def test_full_carries_every_group_of_every_row() -> None:
    parts = full_parts([_movie(1), _removed(2, "f")], SHOWS, viewer_id="kv-1", upload_id="u", upload_seq=1, applied_seq=0,
                       sources=[])  # fmt: skip
    assert parts[0].carried == {"m1": upload.ALL_GROUPS, "r2": upload.ALL_GROUPS}
    assert parts[0].rows == 2


def test_episodes_grouped_by_show_so_show_rows_repeat_least() -> None:
    shows = {s: {"tvshowid": s, "uniqueid": {"tvdb": str(s)}, "title": f"S{s}", "year": 2000} for s in (1, 2)}
    # Kodi's id order mixes the two shows.
    rows = [_episode(n, show=1 if n % 2 else 2) for n in range(1, 9)]
    parts = full_parts(
        rows, shows, viewer_id="kv-1", upload_id="u", upload_seq=1, applied_seq=0, sources=[], max_rows=4
    )
    assert [len(json.loads(p.body)["tvshows"]) for p in parts] == [1, 1]


def test_requests_fit_with_envelope_and_sources() -> None:
    sources = [{"file": os.urandom(400).hex(), "label": "x"} for _ in range(5)]
    rows = [UploadRow(f"m{n}", "movies", {"movieid": n, "title": os.urandom(300).hex(), **STATE}) for n in range(1, 30)]
    parts = full_parts(rows, SHOWS, viewer_id="kv-1", upload_id="u", upload_seq=1, applied_seq=0, sources=sources,
                       max_bytes=6000)  # fmt: skip
    assert all(len(gzip.compress(p.body)) <= 6000 for p in parts)


def test_removed_episode_carries_no_show_row() -> None:
    gone = UploadRow("e9", "episodes", {"in_library": False, "show": {"title": "Old"}, **STATE}, removed=True)
    [body] = deltas([gone], SHOWS, viewer_id="kv-1", first_seq=1, applied_seq=0)
    sent = json.loads(body.body)
    assert "tvshows" not in sent and "movies" not in sent and len(sent["episodes"]) == 1


def test_a_live_episode_without_its_show_row_is_an_error() -> None:
    with pytest.raises(ValueError):
        full_parts(
            [_episode(1, show=99)], SHOWS, viewer_id="kv-1", upload_id="u", upload_seq=1, applied_seq=0, sources=[]
        )


def test_rows_that_must_travel_together_over_the_row_limit() -> None:
    together = [UploadRow(f"m{n}", "movies", {"movieid": n, **STATE}, together="f") for n in range(1, 4)]
    with pytest.raises(UploadTooLarge):
        deltas(together, SHOWS, viewer_id="kv-1", first_seq=1, applied_seq=0, max_rows=2)


def test_removed_rows_go_in_earlier_requests_than_live_ones() -> None:
    rows = [_movie(1), _removed(2, "a"), _movie(3), _removed(4, "b")]
    bodies = [
        json.loads(b.body)["movies"]
        for b in deltas(rows, SHOWS, viewer_id="kv-1", first_seq=1, applied_seq=0, max_rows=1)
    ]
    flags = [m.get("in_library") is False for batch in bodies for m in batch]
    assert flags == sorted(flags, reverse=True)


def test_fresh_full_says_so_on_its_first_part():
    rows = [UploadRow(f"k{n}", "movies", {"movieid": n, "uniqueid": {"tmdb": str(n)}, "title": "t", "file": f"/m/{n}"})
            for n in range(1, 4)]  # fmt: skip
    parts = full_parts(rows, {}, viewer_id="v", upload_id="u", upload_seq=1, applied_seq=0, sources=[], max_rows=1,
                       fresh=True)  # fmt: skip
    bodies = [json.loads(p.body) for p in parts]
    assert bodies[0]["fresh"] is True and all("fresh" not in b for b in bodies[1:])
    plain = full_parts(rows, {}, viewer_id="v", upload_id="u", upload_seq=1, applied_seq=0, sources=[])
    assert "fresh" not in json.loads(plain[0].body)
