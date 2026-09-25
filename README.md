# kodi-addon

Kodi add-on that reports playback and viewers to [CrossWatch](https://github.com/cenodude/CrossWatch).

> ## Not ready to run
>
> This is unreleased work in progress. There is no release, no repository zip, and no
> supported way to install it. Please do not run it against a CrossWatch instance you care
> about.
>
> **The receiving end does not exist yet.** The add-on posts to `/webhook/kodiwatcher`, a
> route that has not shipped in CrossWatch. Until it does, nothing this sends can be
> delivered.
>
> **Running it against an older CrossWatch can destroy data.** The add-on omits `percent`
> when Kodi does not know the duration, which happens for a stream of unknown length. A
> server older than 0.13.0 turns that missing value into `0`, and zero means watched
> nothing, so it overwrites the viewer's real resume point in Plex or Emby, on `start` as
> well as on `stop`. The add-on warns once when the server reports an older version, but it
> cannot refuse on your behalf. This is the one failure here that loses data rather than
> losing an event.
>
> **Nothing has been tested end to end.** The add-on is verified against a real Kodi 21.3
> Omega and against a stub receiver that asserts the published contract, never against a
> live CrossWatch. See the open pull request for exactly what was and was not verified.

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
- When it is finally delivered, it carries its original time, so CrossWatch sees the same
  event arriving late, not a new one. It is removed from the file once CrossWatch accepts it.
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
