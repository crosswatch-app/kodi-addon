import json
from dataclasses import dataclass, field
from typing import Any

from resources.lib.config import Settings
from resources.lib.playlist_index import PlaylistIndex
from resources.lib.service.sync_hooks import SyncHooks
from resources.lib.sync.constants import PLAYBACK_QUIET_SECONDS, RATING_SCAN_SECONDS
from resources.lib.sync.files import StartedFile, write_started
from resources.lib.sync.local import FileIndex, MarkGate, PersonBuffer, RecentPlays, file_signature
from resources.lib.sync.ownership import IndexSnapshot
from resources.lib.sync.reply import connection_id
from resources.lib.sync.writer import OwnWrites
from tests.fakes import FakeKodi

URL = "http://nas:8787/webhook/kodiwatcher"
# The fake Kodi behind each SyncHooks, for a test that changes Kodi's answers midway.
KODIS: dict[int, FakeKodi] = {}


@dataclass
class EngineSpy:
    server_backed_rows: frozenset[tuple[str, int]] = frozenset()
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def _note(self, name: str, value: Any = None) -> None:
        self.calls.append((name, value))

    def on_settings(self, settings: Settings) -> None:
        self._note("on_settings", settings)

    def on_scan(self, active: bool, at: float | None = None) -> None:
        self._note("on_scan", active)

    def on_viewers(self, at: float) -> None:
        self._note("on_viewers", at)

    def on_quiet(self, quiet: bool) -> None:
        self._note("on_quiet", quiet)

    def on_window(self, open_: bool) -> None:
        self._note("on_window", open_)

    def on_wake(self) -> None:
        self._note("on_wake")

    def on_request(self) -> None:
        self._note("on_request")

    def on_snapshot(self, snapshot: IndexSnapshot) -> None:
        self._note("on_snapshot", snapshot)


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def _hooks(tmp_path, scanning: bool = False, started: bool = True):
    path = str(tmp_path / "started.json")
    if started:
        write_started(path, StartedFile(connection=connection_id(URL), start_id="s"))
    kodi = FakeKodi(rpc_handlers={"XBMC.GetInfoBooleans": lambda p: {"Library.IsScanningVideo": scanning}})
    engine, clock, buffer, files = EngineSpy(), Clock(), PersonBuffer(), FileIndex()
    files.replace({("movie", 3): "/m/3.mkv"}, files.generation())
    plays = RecentPlays()
    hooks = SyncHooks(kodi=kodi, engine=engine, own=OwnWrites(), plays=plays, files=files,
                      gate=MarkGate(path, file_signature), buffer=buffer, clock=clock, utc_now=lambda: "T")  # fmt: skip
    hooks.on_settings(Settings(webhook_base_url=URL, webhook_token="t", sync_enabled=True))
    KODIS[id(hooks)] = kodi
    return hooks, engine, clock, buffer, plays, files


def _mark(kodi_id: int = 3, playcount: int = 1) -> str:
    return json.dumps({"item": {"id": kodi_id, "type": "movie"}, "playcount": playcount})


def test_person_mark_buffered_with_its_file(tmp_path):
    hooks, _, _, buffer, _, _ = _hooks(tmp_path)
    hooks.on_notification("VideoLibrary.OnUpdate", _mark())
    [record] = buffer._records
    assert (record.kind, record.kodi_id, record.file, record.playcount, record.at) == ("movie", 3, "/m/3.mkv", 1, "T")


def test_playback_update_is_not_buffered(tmp_path):
    hooks, _, clock, buffer, plays, _ = _hooks(tmp_path)
    plays.started("movie", 3, 0, clock.now)
    hooks.on_notification("VideoLibrary.OnUpdate", _mark())
    assert len(buffer) == 0


def test_marks_ignored_before_start(tmp_path):
    hooks, _, _, buffer, _, _ = _hooks(tmp_path, started=False)
    hooks.on_notification("VideoLibrary.OnUpdate", _mark())
    assert len(buffer) == 0


def test_scan_counted_and_clamped(tmp_path):
    hooks, engine, _, buffer, _, _ = _hooks(tmp_path)
    hooks.on_notification("VideoLibrary.OnScanStarted", "")
    hooks.on_notification("VideoLibrary.OnCleanStarted", "")
    hooks.on_notification("VideoLibrary.OnCleanFinished", "")
    hooks.on_notification("VideoLibrary.OnUpdate", _mark())
    assert len(buffer) == 0  # still scanning
    hooks.on_notification("VideoLibrary.OnScanFinished", "")
    hooks.on_notification("VideoLibrary.OnScanFinished", "")
    scans = [v for n, v in engine.calls if n == "on_scan"]
    assert scans == [True, True, False]


