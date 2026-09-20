import threading

import resources.lib.service.main as main_mod
from resources.lib.log import get_logger
from resources.lib.reporter import HttpReporter, LogReporter


def _sink(url):
    return main_mod._build_sink(url, get_logger("test"), token="t", abort=threading.Event())


def test_an_invalid_webhook_url_falls_back_to_logging_rather_than_posting():
    assert isinstance(_sink("file:///etc/passwd"), LogReporter)


def test_no_webhook_url_uses_the_log_reporter():
    assert isinstance(_sink(None), LogReporter)


def test_a_valid_url_builds_an_http_reporter():
    assert isinstance(_sink("http://host:8787/webhook/kodiwatcher?token=t"), HttpReporter)


def test_the_reporter_is_given_the_abort_event_the_queue_will_set():
    abort = threading.Event()
    sink = main_mod._build_sink(
        "http://host/webhook/kodiwatcher?token=t", get_logger("test"), token="t", abort=abort
    )
    assert isinstance(sink, HttpReporter)
    assert sink._abort is abort


def test_the_shutdown_budget_fits_inside_kodis_kill_window():
    from resources.lib.constants import SHUTDOWN_DRAIN_SECONDS, SHUTDOWN_HTTP_TIMEOUT_SECONDS

    # Kodi injects SystemExit 5000ms after abort (PythonInvoker.cpp:62).
    assert SHUTDOWN_DRAIN_SECONDS + SHUTDOWN_HTTP_TIMEOUT_SECONDS < 5.0
