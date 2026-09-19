from resources.lib.service.playback_monitor import PlaybackMonitor
from resources.lib.service.service_monitor import ServiceMonitor


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def on_av_started(self) -> None:
        self.calls.append("av_started")

    def on_paused(self) -> None:
        self.calls.append("paused")

    def on_resumed(self) -> None:
        self.calls.append("resumed")

    def on_stopped(self, completed: bool) -> None:
        self.calls.append(f"stopped:{completed}")

    def invalidate_index(self) -> None:
        self.calls.append("invalidated")


class Exploding(Recorder):
    def on_av_started(self) -> None:
        raise RuntimeError("boom")


def test_playback_callbacks_forward():
    recorder = Recorder()
    monitor = PlaybackMonitor(recorder)
    monitor.onAVStarted()
    monitor.onPlayBackPaused()
    monitor.onPlayBackResumed()
    assert recorder.calls == ["av_started", "paused", "resumed"]


def test_stopped_and_ended_differ_only_in_completion():
    recorder = Recorder()
    monitor = PlaybackMonitor(recorder)
    monitor.onPlayBackStopped()
    monitor.onPlayBackEnded()
    assert recorder.calls == ["stopped:False", "stopped:True"]


def test_a_raising_controller_does_not_escape_a_callback():
    monitor = PlaybackMonitor(Exploding())
    monitor.onAVStarted()  # must not raise


def test_a_video_scan_invalidates_the_index():
    recorder = Recorder()
    ServiceMonitor(recorder, on_settings_changed=lambda: None).onScanFinished("video")
    assert recorder.calls == ["invalidated"]


def test_a_music_scan_is_ignored():
    recorder = Recorder()
    ServiceMonitor(recorder, on_settings_changed=lambda: None).onScanFinished("music")
    assert recorder.calls == []


def test_settings_changes_reach_the_handler():
    seen: list[int] = []
    ServiceMonitor(Recorder(), on_settings_changed=lambda: seen.append(1)).onSettingsChanged()
    assert seen == [1]
