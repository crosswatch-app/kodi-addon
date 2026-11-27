from __future__ import annotations

from resources.lib.sync.reply import PingTicket, ReplyViewer, connection_id, parse_reply


def test_connection_id_origin_and_path_without_query() -> None:
    assert connection_id("https://User:pw@CW.example:8443/cw/webhook/kodiwatcher/?token=x#f") == (
        "https://cw.example:8443/cw/webhook/kodiwatcher"
    )
    assert connection_id("http://10.0.0.5/webhook/kodiwatcher") == "http://10.0.0.5/webhook/kodiwatcher"


def test_parse_reply_viewers_and_reason() -> None:
    reply = parse_reply(
        {
            "version": 1,
            "enabled": True,
            "viewers": [
                {"viewer_id": "kv-1", "instance": "default", "upload_seq": 41},
                {"viewer_id": "kv-2", "instance": "default~kv-2"},
                {"viewer_id": "", "instance": "x"},
                "junk",
            ],
            "queue_pending": 3,
        }
    )
    assert reply is not None and reply.enabled and reply.reason is None
    assert reply.viewers == {"kv-1": ReplyViewer("default", 41), "kv-2": ReplyViewer("default~kv-2", 0)}
    assert reply.queue_pending == 3
    off = parse_reply({"version": 1, "enabled": False, "reason": "not_sync_device", "viewers": [], "queue_pending": 0})
    assert off is not None and not off.enabled and off.reason == "not_sync_device"


def test_unsupported_version_means_off() -> None:
    assert parse_reply(None) is None
    assert parse_reply({"enabled": True}) is None
    assert parse_reply({"version": 2, "enabled": True}) is None
    assert parse_reply({"version": True, "enabled": True}) is None


def test_bad_values_are_safe() -> None:
    reply = parse_reply({"version": 1, "enabled": "yes", "queue_pending": -4, "viewers": None})
    assert reply is not None and not reply.enabled and reply.queue_pending == 0 and reply.viewers == {}


def test_ticket_defaults_to_no_epochs() -> None:
    assert PingTicket(sent_at=1.0).epochs == {}


def test_connection_id_folds_default_ports() -> None:
    assert connection_id("https://cw:443/x") == connection_id("https://cw/x")
    assert connection_id("http://cw:80/x") == connection_id("http://cw/x")
    assert connection_id("http://cw:8080/x") != connection_id("http://cw/x")


def test_viewers_not_a_list_and_negative_seq() -> None:
    for junk in (5, True, 1.5, {"viewer_id": "kv-1"}):
        reply = parse_reply({"version": 1, "enabled": True, "viewers": junk})
        assert reply is not None and reply.viewers == {}
    reply = parse_reply({"version": 1, "viewers": [{"viewer_id": "kv-1", "instance": "d", "upload_seq": -7}]})
    assert reply is not None and reply.viewers["kv-1"].upload_seq == 0


def test_viewer_next_change_and_run_pending() -> None:
    reply = parse_reply(
        {
            "version": 1,
            "enabled": True,
            "viewers": [
                {"viewer_id": "kv-1", "instance": "default", "upload_seq": 41, "next_change": 1763560000123,
                 "run_pending": True},
                {"viewer_id": "kv-2", "instance": "default~kv-2", "next_change": "x", "run_pending": "true"},
            ],
        }
    )  # fmt: skip
    assert reply is not None
    assert reply.viewers["kv-1"] == ReplyViewer("default", 41, next_change=1763560000123, run_pending=True)
    assert reply.viewers["kv-2"] == ReplyViewer("default~kv-2", 0, next_change=0, run_pending=False)


def test_library_has_sync_device_reason() -> None:
    reply = parse_reply({"version": 1, "enabled": False, "reason": "library_has_sync_device", "viewers": []})
    assert reply is not None and not reply.enabled and reply.reason == "library_has_sync_device"
