# CrossWatch test instance

A throwaway CrossWatch to develop this add-on against.

```bash
docker/crosswatch/setup.sh            # start, configure against the local Kodi, verify
docker/crosswatch/setup.sh --verify   # check an existing instance, change nothing
docker compose -f docker/crosswatch/docker-compose.yml down -v   # stop and wipe
```

`setup.sh` is the whole setup, so there is nothing to click through and nothing to remember.
It configures both sides: the Kodi provider in CrossWatch (the JSON-RPC handshake against the
local Kodi) and the add-on side (the `kodi -> crosswatch` watcher route and the add-on toggle
that issues a webhook token). It only writes config and restarts the container when something
is missing, so re-running against an already configured instance changes nothing and does not
restart it. It is idempotent, verified from a wiped volume, and refuses to run against an
instance that has a real provider connected. `--verify` checks both sides and changes nothing.
Kodi must already be running with its web server on; see `docker/scripts/setup.sh --native`.

It listens on `127.0.0.1:8787` only, keeps its config in its own volume, and does not
restart on boot.

## It is deliberately not connected to anything

No Trakt, Simkl, Plex or MDBList. A test scrobble that reaches a real account writes into
real watch history and cannot be undone from here. If a provider ever needs exercising,
make a test account for it rather than reusing a real one.

Check before trusting it:

```bash
curl -s http://127.0.0.1:8787/api/status | python3 -m json.tool | grep _connected
```

Everything should read `false` except `crosswatch_connected`.

## The image is pinned by digest

`ghcr.io/cenodude/crosswatch-dev`, the dev-db build and the first one with the add-on
endpoint (`/webhook/kodiwatcher`), pinned by digest in `docker-compose.yml`. `:latest` would
let the thing we verified against change under us on the next pull. Bump the digest
deliberately, and re-run the checks below when you do.

## What this can check

| Check | How |
|---|---|
| Ping handshake | `GET /api/kodi/addon` (logged in): mode goes from `polling` to `addon`, `active` becomes `true` |
| A playback routed to local history | play and stop something in Kodi, then check CrossWatch's local history |
| Outbox replay after an outage | stop the container, play and stop in Kodi, restart Kodi, then start the container again |
| Local sink timing | the local crosswatch sink records a replayed stop at arrival time, not `sent_at` (receiver-side behaviour, observed 2026-10-06) |
| CrossWatch's own Kodi watcher | wired up by the same `setup.sh` run: it polls Kodi directly over JSON-RPC until the add-on's first ping tells it to stop |

## How to point the add-on at it

`setup.sh` saves the webhook URL, which carries the token in its query string, as
`CW_ADDON_URL` in `docker/crosswatch/.credentials` (mode 600, excluded from git), and never
prints it. It prints the URL without the query string instead, and says where the token is.

In the add-on's settings:

- **Webhook URL**: the printed URL, `http://127.0.0.1:8787/webhook/kodiwatcher`.
- **Webhook token**: the `token=...` value from `CW_ADDON_URL`.

Wiping the volume (`down -v`) issues a new token, so the add-on's token needs updating
after a `down -v`.

Check it worked with `GET /api/kodi/addon` while logged in: it reports mode `addon` and
`active: true` once the add-on's first ping arrives. Before that it says `polling`.

## Things that cost time to find

**The API needs an `Origin` header.** A cookie-authenticated call without one is refused
with `Origin mismatch`, which says nothing about the actual request.

**Use `/api/kodi/connect`, not a write to `kodi.server` through `/api/config`.** Only the
former performs the JSON-RPC handshake and records `connection_verified`. Writing the
address directly stores it and leaves the provider unverified, so the watcher never starts
and nothing tells you why.

**Watcher routes and `runtime.kodi_addon` are read at container start.** Editing either
needs a restart, which is why `setup.sh` restarts the container whenever it changes one of
them. A route with the wrong shape is silently ignored rather than rejected: the add-on then
gets `ignored: no_routes` or `ignored: no_matching_route` instead of an error.
