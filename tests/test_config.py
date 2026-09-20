from resources.lib.config import Settings, read_settings
from tests.fakes import FakeKodi


def test_defaults_when_nothing_is_configured():
    got: Settings = read_settings(FakeKodi())
    assert got.progress_step == 25
    assert got.index_ttl_seconds == 3600
    assert got.movie_prompts is True
    assert got.debug_logging is False
    assert got.webhook_url() is None


def test_webhook_url_assembles_base_and_token():
    kodi = FakeKodi(settings={"webhook_base_url": "http://host:8787/webhook/kodiwatcher", "webhook_token": "tok"})
    assert read_settings(kodi).webhook_url() == "http://host:8787/webhook/kodiwatcher?token=tok"


def test_webhook_url_is_none_without_a_base():
    assert read_settings(FakeKodi(settings={"webhook_token": "tok"})).webhook_url() is None


def test_webhook_url_is_none_without_a_token():
    kodi = FakeKodi(settings={"webhook_base_url": "http://host/webhook/kodiwatcher"})
    assert read_settings(kodi).webhook_url() is None


def test_a_token_with_url_unsafe_characters_is_encoded():
    kodi = FakeKodi(settings={"webhook_base_url": "http://host/webhook/kodiwatcher", "webhook_token": "a b&c#d"})
    assert read_settings(kodi).webhook_url() == "http://host/webhook/kodiwatcher?token=a+b%26c%23d"


def test_a_base_url_with_a_trailing_slash_does_not_double_up():
    kodi = FakeKodi(settings={"webhook_base_url": "http://host/webhook/kodiwatcher/", "webhook_token": "tok"})
    assert read_settings(kodi).webhook_url() == "http://host/webhook/kodiwatcher?token=tok"


def test_movie_prompts_can_be_switched_off():
    kodi = FakeKodi(settings={"movie_prompts": "false"})
    assert read_settings(kodi).movie_prompts is False


def test_progress_step_falls_back_when_unset_or_zero():
    assert read_settings(FakeKodi(settings={"progress_step": "0"})).progress_step == 25


def test_device_name_falls_back_to_the_friendly_name():
    kodi = FakeKodi(info_labels={"System.FriendlyName": "Living room"})
    assert read_settings(kodi).device_name == "Living room"


def test_settings_is_frozen():
    got = read_settings(FakeKodi())
    try:
        got.progress_step = 5  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Settings must be immutable")
