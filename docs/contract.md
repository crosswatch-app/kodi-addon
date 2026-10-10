# Kodi add-on contract

Version 1.5. Draft.

## The idea

The add-on tells CrossWatch what is playing and who is watching. It replaces the JSON-RPC polling for that Kodi.

Events go through the normal Kodi watcher routes. So every route keeps its own user whitelist, profile, filters and target.

The add-on finds the ids and the viewers. CrossWatch does the routing, throttling, filters and the now-playing card.

## Where events go

Kodi is the source, not the destination. The add-on says who watched what, and CrossWatch writes it wherever the route points: Trakt, Simkl, or a Plex, Emby or Jellyfin server.

So a Kodi watch can end up marked watched on someone's own Plex. That is why timestamps and an unknown `percent` matter this much.

## Setup

- Every Kodi device is its own Kodi instance in CrossWatch.
- The add-on gets its URL and token by pairing, see below. Nobody types a token on a TV.
- Manual setup stays possible for people who prefer it: the instance page can still offer the full URL with `?token=` behind a manual option. The add-on accepts that URL pasted into its Webhook URL setting, takes the token out of it, and sends the token only in the header.
- Routes work like today. For example `Kodi Living room -> Trakt Anna` with whitelist `anna`, and `Kodi Living room -> Trakt Tom` with whitelist `tom`.

## Endpoint

```
POST /webhook/kodiwatcher
X-CrossWatch-Token: <token>
Content-Type: application/json
```

Use the header. `?token=<token>` still works as a fallback, but it ends up in reverse proxy logs.

One URL for everything. The event type is in the body.

## Pairing

The normal way to connect the add-on. The user types the CrossWatch address and a short code into the add-on, and the add-on swaps the code for the token.

1. The user opens the Kodi instance in CrossWatch and asks for a pairing code.
2. In the add-on they enter the CrossWatch address (for example `http://192.168.1.10:8787`) and the code.
3. The add-on posts the code:

```
POST /webhook/kodiwatcher/pair
Content-Type: application/json

{ "code": "ABC123" }
```

4. CrossWatch answers with the endpoint, the token and the instance name:

```json
{ "ok": true, "url": "http://192.168.1.10:8787/webhook/kodiwatcher", "token": "<token>", "instance": "Living room" }
```

- The code is 6 characters, valid for 10 minutes, and works once.
- Capitals and digits, without `0`, `O`, `1` or `I`. CrossWatch ignores case and spaces, and the add-on uppercases the code and strips spaces before sending it.
- A wrong, expired or used code gets a `401` with `{ "ok": false, "error": "invalid_code" }`.
- Too many tries get a `429`.
- `url` is the plain endpoint, without the token. The token only ever travels in the `X-CrossWatch-Token` header.
- The add-on keeps the address it just posted to, since that one is known to reach CrossWatch from this Kodi, and shows `instance` so the user sees which entry it paired with.
- Right after pairing the add-on sends a `ping`, so CrossWatch sees it within seconds.

### Link (shortcut)

Only for a Kodi that CrossWatch already reaches over JSON-RPC. Kodi has its web server off by default, so most Kodis cannot use this.

