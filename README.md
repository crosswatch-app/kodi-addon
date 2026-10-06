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

## Connecting to CrossWatch

### Pairing

This is the normal way to connect. In the add-on settings, CrossWatch category, select "Pair
with CrossWatch".

1. In CrossWatch, open the Kodi instance you want this device to use and ask it for a pairing
   code.
2. Back in the add-on, type the CrossWatch address: `192.168.1.10:8787` is enough, `http://` is
   assumed if you leave it off, and a trailing slash or a full webhook URL both work too.
3. Type the code it gave you: 6 characters, capitals and digits, valid for 10 minutes and good
   for one use. Case and spaces do not matter, so `ab 23 cd` and `AB23CD` are the same.

On success you get a notification naming the CrossWatch instance, and the Status line
(read-only, just under the Pair button) reads "Paired with `<instance>` at `<address>`".

If it does not succeed, nothing is changed: the previous connection, if any, stays in place.
What you see depends on what went wrong:

- A wrong or expired code: "Code wrong or expired. Get a new code in CrossWatch."
- Too many tries: "Too many tries. Wait a minute and try again."
- CrossWatch cannot be reached at that address: "Can't reach CrossWatch at `<address>`."
- Some other failure on CrossWatch's side: "Pairing failed: CrossWatch answered HTTP `<code>`."
- The Kodi add-on is switched off in CrossWatch: "The Kodi add-on is switched off in
  CrossWatch. Turn it on there, then pair again."
- An address that cannot be used, such as one starting with `ftp://`: "Not a CrossWatch
  address: `<address>`"

Pressing Pair or Unpair closes the settings screen first, saving anything else you changed
there. The add-on switches to the new connection as soon as you finish, no Kodi restart
needed, and sends a heartbeat right away, so CrossWatch sees it within seconds.

### Link (shortcut)

Link only works for a Kodi that CrossWatch can already reach over its own network control
(JSON-RPC). Kodi's web server is off by default, so most households cannot use this and should
pair instead.

When it applies, CrossWatch starts it: the TV shows "Link this Kodi to CrossWatch at
`<address>`?". Choosing Yes connects. Choosing No, or not answering within 60 seconds, changes
nothing.

### The Status line

This line is read-only; it only ever reports what the add-on last did. It reads one of:

- "Paired with `<instance>` at `<address>`", after pairing.
- "Linked to CrossWatch at `<address>`", right after a Link. It changes to "Paired with
  `<instance>` at `<address>`" once CrossWatch answers the first heartbeat.
- "Not paired", when nothing is connected.

### Unpair

The "Unpair" button appears only while the add-on is connected. It asks for confirmation, then
stops reporting. Any completed watches already kept on disk for delivery during an outage are
not deleted: they are still sent if you pair again with the same CrossWatch instance. Moving to
a different CrossWatch does not need Unpair first, just pair again.

### Manual setup (advanced)

If you would rather not pair, "Webhook URL" and "Webhook token" are available at the Advanced
settings level (use the settings level selector on Kodi's settings screen to see them).
CrossWatch's instance page also offers a manual option: a full URL with `?token=` in it. You
can paste that whole URL into "Webhook URL"; the add-on takes the token out of it and moves it
into "Webhook token" for you. Whichever way the token gets there, it is only ever sent in a
request header, never in the URL.

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
