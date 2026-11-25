# Settings

Every add-on setting, with its default. Open them under Add-ons > My add-ons > Services >
CrossWatch for Kodi > Configure. Settings marked "Advanced level" only show after you switch
Kodi's settings screen to the Advanced level.

## CrossWatch

| Setting | Default | What it does |
|---|---|---|
| Pair with CrossWatch | | Connects this Kodi to CrossWatch with a pairing code. See [Pairing](connect.md#pairing). |
| Status | | Read-only. "Paired with `<instance>` at `<address>`" or "Not paired". See [The Status line](connect.md#the-status-line). |
| Unpair | | Disconnects this Kodi from CrossWatch. Only shown while connected. |
| Webhook URL | blank | Advanced level. The CrossWatch endpoint, for a [manual setup](connect.md#manual-setup-advanced). Pairing fills it in. |
| Webhook token | blank | Advanced level. The token, for a manual setup. Pairing fills it in. |
| Device id (blank to generate) | blank | Advanced level. Identifies this Kodi to CrossWatch. Leave it blank and the add-on makes one. A change takes effect after Kodi restarts. |

The device name CrossWatch shows is Kodi's own, set under Settings > Services > General >
Device name.

## Viewers

| Setting | Default | What it does |
|---|---|---|
| Configure viewers and playlists | | Add, change or remove viewers. See [Viewers](viewers.md). |
| Remembered answers | | See, change or forget the answers remembered per show. See [Remembered answers](remembered-answers.md). |
| Ask who watched a film | On | Off: films that playlists and profiles do not settle are sent with no viewer instead of asking. |
| Ask for a rating | Off | Films, Episodes, or Films and episodes: ask for a score after a finished watch. See [Rating](rating.md). |
| When to ask | After each watch | Only shown when rating is on. "At the end, for everything watched" saves the questions until playback stops for good. |
| Shared rating | Off | Only shown when rating is on. One question for everyone who watched, instead of one each. |
| Write the rating to Kodi's library | Off | Only shown when rating is on. Also sets Kodi's own user rating. See [Kodi's own rating](rating.md#kodis-own-rating). |
| Skip PlexKodiConnect playback | On | Playback through PlexKodiConnect is not reported, so that a household also running CrossWatch's Plex watcher does not count it twice. Off: it is reported, marked as PlexKodiConnect playback. Not yet tested against a real PlexKodiConnect. |

## Library sync

See [Library sync](library-sync.md).

| Setting | Default | What it does |
|---|---|---|
| Set up library sync | | Opens the [setup screen](library-sync.md#set-it-up): who owns what, and Start. |
| Library sync status | | Opens the [status screen](library-sync.md#the-status-screen). |
| Sync this library with CrossWatch | Off | Start turns it on. Turning it off stops sync on this device and frees it, so another device can become the sync device. Turning it back on does not restart sync: run Set up library sync. |
| Send removals made in Kodi | Off | On: an unwatch or a cleared rating made in Kodi is sent, and one from your services is written into Kodi, when removals are on in the sync pair in CrossWatch too. Off: neither, and sync never removes a watch or a rating. |
| A shared item counts as watched when | Every owner watched it | Or "Any owner watched it". When Kodi shows an item with several owners as watched. |

## Advanced

| Setting | Default | What it does |
|---|---|---|
| Progress report interval (seconds) | 60 | 30 to 600. How often the playback position is sent while something plays, and after a seek. |
| Playlist refresh interval (minutes) | 60 | Advanced level. 5 to 720. How often [playlist membership](who-watched.md#smart-playlists) is re-read. A library scan or clean and a settings change also trigger it. |
| Enable debug logging (records viewing history to disk) | Off | Writes a detailed log file, including titles and viewer names. Takes effect at once. Leave off unless you are troubleshooting. See [Logs](troubleshooting.md#logs). |
