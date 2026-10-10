# Quick start

The steps that take you from an installed add-on to playback reported per person.

1. In CrossWatch, open the Kodi entry and choose to add this device with the add-on option
   (CrossWatch also offers a JSON-RPC option; that is for [Link](connect.md#link-shortcut)).
   CrossWatch gives you a pairing code. CrossWatch's wiki shows its screens: [Kodi
   add-on](https://wiki.crosswatch.app/crosswatch/settings/connections/media-clients/kodi/kodi-add-on).
2. In Kodi, go to Add-ons > My add-ons > Services > CrossWatch > Configure, open the
   CrossWatch category, and select "Pair with CrossWatch" ([Connect to CrossWatch](connect.md)
   has the details). Enter the address first, then the code.
3. In CrossWatch, set up a Kodi watcher route for each person in the household. In each
   route's "Username whitelist" (the names that route accepts), enter that person's name.
   Once the add-on is paired and has viewers, the route's "Find users" button also lists
   the viewer names it reported.
4. In the add-on settings, Viewers category, select "Configure viewers and playlists" and add
   one viewer per person ([Viewers](viewers.md)). Each viewer's name must be the same as the
   username in that person's route (CrossWatch ignores upper and lower case and punctuation
   when comparing). Give each viewer one or more smart playlists, or one or more Kodi profiles.
   A one-person household only needs the one viewer, with no playlists or profiles.
5. Check the Viewers window. Once CrossWatch has answered the add-on's next check-in, each
   viewer shows "CrossWatch route", or "No CrossWatch route" when no route accepts that name
   (see [Troubleshooting](troubleshooting.md#a-viewer-shows-no-crosswatch-route)).

Names have to match because CrossWatch sends a playback to the route whose username whitelist
contains the viewer's name. A viewer named differently from every whitelist is still sent,
but only a route with an empty whitelist (and no CrossWatch profile set on it) accepts it.
