import contextlib
import json
import os
import threading
import time

import pytest

from resources.lib import log as logmod
from resources.lib import storage
from resources.lib.models import Viewer
from resources.lib.routes import Person, RouteFacts
from resources.lib.storage import JsonViewerStore, PromptMemory, RememberedAnswer, RouteStore, file_lock


def test_missing_file_yields_no_viewers(tmp_path):
    assert JsonViewerStore(str(tmp_path / "viewers.json")).viewers() == []


def test_saved_viewers_round_trip(tmp_path):
    path = str(tmp_path / "viewers.json")
    JsonViewerStore(path).save([Viewer(name="anna", playlists=("Anna TV",), profiles=("Anna",))])
    assert JsonViewerStore(path).viewers() == [Viewer(name="anna", playlists=("Anna TV",), profiles=("Anna",))]


def test_corrupt_file_yields_no_viewers_rather_than_raising(tmp_path):
    path = tmp_path / "viewers.json"
    path.write_text("{not json", encoding="utf-8")
    assert JsonViewerStore(str(path)).viewers() == []


def test_entries_missing_a_name_are_skipped(tmp_path):
    path = tmp_path / "viewers.json"
    path.write_text(json.dumps([{"playlists": ["x"]}, {"name": "bob"}]), encoding="utf-8")
    assert [v.name for v in JsonViewerStore(str(path)).viewers()] == ["bob"]


def test_saved_files_are_not_world_readable(tmp_path):
    path = str(tmp_path / "viewers.json")
    JsonViewerStore(path).save([Viewer(name="anna")])
    assert os.stat(path).st_mode & 0o077 == 0


def test_a_failed_write_is_swallowed_because_it_sits_on_the_stop_path(tmp_path, monkeypatch):
    memory = PromptMemory(str(tmp_path / "prompts.json"))

    def boom(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr("resources.lib.storage.os.replace", boom)
    memory.remember("show:tvdb:83462", ("anna",))  # must not raise


def test_prompt_memory_recalls_what_was_remembered(tmp_path):
    path = str(tmp_path / "prompts.json")
    memory = PromptMemory(path)
    assert memory.recall("show:tvdb:83462") is None
    memory.remember("show:tvdb:83462", ("anna",), title="Example", year=2008)
    expected = RememberedAnswer(viewers=("anna",), title="Example", year=2008)
    assert memory.recall("show:tvdb:83462") == expected
    assert PromptMemory(path).recall("show:tvdb:83462") == expected


def test_an_answer_written_before_titles_were_stored_still_reads(tmp_path):
    """Earlier versions wrote a bare list of names per show."""
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps({"show:tvdb:1": ["anna", "bob"]}), encoding="utf-8")
    assert PromptMemory(str(path)).recall("show:tvdb:1") == RememberedAnswer(viewers=("anna", "bob"))


def test_entries_lists_every_remembered_answer(tmp_path):
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps({"show:tvdb:1": ["anna"]}), encoding="utf-8")
    memory = PromptMemory(str(path))
    memory.remember("tvshow:42", ("bob",), title="No Ids", year=2020)
    assert memory.entries() == {
        "show:tvdb:1": RememberedAnswer(viewers=("anna",)),
        "tvshow:42": RememberedAnswer(viewers=("bob",), title="No Ids", year=2020),
    }


def test_malformed_entries_are_skipped_rather_than_raising(tmp_path):
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps({"a": [], "b": "anna", "c": {"viewers": "x"}, "d": {"viewers": ["anna"], "year": "y"}}), encoding="utf-8")
    assert PromptMemory(str(path)).entries() == {"d": RememberedAnswer(viewers=("anna",))}


def test_forget_removes_one_answer_and_keeps_the_rest(tmp_path):
    path = str(tmp_path / "prompts.json")
    memory = PromptMemory(path)
    memory.remember("show:tvdb:1", ("anna",))
    memory.remember("show:tvdb:2", ("bob",))
    memory.forget("show:tvdb:1")
    assert list(PromptMemory(path).entries()) == ["show:tvdb:2"]


def test_forget_many_removes_those_answers_in_one_write(tmp_path, monkeypatch):
    from resources.lib import storage

    path = str(tmp_path / "prompts.json")
    memory = PromptMemory(path)
    for n in ("1", "2", "3"):
        memory.remember(f"show:tvdb:{n}", ("anna",))
    writes = []
    real = storage._write_json
    monkeypatch.setattr(storage, "_write_json", lambda p, v: writes.append(p) or real(p, v))
    assert memory.forget_many(["show:tvdb:1", "show:tvdb:3", "show:tvdb:9"]) is True
    assert list(PromptMemory(path).entries()) == ["show:tvdb:2"]
    assert len(writes) == 1


