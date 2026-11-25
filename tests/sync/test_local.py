import os
from typing import Any

import pytest

from resources.lib import log as logmod
from resources.lib.sync import local as local_mod
from resources.lib.sync.constants import INBOX_MAX, PLAYBACK_QUIET_SECONDS
from resources.lib.sync.files import Inbox, InboxRecord, StartedFile, write_started
from resources.lib.sync.local import (
    QUIET_END_SECONDS,
    FileIndex,
    MarkGate,
    PersonBuffer,
    RecentPlays,
    classify,
    file_signature,
    kodi_counts_play,
    kodi_playcount_percent,
    plays_at_start,
    update_item,
)
from resources.lib.sync.writer import OwnWrites
from tests.fakes import FakeKodi
from tests.sync.fake_library import FakeLibrary

EP = ("episode", 7)


@pytest.fixture
def capture_log():
    logmod.reset()
    captured: list[str] = []
    logmod.configure(log_dir=None, debug=False, sink=lambda msg, level: captured.append(msg))
    yield captured
    logmod.reset()


def _classify(data: dict[str, Any], plays: RecentPlays, now: float = 0.0, **kw: Any) -> str:
    return classify(
        data,
        received=now,
        own=kw.pop("own", OwnWrites()),
        plays=plays,
        scanning=kw.pop("scanning", False),
        server_backed=kw.pop("server_backed", lambda kind, kodi_id: False),
    )


def _library(*ids: int, playcount: int = 0) -> FakeLibrary:
    lib = FakeLibrary()
    lib.add_show(1, title="Show")
    for n, kodi_id in enumerate(ids, start=1):
        lib.add_episode(kodi_id, 1, 1, n, file=f"/tv/e{n}.mkv", playcount=playcount)
    return lib


def _verdicts(lib: FakeLibrary, plays: RecentPlays, now: float) -> list[str]:
    out = [_classify(a, plays, now) for a in lib.announcements]
    lib.announcements.clear()
    return out


def _mark(kodi_id: int, playcount: int) -> dict[str, Any]:
    return {"item": {"id": kodi_id, "type": "episode"}, "playcount": playcount}


# -- playback, in the order Kodi delivers it (verification.md, Task 13a Step 0) ------------


def test_playback_update_is_not_a_person_watch():
    """The close arrives before the stop callback, while isPlaying() is already false."""
    lib, plays = _library(7), RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    lib.play("episodes", 7, 95)
    plays.quiet(1)
    assert _verdicts(lib, plays, 1) == ["playback", "playback"]
    plays.stopped(*EP, raised=True, now=1)


def test_second_raise_after_counted_stop_is_person():
    lib, plays = _library(7), RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    lib.play("episodes", 7, 95)
    _verdicts(lib, plays, 1)
    plays.stopped(*EP, raised=True, now=1)
    assert _classify(_mark(7, 2), plays, 5) == "person"


def test_close_after_the_stop_callback_covered_once():
    """VideoPlayer queues the close before it stops (VideoPlayer.cpp:2566-2586)."""
    lib, plays = _library(7), RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    plays.stopped(*EP, raised=True, now=1)
    lib.play("episodes", 7, 95)
    assert _verdicts(lib, plays, 2) == ["playback", "no_playcount"]
    assert _classify(_mark(7, 2), plays, 3) == "person"


def test_raise_above_start_count_covered_after_mid_play_change():
    plays = RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    # A mark on the playing item during the play is taken for playback, and isn't its close.
    assert _classify(_mark(7, 1), plays, 1) == "playback"
    plays.stopped(*EP, raised=True, now=2)
    assert _classify(_mark(7, 2), plays, 3) == "playback"


def test_mid_play_change_then_stop_below_threshold_is_playback():
    lib, plays = _library(7), RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    lib.row("episodes", 7)["playcount"] = 1
    lib.play("episodes", 7, 46, count_at_start=0)
    assert lib.announcements[0]["playcount"] == 1
    assert _verdicts(lib, plays, 1) == ["playback", "playback"]


