# Kodi add-on contract

Version 1. Draft.

Nothing here is set in stone. If something makes the add-on harder to build, just say so and we change it. CrossWatch can adapt.

## The idea

The add-on tells CrossWatch what is playing and who is watching. It replaces the JSON-RPC polling for that Kodi.

Events go through the normal Kodi watcher routes. So every route keeps its own user whitelist, profile, filters and target.

The add-on finds the ids and the viewers. CrossWatch does the routing, throttling, filters and the now-playing card.

## Setup

- Every Kodi device is its own Kodi instance in CrossWatch.
- The instance settings show an add-on URL with a token. The user pastes it into the add-on.
- Routes work like today. For example `Kodi Living room -> Trakt Anna` with whitelist `anna`, and `Kodi Living room -> Trakt Tom` with whitelist `tom`.

## Endpoint

```
POST /webhook/kodi?token=<token>
Content-Type: application/json
```

One URL for everything. The event type is in the body.

## Add-on mode (just an idea!)

When the add-on is active, CrossWatch stops polling that Kodi. No double scrobbles.

- Send a `ping` on start, then every 5 minutes.
- If CrossWatch heard nothing for 15 minutes, it goes back to polling.

## Payload

```json
{
  "version": 1,
  "event": "progress",
  "event_id": "7c1e4f0a-3b52-4a8e-9a57-0d6f7f4e2c11",
  "session_id": "kodi-livingroom-1726742400",
  "sent_at": "2026-09-19T20:15:03Z",
  "addon_version": "1.0.0",
  "device": {
    "id": "b8f1c2d4-livingroom",
    "name": "Living room"
  },
  "viewers": ["anna", "tom"],
  "media": {
    "type": "episode",
    "title": "The Expanse",
    "year": 2015,
    "season": 2,
    "episode": 5,
    "ids": {
      "tmdb_show": "63639",
      "tvdb_show": "280619",
      "imdb_show": "tt3230854"
    },
    "percent": 42.7,
    "position_ms": 1180000,
    "duration_ms": 2760000,
    "file": "smb://nas/tv/The Expanse/Season 02/S02E05.mkv",
    "source": "library",
    "cover": "https://image.tmdb.org/t/p/w342/abc.jpg"
  }
}
```

### Fields

| Field | Needed | Notes |
|---|---|---|
| `version` | yes | Contract version. Now `1`. |
| `event` | yes | `ping`, `start`, `resume`, `pause`, `progress` or `stop`. |
| `event_id` | yes | Unique per event. Only for logs. |
| `session_id` | playback | Same value for one playback on one device. |
| `sent_at` | no | ISO-8601 UTC. |
| `addon_version` | no | Shown in CrossWatch. |
| `device.id` | yes | Stable device id. Works with the server UUID filters. |
| `device.name` | no | Shown in CrossWatch. |
| `viewers` | yes | Who is watching. Can be empty. |
| `media.type` | playback | `movie` or `episode`. |
| `media.title` | playback | Movie title, or the show title for episodes. |
| `media.year` | no | Year of the movie or show. |
| `media.season`, `media.episode` | episodes | As Kodi has them. |
| `media.ids` | playback | Flat ids, see below. At least one. |
| `media.percent` | playback | 0 to 100. This is the value that counts. |
| `media.position_ms`, `media.duration_ms` | no | Send them if you have them. |
| `media.file` | no | Path as Kodi reports it. Used by the path and filename filters. |
| `media.source` | no | `library` or `plexkodiconnect`. |
| `media.cover` | no | Artwork URL for the now-playing card. |

Unknown fields are ignored. A `ping` has no `media` and no `session_id`.

### Ids

Same keys as the current Kodi watcher.

- Movies: `imdb`, `tmdb`, `tvdb`.
- Episodes: `imdb_show`, `tmdb_show`, `tvdb_show` for the show. Add `imdb`, `tmdb`, `tvdb` for the episode if Kodi has them.

Only send ids Kodi really knows. Skip `unknown`. Show ids plus season and episode is fine. That is the normal case. IMDb ids keep the `tt`.

## Events

| Event | What it means |
|---|---|
| `ping` | Heartbeat and connection test. |
| `start` | Playback started. |
| `resume` | Playback resumed. Same as start. |
| `progress` | Position update. Same as start. |
| `pause` | Paused. |
| `stop` | Stopped. Always send the final `percent`. |

## Viewers

Each Kodi route checks `viewers` against its own whitelist. If it matches, that route gets the event once, as that viewer.

- `["anna", "tom"]` goes to Anna's route and Tom's route. Once each.
- Routes without a whitelist get everything.
- Empty `viewers` only goes to routes without a whitelist.

The `ping` sends all viewer names set up in the add-on. CrossWatch keeps them and shows them in the user picker.

## Cadence

- Send every state change.
- Send `progress` every 60 seconds and after a seek.
- CrossWatch does the throttling. You don't have to.

## Delivery

- No queue needed.
- CrossWatch down? Retry for 2 minutes, then drop it.
- Don't replay old events after a restart. A late `stop` gets the wrong watch time. Sync picks up anything missed.
- Timeout of 10 seconds.
- Only retry on connection errors, timeouts and `5xx`.

## Responses

CrossWatch always answers `200` with JSON.

```json
{ "ok": true, "ignored": false, "crosswatch_version": "0.13.0" }
```

If `ignored` is `true`, `error` tells why. For example `invalid_token`, `instance_disabled` or `no_routes`. Show it in the add-on. Don't retry.

A `ping` also tells which routes each viewer hits. Handy for a Test connection button.

```json
{
  "ok": true,
  "crosswatch_version": "0.13.0",
  "instance": "Living room",
  "routes": [
    { "id": "R1", "sink": "trakt", "label": "Trakt Anna", "viewers": ["anna"] },
    { "id": "R2", "sink": "trakt", "label": "Trakt Tom", "viewers": ["tom"] }
  ]
}
```

A bad token gets a `401`.

## Versions

CrossWatch takes what it understands and ignores the rest. 
Also from a newer `version`. Use `crosswatch_version` to warn if CrossWatch is too old.

## PlexKodiConnect

PKC playback is just a normal event with the ids the add-on found.

Add a setting to skip PKC playback, on by default. 
People who also run the Plex watcher with PKC support would get double scrobbles otherwise.
If thats not possible, then its too bad.. Users are also responsible to setup/configure things themself.

For `plugin://` paths, send them as they are. Path filters just won't match them.
