import itertools
from typing import Any

from resources.lib.advanced_settings import Thresholds
from resources.lib.config import Settings
from resources.lib.media import MediaResolver
from resources.lib.models import Device, PlaybackEvent, Viewer
from resources.lib.service.controller import Controller
from resources.lib.storage import JsonViewerStore, PromptMemory
from tests.fakes import FakeKodi

DEVICE = Device(id="htpc-1", name="Living room")
PAST_START = 200_000

EPISODE = {
    "item": {
        "id": 5,
        "type": "episode",
        "title": "Example episode",
        "showtitle": "Just an example",
        "season": 1,
        "episode": 1,
        "year": 2026,
        "tvshowid": 42,
        "file": "nfs://nas/tv/S01E01.mkv",
        "uniqueid": {},
    }
}


class Collector:
    def __init__(self) -> None:
        self.events: list[PlaybackEvent] = []

    def submit(self, event: PlaybackEvent) -> bool:
        self.events.append(event)
        return True

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]


def _kodi(members, profile: str = "Master user", overrides: dict[str, Any] | None = None):
    # A named dict rather than **kwargs: with **kwargs a caller's RPC method name is
    # indistinguishable from the profile keyword, to a reader and to the type checker.
    handlers = {
        "Player.GetActivePlayers": lambda params: {"result": [{"playerid": 1, "type": "video"}]},
        "Player.GetItem": lambda params: EPISODE,
        "VideoLibrary.GetTVShowDetails": lambda params: {"tvshowdetails": {"uniqueid": {"tvdb": "83462"}}},
        "Files.GetDirectory": lambda params: {"files": members},
        "Profiles.GetCurrentProfile": lambda params: {"label": profile},
    }
    handlers.update(overrides or {})
    kodi = FakeKodi(rpc_handlers=handlers, position_ms=PAST_START, duration_ms=1_320_000)
    kodi.read_text = lambda path, max_bytes=None: '<smartplaylist type="tvshows"/>'  # type: ignore[method-assign]
    return kodi


def _controller(tmp_path, kodi, viewers, collector, settings=None, ttl=3600):
    store = JsonViewerStore(str(tmp_path / "viewers.json"))
    store.save(viewers)
    counter = itertools.count(1)
    clock = itertools.count(0)
    return Controller(
        kodi=kodi,
        viewer_store=store,
        memory=PromptMemory(str(tmp_path / "prompts.json")),
        media_resolver=MediaResolver(kodi),
        queue=collector,
        settings=settings or Settings(progress_step=25, movie_prompts=True, index_ttl_seconds=ttl),
        thresholds=Thresholds(),
        clock=lambda: "2026-09-19T20:00:00Z",
        monotonic=lambda: float(next(clock)),
        ids=lambda: f"id-{next(counter)}",
    )


def _warm_index(controller, kodi):
    """Drive the idle tick until the index has been built.

    The player has to be idle for this to do anything. The controller deliberately refuses
    the whole-library expansion whenever Kodi is decoding something, session or not, so a
    helper that leaves the fake reporting playback warms nothing and every assertion built
    on it passes or fails for the wrong reason.
    """
    was_playing = kodi.playing
    kodi.playing = False
    try:
        for _ in range(10):
            controller.on_tick()
    finally:
        kodi.playing = was_playing


# --- index lifecycle -------------------------------------------------------

def test_the_index_is_built_from_the_idle_tick_not_from_playback_start(tmp_path):
    collector = Collector()
    kodi = _kodi([{"id": 42, "type": "tvshow"}])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna", playlists=("Anna TV",))], collector)
    controller.on_av_started()
    assert [c for c in kodi.calls if c[0] == "Files.GetDirectory"] == []
    assert collector.events[0].viewers == ()


def test_playback_after_a_warm_index_resolves_from_the_playlist(tmp_path):
    collector = Collector()
    kodi = _kodi([{"id": 42, "type": "tvshow"}])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna", playlists=("Anna TV",))], collector)
    _warm_index(controller, kodi)
    controller.on_av_started()
    assert collector.events[0].viewers == ("anna",)
    assert collector.events[0].viewers_source == "playlist"


