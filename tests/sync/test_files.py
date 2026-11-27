from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from resources.lib.sync import files
from resources.lib.sync.files import (
    Inbox,
    InboxRecord,
    Request,
    StartedFile,
    StatusFile,
    append_request,
    done_requests,
    read_requests,
    read_started,
    read_status,
    update_started,
    write_started,
    write_status,
)


def test_files_atomic_and_versioned(tmp_path) -> None:
    path = str(tmp_path / "sync_started.json")
    write_started(path, StartedFile(connection="https://cw/x", start_id="s1", existing_ratings=True))
    raw = json.loads((tmp_path / "sync_started.json").read_text())
    assert raw["version"] == 1 and raw["start_id"] == "s1"
    assert not list(tmp_path.glob(".tmp-*"))
    assert read_started(path) == StartedFile(connection="https://cw/x", start_id="s1", existing_ratings=True)


def test_started_from_another_version_reads_as_none(tmp_path) -> None:
    path = tmp_path / "sync_started.json"
    path.write_text(json.dumps({"version": 2, "connection": "c", "start_id": "s"}))
    assert read_started(str(path)) is None
    path.write_text(json.dumps({"version": 1, "connection": "c", "start_id": ""}))
    assert read_started(str(path)) is None
    assert read_started(str(tmp_path / "missing.json")) is None


def test_worker_pause_write_ignored_after_new_start(tmp_path) -> None:
    path = str(tmp_path / "sync_started.json")
    write_started(path, StartedFile(connection="c", start_id="old"))
    assert update_started(path, "old", paused=True, paused_reason="not_sync_device")
    current = read_started(path)
    assert current is not None and current.paused and current.paused_reason == "not_sync_device"
    write_started(path, StartedFile(connection="c", start_id="new"))
    assert not update_started(path, "old", paused=True)
    current = read_started(path)
    assert current is not None and not current.paused


def test_worker_may_not_change_starts_fields(tmp_path) -> None:
    path = str(tmp_path / "sync_started.json")
    write_started(path, StartedFile(connection="c", start_id="s"))
    with pytest.raises(ValueError):
        update_started(path, "s", connection="other")


def test_requests_stay_until_done(tmp_path) -> None:
    path = str(tmp_path / "sync_requests.json")
    append_request(path, Request(id="r1", kind="assign", args={"entry": "e1", "viewers": ["kv-1"]}))
    append_request(path, Request(id="r2", kind="send_existing_ratings"))
    read = read_requests(path)
    assert [r.id for r in read] == ["r1", "r2"]
    assert read[0].args == {"entry": "e1", "viewers": ["kv-1"]}
    # Not removed by reading: a Kodi exit before the commit loses nothing.
    assert [r.id for r in read_requests(path)] == ["r1", "r2"]
    append_request(path, Request(id="r3", kind="mass_ratings_drop"))
    assert done_requests(path, {"r1", "r2"})
    assert [r.id for r in read_requests(path)] == ["r3"]
    assert done_requests(path, {"nothing"})


def test_unreadable_requests_skipped_and_cleared(tmp_path) -> None:
    path = tmp_path / "sync_requests.json"
    rows = [{"id": "x", "kind": "format_disk"}, {"id": "z", "kind": ["assign"]}, {"id": "y", "kind": "assign"}]
    path.write_text(json.dumps({"version": 1, "requests": rows}))
    assert [r.id for r in read_requests(str(path))] == ["y"]
    done_requests(str(path), {"y"})
    assert json.loads(path.read_text())["requests"] == []


def test_status_with_wrong_types_falls_back_per_field(tmp_path) -> None:
    path = tmp_path / "sync_status.json"
    path.write_text(
        json.dumps({"version": 1, "state": "bogus", "per_viewer": None, "waiting_changes": "3", "message": "Hi"})
    )
    assert read_status(str(path)) == StatusFile(message="Hi")


def test_status_round_trip_and_unknown_fields(tmp_path) -> None:
    path = str(tmp_path / "sync_status.json")
    status = StatusFile(state="syncing", message="Uploading", waiting_changes=3, per_viewer={"kv-1": "ok"})
    write_status(path, status)
    assert read_status(path) == status
    raw = json.loads((tmp_path / "sync_status.json").read_text())
    raw["new_field"] = 1
    (tmp_path / "sync_status.json").write_text(json.dumps(raw))
    assert read_status(path) == status
    assert read_status(str(tmp_path / "missing.json")) is None


def _record(n: int) -> InboxRecord:
    return InboxRecord(at=f"2026-11-19T10:00:{n:02d}Z", kind="movie", kodi_id=n, file=f"smb://nas/{n}.mkv", playcount=1)


def test_inbox_keeps_file_and_order(tmp_path) -> None:
    inbox = Inbox(str(tmp_path / "sync_inbox.db"))
    assert inbox.add_many([_record(1), _record(2)])
    pending = inbox.pending()
    assert [(r.kodi_id, r.file) for r in pending] == [(1, "smb://nas/1.mkv"), (2, "smb://nas/2.mkv")]
    inbox.remove(pending[0].id)
    assert [r.kodi_id for r in inbox.pending()] == [2]


def test_inbox_add_while_worker_removes(tmp_path) -> None:
    path = str(tmp_path / "sync_inbox.db")
    Inbox(path).add_many([_record(0)])
    errors: list[BaseException] = []

    def service() -> None:
        try:
            for n in range(1, 41):
                for _ in range(200):
                    if Inbox(path, busy_timeout=1.0).add_many([_record(n)]):
                        break
                else:
                    raise AssertionError(f"record {n} never written")
        except BaseException as exc:  # noqa: BLE001 - surfaced through the assertion below
            errors.append(exc)

    taken: list[int] = []
    thread = threading.Thread(target=service)
    thread.start()
    worker = Inbox(path, busy_timeout=1.0)
    while thread.is_alive() or worker.pending():
        batch = worker.pending()
        if batch:
            taken.extend(r.kodi_id for r in batch)
            worker.remove(batch[-1].id)
    thread.join()
    assert not errors
    assert sorted(taken) == list(range(41))


def test_inbox_full_drops_oldest_and_counts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(files, "INBOX_MAX", 3)
    inbox = Inbox(str(tmp_path / "sync_inbox.db"))
    inbox.add_many([_record(n) for n in range(1, 6)])
    assert [r.kodi_id for r in inbox.pending()] == [3, 4, 5]
    assert inbox.dropped() == 2


def test_inbox_busy_returns_false_and_writes_nothing(tmp_path) -> None:
    path = str(tmp_path / "sync_inbox.db")
    Inbox(path).add_many([_record(1)])
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        assert not Inbox(path, busy_timeout=0.05).add_many([_record(2)])
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert [r.kodi_id for r in Inbox(path).pending()] == [1]
    assert Inbox(path).add_many([])


def test_corrupt_inbox_is_set_aside_and_recreated(tmp_path) -> None:
    path = tmp_path / "sync_inbox.db"
    path.write_bytes(b"not a database at all, from a power cut" * 100)
    inbox = Inbox(str(path))
    assert not inbox.add_many([_record(1)])
    assert (tmp_path / "sync_inbox.db.corrupt").exists()
    assert inbox.add_many([_record(2)])
    assert [r.kodi_id for r in inbox.pending()] == [2]
