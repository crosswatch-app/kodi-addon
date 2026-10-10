# Troubleshooting

Problems listed by what you see, with what to check.

## Nothing arrives in CrossWatch

- Check the Status line: "Not paired" means pair again. "Paired with ..." only means CrossWatch
  answered at some point (see [The Status line](connect.md#the-status-line)); check Kodi's log
  for `reporter.failed` (CrossWatch could not be reached) or `reporter.rejected` with `status=401`
  (the token was refused: pair again).
- Check CrossWatch is reachable at the paired address.
- Check the Kodi add-on is switched on for this Kodi in CrossWatch. While it is off,
  CrossWatch offers no pairing code, and a code from before is refused as "Code wrong or
  expired". Switching it off after pairing discards this Kodi's token: nothing shows in Kodi,
  and the log shows `reporter.rejected` with `status=401`. Turn it on in CrossWatch and pair
  again. Completed watches still waiting for delivery are then dropped, because the token
  changed.
- Only episodes and films that Kodi has in its library with an id are reported; see
  [What the add-on sends](what-is-sent.md).
- When CrossWatch was down, only completed watches are sent later; everything else is dropped
  after two minutes (see [When CrossWatch cannot be
  reached](what-is-sent.md#when-crosswatch-cannot-be-reached)).
- PlexKodiConnect playback is skipped by default; see "Skip PlexKodiConnect playback" in
  [Settings](settings.md#viewers).
- Using `https://`: the certificate must come from a public certificate authority. A
  certificate problem shows its own message when pairing; after pairing, Kodi's log
  shows `reporter.failed` with `CERTIFICATE_VERIFY_FAILED`, repeated while the add-on retries
  for two minutes.

## Playback arrives under nobody, or under the wrong person

- Check the viewer's name is the same name as in that person's route "Username whitelist"
  (CrossWatch ignores case and punctuation).
- Check the viewer has a playlist or Kodi profile configured (or, with two or more viewers,
  that the question at the end was answered).
- Check whether a playback-driven playlist (such as "Continue Watching") is attributing it to
  the wrong person; see [Smart playlists](who-watched.md#smart-playlists).
- Check the [Remembered answers](remembered-answers.md) screen for a show shown as "Covered by"
  a playlist. Such a show is decided by that playlist and its remembered answer is not used, so
  if the wrong person is credited, fix the playlist.

## The who-watched question never appears

See [The question at the end](who-watched.md#the-question-at-the-end) for the cases where it is
not asked.

## A playlist shows as missing

The playlist was renamed or deleted, or the place it is stored is unavailable. Open that
viewer's "Playlists" ([Viewers](viewers.md)), untick the one tagged "missing", tick its
replacement and press Done.

## A profile shows as missing

The Kodi profile was renamed or deleted. Open that viewer's "Profiles", untick it, tick the
right one and press Done.

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
that route. Until CrossWatch has answered for that name (just after pairing, or right after
adding or renaming a viewer), the line is left out entirely rather than showing "No CrossWatch
route". The line can take a moment to change: it updates when CrossWatch answers the add-on's
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
or remove the viewer and add them again. After making a TV show or film smart playlist, close
the Viewers window and choose "Configure viewers and playlists" again, so it is listed.

## Logs

Normal lines (failures, refusals, check-ins) go to Kodi's own log, `kodi.log` (see the
[Kodi wiki](https://kodi.wiki/view/Log_file) for where it is).

The add-on's own file, `crosswatch.log`, is in the `logs` folder of the add-on's data folder
(`userdata/addon_data/service.crosswatch/logs/`, or under `userdata/profiles/<profile>/`
outside the master profile; see the [Kodi wiki](https://kodi.wiki/view/Userdata) for where
`userdata` is). It is only written while "Enable debug logging (records viewing history to
disk)" is on, and then holds every line including the detail. It is kept to 500 KB, with up to
three older files (`crosswatch.1.log` to `crosswatch.3.log`). Times are in UTC.

The add-on never logs the webhook token or a pairing code. Its normal lines carry counts and
the CrossWatch address, never viewer names or titles; debug lines carry titles and viewer
names. Kodi's own debug logging (not the add-on's) records the one-time code a Link hands over,
which is already used or expired by then.

## Getting help

Open an issue at [crosswatch-app/kodi-addon](https://github.com/crosswatch-app/kodi-addon/issues).
A Kodi forum thread will follow with the first release.
