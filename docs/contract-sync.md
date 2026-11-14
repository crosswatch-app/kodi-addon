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

Each viewer becomes its own Kodi instance in CW: a sync user of the paired instance, for example `default~kv-2`. With one viewer, that's the paired instance itself.

**One sync device per connection.**
- The first device that pings with sync on becomes the sync device for that paired connection. Other devices keep the watcher, and get `403 not_sync_device` on sync requests.
- Devices that report the same `library_id` share a library; the user picks the sync device in setup.
- Turning sync off on the sync device releases it, so another device can take over. Pairing again also resets it.

**One viewer per instance.** The paired instance is bound to the first viewer that uploads to it. Another viewer is refused until the binding is released, by turning sync off on the device or pairing again. When a viewer's first full upload commits, CW starts that Kodi instance fresh. Earlier JSON-RPC or add-on history for it can then never cause deletes on the other side.

An instance syncs either through the add-on or over JSON-RPC, never both. An instance with JSON-RPC configured keeps JSON-RPC, and its viewers aren't listed in the ping reply. A profile viewer only syncs when its profile has its own library id. Items without an owner aren't uploaded for anyone.

## Connection

- Authenticate with `X-CrossWatch-Token`, the existing paired token. Sync endpoints refuse `?token=` with `400 invalid_request`.
- Every sync request carries the device id in `X-CrossWatch-Device`. Without it the request gets `400 invalid_request`.
- All paths live under `/webhook/kodiwatcher/sync/v1/`. Resolve them against the paired address with its query removed and a trailing slash added, so proxy prefixes are kept.
- Never send the token to another origin, including through redirects.
- HTTP stays supported. Both setup screens warn that history and tokens then travel unencrypted.
- Every sync reply carries `"ok"`. A 2xx without it isn't from CW (for example a proxy login page).

The add-on's normal ping carries its device and a `library_sync` block:

```json
{"event": "ping", "device": {"id": "d-7f3a", "name": "Living room"},
 "library_sync": {"version": 1, "enabled": true, "viewers": [{"viewer_id": "kv-1", "name": "user1"}],
                  "library_id": "...", "kodi_version": "21.2", "jsonrpc_version": "13.5.0"}}
```

- `enabled: false` is the add-on setting that turns sync off on this device.
- `viewers` lists every viewer. `viewer_id` is stable and unique per install, for example a random id.
- `library_id` is a hash of what Kodi actually opens: the database folder and name for SQLite, or the type, host, port and versioned database name for MySQL/MariaDB. Only the hash leaves Kodi. It changes on a Kodi upgrade, which is fine.
- Kodi's version and JSON-RPC version. The transport answers the health check from these.

Authenticated ping replies, including those with `ignored: true`, carry:

```json
{"library_sync": {"version": 1, "enabled": true, "viewers": [{"viewer_id": "kv-1", "instance": "default", "upload_seq": 41}], "queue_pending": 3}}
```

- A missing block or an unsupported version means sync is off.
- `enabled` is true only when sync is on in CW and on this device, and this device is the sync device. Otherwise `reason` says why: `not_sync_device` or `device_id_missing`. A ping from a device that isn't the sync device never changes anything in CW.
- `viewers` lists the viewers CW mapped to an instance; an unmapped viewer is left out. `upload_seq` is the last one CW saw for that viewer.
- `queue_pending` tells the add-on to fetch the queue.

## Upload

`POST sync/v1/upload`, gzip allowed with `Content-Encoding: gzip`. One request per viewer, or one per part when a full upload is split.

```json
{
  "version": 1,
  "viewer_id": "kv-1",
  "mode": "full",
  "upload_id": "u-77",
  "upload_seq": 42,
  "part": 1,
  "parts": 3,
  "applied_seq": 120,
  "sources": [{"file": "nfs://nas/movies/", "label": "Movies"}],
  "movies": [{"movieid": 12, "uniqueid": {"tmdb": "550"}, "title": "Fight Club", "year": 1999, "file": "nfs://nas/movies/fc.mkv",
              "playcount": 1, "lastplayed": "2026-11-10T20:04:00Z", "userrating": 8, "resume": {"position": 0, "total": 8340}}],
  "episodes": [{"episodeid": 40, "tvshowid": 3, "uniqueid": {"tvdb": "3254641"}, "title": "Home", "showtitle": "The Expanse",
                "season": 2, "episode": 5, "file": "...", "playcount": 0, "lastplayed": "", "userrating": 0, "resume": {"position": 0, "total": 0}}],
  "tvshows": [{"tvshowid": 3, "uniqueid": {"tmdb": "63639"}, "title": "The Expanse", "year": 2015}]
}
```

