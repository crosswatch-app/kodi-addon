# Remembered answers

The window that lists the shows whose viewer the add-on has remembered, and lets you change or
forget them.

The add-on settings have a Remembered answers entry under Viewers, next to Configure viewers
and playlists. It opens one CrossWatch window titled "Remembered answers", listing every show
with a remembered answer. With nothing remembered yet, the entry shows the short notice "No
remembered answers yet." instead of opening the window. The top line has "CROSSWATCH" on the
left, the title in the middle and a count on the right that shows how many ("12 shows", or "3
of 12" when a search or viewer narrows the list).

![Remembered answers](screenshots/remembered/remembered-split-covered.png)

On the left are the Search box, the Viewer button below it, and then the shows, each with a
small poster and its title. A warning icon on a show means its answer needs a look: the show is
not in your library any more, or the answer is not stable (see "Not stable" below). To search,
press OK on the box, type with Kodi's keyboard and confirm. It matches show titles and viewer
names. The Viewer button reads "Viewer: All" and cycles with OK through All, each configured
viewer, and "will ask again" (shows nobody is remembered for). A show with no known title is
listed by its id, for example "Kodi library id 12" or "tvdb 81189". Search and viewer combine.
When nothing is listed the window says "Nothing matches".

The right side shows the highlighted show, so moving up and down the list changes it. It has
the show's title, its poster (or "No poster"), the year (when known), and then:

- "Answer": the viewers the answer is for, or "will ask again" when none of its viewers is
  configured any more.
- "Covered by": shown when a playlist decides this show, with one line per playlist, such as
  "Anna's playlist 'Cartoons'". The answer is kept, and used again if the show leaves the
  playlist.
- "Not in library": shown with "gone from Kodi's library" when the show is no longer in your
  Kodi library.
- "Not stable": shown when the answer was stored against Kodi's database id and that id can no
  longer be trusted. It says either "now points at `<other show>`" (the id now holds a different
  show) or "no title stored to check it" (the id cannot be checked).

When there are more lines than fit, the last one says "and N more".

Change and Forget are together in the bottom row, on the right, just left of Close:

- Change opens the who-watched window with the current answer ticked and no countdown. Done
  saves the new answer. Done with nobody ticked forgets the answer, so the show is asked about
  again after its next episode. Skip or Back leaves the answer unchanged. Change is not shown
  for a "Not stable" show, which can only be forgotten.
- Forget asks "Forget who watched this show? It will be asked again." first, with No
  preselected. Yes forgets the answer, so the show is asked about again after its next
  episode.

OK on a show moves to Change (or to Forget for a "Not stable" show).

At the bottom left of that row, "Forget all" becomes "Forget N shown" while a search or viewer
narrows the list, and is hidden when nothing is listed. It forgets exactly the shows listed,
after a Yes/No confirmation (No is preselected). Close, in the bottom-right corner, leaves the
screen; so does Back.

After each change the window comes back with the same search and viewer, on the same show;
after a forget, on the show that took its place. When nothing is left the screen closes with a
short notice ("No remembered answers yet."). Changes apply from the next playback, no restart
needed.

Playlist membership and then the active Kodi profile are both checked before a remembered
answer, so either one overrides it. Only playlists are shown as "Covered by": the answer is
kept, and applies again if the show leaves the playlist. A show decided by a viewer's Kodi
profile shows no mark. That makes the choice of playlist matter:
it should describe what that person watches, not what they just happened to play.
