# CrossWatch for Kodi

A Kodi service add-on that reports what you watch to your own
[CrossWatch](https://github.com/cenodude/CrossWatch), including **who** in the household
watched it. CrossWatch then syncs each person's watch history to their own Trakt, Simkl,
Plex, Emby or Jellyfin account, so a household sharing one Kodi profile does not end up
sharing one watch history. See the [CrossWatch wiki](https://wiki.crosswatch.app) for how
CrossWatch itself is set up.

Tested on Kodi 21.3 (Omega) and the Kodi 22 (Piers) release candidate, with the Estuary and
Arctic Zephyr Mod skins. Kodi on Android and other skins have not been tested yet.

![The Viewers window](docs/screenshots/viewers/viewers-split.png)

## Requirements

- Kodi 21 (Omega) or newer. Earlier versions are not supported. Tested on Kodi 21.3 and the
  Kodi 22 (Piers) release candidate.
- CrossWatch v0.13.3 or later, the first release with Kodi add-on support (marked
  experimental there). Rating needs v0.13.4 or later, and picking viewers from CrossWatch's
  user profiles needs v0.13.5 or later.
- Kodi able to reach CrossWatch over the network, for example both on the same home network.
  CrossWatch does not need to reach Kodi, except for Link.
- For an `https://` address: a certificate from a public certificate authority. Self-signed
  certificates are not supported.

## Install

From the Rouzax Repository, which keeps the add-on up to date:

1. Download the latest `repository.rouzax` zip from its
   [releases page](https://github.com/Rouzax/repository.rouzax/releases).
2. In Kodi, turn on Settings > System > Add-ons > "Unknown sources" (needed once, for the
   repository).
3. In the add-on browser, choose "Install from zip file" and pick the repository zip.
4. Choose "Install from repository" > Rouzax Repository > Services > CrossWatch for Kodi.
5. Follow the [Quick start](https://crosswatch-app.github.io/kodi-addon/quick-start/).

The add-on has also been submitted to the official Kodi add-on repository. See
[Install](https://crosswatch-app.github.io/kodi-addon/install/) for that and for zip installs.

## Documentation

The full documentation is at <https://crosswatch-app.github.io/kodi-addon/>.

- [Install](https://crosswatch-app.github.io/kodi-addon/install/): from the Rouzax Repository, the Kodi repository or a zip.
- [Quick start](https://crosswatch-app.github.io/kodi-addon/quick-start/): from install to playback reported per person.
- [Connect to CrossWatch](https://crosswatch-app.github.io/kodi-addon/connect/): pairing, Link, Unpair and manual setup.
- [Who watched](https://crosswatch-app.github.io/kodi-addon/who-watched/): how the add-on decides who watched.
- [Viewers](https://crosswatch-app.github.io/kodi-addon/viewers/): one viewer per person, with playlists or profiles.
- [Remembered answers](https://crosswatch-app.github.io/kodi-addon/remembered-answers/): the answers remembered per show.
- [Rating](https://crosswatch-app.github.io/kodi-addon/rating/): asking how it was after a watch.
- [Settings](https://crosswatch-app.github.io/kodi-addon/settings/): every setting and its default.
- [Troubleshooting](https://crosswatch-app.github.io/kodi-addon/troubleshooting/): problems by what you see.
- [What the add-on sends](https://crosswatch-app.github.io/kodi-addon/what-is-sent/): what goes to CrossWatch, and what happens in an outage.

## Getting help

Open an issue at [crosswatch-app/kodi-addon](https://github.com/crosswatch-app/kodi-addon/issues).
Questions are welcome in the [Kodi forum thread](https://forum.kodi.tv/showthread.php?tid=389119).

Developers: the payload contract is [docs/contract.md](docs/contract.md).

## Licence

GPL-2.0-only. See [LICENSE.txt](LICENSE.txt).

The status icons are Material Symbols by Google, under the Apache License 2.0
([resources/licences/apache-2.0.txt](resources/licences/apache-2.0.txt)).
