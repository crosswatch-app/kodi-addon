from resources.lib.media import MediaResolver, strip_credentials
from tests.fakes import FakeKodi

EPISODE_ITEM = {
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
        "uniqueid": {"unknown": "12345"},
    }
}
SHOW_DETAILS = {"tvshowdetails": {"uniqueid": {"tmdb": "1419", "tvdb": "83462", "imdb": "tt1219024"}}}


def _kodi(item=None, show=None, extra=None):
    handlers = {
        "Player.GetActivePlayers": lambda params: {"result": [{"playerid": 1, "type": "video"}]},
        "Player.GetItem": lambda params: item if item is not None else EPISODE_ITEM,
        "VideoLibrary.GetTVShowDetails": lambda params: show if show is not None else SHOW_DETAILS,
    }
    handlers.update(extra or {})
    return FakeKodi(rpc_handlers=handlers)


def test_strips_smb_credentials():
    assert strip_credentials("smb://user:pass@nas/media/x.mkv") == "smb://nas/media/x.mkv"


def test_strips_nfs_credentials():
    assert strip_credentials("nfs://user:pass@nas/tv/x.mkv") == "nfs://nas/tv/x.mkv"


def test_leaves_a_clean_path_alone():
    assert strip_credentials("nfs://nas/tv/x.mkv") == "nfs://nas/tv/x.mkv"


def test_leaves_a_plugin_url_alone():
    url = "plugin://plugin.video.plexkodiconnect/tvshows/3595/"
    assert strip_credentials(url) == url


def test_redacts_a_credential_carried_in_a_query_string():
    assert strip_credentials("https://cdn/media.mkv?token=s3cret") == "https://cdn/media.mkv?<redacted>"


def test_a_query_string_credential_never_reaches_the_model():
    item = {"item": {**EPISODE_ITEM["item"], "file": "https://cdn/tv/S01E01.mkv?token=s3cret"}}
    media = MediaResolver(_kodi(item=item)).resolve()
    assert media is not None
    assert "s3cret" not in (media.file or "")


def test_resolves_an_episode_with_show_ids_from_the_parent():
    media = MediaResolver(_kodi()).resolve()
    assert media is not None
    assert media.media_type == "episode"
    assert media.show_library_id == 42
    assert (media.season, media.episode) == (1, 1)
    assert media.title == "Just an example"
    assert media.episode_title == "Example episode"
    assert media.show_ids == {"tmdb": "1419", "tvdb": "83462", "imdb": "tt1219024"}


def test_unknown_uniqueid_keys_are_skipped_not_guessed():
    media = MediaResolver(_kodi()).resolve()
    assert media is not None
    assert media.episode_ids == {}


def test_real_episode_ids_are_carried():
    item = {"item": {**EPISODE_ITEM["item"], "uniqueid": {"tvdb": "3110601", "unknown": "x"}}}
    media = MediaResolver(_kodi(item=item)).resolve()
    assert media is not None
    assert media.episode_ids == {"tvdb": "3110601"}


def test_credentials_in_the_playing_path_never_reach_the_model():
    item = {"item": {**EPISODE_ITEM["item"], "file": "smb://user:hunter2@nas/tv/S01E01.mkv"}}
    media = MediaResolver(_kodi(item=item)).resolve()
    assert media is not None
    assert media.file == "smb://nas/tv/S01E01.mkv"
    assert "hunter2" not in (media.file or "")


def test_show_ids_are_fetched_once_per_show():
    kodi = _kodi()
    resolver = MediaResolver(kodi)
    resolver.resolve()
    resolver.resolve()
    assert len([c for c in kodi.calls if c[0] == "VideoLibrary.GetTVShowDetails"]) == 1


def test_a_show_with_no_ids_is_not_retried_every_episode():
    kodi = _kodi(show={"tvshowdetails": {"uniqueid": {}}})
    resolver = MediaResolver(kodi)
    resolver.resolve()
    resolver.resolve()
    assert len([c for c in kodi.calls if c[0] == "VideoLibrary.GetTVShowDetails"]) == 1


def test_forget_shows_clears_the_memo():
    kodi = _kodi()
    resolver = MediaResolver(kodi)
    resolver.resolve()
    resolver.forget_shows()
    resolver.resolve()
    assert len([c for c in kodi.calls if c[0] == "VideoLibrary.GetTVShowDetails"]) == 2


def test_resolves_a_movie_with_its_own_ids():
    item = {
        "item": {
            "id": 7,
            "type": "movie",
            "title": "Example film",
            "year": 2024,
            "file": "nfs://nas/movies/film.mkv",
            "uniqueid": {"tmdb": "999"},
        }
    }
    media = MediaResolver(_kodi(item=item)).resolve()
    assert media is not None
    assert (media.media_type, media.library_id, media.season) == ("movie", 7, None)
    assert media.show_ids == {"tmdb": "999"}


def test_plexkodiconnect_playback_is_labelled_and_carries_the_rating_key():
    item = {
        "item": {
            "id": -1,
            "type": "episode",
            "title": "Example episode",
            "showtitle": "Show",
            "season": 1,
            "episode": 2,
            "year": 2026,
            "file": "plugin://plugin.video.plexkodiconnect/tvshows/3595/",
            "uniqueid": {},
        }
    }
    media = MediaResolver(_kodi(item=item, show={"tvshowdetails": {}})).resolve()
    assert media is not None
    assert media.source == "plexkodiconnect"
    assert media.plex_rating_key == "3595"


def test_local_playback_is_labelled_kodi():
    media = MediaResolver(_kodi()).resolve()
    assert media is not None
    assert media.source == "kodi"


def test_returns_none_when_nothing_is_playing():
    kodi = FakeKodi(rpc_handlers={"Player.GetActivePlayers": lambda params: {"result": []}})
    assert MediaResolver(kodi).resolve() is None


def test_returns_none_for_a_media_type_we_do_not_report():
    item = {"item": {"id": 1, "type": "musicvideo", "title": "x", "file": "x"}}
    assert MediaResolver(_kodi(item=item)).resolve() is None


def test_a_failing_show_lookup_still_yields_the_episode():
    def boom(params):
        raise RuntimeError("no show")

    media = MediaResolver(_kodi(extra={"VideoLibrary.GetTVShowDetails": boom})).resolve()
    assert media is not None
    assert media.show_ids == {}
    assert media.season == 1
