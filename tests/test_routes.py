import json

from resources.lib.routes import Person, RouteFacts, parse_people, parse_routes
from resources.lib.storage import RouteStore


def test_a_ping_reply_gives_the_route_count_and_every_name_a_route_accepts():
    reply = {
        "ok": True,
        "routes": [
            {"id": "R1", "sink": "trakt", "label": "Trakt Anna", "viewers": ["anna"]},
            {"id": "R2", "sink": "trakt", "label": "Trakt Tom", "viewers": ["tom", "anna"]},
        ],
    }
    facts = parse_routes(reply, asked=("anna", "tom", "zoe"))
    assert facts == RouteFacts(count=2, accepted=frozenset({"anna", "tom"}), asked=frozenset({"anna", "tom", "zoe"}))


def test_no_routes_yet_accepts_nobody():
    assert parse_routes({"ok": True, "routes": []}, asked=("anna",)) == RouteFacts(0, frozenset(), frozenset({"anna"}))


def test_a_reply_without_routes_says_nothing():
    """An older CrossWatch: no claim either way, so no viewer gets tagged."""
    assert parse_routes({"ok": True}, asked=("anna",)) is None
    assert parse_routes({"ok": True, "routes": "nope"}, asked=("anna",)) is None


def test_malformed_route_entries_are_skipped():
    reply = {"routes": ["x", {"viewers": "anna"}, {"viewers": ["bob", 3, ""]}]}
    assert parse_routes(reply, asked=("bob",)) == RouteFacts(count=2, accepted=frozenset({"bob"}), asked=frozenset({"bob"}))


def test_the_store_keeps_no_route_labels(tmp_path):
    """Labels name destination accounts ("Trakt Anna"); only counts and our own names are kept."""
    store = RouteStore(str(tmp_path / "routes.json"))
    store.save("fp1", RouteFacts(count=2, accepted=frozenset({"tom", "anna"}), asked=frozenset({"zoe", "anna"})))
    assert json.loads((tmp_path / "routes.json").read_text()) == {
        "fingerprint": "fp1",
        "routes": 2,
        "accepted": ["anna", "tom"],
        "asked": ["anna", "zoe"],
    }


def test_facts_are_read_back_only_for_the_same_pairing(tmp_path):
    store = RouteStore(str(tmp_path / "routes.json"))
    facts = RouteFacts(count=1, accepted=frozenset({"anna"}), asked=frozenset({"anna", "bob"}))
    store.save("fp1", facts)
    assert store.load("fp1") == facts
    assert store.load("fp2") is None


def test_a_missing_or_broken_file_says_nothing(tmp_path):
    store = RouteStore(str(tmp_path / "routes.json"))
    assert store.load("fp1") is None
    (tmp_path / "routes.json").write_text("{nope")
    assert store.load("fp1") is None


def test_no_people_list_is_none_not_empty():
    assert parse_people({"routes": []}) is None


def test_an_empty_people_list_is_empty():
    assert parse_people({"people": []}) == ()


def test_people_keep_id_and_name_and_skip_what_is_malformed():
    reply = {"people": [
        {"id": "p-anna", "name": "Anna"},
        {"id": "", "name": "Nobody"},
        {"id": "p-x"},
        "Ben",
        {"id": "p-anna", "name": "Anna again"},
        {"id": "p-tom", "name": " Tom "},
    ]}
    assert parse_people(reply) == (Person("p-anna", "Anna"), Person("p-tom", "Tom"))


def test_route_facts_carry_the_people():
    facts = parse_routes({"routes": [], "people": [{"id": "p1", "name": "Anna"}]}, ["anna"])
    assert facts is not None and facts.people == (Person("p1", "Anna"),)


def test_route_facts_from_an_older_crosswatch_have_no_people():
    facts = parse_routes({"routes": []}, [])
    assert facts is not None and facts.people is None