Reply: `{"ok": true, "mode": "full", "complete": false, "received_parts": [1], "instance": "default"}`. `complete` turns true when the last part has arrived.

**Order.**
- Uploads for a viewer are sent one at a time, in order.
- Every upload, full or delta, carries `upload_seq`: a whole number that increases per viewer across both modes. All parts of one full upload share it.
- CW refuses anything that isn't newer than the last `upload_seq` it saw with `409 upload_stale`. An exact retry of the last full upload or the last delta is accepted without being applied twice.
- The ping reply shows the last `upload_seq`, so a reinstalled add-on continues above it.
- A full upload reflects every delta sent before its first part. Deltas sent between its parts are kept on top of it.
- A full upload that CW dropped can't be resumed: its parts get `409 upload_stale`. Send a new one with a higher `upload_seq`. CW drops a full upload when it's too large, replaced by a newer one, or the viewer or library changed.

**Size.**
- A request holds at most 10,000 rows per list. Keep each request under about 1 MB so reverse proxies let it through; use gzip and more parts.
- A full upload holds at most 250,000 rows in total.
- Anything larger gets `413 payload_too_large`: split it smaller.

**Rows.**
- Rows use the shape of Kodi's GetMovies, GetEpisodes and GetTVShows results.
- `sources` is the Files.GetSources result, so the library picker and path filters work. The add-on strips credentials from paths.
- `lastplayed` and `dateadded` are UTC, as `2026-11-10T20:04:00Z`; the add-on converts with the operating system's rules. `timezone` is optional: an IANA zone, only used when a row still has Kodi's local format.

**Validation.**
- Every full part carries the `movies`, `episodes` and `tvshows` lists, empty when there are none. A delta may leave lists out.
- Every movie and episode row, live or removed, carries a valid `playcount` (whole number, 0 or more), `lastplayed` (string), `userrating` (0 to 10) and `resume` (`position` and `total`). `unwatched` is optional and must be true or false.
- A row that isn't an object, a library row without a valid Kodi id, or an invalid `timezone` rejects the whole request with `400 invalid_request`. CW's state stays as it was.

**Viewer state, not Kodi state.** `playcount`, `lastplayed`, `userrating` and `resume` are the viewer's state from the add-on's record: what CW sent for them, plus changes assigned to them. They are never the raw play count. A change from CW always updates the record; the add-on's settings only decide what Kodi's own database gets.

**Nothing leaves by absence.**
- A watch only goes away with an explicit `"unwatched": true` on the row. The add-on only sends that for a real unwatch, with the unwatch setting on.
- `playcount: 0` without it never removes a watch. `userrating: 0` and a cleared resume do count as a removal.
- Rows left out of an upload, removed files, a new `library_id` or a reinstall never remove anything. CW keeps each viewer's state per item, by its ids, so it survives a new library and new Kodi ids.

**Removed files.** A removed file keeps its row with `"in_library": false`:
- The row carries no Kodi id, because Kodi ids aren't stable. CW gives it an internal id that can never be written to.
- A removed episode also carries its show as `"show": {"uniqueid": {...}, "title": "...", "year": 2015}`, because the show row may be gone too.
- A removed row in a delta replaces the live row with the same file, or with the same ids when the removed row has no file.
- An item is never uploaded twice. When the file comes back, the same row gets the new Kodi id.
- CW never writes to such a row and leaves it out of library presence and collection.
- When Kodi reuses an id for a different item, the previous item keeps its history in CW.

**Full and delta.**
- `full` replaces CW's view of which items are in the library, with their Kodi ids, once all parts have arrived.
- `delta` only updates the rows it contains and never removes any.
- A delta needs a complete full upload for the same viewer and library first, otherwise `409 upload_incomplete`.
- A new `library_id`, or a change in the viewer-to-instance mapping, makes the instance wait for a new full upload, even when it changes back. Queued changes stay, and after the full upload they're sent with the item's new Kodi id.

