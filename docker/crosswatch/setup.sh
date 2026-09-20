#!/usr/bin/env bash
# Bring up a throwaway CrossWatch and point its Kodi provider at the local test Kodi.
#
# Idempotent: safe to re-run, and it re-checks rather than assuming, so it doubles as a way
# to verify an instance someone else started.
#
#   setup.sh            start, configure, verify
#   setup.sh --verify   verify only, change nothing
#
# It refuses to touch an instance that has a real provider connected, so it cannot be
# pointed at a production CrossWatch by accident.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="$HERE/docker-compose.yml"
BASE="http://127.0.0.1:${CROSSWATCH_TEST_PORT:-8787}"
CREDS="$HERE/.credentials"
COOKIES="$(mktemp)"
trap 'rm -f "$COOKIES"' EXIT

# The Kodi the add-on is installed into, reached from inside the container.
KODI_PORT="${KODI_JSONRPC_PORT:-8081}"
VERIFY_ONLY=0
[ "${1:-}" = "--verify" ] && VERIFY_ONLY=1

say() { printf '  %-40s %s\n' "$1" "$2"; }

# Origin is not optional. CrossWatch rejects a cookie-authenticated API call whose Origin
# does not match, and answers "Origin mismatch" rather than anything about the request.
api() { curl -s -m 15 -b "$COOKIES" -c "$COOKIES" -H "Origin: $BASE" -H "Referer: $BASE/" "$@"; }

gateway() {
    # Whatever address the container can reach the host on. Not host.docker.internal:
    # that does not exist on stock Linux docker.
    docker exec crosswatch-test python3 -c "
import socket, urllib.request
for host in ('host.docker.internal', '172.17.0.1', '172.18.0.1'):
    try:
        socket.gethostbyname(host)
    except OSError:
        continue
    try:
        req = urllib.request.Request('http://%s:${KODI_PORT}/jsonrpc' % host,
            data=b'{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"JSONRPC.Ping\"}',
            headers={'Content-Type': 'application/json'})
        if b'pong' in urllib.request.urlopen(req, timeout=3).read():
            print(host)
            break
    except Exception:
        continue
" 2>/dev/null
}

[ "$VERIFY_ONLY" -eq 1 ] || docker compose -f "$COMPOSE" up -d >/dev/null

for _ in $(seq 1 60); do
    curl -s -m 2 -o /dev/null "$BASE/api/health" 2>/dev/null && break
    sleep 2
done
curl -s -m 5 -o /dev/null "$BASE/api/health" || { echo "CrossWatch did not come up at $BASE" >&2; exit 1; }
say "crosswatch" "up at $BASE"

# The guard that makes this safe to run without reading it first: never configure anything
# that already talks to a real service.
LIVE=$(curl -s -m 5 "$BASE/api/status" | python3 -c "
import json, sys
d = json.load(sys.stdin)
skip = {'crosswatch_connected', 'kodi_connected'}
print(','.join(sorted(k for k, v in d.items() if k.endswith('_connected') and v and k not in skip)))
")
if [ -n "$LIVE" ]; then
    echo "refusing to continue: real providers are connected here ($LIVE)." >&2
    echo "this is not a throwaway instance. check what you are pointed at." >&2
    exit 1
fi
say "providers connected" "none (safe to use)"

# A random password, kept out of git. The instance is loopback-only and disposable, but a
# committed credential is a habit worth not forming.
if [ ! -f "$CREDS" ] && [ "$VERIFY_ONLY" -eq 1 ]; then
    echo "no credentials at $CREDS, so this instance was not set up by this script" >&2
    exit 1
fi
if [ ! -f "$CREDS" ]; then
    printf 'CW_USER=tester\nCW_PASS=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')" > "$CREDS"
    chmod 600 "$CREDS"
fi
# shellcheck disable=SC1090
. "$CREDS"

SETUP_NEEDED=$(curl -s -m 5 "$BASE/api/config" | python3 -c "
import json, sys
try:
    print('yes' if json.load(sys.stdin).get('error') == 'Authentication setup required' else 'no')
except Exception:
    print('no')
")
if [ "$SETUP_NEEDED" = "yes" ]; then
    api -X POST -H 'Content-Type: application/json' \
        -d "{\"enabled\":true,\"username\":\"$CW_USER\",\"password\":\"$CW_PASS\"}" \
        "$BASE/api/app-auth/credentials" >/dev/null
    say "first-run auth" "created"
else
    say "first-run auth" "already done"
fi

LOGIN=$(api -X POST -H 'Content-Type: application/json' \
    -d "{\"username\":\"$CW_USER\",\"password\":\"$CW_PASS\"}" "$BASE/api/app-auth/login")
echo "$LOGIN" | grep -q '"ok":true' || { echo "login failed: $LOGIN" >&2; exit 1; }
say "login" "ok"

# Checked after logging in, deliberately. /api/kodi/status answers Unauthorized without a
# session, and reading that as "not connected" reports a working rig as broken.
if [ "$VERIFY_ONLY" -eq 1 ]; then
    STATUS=$(api "$BASE/api/kodi/status")
    if echo "$STATUS" | grep -q '"connected":true'; then
        say "kodi provider" "connected: $(echo "$STATUS" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("server"))')"
        exit 0
    fi
    say "kodi provider" "NOT connected"
    echo "$STATUS" >&2
    exit 1
fi

GW="$(gateway)"
[ -n "$GW" ] || { echo "the container cannot reach Kodi on :$KODI_PORT. Is Kodi running with its web server enabled?" >&2; exit 1; }
say "kodi reachable from container" "$GW:$KODI_PORT"

# /api/kodi/connect, not a write to kodi.server through /api/config. Only this performs the
# JSON-RPC handshake and records kodi_version, jsonrpc_version and connection_verified.
# Writing the address directly stores it and leaves the provider unverified, so the watcher
# never starts and nothing tells you why.
CONNECT=$(api -X POST -H 'Content-Type: application/json' \
    -d "{\"server\":\"http://$GW:$KODI_PORT\",\"username\":\"\",\"password\":\"\",\"verify_ssl\":false}" \
    "$BASE/api/kodi/connect")
echo "$CONNECT" | grep -q '"ok":true' || { echo "kodi connect failed: $CONNECT" >&2; exit 1; }
say "kodi connected" "$(echo "$CONNECT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("Kodi %s, JSON-RPC %s" % (d.get("kodi_version"), d.get("jsonrpc_version")))')"

STATUS=$(api "$BASE/api/kodi/status")
echo "$STATUS" | grep -q '"connected":true' || { echo "kodi reports not connected: $STATUS" >&2; exit 1; }
say "kodi server" "$(echo "$STATUS" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("server"))')"

echo
echo "  UI: $BASE   user: $CW_USER   password: see $CREDS"
