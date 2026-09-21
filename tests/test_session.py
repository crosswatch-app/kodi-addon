from resources.lib.constants import SEEK_MIN_GAP_SECONDS as SEEK_GAP
from resources.lib.identity import UNRESOLVED
from resources.lib.models import MediaItem
from resources.lib.service.session import PlaybackSession

MEDIA = MediaItem(
    media_type="episode",
    library_id=5,
    show_library_id=42,
    title="Example",
    year=2026,
    season=1,
    episode=1,
    episode_title=None,
)


def _session():
    return PlaybackSession(session_id="s-1", media=MEDIA, identity=UNRESOLVED)


def test_percent_is_unknown_before_any_sample():
    assert _session().percent() is None


def test_percent_derives_from_the_last_sample():
    session = _session()
    session.sample(30_000, 120_000)
    assert session.percent() == 25.0


def test_the_last_sample_survives_for_use_at_stop():
    session = _session()
    session.sample(30_000, 120_000)
    session.sample(90_000, 120_000)
    assert (session.position_ms, session.duration_ms) == (90_000, 120_000)


def test_a_later_sample_without_a_duration_keeps_the_one_already_known():
    session = _session()
    session.sample(30_000, 120_000)
    session.sample(60_000, None)
    assert session.duration_ms == 120_000
    assert session.position_ms == 60_000


def test_percent_stays_unknown_when_duration_is_never_sampled():
    session = _session()
    session.sample(30_000, None)
    assert session.percent() is None
    assert session.position_ms == 30_000


def test_a_zero_duration_is_treated_as_unknown_not_as_zero():
    session = _session()
    session.sample(30_000, 0)
    assert session.percent() is None


def test_percent_is_clamped():
    session = _session()
    session.sample(130_000, 120_000)
    assert session.percent() == 100.0


def test_mark_complete_sets_the_flag_and_fills_position_when_duration_is_known():
    session = _session()
    session.sample(30_000, 120_000)
    session.mark_complete()
    assert session.completed is True
    assert session.position_ms == 120_000
    assert session.percent() == 100.0


def test_mark_complete_survives_an_unknown_duration():
    session = _session()
    session.sample(30_000, None)
    session.mark_complete()
    assert session.completed is True
    assert session.percent() is None


def test_the_first_call_does_not_emit_because_start_already_carried_the_position():
    session = _session()
    session.sample(10_000, 100_000)
    assert session.should_emit_progress(now=100.0, interval=60.0, paused=False) is False


def test_progress_emits_once_the_interval_has_passed():
    session = _session()
    session.sample(10_000, 100_000)
    assert session.should_emit_progress(now=100.0, interval=60.0, paused=False) is False
    assert session.should_emit_progress(now=159.0, interval=60.0, paused=False) is False
    assert session.should_emit_progress(now=160.0, interval=60.0, paused=False) is True


def test_the_interval_restarts_after_each_emission():
    session = _session()
    session.should_emit_progress(now=0.0, interval=60.0, paused=False)
    assert session.should_emit_progress(now=60.0, interval=60.0, paused=False) is True
    assert session.should_emit_progress(now=119.0, interval=60.0, paused=False) is False
    assert session.should_emit_progress(now=120.0, interval=60.0, paused=False) is True


def test_a_seek_emits_without_waiting_for_the_interval():
    session = _session()
    session.should_emit_progress(now=0.0, interval=60.0, paused=False)
    session.note_seek()
    assert session.should_emit_progress(now=SEEK_GAP, interval=60.0, paused=False) is True


def test_a_seek_is_consumed_so_it_emits_once_not_forever():
    session = _session()
    session.should_emit_progress(now=0.0, interval=60.0, paused=False)
    session.note_seek()
    assert session.should_emit_progress(now=SEEK_GAP, interval=60.0, paused=False) is True
    assert session.should_emit_progress(now=SEEK_GAP + 1.0, interval=60.0, paused=False) is False


def test_a_burst_of_seeks_coalesces_into_one_event_per_floor():
    """A held skip button delivers a seek per tick; without the floor this emits per tick."""
    session = _session()
    session.should_emit_progress(now=0.0, interval=60.0, paused=False)
    emitted = 0
    for tick in range(1, 31):
        session.note_seek()
        if session.should_emit_progress(now=float(tick), interval=60.0, paused=False):
            emitted += 1
    assert emitted == 30 // int(SEEK_GAP)


def test_a_seek_still_emits_eventually_rather_than_being_swallowed():
    """The floor delays a seek, it does not discard it."""
    session = _session()
    session.should_emit_progress(now=0.0, interval=60.0, paused=False)
    session.note_seek()
    assert session.should_emit_progress(now=1.0, interval=60.0, paused=False) is False
    assert session.should_emit_progress(now=SEEK_GAP, interval=60.0, paused=False) is True


def test_a_seek_before_the_first_cadence_call_is_not_swallowed():
    """on_av_started installs the session up to a tick before the first cadence call."""
    session = _session()
    session.note_seek()
    session.should_emit_progress(now=0.0, interval=60.0, paused=False)
    assert session.should_emit_progress(now=SEEK_GAP, interval=60.0, paused=False) is True


def test_a_seek_restarts_the_interval_rather_than_stacking_on_it():
    session = _session()
    session.should_emit_progress(now=0.0, interval=60.0, paused=False)
    session.note_seek()
    assert session.should_emit_progress(now=50.0, interval=60.0, paused=False) is True
    assert session.should_emit_progress(now=109.0, interval=60.0, paused=False) is False
    assert session.should_emit_progress(now=110.0, interval=60.0, paused=False) is True


def test_nothing_is_emitted_while_paused():
    session = _session()
    session.should_emit_progress(now=0.0, interval=60.0, paused=False)
    for minute in range(1, 61):
        assert session.should_emit_progress(now=minute * 60.0, interval=60.0, paused=True) is False


def test_a_pause_does_not_bank_time_for_a_burst_on_resume():
    """An hour paused must not make the first tick after resume look an hour overdue."""
    session = _session()
    session.should_emit_progress(now=0.0, interval=60.0, paused=False)
    for minute in range(1, 61):
        session.should_emit_progress(now=minute * 60.0, interval=60.0, paused=True)
    assert session.should_emit_progress(now=3601.0, interval=60.0, paused=False) is False
    assert session.should_emit_progress(now=3661.0, interval=60.0, paused=False) is True


def test_a_seek_while_paused_is_held_until_playback_resumes():
    """The seek survives the pause; the floor is measured from when playback resumed.

    Nothing is lost by the delay: on_resumed samples the position and emits a resume event
    carrying it, so the viewer's new position is already reported. A progress event a tick
    later would only repeat it.
    """
    session = _session()
    session.should_emit_progress(now=0.0, interval=60.0, paused=False)
    session.note_seek()
    assert session.should_emit_progress(now=10.0, interval=60.0, paused=True) is False
    assert session.should_emit_progress(now=11.0, interval=60.0, paused=False) is False
    assert session.should_emit_progress(now=10.0 + SEEK_GAP, interval=60.0, paused=False) is True


def test_progress_is_emitted_even_when_percent_is_unknown():
    """A stream of unknown length still has a position, and the receiver still wants it."""
    session = _session()
    session.sample(30_000, None)
    session.should_emit_progress(now=0.0, interval=60.0, paused=False)
    assert session.should_emit_progress(now=60.0, interval=60.0, paused=False) is True


def test_a_non_positive_interval_never_emits():
    session = _session()
    session.should_emit_progress(now=0.0, interval=0.0, paused=False)
    assert session.should_emit_progress(now=1000.0, interval=0.0, paused=False) is False
