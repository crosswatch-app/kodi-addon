from collections.abc import Callable, Mapping
from typing import Any

import pytest

from resources.lib.config import Settings
from resources.lib.kodi import KodiRpcError
from resources.lib.models import Viewer
from resources.lib.storage import JsonViewerStore
from resources.lib.sync.files import StartedFile, write_started
from resources.lib.sync.ping import (
    PingBlocks,
    block_state,
    device_id_changed,
    jsonrpc_version,
    library_sync_block,
    pause_reason,
)
from resources.lib.sync.reply import SyncReply, connection_id
from tests.fakes import FakeKodi

URL = "http://nas:8787/webhook/kodiwatcher"
CONNECTION = connection_id(URL)
VIEWERS = [Viewer(name="anna", sync_id="kv-a"), Viewer(name="ben", sync_id="kv-b")]


def _started(**fields: Any) -> StartedFile:
    return StartedFile(connection=fields.pop("connection", CONNECTION), start_id="s-1", **fields)


def _reply(reason: str | None = None, enabled: bool = False) -> SyncReply:
    return SyncReply(enabled=enabled, reason=reason, viewers={}, queue_pending=0)


# -- block_state --------------------------------------------------------------------------


def test_block_absent_before_start():
    assert block_state(None, CONNECTION, sync_enabled=True, device_id="box") == "absent"


def test_block_absent_for_other_connection():
    started = _started(connection=connection_id("http://other:8787/webhook/kodiwatcher"))
    assert block_state(started, CONNECTION, sync_enabled=True, device_id="box") == "absent"


def test_block_enabled_after_start():
    assert block_state(_started(), CONNECTION, sync_enabled=True, device_id="box") == "enabled"


def test_block_enabled_false_after_turning_off():
    """Still a block: CrossWatch releases the sync device only on enabled false."""
    assert block_state(_started(), CONNECTION, sync_enabled=False, device_id="box") == "disabled"


def test_block_disabled_while_paused():
    assert block_state(_started(paused=True), CONNECTION, sync_enabled=True, device_id="box") == "disabled"


def test_default_port_is_the_same_connection():
    started = _started(connection=connection_id("http://nas:80/webhook/kodiwatcher"))
    assert (
        block_state(started, connection_id("http://nas/webhook/kodiwatcher"), sync_enabled=True, device_id="box")
        == "enabled"
    )


# -- library_sync_block -------------------------------------------------------------------


def test_block_shape_in_viewer_order():
    block = library_sync_block(True, VIEWERS, "lib-1", "21.2", "13.5.0")
    assert block == {
        "version": 1,
        "enabled": True,
        "viewers": [{"viewer_id": "kv-a", "name": "anna"}, {"viewer_id": "kv-b", "name": "ben"}],
        "library_id": "lib-1",
        "kodi_version": "21.2",
        "jsonrpc_version": "13.5.0",
    }


def test_library_id_failure_leaves_key_out():
    block = library_sync_block(False, VIEWERS, "", "21.2", "13.5.0")
    assert "library_id" not in block
    assert block["enabled"] is False


def test_viewer_without_sync_id_left_out():
    block = library_sync_block(True, [Viewer(name="new"), *VIEWERS], "lib-1", "21.2", "13.5.0")
    assert [v["viewer_id"] for v in block["viewers"]] == ["kv-a", "kv-b"]


# -- jsonrpc_version ----------------------------------------------------------------------


def test_jsonrpc_version_string():
    kodi = FakeKodi(rpc_handlers={"JSONRPC.Version": lambda p: {"version": {"major": 13, "minor": 5, "patch": 0}}})
    assert jsonrpc_version(kodi) == "13.5.0"


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"version": "13.5"},
        {"version": {"major": 13, "minor": 5}},
        {"version": {"major": True, "minor": 5, "patch": 0}},
    ],
)
def test_jsonrpc_version_unusable_answer_is_empty(answer):
    kodi = FakeKodi(rpc_handlers={"JSONRPC.Version": lambda p: answer})
    assert jsonrpc_version(kodi) == ""


def test_jsonrpc_version_error_is_empty():
    def fails(params):
        raise KodiRpcError("JSONRPC.Version", {"code": -32601})

    assert jsonrpc_version(FakeKodi(rpc_handlers={"JSONRPC.Version": fails})) == ""


# -- pausing ------------------------------------------------------------------------------


def test_pause_only_on_observed_turn_off():
    disabled = {"version": 1, "enabled": False}
    # Start turns the setting on before it writes the started file, so a block can carry
    # enabled false right after Start without the household having turned anything off.
    assert pause_reason(disabled, _reply(), turned_off=False) is None
    assert pause_reason(disabled, _reply(), turned_off=True) == "turned_off"
    # Only once CrossWatch answered: an unanswered ping released nothing.
    assert pause_reason(disabled, None, turned_off=True) is None
    assert pause_reason({"version": 1, "enabled": True}, _reply(), turned_off=True) is None