def test_back_to_back_episodes_all_covered():
    lib, plays = _library(1, 2, 3), RecentPlays()
    now = 0.0
    for kodi_id, percent in ((1, 98), (2, 98), (3, 10)):
        plays.started("episode", kodi_id, 0, now)
        lib.play("episodes", kodi_id, percent)
        assert set(_verdicts(lib, plays, now + 1)) == {"playback"}
        plays.stopped("episode", kodi_id, raised=percent >= 90, now=now + 1)
        now += 1
    assert [lib.row("episodes", i)["playcount"] for i in (1, 2, 3)] == [1, 1, 0]


def test_mark_watched_after_stop_below_threshold_is_person():
    lib, plays = _library(7), RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    lib.play("episodes", 7, 46)
    assert _verdicts(lib, plays, 1) == ["playback", "playback"]
    plays.stopped(*EP, raised=False, now=1)
    assert _classify(_mark(7, 1), plays, 5) == "person"


def test_mark_watched_on_unplayed_item_during_playback_is_person():
    plays = RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    assert _classify(_mark(8, 1), plays, 1) == "person"


def test_stopped_coverage_expires():
    plays = RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    plays.stopped(*EP, raised=True, now=1)
    assert _classify(_mark(7, 1), plays, 2 + PLAYBACK_QUIET_SECONDS) == "person"


def test_play_without_stop_callback_ends_at_next_start():
    """Kodi drops queued playback messages when new playback starts."""
    plays = RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    plays.started("episode", 8, 0, now=1)
    assert _classify(_mark(7, 1), plays, 2) == "playback"
    assert _classify(_mark(7, 2), plays, 2) == "person"


def test_stop_of_unknown_play_covers_its_raise():
    plays = RecentPlays()
    plays.stopped(*EP, raised=True, now=0)
    assert _classify(_mark(7, 4), plays, 1) == "playback"


# -- plays that couldn't be resolved ----------------------------------------------------


def test_unresolved_play_covers_every_announcement():
    plays = RecentPlays()
    plays.started(None, None, None, now=0)
    assert _classify(_mark(7, 1), plays, 1) == "playback"
    assert _classify({"item": {"id": 3, "type": "movie"}, "playcount": 5}, plays, 1) == "playback"


def test_unresolved_play_ends_when_quiet_and_coverage_lasts_after():
    plays = RecentPlays()
    plays.started(None, None, None, now=0)
    plays.quiet(10)
    assert _classify(_mark(7, 1), plays, 10 + PLAYBACK_QUIET_SECONDS) == "playback"
    assert _classify(_mark(7, 1), plays, 11 + PLAYBACK_QUIET_SECONDS) == "person"


def test_quiet_keeps_a_resolved_play_until_its_stop():
    """isPlaying() turns false about 0.1 s before the stop callback."""
    plays = RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    plays.quiet(1)
    plays.quiet(1 + QUIET_END_SECONDS - 0.5)
    assert _classify(_mark(7, 1), plays, 1 + QUIET_END_SECONDS - 0.5) == "playback"


def test_lost_stop_callback_ends_the_play_after_quiet():
    plays = RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    plays.quiet(1)
    plays.quiet(1 + QUIET_END_SECONDS)
    # Its one raise is still covered, then a person's mark counts.
    assert _classify(_mark(7, 1), plays, 2 + QUIET_END_SECONDS) == "playback"
    assert _classify(_mark(7, 2), plays, 3 + QUIET_END_SECONDS) == "person"


def test_gap_between_playlist_items_is_not_an_end():
    plays = RecentPlays()
    plays.started(*EP, playcount=0, now=0)
    plays.quiet(1)
    plays.active()
    plays.quiet(1 + QUIET_END_SECONDS)
    assert _classify(_mark(7, 1), plays, 2 + QUIET_END_SECONDS) == "playback"
    assert _classify(_mark(7, 2), plays, 2 + QUIET_END_SECONDS) == "playback"


