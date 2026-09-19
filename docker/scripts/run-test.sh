#!/usr/bin/env bash
# Inspect the running addon.
#
#   run-test.sh --log              follow the addon log
#   run-test.sh --kodi-log         addon entries in kodi.log
#   run-test.sh --dropped          how often Kodi discarded queued playback messages
#   run-test.sh --rpc <method> [params-json]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADDON_SRC="$(cd "$SCRIPT_DIR/../.." && pwd)"
ADDON_ID="$(python3 "$SCRIPT_DIR/addon_meta.py" id "$ADDON_SRC")"
LOG_NAME="$(python3 "$SCRIPT_DIR/addon_meta.py" log_name "$ADDON_SRC")"

NATIVE_DATA="$HOME/.var/app/tv.kodi.Kodi/data"
ADDON_LOG="$NATIVE_DATA/userdata/addon_data/$ADDON_ID/logs/$LOG_NAME.log"
KODI_LOG="$NATIVE_DATA/temp/kodi.log"
RPC_URL="${RPC_URL:-http://localhost:8080/jsonrpc}"

case "${1:---log}" in
    --log)      tail -n 100 -f "$ADDON_LOG" ;;
    --kodi-log) grep -i "$LOG_NAME" "$KODI_LOG" | tail -n 100 ;;
    --dropped)  grep -c "Ignored .* playback thread messages" "$KODI_LOG" || echo 0 ;;
    --rpc)
        method="${2:?method required}"
        params="${3:-{\}}"
        curl -s -X POST -H 'Content-Type: application/json' \
            -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$method\",\"params\":$params}" \
            "$RPC_URL" | python3 -m json.tool
        ;;
    *) echo "usage: run-test.sh [--log|--kodi-log|--dropped|--rpc <method> [params]]" >&2; exit 2 ;;
esac
