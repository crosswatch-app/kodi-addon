import pytest

from resources.lib.kodi import KodiRpcError, KodiRuntime
from resources.lib.ui import window as window_mod
from resources.lib.ui.list_window import ListRequest, ListResult, ListState
from resources.lib.ui.who_watched import WhoWatchedRequest


class RecordingPlayer:
    def __init__(self, time_value=None, total_value=None) -> None:
        self.time_value = time_value
        self.total_value = total_value

    def isPlaying(self) -> bool:
        return True

    def getTime(self) -> float:
        if self.time_value is None:
            raise RuntimeError("not playing")
        return self.time_value

    def getTotalTime(self) -> float:
        if self.total_value is None:
            raise RuntimeError("not playing")
        return self.total_value


@pytest.fixture
def runtime():
    return KodiRuntime(player=RecordingPlayer(30.0, 120.0))


def test_player_times_converts_seconds_to_milliseconds(runtime):
    assert runtime.player_times() == (30_000, 120_000)


def test_a_negative_position_is_clamped_to_zero():
    """Observed on a real Kodi: getTime() returned -0.028 at the instant onAVStarted fired.

    A negative position is not a state the player can be in, and it reached the wire as
    position_ms: -28, which the receiver would carry into a resume point.
    """
    assert KodiRuntime(player=RecordingPlayer(-0.028, 120.0)).player_times() == (0, 120_000)


def test_a_zero_duration_is_reported_as_unknown():
    got = KodiRuntime(player=RecordingPlayer(30.0, 0.0)).player_times()
    assert got == (30_000, None)


def test_a_failed_duration_read_keeps_the_position():
    got = KodiRuntime(player=RecordingPlayer(30.0, None)).player_times()
    assert got == (30_000, None)


def test_a_failed_position_read_still_reports_the_duration():
    got = KodiRuntime(player=RecordingPlayer(None, 120.0)).player_times()
    assert got == (None, 120_000)


def test_multiselect_converts_the_autoclose_to_milliseconds(runtime, monkeypatch):
    seen = {}

    class Dialog:
        def multiselect(self, heading, options, autoclose=0, preselect=None, useDetails=False):
            seen.update(heading=heading, autoclose=autoclose, preselect=preselect)
            return [0]

    monkeypatch.setattr(runtime._xbmcgui, "Dialog", Dialog)
    runtime.multiselect("Who watched?", ["anna"], preselect=[0], autoclose=90)
    assert seen["autoclose"] == 90_000
    assert seen["preselect"] == [0]


def test_multiselect_passes_an_empty_preselect_rather_than_none(runtime, monkeypatch):
    seen = {}

    class Dialog:
        def multiselect(self, heading, options, autoclose=0, preselect=None, useDetails=False):
            seen["preselect"] = preselect
            return None

    monkeypatch.setattr(runtime._xbmcgui, "Dialog", Dialog)
    runtime.multiselect("Who watched?", ["anna"])
    assert seen["preselect"] == []


def test_jsonrpc_raises_on_an_error_body(runtime, monkeypatch):
    monkeypatch.setattr(
        runtime._xbmc, "executeJSONRPC", lambda request: '{"error": {"code": -32601}}'
    )
    with pytest.raises(KodiRpcError):
        runtime.jsonrpc("No.Such.Method")


def test_jsonrpc_wraps_a_non_dict_result(runtime, monkeypatch):
    monkeypatch.setattr(runtime._xbmc, "executeJSONRPC", lambda request: '{"result": [1, 2]}')
    assert runtime.jsonrpc("Player.GetActivePlayers") == {"result": [1, 2]}


def test_read_text_bounds_the_read_rather_than_slicing_afterwards(runtime, monkeypatch):
    asked = {}

    class File:
        def __init__(self, path, mode="r") -> None: ...

        def read(self, count=0):
            asked["count"] = count
            return "x" * 10

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(runtime._xbmcvfs, "exists", lambda path: True)
    monkeypatch.setattr(runtime._xbmcvfs, "File", File)
    runtime.read_text("special://profile/advancedsettings.xml", max_bytes=10)
    assert asked["count"] == 10

REQUEST = WhoWatchedRequest(title="t", subtitle="", poster="", names=("anna", "bob"), autoclose_seconds=120)


def test_who_watched_opens_the_window_from_the_font_adapted_path(runtime, monkeypatch):
    opened = {}

    class Dialog(window_mod.WhoWatchedDialog):
        def __init__(self, xml, path, skin, resolution) -> None:
            super().__init__(xml, path, skin, resolution)
            opened.update(xml=xml, path=path)

        def doModal(self) -> None:
            self.finish("done", ("bob",))

    monkeypatch.setattr(window_mod, "WhoWatchedDialog", Dialog)
    monkeypatch.setattr("resources.lib.ui.skin_fonts.ensure_generated", lambda addon_id: "/generated")
    assert runtime.who_watched(REQUEST) == ("bob",)
    assert opened == {"xml": "crosswatch-who.xml", "path": "/generated"}


