# Who watched

How the add-on decides who in the household watched something, in the order it checks.

The add-on works out who was watching, first match wins:

## Smart playlists

A show (for an episode) or a film is credited to every viewer whose playlist contains it.
Playlists are video smart playlists from Kodi's playlists folder, of shows or of films;
playlists of episodes or music videos are not used. The playlist picker only offers playlists
of shows or films. If a viewer already had a playlist of another type, it is not listed in
the picker, and it stays in their list until you press Done, which removes it from that
viewer (Cancel or Back leaves it). Until it is removed, the Viewers window still shows that
playlist tagged "unusable". It never credits anyone and is simply ignored: the viewer's other
playlists still count. Playlists of the wrong type are never offered to anyone.

- Works well: a fixed list of shows, or rules on genre, tag or similar show attributes.
- Avoid playlists whose membership follows from playback itself, such as "Continue Watching"
  or "In Progress" smart playlists (rules on in-progress, play count or last played). Playing
  a show adds it to such a playlist, so the next episode is attributed to whoever owns it,
  even if someone else watched it.

If a viewer's playlist cannot be read (it was deleted or renamed), the viewer list shows it
as missing, Kodi shows a notification, and all of that viewer's playlists stop counting until
it is fixed: that viewer falls through to profile matching and the end-of-playback question.

## Kodi profile

If the active Kodi profile is one of a viewer's profiles (the name match ignores case), that
viewer is credited. Profiles are picked from Kodi's own list under the viewer's "Profiles"
button (see [Viewers](viewers.md)).

## The question at the end

When neither of the above answered, Kodi asks who watched when playback stops, never at the
start, so it never delays playback.

The question is a CrossWatch window, not the skin's standard dialog. It shows the title of the
show or film with its poster (if there is none, a placeholder in the CrossWatch colours reading
"No poster"), plus "Season N, episode M" for an episode or the year for a film. Below that is
an "Everyone" row, then each configured viewer; focus starts on Everyone.

- OK on a viewer ticks or unticks that viewer. You can tick more than one.
- OK on Everyone ticks every viewer, or clears them all when all are already ticked.
- Done, below the list, sends the watch with the ticked viewers.
- Skip or Back sends the watch with no viewer. Nothing is remembered, so the question comes
  back next time. Done with nobody ticked does the same.

A countdown in the corner ("Closes in N s") runs from 120 seconds; at zero the window closes
as if you had skipped. It also closes straight away, as if skipped, when playback starts
again while it is open.

It is not asked:

- with fewer than two viewers configured.
- for a film, when "Ask who watched a film" is off.
- when playback stopped within Kodi's "ignore at start" time (180 seconds unless changed in
  Kodi's advanced settings).
- while another dialog is already open.
- when the next item starts playing right away (binge or autoplay); the one that just ended
  is then sent with no viewer.

For a show, the answer is remembered and reused for later episodes, including from the start
of playback. For a film, nothing is remembered. [Remembered answers](remembered-answers.md)
shows, changes and forgets them.

## Nobody resolved

If no viewer is resolved by any of the above, the event is sent with no viewers. CrossWatch
treats that as an unknown viewer. The exception is a household with exactly one viewer
configured: that viewer is credited instead (see [One person in the household](#one-person-in-the-household)).

## One person in the household

If you are the only person who watches, add yourself as the one viewer under "Configure viewers
and playlists". The viewer's name must match the username in your CrossWatch route's whitelist.
You do not need any playlists or Kodi profiles: with exactly one viewer configured, every
playback (start, pause, progress and stop) is credited to you. The end-of-playback question is
never asked when there is only one viewer. With two or more viewers nothing changes.

The event carries your name, so a route with your name in its username whitelist receives it,
and so does a route with an empty whitelist.

Configuring no viewers at all also still works, with a CrossWatch route that has an empty
username whitelist. The events then carry no viewer.
