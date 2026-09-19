# Verification against a real Kodi

What has actually been observed on Kodi 21.3 Omega, as opposed to reasoned about. Anything
not listed as confirmed here is still an assumption.

Values that would identify a library, a share or a person are replaced: `<path>`, `<id>`,
`<name>`. This file is public.

## Environments

| Name | What | Used for |
|---|---|---|
| headless | `matthuisman/kodi-headless:Omega`, JSON-RPC on 8080 | service lifecycle, RPC response shapes |
| flatpak | `tv.kodi.Kodi` 21.3-Omega, with a GUI and a real library | settings screen, viewer dialog, playback |

Install, inspect and remove with `docker/scripts/setup.sh`, `run-test.sh` and `teardown.sh`.
Both take `--native` (flatpak) or `--docker`.

## Confirmed on the headless container

The addon was installed, Kodi found it, and the service ran and shut down cleanly.

```
CAddonMgr::FindAddons: service.crosswatch v0.1.0 installed
[service] service.starting | addon=service.crosswatch, version=0.1.0
[settings] settings.thresholds | source=defaults
[playlists] playlists.index_built | playlists=0, entries=0
[service] service.stopping | reason=abort, undelivered=0
```

That single sequence establishes several things that unit tests cannot:

- every module imports under Kodi's own Python 3.11, and `addon.xml`'s service extension
  point is valid
- `KodiRuntime` constructs against the real `xbmcaddon`, `xbmc` and `xbmcvfs`
- the structured log format reaches Kodi's log through the `xbmc.log` sink
- the addon's own debug log is created under `addon_data/.../logs/` with mode `0600`, and
  its timestamps are UTC
- the idle tick runs and completes an index build
- abort is observed, `on_abort` runs, and the reporter queue drains inside its deadline
  with nothing undelivered

No traceback, no `Error Type` and no `Error Contents` appeared in `kodi.log` across two
start/stop cycles.

### RPC response shapes

**`Profiles.GetCurrentProfile`** returns `label` at the top level of `result`, **not** nested
under a `profile` key. `Controller._profile_label` depends on this exactly.

```json
{"id": 1, "jsonrpc": "2.0",
 "result": {"label": "Master user", "lockmode": 0, "thumbnail": ""}}
```

**`Player.GetActivePlayers`** returns a bare list as its `result`, not an object. This is why
`KodiRuntime.jsonrpc` wraps a non-dict result as `{"result": ...}` and why
`MediaResolver._active_player` reads `result.get("result")`.

```json
{"id": 1, "jsonrpc": "2.0", "result": []}
```

**`Files.GetDirectory` on a playlist that does not exist** answers with a JSON-RPC error, not
an empty list, so `KodiRuntime.jsonrpc` raises and the index build is discarded rather than
being published empty.

```json
{"id": 1, "jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params."}}
```

In normal operation this path is not reached: `IndexBuilder._declared_type` reads the `.xsp`
first and already treats an unreadable file as a failure.

## Not yet verified

These need the flatpak, a real library and someone at the screen. The addon is installed
there; restart Kodi to load it.

| # | Check | Why it cannot be inferred |
|---|---|---|
| 1 | `Files.GetDirectory` on a real `.xsp` returns members with a numeric `id` and `type` of `tvshow` | the headless container has no library, so every expansion is empty |
| 2 | `Player.GetItem` on a playing episode returns `tvshowid` without a second call | needs playback of a library item |
| 3 | `getTime()` raises after stop, seen as the sampler reporting no position | needs playback |
| 4 | Index rebuild cost per playlist, and in total | the design assumes one playlist per idle tick keeps the cost off the playback path; this measurement is what confirms or refutes it |
| 5 | How often Kodi discards queued playback messages | a rising count proves the controller's liveness reconciliation is load-bearing rather than defensive |
| 6 | The settings screen and the viewer dialog | see below |

### The settings and dialog checklist

This is the cheapest check in the plan and it catches a class of defect the unit tests
structurally cannot. Two of them would ship a non-functional addon and both are visible
within seconds. `tests/test_settings_xml.py` pins the static form of each, but only the real
UI proves Kodi agrees.

1. Every category and every setting shows its **text**, not a blank row. A blank row means a
   label id is missing from `strings.po`.
2. Typing a webhook URL and reading it back gives the value, not `""`. An empty read means
   the setting was never registered, which is the `allowempty` failure.
3. The token field renders as dots, not clear text.
4. "Configure viewers and playlists" **launches**. Nothing happening means the script
   extension point or the `RunScript` form is wrong.
5. In that dialog: add a viewer, name it, tick a playlist, confirm. Reopen and check the
   playlist is still ticked, which is the preselect path. Then edit it and remove it.
6. `viewers.json` under `addon_data` contains what was entered.

### Watching one episode end to end

With a webhook base URL pointing at a local listener and any token, play an episode past the
start threshold and stop it. The addon log should show:

- `identity.resolved` or `identity.unresolved` with the inputs that produced it, and **no
  names at INFO**
- `prompt.suppressed` with a reason code, or `prompt.answered`
- `reporter.posted` or `reporter.rejected`, with the URL redacted and no token anywhere

Save the captured JSON under `tests/fixtures/` with paths replaced, and add a regression test
asserting `build_payload` still produces it.
