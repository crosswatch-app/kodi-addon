# Settings

Every add-on setting by category, with its default and what it does.

## CrossWatch

| Setting | Default | What it does |
|---|---|---|
| Pair with CrossWatch | | Starts pairing: enter the CrossWatch address, then the pairing code. |
| Status | (read-only) | Shows "Paired with `<instance>` at `<address>`" or "Not paired". |
| Unpair | | Disconnects this Kodi from CrossWatch. Shown while a webhook token is set (paired or manual). |
| Webhook URL | (blank) | Advanced level. The full webhook address for a manual setup, such as `http://192.168.1.10:8787/webhook/kodiwatcher`. Pairing fills it in. |
| Webhook token | (blank) | Advanced level. Manual webhook token, instead of pairing. |
| Device id (blank to generate) | (blank) | Advanced level. Leave blank to have the add-on generate one. Takes effect after Kodi restarts. |

The device name sent to CrossWatch is Kodi's own device name (Settings > Services > General >
Device name).

## Viewers

| Setting | Default | What it does |
|---|---|---|
| Configure viewers and playlists | | Add, edit or remove viewers and their playlists or Kodi profiles. |
| Remembered answers | | View, change or forget who-watched answers remembered per show. |
| Ask who watched a film | on | Whether the who-watched question is also asked after a film. |
| Skip PlexKodiConnect playback | on | PlexKodiConnect playback is not reported, so a household also running CrossWatch's own Plex watcher does not get the same watch counted twice. Turn off to report PlexKodiConnect playback instead, labelled as such. This setting has not been tested, because PlexKodiConnect is not available on the test machine. |

## Advanced

| Setting | Default | Range | What it does |
|---|---|---|---|
| Progress report interval (seconds) | 60 | 30 to 600, steps of 30 | Standard level. How often a progress event is sent while something is playing, and again after a seek. |
| Playlist refresh interval (minutes) | 60 | 5 to 720, steps of 5 | Advanced level. How often playlist membership is re-read. Also re-read after a library scan or clean, and after a settings change. |
| Enable debug logging (records viewing history to disk) | off | | Writes the add-on's own log file (see [Logs](troubleshooting.md#logs)), with every detail including titles and viewer names. Applies straight away. Leave off unless troubleshooting. |
