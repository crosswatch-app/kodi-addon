import json
import os

from resources.lib.models import Viewer
from resources.lib.storage import JsonViewerStore, PromptMemory, RememberedAnswer


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
