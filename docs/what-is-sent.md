# What the add-on sends

Everything goes to your own CrossWatch and nowhere else. CrossWatch then passes it on to the
services you set up there.

## What is sent

- **Playback**: start, pause, resume, progress and stop, for episodes and films.
- **A heartbeat** every five minutes, and straight away after pairing or a change of viewer
  names. It carries the names of all configured viewers, so CrossWatch can tell which of them
  have a route.
- **Ratings**, if you switched [rating](rating.md) on: the score per viewer, with the same item
  details as a stop, minus the file path and playback position.

Each playback event carries:

- who watched: the viewer names, and whether a playlist, a profile or the question decided it.
- the item: title, year, season, episode and episode title, and the ids Kodi has (such as TMDb,
  TVDb, IMDb).
- playback: duration, position and progress.
- the file path, with any user name and password removed. For a stream URL, everything after
  the `?` is replaced by `<redacted>`, since a query string often carries an access token. So
  are request headers added after a `|` (such as a cookie or an authorization header).
- this Kodi: its device name, the add-on's device id and the add-on version.
- for PlexKodiConnect playback, the Plex rating key. That playback is only reported when "Skip
  PlexKodiConnect playback" is off; it is on by default.

Only episodes and films with at least one real id (such as TMDb, TVDb or IMDb) are reported.
The ids can come from Kodi's library or from the add-on playing the item. Music, live TV and
files Kodi knows nothing about are not reported.

The webhook token is sent in a request header, never in a URL.

Developers: the exact payload is the
[contract](https://github.com/crosswatch-app/kodi-addon/blob/main/docs/contract.md).

## When CrossWatch cannot be reached

The add-on keeps trying each event for two minutes, then drops it. An old progress or pause
event is useless later and could even overwrite a newer resume point, so it is never sent late.
An answer that does not come from CrossWatch, such as a reverse proxy's login page, counts as
not reached.

**A finished watch is the exception.** When a stop completes a watch (played to the end, or
past Kodi's watched threshold, 90 percent by default), the add-on saves it to disk before
sending, so it survives CrossWatch being down and Kodi restarting. It is then:

- retried every five minutes while Kodi runs, and straight away when Kodi starts.
- delivered with its original time, marked as a late delivery. Plex, Emby and Jellyfin record
  the watch at the time it happened; Trakt, Simkl and CrossWatch's own history record it when
  it arrives.
- dropped, with a warning in the log, when CrossWatch refuses it, when it is more than seven
  days old at Kodi's start, when more than 200 are waiting (the oldest goes), or when the token
  has changed since, for example by pairing with a different CrossWatch. A stored watch is never
  sent to a different CrossWatch.

Ratings are kept and retried the same way.

Kodi marks the item watched in its own library regardless. A dropped stop loses only who
watched it.

The saved watches are in `outbox.json` in the add-on's data folder,
`userdata/addon_data/service.crosswatch/` (in another Kodi profile:
`userdata/profiles/<profile>/addon_data/service.crosswatch/`). No other event is written to
disk.
