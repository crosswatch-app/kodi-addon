# Viewers

The window where you add one viewer per person and give each their playlists or Kodi profiles.

"Configure viewers and playlists" opens one CrossWatch window titled "Viewers". The top line
has "CROSSWATCH" on the left, the title in the middle and a count on the right ("3 viewers").
The viewers are listed by name on the left. A warning icon on a row means something about that
viewer needs fixing: a missing or unusable playlist, a profile Kodi no longer has, or no
CrossWatch route.

![The Viewers window](screenshots/viewers/viewers-split.png)

The right side shows the highlighted viewer, so moving up and down the list changes it. It has
the viewer's name, directly below it the CrossWatch line (described below), then "Playlists"
with each playlist on its own line, then "Profiles" with each profile (a section reads "none"
when it is empty). A playlist that was deleted or renamed, and a profile Kodi no longer has,
are tagged "missing" with a warning icon; a playlist or profile is only tagged when Kodi's list
of them could be read. A playlist that is not a TV show or film playlist is tagged "unusable".
When there are more lines than fit, the last one says "and N more": the "Playlists" and
"Profiles" buttons list them all. That line carries the warning icon when one of the lines it
hides needs fixing.

The CrossWatch line works like this. A check icon and "CrossWatch route" mean a CrossWatch
route accepts the viewer's name; a warning icon and "No CrossWatch route" mean none does.
CrossWatch reports its routes for this Kodi, and the names each route accepts, when it answers
the add-on's regular check-in (every five minutes, and straight away when the viewer names
change). The line only appears once CrossWatch has answered with its routes for the current
pairing, and only for names it was asked about: when the add-on is not paired, or CrossWatch
has never sent its routes, nothing is shown, and a viewer just added or renamed shows nothing
until CrossWatch's next answer.

The bottom row has "Add viewer" on the left, the buttons "Playlists", "Profiles", "Rename" and
"Remove" together in the middle, and "Close" on the right. The four in the middle act on the
highlighted viewer. OK on a viewer moves to "Playlists". Back closes the window. After each
action the window comes back on the same viewer: after "Rename" on the new name, after "Add
viewer" on the new viewer, and after "Remove" on the viewer that took its place (the one before
it, when the last viewer was removed). With no viewers configured yet, the screen opens
straight on the keyboard for a first viewer's name; cancelling it closes the screen. If every
viewer is removed, the window shows "No viewers yet" with only "Add viewer" and "Close".

Every change is saved straight away, so there is no need to close the screen, and it applies
from the next playback.

"Add viewer" asks for the viewer's name ("Viewer name, as CrossWatch routes it"), then which
playlists are theirs (the playlist window, described below), then the Viewers window comes
back with the new viewer highlighted. Cancelling the playlist step still adds the viewer, with
no playlists. A blank name, or one another viewer already has, is refused without a message.

"Profiles" opens a window titled "Profiles for `<name>`" that lists Kodi's profiles. The list
comes from Kodi itself, so nothing is typed. Press OK on a profile to tick or untick it; the
viewer's current profiles start ticked. "Done" keeps the ticked profiles. "Cancel" or Back
leaves them as they were. A profile the viewer has that Kodi no longer has is listed last,
ticked, tagged "missing". A profile that another viewer already has shows "Also `<names>`". If
Kodi's profiles cannot be read, a message says so ("Kodi's profiles could not be read. Try
again in a moment.") and nothing changes. The window has the same search box and count as the
playlist window.

"Rename" opens Kodi's keyboard with the current name filled in. A name that another viewer
already has is refused and nothing changes; changing only the upper and lower case of the
viewer's own name is allowed. The viewer's remembered answers move to the new name. Change the
username whitelist of the CrossWatch route to the new name too, or that route stops receiving
this viewer's playback.

"Remove" first asks in a CrossWatch window titled "Remove `<name>`?": "Remembered answers lose
this name. A show nobody else is remembered for is asked again." No is preselected. Yes
removes the viewer and takes their name out of every remembered answer. An answer that is left
with nobody is forgotten, so that show is asked about again at the end of its next episode.

"Playlists", and the playlist step of Add viewer, open a CrossWatch window titled "Playlists
for `<viewer name>`". It lists Kodi's smart playlists of TV shows or films. Press OK on a
playlist to tick or untick it; the viewer's current playlists start ticked. "Done" keeps the
ticked playlists. "Cancel" or Back leaves the viewer's playlists exactly as they were. Kodi's
playlists are read when the Viewers window opens, so a playlist made while it is open appears
after closing it and choosing "Configure viewers and playlists" again.

A search box at the top narrows the list by playlist name: press OK on it, type with Kodi's
keyboard and confirm. A ticked playlist the search hides stays ticked and is kept when you
press Done. The count on the top line, on the right, shows how many playlists are listed ("7
playlists", or "1 of 7" while searching).

A playlist that another viewer already has shows "Also `<names>`" next to it (for example "Also
Ben, Chloe"). Giving a playlist to two viewers credits both of them for everything in it.

A playlist the viewer has that was deleted or renamed is listed at the end, ticked, tagged
"missing". Untick it and press Done to remove it from that viewer; leave it ticked and it is
kept, so it counts again if the playlist comes back. A playlist the viewer has that is not a
TV show or film playlist is not listed, and Done removes it from the viewer (Cancel or Back
keeps it; see [Smart playlists](who-watched.md#smart-playlists)). Playlists of the wrong
type are never offered to anyone.

When Kodi has no smart playlists of TV shows or films at all, a message says so ("Kodi has
no smart playlists of TV shows or films yet. Make one under Videos, Playlists, then come
back here.") and no list opens. The viewer's playlists stay as they were.
