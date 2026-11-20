import time

import pytest

from resources.lib.sync import items
from resources.lib.sync.clock import kodi_to_utc
from resources.lib.sync.constants import OWN_WRITE_TICKS
from resources.lib.sync.items import RowKey, read_library
from resources.lib.sync.record import (
    Batch,
    Entry,
    Group,
    History,
    ItemState,
    PendingWrite,
    Rating,
    RecordStore,
    Resume,
    Seen,
)
from resources.lib.sync.settings import SyncSettings
from resources.lib.sync.writer import (
    OwnWrites,
    Writer,
    announcement_item,
    episode_counts,
    read_row,
    restore_copies,
)
from tests.fakes import FakeKodi
from tests.sync.fake_library import FakeLibrary

SHOW = {"tvshowid": 7, "uniqueid": {"tvdb": "81189"}, "title": "Breaking Bad", "year": 2008}


def _library() -> FakeLibrary:
    lib = FakeLibrary()
    lib.add_show(7, uniqueid={"tvdb": "81189"}, title="Breaking Bad", year=2008)
    lib.add_episode(10, 7, 1, 1, file="smb://nas/bb/s01e01.mkv")
    lib.add_episode(11, 7, 1, 1, file="smb://nas/bb/s01e01-4k.mkv", playcount=3, lastplayed="2026-11-01 20:00:00")
    lib.add_movie(1, uniqueid={"tmdb": "550"}, title="Fight Club", file="smb://u:p@nas/fc.mkv")
    return lib


def _kodi(lib: FakeLibrary) -> FakeKodi:
    return FakeKodi(rpc_handlers=lib.handlers())


# -- OwnWrites ---------------------------------------------------------------------------


def test_echo_match_both_shapes() -> None:
    own = OwnWrites()
    own.note("movie", 1, {"playcount": 2}, "w1")
    assert not own.matches("movie", 1, {"item": {"id": 1, "type": "movie"}, "playcount": 3})
    assert own.matches("movie", 1, {"item": {"id": 1, "type": "movie"}, "playcount": 2})
    # Used up: a second announcement for the same item is not ours.
    assert not own.matches("movie", 1, {"item": {"id": 1, "type": "movie"}, "playcount": 2})
    own.note("episode", 5, {"lastplayed": "x"}, "w2")
    assert own.matches("episode", 5, {"id": 5, "type": "episode"})
    assert announcement_item({"item": {"id": 5, "type": "episode"}}) == ("episode", 5)
    assert announcement_item({"id": 9, "type": "movie"}) == ("movie", 9)
    assert announcement_item({"id": 9, "type": "song"}) is None
    assert announcement_item({"item": {"id": True, "type": "movie"}}) is None


def test_echo_delivered_after_read_back_is_own() -> None:
    own = OwnWrites()
    own.note("movie", 1, {"playcount": 1}, "w1")
    own.committed("w1")
    for _ in range(OWN_WRITE_TICKS - 1):
        own.tick()
    assert own.matches("movie", 1, {"item": {"id": 1, "type": "movie"}, "playcount": 1})


def test_own_write_expires_after_ticks() -> None:
    own = OwnWrites()
    own.note("movie", 1, {"userrating": 8}, "w1")
    for _ in range(OWN_WRITE_TICKS * 2):
        own.tick()
    # Not committed yet: never expires while its read-back is still to come.
    assert own.matches("movie", 1, {"item": {"id": 1, "type": "movie"}})
    own.note("movie", 1, {"userrating": 8}, "w2")
    own.committed("w2")
    for _ in range(OWN_WRITE_TICKS):
        own.tick()
    assert not own.matches("movie", 1, {"item": {"id": 1, "type": "movie"}})


def test_crash_between_write_and_read_back_is_own_after_restart() -> None:
    row = RowKey("lib1", "movies", 1, "smb://nas/fc.mkv")
    moved = {"kind": "movies", "kodi_id": 3, "file": "smb://nas/fc.mkv", "fields": {"playcount": 2}}
    in_flight = PendingWrite(id="w1", row=row, tokens=frozenset({"t"}), group="history", created="t",
                             in_flight={"rows": [moved]})  # fmt: skip
    own = OwnWrites()
    own.seed([in_flight, PendingWrite(id="w2", row=row, tokens=frozenset({"t"}), group="ratings", created="t")])
    # The note is on the row the write went to (id 3 after a move), not the intent's old id.
    assert not own.matches("movie", 1, {"item": {"id": 1, "type": "movie"}, "playcount": 2})
    assert own.matches("movie", 3, {"item": {"id": 3, "type": "movie"}, "playcount": 2})


