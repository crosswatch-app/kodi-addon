# Troubleshooting

Problems listed by what you see, with what to check.

## Nothing arrives in CrossWatch

- Check the Status line: if it says "Not paired", pair again.
- Check CrossWatch is reachable at the paired address.
- If the add-on is switched off in CrossWatch, pairing itself will say so.
- PlexKodiConnect playback is skipped by default; see "Skip PlexKodiConnect playback" in [Settings](settings.md#viewers).
- Using `https://`: the certificate must come from a public certificate authority. A
  certificate problem shows its own message when pairing; after pairing, the add-on's log
  shows `CERTIFICATE_VERIFY_FAILED` on each failed send.

## Playback arrives under nobody, or under the wrong person

- Check the viewer's name matches the name in that person's route "Username whitelist"
  exactly.
- Check the viewer has a playlist or Kodi profile configured.
- Check whether a playback-driven playlist (such as "Continue Watching") is attributing it to
  the wrong person; see [Smart playlists](who-watched.md#smart-playlists).
- Check the [Remembered answers](remembered-answers.md) screen for a show shown as "Covered by" a playlist.

## The who-watched question never appears

See [The question at the end](who-watched.md#the-question-at-the-end) for the cases where it is
not asked.

## A playlist shows as missing

The playlist was renamed or deleted. Edit that viewer's playlists ([Viewers](viewers.md)) and point it at a playlist
that still exists.

## A viewer shows "No CrossWatch route"

No CrossWatch route accepts that viewer's name, so their playback has nowhere to go. Check, in
order:

1. The name in the route's username whitelist against the viewer's name. They must be the
   same name; CrossWatch ignores upper and lower case and punctuation when comparing.
2. That the person has a route in CrossWatch at all. If not, create one with their name in its
   username whitelist.
3. Whether the viewer was just renamed. The whitelist may still have the old name: change it
   to the new one.

A route with an empty whitelist accepts every viewer, unless a CrossWatch profile is set on
that route. The line can take a moment to change: it updates when CrossWatch answers the add-on's
next check-in (every five minutes, and straight away when the viewer names change).

## A playlist shows as unusable

The playlist is not a TV show or film playlist, so it can never credit anyone. It is not listed
in the picker, so there is nothing to untick: open that viewer's "Playlists" and press Done to
remove it, or change the playlist's type in Kodi to shows or films. The viewer's other
playlists keep working in the meantime.

If Kodi has no TV show or film smart playlists at all, the viewer's "Playlists" shows the "Kodi
has no smart playlists of TV shows or films yet" message instead of the list, so Done is not
available. In that case, make a TV show or film smart playlist ("Playlists" then opens, and
Done removes the unusable one), change the unusable playlist's type in Kodi to shows or films,
or remove the viewer and add them again.

## Logs

The add-on writes its own log under
`userdata/addon_data/service.crosswatch/logs/crosswatch.log`
(see the [Kodi wiki](https://kodi.wiki/view/Userdata) for where `userdata` is on your system).
Turn on "Enable debug logging" for more detail.

The log never contains the webhook token or a pairing code. Normal lines carry counts, not
names. Debug lines carry titles and viewer names.

## Getting help

Open an issue at [crosswatch-app/kodi-addon](https://github.com/crosswatch-app/kodi-addon/issues).
A Kodi forum thread will follow with the first release.
