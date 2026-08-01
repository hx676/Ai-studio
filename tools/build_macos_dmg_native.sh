#!/bin/bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This final signing/notarization script must run on macOS." >&2
    exit 1
fi

APP_PATH="${1:?Usage: build_macos_dmg_native.sh /path/to/SynCanvas.app output.dmg}"
OUTPUT_DMG="${2:?Usage: build_macos_dmg_native.sh /path/to/SynCanvas.app output.dmg}"
VOLUME_NAME="${VOLUME_NAME:-SynCanvas}"
SIGN_IDENTITY="${SIGN_IDENTITY:-}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

/usr/bin/ditto "$APP_PATH" "$WORK_DIR/SynCanvas.app"
ln -s /Applications "$WORK_DIR/Applications"

if [ -n "$SIGN_IDENTITY" ]; then
    codesign --force --deep --options runtime --timestamp --sign "$SIGN_IDENTITY" "$WORK_DIR/SynCanvas.app"
    codesign --verify --deep --strict --verbose=2 "$WORK_DIR/SynCanvas.app"
fi

hdiutil create -volname "$VOLUME_NAME" -srcfolder "$WORK_DIR" -ov -format UDZO "$OUTPUT_DMG"

if [ -n "$SIGN_IDENTITY" ]; then
    codesign --force --timestamp --sign "$SIGN_IDENTITY" "$OUTPUT_DMG"
fi
if [ -n "$NOTARY_PROFILE" ]; then
    xcrun notarytool submit "$OUTPUT_DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$OUTPUT_DMG"
fi
