import resources.lib.service.main as main_mod


def test_an_invalid_webhook_url_falls_back_to_logging_rather_than_posting():
    from resources.lib.log import get_logger
    from resources.lib.reporter import LogReporter

    sink = main_mod._build_sink("file:///etc/passwd", get_logger("test"))
    assert isinstance(sink, LogReporter)


def test_no_webhook_url_uses_the_log_reporter():
    from resources.lib.log import get_logger
    from resources.lib.reporter import LogReporter

    assert isinstance(main_mod._build_sink(None, get_logger("test")), LogReporter)


def test_a_valid_url_builds_an_http_reporter():
    from resources.lib.log import get_logger
    from resources.lib.reporter import HttpReporter

    sink = main_mod._build_sink("http://host:8787/webhook/kodi?profile=t", get_logger("test"))
    assert isinstance(sink, HttpReporter)


def test_the_shutdown_budget_fits_inside_kodis_kill_window():
    from resources.lib.constants import SHUTDOWN_DRAIN_SECONDS, SHUTDOWN_HTTP_TIMEOUT_SECONDS

    # Kodi injects SystemExit 5000ms after abort (PythonInvoker.cpp:62).
    assert SHUTDOWN_DRAIN_SECONDS + SHUTDOWN_HTTP_TIMEOUT_SECONDS < 5.0
