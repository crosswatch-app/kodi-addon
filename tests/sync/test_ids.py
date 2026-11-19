from __future__ import annotations

import itertools

from resources.lib.models import Viewer
from resources.lib.storage import JsonViewerStore
from resources.lib.sync.ids import SYNC_ID_PATTERN, ensure_sync_ids, new_sync_id


def _store(tmp_path, viewers: list[Viewer]) -> JsonViewerStore:
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save(viewers)
    return store


def _ids():
    counter = itertools.count(1)
    return lambda: f"kv-test{next(counter):04d}"


def test_new_sync_id_format() -> None:
    assert SYNC_ID_PATTERN.match(new_sync_id())
    assert new_sync_id() != new_sync_id()


def test_sync_id_assigned_once_and_kept_through_rename(tmp_path) -> None:
    store = _store(tmp_path, [Viewer(name="anna"), Viewer(name="bob")])
    first = ensure_sync_ids(store, _ids())
    assert [v.sync_id for v in first] == ["kv-test0001", "kv-test0002"]
    assert store.viewers() == first
    store.save([Viewer(name="Anna Lee", sync_id="kv-test0001"), first[1]])
    again = ensure_sync_ids(store, _ids())
    assert [(v.name, v.sync_id) for v in again] == [("Anna Lee", "kv-test0001"), ("bob", "kv-test0002")]


def test_viewer_added_later_gets_id(tmp_path) -> None:
    store = _store(tmp_path, [Viewer(name="anna", sync_id="kv-test0001")])
    store.save([*store.viewers(), Viewer(name="bob")])
    viewers = ensure_sync_ids(store, _ids())
    assert viewers[0].sync_id == "kv-test0001"
    assert viewers[1].sync_id == "kv-test0002"


def test_viewer_removed_and_readded_keeps_id(tmp_path) -> None:
    store = _store(tmp_path, [Viewer(name="Anna", sync_id="kv-test0009"), Viewer(name="bob", sync_id="kv-test0008")])
    store.save([Viewer(name="bob", sync_id="kv-test0008")])
    store.save([Viewer(name="bob", sync_id="kv-test0008"), Viewer(name="anna")])
    viewers = ensure_sync_ids(store, _ids())
    assert [v.sync_id for v in viewers] == ["kv-test0008", "kv-test0009"]


def test_a_retired_id_in_use_is_not_reused(tmp_path) -> None:
    store = _store(tmp_path, [Viewer(name="anna", sync_id="kv-test0009")])
    store.save([])
    store.save([Viewer(name="carol", sync_id="kv-test0009"), Viewer(name="anna")])
    viewers = ensure_sync_ids(store, _ids())
    assert viewers[1].sync_id == "kv-test0001"


def test_an_invalid_id_is_replaced_and_a_clash_skipped(tmp_path) -> None:
    store = _store(tmp_path, [Viewer(name="anna", sync_id="Bad Id!"), Viewer(name="bob", sync_id="kv-test0001")])
    viewers = ensure_sync_ids(store, _ids())
    assert viewers[0].sync_id == "kv-test0002"


def test_nothing_saved_when_every_viewer_has_an_id(tmp_path) -> None:
    store = _store(tmp_path, [Viewer(name="anna", sync_id="kv-test0001")])
    before = (tmp_path / "viewers.json").stat().st_mtime_ns
    ensure_sync_ids(store, _ids())
    assert (tmp_path / "viewers.json").stat().st_mtime_ns == before
