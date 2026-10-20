#!/usr/bin/env bash
# Render fanart.jpg (1920x1080, the size Kodi's add-on rules ask for) and the repository's
# social banner (1280x640, GitHub's social preview size, also used in the forum thread) from
# icon.png and the palette the add-on's windows use. Run when the icon or the name changes;
# both images are committed, and only the fanart ships in the add-on. Needs ImageMagick 7
# and Noto Sans (SIL Open Font License; the text is rendered into the images, so no font
# ships with the add-on).
#   fanart.sh [FANART [BANNER]]   defaults: fanart.jpg, docs/assets/banner.png
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-$ROOT/fanart.jpg}"
BANNER="${2:-$ROOT/docs/assets/banner.png}"
FONT_DIR="${FONT_DIR:-/usr/share/fonts/truetype/noto}"
BOLD="$FONT_DIR/NotoSans-Bold.ttf"
REGULAR="$FONT_DIR/NotoSans-Regular.ttf"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The icon's own background, so the icon sits on the canvas without a visible square.
BASE="rgb(4,5,9)"

# Soft light behind the icon in the icon's gradient colours (cyan to violet), at a tenth of
# the fanart's size: blurred small and scaled up, the same smooth glow renders in seconds
# rather than minutes. Each image scales it to its own size.
magick -size 192x108 "xc:$BASE" \
    \( -size 192x108 xc:none -fill "rgba(45,226,255,0.22)" -draw "circle 130,43 130,79" -blur 0x16 \) -composite \
    \( -size 192x108 xc:none -fill "rgba(124,92,255,0.30)" -draw "circle 152,64 152,106" -blur 0x18 \) -composite \
    \( -size 192x108 xc:none -fill "rgba(255,122,224,0.12)" -draw "circle 165,86 165,112" -blur 0x16 \) -composite \
    "$WORK/glow.png"
magick "$WORK/glow.png" -filter Gaussian -resize '1920x1080!' "$WORK/bg.png"
magick "$WORK/glow.png" -filter Gaussian -resize '1280x720!' -gravity center -crop 1280x640+0+0 +repage "$WORK/banner-bg.png"

# Lighten keeps the icon's glow and drops its near-black background into the canvas.
magick "$WORK/bg.png" \( "$ROOT/icon.png" -resize 700x700 \) -gravity center -geometry +430+0 -compose Lighten -composite \
    -compose Over -gravity NorthWest \
    -font "$BOLD" -pointsize 132 -fill "#F1F3F5" -annotate +150+330 "CrossWatch" \
    -font "$REGULAR" -pointsize 76 -fill "#9299A6" -annotate +156+500 "for Kodi" \
    -font "$REGULAR" -pointsize 40 -fill "#9299A6" -annotate +158+640 "Everyone in the household keeps" \
    -annotate +158+694 "their own watch history." \
    -strip -sampling-factor 4:2:0 -quality 88 "$OUT"

mkdir -p "$(dirname "$BANNER")"
magick "$WORK/banner-bg.png" \( "$ROOT/icon.png" -resize 460x460 \) -gravity center -geometry +290+0 -compose Lighten -composite \
    -compose Over -gravity NorthWest \
    -font "$BOLD" -pointsize 92 -fill "#F1F3F5" -annotate +96+170 "CrossWatch" \
    -font "$REGULAR" -pointsize 54 -fill "#9299A6" -annotate +100+290 "for Kodi" \
    -font "$REGULAR" -pointsize 30 -fill "#9299A6" -annotate +102+392 "Everyone in the household keeps" \
    -annotate +102+432 "their own watch history." \
    -strip -define png:exclude-chunks=date,time "PNG24:$BANNER"
chmod 0644 "$OUT" "$BANNER"
