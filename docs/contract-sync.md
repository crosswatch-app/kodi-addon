# Kodi add-on sync contract
**Working draft for v1 — not implemented or agreed yet.** Updated 2026-11-07 updated by your best friend :-)
Related: [kodi-addon #72](https://github.com/crosswatch-app/kodi-addon/issues/72)

## What we're building

The add-on starts every connection. It pulls history and ratings on startup and at an interval, then updates Kodi locally. CW doesn't need access to Kodi's HTTP server. Existing pairing and playback/rating events stay unchanged.
V1 includes manual watched/unwatched changes from Kodi to CW. A resume point must not block a watched-state update from CW.
CW handles permissions, viewer scopes and exported state. The add-on handles ownership, matching, preview and local writes. Start with an opt-in preview. Keep downloads and long library updates out of the playback reporting loop, pause writes during playback, and recheck each item before writing.

## Connection and activation

Use `X-CrossWatch-Token` with the existing paired token. New sync endpoints reject `?token=` with `400 invalid_request`; they don't require browser login. CW enables read access separately and shows which viewers/accounts it exposes. A scrobble route alone isn't permission to export full history. Check authorization on every request.
Add this to authenticated ping replies, including replies with `ignored: true`:
```json
{
  "library_sync": {
    "version": 1,
    "enabled": true,
    "active": false,
    "reason": null,
    "features": ["history", "ratings"],
    "viewers_url": "sync/v1/viewers",
    "state_url": "sync/v1/state",
    "changes_url": "sync/v1/changes",
    "manual_watched": true,
    "manual_unwatched": false
  }
}
```
Missing block or unsupported version means don't use this API. Read the block before discarding an ignored reply. `features` lists pull features. Manual-write flags mean supported and permitted for this instance; read permission alone doesn't allow writes.
Resolve advertised paths against the saved webhook URL with its query/fragment removed and a trailing slash added:
```text
https://example.com/cw/webhook/kodiwatcher/
+ sync/v1/state
= https://example.com/cw/webhook/kodiwatcher/sync/v1/state
```
Use the same rule for status URLs. Keep proxy prefixes. Never send the token to another origin, including through redirects. HTTP stays supported; both enable screens should mention that it sends history and tokens unencrypted.
Preview needs only `enabled: true` and mustn't change polling or pairs. To request actual sync, add `"library_sync": {"version": 1, "active": true}` to the normal ping. Wait for `active: true` in the reply before applying imports. Repeating this is safe.

- CW refuses conflicting direct history/ratings pairs in either direction rather than silently disabling them. Progress pairs that affect watched/resume state also need checking; they aren't automatically exempt.
- `reason` is null, `read_disabled`, `pair_conflict` or `no_ready_viewers`. Report blockers in preview too.
- Accepted client-only mode persists across restarts and heartbeat expiry. Kodi being off must never restore remote watcher polling. Prevent conflicting pairs from being enabled while this mode is selected.
- The add-on stops importing immediately when turned off and sends `active: false`. An omitted field leaves the selection unchanged. CW also has an explicit off switch.
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
      "reason": null,
      "excluded_sources": [
        {
          "provider": "PLEX",
          "instance": "household",
          "reason": "shared_source"
        }
      ]
    }
  ]
}
```
`viewer_id` is stable within this Kodi instance, even after renaming/linking. `person_id` is the linked profile or null. Use existing person links or a confirmed local association; don't guess between names. Outgoing playback events still use names. This doesn't replace the existing `people` list.
Status is `ready`, `unmapped` or `ambiguous`. Reasons are null, `empty_scope`, `multiple_matches`, `shared_source` or `unverified_source_identity`. Only ready viewers can be read. Mapping readiness doesn't imply imported history is available.
Every read must have a nonempty, explicitly authorized account scope. Empty means unmapped, never everyone. A null person_id needs an explicit account mapping. `mapping_revision` is instance-wide and changes with associations, defaults or authorized scopes.
For one person without a profile, whitelist or local viewers, offer one `default: true` viewer when CW can resolve an unambiguous authorized scope. Confirm its accounts in CW. Only use that default automatically when no local viewers exist.
Exclude shared source accounts from individual viewer scopes and keep each viewer's independent sources. Compare actual provider account identities, not instance labels. If exclusions leave no sources, return ambiguous with shared_source and suggest configuring one shared viewer.
Return exclusions in `excluded_sources`, or an empty array when there are none. Show them in the preview: watches found only there won't count. Excluded sources must not become manual-write destinations for individual viewers merely because they appear in existing routes.

## State payloads

`GET /webhook/kodiwatcher/sync/v1/state?viewer_id=kv-anna&feature=history&limit=500`
Feature is `history` or `ratings`. Limit is 1–1000, default 500. Example history page; IDs are illustrative:
```json
{
  "version": 1,
  "viewer_id": "kv-anna",
  "feature": "history",
  "mapping_revision": "m-12",
  "state_revision": 42,
  "content_token": "c-h41",
  "snapshot_id": "s-902",
  "generated_at": "2026-11-07T12:00:00Z",
  "availability": {
    "status": "ready",
    "sources": [
      {
        "provider": "TRAKT",
        "instance": "anna",
        "status": "ready",
        "last_successful_refresh_at": "2026-11-07T11:55:00Z"
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
        "numbering": {
          "scheme": "tmdb",
          "order": "aired",
          "source_provider": "TRAKT"
        }
      },
      "watched": true,
      "play_count": 2,
      "last_watched_at": "2026-11-06T20:52:40Z"
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
Ratings are integers 1–10. Conflicting source ratings use `rating_status: "conflict"` with null rating/date and are skipped. Don't resolve a rating while a missing selected source could hide a conflict. All exported dates are UTC RFC 3339 or null.

### Matching

Support movies and episodes. Keep flat string IDs: imdb/tmdb/tvdb for the item, and corresponding _show keys for its show. Include known episode IDs. Titles are strings or null; episode coordinates are integers or null, with season zero valid. Movies omit episode_title, coordinates and numbering. Omit unusable identities/placeholders. `key` is opaque; don't parse it.
Numbering scheme is tmdb, tvdb, imdb or unknown; order is aired, dvd, absolute or unknown. Source_provider is a provider name or null, not proof of a numbering scheme.
Require the same media type and a matching ID at the same level. Conflicting IDs in a shared namespace mean skip. Prefer episode IDs; an unambiguous episode-ID match doesn't require equal coordinates.
Without a matching episode ID, require a matching show ID and coordinates, plus matching known numbering or a confirming title when numbering is unknown. A title cannot override a known numbering mismatch. Compare titles after NFC normalization, case folding and collapsing whitespace; no fuzzy/title-only matches. Skip competing matches; confirmed duplicate files can all receive the result.

### Applying history and ratings

- On unwatched items, write reliable play_count or 1 when null. Don't treat merged provider observations as exact counts. Already-watched items stay unchanged.
- Keep undated watches in the export; the profile timeline currently drops them. Use Kodi's dateadded locally if the watch date is null. If both are missing, skip and explain. Never report the fallback as a real watch date.
- Apply watched state even when a resume point exists. Clear the resume point only when CW's watch date is strictly newer than Kodi's last-played date read before the update. Compare both in the same timezone. Keep the resume point when either date is missing, cannot be reliably interpreted, is equal, or CW's date is older. Never use dateadded for this comparison. Keep the previous resume position in the applied-change record so it can be undone.
- For shared items, default to all owners having watched. Use the lowest count, or 1 if any count is unknown. Any-owner mode uses count 1. Use the latest known qualifying owner's watch date, otherwise dateadded.
- Fill only empty ratings. Every owner of a shared item needs a usable rating and they must agree. Missing means unknown.

## Paging, changes and caches

A snapshot is an immutable paged download valid for 30 minutes, unless mappings/authorization change or CW restarts. Request subsequent pages with `?cursor=<opaque cursor>` and no other parameters. The cursor binds instance, viewer, feature, mapping, snapshot and page size. Pages repeat the same metadata, including state_revision, without duplicate keys or gaps. Null next_cursor finishes the download.
Retrying a cursor returns the same page. Initial retries reuse equivalent unexpired snapshots rather than accumulating abandoned downloads. If data changed, a retry may start a new snapshot. Download all pages before applying. Paging expiry doesn't interrupt an already downloaded local application. Shared-viewer decisions require the same current mapping revision.
Content tokens track exported rows and mappings independently of paging expiry or normal CW restarts. Refresh timestamps/availability alone don't change them. Check with `?viewer_id=kv-anna&feature=history&known_content=c-h41`:
```json
{
  "version": 1,
  "viewer_id": "kv-anna",
  "feature": "history",
  "mapping_revision": "m-12",
  "state_revision": 42,
  "content_token": "c-h41",
  "unchanged": true,
  "availability": {
    "status": "ready",
    "sources": [
      {
        "provider": "TRAKT",
        "instance": "anna",
        "status": "ready",
        "last_successful_refresh_at": "2026-11-07T13:00:00Z"
      }
    ]
  }
}
```
Changed content or an unknown token returns a first page. Send known_content only when the complete matching rows still exist locally. No ETag/304 in v1; successful reads use HTTP 200. Newly scanned Kodi items still need matching when content is unchanged.
Availability is ready (completed imports), partial (usable but incomplete) or unavailable (nothing usable now). Source status also allows error; refresh times can be null. A scrobble-only account may have little imported history.
Keep good cached rows during outages, but don't apply unavailable data or use old rows to fill gaps in current household evidence. Partial results can supply positive evidence without erasing retained good data. A complete ready result can replace the cache. Storage layout is up to the add-on.
Unavailable full responses have items [], next_cursor null and content_token null; don't overwrite saved rows/token with them. An unavailable response cannot validate cached rows or release manual-change suppression. Always process availability on unchanged replies.
Missing rows never mean unwatch or clear rating. Discard affected caches on mapping/permission changes; don't reuse them across CW connections or paired instances.

## Manual watched/unwatched — included in v1

Report manual watched-state changes for specific viewers without inventing playback sessions. One owner reports directly. For multiple owners, use the viewer prompt and report only for those selected. No answer or no owner means nothing is sent.
Suppress imported writes and avoid reporting finished playback again through library notifications. Other add-ons can produce the same notifications, so explain that these aren't guaranteed to be human actions.
Manual watched adds a watch at the time of the action. Unwatched removes all plays for the selected viewer/item at its authorized destinations, or clears the watched flag where that's all the provider stores. Unwatched reporting has its own setting, off by default, with a warning that dated history will be removed. Enabling manual watched doesn't enable unwatch.

### Request

Use `POST sync/v1/changes`, resolved against the saved webhook URL like the read endpoints. Authenticate with the existing paired token and send one request per selected viewer:
```json
{
  "version": 1,
  "event_id": "e49b7624-86c2-4c65-a4dd-c8166ebbc138",
  "viewer_id": "kv-anna",
  "mapping_revision": "m-12",
  "action": "watched",
  "occurred_at": "2026-11-07T18:30:00Z",
  "media": {
    "type": "movie",
    "ids": {"tmdb": "550"}
  }
}
```
Action is `watched` or `unwatched`. CW selects the authorized destinations; the client cannot supply arbitrary accounts. Read permission alone doesn't allow writes. CW checks the corresponding manual-write permission and current viewer mapping before accepting the operation.
Use a stable event_id for every retry of the same action. Record occurred_at when the library change is observed, not when the request is eventually delivered.

### Acceptance and results

CW stores the operation before returning HTTP 202:
```json
{
  "change_id": "chg-123",
  "status": "pending",
  "status_url": "sync/v1/changes/chg-123",
  "effective_revision": null,
  "destinations": [
    {
      "provider": "TRAKT",
      "instance": "anna",
      "status": "pending",
      "error": null
    }
  ]
}
```
Repeating the same event_id and payload returns the existing operation. Reusing it with different content returns `409 event_conflict`. Retry tracking survives restarts.
Poll status_url with the same token. Each destination reports pending, applied, failed or unsupported. Applied means the resulting state has been confirmed. CW retries temporary failures without repeating confirmed writes. If a write times out with an uncertain outcome, check the destination before retrying an addition.
Overall status is pending while work/retries remain, partial when some destinations succeeded or exported state still conflicts, failed when processing ended without success, or complete when every selected destination succeeded and CW's exported state reflects the action.
Status reads use HTTP 200. A completed reply looks like this:
```json
{
  "change_id": "chg-123",
  "status": "complete",
  "status_url": "sync/v1/changes/chg-123",
  "effective_revision": 42,
  "destinations": [
    {
      "provider": "TRAKT",
      "instance": "anna",
      "status": "applied",
      "error": null
    }
  ]
}
```
For failed or unsupported destinations, error contains a stable code and readable message:
```json
{"code": "unsupported_action", "message": "This destination cannot remove history."}
```
If all destination writes succeeded but another read source still contradicts the action, return partial with an operation-level error:
```json
{"code": "source_conflict", "message": "Another read source still reports this item as watched."}
```
Use an `error` field on the operation for this case; it is null or omitted otherwise. Don't silently delete from additional sources to force completion.

### Keeping stale pulls from undoing changes

Full and unchanged history replies include state_revision: a persistent, increasing integer scoped to the Kodi instance, viewer and mapping revision. Each snapshot keeps the revision it was created with.
When a manual change is confirmed in CW's exported state, assign its effective_revision and persist it with the completed operation. Advance the revision for that confirmation even if the exported rows happen to be identical. Content_token remains the unchanged-content check; it isn't an ordering marker.
Keep manual changes queued and suppress conflicting imports for the affected item. Release suppression only when the change is complete and a fully downloaded pull has state_revision at least effective_revision under the same mapping. An unchanged reply can also validate the complete cached rows. An unavailable reply cannot.
Process changes in order for each viewer/item. The add-on submits them in order and CW preserves that order. An earlier completion mustn't release suppression for a newer pending change. Mapping changes require review rather than redirecting queued writes.
Failed or partial operations stay visible and suppressed until resolved or explicitly dismissed. Receipt of a request isn't confirmation of its effective state. Dismissing suppression allows normal pulls to apply again, so show that consequence.

### Kodi's own ratings — awaiting add-on feedback

Compare local ratings on startup and during sync, using a saved baseline and records of imported writes to avoid sending our own changes back. For one owner, report directly; for shared items, ask whose rating changed. Existing ratings go through the initial preview.
Confirm scan cost, handling of skipped prompts and rating removal before fixing this part of the contract. Keep the existing rating window working as it does today.

## Errors and finishing up

Sync request errors use:
```json
{"ok": false, "error": {"code": "viewer_unmapped", "message": "No source configured.", "retryable": false}}
```

| HTTP | Code | Action |
| --- | --- | --- |
| 400 | invalid_request | Fix request; don't retry unchanged. |
| 401 | invalid_token | Stop authenticated sync work and clear this connection's read caches. Don't redirect queued writes to another connection. |
| 403 | read_disabled / viewer_forbidden | Stop affected imports; clear affected caches and refresh permissions/mappings. |
| 403 | write_disabled | Stop submitting the affected manual action until permission changes. |
| 409 | viewer_unmapped / viewer_ambiguous / mapping_changed | Discard affected read state and resolve/refresh mappings; don't redirect queued writes. |
| 409 | event_conflict | Don't retry with changed content under the same event_id. |
| 410 | snapshot_expired | Restart download; includes cursors from before CW restart. |
| 429 | rate_limited | Retry after Retry-After. |
| 503 | temporarily_unavailable | Retry with backoff. |

Only 429/503 above have retryable true. Network failures and other 5xx responses also get bounded backoff. Unknown viewers return viewer_forbidden; malformed cursors return invalid_request. Check authorization before serving cached pages or operation status. Never interpret errors as empty libraries. Existing webhook error behaviour stays unchanged.
Make local application resumable, with fresh activation/mapping/availability checks after reconnect. Keep records of imported writes so library notifications can't feed them back to CW.
Next: confirm this draft and native rating scanning, then start on the preview. The add-on copy can live at docs/contract-sync.md, excluded from published docs while still a draft.
