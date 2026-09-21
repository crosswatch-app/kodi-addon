from resources.lib.identity import UNRESOLVED, Identity, resolve
from resources.lib.models import MediaItem, Viewer
from resources.lib.playlist_index import PlaylistIndex

ANNA = Viewer(name="anna", playlists=("Anna TV",), profiles=("Anna",))
BOB = Viewer(name="bob", playlists=("Bob TV",))


def _episode(show_library_id: int | None = 42) -> MediaItem:
    return MediaItem(
        media_type="episode",
        library_id=5,
        show_library_id=show_library_id,
        title="Example",
        year=2026,
        season=1,
        episode=1,
        episode_title=None,
    )


def _movie(library_id: int | None = 3) -> MediaItem:
    return MediaItem(
        media_type="movie",
        library_id=library_id,
        show_library_id=None,
        title="Film",
        year=2024,
        season=None,
        episode=None,
        episode_title=None,
    )


def test_episode_resolves_through_its_parent_show():
    index = PlaylistIndex({("tvshow", 42): ("anna",)})
    assert resolve(index, [ANNA, BOB], _episode(), "Master user") == Identity(("anna",), "playlist")


def test_movie_resolves_directly_by_its_own_id():
    index = PlaylistIndex({("movie", 3): ("bob",)})
    assert resolve(index, [ANNA, BOB], _movie(), "Master user") == Identity(("bob",), "playlist")


def test_a_shared_playlist_yields_both_viewers():
    index = PlaylistIndex({("tvshow", 42): ("anna", "bob")})
    assert resolve(index, [ANNA, BOB], _episode(), "").viewers == ("anna", "bob")


def test_playlist_wins_over_a_matching_profile():
    index = PlaylistIndex({("tvshow", 42): ("bob",)})
    assert resolve(index, [ANNA, BOB], _episode(), "Anna") == Identity(("bob",), "playlist")


def test_profile_answers_when_no_playlist_matches():
    assert resolve(PlaylistIndex(), [ANNA, BOB], _episode(), "Anna") == Identity(("anna",), "profile")


def test_profile_matching_is_case_insensitive():
    assert resolve(PlaylistIndex(), [ANNA], _episode(), "anna").viewers == ("anna",)


def test_an_unmatched_profile_leaves_identity_unresolved():
    assert resolve(PlaylistIndex(), [ANNA, BOB], _episode(), "Guest") is UNRESOLVED


def test_an_empty_profile_label_leaves_identity_unresolved():
    assert resolve(PlaylistIndex(), [ANNA], _episode(), "") is UNRESOLVED


def test_an_episode_with_no_parent_show_skips_the_playlist_step():
    index = PlaylistIndex({("tvshow", 42): ("anna",)})
    assert resolve(index, [ANNA], _episode(show_library_id=None), "Guest") is UNRESOLVED


def test_no_index_yet_still_falls_through_to_profile():
    assert resolve(None, [ANNA], _episode(), "Anna") == Identity(("anna",), "profile")
