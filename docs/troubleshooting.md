# Troubleshooting

Problems listed by what you see. Several checks point at Kodi's log; [Logs](#logs) says where it
is and what the add-on writes there.

## Nothing arrives in CrossWatch

1. **Status line.** "Not paired" means the add-on has no connection: [pair](connect.md#pairing).
   "Paired with ..." only means CrossWatch answered at some point, so go on.
2. **Kodi's log.** `reporter.failed` means CrossWatch could not be reached: check that it runs
   and that Kodi can reach the paired address. With `CERTIFICATE_VERIFY_FAILED` in it, the
   `https://` certificate is not trusted; it must come from a public authority such as Let's
   Encrypt. With `reason=not_crosswatch` in it, something else answered at that address, such
   as a reverse proxy's login page: let Kodi's requests to `/webhook/kodiwatcher` past the
   login, or pair with an address that reaches CrossWatch directly. Finished watches stay saved
   until then, for up to seven days. `reporter.rejected` with `status=401` means CrossWatch
   refused the token: pair again.
3. **The Kodi add-on in CrossWatch.** It must be switched on for this Kodi instance. Switching it
   off throws away this Kodi's token without any sign in Kodi; turn it back on and pair again.
4. **What you played.** Only episodes and films with a real id (TMDb, TVDb, IMDb and the like)
   are reported, and PlexKodiConnect playback is skipped by default. See
   [What the add-on sends](what-is-sent.md).
5. **An outage.** While CrossWatch was down, only finished watches were kept for later; the rest
   was dropped after two minutes. See
   [When CrossWatch cannot be reached](what-is-sent.md#when-crosswatch-cannot-be-reached).

Pairing again after a token change drops finished watches still waiting for delivery, because
they belong to the old token.

## Playback arrives under nobody, or under the wrong person

- The viewer's name must match the name in that person's route "Username whitelist", or, for a
  route without a whitelist, the name of the user profile the route is assigned to (case and
  punctuation do not matter). A viewer showing "No CrossWatch route" is the giveaway; see
  [below](#a-viewer-shows-no-crosswatch-route).
- With two or more viewers, someone has to be named: by a playlist, a profile, or an answer to
  the question at the end. A skipped question sends the watch with no viewer.
- A playlist that changes with playback, such as "Continue Watching", credits whoever owns it;
  see [Smart playlists](who-watched.md#smart-playlists).
- A playlist wins over a remembered answer. In [Remembered answers](remembered-answers.md), a
  show marked "Covered by" is decided by that playlist, so fix the playlist rather than the
  answer.

## The who-watched question never appears

It is skipped on purpose in several cases, such as a single viewer, playback stopped in the
first three minutes, or autoplay moving on to the next episode. See the list under
[The question at the end](who-watched.md#the-question-at-the-end).

It is also not asked for a show with a remembered answer; check
[Remembered answers](remembered-answers.md).

## A viewer shows "No CrossWatch route"

No route in CrossWatch accepts this viewer's name, so their playback goes nowhere. Check:

1. that the person has a Kodi watcher route in CrossWatch at all.
2. that the route's "Username whitelist" holds the viewer's name. Case and punctuation do not
   matter; anything else does. A route without a whitelist takes the name of the user profile
   it is assigned to instead, and nobody else.
3. if you just renamed the viewer, that the whitelist has the new name.

The line updates at the add-on's next check-in, within five minutes (at once after adding or
renaming a viewer).

## A person is missing from Add viewer

"Add viewer" lists only CrossWatch user profiles that are assigned to a route on this Kodi
without a username whitelist, and leaves out people who are already viewers. Check:

1. that the person's Kodi watcher route in CrossWatch is assigned to their user profile, on the
   Kodi instance this add-on is paired with, and that its "Username whitelist" is empty. A
   route with a whitelist takes the names in it instead, so type the viewer's name to match
   the whitelist.
2. that no viewer is linked to them already; a viewer that shows "Name from CrossWatch" is.
3. that the add-on has checked in since the route changed: within five minutes. Close the
   Viewers window and open it again.

CrossWatch releases before v0.13.5 list no people, so "Add viewer" always opens the keyboard
there.
To add someone without a profile, select "Type a name".

## A typed name is refused as the same as another

"CrossWatch treats this name as the same as `<name>`" means CrossWatch ignores case, spaces and
punctuation when it compares names, so it could not tell the two viewers apart and would send
both their playback to the same routes. Choose a name that differs in a letter or digit (an
accent counts).

Only two people picked from CrossWatch's list may differ just in spaces or punctuation, because
CrossWatch keeps two profiles like that apart. If both people have profiles, pick them instead
of typing.

## A playlist or profile shows as missing

The playlist or Kodi profile was renamed or deleted. Open that viewer's "Playlists" or
"Profiles", untick the entry tagged "missing", tick the right one and press Done. While a
playlist is missing, none of that viewer's playlists count.

## A playlist shows as unusable

The playlist is not a playlist of TV shows or films, so it never credits anyone. It does not
appear in the picker, so there is nothing to untick: open that viewer's "Playlists" and press
Done to remove it. Or change the playlist's type to TV shows or Movies in Kodi.

If Kodi has no TV show or film smart playlists at all, the picker does not open. Make one in
Kodi first, then close the Viewers window and open it again.

## No rating question appears

"Ask for a rating" is Off by default. If it is on, see [When it asks](rating.md#when-it-asks)
for the cases where it is skipped.

## Ratings do not arrive

Look in Kodi's log for:

| Log line | Meaning |
|---|---|
| `reporter.rejected` with `reason=unsupported_event` | Your CrossWatch is too old for ratings. Update it. |
| `reporter.rejected` with `reason=no_rating_target` | None of the matching routes takes this kind of rating, for example an episode rating for Simkl. See [Where ratings go](rating.md#where-ratings-go). |
| `reporter.rejected` with `reason=no_matching_route` | No route accepts the viewer; see [No CrossWatch route](#a-viewer-shows-no-crosswatch-route). |
| `reporter.rated` with `failed=` above 0 | A service refused the rating. CrossWatch's log has the reason. |

## The add-on stopped reporting after an update

If Kodi updated or stopped the add-on while a "Who watched it?" or rating question was on
screen, Kodi stops it forcefully, and it does not start again until Kodi restarts. Restart
Kodi. If Kodi was shutting down at that moment, it can hang while closing; close it fully
and start it again. This comes from how Kodi stops add-ons
([#60](https://github.com/crosswatch-app/kodi-addon/issues/60)).

## Logs

Failures and refusals, such as `reporter.failed` and `reporter.rejected`, always go to Kodi's
log, `kodi.log`. The add-on's routine lines, such as check-ins and each event it sends, appear
there only with Kodi's own debug logging on (Settings > System > Logging > "Enable debug
logging"), as Kodi's add-on rules ask. The [Kodi wiki](https://kodi.wiki/view/Log_file) says
where to find the log.

For more detail, switch on "Enable debug logging" in the [Advanced settings](settings.md#advanced).
The add-on then also writes `crosswatch.log` in the `logs` folder of its data folder,
`userdata/addon_data/service.crosswatch/logs/` (in another Kodi profile, under
`userdata/profiles/<profile>/`; the [Kodi wiki](https://kodi.wiki/view/Userdata) says where
`userdata` is). It is capped at 500 KB plus three older files, with times in UTC. Switch it off
again when you are done, because it records titles and viewer names.

What the logs never contain: the webhook token and pairing codes. The normal lines in `kodi.log`
carry no titles or viewer names either. One exception outside the add-on's control: with
Kodi's own debug logging on, `kodi.log` records the one-time code that a [Link](connect.md#link-shortcut)
hands over. It is used up by then.

## Getting help

Open an issue at [crosswatch-app/kodi-addon](https://github.com/crosswatch-app/kodi-addon/issues).
Attach `kodi.log`, and `crosswatch.log` if you have one, but check them for anything you would
rather not share first.

Questions are welcome in the [Kodi forum thread](https://forum.kodi.tv/showthread.php?tid=389119);
bug reports go to GitHub.