def test_quiet_without_a_play_does_nothing():
    plays = RecentPlays()
    plays.quiet(0)
    assert _classify(_mark(7, 1), plays, 1) == "person"


# -- other announcements ------------------------------------------------------------------


def test_own_write_echo_is_not_a_change():
    own = OwnWrites()
    own.note("episode", 7, {"playcount": 1}, "w1")
    assert _classify(_mark(7, 1), RecentPlays(), own=own) == "own"
    assert _classify(_mark(7, 1), RecentPlays(), own=own) == "person"


def test_refresh_added_is_not_a_change():
    lib = _library(7, 9, playcount=1)
    new_id = lib.refresh("episodes", 7)
    assert new_id == 10 and lib.removals == [{"id": 7, "type": "episode"}]
    assert _verdicts(lib, RecentPlays(), 0) == ["added"]


def test_refresh_of_the_highest_row_keeps_its_id():
    lib = _library(7, 9)
    assert lib.refresh("episodes", 9) == 9


def test_clean_and_rescan_loses_count_and_is_not_a_change():
    lib = _library(7, 9, playcount=2)
    lib.row("episodes", 9)["resume"] = {"position": 30.0, "total": 600.0}
    assert lib.clean_and_rescan("episodes", 9) == 9
    row = lib.row("episodes", 9)
    assert row["playcount"] == 0 and row["resume"]["position"] == 0.0
    assert _verdicts(lib, RecentPlays(), 0) == ["added"]


def test_scan_transaction_is_not_a_change():
    assert _classify({**_mark(7, 1), "transaction": True}, RecentPlays()) == "scan"
    assert _classify(_mark(7, 1), RecentPlays(), scanning=True) == "scan"


def test_announcement_without_playcount_ignored():
    assert _classify({"item": {"id": 7, "type": "episode"}}, RecentPlays()) == "no_playcount"
    assert _classify({"id": 7, "type": "episode"}, RecentPlays()) == "no_playcount"


@pytest.mark.parametrize(
    "data", [{"item": {"id": 1, "type": "tvshow"}, "playcount": 1}, {"playcount": 1}, {"id": True, "type": "movie"}]
)
def test_other_items_are_not_video(data):
    assert _classify(data, RecentPlays()) == "not_video"


def test_server_backed_row_never_person():
    backed = {("episode", 7)}
    assert _classify(_mark(7, 1), RecentPlays(), server_backed=lambda k, i: (k, i) in backed) == "server"


def test_update_item_shapes():
    assert update_item({"item": {"id": 3, "type": "movie"}, "playcount": 1}) == ("movie", 3)
    assert update_item({"id": 3, "type": "movie"}) == ("movie", 3)
    assert update_item({"item": "x"}) is None


# -- Kodi's play-count rule -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("percent", "minimum", "counted"),
    [(95, 90, True), (90, 90, True), (89.9, 90, False), (100, 0, False), (100, 101, False), (None, 90, False)],
)
def test_raised_follows_kodi_rule_zero_and_101_off(percent, minimum, counted):
    assert kodi_counts_play(percent, minimum) is counted


def test_near_threshold_band_off_at_101():
    assert kodi_counts_play(89.5, 90, band=1.0) is True
    assert kodi_counts_play(88.0, 90, band=1.0) is False
    assert kodi_counts_play(100.0, 101, band=5.0) is False


PROFILE = "special://profile/advancedsettings.xml"
MASTER = "special://masterprofile/advancedsettings.xml"
SYSTEM = "special://xbmc/system/advancedsettings.xml"