def test_forget_many_reports_a_failed_write(tmp_path, monkeypatch):
    from resources.lib import storage

    memory = PromptMemory(str(tmp_path / "prompts.json"))
    memory.remember("show:tvdb:1", ("anna",))
    monkeypatch.setattr(storage, "_write_json", lambda p, v: False)
    assert memory.forget_many(["show:tvdb:1"]) is False


def _memory(tmp_path, data):
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps(data))
    return PromptMemory(str(path)), path


def test_rename_viewer_moves_every_answer_to_the_new_name(tmp_path):
    memory, path = _memory(tmp_path, {
        "show:tvdb:1": {"viewers": ["anna", "bob"], "title": "A", "year": 2020},
        "show:tvdb:2": {"viewers": ["bob"], "title": "B", "year": None},
    })
    assert memory.rename_viewer("anna", "annie") == 1
    data = json.loads(path.read_text())
    assert data["show:tvdb:1"] == {"viewers": ["annie", "bob"], "title": "A", "year": 2020}
    assert data["show:tvdb:2"]["viewers"] == ["bob"]


def test_rename_viewer_keeps_an_old_format_answer(tmp_path):
    memory, path = _memory(tmp_path, {"show:tvdb:1": ["anna"]})
    assert memory.rename_viewer("anna", "Anna") == 1
    assert json.loads(path.read_text()) == {"show:tvdb:1": ["Anna"]}


def test_rename_viewer_leaves_a_name_that_differs_in_case(tmp_path):
    memory, _ = _memory(tmp_path, {"show:tvdb:1": {"viewers": ["Anna"]}})
    assert memory.rename_viewer("anna", "annie") == 0


def test_rename_viewer_writes_nothing_when_nobody_matches(tmp_path, monkeypatch):
    memory, _ = _memory(tmp_path, {"show:tvdb:1": {"viewers": ["bob"]}})
    writes = []
    monkeypatch.setattr("resources.lib.storage._write_json", lambda *a: writes.append(a) or True)
    assert memory.rename_viewer("anna", "annie") == 0
    assert writes == []


def test_drop_viewer_forgets_an_answer_left_with_nobody(tmp_path):
    memory, path = _memory(tmp_path, {"show:tvdb:1": {"viewers": ["anna"]}, "show:tvdb:2": ["anna"]})
    assert memory.drop_viewer("anna") == (2, 2)
    assert json.loads(path.read_text()) == {}


def test_drop_viewer_keeps_an_answer_someone_else_shares(tmp_path):
    memory, path = _memory(tmp_path, {"show:tvdb:1": {"viewers": ["anna", "bob"], "title": "A", "year": 1}})
    assert memory.drop_viewer("anna") == (1, 0)
    assert json.loads(path.read_text())["show:tvdb:1"] == {"viewers": ["bob"], "title": "A", "year": 1}


def test_drop_viewer_writes_once(tmp_path, monkeypatch):
    memory, _ = _memory(tmp_path, {"a": ["anna"], "b": ["anna", "bob"], "c": ["bob"]})
    import resources.lib.storage as storage

    real = storage._write_json
    writes = []
    monkeypatch.setattr(storage, "_write_json", lambda p, v: writes.append(v) or real(p, v))
    memory.drop_viewer("anna")
    assert len(writes) == 1


def test_route_store_keeps_the_people(tmp_path):
    store = RouteStore(str(tmp_path / "routes.json"))
    facts = RouteFacts(1, frozenset({"anna"}), frozenset({"anna"}), (Person("p1", "Anna"),))
    store.save("fp", facts)
    assert store.load("fp") == facts


def test_route_store_without_people_loads_none(tmp_path):
    """What beta2 wrote: no people key."""
    path = tmp_path / "routes.json"
    path.write_text(json.dumps({"fingerprint": "fp", "routes": 1, "accepted": [], "asked": []}))
    facts = RouteStore(str(path)).load("fp")
    assert facts is not None and facts.people is None


def test_route_store_keeps_an_empty_people_list_apart_from_none(tmp_path):
    store = RouteStore(str(tmp_path / "routes.json"))
    store.save("fp", RouteFacts(0, frozenset(), frozenset(), ()))
    loaded = store.load("fp")
    assert loaded is not None and loaded.people == ()


def test_a_viewer_keeps_its_person_id(tmp_path):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    viewers = [Viewer(name="Anna Lee", person_id="p-anna"), Viewer(name="bob")]
    store.save(viewers)
    assert store.viewers() == viewers


