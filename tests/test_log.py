import pytest

from resources.lib import log as logmod


@pytest.fixture
def lines():
    logmod.reset()
    captured: list[str] = []
    logmod.configure(log_dir=None, debug=True, sink=lambda msg, level: captured.append(msg))
    yield captured
    logmod.reset()


def test_redacts_a_query_string():
    assert logmod.redact("http://host:8787/webhook/kodi?profile=secret") == "http://host:8787/webhook/kodi?<redacted>"


def test_redacts_userinfo_in_a_share_path():
    assert logmod.redact("smb://user:hunter2@nas/media/file.mkv") == "smb://<redacted>@nas/media/file.mkv"


def test_redacts_a_url_embedded_in_a_longer_message():
    got = logmod.redact("unknown url type: 'crosswatch.local/webhook/kodi?profile=secret'")
    assert "secret" not in got


def test_leaves_ordinary_text_alone():
    assert logmod.redact("Just an example (2026)") == "Just an example (2026)"


def test_leaves_a_title_containing_a_question_mark_alone():
    assert logmod.redact("Who Framed Roger Rabbit?") == "Who Framed Roger Rabbit?"


def test_leaves_a_dotted_media_filename_containing_a_question_mark_intact():
    path = "/media/tv/Doctor.Who/S01E01.Are.You.My.Mummy?.mkv"
    assert logmod.redact(path) == path


def test_leaves_a_share_path_with_a_question_mark_intact():
    path = "nfs://nas/tv/Doctor.Who/S01E01.Are.You.My.Mummy?.mkv"
    assert logmod.redact(path) == path


def test_leaves_a_windows_path_alone():
    assert logmod.redact(r"C:\media\Whats.Up.Doc?.mkv") == r"C:\media\Whats.Up.Doc?.mkv"


def test_redacting_a_url_does_not_eat_the_sentence_after_it():
    got = logmod.redact("See http://host/a?tok=1. Next sentence.")
    assert "tok=1" not in got
    assert got.endswith(". Next sentence.")


def test_formats_event_name_and_fields(lines):
    logmod.get_logger("identity").info("identity.resolved", source="playlist", viewers_count=1)
    assert lines == ["[identity] identity.resolved | source=playlist, viewers_count=1"]


def test_escapes_newlines_so_a_title_cannot_forge_a_line(lines):
    logmod.get_logger("media").debug("media.resolved", title="Foo\n2026-01-01 [identity] identity.resolved | x=1")
    assert len(lines) == 0  # debug does not reach the sink
    logmod.get_logger("media").info("media.resolved", title="Foo\nBar")
    assert lines == [r"[media] media.resolved | title=Foo\nBar"]


def test_bound_fields_appear_on_every_line(lines):
    log = logmod.get_logger("session").bind(session_id="s1")
    log.info("playback.start")
    log.info("playback.stop", percent=91.2)
    assert lines == [
        "[session] playback.start | session_id=s1",
        "[session] playback.stop | session_id=s1, percent=91.2",
    ]


def test_debug_never_reaches_the_kodi_sink_even_when_enabled(lines):
    logmod.get_logger("x").debug("detail")
    logmod.get_logger("x").info("notable")
    assert lines == ["[x] notable"]


def test_set_debug_takes_effect_without_reconfiguring(tmp_path):
    logmod.reset()
    logmod.configure(log_dir=str(tmp_path), debug=True, sink=lambda msg, level: None)
    logmod.get_logger("x").debug("first")
    logmod.set_debug(False)
    logmod.get_logger("x").debug("second")
    contents = (tmp_path / "crosswatch.log").read_text(encoding="utf-8")
    assert "first" in contents
    assert "second" not in contents
    logmod.reset()


def test_is_debug_reports_the_current_state():
    logmod.reset()
    logmod.configure(log_dir=None, debug=False, sink=lambda msg, level: None)
    assert logmod.is_debug() is False
    logmod.set_debug(True)
    assert logmod.is_debug() is True
    logmod.reset()


def test_an_unwritable_log_dir_does_not_raise(tmp_path, monkeypatch):
    logmod.reset()

    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(logmod.os, "makedirs", boom)
    captured: list[str] = []
    logmod.configure(log_dir=str(tmp_path / "logs"), debug=True, sink=lambda msg, level: captured.append(msg))
    logmod.get_logger("x").info("still.works")
    assert any("still.works" in line for line in captured)
    logmod.reset()


def test_file_rotates_on_the_tracked_size(tmp_path, monkeypatch):
    logmod.reset()
    monkeypatch.setattr(logmod, "MAX_BYTES", 200)
    logmod.configure(log_dir=str(tmp_path), debug=True, sink=lambda msg, level: None)
    log = logmod.get_logger("x")
    for i in range(50):
        log.debug("filler", i=i, padding="x" * 40)
    assert (tmp_path / "crosswatch.log").exists()
    assert (tmp_path / "crosswatch.1.log").exists()
    logmod.reset()


def test_timing_reports_duration_and_namespaced_phases(tmp_path):
    logmod.reset()
    logmod.configure(log_dir=str(tmp_path), debug=True, sink=lambda msg, level: None)
    with logmod.log_timing(logmod.get_logger("index"), "index.rebuild", playlists=2) as timer:
        timer.mark("anna")
    contents = (tmp_path / "crosswatch.log").read_text(encoding="utf-8")
    assert "index.rebuild" in contents
    assert "outcome=success" in contents
    assert "duration_ms=" in contents
    assert "phase_anna_ms=" in contents
    logmod.reset()


def test_timing_reports_failure_and_still_raises(tmp_path):
    logmod.reset()
    logmod.configure(log_dir=str(tmp_path), debug=True, sink=lambda msg, level: None)
    with pytest.raises(ValueError), logmod.log_timing(logmod.get_logger("index"), "index.rebuild"):
        raise ValueError("boom")
    assert "outcome=error" in (tmp_path / "crosswatch.log").read_text(encoding="utf-8")
    logmod.reset()


def test_a_phase_named_like_a_reserved_field_cannot_mask_the_real_exception(tmp_path):
    logmod.reset()
    logmod.configure(log_dir=str(tmp_path), debug=True, sink=lambda msg, level: None)
    with pytest.raises(ValueError, match="real"), logmod.log_timing(logmod.get_logger("x"), "e") as timer:
        timer.mark("duration")
        timer.mark("outcome")
        raise ValueError("real")
    logmod.reset()