def test_who_watched_failure_is_a_cancel(runtime, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("Unable to load skin file")

    monkeypatch.setattr(window_mod, "WhoWatchedDialog", broken)
    monkeypatch.setattr("resources.lib.ui.skin_fonts.ensure_generated", lambda addon_id: "/generated")
    assert runtime.who_watched(REQUEST) is None


def test_who_watched_failure_logs_the_window_and_error_type_only(runtime, monkeypatch):
    from resources.lib import log as logmod

    captured: list[str] = []
    logmod.configure(log_dir=None, debug=False, sink=lambda message, level: captured.append(message))

    def broken(*args, **kwargs):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(window_mod, "WhoWatchedDialog", broken)
    monkeypatch.setattr("resources.lib.ui.skin_fonts.ensure_generated", lambda addon_id: "/generated")
    runtime.who_watched(REQUEST)
    line = next(m for m in captured if "ui.window_failed" in m)
    assert "who_watched" in line and "RuntimeError" in line and "secret detail" not in line

def test_confirm_window_returns_true_only_for_yes(runtime, monkeypatch):
    class Dialog(window_mod.ConfirmDialog):
        def doModal(self) -> None:
            self.finish("yes", True)

    monkeypatch.setattr(window_mod, "ConfirmDialog", Dialog)
    monkeypatch.setattr("resources.lib.ui.skin_fonts.ensure_generated", lambda addon_id: "/generated")
    assert runtime.confirm_window("h", "m") is True


def test_confirm_window_back_and_failure_are_no(runtime, monkeypatch):
    class Back(window_mod.ConfirmDialog):
        def doModal(self) -> None:
            self.finish("back")

    monkeypatch.setattr("resources.lib.ui.skin_fonts.ensure_generated", lambda addon_id: "/generated")
    monkeypatch.setattr(window_mod, "ConfirmDialog", Back)
    assert runtime.confirm_window("h", "m") is False

    def broken(*args, **kwargs):
        raise RuntimeError("no skin file")

    monkeypatch.setattr(window_mod, "ConfirmDialog", broken)
    assert runtime.confirm_window("h", "m") is False

LIST_REQUEST = ListRequest(heading="h", rows=(), count_one="1", count_all="%s", bulk_all="Forget all", bulk_shown="Forget %s shown")


def test_list_window_returns_what_the_window_returned(runtime, monkeypatch):
    class Dialog(window_mod.ListDialog):
        def doModal(self) -> None:
            self.finish("open", ListResult("open", ListState(search="x"), key="k"))

    monkeypatch.setattr(window_mod, "ListDialog", Dialog)
    monkeypatch.setattr("resources.lib.ui.skin_fonts.ensure_generated", lambda addon_id: "/generated")
    assert runtime.list_window(LIST_REQUEST, ListState()) == ListResult("open", ListState(search="x"), key="k")


def test_list_window_back_or_failure_is_close_with_the_state_kept(runtime, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("no skin file")

    monkeypatch.setattr(window_mod, "ListDialog", broken)
    monkeypatch.setattr("resources.lib.ui.skin_fonts.ensure_generated", lambda addon_id: "/generated")
    state = ListState(search="a")
    assert runtime.list_window(LIST_REQUEST, state) == ListResult("close", state)


PICK_REQUEST = ListRequest(heading="h", rows=(), count_one="1", count_all="%s", pick=True)


def test_list_window_opens_the_pick_window_for_a_pick_request(runtime, monkeypatch):
    opened = []

    class Pick(window_mod.PickListDialog):
        def doModal(self) -> None:
            opened.append(type(self))
            self.finish("done", ListResult("done", ListState(), keys=("a",)))

    monkeypatch.setattr(window_mod, "PickListDialog", Pick)
    monkeypatch.setattr("resources.lib.ui.skin_fonts.ensure_generated", lambda addon_id: "/generated")
    assert runtime.list_window(PICK_REQUEST, ListState()) == ListResult("done", ListState(), keys=("a",))
    assert opened == [Pick]


def test_a_failed_pick_window_is_a_logged_close(runtime, monkeypatch):
    from resources.lib import log as logmod

    captured: list[str] = []
    logmod.configure(log_dir=None, debug=False, sink=lambda message, level: captured.append(message))

    def broken(*args, **kwargs):
        raise RuntimeError("no skin file")

    monkeypatch.setattr(window_mod, "PickListDialog", broken)
    monkeypatch.setattr("resources.lib.ui.skin_fonts.ensure_generated", lambda addon_id: "/generated")
    assert runtime.list_window(PICK_REQUEST, ListState()) == ListResult("close", ListState())
    line = next(m for m in captured if "ui.window_failed" in m)
    assert "pick_list" in line and "RuntimeError" in line