def test_an_unlinked_viewer_is_written_without_a_person_id(tmp_path):
    """So the file still reads the same in beta2."""
    path = tmp_path / "viewers.json"
    JsonViewerStore(str(path)).save([Viewer(name="bob")])
    assert "person_id" not in json.loads(path.read_text())[0]


def test_a_blank_or_odd_person_id_reads_as_unlinked(tmp_path):
    path = tmp_path / "viewers.json"
    path.write_text(
        json.dumps([{"name": "a", "person_id": ""}, {"name": "b", "person_id": 7}])
    )
    assert [v.person_id for v in JsonViewerStore(str(path)).viewers()] == [None, None]


def _append_slowly(path, item, guard, ready):
    """Read the list, pause, write it back with item added: the window a second writer
    lands in when nothing spans the read and the write."""
    ready.wait()
    with guard():
        rows = json.loads(path.read_text()) if path.exists() else []
        time.sleep(0.2)
        path.write_text(json.dumps([*rows, item]))


def _two_writers(path, guard):
    ready = threading.Barrier(2)
    threads = [threading.Thread(target=_append_slowly, args=(path, item, guard, ready)) for item in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return sorted(json.loads(path.read_text()))


def test_two_writers_without_the_lock_lose_an_update(tmp_path):
    assert len(_two_writers(tmp_path / "viewers.json", contextlib.nullcontext)) == 1


def test_two_writers_under_the_lock_keep_both_updates(tmp_path):
    path = tmp_path / "viewers.json"
    assert _two_writers(path, lambda: file_lock(str(path))) == ["a", "b"]
    assert not os.path.exists(f"{path}.lock")


def test_the_lock_file_is_removed_after_use(tmp_path):
    path = str(tmp_path / "viewers.json")
    with file_lock(path):
        assert os.path.exists(f"{path}.lock")
    assert not os.path.exists(f"{path}.lock")


def test_the_lock_file_is_removed_after_an_exception(tmp_path):
    path = str(tmp_path / "viewers.json")
    with pytest.raises(RuntimeError), file_lock(path):
        raise RuntimeError("boom")
    assert not os.path.exists(f"{path}.lock")


def test_a_stale_lock_is_broken(tmp_path):
    path = str(tmp_path / "viewers.json")
    lock = f"{path}.lock"
    with open(lock, "w"):
        pass
    old = time.time() - 60
    os.utime(lock, (old, old))
    start = time.monotonic()
    with file_lock(path, timeout=1.0):
        # Ours now, not the dead writer's.
        assert os.path.getmtime(lock) > old
    assert time.monotonic() - start < 1.0
    assert not os.path.exists(lock)


def test_a_held_lock_past_the_timeout_writes_anyway_and_warns_once(tmp_path):
    captured: list[str] = []
    logmod.configure(log_dir=None, debug=False, sink=lambda msg, level: captured.append(msg))
    path = str(tmp_path / "viewers.json")
    lock = f"{path}.lock"
    with open(lock, "w"):
        pass
    entered = False
    with file_lock(path, timeout=0.2):
        entered = True
    assert entered
    # The holder's lock is left alone: it is not ours to remove.
    assert os.path.exists(lock)
    assert sum("storage.lock_timeout" in line for line in captured) == 1


def test_a_lock_is_waited_for_until_its_holder_lets_go(tmp_path):
    path = str(tmp_path / "prompts.json")
    held = threading.Event()

    def hold():
        with file_lock(path):
            held.set()
            time.sleep(0.3)

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait()
    start = time.monotonic()
    PromptMemory(path).remember("tvdb:1", ("anna",))
    waited = time.monotonic() - start
    holder.join()
    assert waited >= 0.25
    assert PromptMemory(path).recall("tvdb:1") == RememberedAnswer(viewers=("anna",))


def test_the_viewer_store_locks_its_own_file(tmp_path):
    path = str(tmp_path / "viewers.json")
    with JsonViewerStore(path).locked():
        assert os.path.exists(f"{path}.lock")
    assert not os.path.exists(f"{path}.lock")


def test_an_unwritable_folder_is_waited_out_and_never_raises(tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("")
    path = str(blocker / "viewers.json")
    with file_lock(path, timeout=0.05):
        pass
    assert JsonViewerStore(path).viewers() == []


def test_a_stale_lock_another_waiter_claimed_first_is_left_to_it(tmp_path, monkeypatch):
    path = str(tmp_path / "viewers.json")
    lock = f"{path}.lock"
    with open(lock, "w"):
        pass
    old = time.time() - 60
    os.utime(lock, (old, old))

    def claimed_already(_src, _dst):
        raise FileNotFoundError

    monkeypatch.setattr(os, "rename", claimed_already)
    with file_lock(path, timeout=0.05):
        pass
    assert os.path.exists(lock)


def test_a_lock_removed_under_its_holder_is_released_quietly(tmp_path):
    path = str(tmp_path / "viewers.json")
    with file_lock(path):
        os.remove(f"{path}.lock")
    assert not os.path.exists(f"{path}.lock")


def _retaken_before_the_rename(tmp_path, monkeypatch) -> tuple[str, list[int]]:
    # Both waiters read the stale age; the other breaks it and takes a fresh lock before this
    # one's rename, which then lands on the fresh lock instead of the stale one.
    path = str(tmp_path / "viewers.json")
    lock = f"{path}.lock"
    with open(lock, "w"):
        pass
    old = time.time() - 60
    os.utime(lock, (old, old))
    real_rename = os.rename
    other: list[int] = []

    def retaken_first(src: str, dst: str) -> None:
        if src == lock and not other:
            real_rename(lock, f"{lock}.other-claim")
            os.remove(f"{lock}.other-claim")
            other.append(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", retaken_first)
    return lock, other


def test_a_stale_lock_broken_and_retaken_by_another_waiter_is_not_taken_from_it(tmp_path, monkeypatch):
    lock, other = _retaken_before_the_rename(tmp_path, monkeypatch)
    with file_lock(lock.removesuffix(".lock"), timeout=0.1):
        assert os.path.samestat(os.fstat(other[0]), os.stat(lock))
    # Still the other holder's, for it to release.
    assert os.path.samestat(os.fstat(other[0]), os.stat(lock))
    assert [name for name in os.listdir(tmp_path) if ".stale." in name] == []
    os.close(other[0])


def test_a_retaken_lock_is_renamed_back_where_links_are_not_supported(tmp_path, monkeypatch):
    lock, other = _retaken_before_the_rename(tmp_path, monkeypatch)

    def unsupported(_src: str, _dst: str) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "link", unsupported)
    with file_lock(lock.removesuffix(".lock"), timeout=0.1):
        assert os.path.samestat(os.fstat(other[0]), os.stat(lock))
    assert [name for name in os.listdir(tmp_path) if ".stale." in name] == []
    os.close(other[0])


def test_a_lock_taken_while_the_name_was_free_is_not_overwritten(tmp_path, monkeypatch):
    lock, other = _retaken_before_the_rename(tmp_path, monkeypatch)
    third: list[int] = []
    real_link = os.link

    def taken_meanwhile(src: str, dst: str) -> None:
        if not third:
            third.append(os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        real_link(src, dst)

    monkeypatch.setattr(os, "link", taken_meanwhile)
    with file_lock(lock.removesuffix(".lock"), timeout=0.1):
        assert os.path.samestat(os.fstat(third[0]), os.stat(lock))
    assert os.path.samestat(os.fstat(third[0]), os.stat(lock))
    assert [name for name in os.listdir(tmp_path) if ".stale." in name] == []
    os.close(other[0])
    os.close(third[0])


def test_a_lock_this_holder_does_not_own_is_never_removed_on_release(tmp_path):
    path = str(tmp_path / "viewers.json")
    lock = f"{path}.lock"
    with file_lock(path):
        # Broken as stale and retaken by another writer while this one still held it.
        os.remove(lock)
        theirs = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    assert os.path.samestat(os.fstat(theirs), os.stat(lock))
    os.close(theirs)


@pytest.mark.parametrize(
    "change",
    [
        lambda memory: memory.forget_many(["tvdb:1"]),
        lambda memory: memory.rename_viewer("anna", "Annie"),
        lambda memory: memory.drop_viewer("anna"),
    ],
    ids=["forget_many", "rename_viewer", "drop_viewer"],
)
def test_every_prompt_memory_change_writes_under_the_file_lock(tmp_path, monkeypatch, change):
    # The settings screen edits the same file from another interpreter, which the thread
    # lock does not reach.
    path = str(tmp_path / "prompts.json")
    memory = PromptMemory(path)
    memory.remember("tvdb:1", ("anna", "bob"))
    held: list[str] = []
    unlocked: list[str] = []
    writes: list[str] = []
    real_lock, real_write = storage.file_lock, storage._write_json

    @contextlib.contextmanager
    def recording_lock(target: str, *args, **kwargs):
        with real_lock(target, *args, **kwargs):
            held.append(target)
            try:
                yield
            finally:
                held.remove(target)

    def recording_write(target: str, value) -> bool:
        writes.append(target)
        if target not in held:
            unlocked.append(target)
        return real_write(target, value)

    monkeypatch.setattr(storage, "file_lock", recording_lock)
    monkeypatch.setattr(storage, "_write_json", recording_write)
    change(memory)
    assert writes == [path]
    assert unlocked == []
