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
    local dest="$1"
    mkdir -p "$dest"
    # Everything dev-only is excluded, so what Kodi loads is what a released zip would
    # contain. A file listed here that stops existing is harmless; one that appears and is
    # not listed ships to users.
    rsync -a --delete \
        --exclude '.git' --exclude '.github' --exclude '.gitignore' \
        --exclude 'tests' --exclude 'docker' --exclude 'docs' \
        --exclude '.claude' --exclude 'notes' --exclude 'CLAUDE.md' \
        --exclude '__pycache__' --exclude '*.pyc' \
        --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude '.coverage' \
        --exclude 'pyproject.toml' --exclude 'requirements-dev.txt' \
        --exclude 'requirements-dev.lock' \
        --exclude 'pyrightconfig.json' --exclude '.pre-commit-config.yaml' \
        "$ADDON_SRC/" "$dest/"
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