def test_the_index_is_not_rebuilt_while_something_is_playing(tmp_path):
    collector = Collector()
    kodi = _kodi([{"id": 42, "type": "tvshow"}])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna", playlists=("Anna TV",))], collector)
    _warm_index(controller, kodi)
    controller.on_av_started()
    controller.invalidate_index()
    before = len([c for c in kodi.calls if c[0] == "Files.GetDirectory"])
    controller.on_tick()
    assert len([c for c in kodi.calls if c[0] == "Files.GetDirectory"]) == before


def test_a_stale_index_is_rebuilt_on_the_ttl(tmp_path):
    collector = Collector()
    kodi = _kodi([{"id": 42, "type": "tvshow"}])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna", playlists=("Anna TV",))], collector, ttl=3)
    _warm_index(controller, kodi)
    before = len([c for c in kodi.calls if c[0] == "Files.GetDirectory"])
    _warm_index(controller, kodi)
    assert len([c for c in kodi.calls if c[0] == "Files.GetDirectory"]) > before


def test_a_failed_build_keeps_the_previous_index(tmp_path):
    collector = Collector()
    kodi = _kodi([{"id": 42, "type": "tvshow"}])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna", playlists=("Anna TV",))], collector)
    _warm_index(controller, kodi)

    def boom(params):
        raise RuntimeError("database is locked")

    kodi.rpc_handlers["Files.GetDirectory"] = boom
    controller.invalidate_index()
    _warm_index(controller, kodi)
    controller.on_av_started()
    assert collector.events[0].viewers == ("anna",)


# --- session lifecycle -----------------------------------------------------

def test_session_id_is_stable_and_event_ids_are_not(tmp_path):
    collector = Collector()
    controller = _controller(tmp_path, _kodi([]), [Viewer(name="anna")], collector)
    controller.on_av_started()
    controller.on_stopped(completed=False)
    controller.on_tick()
    assert collector.events[0].session_id == collector.events[-1].session_id
    assert collector.events[0].event_id != collector.events[-1].event_id


def test_pause_and_resume_emit_their_own_events(tmp_path):
    collector = Collector()
    controller = _controller(tmp_path, _kodi([]), [Viewer(name="anna")], collector)
    controller.on_av_started()
    controller.on_paused()
    controller.on_resumed()
    assert collector.kinds() == ["start", "pause", "resume"]


def test_a_pause_with_no_session_is_logged_rather_than_silently_dropped(tmp_path):
    collector = Collector()
    controller = _controller(tmp_path, _kodi([]), [Viewer(name="anna")], collector)
    controller.on_paused()
    assert collector.events == []


def test_the_stop_is_not_emitted_inside_the_callback(tmp_path):
    collector = Collector()
    controller = _controller(tmp_path, _kodi([]), [Viewer(name="anna")], collector)
    controller.on_av_started()
    controller.on_stopped(completed=False)
    assert collector.kinds() == ["start"]
    controller.on_tick()
    assert collector.kinds() == ["start", "stop"]


def test_stop_uses_the_last_sample(tmp_path):
    collector = Collector()
    controller = _controller(tmp_path, _kodi([]), [Viewer(name="anna")], collector)
    controller.on_av_started()
    controller.on_tick()
    controller.on_stopped(completed=False)
    controller.on_tick()
    assert collector.events[-1].position_ms == PAST_START


def test_completed_playback_is_reported_as_completed(tmp_path):
    collector = Collector()
    controller = _controller(tmp_path, _kodi([]), [Viewer(name="anna")], collector)
    controller.on_av_started()
    controller.on_stopped(completed=True)
    controller.on_tick()
    assert collector.events[-1].completed is True


def test_an_unknown_duration_reports_percent_as_none(tmp_path):
    collector = Collector()
    kodi = _kodi([])
    kodi.duration_ms = None
    controller = _controller(tmp_path, kodi, [Viewer(name="anna")], collector)
    controller.on_av_started()
    controller.on_stopped(completed=True)
    controller.on_tick()
    assert collector.events[-1].percent is None
    assert collector.events[-1].completed is True


