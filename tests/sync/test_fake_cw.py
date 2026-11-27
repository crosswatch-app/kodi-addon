from __future__ import annotations

import threading

import pytest

from resources.lib.sync.client import SyncClient, SyncError
from tests.sync.fake_cw import Reply, ScriptedCrossWatch, error, ok


def _client(cw: ScriptedCrossWatch) -> SyncClient:
    return SyncClient("http://cw/webhook/kodiwatcher", token="t", device_id="d", abort=threading.Event(),
                      connection_factory=cw.connection_factory)  # fmt: skip


def test_scripted_replies_in_order_then_ok() -> None:
    cw = ScriptedCrossWatch()
    cw.script("upload", ok(mode="full", complete=False), error(409, "upload_stale"))
    client = _client(cw)
    assert client.upload(b'{"mode": "full"}')["complete"] is False
    with pytest.raises(SyncError) as caught:
        client.upload(b'{"mode": "full"}')
    assert caught.value.code == "upload_stale"
    assert client.upload(b"{}") == {"ok": True}


def test_requests_recorded_with_bodies_and_query() -> None:
    cw = ScriptedCrossWatch()
    client = _client(cw)
    client.upload(b'{"mode": "delta", "upload_seq": 4}')
    client.queue("kv-1", after=7)
    [upload] = cw.sent_to("upload")
    assert upload.body == {"mode": "delta", "upload_seq": 4} and upload.headers["X-CrossWatch-Device"] == "d"
    [queue] = cw.sent_to("queue")
    assert queue.query == {"viewer_id": "kv-1", "after": "7", "limit": "500"} and queue.body is None


def test_lost_reply_and_retry_after() -> None:
    cw = ScriptedCrossWatch()
    cw.script("queue/ack", Reply(lost=True), error(503, "temporarily_unavailable", retryable=True, retry_after="20"))
    client = _client(cw)
    with pytest.raises(SyncError) as lost:
        client.ack("kv-1", [])
    assert lost.value.code == "lost_reply" and len(cw.sent_to("queue/ack")) == 1
    with pytest.raises(SyncError) as busy:
        client.ack("kv-1", [])
    assert busy.value.retry_after == 20.0 and busy.value.from_crosswatch