**Send:**
- a delta right away on VideoLibrary.OnUpdate (ignored during and just after playback, and for the add-on's own writes)
- a cheap rating scan every few minutes while idle
- a full upload at startup, after a library scan and once a day
- nothing during playback

**Changes made in Kodi.** Manual watched, manual unwatched (with the setting on) and Kodi's own ratings reach CW through uploads. CW's pairs carry them on to the providers. Shared items wait in the add-on's assignment list until someone assigns them. On the first run, sending existing ratings is an explicit step in the add-on.

**First run.** Before the record holds anything, an item with one owner starts from Kodi's own state, and a shared item stays out until it's assigned. So the first full upload doesn't hold every watched item; the pairs fill in the rest as queued changes.

## Queue

`GET sync/v1/queue?viewer_id=kv-1&after=0&limit=500` returns the changes in order. `seq` counts per viewer. `limit` defaults to 500, at most 1000.

```json
{"ok": true, "version": 1, "has_more": false, "next_after": null,
 "changes": [{"seq": 121, "method": "VideoLibrary.SetEpisodeDetails",
              "params": {"episodeid": 40, "playcount": 1, "lastplayed": "2026-11-11T19:10:00Z"},
              "item": {"type": "episode", "uniqueid": {"tvdb": "3254641"}, "title": "Home", "file": "...",
                       "showtitle": "The Expanse", "season": 2, "episode": 5, "show_uniqueid": {"tmdb": "63639"}}}]}
```

With `has_more: true`, fetch the next page with `after` set to `next_after`.

**What a change means.**
- The params are exactly what CW's Kodi provider sends over JSON-RPC today, but each change is an intent for that viewer. `lastplayed` is UTC; the add-on converts it to Kodi's local format before writing.
- `item` says which item CW matched (a movie gets `year` instead of the show fields). The add-on checks it against the current row. On a mismatch it acks `item_changed`, which CW treats as a failed write, never as a delete.
- The add-on always updates the viewer's record, then decides what Kodi gets:
  - all or any owners for shared items
  - a resume point is cleared only when the watch is newer than the last play here
  - an unwatch only lands in Kodi with the setting on

**Allowed.** Only these two methods. Besides the item id, a change may only carry `playcount`, `lastplayed`, `userrating` and `resume`. The add-on refuses anything else.

**One change per item and field.** A newer change for the same item and field replaces the older one: latest wins, the older `seq` disappears from the queue, and an ack for it is ignored. So the queue stays bounded while Kodi is off.

**Confirming.** `POST sync/v1/queue/ack`:

```json
{"version": 1, "viewer_id": "kv-1", "results": [{"seq": 121, "status": "applied"}, {"seq": 122, "status": "failed", "error": "item_changed"}]}
```

Reply: `{"ok": true, "applied": 1, "failed": 1}`.

- `applied` means the viewer's record was updated, even when Kodi's own play count didn't change.
- `failed` is only for a change the add-on can't record at all. CW logs it and stops sending it.
- No ack ever undoes CW's change on CW's side. A failed change only gives way to a newer upload with a real, non-zero value for that item.

**`applied_seq`.**
- Every upload says the highest `seq` its record already reflects.
- A change from CW only ends once an upload carries that item's row and its `applied_seq` covers the change. Until then CW keeps its own value for that item.
- A delta that carries other items never ends it.

## Health

- An instance is ready once it has a complete full upload. Kodi doesn't need to be on: changes queue while it's off, and the add-on applies them when it starts.
- Without a complete full upload the instance reports not ready, and its pairs are skipped for that run, not failed. An upload that is missing or partial is never treated as an empty library.
- When a full upload completes, CW runs the managed pairs for that instance right away. If a sync is already running, CW retries until the run is accepted.

## Errors

```json
{"ok": false, "error": {"code": "viewer_unknown", "message": "Unknown viewer.", "retryable": false}}
```

| HTTP | Code | Add-on action |
| --- | --- | --- |
| 400 | invalid_request | Fix the request, for example a missing list or device id, a malformed row or an invalid timezone; don't resend it unchanged. |
| 401 | invalid_token | Stop sync and reset the queue position. Keep the viewers' records. |
| 403 | sync_disabled | Sync is off in CW or on this device. Stop uploads and queue fetches until the ping says enabled. |
| 403 | not_sync_device | Another device is the sync device. Keep the watcher only. |
| 409 | viewer_unknown | Refresh the viewers from the ping. |
| 409 | upload_incomplete | Send a full upload. |
| 409 | upload_stale | Send a new upload with a higher upload_seq. |
| 413 | payload_too_large | Split into smaller parts. |
| 429 / 503 | rate_limited / temporarily_unavailable | Retry with backoff, after Retry-After when it's given. |

Network failures and other 5xx responses also get bounded backoff. Existing webhook behaviour stays unchanged.

**Next:** agree on this draft. The CW side is built behind a hidden switch (`runtime.kodi_addon_sync`) for testing; the add-on builds upload, queue and ack.
