# Kodi add-on sync contract

**Working draft for v1 — not implemented or agreed yet.** Updated 2026-11-05.

Related: [kodi-addon #72](https://github.com/crosswatch-app/kodi-addon/issues/72)

## What we're building

The add-on starts every connection. It pulls history and ratings on startup and at an interval, then updates Kodi locally. CW doesn't need access to Kodi's HTTP server. Existing pairing and playback/rating events stay unchanged.

**V1 includes manual watched/unwatched changes from Kodi to CW. A resume point must not block a watched-state update from CW.** These are core behaviour, not follow-up features. The remaining manual-write decisions are listed below.

CW handles permissions, viewer scopes and exported state. The add-on handles ownership, matching, preview and local writes. Start with an opt-in preview. Keep downloads and long library updates out of the playback reporting loop, pause writes during playback, and recheck the item before writing.

## Connection and activation

Use `X-CrossWatch-Token` with the existing paired token. New sync endpoints reject `?token=` with `400 invalid_request`; they don't require browser login. CW enables read access separately and shows which viewers/accounts it exposes. A scrobble route alone isn't permission to export full history. Check authorization on every request.

Add this to an authenticated ping reply, including replies with `ignored: true`:

```json
{
  "library_sync": {
    "version": 1,
    "enabled": true,
    "active": false,
    "reason": null,
    "features": ["history", "ratings"],
    "viewers_url": "sync/v1/viewers",
    "state_url": "sync/v1/state"
  }
}
```

Missing block or unsupported version means don't use this API. Read the block before discarding an ignored reply. `features` lists pull features; it doesn't advertise manual-write support yet.

Resolve advertised paths against the saved webhook URL with its query/fragment removed and a trailing slash added:

```text
https://example.com/cw/webhook/kodiwatcher/
+ sync/v1/state
= https://example.com/cw/webhook/kodiwatcher/sync/v1/state
```

Keep proxy prefixes. Never send the token to another origin, including through redirects. HTTP stays supported; both enable screens should mention that it sends history and tokens unencrypted.

Preview needs only `enabled: true` and mustn't change polling or pairs. To request actual sync, add `"library_sync": {"version": 1, "active": true}` to the normal ping. Wait for `active: true` in the reply before applying imports. Repeating this is safe.

Proposed activation rules:

- CW refuses conflicting direct history/ratings pairs in either direction, rather than silently disabling them. Progress pairs that affect watched/resume state also need checking; they aren't automatically exempt.
- `reason` is null, `read_disabled`, `pair_conflict` or `no_ready_viewers`. Report blockers in preview too.
- Accepted client-only mode persists across restarts and heartbeat expiry. Kodi being off must never restore remote watcher polling. Prevent conflicting pairs from being enabled while this mode is selected.
- The add-on stops writing immediately when turned off and sends `active: false`. An omitted field leaves the selection unchanged. CW also has an explicit off switch.
- Revoking read access/token stops imports without silently restoring polling. Only explicitly leaving client-only mode does that. Never automatically re-enable disabled pairs.
- Reconfirm activation after startup, reconnect or a mapping/authentication failure.

## Viewers

`GET /webhook/kodiwatcher/sync/v1/viewers`

```json
{
  "version": 1,
  "mapping_revision": "m-12",
  "viewers": [
    {
      "viewer_id": "kv-anna",
      "person_id": "p-anna",
      "name": "Anna",
      "default": false,
      "status": "ready",
      "reason": null
    }
  ]
}
```

`viewer_id` is stable within this Kodi instance, even after renaming/linking. `person_id` is the linked profile or null. Use existing person links or a confirmed local association; don't guess between names. Outgoing playback events still use names. This doesn't replace the existing `people` list.

Status is `ready`, `unmapped` or `ambiguous`. Reasons are null, `empty_scope`, `multiple_matches`, `shared_source` or `unverified_source_identity`. Only ready viewers can be read. Mapping readiness doesn't imply imported history is available.

**Every read must have a nonempty, explicitly authorized account scope. Empty means unmapped, never everyone.** A null person_id needs an explicit account mapping. `mapping_revision` is instance-wide and changes with associations, defaults or authorized scopes.

For one person without a profile, whitelist or local viewers, offer one `default: true` viewer when CW can resolve an unambiguous authorized scope. Confirm its accounts in CW. Only use that default automatically when no local viewers exist.

Shared accounts aren't independent evidence for multiple people. Proposed v1 handling: mark overlapping viewer scopes ambiguous until configured as separate accounts or one explicitly shared viewer. Compare actual provider account identities, not instance labels. **Still to agree:** whether CW should instead exclude shared sources while keeping each viewer's independent sources usable.

## State payloads

`GET /webhook/kodiwatcher/sync/v1/state?viewer_id=kv-anna&feature=history&limit=500`

Feature is `history` or `ratings`. Limit is 1–1000, default 500. Example history page; IDs are illustrative:

```json
{
  "version": 1,
  "viewer_id": "kv-anna",
  "feature": "history",
  "mapping_revision": "m-12",
  "content_token": "c-h41",
  "snapshot_id": "s-902",
  "generated_at": "2026-11-05T12:00:00Z",
  "availability": {
    "status": "ready",
    "sources": [
      {
        "provider": "TRAKT",
        "instance": "anna",
        "status": "ready",
        "last_successful_refresh_at": "2026-11-05T11:55:00Z"
      }
    ]
  },
  "items": [
    {
      "key": "item-episode-42",
      "media": {
        "type": "episode",
        "title": "The Expanse",
        "episode_title": "Home",
        "ids": {"tmdb": "1234567", "tmdb_show": "63639"},
        "season": 2,
        "episode": 5,
        "numbering": {"scheme": "tmdb", "order": "aired", "source_provider": "TRAKT"}
      },
      "watched": true,
      "play_count": 2,
      "last_watched_at": "2026-11-04T20:52:40Z"
    }
  ],
  "next_cursor": null
}
```

Ratings use the same envelope and media shape, replacing watched/count/date with:

```json
{
  "rating_status": "rated",
  "rating": 8,
  "rated_at": null
}
```

Ratings are integers 1–10. Conflicting source ratings use `rating_status: "conflict"` with null rating/date and are skipped. Don't resolve a rating while a missing selected source could hide a conflict. All dates are UTC RFC 3339 or null.

### Matching

Support movies and episodes. Keep flat string IDs: imdb/tmdb/tvdb for the item, and corresponding _show keys for its show. Include known episode IDs. Titles are strings or null; episode coordinates are integers or null, with season zero valid. Movies omit episode_title, coordinates and numbering. Omit unusable identities/placeholders. `key` is opaque; don't parse it.

Numbering scheme is tmdb, tvdb, imdb or unknown; order is aired, dvd, absolute or unknown. Source_provider is a provider name or null, not proof of a numbering scheme.

Require the same media type and a matching ID at the same level. Conflicting IDs in a shared namespace mean skip. Prefer episode IDs. Without one, require a matching show ID and coordinates, plus matching known numbering or a confirming title when numbering is unknown. **A title cannot override a known numbering mismatch.** Compare titles after NFC normalization, case folding and collapsing whitespace; no fuzzy/title-only matches. Skip competing matches; confirmed duplicate files can all receive the result.

### Applying history and ratings

- On unwatched items, write reliable play_count or 1 when null. Don't treat merged provider observations as exact counts. Already-watched items stay unchanged.
- Keep undated watches in the export; the profile timeline currently drops them. Use Kodi's dateadded locally if the watch date is null. If both are missing, skip and explain. Never report the fallback as a real watch date.
- **Apply watched state even when a resume point exists.** Proposed local behaviour is to leave the resume value alone while updating watched state; don't skip the item. Confirm Kodi's resulting behaviour in testing. Whether to clear the resume value instead is still a choice to agree, not a reason to defer support.
- For shared items, default to all owners having watched. Use the lowest count, or 1 if any count is unknown. Proposed any-owner mode uses count 1. Use the latest known qualifying owner's watch date, otherwise dateadded. These shared count/date choices still need agreement.
- Fill only empty ratings. Every owner of a shared item needs a usable rating and they must agree. Missing means unknown.

## Paging, changes and caches

A snapshot is an immutable paged download valid for 30 minutes, unless mappings/authorization change or CW restarts. Request subsequent pages with `?cursor=<opaque cursor>` and no other parameters. The cursor binds instance, viewer, feature, mapping, snapshot and page size. Pages repeat the same metadata, without duplicate keys or gaps. Null next_cursor finishes the download.

Retrying a cursor returns the same page. Initial retries reuse equivalent unexpired snapshots rather than accumulating abandoned downloads. If data changed, the retry may start a new snapshot. Download all pages before applying. Paging expiry doesn't interrupt an already downloaded local application. Shared-viewer decisions require the same current mapping revision.

Content tokens track exported rows and mappings, independently of paging expiry or normal CW restarts. Refresh timestamps/availability alone don't change them. Check with `?viewer_id=kv-anna&feature=history&known_content=c-h41`:

```json
{
  "version": 1,
  "viewer_id": "kv-anna",
  "feature": "history",
  "mapping_revision": "m-12",
  "content_token": "c-h41",
  "unchanged": true,
  "availability": {"status": "ready", "sources": []}
}
```

Here sources is shortened for illustration; actual replies include current source status/refresh entries. Changed content or an unknown token returns a first page. Send known_content only when the complete matching rows still exist locally. No ETag/304 in v1; successful reads use HTTP 200. Newly scanned Kodi items still need matching when content is unchanged.

Availability is ready (completed imports), partial (usable but incomplete) or unavailable (nothing usable now). Source status also allows error; refresh times can be null. A scrobble-only account may have little imported history.

Keep good cached rows during outages, but don't apply unavailable data or use old rows to fill gaps in current household evidence. Partial results can supply positive evidence without erasing retained good data. A complete ready result can replace the cache. Storage layout is up to the add-on.

Unavailable full responses have items [], next_cursor null and content_token null; don't overwrite saved rows/token with them. Always process availability on unchanged replies. Missing rows never mean unwatch or clear rating. Discard affected caches on mapping/permission changes; don't reuse them across CW connections or paired instances.

## Manual watched/unwatched — included in v1

The add-on reports owned library watched-state changes to CW without inventing playback sessions. It must suppress its own imports and avoid reporting the same playback through both playback events and library notifications. Another add-on can change the same field, so notifications alone don't prove a human action; show this limitation in setup.

CW needs an explicit viewer-scoped manual-state operation. It must authorize the destination, handle retries without duplicate watches, and report success/failure per destination. The existing playback route or read permission mustn't silently authorize deleting history.

**The manual-write wire format isn't fixed yet. Before implementing it, agree:**

- The request/reply, capability flag and explicit write permission, including stable event IDs, viewer ID, media identity, action and timestamp.
- What unwatched removes: the latest watch, all watches, or a provider's aggregate watched flag. Don't silently pick one across providers.
- How queued manual changes win over stale pulls. A pending unwatch mustn't immediately get re-marked watched by an old download. Suppression must last until CW confirms the effective state, not just receipt of a request.
- How failed/unsupported destinations and conflicting source histories are presented. Don't claim success or release suppression while stale data can undo the action.

These are remaining contract decisions within v1, not deferred features. Native Kodi rating changes can reuse rate only if detection and viewer attribution work; confirm that separately.

## Errors and finishing up

Read errors use:

```json
{"ok": false, "error": {"code": "viewer_unmapped", "message": "No read source configured.", "retryable": false}}
```

| HTTP | Code | Action |
| --- | --- | --- |
| 400 | invalid_request | Fix request; don't retry unchanged. |
| 401 | invalid_token | Stop imports/reads and clear this connection's read caches. |
| 403 | read_disabled / viewer_forbidden | Stop affected imports; clear affected caches and refresh permissions/mappings. |
| 409 | viewer_unmapped / viewer_ambiguous / mapping_changed | Discard affected state and resolve/refresh mappings. |
| 410 | snapshot_expired | Restart download; includes cursors from before CW restart. |
| 429 | rate_limited | Retry after Retry-After. |
| 503 | temporarily_unavailable | Retry with backoff. |

Only 429/503 above have retryable true. Network failures and other 5xx responses also get bounded backoff. Unknown viewers return viewer_forbidden; malformed cursors return invalid_request. Check authorization before serving cached pages. Never interpret errors as empty libraries. Existing webhook error behaviour stays unchanged.

Make local application resumable, with fresh activation/mapping/availability checks after reconnect. Keep records of imported writes so library notifications can't feed them back to CW.

Next: settle shared-source handling, activation details and the manual-write contract, then build preview and both sync directions. The add-on copy can live at docs/contract-sync.md, excluded from published docs while still a draft.
