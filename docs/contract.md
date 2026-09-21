# Kodi add-on contract

Version 1.1. Draft.

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
POST /webhook/kodiwatcher
X-CrossWatch-Token: <token>
Content-Type: application/json
```

Use the header. `?token=<token>` still works as a fallback, but it ends up in reverse proxy logs.

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
  "viewers_source": "playlist",
  "media": {
    "type": "episode",
    "title": "The Expanse",
    "episode_title": "Home",
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
    "completed": false,
    "file": "smb://nas/tv/The Expanse/Season 02/S02E05.mkv",
    "source": "library"
  }
}
```

### Fields

| Field | Needed | Notes |
|---|---|---|
| `version` | yes | Contract version. Now `1`. |
| `event` | yes | `ping`, `start`, `resume`, `pause`, `progress` or `stop`. |
| `event_id` | yes | Unique per event. For logs, and for dedupe if CrossWatch ever needs it. |
| `session_id` | playback | Same value for one playback on one device. |
| `sent_at` | no | ISO-8601 UTC. |
| `addon_version` | no | Shown in CrossWatch. |
| `device.id` | yes | Stable device id. Works with the server UUID filters. |
| `device.name` | no | Shown in CrossWatch. |
| `viewers` | yes | Who is watching. Can be empty. |
| `viewers_source` | no | `playlist`, `profile` or `prompt`. How identity was decided. Diagnostic only. |
| `media.type` | playback | `movie` or `episode`. |
| `media.title` | playback | Movie title, or the show title for episodes. |
| `media.episode_title` | no | The episode's own title. |
| `media.year` | no | Year of the movie or show. |
| `media.season`, `media.episode` | episodes | As Kodi has them. |
| `media.ids` | playback | Flat ids, see below. At least one. |
| `media.percent` | when known | 0 to 100. Leave it out when Kodi never knew the duration. Absent means unknown, and CrossWatch does not read it as zero. |
| `media.position_ms`, `media.duration_ms` | no | Send them if you have them. |
| `media.completed` | no | Played to the end. The only completion signal that survives an unknown duration, so CrossWatch uses it on `stop` when `percent` is absent. |
| `media.file` | no | The path, with credentials stripped. See below. |
| `media.source` | no | `library` or `plexkodiconnect`. |
| `media.plex_rating_key` | no | PKC rating key, when `source` is `plexkodiconnect`. Not used yet. |
| `pkc_skipped` | no | On the `ping` only. How many PKC playbacks were declined. Left out when zero. |

Unknown fields are ignored. A `ping` has no `media` and no `session_id`.

There is no `cover` field. The playing card builds the poster from the tmdb id, and Kodi wraps its own art as `image://...` which CrossWatch would reject anyway.

### Paths

Kodi carries `user:password@` inline in VFS paths, so the add-on strips the userinfo and redacts query strings before sending. `plugin://` paths go out untouched.

So path and filename filters have to be written against `smb://nas/tv/...`, not `smb://user:pass@nas/tv/...`.

### Ids

Same keys as the current Kodi watcher.

- Movies: `imdb`, `tmdb`, `tvdb`.
- Episodes: `imdb_show`, `tmdb_show`, `tvdb_show` for the show. Add `imdb`, `tmdb`, `tvdb` for the episode if Kodi has them.

Only send ids Kodi really knows. Show ids plus season and episode is fine. That is the normal case. IMDb ids keep the `tt`.

Drop the `unknown` key, and drop placeholder values: `none`, `null`, `nan`, `unknown`, `0` and `-1`, case insensitive. Scrapers write those as text, and a `"None"` looks like a real id to anything that only checks whether a value is there.

No usable ids at all means don't send the event. There is nothing to route.

## Events

| Event | What it means |
|---|---|
| `ping` | Heartbeat and connection test. |
| `start` | Playback started. |
| `resume` | Playback resumed. Same as start. |
| `progress` | Position update. Same as start. |
| `pause` | Paused. |
| `stop` | Stopped. Send the final `percent`, or `completed` when the duration was never known. |

Live TV and recordings are out of scope. `Player.GetProperties` has a `live` property, and a channel reports `type: "channel"`. Send nothing for those. Recordings carry EPG metadata instead of library ids, which is a matching problem for CrossWatch to solve later.

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
- Retry on connection errors, timeouts, `5xx` and a lost response. A duplicate is safe: Trakt and Simkl dedupe server side, and the media server sinks just set a watched flag. A lost `stop` is worse.
- Don't retry a `200` with `ignored: true`, and don't retry a `401`.

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

CrossWatch needs 0.13.0 or newer. Older builds turn an absent `percent` into `0`, which wipes real resume points in Plex and Emby.

## PlexKodiConnect

PKC playback is just a normal event with the ids the add-on found. `media.plex_rating_key` comes along when there is one, but CrossWatch does not use it yet.

The add-on skips PKC playback by default, so people who also run the Plex watcher with PKC support do not get double scrobbles. Untested so far, since PKC is not installed on the dev machine.

For `plugin://` paths, send them as they are. Path filters just won't match them.