def _percent(value: str) -> str:
    return f"<advancedsettings><video><playcountminimumpercent>{value}</playcountminimumpercent></video></advancedsettings>"


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({}, 90.0),
        ({PROFILE: _percent("75")}, 75.0),
        # Whole file: a profile file without the element doesn't fall back to the master's.
        ({PROFILE: "<advancedsettings/>", MASTER: _percent("75")}, 90.0),
        ({MASTER: _percent("75")}, 75.0),
        ({PROFILE: _percent("85abc")}, 85.0),
        ({PROFILE: _percent("abc")}, 0.0),
        ({PROFILE: _percent(" ")}, 90.0),
        ({PROFILE: _percent("150")}, 101.0),
        ({PROFILE: _percent("-3")}, 0.0),
        ({PROFILE: _percent("1e1")}, 10.0),
        ({PROFILE: "<advancedsettings><video>"}, 90.0),
        ({PROFILE: "<other>" + _percent("75") + "</other>"}, 90.0),
        ({PROFILE: _percent("75").replace("<video>", "<video><x>a&b</x>")}, 75.0),
        ({SYSTEM: _percent("70")}, 70.0),
        ({SYSTEM: _percent("70"), PROFILE: _percent("80")}, 80.0),
        ({SYSTEM: _percent("70"), PROFILE: "<AdvancedSettings/>"}, 70.0),
    ],
)
def test_kodi_thresholds_whole_file_and_numeric_prefix(files, expected):
    assert kodi_playcount_percent(FakeKodi(files=files)) == expected


# -- buffer, gate, files --------------------------------------------------------------------


def _record(kodi_id: int = 7) -> InboxRecord:
    return InboxRecord(at="2026-11-20T10:00:00Z", kind="episode", kodi_id=kodi_id, file="/tv/e1.mkv", playcount=1)


def test_person_verdict_in_inbox_once(tmp_path):
    inbox, buffer = Inbox(str(tmp_path / "inbox.db")), PersonBuffer()
    assert buffer.flush(inbox) is False
    buffer.add(_record())
    assert buffer.flush(inbox) is True
    assert buffer.flush(inbox) is False
    assert [r.kodi_id for r in inbox.pending()] == [7]


class _BusyInbox:
    def __init__(self) -> None:
        self.busy = True
        self.added: list[InboxRecord] = []

    def add_many(self, records: list[InboxRecord]) -> bool:
        if self.busy:
            return False
        self.added.extend(records)
        return True


def test_busy_inbox_keeps_buffer():
    inbox, buffer = _BusyInbox(), PersonBuffer()
    buffer.add(_record())
    assert buffer.flush(inbox) is False and len(buffer) == 1  # type: ignore[arg-type]
    inbox.busy = False
    assert buffer.flush(inbox) is True and len(buffer) == 0  # type: ignore[arg-type]
    assert [r.kodi_id for r in inbox.added] == [7]


def test_buffer_kept_while_inbox_fails_is_capped(capture_log):
    buffer = PersonBuffer()
    for n in range(INBOX_MAX + 2):
        buffer.add(_record(n))
    assert len(buffer) == INBOX_MAX
    inbox = _BusyInbox()
    inbox.busy = False
    buffer.flush(inbox)  # type: ignore[arg-type]
    assert inbox.added[0].kodi_id == 2
    assert sum("sync.inbox.buffer_full" in line for line in capture_log) == 2


def test_onremove_updates_files_at_once():
    files = FileIndex()
    files.replace({("episode", 7): "/tv/e1.mkv", ("movie", 3): "/m.mkv"}, files.generation())
    files.remove("episode", 7)
    files.remove("episode", 7)
    assert files.file("episode", 7) == "" and files.file("movie", 3) == "/m.mkv"


def test_read_begun_before_a_removal_does_not_restore_the_row():
    files = FileIndex()
    started = files.generation()
    files.remove("episode", 7)
    files.replace({("episode", 7): "/tv/old.mkv", ("movie", 3): "/m.mkv"}, started)
    assert files.file("episode", 7) == ""
    # A read taken after the removal is trusted: the id may be a new row now.
    files.replace({("episode", 7): "/tv/new.mkv"}, files.generation())
    assert files.file("episode", 7) == "/tv/new.mkv"