def test_a_missing_stop_callback_is_reconciled_by_the_liveness_check(tmp_path):
    collector = Collector()
    kodi = _kodi([])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna")], collector)
    controller.on_av_started()
    kodi.playing = False
    controller.on_tick()
    controller.on_tick()
    assert collector.kinds() == ["start", "stop"]


def test_a_new_playback_closes_a_surviving_session_rather_than_replacing_it(tmp_path):
    collector = Collector()
    kodi = _kodi([])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna")], collector)
    controller.on_av_started()
    controller.on_av_started()
    assert collector.kinds() == ["start", "stop", "start"]
    assert collector.events[0].session_id == collector.events[1].session_id
    assert collector.events[2].session_id != collector.events[0].session_id


def test_an_unresolvable_next_item_does_not_leave_the_old_session_installed(tmp_path):
    collector = Collector()
    kodi = _kodi([])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna")], collector)
    controller.on_av_started()
    kodi.rpc_handlers["Player.GetItem"] = lambda params: {
        "item": {"id": 1, "type": "musicvideo", "title": "x", "file": "x"}
    }
    controller.on_av_started()
    controller.on_tick()
    controller.on_tick()
    assert collector.kinds() == ["start", "stop"]


def test_a_raising_resolve_does_not_leave_the_old_session_installed(tmp_path):
    collector = Collector()
    kodi = _kodi([])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna")], collector)
    controller.on_av_started()

    def boom(params):
        raise RuntimeError("rpc down")

    kodi.rpc_handlers["Player.GetItem"] = boom
    controller.on_av_started()
    controller.on_tick()
    assert collector.kinds() == ["start", "stop"]


def test_identity_resolves_through_the_active_profile(tmp_path):
    collector = Collector()
    kodi = _kodi([], profile="Anna")
    viewers = [Viewer(name="anna", profiles=("Anna",)), Viewer(name="bob")]
    controller = _controller(tmp_path, kodi, viewers, collector)
    controller.on_av_started()
    assert collector.events[0].viewers == ("anna",)
    assert collector.events[0].viewers_source == "profile"


def test_a_remembered_show_resolves_at_start_not_only_at_stop(tmp_path):
    collector = Collector()
    kodi = _kodi([], profile="Guest")
    PromptMemory(str(tmp_path / "prompts.json")).remember("show:tvdb:83462", ("bob",))
    viewers = [Viewer(name="anna"), Viewer(name="bob")]
    controller = _controller(tmp_path, kodi, viewers, collector)
    controller.on_av_started()
    assert collector.events[0].viewers == ("bob",)
    assert collector.events[0].viewers_source == "prompt"


def test_the_index_is_not_built_while_an_unresolvable_item_is_playing(tmp_path):
    collector = Collector()
    kodi = _kodi([{"id": 42, "type": "tvshow"}])
    kodi.rpc_handlers["Player.GetItem"] = lambda params: {
        "item": {"id": 1, "type": "musicvideo", "title": "x", "file": "x"}
    }
    kodi.playing = True
    controller = _controller(tmp_path, kodi, [Viewer(name="anna", playlists=("Anna TV",))], collector)
    controller.on_av_started()
    for _ in range(5):
        controller.on_tick()
    assert [c for c in kodi.calls if c[0] == "Files.GetDirectory"] == []


def test_a_failed_build_is_retried_after_the_backoff_not_after_the_ttl(tmp_path):
    collector = Collector()
    kodi = _kodi([{"id": 42, "type": "tvshow"}])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna", playlists=("Anna TV",))], collector)

    def boom(params):
        raise RuntimeError("database is locked")

    kodi.rpc_handlers["Files.GetDirectory"] = boom
    _warm_index(controller, kodi)
    first = len([c for c in kodi.calls if c[0] == "Files.GetDirectory"])
    _warm_index(controller, kodi)
    # Inside the backoff window, so no new attempt.
    assert len([c for c in kodi.calls if c[0] == "Files.GetDirectory"]) == first


