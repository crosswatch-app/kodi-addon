import threading

from resources.lib.config import KEY_STATUS, Settings
from resources.lib.constants import STATUS_NOT_PAIRED, STATUS_PAIRED
from resources.lib.outbox import Outbox, config_fingerprint
from resources.lib.reporter import HttpReporter, LogReporter
from resources.lib.routes import RouteFacts
from resources.lib.service.delivery import Delivery
from resources.lib.storage import RouteStore
from tests.fakes import FakeKodi

URL = "http://nas:8787/webhook/kodiwatcher"


def _settings(url: str = URL, token: str = "tok") -> Settings:
    return Settings(webhook_base_url=url, webhook_token=token)


def _delivery(tmp_path, kodi=None) -> Delivery:
    return Delivery(kodi or FakeKodi(), str(tmp_path / "outbox.json"), threading.Event())


def test_no_connection_builds_the_log_reporter_and_no_lane(tmp_path):
    sink, lane = _delivery(tmp_path).build(_settings(url=""))
    assert isinstance(sink, LogReporter)
    assert lane is None


def test_a_connection_builds_an_http_reporter_with_the_given_abort_event(tmp_path):
    abort = threading.Event()
    sink, lane = Delivery(FakeKodi(), str(tmp_path / "outbox.json"), abort).build(_settings())
    assert isinstance(sink, HttpReporter)
    assert sink._abort is abort
    assert lane is not None


def test_an_invalid_url_falls_back_to_logging_rather_than_posting(tmp_path):
    sink, lane = _delivery(tmp_path).build(_settings(url="file:///etc/passwd"))
    assert isinstance(sink, LogReporter)
    assert lane is None


def test_the_lane_is_loaded_from_disk_for_the_token(tmp_path):
    Outbox(str(tmp_path / "outbox.json"), config_fingerprint("tok")).add({"event": "stop", "event_id": "e-1"}, "stop")
    _, lane = _delivery(tmp_path).build(_settings())
    assert lane is not None and lane.store.pending() == 1


def test_a_new_address_for_the_same_token_keeps_the_stored_watches(tmp_path):
    delivery = _delivery(tmp_path)
    _, lane = delivery.build(_settings())
    assert lane is not None
    lane.store.add({"event": "stop", "event_id": "e-1"}, "stop")
    _, moved = delivery.build(_settings(url="http://10.0.0.5:8787/webhook/kodiwatcher"))
    assert moved is not None and moved.store is lane.store
    assert moved.store.pending() == 1


def test_a_new_token_drops_the_stored_watches(tmp_path):
    delivery = _delivery(tmp_path)
    _, lane = delivery.build(_settings())
    assert lane is not None
    lane.store.add({"event": "stop", "event_id": "e-1"}, "stop")
    _, other = delivery.build(_settings(token="other"))
    assert other is not None and other.store.pending() == 0


def test_unpair_then_the_same_token_again_still_has_the_stored_watches(tmp_path):
    delivery = _delivery(tmp_path)
    _, lane = delivery.build(_settings())
    assert lane is not None
    lane.store.add({"event": "stop", "event_id": "e-1"}, "stop")
    delivery.build(_settings(url="", token=""))
    _, again = delivery.build(_settings())
    assert again is not None and again.store.pending() == 1


def test_changed_compares_url_and_token_only(tmp_path):
    delivery = _delivery(tmp_path)
    delivery.build(_settings())
    assert delivery.changed(Settings(webhook_base_url=URL, webhook_token="tok", debug_logging=True)) is False
    assert delivery.changed(_settings(token="new")) is True
    assert delivery.changed(_settings(url="http://other/webhook/kodiwatcher")) is True


def test_a_token_without_a_url_counts_as_unconfigured(tmp_path):
    delivery = _delivery(tmp_path)
    delivery.build(_settings(url="", token=""))
    assert delivery.changed(_settings(url="", token="tok")) is False


def test_no_connection_writes_not_paired(tmp_path):
    kodi = FakeKodi()
    _delivery(tmp_path, kodi).build(_settings(url=""))
    assert kodi.settings[KEY_STATUS] == f"#{STATUS_NOT_PAIRED}"


def test_a_ping_reply_becomes_the_status_line_on_publish(tmp_path):
    kodi = FakeKodi()
    delivery = _delivery(tmp_path, kodi)
    sink, _ = delivery.build(_settings())
    assert isinstance(sink, HttpReporter) and sink._on_instance is not None
    sink._on_instance("Living room")  # what the worker does with a ping reply
    assert KEY_STATUS not in kodi.settings
    delivery.publish_status()
    assert kodi.settings[KEY_STATUS] == f"#{STATUS_PAIRED}"


def test_a_ping_reply_from_a_replaced_connection_is_ignored(tmp_path):
    kodi = FakeKodi()
    delivery = _delivery(tmp_path, kodi)
    old, _ = delivery.build(_settings())
    delivery.build(_settings(token="new"))
    assert isinstance(old, HttpReporter) and old._on_instance is not None
    old._on_instance("Old server")
    delivery.publish_status()
    assert KEY_STATUS not in kodi.settings


def test_publish_without_a_reply_writes_nothing(tmp_path):
    kodi = FakeKodi()
    delivery = _delivery(tmp_path, kodi)
    delivery.build(_settings())
    delivery.publish_status()
    assert kodi.writes == []


def _routed(tmp_path):
    store = RouteStore(str(tmp_path / "routes.json"))
    return Delivery(FakeKodi(), str(tmp_path / "outbox.json"), threading.Event(), routes=store), store


def test_route_facts_are_saved_for_the_current_pairing_on_publish(tmp_path):
    delivery, store = _routed(tmp_path)
    sink, _ = delivery.build(_settings())
    assert isinstance(sink, HttpReporter) and sink._on_routes is not None
    sink._on_routes(RouteFacts(count=1, accepted=frozenset({"anna"})))  # the worker thread
    assert store.load(config_fingerprint("tok")) is None
    delivery.publish_status()
    assert store.load(config_fingerprint("tok")) == RouteFacts(count=1, accepted=frozenset({"anna"}))


def test_route_facts_from_a_replaced_connection_are_ignored(tmp_path):
    delivery, store = _routed(tmp_path)
    old, _ = delivery.build(_settings())
    delivery.build(_settings(token="new"))
    assert isinstance(old, HttpReporter) and old._on_routes is not None
    old._on_routes(RouteFacts(count=1, accepted=frozenset({"anna"})))
    delivery.publish_status()
    assert store.load(config_fingerprint("tok")) is None
    assert store.load(config_fingerprint("new")) is None