# -- episode copies ----------------------------------------------------------------------


def test_rating_write_on_copy_keeps_play_count() -> None:
    lib = _library()
    kodi = _kodi(lib)
    before = episode_counts(kodi, 10)
    assert before == (0, "")
    kodi.jsonrpc("VideoLibrary.SetEpisodeDetails", {"episodeid": 10, "userrating": 7})
    # Kodi copied the 4K copy's play count onto this row, silently.
    assert lib.row("episodes", 10)["playcount"] == 3 and lib.announcements == []
    own = OwnWrites()
    assert restore_copies(kodi, own, 10, before, "restore-10")
    assert (lib.row("episodes", 10)["playcount"], lib.row("episodes", 10)["userrating"]) == (0, 7)
    # The restore announces, and its echo is the add-on's own.
    [echo] = lib.announcements
    assert own.matches("episode", 10, echo)


def test_restore_copies_does_nothing_when_unchanged() -> None:
    lib = _library()
    kodi = _kodi(lib)
    assert not restore_copies(kodi, OwnWrites(), 11, episode_counts(kodi, 11), "r")
    assert lib.writes == []


# -- read_rows ---------------------------------------------------------------------------


def test_read_row_episode_tokens_use_the_show() -> None:
    lib = _library()
    row = read_row(_kodi(lib), RowKey("lib1", "episodes", 11, "smb://nas/bb/s01e01-4k.mkv"), {7: SHOW})
    assert row is not None and "episodes|show:tvdb:81189|1|1" in row.tokens
    assert row.raw["lastplayed"].endswith("Z")


def test_announcements_as_kodi_makes_them() -> None:
    lib = _library()
    kodi = _kodi(lib)
    kodi.jsonrpc("VideoLibrary.SetMovieDetails", {"movieid": 1, "userrating": 9})
    kodi.jsonrpc("VideoLibrary.SetMovieDetails", {"movieid": 1, "resume": {"position": 30, "total": 100}})
    assert lib.announcements == []
    kodi.jsonrpc("VideoLibrary.SetMovieDetails", {"movieid": 1, "lastplayed": "2026-11-20 10:00:00"})
    assert lib.announcements == [{"item": {"id": 1, "type": "movie"}}]
    lib.scanning = True
    kodi.jsonrpc("VideoLibrary.SetMovieDetails", {"movieid": 1, "playcount": 1})
    assert lib.announcements[-1] == {"item": {"id": 1, "type": "movie"}, "playcount": 1, "transaction": True}


# -- the writer --------------------------------------------------------------------------


NOW = "2026-11-20T12:00:00Z"


class Rig:
    def __init__(self, tmp_path, lib: FakeLibrary, *, may_write=lambda: True) -> None:
        self.lib = lib
        self.kodi = _kodi(lib)
        self.store = RecordStore(str(tmp_path / "sync.db"))
        self.store.open(create=True)
        self.own = OwnWrites()
        self.sleeps: list[float] = []
        self.between = 0
        self.may_write = may_write
        self.ticks = iter(float(n) for n in range(10_000))

    def library(self):
        library = read_library(self.kodi, "lib1", may_continue=lambda: True, previous_shows={}, to_utc=kodi_to_utc)
        assert library is not None
        return library

    def entry(self, row, state: ItemState) -> None:
        library = self.library()
        found = next(
            r
            for r in [*library.movies, *library.episodes]
            if r.key.kodi_id == row and r.key.kind == ("movies" if row < 10 else "episodes")
        )
        entry = Entry(
            key=items.identity(found.key.kind, found.raw, None), tokens=frozenset(found.tokens), meta={}, state=state
        )
        self.store.commit(Batch(entries={"kv-1": [entry]}))

    def intent(self, wid: str, kind: str, kodi_id: int, group: Group, **kw) -> None:
        library = self.library()
        row = next(r for r in [*library.movies, *library.episodes] if r.key.kodi_id == kodi_id and r.key.kind == kind)
        self.store.commit(
            Batch(
                pending_add=[
                    PendingWrite(id=wid, row=row.key, tokens=frozenset(row.tokens), group=group, created=NOW, **kw)
                ]
            )
        )

    def run(self, settings: SyncSettings | None = None):
        writer_ = Writer(self.kodi, self.store, self.own, self.may_write, lambda: next(self.ticks), self.sleeps.append,
                         settings=settings or SyncSettings(), library=self.library(), owners=lambda r: ("kv-1",),
                         between_batches=self._between, now_utc=lambda: NOW)  # fmt: skip
        return writer_.run()

    def _between(self) -> None:
        self.between += 1


