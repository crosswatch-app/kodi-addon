import pytest

from resources.lib.kodi import KodiRpcError, KodiRuntime


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
