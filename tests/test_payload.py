from dataclasses import replace
from typing import Any

from resources.lib.models import Device, MediaItem, PingEvent, PlaybackEvent
from resources.lib.payload import build_payload

DEVICE = Device(id="htpc-1", name="Living room")

EPISODE = MediaItem(
    media_type="episode",
    library_id=5,
    show_library_id=42,
    title="Just an example",
    year=2026,
    season=1,
    episode=1,
    episode_title="Example episode",
    show_ids={"tmdb": "1419", "tvdb": "83462", "imdb": "tt1219024"},
    episode_ids={"tvdb": "3110601"},
    file="nfs://nas/media/tv/Just an example (2026)/Season 01/S01E01.mkv",
    source="kodi",
    plex_rating_key=None,
)

MOVIE = MediaItem(
    media_type="movie",
    library_id=7,
    show_library_id=None,
    title="Example film",
    year=2024,
    season=None,
    episode=None,
    episode_title=None,
    show_ids={"tmdb": "999"},
    episode_ids={},
    file="nfs://nas/media/movies/Example film (2024)/film.mkv",
    source="kodi",
    plex_rating_key=None,
)


BASE_EVENT = PlaybackEvent(
    kind="stop",
    event_id="e-1",
    session_id="s-1",
    sent_at="2026-09-19T20:31:05Z",
    media=EPISODE,
    viewers=("anna", "bob"),
    viewers_source="playlist",
    position_ms=1_200_000,
    duration_ms=1_320_000,
    percent=90.9,
    completed=False,
)


def _event(**overrides: Any) -> PlaybackEvent:
    # replace() rather than a dict of defaults unpacked into the constructor: unpacking a
    # heterogeneous dict erases every field's type, so the checker can no longer tell a
    # malformed test event from a real one.
    return replace(BASE_EVENT, **overrides)


def test_episode_payload_carries_version_event_and_viewers():
    body = build_payload(_event(), DEVICE)
    assert body["version"] == 1
    assert body["event"] == "stop"
    assert body["event_id"] == "e-1"
    assert body["session_id"] == "s-1"
    assert body["viewers"] == ["anna", "bob"]
    assert body["viewers_source"] == "playlist"
    assert body["device"] == {"id": "htpc-1", "name": "Living room"}


def test_episode_payload_uses_show_ids_and_episode_ids():
    media = build_payload(_event(), DEVICE)["media"]
    assert media["type"] == "episode"
    assert media["show_ids"] == {"tmdb": "1419", "tvdb": "83462", "imdb": "tt1219024"}
    assert media["episode_ids"] == {"tvdb": "3110601"}
    assert (media["season"], media["episode"]) == (1, 1)
    assert "ids" not in media


def test_episode_without_episode_ids_omits_the_key():
    media = MediaItem(**{**EPISODE.__dict__, "episode_ids": {}})
    assert "episode_ids" not in build_payload(_event(media=media), DEVICE)["media"]


def test_movie_payload_uses_ids_and_omits_season_and_episode():
    media = build_payload(_event(media=MOVIE), DEVICE)["media"]
    assert media["type"] == "movie"
    assert media["ids"] == {"tmdb": "999"}
    assert "season" not in media
    assert "episode" not in media
    assert "show_ids" not in media


def test_pkc_playback_sets_source_and_rating_key():
    media = MediaItem(
        **{**EPISODE.__dict__, "source": "plexkodiconnect", "plex_rating_key": "3595", "library_id": None}
    )
    body = build_payload(_event(media=media), DEVICE)["media"]
    assert body["source"] == "plexkodiconnect"
    assert body["plex_rating_key"] == "3595"


def test_known_progress_carries_percent_position_and_duration():
    progress = build_payload(_event(), DEVICE)["progress"]
    assert progress == {"percent": 90.9, "position_ms": 1_200_000, "duration_ms": 1_320_000}


def test_unknown_duration_omits_percent_rather_than_sending_zero():
    progress = build_payload(_event(percent=None, duration_ms=None), DEVICE)["progress"]
    assert "percent" not in progress
    assert "duration_ms" not in progress
    assert progress["position_ms"] == 1_200_000


def test_completed_is_carried_so_it_survives_an_unknown_duration():
    body = build_payload(_event(percent=None, duration_ms=None, completed=True), DEVICE)
    assert body["progress"]["completed"] is True


def test_completed_is_omitted_when_false():
    assert "completed" not in build_payload(_event(), DEVICE)["progress"]


def test_empty_viewers_is_an_empty_list_and_source_is_omitted():
    body = build_payload(_event(viewers=(), viewers_source=None), DEVICE)
    assert body["viewers"] == []
    assert "viewers_source" not in body


def test_a_device_can_carry_the_addon_version():
    assert Device(id="htpc-1", name="Living room", addon_version="1.0.0").addon_version == "1.0.0"


def test_an_unknown_addon_version_is_representable():
    """Defaulted so a device built before the version is known is still constructible."""
    assert Device(id="htpc-1", name="Living room").addon_version == ""


def test_a_ping_carries_viewers_and_neither_media_nor_a_session():
    ping = PingEvent(event_id="e-1", sent_at="2026-09-19T20:00:00Z", viewers=("anna", "bob"))
    assert ping.viewers == ("anna", "bob")
    assert not hasattr(ping, "media")
    assert not hasattr(ping, "session_id")


def test_a_ping_counts_no_skipped_pkc_playbacks_by_default():
    assert PingEvent(event_id="e-1", sent_at="2026-09-19T20:00:00Z", viewers=()).pkc_skipped == 0