def test_onremove_updates_files_at_once(tmp_path):
    hooks, _, _, _, _, files = _hooks(tmp_path)
    hooks.on_notification("VideoLibrary.OnRemove", json.dumps({"id": 3, "type": "movie"}))
    assert files.file("movie", 3) == ""


def test_viewer_change_noted_after_the_first_look(tmp_path):
    seen = {"sig": 1}
    told = []
    hooks, engine, clock, _, _, _ = _hooks(tmp_path)
    hooks._viewers = lambda: seen["sig"]
    hooks._on_viewers_changed = lambda: told.append(1)
    snapshot = IndexSnapshot(index=None, built_at=0.0, invalidated_at=0.0, viewers=())
    hooks.tick(playing=False, snapshot=snapshot)
    seen["sig"] = 2
    hooks.tick(playing=False, snapshot=snapshot)
    assert told == [1] and [n for n, _ in engine.calls].count("on_viewers") == 1


def test_later_resync_never_raises_the_scan_count(tmp_path):
    hooks, engine, clock, _, _, _ = _hooks(tmp_path)
    snapshot = IndexSnapshot(index=None, built_at=0.0, invalidated_at=0.0, viewers=())
    hooks.tick(playing=False, snapshot=snapshot)
    KODIS[id(hooks)].rpc_handlers["XBMC.GetInfoBooleans"] = lambda p: {"Library.IsScanningVideo": True}
    clock.now += RATING_SCAN_SECONDS
    hooks.tick(playing=False, snapshot=snapshot)
    assert [v for n, v in engine.calls if n == "on_scan"] == []


def test_wake_and_unrelated_notifications(tmp_path):
    hooks, engine, _, _, _, _ = _hooks(tmp_path)
    hooks.on_notification("System.OnWake", "")
    hooks.on_notification("Player.OnPlay", "{}")
    hooks.on_notification("VideoLibrary.OnUpdate", "not json")
    assert [n for n, _ in engine.calls if n != "on_settings"] == ["on_wake"]


def test_quiet_only_a_while_after_playback(tmp_path):
    hooks, engine, clock, _, _, _ = _hooks(tmp_path)
    snapshot = IndexSnapshot(index=PlaylistIndex(), built_at=1.0, invalidated_at=0.0, viewers=())
    hooks.tick(playing=True, snapshot=snapshot)
    clock.now += 1
    hooks.tick(playing=False, snapshot=snapshot)
    clock.now += PLAYBACK_QUIET_SECONDS
    hooks.tick(playing=False, snapshot=snapshot)
    assert [v for n, v in engine.calls if n == "on_quiet"] == [False, True]
    assert [n for n, _ in engine.calls].count("on_snapshot") == 1


def test_scan_state_seeded_from_kodi_at_start(tmp_path):
    hooks, engine, clock, _, _, _ = _hooks(tmp_path, scanning=True)
    snapshot = IndexSnapshot(index=None, built_at=0.0, invalidated_at=0.0, viewers=())
    hooks.tick(playing=False, snapshot=snapshot)
    assert ("on_scan", True) in engine.calls
    # Resynced later: a lost end notification doesn't keep it scanning.
    KODIS[id(hooks)].rpc_handlers["XBMC.GetInfoBooleans"] = lambda p: {"Library.IsScanningVideo": False}
    clock.now += RATING_SCAN_SECONDS
    hooks.tick(playing=False, snapshot=snapshot)
    assert [v for n, v in engine.calls if n == "on_scan"] == [True, False]


def test_sync_request_bump_tells_the_engine(tmp_path):
    hooks, engine, _, _, _, _ = _hooks(tmp_path)
    hooks.on_settings(Settings(webhook_base_url=URL, webhook_token="t", sync_enabled=True, sync_request=1))
    assert ("on_request", None) in engine.calls


def test_window_forwarded(tmp_path):
    hooks, engine, _, _, _, _ = _hooks(tmp_path)
    hooks.on_window(True)
    assert ("on_window", True) in engine.calls
