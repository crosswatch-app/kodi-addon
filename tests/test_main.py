import threading

import resources.lib.service.main as main_mod
from resources.lib.config import KEY_BASE_URL, KEY_DEVICE_ID, KEY_TOKEN, Settings, read_settings
from resources.lib.constants import SHUTDOWN_DRAIN_SECONDS, SHUTDOWN_HTTP_TIMEOUT_SECONDS
from resources.lib.log import get_logger
from resources.lib.models import Device
from resources.lib.reporter import HttpReporter, LogReporter
from resources.lib.service.delivery import Delivery
from tests.fakes import FakeKodi

URL = "http://nas:8787/webhook/kodiwatcher"


def test_the_shutdown_budget_fits_inside_kodis_kill_window():
    # Kodi injects SystemExit 5000ms after abort (PythonInvoker.cpp:62). This now constrains
    # the timeout _connect actually uses once abort is set, not a constant nothing reads.
    assert SHUTDOWN_DRAIN_SECONDS + SHUTDOWN_HTTP_TIMEOUT_SECONDS < 5.0


class Controller:
    def __init__(self) -> None:
        self.settings: list[Settings] = []
        self.pings = 0

    def on_settings_changed(self, settings, thresholds) -> None:
        self.settings.append(settings)

    def ping_now(self) -> None:
        self.pings += 1


class Queue:
    def __init__(self, device: Device) -> None:
        self.routes: list[tuple] = []
        self.device = device

    def set_device(self, device: Device) -> None:
        self.device = device

    def replace(self, sink, outbox) -> None:
        self.routes.append((sink, outbox))


def _apply(tmp_path, kodi, delivery=None, queue=None):
    # The device the settings already give, so only a change to it counts.
    controller, queue = Controller(), queue or Queue(main_mod._device(kodi, read_settings(kodi)))
    delivery = delivery or Delivery(kodi, str(tmp_path / "outbox.json"), threading.Event())
    main_mod.apply_settings(kodi, controller, queue, delivery, get_logger("test"))  # type: ignore[arg-type]
    return controller, queue, delivery


def test_a_new_connection_swaps_the_sink_and_pings_at_once(tmp_path):
    kodi = FakeKodi(settings={KEY_BASE_URL: URL, KEY_TOKEN: "tok"})
    controller, queue, _ = _apply(tmp_path, kodi)
    assert [type(sink) for sink, _ in queue.routes] == [HttpReporter]
    assert controller.pings == 1


def test_a_settings_change_that_is_not_the_connection_neither_swaps_nor_pings(tmp_path):
    kodi = FakeKodi(settings={KEY_BASE_URL: URL, KEY_TOKEN: "tok"})
    delivery = Delivery(kodi, str(tmp_path / "outbox.json"), threading.Event())
    delivery.build(Settings(webhook_base_url=URL, webhook_token="tok"))
    kodi.settings["debug_logging"] = "true"
    controller, queue, _ = _apply(tmp_path, kodi, delivery)
    assert queue.routes == []
    assert controller.pings == 0
    assert controller.settings[-1].debug_logging is True


def test_clearing_the_connection_swaps_to_logging(tmp_path):
    kodi = FakeKodi(settings={KEY_BASE_URL: URL, KEY_TOKEN: "tok"})
    _, _, delivery = _apply(tmp_path, kodi)
    kodi.settings[KEY_TOKEN] = ""
    _, queue, _ = _apply(tmp_path, kodi, delivery)
    assert [type(sink) for sink, _ in queue.routes] == [LogReporter]


def test_a_pasted_url_is_written_back_split_and_used_at_once(tmp_path):
    kodi = FakeKodi(settings={KEY_BASE_URL: f"{URL}?token=pasted"})
    controller, queue, _ = _apply(tmp_path, kodi)
    assert kodi.settings[KEY_BASE_URL] == URL
    assert kodi.settings[KEY_TOKEN] == "pasted"
    sink = queue.routes[0][0]
    assert isinstance(sink, HttpReporter) and sink._headers["X-CrossWatch-Token"] == "pasted"
    assert "token" not in sink._path


class _Monitor:
    """Stands in for ServiceMonitor with Kodi already shutting down, so main() returns."""

    def __init__(self, controller, on_settings_changed) -> None:
        del controller, on_settings_changed

    def abortRequested(self) -> bool:
        return True


def test_the_service_starts_with_a_stored_address_it_cannot_use(tmp_path, monkeypatch):
    kodi = FakeKodi(settings={KEY_BASE_URL: "http://nas:99999/webhook/kodiwatcher", KEY_TOKEN: "tok"}, root=str(tmp_path))
    monkeypatch.setattr(main_mod, "KodiRuntime", lambda player: kodi)
    monkeypatch.setattr(main_mod, "ServiceMonitor", _Monitor)
    main_mod.main()
    logged = [message for message, _ in kodi.logged]
    assert any("service.webhook_url_rejected" in message for message in logged)
    assert any("service.stopping" in message for message in logged)


def test_device_id_reread_on_settings_change(tmp_path):
    kodi = FakeKodi(settings={KEY_DEVICE_ID: "htpc-1"}, info_labels={"System.FriendlyName": "Lounge"}, root=str(tmp_path))
    delivery = Delivery(kodi, str(tmp_path / "outbox.json"), threading.Event())
    delivery.build(Settings())
    queue = Queue(Device(id="htpc-1", name="Lounge", addon_version="0.1.0"))
    controller, _, _ = _apply(tmp_path, kodi, delivery, queue)
    assert controller.pings == 0
    kodi.settings[KEY_DEVICE_ID] = "htpc-2"
    controller, _, _ = _apply(tmp_path, kodi, delivery, queue)
    assert queue.device == Device(id="htpc-2", name="Lounge", addon_version="0.1.0")
    assert controller.pings == 1


def test_a_failing_device_lookup_still_applies_the_new_connection(tmp_path, monkeypatch):
    kodi = FakeKodi(settings={KEY_BASE_URL: URL, KEY_TOKEN: "tok"})
    queue = Queue(Device(id="", name=""))

    def broken(kodi, settings):
        raise RuntimeError("device lookup failed")

    monkeypatch.setattr(main_mod, "_device", broken)
    controller = Controller()
    delivery = Delivery(kodi, str(tmp_path / "outbox.json"), threading.Event())
    try:
        main_mod.apply_settings(kodi, controller, queue, delivery, get_logger("test"))  # type: ignore[arg-type]
    except RuntimeError:
        pass
    assert [type(sink) for sink, _ in queue.routes] == [HttpReporter]
