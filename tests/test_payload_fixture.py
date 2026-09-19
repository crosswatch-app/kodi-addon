"""The wire format, pinned against a payload a real Kodi actually sent.

Captured on Kodi 21.3 Omega playing a library episode whose show was a member of a smart
playlist held by one viewer, so identity resolved through the playlist. Names, paths and
the two uuids are replaced; the ids, the timings and the shape are exactly as observed.

A unit test can only assert that the builder agrees with itself. This one asserts it still
agrees with something Kodi produced.
"""

import json
from pathlib import Path

from resources.lib.models import Device, MediaItem, PlaybackEvent
from resources.lib.payload import build_payload

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "stop_episode.json"


def test_build_payload_still_matches_a_payload_captured_from_a_real_kodi():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    event = PlaybackEvent(
        kind="stop",
        event_id="event-1",
        session_id="session-1",
        sent_at="2026-09-19T18:02:25Z",
        media=MediaItem(
            media_type="episode",
            library_id=1,
            show_library_id=2,
            title="Example show",
            year=2022,
            season=1,
            episode=1,
            episode_title="Example episode",
            show_ids={"imdb": "tt18335752", "tmdb": "157744", "tvdb": "416491"},
            episode_ids={"imdb": "tt18469978", "tmdb": "4014249", "tvdb": "9032340"},
            file="smb://nas/tv/Example show (2022)/Season 01/S01E01.mkv",
        ),
        viewers=("anna",),
        viewers_source="playlist",
        position_ms=2262631,
        duration_ms=3633536,
        percent=62.3,
    )
    assert build_payload(event, Device(id="device-1", name="Living room")) == expected


def test_the_captured_payload_omits_completed_because_the_episode_was_not_finished():
    """Absence is the signal. A false completed flag would have to be sent every event."""
    assert "completed" not in json.loads(FIXTURE.read_text(encoding="utf-8"))["progress"]
