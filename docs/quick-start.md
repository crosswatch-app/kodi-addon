# Quick start

From an installed add-on to playback that CrossWatch credits to the right person. It takes a few
minutes, once per Kodi device.

**Before you start:** a running CrossWatch with the services each person wants to sync to
(Trakt, Plex and so on) already connected, and the add-on [installed](install.md).

1. **Pair.** In CrossWatch, open the Kodi entry and add this device with the add-on option (not
   the JSON-RPC option) to get a pairing code. In Kodi, open the add-on settings and select
   "Pair with CrossWatch", then type CrossWatch's address and the code.
   [Connect to CrossWatch](connect.md) has the details and the error messages.

2. **Add a viewer per person in Kodi.** In the add-on settings, Viewers category, select
   "Configure viewers and playlists" and add each person by name. Give each viewer one or more
   smart playlists of the shows and films they watch, or their Kodi profile. See
   [Viewers](viewers.md).

    Living alone? Add one viewer with your name and nothing else; everything is credited to you.

3. **Add a route per person in CrossWatch.** Each person needs a Kodi watcher route with their
   viewer name in its "Username whitelist"; this is how CrossWatch knows where to send their
   playback. The names must match, though case and punctuation do not matter. The route's
   "Find users" button lists the viewer names the add-on has reported.

4. **Check.** Within a few minutes, each viewer in the Viewers window shows "CrossWatch route".
   "No CrossWatch route" means the names do not match; see
   [Troubleshooting](troubleshooting.md#a-viewer-shows-no-crosswatch-route).

Now play something. Anything the playlists and profiles do not settle is asked when playback
stops (see [Who watched](who-watched.md)). To get a rating question as well, see
[Rating](rating.md).
