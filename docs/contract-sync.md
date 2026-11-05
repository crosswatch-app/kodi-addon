# Kodi add-on pull sync contract

Related issue: [kodi-addon #72](https://github.com/crosswatch-app/kodi-addon/issues/72)

This folds in the add-on review. Pairing, playback events and outgoing rating events stay as they are. The examples below describe the new pull API, not endpoints already available in CW.

## Who does what
Kodi starts the connection, on startup and at an interval. CW doesn't connect to Kodi for this feature. The add-on can use Kodi's local APIs without enabling its HTTP server.
CW handles permissions, viewer mappings, exported data and conflicts with direct Kodi history/ratings pairs. The add-on handles preview, scheduling, ownership, matching and local writes. Downloads must not hold up playback reporting.
Start with a preview. V1 only changes unwatched items and empty ratings. Already-watched items stay as they are. No unwatch or rating removal comes down through this API. Imported changes must not become outgoing playback or rating events.

## 1. Connection and permissions

Every read uses the existing paired token:

```http
X-CrossWatch-Token: <paired instance token>
```

The new endpoints reject `?token=` with `400 invalid_request`. Existing webhook token handling stays unchanged. These reads use the instance token, not a browser login session, and must be reachable through the webhook authentication path.
Read access is a separate opt-in in CW. The enable screen lists the viewers and source accounts being exposed. A scrobble route alone doesn't grant permission to read its account's full history. Check authorization on every request, including every page.
Both UIs should explain that an `http://` address sends the token and history without transport encryption. HTTP remains supported.

### Discovery

Add this block to the existing ping reply:

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

`enabled` means reads are allowed. `active` confirms write mode, described below. Preview only needs enabled.

Include the block even when an authenticated ping returns `ignored: true`. The add-on reads it before deciding to ignore the rest of that response. It doesn't make a rejected playback event valid.
A missing block means unsupported. Don't fetch when disabled or when the version isn't supported. This version is separate from the existing event version.

### URLs and proxy prefixes

The default endpoints are:

```text
/webhook/kodiwatcher/sync/v1/viewers
/webhook/kodiwatcher/sync/v1/state
```

Advertise paths relative to the saved webhook endpoint, without a leading slash. Take the add-on's saved, reachable webhook URL, remove any query/fragment, add a trailing slash, then resolve the advertised path against it. Don't replace the origin or drop a proxy prefix.

```text
Saved webhook: https://example.com/cw/webhook/kodiwatcher
Base:          https://example.com/cw/webhook/kodiwatcher/
Advertised:    sync/v1/viewers
Resolved:      https://example.com/cw/webhook/kodiwatcher/sync/v1/viewers
```

Never forward the token to another origin, including through redirects.

## 2. Preview and activation

Read permission doesn't stop polling or change pairs. Someone can preview while their existing setup keeps working.
When the user enables local writes, add this to the normal ping request:

```json
{
  "library_sync": {
    "version": 1,
    "active": true
  }
}
```

This is an extra field, not a replacement for the existing ping payload. The add-on waits for `library_sync.active: true` in the reply before writing. Repeating the request is safe.
CW checks direct history/ratings pairs involving this Kodi in both directions, including two-way pairs. Refuse activation while a conflicting pair is enabled; don't silently disable it. Collection-only and progress-only pairs aren't blocked by this rule.

The reply's reason is null when there's no blocker, otherwise:

| Reason | Meaning |
| --- | --- |
| `read_disabled` | Enable read access first. |
| `pair_conflict` | Disable a conflicting direct history/ratings pair first. |
| `no_ready_viewers` | There isn't an authorized viewer with a usable mapping. |

Report blockers during preview too, even without an activation request. Only `active: true` confirms activation. The UI can inspect existing CW pairs to resolve a pair conflict; the add-on mustn't guess which pair to disable.
CW persists accepted client-only mode across restarts and heartbeat expiry. While selected, there is no remote watcher polling fallback. Kodi being off doesn't change that. CW must also prevent conflicting pairs from being enabled while the mode is selected.
To turn it off, the add-on stops writing immediately and sends `active: false`. CW clears the selection. A ping without this field leaves it alone. CW also offers an explicit off switch if the device is gone. Missing heartbeats never turn it off.
Revoking read permission or the token stops writes but must not silently restore polling. CW retains the client-only selection until explicitly turned off; the reply still reports active false while reads are forbidden. Switching modes never automatically re-enables disabled pairs.

After startup, reconnect, or an authentication/mapping failure, get fresh activation confirmation before applying data.

## 3. Viewer mapping

```http
GET /webhook/kodiwatcher/sync/v1/viewers
```

Example:

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
    },
    {
      "viewer_id": "kv-tom",
      "person_id": null,
      "name": "Tom",
      "default": false,
      "status": "unmapped",
      "reason": "empty_scope"
    }
  ]
}
```

- `viewer_id` is an opaque, persistent ID scoped to this Kodi instance. Renaming or linking a viewer doesn't change it.
- `person_id` is the linked CW profile ID or null. It can change independently.
- Use existing person links to establish the association, then keep viewer_id. For unlinked viewers, keep a confirmed association; don't guess between similar names.
- Outgoing events still use names. Their routing doesn't change.
- `status` is `ready`, `unmapped` or `ambiguous`. Only ready viewers can be read.
- `reason` is null for ready, `empty_scope` for unmapped, or `multiple_matches`, `shared_source` or `unverified_source_identity` for ambiguous.
- `mapping_revision` is instance-wide. It changes when viewer associations, defaults or authorized source scopes change.

**A read scope must resolve to at least one explicitly authorized account. Empty scope means unmapped, never everyone.** Don't pass an empty filter into the profile timeline helpers. A null person_id is fine only with an explicit account scope.

This list stays separate from the existing people list. Ready here means the mapping is resolved, not that its history has been imported.

### Two viewers sharing an account

One account isn't independent evidence for two people. If two separate viewer scopes overlap on the same underlying provider account, mark both ambiguous with shared_source. Compare actual account identities, not instance labels. If CW can't establish account identity safely, use unverified_source_identity rather than claim independence.
The user can choose separate accounts or explicitly configure one shared viewer. A shared viewer represents that account; it doesn't prove every household member watched. V1 doesn't infer individual watches from a shared account.

### One person without a profile or whitelist

When there are no add-on viewers and CW can resolve one unambiguous authorized account scope, return one ready viewer with `default: true` and `person_id: null`. The CW enable screen still confirms the account being exposed.
There is at most one default viewer. The add-on uses it automatically only when it has no local viewers configured. This doesn't change outgoing events with empty viewers. If the scope is unclear, return an unmapped or ambiguous entry and ask for setup.

## 4. Reading state

Read history and ratings separately:

```http
GET /webhook/kodiwatcher/sync/v1/state?viewer_id=kv-anna&feature=history&limit=500
```

Feature is history or ratings. Limit is an integer from 1 to 1000, default 500.

Example history page. IDs in this example are illustrative:

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
        "ids": {
          "tmdb": "1234567",
          "tmdb_show": "63639",
          "tvdb_show": "280619"
        },
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
      "last_watched_at": "2026-11-04T20:52:40Z"
    }
  ],
  "next_cursor": null
}
```