def test_a_paused_session_does_not_block_the_index_rebuild(tmp_path):
    collector = Collector()
    kodi = _kodi([{"id": 42, "type": "tvshow"}])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna", playlists=("Anna TV",))], collector)
    controller.on_av_started()
    controller.on_paused()
    for _ in range(5):
        controller.on_tick()
    assert [c for c in kodi.calls if c[0] == "Files.GetDirectory"] != []


def test_pause_state_does_not_survive_into_the_next_playback(tmp_path):
    collector = Collector()
    kodi = _kodi([{"id": 42, "type": "tvshow"}])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna", playlists=("Anna TV",))], collector)
    controller.on_av_started()
    controller.on_paused()
    controller.on_stopped(completed=False)
    controller.on_tick()
    kodi.rpc_handlers["Player.GetItem"] = lambda params: {
        "item": {"id": 1, "type": "musicvideo", "title": "x", "file": "x"}
    }
    controller.on_av_started()
    before = len([c for c in kodi.calls if c[0] == "Files.GetDirectory"])
    for _ in range(3):
        controller.on_tick()
    assert len([c for c in kodi.calls if c[0] == "Files.GetDirectory"]) == before


def test_a_new_playback_flushes_a_parked_stop_without_prompting(tmp_path):
    collector = Collector()
    kodi = _kodi([], profile="Guest")
    kodi.multiselect_answer = [0]
    viewers = [Viewer(name="anna"), Viewer(name="bob")]
    controller = _controller(tmp_path, kodi, viewers, collector)
    controller.on_av_started()
    controller.on_tick()
    controller.on_stopped(completed=False)
    controller.on_av_started()
    assert collector.kinds() == ["start", "stop", "start"]
    assert kodi.multiselect_calls == []
    assert collector.events[1].viewers == ()


def test_abort_emits_a_stop_for_an_open_session(tmp_path):
    collector = Collector()
    controller = _controller(tmp_path, _kodi([]), [Viewer(name="anna")], collector)
    controller.on_av_started()
    controller.on_tick()
    controller.on_abort()
    assert collector.kinds() == ["start", "stop"]
    assert collector.events[-1].position_ms == PAST_START


def test_abort_flushes_a_parked_stop_without_prompting(tmp_path):
    collector = Collector()
    kodi = _kodi([], profile="Guest")
    kodi.multiselect_answer = [0]
    controller = _controller(tmp_path, kodi, [Viewer(name="anna"), Viewer(name="bob")], collector)
    controller.on_av_started()
    controller.on_tick()
    controller.on_stopped(completed=False)
    controller.on_abort()
    assert collector.kinds() == ["start", "stop"]
    assert kodi.multiselect_calls == []


def test_abort_with_nothing_playing_emits_nothing(tmp_path):
    collector = Collector()
    controller = _controller(tmp_path, _kodi([]), [Viewer(name="anna")], collector)
    controller.on_abort()
    assert collector.events == []


# --- prompt ----------------------------------------------------------------

def test_an_unresolved_stop_prompts_on_the_next_tick_and_attributes_that_watch(tmp_path):
    collector = Collector()
    kodi = _kodi([], profile="Guest")
    kodi.multiselect_answer = [1]
    controller = _controller(tmp_path, kodi, [Viewer(name="anna"), Viewer(name="bob")], collector)
    controller.on_av_started()
    controller.on_tick()
    controller.on_stopped(completed=False)
    controller.on_tick()
    stop = collector.events[-1]
    assert stop.kind == "stop"
    assert stop.viewers == ("bob",)
    assert stop.viewers_source == "prompt"


def test_the_answer_is_remembered_under_a_stable_key(tmp_path):
    collector = Collector()
    kodi = _kodi([], profile="Guest")
    kodi.multiselect_answer = [1]
    controller = _controller(tmp_path, kodi, [Viewer(name="anna"), Viewer(name="bob")], collector)
    controller.on_av_started()
    controller.on_tick()
    controller.on_stopped(completed=False)
    controller.on_tick()
    assert PromptMemory(str(tmp_path / "prompts.json")).recall("show:tvdb:83462") == ("bob",)


