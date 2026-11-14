# Kodi add-on sync contract

**Working draft for v1. Not implemented or agreed yet.** Updated 2026-11-14.
Related: [kodi-addon #72](https://github.com/crosswatch-app/kodi-addon/issues/72)

## What we're building

Kodi becomes a normal sync provider in CW, and the add-on is its transport. The add-on uploads library rows with each viewer's state. CW runs its normal sync pairs and queues the changes for Kodi. The add-on applies them locally and confirms.

CW never connects to Kodi, and Kodi's web server isn't needed. Pairing, playback events and the rating window stay as they are.

**Responsibility split.** This is the rule that keeps CW from growing a second sync engine:

- **CW** knows instances, rows and the queue. It reuses its existing Kodi provider unchanged, with a different transport behind KodiClient. Matching, pairs, conflict handling and provider writes stay in CW.
- **The add-on** owns everything about viewers and Kodi: who owns an item, shared items, the viewer ledger, prompts, the assignment list, and how a change lands in Kodi.

## Setup

1. The user installs the add-on and pairs it with the code, as today.
2. In CW, the user picks a target per viewer, for example user1 to Trakt user1 and user2 to Trakt user2, with switches for live scrobble and library sync.
3. CW creates the watcher route and the sync pair itself, marked as managed by the add-on. Power users can still edit both.

Each viewer becomes its own Kodi instance in CW. With one viewer, that's the default instance.

An instance syncs either through the add-on or over JSON-RPC, never both. One sync device per library: devices that report the same `library_id` share a library, the user picks the sync device in setup, and the others keep the watcher. The add-on also has a setting to turn sync off on a device. A profile viewer only syncs when its profile has its own library id. Items without an owner aren't uploaded for anyone.

## Connection

- Authenticate with `X-CrossWatch-Token`, the existing paired token. Sync endpoints refuse `?token=` with `400 invalid_request`.
- All paths live under `/webhook/kodiwatcher/sync/v1/`. Resolve them against the paired address with its query removed and a trailing slash added, so proxy prefixes are kept.
- Never send the token to another origin, including through redirects.
- HTTP stays supported. Both setup screens warn that history and tokens then travel unencrypted.

Authenticated ping replies, including those with `ignored: true`, carry:

```json
{"library_sync": {"version": 1, "enabled": true, "viewers": [{"viewer_id": "kv-1", "instance": "kodi-user1"}], "queue_pending": 3}}
```

A missing block or an unsupported version means sync is off. The add-on reports its viewers in the ping (`viewer_id` stable, plus `name`). CW maps each viewer to an instance. `queue_pending` tells the add-on to fetch the queue.

The ping also carries:
- `library_id`: a hash of the database type, host, port, name and Kodi's major version, per device on SQLite. Only the hash leaves Kodi.
- Kodi's version and JSON-RPC version. The transport answers the health check from these.

## Upload

`POST sync/v1/upload`, gzip allowed. One request per viewer, or one per part when a full upload is split.

```json
{
  "version": 1,
  "viewer_id": "kv-1",
  "mode": "full",
  "upload_id": "u-77",
  "part": 1,
  "parts": 1,
  "applied_seq": 120,
  "timezone": "Europe/Amsterdam",
  "sources": [{"file": "nfs://nas/movies/", "label": "Movies"}],
  "movies": [{"movieid": 12, "uniqueid": {"tmdb": "550"}, "title": "Fight Club", "year": 1999, "file": "nfs://nas/movies/fc.mkv",
              "playcount": 1, "lastplayed": "2026-11-10 21:04:00", "userrating": 8, "resume": {"position": 0, "total": 8340}}],
  "episodes": [{"episodeid": 40, "tvshowid": 3, "uniqueid": {"tvdb": "3254641"}, "title": "Home", "showtitle": "The Expanse",
                "season": 2, "episode": 5, "file": "...", "playcount": 0, "lastplayed": "", "userrating": 0, "resume": {"position": 0, "total": 0}}],
  "tvshows": [{"tvshowid": 3, "uniqueid": {"tmdb": "63639"}, "title": "The Expanse", "year": 2015}]
}
```

**Rows.** Rows use the shape of Kodi's GetMovies, GetEpisodes and GetTVShows results. `sources` is the Files.GetSources result, so the library picker and path filters work. The add-on strips credentials from paths. `timezone` is the device's IANA zone; CW uses it to convert `lastplayed` in both directions.

**Viewer state, not Kodi state.** `playcount`, `lastplayed`, `userrating` and `resume` are the viewer's state from the add-on's record: what CW applied for them, plus changes assigned to them. They are never the raw play count.

**The upload is a ledger.** A watched item only leaves a viewer's state on an explicit unwatch, and only with the unwatch setting on. A show moving to another viewer, a lost owner or a removed file never clears it.

**Removed files.** A removed file keeps its row with `"in_library": false`:
- The row carries no Kodi id, because Kodi ids aren't stable. CW gives it an internal id that can never be written to.
- A removed episode also carries its show as `"show": {"uniqueid": {...}, "title": "...", "year": 2015}`, because the show row may be gone too.
- An item is never uploaded twice. When the file comes back, the same row gets the new Kodi id.
- CW never writes to such a row and leaves it out of library presence and collection.

**Full and delta.**
- `full` replaces the viewer's rows once all parts have arrived.
- `delta` only updates the rows it contains and never removes any.

**Send:**
- a delta right away on VideoLibrary.OnUpdate (ignored during and just after playback, and for the add-on's own writes)
- a cheap rating scan every few minutes while idle
- a full upload at startup, after a library scan and once a day
- nothing during playback

**Changes made in Kodi.** Manual watched, manual unwatched (with the setting on) and Kodi's own ratings reach CW through uploads. CW's pairs carry them on to the providers. Shared items wait in the add-on's assignment list until someone assigns them. On the first run, sending existing ratings is an explicit step in the add-on.

**First run.** Before the record holds anything, an item with one owner starts from Kodi's own state, and a shared item stays out until it's assigned. So the first full upload doesn't hold every watched item; the pairs fill in the rest as queued changes.

## Queue

`GET sync/v1/queue?viewer_id=kv-1` returns the changes in order. `seq` counts per viewer:

```json
{"version": 1, "changes": [{"seq": 121, "method": "VideoLibrary.SetEpisodeDetails",
  "params": {"episodeid": 40, "playcount": 1, "lastplayed": "2026-11-11 20:10:00"}}]}
```

**What a change means.** The params are exactly what CW's Kodi provider sends over JSON-RPC today, but each change is an intent for that viewer. The add-on updates the viewer's record, then decides what Kodi gets:
- all or any owners for shared items
- a resume point is cleared only when the watch is newer than the last play here
- an unwatch only lands with the setting on

Only these methods are allowed. Besides the item id, a change may only carry `playcount`, `lastplayed`, `userrating` and `resume`. The add-on refuses anything else.

**Confirming.** `POST sync/v1/queue/ack`:

```json
{"version": 1, "viewer_id": "kv-1", "results": [{"seq": 121, "status": "applied"}, {"seq": 122, "status": "failed", "error": "item_not_found"}]}
```

`applied` means the viewer's record was updated, even when Kodi's own play count didn't change. A later upload's `applied_seq` must include it.

**Pending changes.** Until a change is acked, CW counts it as done when it reads the viewer's rows. Every upload says up to which `seq` it already reflects (`applied_seq`), and CW overlays the changes after that. So a stale upload can't undo a pending change, and nothing is queued twice. A failed change drops out, and the next pair run plans it again.

## Health

An instance is ready once it has a complete full upload. Kodi doesn't need to be on: changes queue while it's off, and the add-on applies them when it starts. Without a complete full upload, the pair's read fails and the pair is skipped for that run. An upload that is missing or partial is never treated as an empty library.

When a full upload completes, CW runs the managed pairs for that instance right away, unless a run is already going.

## Errors

```json
{"ok": false, "error": {"code": "viewer_unknown", "message": "Unknown viewer.", "retryable": false}}
```

| HTTP | Code | Add-on action |
| --- | --- | --- |
| 400 | invalid_request | Fix the request; don't resend it unchanged. |
| 401 | invalid_token | Stop sync and reset the queue position. Keep the viewers' records. |
| 403 | sync_disabled | Stop uploads and queue fetches until the ping says enabled. |
| 409 | viewer_unknown / upload_incomplete | Refresh the viewers from the ping, or restart the full upload. |
| 429 / 503 | rate_limited / temporarily_unavailable | Retry with backoff, after Retry-After when it's given. |

Network failures and other 5xx responses also get bounded backoff. Existing webhook behaviour stays unchanged.

**Next:** agree on this draft, then CW builds the endpoints and the transport, and the add-on builds upload, queue and ack.