def _movies(*specs: tuple[int, str, dict]) -> FakeLibrary:
    lib = FakeLibrary()
    for kodi_id, file, fields in specs:
        lib.add_movie(
            kodi_id, uniqueid={"tmdb": "550"} if kodi_id < 5 else {"tmdb": str(kodi_id)}, title="M", file=file, **fields
        )
    return lib


RATED = ItemState(rating=Rating(value=8, from_cw=True))
WATCHED = ItemState(history=History(watched=True, playcount=2, lastplayed="2026-11-01T20:00:00Z"))


def test_rating_intent_written_read_back_and_seen(tmp_path) -> None:
    rig = Rig(tmp_path, _movies((1, "smb://nas/a.mkv", {})))
    rig.entry(1, RATED)
    rig.intent("w1", "movies", 1, "ratings")
    report = rig.run()
    assert (report.written, rig.lib.row("movies", 1)["userrating"]) == (1, 8)
    assert rig.store.pending_writes() == []
    seen = rig.store.seen(items.RowKey("lib1", "movies", 1, "smb://nas/a.mkv"))
    assert seen is not None and seen.userrating == 8


def test_pending_write_survives_restart(tmp_path) -> None:
    rig = Rig(tmp_path, _movies((1, "smb://nas/a.mkv", {})), may_write=lambda: False)
    rig.entry(1, RATED)
    rig.intent("w1", "movies", 1, "ratings")
    assert rig.run().deferred == 1
    rig.store.close()
    again = Rig(tmp_path, rig.lib)
    assert [w.id for w in again.store.pending_writes()] == ["w1"]
    assert again.run().written == 1


def test_pending_write_after_playback_never_lowers(tmp_path) -> None:
    rig = Rig(tmp_path, _movies((1, "smb://nas/a.mkv", {"playcount": 5, "lastplayed": "2026-11-19 20:00:00"})))
    rig.entry(1, WATCHED)
    rig.intent("w1", "movies", 1, "history")
    report = rig.run()
    assert (report.written, report.dropped) == (0, 1)
    assert rig.lib.row("movies", 1)["playcount"] == 5 and rig.lib.writes == []


def test_write_dropped_when_rules_give_nothing(tmp_path) -> None:
    rig = Rig(tmp_path, _movies((1, "smb://nas/a.mkv", {"userrating": 8})))
    rig.entry(1, RATED)
    rig.store.commit(Batch(seen={items.RowKey("lib1", "movies", 1, "smb://nas/a.mkv"): Seen(userrating=8)}))
    rig.intent("w1", "movies", 1, "ratings")
    assert rig.run().dropped == 1 and rig.store.pending_writes() == []


def test_write_not_landed_on_reused_id_and_relocated_by_tokens(tmp_path) -> None:
    lib = _movies((1, "smb://nas/a.mkv", {}))
    rig = Rig(tmp_path, lib)
    rig.entry(1, RATED)
    rig.intent("w1", "movies", 1, "ratings")
    # Refresh information: the movie is now id 3 on the same file; id 1 is another film.
    lib.row("movies", 1).update(movieid=3)
    lib.add_movie(1, uniqueid={"tmdb": "999"}, title="Other", file="smb://nas/other.mkv")
    report = rig.run()
    assert report.written == 1
    assert lib.row("movies", 3)["userrating"] == 8 and lib.row("movies", 1)["userrating"] == 0


def test_intent_for_an_item_not_in_the_library_stays(tmp_path) -> None:
    lib = _movies((1, "smb://nas/a.mkv", {}))
    rig = Rig(tmp_path, lib)
    rig.entry(1, RATED)
    rig.intent("w1", "movies", 1, "ratings")
    lib.movies.clear()
    assert rig.run().deferred == 1 and [w.id for w in rig.store.pending_writes()] == ["w1"]


def test_batches_of_five_with_gap(tmp_path) -> None:
    lib = _movies(*[(n, f"smb://nas/{n}.mkv", {}) for n in range(5, 17)])
    rig = Rig(tmp_path, lib)
    for n in range(5, 17):
        library = rig.library()
        row = next(r for r in library.movies if r.key.kodi_id == n)
        rig.store.commit(
            Batch(entries={"kv-1": [Entry(key=f"k{n}", tokens=frozenset(row.tokens), meta={}, state=RATED)]})
        )
        rig.intent(f"w{n:02d}", "movies", n, "ratings")
    report = rig.run()
    assert report.written == 12 and rig.between == 3 and len(rig.sleeps) == 2
    assert all(gap >= 0.1 for gap in rig.sleeps)


