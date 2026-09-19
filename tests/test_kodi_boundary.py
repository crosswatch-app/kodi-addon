from resources.lib.kodi import WINDOW_INVALID, KodiApi
from tests.fakes import FakeKodi


def test_fake_satisfies_the_protocol():
    # Fails type checking the moment KodiApi grows a method FakeKodi lacks.
    checked: KodiApi = FakeKodi()
    assert checked is not None


def test_fake_records_calls_and_returns_configured_results():
    kodi = FakeKodi(rpc_handlers={"Profiles.GetCurrentProfile": lambda params: {"label": "Master user"}})
    assert kodi.jsonrpc("Profiles.GetCurrentProfile")["label"] == "Master user"
    assert kodi.calls == [("Profiles.GetCurrentProfile", {})]


def test_fake_raises_for_unstubbed_methods():
    try:
        FakeKodi().jsonrpc("VideoLibrary.GetTVShows")
    except AssertionError as exc:
        assert "VideoLibrary.GetTVShows" in str(exc)
    else:
        raise AssertionError("expected an AssertionError for an unstubbed method")


def test_player_times_returns_both_when_both_are_available():
    assert FakeKodi(position_ms=30_000, duration_ms=120_000).player_times() == (30_000, 120_000)


def test_player_times_keeps_the_position_when_the_duration_read_fails():
    kodi = FakeKodi(position_ms=30_000, duration_raises=True)
    assert kodi.player_times() == (30_000, None)


def test_player_times_is_all_none_when_nothing_is_playing():
    assert FakeKodi(position_raises=True, duration_raises=True).player_times() == (None, None)


def test_no_dialog_open_reports_the_invalid_window_id():
    assert FakeKodi().topmost_dialog_id() == WINDOW_INVALID


def test_multiselect_records_preselect_and_autoclose():
    kodi = FakeKodi(multiselect_answer=[1])
    kodi.multiselect("Who watched?", ["anna", "bob"], preselect=[0], autoclose=120)
    assert kodi.multiselect_calls == [("Who watched?", ["anna", "bob"], [0], 120)]


def test_read_text_honours_a_size_cap():
    kodi = FakeKodi(files={"special://profile/advancedsettings.xml": "x" * 100})
    assert kodi.read_text("special://profile/advancedsettings.xml", max_bytes=10) == "x" * 10
