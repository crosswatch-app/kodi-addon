# What the add-on sends

What the add-on sends to CrossWatch, and what happens when CrossWatch cannot be reached.

Everything goes only to your own CrossWatch, which then passes it on to whatever services you
have set up there:

- Playback events (start, pause, resume, progress, stop), and a heartbeat. The heartbeat goes
  every five minutes and straight away after pairing or when the viewer names change, and
  carries the names of all configured viewers.
- Kodi's device name, a generated device id and the add-on's version.
- Viewer names, and how they were found (playlist, profile, or the question, which includes
  an answer remembered from it; nothing when the only viewer is
  credited because they are the only one).
- Title, year, season, episode and episode title; the ids Kodi's library has for it (such as
  TMDb, TVDb, IMDb); duration, playback position and progress; the file path, with any user
  name, password and query string removed; and the Plex rating key for
  PlexKodiConnect playback, which is only reported when "Skip PlexKodiConnect playback" is off
  (it is on by default, see [Settings](settings.md#viewers)).

Only episodes and films are reported, and only when Kodi knows at least one real id for them
(such as TMDb, TVDb or IMDb); music, live TV, and files Kodi has not scraped into its library
send nothing.

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
  (`userdata/addon_data/service.crosswatch/`; per Kodi profile: outside the master profile it is
  under `userdata/profiles/<profile>/addon_data/`) before the first send is even attempted. It
  survives CrossWatch being down and Kodi being restarted or switched off.
- It is retried every five minutes while Kodi runs, and straight away when Kodi starts.
- When it is finally delivered, it carries its original time and is marked as replayed,
  so CrossWatch sees the same event arriving late, not a new one. Plex, Emby and
  Jellyfin record the watch at the time it happened; trackers such as Trakt and Simkl,
  and CrossWatch's own history, record it at the time it arrives, so a watch delivered
  late shows up late there. It is removed from the file once CrossWatch accepts it.
- It is dropped, with a warning in the log, if CrossWatch refuses it, if it is more
  than seven days old when Kodi starts, when a new stop would make more than 200 waiting (the
  oldest goes), or if the token has changed since, for example after pairing with a different
  CrossWatch, so a stored stop is never sent to a different CrossWatch. A new address for the
  same instance keeps it. Unpair alone keeps them.

Kodi still marks the item watched in its own library either way. But only this add-on knows
who watched it, so a stop that is ultimately dropped loses the viewer attribution for that
watch, not the watched state itself.

Nothing is stored when no webhook is configured. Every other event (start, pause, resume,
progress, a stop that did not complete a watch, and the heartbeat) is never written to disk:
it is held only in memory for its two-minute window.