def test_stops_when_playback_starts_and_keeps_rest(tmp_path) -> None:
    playing = {"on": False}
    lib = _movies(*[(n, f"smb://nas/{n}.mkv", {}) for n in range(5, 12)])
    rig = Rig(tmp_path, lib, may_write=lambda: not playing["on"])
    for n in range(5, 12):
        row = next(r for r in rig.library().movies if r.key.kodi_id == n)
        rig.store.commit(
            Batch(entries={"kv-1": [Entry(key=f"k{n}", tokens=frozenset(row.tokens), meta={}, state=RATED)]})
        )
        rig.intent(f"w{n:02d}", "movies", n, "ratings")
    setter = rig.kodi.rpc_handlers["VideoLibrary.SetMovieDetails"]

    def then_playback(params):
        playing["on"] = True
        return setter(params)

    rig.kodi.rpc_handlers["VideoLibrary.SetMovieDetails"] = then_playback
    rig.run()
    assert len(lib.writes) == 1 and len(rig.store.pending_writes()) == 7
    playing["on"] = False
    rig.kodi.rpc_handlers["VideoLibrary.SetMovieDetails"] = setter
    rig.run()
    assert rig.store.pending_writes() == [] and all(lib.row("movies", n)["userrating"] == 8 for n in range(5, 12))


def test_in_flight_stored_before_rpc(tmp_path) -> None:
    lib = _movies((1, "smb://nas/a.mkv", {}))
    rig = Rig(tmp_path, lib)
    rig.entry(1, RATED)
    rig.intent("w1", "movies", 1, "ratings")
    seen_in_flight: list[object] = []
    setter = rig.kodi.rpc_handlers["VideoLibrary.SetMovieDetails"]

    def spy(params):
        other = RecordStore(str(tmp_path / "sync.db"))
        other.open()
        seen_in_flight.append(other.pending_writes()[0].in_flight)
        other.close()
        return setter(params)

    rig.kodi.rpc_handlers["VideoLibrary.SetMovieDetails"] = spy
    rig.run()
    assert seen_in_flight[-1] == {
        "rows": [{"kind": "movies", "kodi_id": 1, "file": "smb://nas/a.mkv", "fields": {"userrating": 8}}]
    }
    assert rig.store.pending_writes() == []


def test_failed_request_keeps_intent(tmp_path) -> None:
    lib = _movies((1, "smb://nas/a.mkv", {}))
    rig = Rig(tmp_path, lib)
    rig.entry(1, RATED)
    rig.intent("w1", "movies", 1, "ratings")

    def broken(params):
        raise RuntimeError("Kodi said no")

    rig.kodi.rpc_handlers["VideoLibrary.SetMovieDetails"] = broken
    assert rig.run().deferred == 1 and [w.id for w in rig.store.pending_writes()] == ["w1"]


def test_all_copies_written(tmp_path) -> None:
    lib = _movies((1, "smb://nas/a.mkv", {}), (2, "smb://nas/a-4k.mkv", {}))
    rig = Rig(tmp_path, lib)
    rig.entry(1, WATCHED)
    rig.intent("w1", "movies", 1, "history")
    rig.run()
    assert [lib.row("movies", n)["playcount"] for n in (1, 2)] == [2, 2]


def test_rating_fill_skipped_on_copies(tmp_path) -> None:
    lib = _movies((1, "smb://nas/a.mkv", {}), (2, "smb://nas/a-4k.mkv", {}))
    rig = Rig(tmp_path, lib)
    rig.entry(1, RATED)
    rig.intent("w1", "movies", 1, "ratings")
    assert rig.run().dropped == 1 and lib.writes == []


def _episodes(lib: FakeLibrary, *specs: tuple[int, int, str, dict]) -> FakeLibrary:
    lib.add_show(7, uniqueid={"tvdb": "81189"}, title="BB", year=2008)
    for kodi_id, number, file, fields in specs:
        lib.add_episode(kodi_id, 7, 1, number, file=file, **fields)
    return lib


