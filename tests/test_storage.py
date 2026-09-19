import json
import os

from resources.lib.models import Viewer
from resources.lib.storage import JsonViewerStore, PromptMemory


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
    memory.remember("show:tvdb:83462", ("anna",))
    assert memory.recall("show:tvdb:83462") == ("anna",)
    assert PromptMemory(path).recall("show:tvdb:83462") == ("anna",)


def test_forget_all_clears_every_remembered_answer(tmp_path):
    path = str(tmp_path / "prompts.json")
    memory = PromptMemory(path)
    memory.remember("show:tvdb:1", ("anna",))
    memory.forget_all()
    assert PromptMemory(path).recall("show:tvdb:1") is None
