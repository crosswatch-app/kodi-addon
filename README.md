# kodi-addon

Kodi add-on that reports playback and viewers to [CrossWatch](https://github.com/cenodude/CrossWatch).

> ## Not ready to run
>
> This is unreleased work in progress. There is no release, no repository zip, and no
> supported way to install it. Please do not run it against a CrossWatch instance you care
> about.
>
> **The receiving end is not in a CrossWatch release yet.** The add-on posts to
> `/webhook/kodiwatcher`, which exists in CrossWatch's development code but not in
> any release up to 0.13.2. A CrossWatch without it answers 404, and the add-on sends
> nothing anywhere else, so pointing it at an older CrossWatch delivers nothing, but
> also changes nothing.
>
> **Testing is partial.** The add-on is verified against a real Kodi 21.3 Omega and,
> since 2026-10-06, end to end against a development build of CrossWatch that has the
> add-on endpoint: the handshake that switches CrossWatch into add-on mode, routing a
> playback by viewer, and replaying a stored stop after an outage and a Kodi restart. Not
> yet verified: delivery through to a real Trakt, Simkl, Plex, Emby or Jellyfin account.

## What it is for

CrossWatch routes a scrobble per user, but Kodi cannot say who pressed play inside a single
profile. A shared MySQL or MariaDB library gives every profile the same watched state, so
separate Kodi profiles are not a workaround either. This add-on works out who is watching
inside Kodi and sends that with the playback event.

It determines the viewer three ways, first match wins:

1. Smart playlist membership of the playing item's parent show.
2. The active Kodi profile.
3. Asking at the end of playback, remembered per show. At stop, never at start, so it
   cannot delay playback, and skipped entirely for a single-viewer household.

The add-on settings have a Remembered answers entry under Viewers, next to Configure
viewers and playlists. It lists every show with a remembered answer and who it is for, with
Change (the who-watched picker, current answer ticked) and Forget (asks again next
episode). Forget all remembered answers, at the bottom, clears everything after a
confirmation; changes apply from the next playback, no restart needed.

Playlist membership is checked first, so it overrides a remembered answer to the prompt. That
makes the choice of playlist matter: it should describe what that person watches, not what
they just happened to play.

- Works well: a fixed list of shows, or rules on genre, tag or similar show attributes.
- Avoid playlists whose membership follows from playback itself, such as "Continue Watching"
  or "In Progress" smart playlists (rules on in-progress, play count or last played). Playing
  a show adds it to such a playlist, so the next episode is attributed to whoever owns it,
  even if someone else watched it. On a real Kodi, a show two people had been credited with
  watching started resolving to one person after a single playback, once it joined that
  person's "Continue Watching" playlist.

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
  the webhook address or token has changed since it was stored, so a stored stop is never
  sent to a different CrossWatch.

Kodi still marks the item watched in its own library either way. But only this add-on knows
who watched it, so a stop that is ultimately dropped loses the viewer attribution for that
watch, not the watched state itself.

Nothing is stored when no webhook is configured. No other event is written to disk: progress,
pause and heartbeat events are held only in memory for their two-minute window.

## Status

| | |
|---|---|
| Kodi | 21 Omega and later, Python 3.11 |
| Contract | implements `docs/contract.md` |
| Verified against | a real Kodi 21.3 Omega, and a contract-asserting stub receiver |
| Verified against a live CrossWatch | no, the route has not shipped |
| Released | no |

The payload contract and the open questions behind it are in
[issue #1](https://github.com/crosswatch-app/kodi-addon/issues/1).

## Licence

GPL-2.0-only. See [LICENSE](LICENSE).
