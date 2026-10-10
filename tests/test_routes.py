import json

from resources.lib.routes import RouteFacts, parse_routes
from resources.lib.storage import RouteStore


def test_a_ping_reply_gives_the_route_count_and_every_name_a_route_accepts():
    reply = {
        "ok": True,
        "routes": [
            {"id": "R1", "sink": "trakt", "label": "Trakt Anna", "viewers": ["anna"]},
            {"id": "R2", "sink": "trakt", "label": "Trakt Tom", "viewers": ["tom", "anna"]},
        ],
    }
    assert parse_routes(reply) == RouteFacts(count=2, accepted=frozenset({"anna", "tom"}))


def test_no_routes_yet_accepts_nobody():
    assert parse_routes({"ok": True, "routes": []}) == RouteFacts(count=0, accepted=frozenset())


def test_a_reply_without_routes_says_nothing():
    """An older CrossWatch: no claim either way, so no viewer gets tagged."""
    assert parse_routes({"ok": True}) is None
    assert parse_routes({"ok": True, "routes": "nope"}) is None


def test_malformed_route_entries_are_skipped():
    reply = {"routes": ["x", {"viewers": "anna"}, {"viewers": ["bob", 3, ""]}]}
    assert parse_routes(reply) == RouteFacts(count=2, accepted=frozenset({"bob"}))


def test_the_store_keeps_no_route_labels(tmp_path):
    """Labels name destination accounts ("Trakt Anna"); only counts and our own names are kept."""
    store = RouteStore(str(tmp_path / "routes.json"))
    store.save("fp1", RouteFacts(count=2, accepted=frozenset({"tom", "anna"})))
    assert json.loads((tmp_path / "routes.json").read_text()) == {
        "fingerprint": "fp1",
        "routes": 2,
        "accepted": ["anna", "tom"],
    }


def test_facts_are_read_back_only_for_the_same_pairing(tmp_path):
    store = RouteStore(str(tmp_path / "routes.json"))
    store.save("fp1", RouteFacts(count=1, accepted=frozenset({"anna"})))
    assert store.load("fp1") == RouteFacts(count=1, accepted=frozenset({"anna"}))
    assert store.load("fp2") is None


def test_a_missing_or_broken_file_says_nothing(tmp_path):
    store = RouteStore(str(tmp_path / "routes.json"))
    assert store.load("fp1") is None
    (tmp_path / "routes.json").write_text("{nope")
    assert store.load("fp1") is None