def test_multi_episode_file_waits(tmp_path) -> None:
    lib = _episodes(FakeLibrary(), (10, 1, "smb://nas/e1-e2.mkv", {}), (11, 2, "smb://nas/e1-e2.mkv", {}))
    rig = Rig(tmp_path, lib)
    rig.entry(10, WATCHED)
    rig.intent("w1", "episodes", 10, "history")
    assert rig.run().deferred == 1 and lib.writes == []
    rig.entry(11, WATCHED)
    assert rig.run().written == 1


def test_rating_write_on_episode_restores_the_copied_count(tmp_path) -> None:
    lib = _episodes(FakeLibrary(), (10, 1, "smb://nas/e1.mkv", {}), (11, 1, "smb://nas/e1-4k.mkv", {"playcount": 3}))
    rig = Rig(tmp_path, lib)
    rig.entry(10, ItemState(rating=Rating(value=6, from_cw=True)))
    row = next(r for r in rig.library().episodes if r.key.kodi_id == 10)
    rig.store.commit(
        Batch(
            seen={
                row.key: Seen(),
                next(r for r in rig.library().episodes if r.key.kodi_id == 11).key: Seen(playcount=3),
            }
        )
    )
    rig.intent("w1", "episodes", 10, "ratings")
    rig.run()
    assert (lib.row("episodes", 10)["userrating"], lib.row("episodes", 10)["playcount"]) == (6, 0)


@pytest.fixture(autouse=True, params=["UTC", "Europe/Amsterdam"])
def _zone(request, monkeypatch):
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_own_rating_write_updates_seen_rating_keeps_dateadded(tmp_path) -> None:
    rig = Rig(tmp_path, _movies((1, "smb://nas/a.mkv", {})))
    rig.entry(1, RATED)
    key = items.RowKey("lib1", "movies", 1, "smb://nas/a.mkv")
    rig.store.commit(Batch(seen={key: Seen(userrating=0, dateadded="2020-01-01T00:00:00Z")}))
    rig.intent("w1", "movies", 1, "ratings")
    rig.run()
    seen = rig.store.seen(key)
    assert seen is not None and seen.userrating == 8


def test_second_cw_rating_within_scan_interval_is_written(tmp_path) -> None:
    rig = Rig(tmp_path, _movies((1, "smb://nas/a.mkv", {})))
    rig.entry(1, RATED)
    rig.intent("w1", "movies", 1, "ratings")
    rig.run()
    rig.entry(1, ItemState(rating=Rating(value=5, from_cw=True)))
    rig.intent("w2", "movies", 1, "ratings")
    assert rig.run().written == 1 and rig.lib.row("movies", 1)["userrating"] == 5


def test_deferred_rating_write_does_not_overwrite_person(tmp_path) -> None:
    rig = Rig(tmp_path, _movies((1, "smb://nas/a.mkv", {"userrating": 9})))
    rig.entry(1, RATED)
    rig.store.commit(Batch(seen={items.RowKey("lib1", "movies", 1, "smb://nas/a.mkv"): Seen(userrating=6)}))
    rig.intent("w1", "movies", 1, "ratings")
    assert rig.run().dropped == 1 and rig.lib.row("movies", 1)["userrating"] == 9


def test_rating_write_skipped_while_a_copy_differs(tmp_path) -> None:
    lib = _movies((1, "smb://nas/a.mkv", {}), (2, "smb://nas/a-4k.mkv", {"userrating": 3}))
    rig = Rig(tmp_path, lib)
    rig.entry(1, RATED)
    rig.store.commit(Batch(seen={items.RowKey("lib1", "movies", 1, "smb://nas/a.mkv"): Seen(),
                                 items.RowKey("lib1", "movies", 2, "smb://nas/a-4k.mkv"): Seen()}))  # fmt: skip
    rig.intent("w1", "movies", 1, "ratings")
    assert rig.run().dropped == 1 and lib.writes == []


def test_older_cw_resume_does_not_overwrite_newer_local(tmp_path) -> None:
    lib = _movies(
        (1, "smb://nas/a.mkv", {"lastplayed": "2026-11-19 20:00:00", "resume": {"position": 3000, "total": 5400}})
    )
    rig = Rig(tmp_path, lib)
    rig.entry(1, ItemState(resume=Resume(position=600.0, total=5400.0)))
    rig.intent("w1", "movies", 1, "progress", fetched_at="2026-11-19T10:00:00Z")
    assert rig.run().dropped == 1 and lib.row("movies", 1)["resume"]["position"] == 3000


