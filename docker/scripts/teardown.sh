#!/usr/bin/env bash
# Remove the addon from a test environment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADDON_SRC="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONTAINER="${CONTAINER:-kodi-test}"
MODE="${1:---native}"
ADDON_ID="$(python3 "$SCRIPT_DIR/addon_meta.py" id "$ADDON_SRC")"
[ -n "$ADDON_ID" ] || { echo "refusing to remove without an addon id" >&2; exit 1; }

case "$MODE" in
    --native) rm -rf "$HOME/.var/app/tv.kodi.Kodi/data/addons/$ADDON_ID" ;;
    --docker) docker exec "$CONTAINER" rm -rf "/config/.kodi/addons/$ADDON_ID" ;;
    *) echo "usage: teardown.sh [--native|--docker]" >&2; exit 2 ;;
esac
echo "Removed $ADDON_ID."
