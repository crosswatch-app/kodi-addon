# CrossWatch for Kodi

A Kodi service add-on that reports what you watch to your own
[CrossWatch](https://github.com/cenodude/CrossWatch), including **who** in the household
watched it. CrossWatch then syncs each person's watch history to their own Trakt, Simkl,
Plex, Emby or Jellyfin account, so a household sharing one Kodi profile does not end up
sharing one watch history. See the [CrossWatch wiki](https://wiki.crosswatch.app) for how
CrossWatch itself is set up.

## How it decides who watched

Kodi cannot say who pressed play inside one profile, so the add-on works it out. It checks
three things in order, and the first match wins:

1. Smart playlists: every viewer whose playlist contains the show or film.
2. The active Kodi profile.
3. A question at the end of playback; for a show, the answer is remembered.

With only one viewer configured, everything is credited to that viewer and nothing is asked.

See [Who watched](who-watched.md) for the details.

!!! warning "Beta"

    This is a beta (1.0.0~beta1), installable from a GitHub release; see
    [Install](install.md). It needs CrossWatch v0.13.3 or later, where Kodi add-on support is
    marked experimental.

    It has been tested on Kodi 21.3 (Omega) against CrossWatch v0.13.3 and the development
    builds before it, including pairing, Link, routing playback by viewer, and replay after an
    outage. The add-on's windows were checked in Estuary and Arctic Zephyr Mod; Kodi on
    Android and other skins have not been tested.

## Requirements

- Kodi 21 (Omega). Earlier versions are not supported; later versions have not been tested.
- CrossWatch v0.13.3 or later, the first release with Kodi add-on support.
- Kodi able to reach CrossWatch over the network, for example both on the same home
  network. CrossWatch does not need to reach Kodi, except for Link.
- For an `https://` address: a certificate from a public certificate authority, such as
  Let's Encrypt through a reverse proxy. Self-signed certificates are not supported. Type the
  address with `https://`; without a scheme, `http://` is assumed.

Kodi's web server and JSON-RPC interface are not needed. The only thing that uses them is
the optional [Link](connect.md#link-shortcut) shortcut.

## Where to go next

<div class="grid cards" markdown>

-   :lucide-download:{ .lg .middle } __Install__

    ---

    Install the beta from a GitHub release.

    [:octicons-arrow-right-24: Install](install.md)

-   :lucide-play:{ .lg .middle } __Quick start__

    ---

    The steps from an installed add-on to playback reported per person.

    [:octicons-arrow-right-24: Quick start](quick-start.md)

-   :lucide-link:{ .lg .middle } __Connect to CrossWatch__

    ---

    Pair the add-on, check the Status line, unpair.

    [:octicons-arrow-right-24: Connect to CrossWatch](connect.md)

-   :lucide-users:{ .lg .middle } __Who watched__

    ---

    How the add-on decides who watched something.

    [:octicons-arrow-right-24: Who watched](who-watched.md)

-   :lucide-user-round-cog:{ .lg .middle } __Viewers__

    ---

    Add one viewer per person, with playlists or profiles.

    [:octicons-arrow-right-24: Viewers](viewers.md)

-   :lucide-history:{ .lg .middle } __Remembered answers__

    ---

    See, change or forget the answers remembered per show.

    [:octicons-arrow-right-24: Remembered answers](remembered-answers.md)

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
