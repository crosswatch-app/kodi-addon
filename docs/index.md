# CrossWatch for Kodi

A Kodi service add-on that reports what you watch to your own
[CrossWatch](https://github.com/cenodude/CrossWatch), including **who** in the household
watched it. CrossWatch then syncs each person's watch history to their own Trakt, Simkl,
Plex, Emby or Jellyfin account, so a household sharing one Kodi profile does not end up
sharing one watch history. See the [CrossWatch wiki](https://wiki.crosswatch.app) for how
CrossWatch itself is set up.

## How it decides who watched

Kodi cannot tell who pressed play, so the add-on works it out, first match wins:

1. a smart playlist of a viewer contains the show or film;
2. the active Kodi profile belongs to a viewer;
3. otherwise Kodi asks when playback stops, and remembers the answer for a show.

With only one viewer, everything is credited to that viewer and nothing is asked. See
[Who watched](who-watched.md).

!!! note "Tested on"

    Kodi 21.3 (Omega) and the Kodi 22 (Piers) release candidate, with the Estuary and
    Arctic Zephyr Mod skins. Kodi on Android and other skins have not been tested yet.

## Requirements

- Kodi 21 (Omega) or newer. Earlier versions are not supported. Tested on Kodi 21.3 and the
  Kodi 22 (Piers) release candidate.
- CrossWatch v0.13.3 or later, the first release with Kodi add-on support (marked
  experimental there). Rating needs v0.13.4 or later, and picking viewers from CrossWatch's
  user profiles needs v0.13.5 or later.
- Kodi able to reach CrossWatch over the network, for example both on the same home
  network. CrossWatch does not need to reach Kodi, except for Link.
- For an `https://` address: a certificate from a public certificate authority, such as
  Let's Encrypt through a reverse proxy. Self-signed certificates are not supported. Type the
  address with `https://`; without a scheme, `http://` is assumed.

Kodi's web server and JSON-RPC interface are not needed; only the optional
[Link](connect.md#link-shortcut) shortcut uses them.

## Where to go next

<div class="grid cards" markdown>

-   :lucide-download:{ .lg .middle } __Install__

    ---

    From the Kodi repository, the Rouzax Repository or a zip.

    [:octicons-arrow-right-24: Install](install.md)

-   :lucide-play:{ .lg .middle } __Quick start__

    ---

    The steps from an installed add-on to playback reported per person.

    [:octicons-arrow-right-24: Quick start](quick-start.md)

-   :lucide-link:{ .lg .middle } __Connect to CrossWatch__

    ---

    Pair the add-on with your CrossWatch.

    [:octicons-arrow-right-24: Connect to CrossWatch](connect.md)

-   :lucide-users:{ .lg .middle } __Who watched__

    ---

    How the add-on decides who watched something.

    [:octicons-arrow-right-24: Who watched](who-watched.md)

-   :lucide-user-round-cog:{ .lg .middle } __Viewers__

    ---

    One viewer per person, recognised by playlist or profile.

    [:octicons-arrow-right-24: Viewers](viewers.md)

-   :lucide-history:{ .lg .middle } __Remembered answers__

    ---

    See, change or forget the answers remembered per show.

    [:octicons-arrow-right-24: Remembered answers](remembered-answers.md)

-   :lucide-star:{ .lg .middle } __Rating__

    ---

    Ask how it was after a watch, and send the score.

    [:octicons-arrow-right-24: Rating](rating.md)

-   :lucide-settings:{ .lg .middle } __Settings__

    ---

    Every setting, with its default.

    [:octicons-arrow-right-24: Settings](settings.md)

-   :lucide-life-buoy:{ .lg .middle } __Troubleshooting__

    ---

    Problems listed by what you see.

    [:octicons-arrow-right-24: Troubleshooting](troubleshooting.md)

-   :lucide-send:{ .lg .middle } __What the add-on sends__

    ---

    What goes to CrossWatch, and what happens during an outage.

    [:octicons-arrow-right-24: What the add-on sends](what-is-sent.md)

</div>