def test_read_back_compares_sent_strings(tmp_path) -> None:
    lib = _movies((1, "smb://nas/a.mkv", {}))
    rig = Rig(tmp_path, lib)
    rig.entry(1, WATCHED)
    rig.intent("w1", "movies", 1, "history")
    setter = rig.kodi.rpc_handlers["VideoLibrary.SetMovieDetails"]

    def drops_the_date(params):
        return setter({k: v for k, v in params.items() if k != "lastplayed"})

    rig.kodi.rpc_handlers["VideoLibrary.SetMovieDetails"] = drops_the_date
    report = rig.run()
    assert (report.written, report.dropped) == (0, 1)


def test_history_write_keeps_seen_lastplayed_for_a_later_resume(tmp_path) -> None:
    lib = _movies(
        (1, "smb://nas/a.mkv", {"lastplayed": "2026-11-10 10:00:00", "resume": {"position": 500, "total": 5400}})
    )
    rig = Rig(tmp_path, lib)
    key = items.RowKey("lib1", "movies", 1, "smb://nas/a.mkv")
    rig.store.commit(Batch(seen={key: Seen(lastplayed=kodi_to_utc("2026-11-10 10:00:00"), resume={"position": 500.0})}))
    finished = ItemState(history=History(watched=True, playcount=1, lastplayed="2026-11-18T10:00:00Z"))
    rig.entry(1, finished)
    rig.intent("h1", "movies", 1, "history")
    rig.run()
    rig.intent("p1", "movies", 1, "progress")
    rig.run()
    assert lib.row("movies", 1)["resume"] == {"position": 0, "total": 0}


def test_partial_failure_commits_notes_and_clears_in_flight(tmp_path) -> None:
    lib = _movies((1, "smb://nas/a.mkv", {}), (2, "smb://nas/a-4k.mkv", {}))
    rig = Rig(tmp_path, lib)
    rig.entry(1, WATCHED)
    rig.intent("w1", "movies", 1, "history")
    setter = rig.kodi.rpc_handlers["VideoLibrary.SetMovieDetails"]

    def second_fails(params):
        if params["movieid"] == 2:
            raise RuntimeError("busy")
        return setter(params)

    rig.kodi.rpc_handlers["VideoLibrary.SetMovieDetails"] = second_fails
    assert rig.run().deferred == 1
    [write] = rig.store.pending_writes()
    assert write.in_flight is None
    for _ in range(OWN_WRITE_TICKS):
        rig.own.tick()
    assert not rig.own.matches("movie", 1, {"item": {"id": 1, "type": "movie"}, "playcount": 2})


def test_a_failing_restore_read_keeps_the_intent(tmp_path) -> None:
    lib = _episodes(FakeLibrary(), (10, 1, "smb://nas/e1.mkv", {}))
    rig = Rig(tmp_path, lib)
    rig.entry(10, ItemState(rating=Rating(value=6, from_cw=True)))
    rig.intent("w1", "episodes", 10, "ratings")
    getter = rig.kodi.rpc_handlers["VideoLibrary.GetEpisodeDetails"]
    calls = {"n": 0}

    def flaky(params):
        calls["n"] += 1
        if params.get("properties") == ["playcount", "lastplayed"]:
            raise RuntimeError("timeout")
        return getter(params)

    rig.kodi.rpc_handlers["VideoLibrary.GetEpisodeDetails"] = flaky
    assert rig.run().deferred == 1 and len(rig.store.pending_writes()) == 1


def test_stops_mid_intent_when_may_write_turns_false(tmp_path) -> None:
    lib = _movies((1, "smb://nas/a.mkv", {}))
    answers = iter([True, True, True, False])
    rig = Rig(tmp_path, lib, may_write=lambda: next(answers, False))
    rig.entry(1, WATCHED)
    rig.intent("w1", "movies", 1, "history")
    assert rig.run().deferred == 1 and lib.writes == []


def test_multi_episode_raise_never_brings_back_a_local_unwatch(tmp_path) -> None:
    lib = _episodes(FakeLibrary(), (10, 1, "smb://nas/e1-e2.mkv", {}), (11, 2, "smb://nas/e1-e2.mkv", {}))
    rig = Rig(tmp_path, lib)
    rig.entry(10, WATCHED)
    rig.entry(
        11, ItemState(history=History(watched=True, playcount=1, lastplayed="2026-11-01T20:00:00Z", local_unwatch=True))
    )
    rig.intent("w1", "episodes", 10, "history")
    assert rig.run().deferred == 1 and lib.writes == []
