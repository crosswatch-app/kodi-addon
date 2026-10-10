# What the add-on sends

What the add-on sends to CrossWatch, and what happens when CrossWatch cannot be reached.

Everything goes only to your own CrossWatch, which then passes it on to whatever services you
have set up there:

- Playback events (start, pause, resume, progress, stop) and a heartbeat every five minutes.
- The device name, a generated device id and the add-on's version.
- Viewer names, and how they were found (playlist, profile or the question).
- Title, year, season and episode; the ids Kodi's library has for it (such as TMDb, TVDb,
  IMDb); playback position and progress; the file path; and, for PlexKodiConnect playback,
  the Plex rating key.

The webhook token travels only in a request header, never in a URL. Nothing is sent anywhere
other than your own CrossWatch.

Developers: the exact payload is the
[contract](https://github.com/crosswatch-app/kodi-addon/blob/main/docs/contract.md).

## When CrossWatch cannot be reached

Every playback event (start, pause, resume, progress, stop) and the periodic heartbeat is
sent only while it is still true. The add-on keeps trying for two minutes from the moment
the event happened, then gives up on it. It never sends one late, because an old position
could overwrite a newer resume point.

The one exception is a stop that completes a watch, meaning Kodi played the item to the end,
or played to at least its watched threshold (90 percent by default, the
`playcountminimumpercent` advanced setting, the same rule Kodi itself uses).

- That stop is written to `outbox.json` in the add-on's data folder
  (`userdata/addon_data/service.crosswatch/`) before the first send is even attempted. It
  survives CrossWatch being down and Kodi being restarted or switched off.
- It is retried every five minutes while Kodi runs, and straight away when Kodi starts.
- When it is finally delivered, it carries its original time and is marked as replayed,
  so CrossWatch sees the same event arriving late, not a new one. Plex, Emby and
  Jellyfin record the watch at the time it happened; trackers such as Trakt and Simkl,
  and CrossWatch's own history, record it at the time it arrives, so a watch delivered
  late shows up late there. It is removed from the file once CrossWatch accepts it.
- It is dropped, with a warning in the log, if CrossWatch refuses it, if it has waited more
  than seven days, if more than 200 stops are already waiting (oldest dropped first), or if
  the add-on has since been paired with a different CrossWatch instance (a new token), so a
  stored stop is never sent to a different CrossWatch. A new address for the same instance
  keeps it.

Kodi still marks the item watched in its own library either way. But only this add-on knows
who watched it, so a stop that is ultimately dropped loses the viewer attribution for that
watch, not the watched state itself.

Nothing is stored when no webhook is configured. No other event is written to disk: progress,
pause and heartbeat events are held only in memory for their two-minute window.