### Media and matching

V1 supports movie and episode. Keep the existing flat ID keys with string values. tmdb, tvdb and imdb identify the item; the corresponding _show keys identify its parent show. Include every usable episode ID CW knows, not just show IDs. Omit unknown IDs and placeholders.
Title and episode_title are strings or null; movies omit episode_title. Episodes have integer season and episode when known, otherwise null; season zero is valid. Movies omit coordinates and numbering.
For episodes, numbering.scheme is tmdb, tvdb, imdb or unknown. Order is aired, dvd, absolute or unknown. Source_provider is the uppercase provider supplying the coordinates, or null. A source provider doesn't prove a numbering scheme. Use unknown when CW can't establish it; don't attach one source's numbers to another scheme.

Matching rules:
1. Require the same media type and at least one matching external ID. Compare only the same namespace and level. A show ID is never an episode ID.
2. If an ID present on both sides disagrees, skip, even when another ID agrees.
3. Prefer matching episode IDs. Different numbering alone doesn't override an otherwise unambiguous episode-ID match.
4. Without a matching episode ID, require a matching show ID and matching season/episode coordinates. Also require either the same known numbering scheme and order, or a confirming episode title.
5. Title confirmation requires two nonempty titles equal after Unicode NFC normalization, case folding and collapsing whitespace. No fuzzy matching. Title alone never identifies an episode.
6. Skip competing episode candidates. Confirmed local copies of the same episode can all receive the result, subject to each copy's local watched/resume state.

Key is an opaque CW deduplication key, stable while CW's item identity stays the same. Don't parse it for matching. Omit rows without usable identity. Ratings use these same media fields and rules.