CrossWatch calls Kodi:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "Addons.ExecuteAddon",
  "params": {
    "addonid": "service.crosswatch",
    "params": ["action=link", "url=<endpoint>", "code=<code>"]
  }
}
```

- Use the array form of `params`. Kodi quotes each array item, but joins an object as `key=value` with commas and no quoting, so a comma in a value would break it.
- `url` is the plain endpoint, as with pairing.
- `code` is a one-time code, like a pairing code: single use, good for 10 minutes. It is separate from the code on the instance page, so linking does not cancel a code the user is typing, and the other way round.
- Not the token: Kodi writes every script argument to `kodi.log` at debug level, so a token would end up in debug logs. A code found there is already used or expired.
- The add-on asks the user on the TV to confirm, showing the CrossWatch address. Yes redeems the code at `/webhook/kodiwatcher/pair`, exactly like a typed code (same answer, same `401` and `429`), stores the address and token, and sends a `ping` at once. No, or no answer, changes nothing and leaves the code unused.
- Kodi answers `OK` as soon as the add-on starts, before the user has answered. So the first `ping` with the new token is the signal that linking worked.

## Add-on mode

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
| `event` | yes | `ping`, `start`, `resume`, `pause`, `progress`, `stop` or `rate`. |
| `event_id` | yes | Unique per event. For logs, and for dedupe if CrossWatch ever needs it. |
| `session_id` | playback | Same value for one playback on one device. On a `rate`, the playback it follows. |
| `sent_at` | yes | ISO-8601 UTC, when the event happened. CrossWatch uses it as the watch time. |
| `replayed` | no | `true` when a stored `stop` or `rate` is delivered later. |
| `addon_version` | no | Shown in CrossWatch. |
| `device.id` | yes | Stable device id. Works with the server UUID filters. |
| `device.name` | no | Shown in CrossWatch. |
| `viewers` | yes | Who is watching. Can be empty. On a `rate`, only who the rating belongs to. |
| `viewers_source` | no | `playlist`, `profile` or `prompt`. How identity was decided. Diagnostic only. |
| `rating` | `rate` only | Whole number, `1` to `10` to set, `0` to remove. See Ratings. |
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

"Playback" in the Needed column includes `rate`. Unknown fields are ignored. A `ping` has no `media` and no `session_id`.

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
| `rate` | One viewer rated what was just watched, or removed their rating. See Ratings. |

Live TV and recordings are out of scope. `Player.GetProperties` has a `live` property, and a channel reports `type: "channel"`. Send nothing for those. Recordings carry EPG metadata instead of library ids, which is a matching problem for CrossWatch to solve later.

## Viewers

Each Kodi route checks `viewers` against its own whitelist. If it matches, that route gets the event once, as that viewer.

- `["anna", "tom"]` goes to Anna's route and Tom's route. Once each.
- Routes without a whitelist get everything.
- Empty `viewers` only goes to routes without a whitelist.

The `ping` sends all viewer names set up in the add-on. CrossWatch keeps them and shows them in the user picker.

## Ratings

A `rate` is its own event, sent after the `stop` of a finished watch. The `stop` never waits for it.

```json
{
  "version": 1,
  "event": "rate",
  "event_id": "2b9d6c41-8a17-4f3e-b0c5-6e1f9a7d3c22",
  "session_id": "kodi-livingroom-1726742400",
  "sent_at": "2026-09-19T20:52:40Z",
  "device": { "id": "b8f1c2d4-livingroom", "name": "Living room" },
  "viewers": ["anna"],
  "rating": 8,
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
    "source": "library"
  }
}
```

- `rating` is a whole number. `1` to `10` sets the rating. `0` removes it.
- Only send a `0` when the user chose to remove their rating. Closing or skipping the question sends nothing.
- One event per viewer. `viewers` holds exactly the one person the rating belongs to. A playback without any viewer sends one event with an empty `viewers`.
- With several viewers the add-on asks each one, and every answer is its own event. When the household chose a shared rating in the add-on, it still sends one event per viewer, each with the same score.
- `media` is the same block as on the `stop`: same type, titles and ids. `percent`, `position_ms`, `duration_ms`, `completed` and `file` are not needed.
- The rating is for what was played: the movie or the episode. Rating the show itself is not part of this.

What CrossWatch does with it:

- It routes a `rate` like a `stop`. The route of that viewer gets it once, and routes without a whitelist get it too.
- It forwards the rating to the route's destination when that destination takes ratings. There is no extra switch on the CrossWatch side. The switch is the add-on's own setting.
- Not every service takes a rating for an episode. CrossWatch sorts that out and skips the ones that do not. It does not turn an episode rating into a show rating.
- A `rate` never touches the now-playing card or the watched state.
- The same rating sent twice is harmless. CrossWatch ignores an identical rating for the same viewer and title within 10 seconds.
- Show ids plus season and episode are enough for an episode rating. The episode's own id is not needed.

The answer lists every route CrossWatch wrote to:

```json
{
  "ok": true,
  "ignored": false,
  "crosswatch_version": "0.13.0",
  "rated": [
    { "id": "R1", "sink": "trakt", "ok": true }
  ]
}
```

- `rated` has one entry per route that took the rating. `ok` is `false` when the write to that destination failed. That is still a `200`, so don't retry it.
- A `rating` that is not a whole number from `0` to `10` gets `ignored: true` with `invalid_rating`.
- When no route accepts that viewer, the answer is `ignored: true` with `no_matching_route`.
- When routes match but none can take the rating, the answer is `ignored: true` with `no_rating_target`. For example an episode rating to a Simkl route.

Where a rating can go:

| Destination | Movie | Episode | Note |
|---|---|---|---|
| Trakt | yes | yes | |
| Plex | yes | yes | The title must be in the Plex library. |
| MDBList | yes | yes | |
| CrossWatch tracker | yes | yes | |
| Floppy | yes | yes | Needs a TMDb id. |
| PunchPlay | yes | yes | |
| FlickList | yes | yes | |
| WeTrakr | yes | yes | |
| Scrob | yes | yes | Needs a TMDb id. |
| Simkl | yes | no | Simkl has no episode ratings. |
| AniList | yes | no | Scores a whole anime entry. |
| Kitsu | yes | no | Scores a whole anime entry. |
| MyAnimeList | yes | no | Scores a whole anime entry. |

Emby, Jellyfin, Kodi and BingeBase take no rating.

Writing the rating into the Kodi library is the add-on's own choice and not part of this contract. Kodi holds one rating per item, not one per viewer.

## Cadence

- Send every state change.
- Send `progress` every 60 seconds and after a seek.
- CrossWatch does the throttling. You don't have to.

## Delivery

- No queue for normal events. Retry for 2 minutes counted from when the event happened, then drop it. So nothing arrives late.
- One exception: a `stop` that completes a watch goes to disk before the first attempt and is kept until CrossWatch takes it, up to 7 days, surviving a Kodi restart. Kodi bumps its own playcount anyway, but only the add-on knows who watched.
- A `rate` follows the same rule as that `stop`: stored before the first attempt, kept up to 7 days, and replayed with its original `event_id` and `sent_at` and `replayed: true`. Only the add-on knows who rated.
- A stored stop is replayed with its original `event_id` and `sent_at`, and carries `replayed: true`. Plex, Emby and Jellyfin date the watch from `sent_at`. Trackers, CrossWatch's own history included, record it when it arrives.
- A replayed `rate` is recorded when it arrives.
- A late stop never touches the now-playing card. That is keyed per session.
- Timeout of 10 seconds.
- Retry on connection errors, timeouts, `5xx` and a lost response. A duplicate is safe: Trakt and Simkl dedupe server side, and the media server sinks just set a watched flag. A lost `stop` is worse.
- Don't retry a `200` with `ignored: true`, and don't retry a `401`.

## Responses

CrossWatch always answers with JSON, normally a `200`.

```json
{ "ok": true, "ignored": false, "crosswatch_version": "0.13.0" }
```

If `ignored` is `true`, `error` tells why. For example `no_routes`, `no_matching_route` or `watcher_disabled`. Show it in the add-on. Don't retry.

- A bad token gets a `401` with `{ "ok": false, "error": "invalid_token" }`. Don't retry.
- An error inside CrossWatch gets a `500` with `internal_error`. Retry like any `5xx`.

A `ping` also tells which routes each viewer hits. Handy for a Test connection button. With no routes yet it is still a plain `ok`, with an empty `routes` list.

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

## Versions

CrossWatch takes what it understands and ignores the rest. Also from a newer `version`.

There is no minimum CrossWatch version to check. Only a CrossWatch with this endpoint can take an event, and it reads an absent `percent` as unknown. An older CrossWatch answers `404`, so nothing is written.

A CrossWatch that does not know `rate` yet answers it with `ignored: true` and `unsupported_event`. Nothing is written.

## PlexKodiConnect

PKC playback is just a normal event with the ids the add-on found. `media.plex_rating_key` comes along when there is one, but CrossWatch does not use it yet.

The add-on skips PKC playback by default, so people who also run the Plex watcher with PKC support do not get double scrobbles. Untested so far, since PKC is not installed on the dev machine.

For `plugin://` paths, send them as they are. Path filters just won't match them.
