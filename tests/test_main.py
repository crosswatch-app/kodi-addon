import threading

import resources.lib.service.main as main_mod
from resources.lib.config import KEY_BASE_URL, KEY_TOKEN, Settings
from resources.lib.constants import SHUTDOWN_DRAIN_SECONDS, SHUTDOWN_HTTP_TIMEOUT_SECONDS
from resources.lib.log import get_logger
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
    def __init__(self) -> None:
        self.routes: list[tuple] = []

    def replace(self, sink, outbox) -> None:
        self.routes.append((sink, outbox))


def _apply(tmp_path, kodi, delivery=None):
    controller, queue = Controller(), Queue()
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