### History and counts

History rows describe known watched items: watched is true. Play_count is a reliable positive integer or null. Don't turn merged observations from several providers into an exact count. Last_watched_at is UTC RFC 3339 or null.
On an unwatched Kodi item, write the count when known, otherwise 1. Leave already-watched items alone.
For shared items, default to all owners having positive watched evidence. Use the lowest count, or 1 if any owner's count is null. An optional any-owner mode may use positive evidence from one owner; use count 1 in that mode for v1. Use the latest known watch date among the owners whose evidence qualifies the item. If none have a date, use the fallback below.
Keep undated watches in the export. The existing profile timeline drops them, so don't reuse that output unchanged. For an undated item, use Kodi's dateadded locally; if that is missing too, skip and show why. Never send this fallback back as a real watch date.
If a local item has a nonzero resume point, skip the watched-state write and list it separately in preview. Don't clear the resume point. Reconsider when it's gone. This doesn't prevent filling an empty rating.

### Ratings

Use the same envelope with feature ratings. An item looks like this:

```json
{
  "key": "item-movie-550",
  "media": {
    "type": "movie",
    "title": "Fight Club",
    "ids": {
      "tmdb": "550",
      "imdb": "tt0137523"
    }
  },
  "rating_status": "rated",
  "rating": 8,
  "rated_at": null
}
```

Rating_status is rated or conflict. Rated rows have integer ratings from 1 to 10. Rated_at is UTC RFC 3339 or null. If the viewer's mapped sources disagree, return conflict with null rating and date; skip it. This API doesn't return zero/removal ratings.
Only fill empty Kodi ratings. For a shared item, every owner needs a usable rating and all scores must agree. Missing means unknown, not agreement. If a selected source is missing and could hide a conflicting score, CW must not present the remaining score as resolved; omit that rating until resolved.

## 5. Availability and caches

Every full or unchanged reply includes availability:

| Status | Meaning |
| --- | --- |
| ready | All selected sources have a usable completed import for this feature. |
| partial | Some data is usable, but coverage is incomplete or uncertain. |
| unavailable | No usable state is available for this read right now. |

Per-source status is ready, partial, unavailable or error. Refresh times can be null. Ready describes imported coverage, not someone's lifetime history. A scrobble-only setup may have little to export.

Missing rows never mean unwatch or clear rating. Handle caches like this:

- A fully downloaded ready response replaces the previous ready cache, even if empty. It still doesn't remove anything in Kodi.
- Keep a fully downloaded partial response separately. Its positive rows may be used, but it doesn't erase the last ready cache. Don't fill holes in current evidence with older cached rows.
- Unavailable returns no usable rows. Keep previous caches, but don't apply them while unavailable. Never replace good cached data with an unavailable empty list.
- Authentication, permission and mapping changes discard affected caches regardless of availability. A 401 clears this connection's read caches; viewer-specific rejection clears that viewer's data.

Only the currently validated ready/partial response supplies evidence for shared-item decisions. Old rows retained during an outage don't prove that a missing owner watched or agreed on a rating.

## 6. Paging and unchanged checks

There are two tokens:

- Snapshot_id identifies an immutable paged download. It lasts 30 minutes from creation unless permissions/mappings change or CW restarts.
- Content_token identifies the exported rows and mapping for this instance/viewer/feature. It survives paging expiry and normal CW restarts. Refresh timestamps and availability metadata alone don't change it.

CW computes or persists content identity independently of the paging snapshot. Losing a snapshot must not force a download if content is unchanged. Changed mappings or exported rows change the content token. Tokens are opaque and don't grant access.

Fetch another page with:

```http
GET /webhook/kodiwatcher/sync/v1/state?cursor=<opaque cursor>
```

The cursor binds instance, viewer, feature, mapping, snapshot and page size. Send no other query parameters with it. Every page repeats the same snapshot metadata. Ordering is deterministic, without gaps or duplicate keys. Next_cursor null finishes the download.
Repeating a cursor returns the same page while valid. Retrying the initial request is safe: reuse an unexpired equivalent snapshot for the same instance, viewer, feature, mapping, page size and exported state/metadata. Lost replies mustn't create an unbounded pile of snapshots. If the underlying data changed, a retry can return a new snapshot; only follow cursors from the response actually received.
Download all pages before applying that feature. Paging expiry doesn't interrupt local application of a completed download. When combining viewers, require the same current instance-wide mapping revision.

For later checks:

```http
GET /webhook/kodiwatcher/sync/v1/state?viewer_id=kv-anna&feature=history&limit=500&known_content=c-h41
```

If rows and mapping are unchanged:

```json
{
  "version": 1,
  "viewer_id": "kv-anna",
  "feature": "history",
  "mapping_revision": "m-12",
  "content_token": "c-h41",
  "unchanged": true,
  "availability": {
    "status": "ready",
    "sources": [
      {
        "provider": "TRAKT",
        "instance": "anna",
        "status": "ready",
        "last_successful_refresh_at": "2026-11-05T13:00:00Z"
      }
    ]
  }
}
```

Otherwise return a fresh first page. Unknown content tokens also get a first page. Known_content replaces known_snapshot in this revision. An unchanged reply doesn't need a snapshot ID or cursor.
Only send a content token when the add-on still has all rows downloaded under it. Keep current partial rows separate from retained ready rows and use the token belonging to the rows being checked. If local data is missing, fetch without a token. Always process current availability, even on unchanged replies; unchanged doesn't authorize using unavailable data.
An unavailable full response has items [], next_cursor null and content_token null. It doesn't replace saved rows or their token. CW mustn't return unchanged unless it can establish the requested content identity.
Successful reads use HTTP 200. V1 doesn't use ETag/304. Newly scanned Kodi items still need matching against the validated local download even when CW returns unchanged.
Don't reuse caches across CW connections, paired instances or changed mappings. Discard affected caches on a mapping change; discarding all instance caches is fine if the add-on can't identify affected viewers.

## 7. Errors

New reads use this shape:

```json
{
  "ok": false,
  "error": {
    "code": "viewer_unmapped",
    "message": "This viewer has no configured read source.",
    "retryable": false
  }
}
```

Use code in code; message is for people. Retryable means the same request can be tried later. Other errors may need recovery first.

| HTTP | Code | Retryable | What the add-on does |
| --- | --- | --- | --- |
| 400 | invalid_request | false | Fix the request; don't repeat it unchanged. |
| 401 | invalid_token | false | Stop pulls/writes and delete this connection's read caches. Wait for credentials to change. |
| 403 | read_disabled | false | Stop pulls/writes, delete read caches and recheck through ping. |
| 403 | viewer_forbidden | false | Delete that viewer's cache and refresh mappings. |
| 409 | viewer_unmapped / viewer_ambiguous | false | Discard that viewer's pending/cached state; skip until configured. |
| 409 | mapping_changed | false | Discard affected caches/downloads, refresh mappings and restart. |
| 410 | snapshot_expired | false | Restart from the first page. |
| 429 | rate_limited | true | Honour Retry-After. |
| 503 | temporarily_unavailable | true | Retry with bounded backoff. |

Unknown/unauthorized viewers return viewer_forbidden. Malformed cursors return invalid_request. Well-formed cursors from before a restart, expired snapshots or missing snapshots return snapshot_expired. Check current authorization before serving any snapshot data.

Network failures and other 5xx responses also get bounded backoff. Never treat an error, login page or malformed payload as an empty library. Stop on unknown non-retryable errors and show the problem. Existing webhook status codes and behaviour stay unchanged.

## 8. Applying the download

Local application can take a while, especially on MariaDB. Make it interruptible and resumable. Don't block Kodi's callback loop.
Before each write, recheck the local watched count, rating and resume point as appropriate. Pause writes when playback starts. Applied rows are safe to revisit because watched items and nonempty ratings are left alone. After restart/reconnect, revalidate activation, mappings and current availability before resuming saved work.
Imported writes stay out of the outgoing queue. The current add-on reports playback and its rating window, so local library writes don't currently trigger those events. If library-change monitoring is added later, it needs explicit suppression of the add-on's own writes.

## 9. After v1

Manual watched/unwatched changes and ratings made through Kodi's own UI aren't exported by this pull feature. Changes from other add-ons aren't either. With direct pairs disabled, this is a known gap; explain it during setup.
Add manual-change reporting after watched sync works. It needs its own contract for ownership, suppression of imported writes and what unwatch means at each destination. Removing one watch and clearing all history aren't the same thing. Don't turn a manual mark into a fake playback stop. The existing rate event may be reusable after Kodi rating-change detection has been checked.

Agree this draft first, then build the API and preview, followed by watched writes, ratings and the interval. In the add-on repo, keep it as docs/contract-sync.md and exclude it from published docs while it's still a proposal.
