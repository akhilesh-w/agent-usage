#!/usr/bin/env bash
# Build a double-clickable (and Raycast-launchable) macOS .app
# without requiring a full Xcode install — Swift CLI tools + SDK are enough.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/desktop/macos/Sources"
OUT_DIR="${1:-$ROOT/dist}"
APP="$OUT_DIR/Agent Readout.app"
BIN_NAME="AgentReadout"
SDK="$(xcrun --show-sdk-path)"

echo "→ Building Agent Readout.app"
echo "  SDK: $SDK"
echo "  Out: $APP"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Compile SwiftUI + WebKit shell
swiftc -parse-as-library \
  -O \
  -sdk "$SDK" \
  -target arm64-apple-macosx13.0 \
  -framework SwiftUI \
  -framework AppKit \
  -framework WebKit \
  -framework Foundation \
  "$SRC/App.swift" \
  "$SRC/ContentView.swift" \
  "$SRC/DashboardWebView.swift" \
  "$SRC/DashboardRuntime.swift" \
  -o "$APP/Contents/MacOS/$BIN_NAME"

# Info.plist
cp "$ROOT/desktop/macos/Info.plist" "$APP/Contents/Info.plist"

# Bundle Python package + UI + pricing so the app is relocatable
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "$ROOT/agent_readout/" "$APP/Contents/Resources/agent_readout/"
rsync -a --delete "$ROOT/web/static/" "$APP/Contents/Resources/web/static/"
rsync -a --delete "$ROOT/data/" "$APP/Contents/Resources/data/"

# PkgInfo
echo -n "APPL????" > "$APP/Contents/PkgInfo"

chmod +x "$APP/Contents/MacOS/$BIN_NAME"

echo "✓ Built: $APP"
echo
echo "Run:"
echo "  open \"$APP\""
echo
echo "Raycast: add Application → pick “Agent Readout”, or"
echo "  open -a \"Agent Readout\""
echo "(optionally copy the .app to /Applications first)"