def test_a_dismissed_prompt_still_emits_the_stop_with_no_viewers(tmp_path):
    collector = Collector()
    kodi = _kodi([], profile="Guest")
    kodi.multiselect_answer = None
    controller = _controller(tmp_path, kodi, [Viewer(name="anna"), Viewer(name="bob")], collector)
    controller.on_av_started()
    controller.on_tick()
    controller.on_stopped(completed=False)
    controller.on_tick()
    assert collector.events[-1].viewers == ()
    assert collector.events[-1].viewers_source is None
    assert PromptMemory(str(tmp_path / "prompts.json")).recall("show:tvdb:83462") is None


def test_a_raising_prompt_still_emits_the_stop(tmp_path):
    collector = Collector()
    kodi = _kodi([], profile="Guest")

    def boom(*args, **kwargs):
        raise RuntimeError("window is gone")

    kodi.multiselect = boom  # type: ignore[method-assign]
    controller = _controller(tmp_path, kodi, [Viewer(name="anna"), Viewer(name="bob")], collector)
    controller.on_av_started()
    controller.on_tick()
    controller.on_stopped(completed=False)
    controller.on_tick()
    assert collector.kinds() == ["start", "stop"]
    assert collector.events[-1].viewers == ()


def test_a_remembered_answer_is_used_without_asking(tmp_path):
    collector = Collector()
    kodi = _kodi([], profile="Guest")
    PromptMemory(str(tmp_path / "prompts.json")).remember("show:tvdb:83462", ("anna",))
    controller = _controller(tmp_path, kodi, [Viewer(name="anna"), Viewer(name="bob")], collector)
    controller.on_av_started()
    controller.on_tick()
    controller.on_stopped(completed=False)
    controller.on_tick()
    assert collector.events[-1].viewers == ("anna",)
    assert kodi.multiselect_calls == []


def test_the_prompt_carries_an_autoclose(tmp_path):
    collector = Collector()
    kodi = _kodi([], profile="Guest")
    kodi.multiselect_answer = [0]
    controller = _controller(tmp_path, kodi, [Viewer(name="anna"), Viewer(name="bob")], collector)
    controller.on_av_started()
    controller.on_tick()
    controller.on_stopped(completed=False)
    controller.on_tick()
    assert kodi.multiselect_calls[0][3] > 0


# --- cadence and resilience ------------------------------------------------

def test_progress_is_emitted_once_per_cadence_bucket(tmp_path):
    collector = Collector()
    kodi = _kodi([])
    kodi.position_ms, kodi.duration_ms = 300_000, 1_000_000
    controller = _controller(tmp_path, kodi, [Viewer(name="anna")], collector)
    controller.on_av_started()
    controller.on_tick()
    controller.on_tick()
    assert collector.kinds() == ["start", "progress"]


def test_nothing_is_emitted_when_no_media_is_playing(tmp_path):
    collector = Collector()
    kodi = _kodi([], overrides={"Player.GetActivePlayers": lambda params: {"result": []}})
    controller = _controller(tmp_path, kodi, [Viewer(name="anna")], collector)
    controller.on_av_started()
    controller.on_tick()
    assert collector.events == []


def test_a_settings_change_replaces_the_settings_and_invalidates_the_index(tmp_path):
    collector = Collector()
    kodi = _kodi([{"id": 42, "type": "tvshow"}])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna", playlists=("Anna TV",))], collector)
    _warm_index(controller, kodi)
    before = len([c for c in kodi.calls if c[0] == "Files.GetDirectory"])
    controller.on_settings_changed(Settings(progress_step=5), Thresholds())
    _warm_index(controller, kodi)
    assert len([c for c in kodi.calls if c[0] == "Files.GetDirectory"]) > before


def test_a_library_scan_clears_the_memoised_show_ids(tmp_path):
    collector = Collector()
    kodi = _kodi([])
    controller = _controller(tmp_path, kodi, [Viewer(name="anna")], collector)
    controller.on_av_started()
    controller.invalidate_index()
    controller.on_av_started()
    lookups = len([c for c in kodi.calls if c[0] == "VideoLibrary.GetTVShowDetails"])
    assert lookups == 2
