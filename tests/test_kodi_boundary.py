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


def test_read_text_honours_a_size_cap():
    kodi = FakeKodi(files={"special://profile/advancedsettings.xml": "x" * 100})
    assert kodi.read_text("special://profile/advancedsettings.xml", max_bytes=10) == "x" * 10


def test_who_watched_records_the_request_and_returns_the_scripted_answer():
    from resources.lib.ui.who_watched import WhoWatchedRequest

    kodi = FakeKodi(who_watched_answer=("anna",))
    request = WhoWatchedRequest(title="t", subtitle="", poster="", names=("anna", "bob"))
    assert kodi.who_watched(request) == ("anna",)
    assert kodi.who_watched_calls == [request]


def test_confirm_window_records_and_answers():
    kodi = FakeKodi(confirm_window_answer=True)
    assert kodi.confirm_window("h", "m") is True
    assert kodi.confirm_window_calls == [("h", "m")]


def test_list_window_returns_scripted_results_then_close():
    from resources.lib.ui.list_window import ListRequest, ListResult, ListState

    request = ListRequest(heading="h", rows=(), count_one="1", count_all="%s", bulk_all="a", bulk_shown="s")
    opened = ListResult("done", ListState(), keys=("k",))
    kodi = FakeKodi()
    kodi.list_window_results = [opened]
    assert kodi.list_window(request, ListState()) == opened
    assert kodi.list_window(request, ListState(search="x")) == ListResult("close", ListState(search="x"))
    assert kodi.list_window_calls == [(request, ListState()), (request, ListState(search="x"))]


def test_viewers_window_returns_scripted_results_then_close():
    from resources.lib.ui.viewers_window import ViewersRequest, ViewersResult

    request = ViewersRequest(heading="h", count="", rows=(), more="", route_ok="", route_missing="")
    kodi = FakeKodi()
    kodi.viewers_window_results = [ViewersResult("add", "anna")]
    assert kodi.viewers_window(request, "anna") == ViewersResult("add", "anna")
    assert kodi.viewers_window(request, "bob") == ViewersResult("close", "bob")
    assert kodi.viewers_window_calls == [(request, "anna"), (request, "bob")]


def test_remembered_window_returns_scripted_results_then_close():
    from resources.lib.ui.list_window import ListRequest, ListResult, ListState

    request = ListRequest(heading="h", rows=(), count_one="1", count_all="%s")
    forget = ListResult("forget", ListState(key="a"), key="a")
    kodi = FakeKodi()
    kodi.remembered_window_results = [forget]
    assert kodi.remembered_window(request, ListState()) == forget
    assert kodi.remembered_window(request, ListState(key="b")) == ListResult("close", ListState(key="b"))
    assert kodi.remembered_window_calls == [(request, ListState()), (request, ListState(key="b"))]