def test_cached_gate_sees_worker_pause(tmp_path):
    path = str(tmp_path / "started.json")
    stats: list[str] = []

    def stat(p: str) -> tuple[int, int, int] | None:
        stats.append(p)
        return file_signature(p)

    gate = MarkGate(path, stat)
    gate.on_settings("conn", True)
    assert gate.open() is False
    write_started(path, StartedFile(connection="conn", start_id="s"))
    gate.refresh()
    assert gate.open() is True
    before = os.stat(path).st_mtime_ns
    write_started(path, StartedFile(connection="conn", start_id="s", paused=True, paused_reason="turned_off"))
    # A file system with coarse timestamps: the rewrite keeps the old mtime, and the
    # replaced inode still shows the change.
    os.utime(path, ns=(before, before))
    gate.refresh()
    assert gate.open() is False
    assert stats


def test_gate_closed_for_other_connection_or_sync_off(tmp_path):
    path = str(tmp_path / "started.json")
    write_started(path, StartedFile(connection="conn", start_id="s"))
    gate = MarkGate(path, file_signature)
    gate.on_settings("other", True)
    assert gate.open() is False
    gate.on_settings("conn", False)
    assert gate.open() is False
    gate.on_settings("conn", True)
    assert gate.open() is True


def test_gate_reads_the_file_only_when_it_changes(tmp_path, monkeypatch):
    path = str(tmp_path / "started.json")
    write_started(path, StartedFile(connection="conn", start_id="s"))
    reads: list[str] = []
    real = local_mod.read_started
    monkeypatch.setattr(local_mod, "read_started", lambda p: reads.append(p) or real(p))
    gate = MarkGate(path, file_signature)
    gate.on_settings("conn", True)
    gate.refresh()
    gate.refresh()
    assert len(reads) == 1


# -- start mid-playback ---------------------------------------------------------------------


def _playing(item: Any, video: bool = True, players: Any = None) -> FakeKodi:
    def get_item(params):
        if isinstance(item, Exception):
            raise item
        return {"item": item} if item is not None else {}

    return FakeKodi(
        playing_video=video,
        rpc_handlers={
            "Player.GetActivePlayers": lambda p: {
                "result": players if players is not None else [{"playerid": 1, "type": "video"}]
            },
            "Player.GetItem": get_item,
        },
    )


@pytest.mark.parametrize(
    ("kodi", "covered"),
    [
        # A library item is keyed exactly: a mark on another item is a person's.
        (_playing({"id": 4, "type": "episode", "playcount": 0}), {"episode-4"}),
        (_playing({"id": -1, "type": "movie"}), set()),
        (_playing({"id": 3, "type": "channel"}), set()),
        (_playing({"type": "unknown"}), set()),
        (_playing({"id": 4, "type": "episode"}, video=False), set()),
        (_playing(RuntimeError("rpc down")), {"episode-4", "movie-9"}),
        (_playing(None), {"episode-4", "movie-9"}),
        (_playing({"id": 4, "type": "episode"}, players=[]), {"episode-4", "movie-9"}),
    ],
)
def test_live_tv_at_start_is_not_unresolved(kodi, covered):
    plays = plays_at_start(kodi, now=0)
    got = {
        f"{kind}-{kodi_id}"
        for kind, kodi_id in (("episode", 4), ("movie", 9))
        if _classify({"item": {"id": kodi_id, "type": kind}, "playcount": 1}, plays, 1) == "playback"
    }
    assert got == covered


def test_restart_mid_playback_keys_the_start_count():
    plays = plays_at_start(_playing({"id": 4, "type": "episode", "playcount": 2}), now=0)
    plays.stopped("episode", 4, raised=True, now=1)
    assert _classify({"item": {"id": 4, "type": "episode"}, "playcount": 2}, plays, 2) == "person"
