from resources.lib.config import Settings, read_settings, split_token, write_back_pasted_url
from tests.fakes import FakeKodi


def test_defaults_when_nothing_is_configured():
    got: Settings = read_settings(FakeKodi())
    assert got.progress_interval_seconds == 60
    assert got.index_ttl_seconds == 3600
    assert got.movie_prompts is True
    assert got.skip_pkc is True
    assert got.debug_logging is False
    assert got.webhook_url() is None


def test_webhook_url_is_the_plain_endpoint_without_the_token():
    kodi = FakeKodi(settings={"webhook_base_url": "http://host:8787/webhook/kodiwatcher", "webhook_token": "tok"})
    assert read_settings(kodi).webhook_url() == "http://host:8787/webhook/kodiwatcher"


def test_webhook_url_is_none_without_a_base():
    assert read_settings(FakeKodi(settings={"webhook_token": "tok"})).webhook_url() is None


def test_webhook_url_is_none_without_a_token():
    kodi = FakeKodi(settings={"webhook_base_url": "http://host/webhook/kodiwatcher"})
    assert read_settings(kodi).webhook_url() is None


def test_a_base_url_with_a_trailing_slash_loses_it():
    kodi = FakeKodi(settings={"webhook_base_url": "http://host/webhook/kodiwatcher/", "webhook_token": "tok"})
    assert read_settings(kodi).webhook_url() == "http://host/webhook/kodiwatcher"


def test_a_pasted_url_is_split_into_endpoint_and_token():
    kodi = FakeKodi(settings={"webhook_base_url": "http://host:8787/webhook/kodiwatcher?token=a%26b"})
    got = read_settings(kodi)
    assert got.webhook_base_url == "http://host:8787/webhook/kodiwatcher"
    assert got.webhook_token == "a&b"


def test_a_pasted_token_wins_over_the_stored_one():
    kodi = FakeKodi(settings={"webhook_base_url": "http://host/webhook/kodiwatcher?token=new", "webhook_token": "old"})
    assert read_settings(kodi).webhook_token == "new"


def test_split_token_keeps_other_query_parameters():
    assert split_token("http://host/hook?a=1&token=t&b=2") == ("http://host/hook?a=1&b=2", "t")


def test_split_token_leaves_a_url_without_a_token_alone():
    assert split_token("http://host/hook?a=1") == ("http://host/hook?a=1", "")


def test_split_token_survives_an_unparseable_url():
    assert split_token("http://[::1/hook?token=t") == ("http://[::1/hook?token=t", "")


def test_the_write_back_stores_token_then_plain_url_and_only_once():
    kodi = FakeKodi(settings={"webhook_base_url": "http://host/webhook/kodiwatcher?token=tok"})
    assert write_back_pasted_url(kodi, read_settings(kodi)) is True
    assert kodi.writes == [("webhook_token", "tok"), ("webhook_base_url", "http://host/webhook/kodiwatcher")]
    assert write_back_pasted_url(kodi, read_settings(kodi)) is False
    assert len(kodi.writes) == 2


def test_movie_prompts_can_be_switched_off():
    kodi = FakeKodi(settings={"movie_prompts": "false"})
    assert read_settings(kodi).movie_prompts is False


def test_progress_interval_falls_back_when_unset_or_zero():
    assert read_settings(FakeKodi(settings={"progress_interval_seconds": "0"})).progress_interval_seconds == 60


def test_progress_interval_is_read_from_settings():
    kodi = FakeKodi(settings={"progress_interval_seconds": "30"})
    assert read_settings(kodi).progress_interval_seconds == 30


def test_pkc_playback_is_skipped_by_default():
    assert read_settings(FakeKodi()).skip_pkc is True


def test_pkc_skipping_can_be_switched_off():
    assert read_settings(FakeKodi(settings={"skip_pkc": "false"})).skip_pkc is False


def test_device_name_falls_back_to_the_friendly_name():
    kodi = FakeKodi(info_labels={"System.FriendlyName": "Living room"})
    assert read_settings(kodi).device_name == "Living room"


def test_settings_is_frozen():
    got = read_settings(FakeKodi())
    try:
        got.progress_interval_seconds = 5  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Settings must be immutable")
