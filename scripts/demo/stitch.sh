#!/usr/bin/env bash
# stitch.sh — build the final 30s portrait MP4 from recorded segments.
#
# Composition:
#   0–9 s    chat.webm       (recorded approval-card sequence)
#   9–15 s   signing.png     (real keys.coinbase.com sign view, vertical pan)
#   15–30 s  result.png      (post-payment result card)
#
# Inputs (under scripts/demo/build/$KEY/):
#   chat.webm, result.png — produced by record.mjs
#   signing.png           — produced inline below from docs/screenshots/04-signing.png
#                           via signing.py with the per-endpoint price
#
# Output: docs/demos/$KEY.mp4

set -euo pipefail

KEY="${1:?usage: stitch.sh <key>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="$ROOT/scripts/demo/build/$KEY"
OUT_DIR="$ROOT/docs/demos"
OUT="$OUT_DIR/$KEY.mp4"

mkdir -p "$BUILD" "$OUT_DIR"

# Extract the price for this endpoint from demos.json
PRICE="$(python3 -c "
import json, sys
demos = json.load(open('$ROOT/scripts/demo/demos.json'))
match = [d for d in demos if d['key'] == '$KEY']
if not match: sys.exit(f'no key=$KEY in demos.json')
print(match[0]['price'])
")"

# Render the signing.png variant at the right price.
"$ROOT/.venv/bin/python" "$ROOT/scripts/demo/signing.py" "$PRICE" "$BUILD/signing.png"

# Verify inputs exist.
for f in chat.webm signing.png result.png; do
  test -s "$BUILD/$f" || { echo "missing: $BUILD/$f"; exit 1; }
done

# Vertical pan range on the signing screenshot (1080×2640):
#   y=0   shows URL bar + "Review" + price band
#   y=720 shows JSON tree + Deny/Sign buttons
PAN_FROM=0
PAN_TO=720
XFADE=0.5

if [ -f "$BUILD/poll.webm" ]; then
  # Long-form (90s) composition: chat → sign → poll → result
  CHAT_T=9
  SIGN_T=6
  POLL_T=45
  RESULT_T=30

  ffmpeg -y -loglevel warning \
    -i "$BUILD/chat.webm" \
    -loop 1 -t "$SIGN_T" -i "$BUILD/signing.png" \
    -i "$BUILD/poll.webm" \
    -loop 1 -t "$RESULT_T" -i "$BUILD/result.png" \
    -filter_complex "
      [0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x0a0c0d,trim=duration=${CHAT_T},setpts=PTS-STARTPTS,fps=30[v0];
      [1:v]crop=1080:1920:0:'min(${PAN_TO}\\,${PAN_FROM}+(t/${SIGN_T})*(${PAN_TO}-${PAN_FROM}))',setpts=PTS-STARTPTS,fps=30[v1];
      [2:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x0a0c0d,trim=duration=${POLL_T},setpts=PTS-STARTPTS,fps=30[v2];
      [3:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x0a0c0d,setpts=PTS-STARTPTS,fps=30[v3];
      [v0][v1]xfade=transition=fade:duration=${XFADE}:offset=$(echo "$CHAT_T - $XFADE" | bc -l)[x01];
      [x01][v2]xfade=transition=fade:duration=${XFADE}:offset=$(echo "$CHAT_T + $SIGN_T - 2*$XFADE" | bc -l)[x02];
      [x02][v3]xfade=transition=fade:duration=${XFADE}:offset=$(echo "$CHAT_T + $SIGN_T + $POLL_T - 3*$XFADE" | bc -l)[xout]
    " \
    -map "[xout]" \
    -c:v libx264 -pix_fmt yuv420p -preset medium -crf 23 \
    -movflags +faststart \
    "$OUT"
else
  # Short-form (30s) composition: chat → sign → result
  CHAT_T=9
  SIGN_T=6
  RESULT_T=16

  ffmpeg -y -loglevel warning \
    -i "$BUILD/chat.webm" \
    -loop 1 -t "$SIGN_T" -i "$BUILD/signing.png" \
    -loop 1 -t "$RESULT_T" -i "$BUILD/result.png" \
    -filter_complex "
      [0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x0a0c0d,trim=duration=${CHAT_T},setpts=PTS-STARTPTS,fps=30[v0];
      [1:v]crop=1080:1920:0:'min(${PAN_TO}\\,${PAN_FROM}+(t/${SIGN_T})*(${PAN_TO}-${PAN_FROM}))',setpts=PTS-STARTPTS,fps=30[v1];
      [2:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x0a0c0d,setpts=PTS-STARTPTS,fps=30[v2];
      [v0][v1]xfade=transition=fade:duration=${XFADE}:offset=$(echo "$CHAT_T - $XFADE" | bc -l)[x01];
      [x01][v2]xfade=transition=fade:duration=${XFADE}:offset=$(echo "$CHAT_T + $SIGN_T - 2*$XFADE" | bc -l)[xout]
    " \
    -map "[xout]" \
    -c:v libx264 -pix_fmt yuv420p -preset medium -crf 23 \
    -movflags +faststart \
    "$OUT"
fi

SIZE_KB=$(($(stat -f%z "$OUT") / 1024))
DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT" | awk '{printf "%.1f", $1}')
echo "$OUT — ${SIZE_KB} KB · ${DUR}s"