def test_not_sync_device_pauses():
    block = {"version": 1, "enabled": True}
    assert pause_reason(block, _reply("not_sync_device"), turned_off=False) == "not_sync_device"


@pytest.mark.parametrize("reason", ["library_has_sync_device", "device_id_missing", None])
def test_other_replies_do_not_pause(reason):
    assert pause_reason({"version": 1, "enabled": True}, _reply(reason), turned_off=False) is None


def test_no_block_never_pauses():
    assert pause_reason(None, _reply("not_sync_device"), turned_off=True) is None


def test_device_id_change_never_releases_automatically(tmp_path):
    """A changed id asks for nothing until Start, and has no way to release the old id."""
    path = str(tmp_path / "started.json")
    write_started(path, _started(bound_device="old-id"))
    blocks, _ = _blocks(tmp_path, path, device_id="new-id")
    built = blocks(SETTINGS)
    assert built is not None and built[0]["enabled"] is False


def test_changed_device_id_is_disabled_until_start():
    assert block_state(_started(bound_device="old-id"), CONNECTION, sync_enabled=True, device_id="new-id") == "disabled"
    assert block_state(_started(bound_device="box"), CONNECTION, sync_enabled=True, device_id="box") == "enabled"
    # Start clears bound_device, so a new id asks for sync again.
    assert block_state(_started(), CONNECTION, sync_enabled=True, device_id="new-id") == "enabled"


def test_changed_id_status_only_on_not_sync_device():
    started = _started(bound_device="old-id")
    assert device_id_changed(_reply("not_sync_device"), "new-id", started) is True
    assert device_id_changed(_reply(None, enabled=True), "new-id", started) is False
    assert device_id_changed(_reply("library_has_sync_device"), "new-id", started) is False
    assert device_id_changed(_reply("not_sync_device"), "old-id", started) is False
    # Never bound: another device is the sync device, the id didn't change under it.
    assert device_id_changed(_reply("not_sync_device"), "new-id", _started()) is False
    assert device_id_changed(None, "new-id", started) is False


# -- PingBlocks ---------------------------------------------------------------------------


def _kodi(library_settings: str | None = None) -> FakeKodi:
    return FakeKodi(
        rpc_handlers={"JSONRPC.Version": lambda p: {"version": {"major": 13, "minor": 5, "patch": 0}}},
        info_labels={"System.BuildVersion": "21.2-Omega (21.2.0) Git:20250101"},
        files={"special://profile/advancedsettings.xml": library_settings} if library_settings is not None else {},
    )


def _blocks(
    tmp_path,
    started_path: str,
    kodi: FakeKodi | None = None,
    epochs: Callable[[], Mapping[str, int]] = dict,
    device_id: str = "box",
):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save(VIEWERS)
    kodi = kodi or _kodi()
    clock = iter([10.0, 20.0, 30.0])
    return (
        PingBlocks(kodi, store, started_path, clock=lambda: next(clock), device_id=lambda: device_id, epochs=epochs),
        kodi,
    )


SETTINGS = Settings(webhook_base_url=URL, webhook_token="t", sync_enabled=True)


def test_no_lookups_before_start(tmp_path):
    blocks, kodi = _blocks(tmp_path, str(tmp_path / "started.json"))
    assert blocks(SETTINGS) is None
    assert kodi.calls == []


def test_blocks_for_the_started_connection(tmp_path):
    path = str(tmp_path / "started.json")
    write_started(path, _started())
    blocks, _ = _blocks(tmp_path, path)
    built = blocks(SETTINGS)
    assert built is not None
    block, ticket = built
    assert block["enabled"] is True and block["kodi_version"] == "21.2" and block["jsonrpc_version"] == "13.5.0"
    assert len(block["library_id"]) == 64
    assert ticket.sent_at == 10.0 and ticket.epochs == {} and ticket.connection == CONNECTION
    assert blocks(Settings(webhook_base_url="http://other:8787/webhook/kodiwatcher", sync_enabled=True)) is None


def test_ping_ticket_carries_epochs(tmp_path):
    path = str(tmp_path / "started.json")
    write_started(path, _started())
    published = {"kv-a": 3}
    blocks, _ = _blocks(tmp_path, path, epochs=lambda: published)
    built = blocks(SETTINGS)
    assert built is not None and built[1].epochs == {"kv-a": 3}
    # A copy: the engine replaces its published mapping, the ticket keeps what it saw.
    assert built[1].epochs is not published


def test_unparseable_library_settings_leave_library_id_out(tmp_path):
    path = str(tmp_path / "started.json")
    write_started(path, _started())
    blocks, _ = _blocks(tmp_path, path, kodi=_kodi("<advancedsettings><videodatabase>"))
    built = blocks(SETTINGS)
    assert built is not None and "library_id" not in built[0]


def test_paused_start_sends_disabled(tmp_path):
    path = str(tmp_path / "started.json")
    write_started(path, _started(paused=True, paused_reason="not_sync_device"))
    blocks, _ = _blocks(tmp_path, path)
    built = blocks(SETTINGS)
    assert built is not None and built[0]["enabled"] is False
