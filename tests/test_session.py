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


def test_progress_is_emitted_once_per_step_bucket():
    session = _session()
    session.sample(10_000, 100_000)
    assert session.should_emit_progress(25) is False
    session.sample(30_000, 100_000)
    assert session.should_emit_progress(25) is True
    session.sample(40_000, 100_000)
    assert session.should_emit_progress(25) is False
    session.sample(55_000, 100_000)
    assert session.should_emit_progress(25) is True


def test_no_progress_is_emitted_while_percent_is_unknown():
    session = _session()
    session.sample(30_000, None)
    assert session.should_emit_progress(25) is False
