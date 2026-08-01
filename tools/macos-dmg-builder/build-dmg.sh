#!/bin/sh
set -eu

DMG_NAME="${DMG_NAME:-SynCanvas.dmg}"
VOLUME_NAME="${VOLUME_NAME:-SynCanvas}"
WORK_ROOT=/tmp/syncanvas-dmg
DMG_ROOT="$WORK_ROOT/root"
RAW_IMAGE="$WORK_ROOT/SynCanvas.iso"

rm -rf "$WORK_ROOT"
mkdir -p "$DMG_ROOT" /output
cp -a /input/. "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
chmod +x "$DMG_ROOT/SynCanvas.app/Contents/MacOS/SynCanvas"
chmod +x "$DMG_ROOT/Stop-SynCanvas.command"

genisoimage \
    -D \
    -V "$VOLUME_NAME" \
    -no-pad \
    -r \
    -apple \
    -o "$RAW_IMAGE" \
    "$DMG_ROOT"

dmg dmg "$RAW_IMAGE" "/output/$DMG_NAME"
test -s "/output/$DMG_NAME"
