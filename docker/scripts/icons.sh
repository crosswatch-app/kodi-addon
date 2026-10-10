#!/usr/bin/env bash
# Render the window icons from Material Symbols Rounded (Google, Apache License 2.0), the
# set CrossWatch's own UI uses. Run once when an icon is added; the PNGs are committed, so
# nothing is fetched by the add-on.
#   icons.sh [OUT_DIR]   default: resources/skins/Default/media/crosswatch/icons
set -euo pipefail
COMMIT="27e9ef1dbeedc13d682fece4a58e1eda4cb0961a"
OUT="${1:-$(dirname "$0")/../../resources/skins/Default/media/crosswatch/icons}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"
for name in warning check_circle; do
    # The plain _48px file is the default axes: outline (FILL 0), weight 400, grade 0.
    curl -fsSL -o "$WORK/$name.svg" \
        "https://raw.githubusercontent.com/google/material-design-icons/$COMMIT/symbols/web/$name/materialsymbolsrounded/${name}_48px.svg"
    # Rendered large and scaled down for smooth edges; RGB set to white, alpha kept, so
    # the XML can tint it with colordiffuse.
    magick -background none -density 768 "$WORK/$name.svg" -resize 64x64 \
        -channel RGB -fill white -colorize 100 +channel -strip "PNG32:$OUT/$name.png"
done
