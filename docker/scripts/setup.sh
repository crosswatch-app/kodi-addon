#!/usr/bin/env bash
# Install this addon into a Kodi test environment.
#
#   setup.sh --native   Flatpak Kodi (default)
#   setup.sh --docker   headless container

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADDON_SRC="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONTAINER="${CONTAINER:-kodi-test}"
MODE="${1:---native}"

ADDON_ID="$(python3 "$SCRIPT_DIR/addon_meta.py" id "$ADDON_SRC")"
[ -n "$ADDON_ID" ] || { echo "refusing to continue without an addon id" >&2; exit 1; }
NATIVE_DATA="$HOME/.var/app/tv.kodi.Kodi/data"

copy_tree() {
    # stage.py's allow-list, so what Kodi loads is exactly what a release zip contains.
    python3 "$SCRIPT_DIR/stage.py" folder "$1"
}

case "$MODE" in
    --native)
        copy_tree "$NATIVE_DATA/addons/$ADDON_ID"
        echo "Installed $ADDON_ID into the Flatpak Kodi. Restart Kodi to load it."
        ;;
    --docker)
        docker start "$CONTAINER" >/dev/null
        TMP="$(mktemp -d)"
        copy_tree "$TMP/$ADDON_ID"
        docker cp "$TMP/$ADDON_ID" "$CONTAINER:/config/.kodi/addons/"
        rm -rf "$TMP"
        docker restart "$CONTAINER" >/dev/null
        echo "Installed $ADDON_ID into $CONTAINER and restarted it."
        ;;
    *)
        echo "usage: setup.sh [--native|--docker]" >&2
        exit 2
        ;;
esac
