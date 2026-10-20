#!/usr/bin/env bash
# Render fanart.jpg (1920x1080, the size Kodi's add-on rules ask for) from icon.png and the
# palette the add-on's windows use. Run when the icon or the name changes; the JPEG is
# committed. Needs ImageMagick 7 and Noto Sans (SIL Open Font License; the text is rendered
# into the image, so no font ships with the add-on).
#   fanart.sh [OUT]   default: fanart.jpg at the repo root
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-$ROOT/fanart.jpg}"
FONT_DIR="${FONT_DIR:-/usr/share/fonts/truetype/noto}"
BOLD="$FONT_DIR/NotoSans-Bold.ttf"
REGULAR="$FONT_DIR/NotoSans-Regular.ttf"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The icon's own background, so the icon sits on the canvas without a visible square.
BASE="rgb(4,5,9)"

# Soft light behind the icon in the icon's gradient colours (cyan to violet). Blurred at a
# tenth of the size and scaled up: the same smooth glow, in seconds rather than minutes.
magick -size 192x108 "xc:$BASE" \
    \( -size 192x108 xc:none -fill "rgba(45,226,255,0.22)" -draw "circle 130,43 130,79" -blur 0x16 \) -composite \
    \( -size 192x108 xc:none -fill "rgba(124,92,255,0.30)" -draw "circle 152,64 152,106" -blur 0x18 \) -composite \
    \( -size 192x108 xc:none -fill "rgba(255,122,224,0.12)" -draw "circle 165,86 165,112" -blur 0x16 \) -composite \
    -filter Gaussian -resize '1920x1080!' "$WORK/bg.png"

# Lighten keeps the icon's glow and drops its near-black background into the canvas.
magick "$WORK/bg.png" \( "$ROOT/icon.png" -resize 700x700 \) -gravity center -geometry +430+0 -compose Lighten -composite \
    -compose Over -gravity NorthWest \
    -font "$BOLD" -pointsize 132 -fill "#F1F3F5" -annotate +150+330 "CrossWatch" \
    -font "$REGULAR" -pointsize 76 -fill "#9299A6" -annotate +156+500 "for Kodi" \
    -font "$REGULAR" -pointsize 40 -fill "#9299A6" -annotate +158+640 "Everyone in the household keeps" \
    -annotate +158+694 "their own watch history." \
    -strip -sampling-factor 4:2:0 -quality 88 "$OUT"
chmod 0644 "$OUT"
