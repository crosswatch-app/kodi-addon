# CrossWatch for Kodi

A Kodi service add-on that reports what you watch to your own
[CrossWatch](https://github.com/cenodude/CrossWatch), including **who** in the household
watched it. CrossWatch then syncs each person's watch history to their own Trakt, Simkl,
Plex, Emby or Jellyfin account, so a household sharing one Kodi profile does not end up
sharing one watch history. See the [CrossWatch wiki](https://wiki.crosswatch.app) for how
CrossWatch itself is set up.

This is a beta (1.0.0~beta1), installable from a GitHub release. It needs CrossWatch v0.13.3
or later, where Kodi add-on support is marked experimental. Tested on Kodi 21.3 (Omega); the
add-on's windows were checked in Estuary and Arctic Zephyr Mod. Kodi on Android and other skins
have not been tested.

![The Viewers window](docs/screenshots/viewers/viewers-split.png)

## Requirements

- Kodi 21 (Omega). Earlier versions are not supported; later versions have not been tested.
- CrossWatch v0.13.3 or later, the first release with Kodi add-on support.
- Kodi able to reach CrossWatch over the network, for example both on the same home network.
  CrossWatch does not need to reach Kodi, except for Link.
- For an `https://` address: a certificate from a public certificate authority. Self-signed
  certificates are not supported.

## Install

This add-on has not reached the official Kodi add-on repository yet.

1. Download `service.crosswatch-v1.0.0-beta1.zip` from the
   [releases page](https://github.com/crosswatch-app/kodi-addon/releases).
2. In Kodi, turn on Settings > System > Add-ons > "Unknown sources".
3. Go to Add-ons, open the add-on browser (the open box icon at the top left), choose
   "Install from zip file" and pick the zip.
4. Follow the [Quick start](https://crosswatch-app.github.io/kodi-addon/quick-start/).

A zip install does not update itself: to update, install the next zip over it.

## Documentation

The full documentation is at <https://crosswatch-app.github.io/kodi-addon/>.

- [Install](https://crosswatch-app.github.io/kodi-addon/install/): installing the beta.
- [Quick start](https://crosswatch-app.github.io/kodi-addon/quick-start/): from install to playback reported per person.
- [Connect to CrossWatch](https://crosswatch-app.github.io/kodi-addon/connect/): pairing, Link, Unpair and manual setup.
- [Who watched](https://crosswatch-app.github.io/kodi-addon/who-watched/): how the add-on decides who watched.
- [Viewers](https://crosswatch-app.github.io/kodi-addon/viewers/): one viewer per person, with playlists or profiles.
- [Remembered answers](https://crosswatch-app.github.io/kodi-addon/remembered-answers/): the answers remembered per show.
- [Settings](https://crosswatch-app.github.io/kodi-addon/settings/): every setting and its default.
- [Troubleshooting](https://crosswatch-app.github.io/kodi-addon/troubleshooting/): problems by what you see.
- [What the add-on sends](https://crosswatch-app.github.io/kodi-addon/what-is-sent/): what goes to CrossWatch, and what happens in an outage.

## Getting help

Open an issue at [crosswatch-app/kodi-addon](https://github.com/crosswatch-app/kodi-addon/issues).
A Kodi forum thread will follow with the first release.

Developers: the payload contract is [docs/contract.md](docs/contract.md).

## Licence

GPL-2.0-only. See [LICENSE](LICENSE).

The status icons are Material Symbols by Google, under the Apache License 2.0
([resources/licences/Apache-2.0.txt](resources/licences/Apache-2.0.txt)).
