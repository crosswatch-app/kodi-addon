# CrossWatch test instance

A throwaway CrossWatch to develop this add-on against.

```bash
docker compose -f docker/crosswatch/docker-compose.yml up -d     # start
docker compose -f docker/crosswatch/docker-compose.yml down -v   # stop and wipe config
```

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

`:latest` would let the thing we verified against change under us on the next pull. Bump
the digest in `docker-compose.yml` deliberately, and re-run the checks below when you do.

## What this instance can and cannot do

`/webhook/kodiwatcher` does not exist yet, so the add-on's own transport cannot be tested
end to end here. What can:

| Check | How |
|---|---|
| Which routes exist | `curl -o /dev/null -w '%{http_code}' -X POST -d '{}' http://127.0.0.1:8787/webhook/kodiwatcher` |
| Our handling of a real rejection | point `HttpReporter` at the 404 and confirm one `reporter.rejected`, no retry |
| Our parsing of a real reply | `POST /webhook/plex` returns `{"ok":true,"ignored":true,...}`, which `_accepted` must read as not delivered |
| His Kodi watcher against our Kodi | connect the Kodi provider to the flatpak Kodi, see `docker/scripts/setup.sh --native` |

That last one is the half nobody has exercised: CrossWatch already polls Kodi over JSON-RPC
in `providers/scrobble/kodi/watch.py`, and this add-on's `ping` exists to tell it to stop.
