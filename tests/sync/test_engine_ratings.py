from dataclasses import replace

from resources.lib.sync.constants import MASS_RATING_CHANGE, RATING_SCAN_SECONDS, SETTLE_SECONDS
from resources.lib.sync.files import (
    Request,
    StartedFile,
    append_request,
    read_requests,
    read_started,
    read_status,
    write_started,
)
from resources.lib.sync.items import RowKey
from resources.lib.sync.library_id import library_id
from resources.lib.sync.record import Assignment, Batch, Entry, ItemState, PendingWrite, Rating
from resources.lib.sync.reply import PingTicket, ReplyViewer, SyncReply, connection_id
from tests.sync.fake_cw import ok
from tests.sync.test_engine import NOW, URL
from tests.sync.test_engine_uploads import CwRig, _after_first_full


def _key(rig: CwRig, kodi_id: int = 3) -> RowKey:
    return RowKey(library_id(rig.kodi), "movies", kodi_id, f"/m/{kodi_id}.mkv")


def _scan(rig: CwRig) -> None:
    rig.clock.now += RATING_SCAN_SECONDS
    rig.engine._rating_scan()


def test_rating_set_in_kodi_goes_into_the_record(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.lib.row("movies", 3)["userrating"] = 8
    _scan(rig)
    [entry] = rig.store().entries("kv-a")
    assert entry.state.rating.value == 8 and entry.unsent.get("ratings") and entry.touched
    seen = rig.store().seen(_key(rig))
    assert seen is not None and seen.userrating == 8
    assert rig.engine.state.viewers["kv-a"].unsent


def test_rating_on_one_copy_gives_the_other_copy_the_value(tmp_path):
    rig = CwRig(tmp_path)
    rig.lib.add_movie(4, title="Film", year=2001, uniqueid={"tmdb": "3"}, file="/m/4.mkv")
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    rig.judged()
    rig.run(3)
    rig.lib.row("movies", 4)["userrating"] = 6
    _scan(rig)
    [write] = rig.store().pending_writes()
    assert write.group == "ratings" and write.row == _key(rig, 3)


def _held(tmp_path) -> CwRig:
    """More ratings changed in one scan than are sent without asking."""
    rig = CwRig(tmp_path)
    for n in range(4, 4 + MASS_RATING_CHANGE + 1):
        rig.lib.add_movie(n, title=f"F{n}", year=2001, uniqueid={"tmdb": str(n)}, file=f"/m/{n}.mkv")
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    rig.judged()
    rig.run(3)
    for n in range(4, 4 + MASS_RATING_CHANGE + 1):
        rig.lib.row("movies", n)["userrating"] = 5
    _scan(rig)
    assert len(rig.engine.held_ratings) == MASS_RATING_CHANGE + 1
    assert all(e.state.rating.value == 0 for e in rig.store().entries("kv-a"))
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.mass_ratings == MASS_RATING_CHANGE + 1
    return rig


def test_mass_rating_change_asks_first(tmp_path):
    _held(tmp_path)


def test_mass_rating_send_records_them(tmp_path):
    rig = _held(tmp_path)
    append_request(rig.paths.requests, Request(id="r1", kind="mass_ratings_send"))
    rig.engine.on_request()
    rig.engine.step()
    rated = [e for e in rig.store().entries("kv-a") if e.state.rating.value == 5]
    assert len(rated) == MASS_RATING_CHANGE + 1 and rig.engine.held_ratings == []
    assert read_requests(rig.paths.requests) == []


def test_mass_rating_dont_send_updates_seen(tmp_path):
    rig = _held(tmp_path)
    append_request(rig.paths.requests, Request(id="r1", kind="mass_ratings_drop"))
    rig.engine.on_request()
    rig.engine.step()
    assert all(e.state.rating.value == 0 for e in rig.store().entries("kv-a"))
    seen = rig.store().seen(_key(rig, 4))
    assert seen is not None and seen.userrating == 5 and rig.engine.held_ratings == []


def test_rating_window_write_recorded_when_viewers_are_owners(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.store().commit(Batch(pending_add=[
        PendingWrite(id="w1", row=_key(rig), tokens=frozenset(rig.library().movies[0].tokens), group="ratings", created=NOW)
    ]))  # fmt: skip
    rig.lib.row("movies", 3)["userrating"] = 9
    rig.engine.on_rating_written("movie", 3, 9, ("kv-a",))
    rig.engine._take_inputs()
    _scan(rig)
    [entry] = rig.store().entries("kv-a")
    assert entry.state.rating.value == 9 and rig.store().pending_writes() == []
    seen = rig.store().seen(_key(rig))
    assert seen is not None and seen.userrating == 9


def test_rating_window_write_that_didnt_land_records_nothing(tmp_path):
    rig = _after_first_full(tmp_path)
    rig.engine.on_rating_written("movie", 3, 9, ("kv-a",))
    rig.engine._take_inputs()
    _scan(rig)
    [entry] = rig.store().entries("kv-a")
    assert entry.state.rating.value == 0


def test_assign_request_applies_the_assignment(tmp_path):
    rig = _after_first_full(tmp_path)
    tokens = frozenset(rig.library().movies[0].tokens)
    assignment = Assignment(id="a1", entry_key="movies|tmdb:3", tokens=tokens, group="ratings",
                            value=ItemState(rating=Rating(value=7)), title="Film (2001)", at=NOW)  # fmt: skip
    rig.store().commit(Batch(assignments_add=[assignment]))
    append_request(rig.paths.requests, Request(id="r1", kind="assign", args={"assignment": "a1", "viewers": ["kv-a"]}))
    rig.engine.on_request()
    rig.engine.step()
    [entry] = rig.store().entries("kv-a")
    assert entry.state.rating.value == 7 and rig.store().assignments() == []
    assert read_requests(rig.paths.requests) == []


def test_assignments_listed_in_the_status(tmp_path):
    rig = _after_first_full(tmp_path)
    assignment = Assignment(id="a1", entry_key="k", tokens=frozenset({"t"}), group="history", value=ItemState(),
                            title="Film (2001)", at=NOW)  # fmt: skip
    rig.store().commit(Batch(assignments_add=[assignment]))
    rig.engine.on_quiet(False)
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and [a["id"] for a in status.assignments] == ["a1"]


def _settle(rig: CwRig) -> None:
    store = rig.store()
    store._db.execute(
        "UPDATE viewers SET body = json_set(body, '$.first_full_at', '2026-11-20T08:00:00Z') WHERE viewer = 'kv-a'"
    )
    reply = SyncReply(
        enabled=True, reason=None, viewers={"kv-a": ReplyViewer("default", store.seqs("kv-a").sent)}, queue_pending=0
    )
    rig.engine.on_ping_reply(reply, PingTicket(sent_at=rig.clock.now, epochs=dict(rig.engine.epochs)))
    rig.engine._take_inputs()


def test_first_round_settles_after_a_quiet_ping(tmp_path):
    rig = _after_first_full(tmp_path)
    assert SETTLE_SECONDS <= 7200
    _settle(rig)
    assert rig.store().settled("kv-a") and rig.engine.state.viewers["kv-a"].settled


def test_existing_ratings_sent_after_settling(tmp_path):
    rig = _after_first_full(tmp_path)
    write_started(rig.paths.started, StartedFile(connection=connection_id(URL), start_id="s", existing_ratings=True))
    tokens = frozenset(rig.library().movies[0].tokens)
    [entry] = rig.store().entries("kv-a")
    rig.store().commit(
        Batch(entries={"kv-a": [replace(entry, origin="seed", seed_rating=6, tokens=entry.tokens | tokens)]})
    )
    rig.lib.row("movies", 3)["userrating"] = 6
    _settle(rig)
    vs = rig.engine.state.viewers["kv-a"]
    assert vs.existing_ratings_due
    rig.engine._send_existing_ratings("kv-a")
    [entry] = rig.store().entries("kv-a")
    assert entry.state.rating.value == 6 and entry.unsent.get("ratings")
    started = read_started(rig.paths.started)
    assert started is not None and started.existing_ratings_sent


def test_existing_ratings_owed_at_restart(tmp_path):
    rig = _after_first_full(tmp_path)
    write_started(rig.paths.started, StartedFile(connection=connection_id(URL), start_id="s", existing_ratings=True))
    rig.store().set_settled("kv-a")
    rig.engine.end()
    rig.engine.begin()
    assert rig.engine.state.viewers["kv-a"].existing_ratings_due


def test_unknown_assignment_request_is_removed(tmp_path):
    rig = _after_first_full(tmp_path)
    append_request(
        rig.paths.requests, Request(id="r1", kind="assign", args={"assignment": "gone", "viewers": ["kv-a"]})
    )
    rig.engine.on_request()
    rig.engine.step()
    assert read_requests(rig.paths.requests) == []


def test_entry_unchanged_by_a_scan_without_changes(tmp_path):
    rig = _after_first_full(tmp_path)
    before = rig.store().entries("kv-a")
    _scan(rig)
    assert rig.store().entries("kv-a") == before and isinstance(before[0], Entry)


def _two_copies(tmp_path) -> CwRig:
    rig = CwRig(tmp_path)
    rig.lib.row("movies", 3)["userrating"] = 7
    rig.lib.add_movie(4, title="Film", year=2001, uniqueid={"tmdb": "3"}, file="/m/4.mkv", userrating=7)
    rig.cw.script("upload", ok(complete=True))
    rig.ready()
    rig.judged()
    rig.run(3)
    return rig


def test_cleared_rating_with_removals_off_leaves_the_copies_alone(tmp_path):
    rig = _two_copies(tmp_path)
    rig.lib.row("movies", 4)["userrating"] = 0
    _scan(rig)
    assert rig.store().pending_writes() == []


def test_dont_send_keeps_every_copy_from_coming_back(tmp_path):
    rig = _two_copies(tmp_path)
    for n in range(5, 5 + MASS_RATING_CHANGE + 1):
        rig.lib.add_movie(n, title=f"F{n}", year=2001, uniqueid={"tmdb": str(n)}, file=f"/m/{n}.mkv")
    rig.run(1)
    rig.engine._state = replace(
        rig.engine.state, viewers={"kv-a": replace(rig.engine.state.viewers["kv-a"], full_due=True)}
    )
    rig.cw.script("upload", ok(complete=True))
    rig.run(3)
    for n in (3, 4, *range(5, 5 + MASS_RATING_CHANGE + 1)):
        rig.lib.row("movies", n)["userrating"] = 5
    _scan(rig)
    assert rig.engine.held_ratings
    append_request(rig.paths.requests, Request(id="r1", kind="mass_ratings_drop"))
    rig.engine.on_request()
    rig.engine.step()
    _scan(rig)
    assert all(e.state.rating.value == 0 for e in rig.store().entries("kv-a"))
    assert rig.engine.held_ratings == []


def test_a_second_mass_change_adds_to_the_held_set(tmp_path):
    rig = _held(tmp_path)
    first = {f.row.key for f in rig.engine.held_ratings}
    for n in range(100, 100 + MASS_RATING_CHANGE + 1):
        rig.lib.add_movie(n, title=f"G{n}", year=2001, uniqueid={"tmdb": str(n)}, file=f"/m/{n}.mkv")
    rig.engine._state = replace(
        rig.engine.state, viewers={"kv-a": replace(rig.engine.state.viewers["kv-a"], full_due=True)}
    )
    rig.cw.script("upload", ok(complete=True))
    rig.run(4)
    for n in range(100, 100 + MASS_RATING_CHANGE + 1):
        rig.lib.row("movies", n)["userrating"] = 4
    _scan(rig)
    assert first <= {f.row.key for f in rig.engine.held_ratings}


def test_send_skips_a_rating_crosswatch_set_during_the_hold(tmp_path):
    rig = _held(tmp_path)
    key = rig.engine.held_ratings[0].row.key
    entry = next(e for e in rig.store().entries("kv-a") if e.meta.get("file") == key.file)
    rig.store().commit(Batch(entries={"kv-a": [replace(entry, applied_change={"ratings": 900})]}))
    append_request(rig.paths.requests, Request(id="r1", kind="mass_ratings_send"))
    rig.engine.on_request()
    rig.engine.step()
    after = next(e for e in rig.store().entries("kv-a") if e.key == entry.key)
    assert after.state.rating.value == 0


def test_mass_answer_waits_for_quiet(tmp_path):
    rig = _held(tmp_path)
    rig.engine.on_quiet(False)
    append_request(rig.paths.requests, Request(id="r1", kind="mass_ratings_send"))
    rig.engine.on_request()
    rig.engine.step()
    assert len(read_requests(rig.paths.requests)) == 1 and rig.engine.held_ratings
    assert not rig.engine.state.requests_waiting


def test_failing_request_does_not_spin_the_worker(tmp_path, monkeypatch):
    rig = _after_first_full(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("bad")

    monkeypatch.setattr("resources.lib.sync.engine.apply_assignment", boom)
    tokens = frozenset(rig.library().movies[0].tokens)
    rig.store().commit(Batch(assignments_add=[Assignment(id="a1", entry_key="k", tokens=tokens, group="ratings",
                                                         value=ItemState(), title="F", at=NOW)]))  # fmt: skip
    append_request(rig.paths.requests, Request(id="r1", kind="assign", args={"assignment": "a1", "viewers": ["kv-a"]}))
    rig.engine.on_request()
    rig.engine.step()
    assert not rig.engine.state.requests_waiting and len(read_requests(rig.paths.requests)) == 1


def test_existing_ratings_marked_sent_only_when_every_viewer_sent(tmp_path):
    rig = _after_first_full(tmp_path)
    write_started(rig.paths.started, StartedFile(connection=connection_id(URL), start_id="s", existing_ratings=True))
    reply = SyncReply(enabled=True, reason=None, queue_pending=0,
                      viewers={"kv-a": ReplyViewer("default", 0), "kv-b": ReplyViewer("default~kv-b", 0)})  # fmt: skip
    rig.engine._state = replace(rig.engine.state, reply=reply)
    rig.engine._send_existing_ratings("kv-a")
    started = read_started(rig.paths.started)
    assert started is not None and not started.existing_ratings_sent
    rig.engine._send_existing_ratings("kv-b")
    started = read_started(rig.paths.started)
    assert started is not None and started.existing_ratings_sent


def test_status_survives_a_failing_assignment_read(tmp_path, monkeypatch):
    rig = _after_first_full(tmp_path)

    def boom():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(rig.store(), "assignments", boom)
    rig.engine.on_quiet(False)
    rig.engine.step()
    status = read_status(rig.paths.status)
    assert status is not None and status.assignments == []


def test_crosswatch_rating_for_a_held_row_waits_for_the_answer(tmp_path):
    rig = _held(tmp_path)
    held = rig.engine.held_ratings[0]
    entry = next(e for e in rig.store().entries("kv-a") if e.meta.get("file") == held.row.key.file)
    rig.store().commit(Batch(
        entries={"kv-a": [replace(entry, state=ItemState(rating=Rating(value=9, from_cw=True)))]},
        pending_add=[PendingWrite(id="cw1", row=held.row.key, tokens=entry.tokens, group="ratings", created=NOW)],
    ))  # fmt: skip
    rig.engine._write_kodi()
    assert [w.id for w in rig.store().pending_writes()] == ["cw1"]
