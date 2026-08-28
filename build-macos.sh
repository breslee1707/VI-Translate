#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv-macos"
PYTHON_BIN="$VENV/bin/python3"
SKIP_ASSETS=0

if [[ "${1:-}" == "--skip-assets" ]]; then
  SKIP_ASSETS=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: ./build-macos.sh [--skip-assets]" >&2
  exit 2
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script must run on macOS. Use the GitHub Actions workflow from Windows." >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "==> Creating the macOS virtual environment"
  python3 -m venv "$VENV"
fi

echo "==> Installing app and packaging dependencies"
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r "$ROOT/requirements-app.txt"

if [[ $SKIP_ASSETS -eq 0 ]]; then
  echo "==> Fetching the layout model and font"
  "$PYTHON_BIN" "$ROOT/scripts/fetch_assets.py"
fi

echo "==> Creating the macOS icon"
ICON_ROOT="$ROOT/build/macos"
ICONSET="$ICON_ROOT/PDFTranslate.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  double=$((size * 2))
  sips -z "$size" "$size" "$ROOT/app/assets/icon.png" \
    --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z "$double" "$double" "$ROOT/app/assets/icon.png" \
    --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$ICON_ROOT/PDFTranslate.icns"

case "$(uname -m)" in
  arm64) ARCH_LABEL="apple-silicon"; export MACOS_TARGET_ARCH="arm64" ;;
  x86_64) ARCH_LABEL="intel"; export MACOS_TARGET_ARCH="x86_64" ;;
  *) echo "Unsupported Mac architecture: $(uname -m)" >&2; exit 1 ;;
esac

echo "==> Running PyInstaller for $ARCH_LABEL"
"$PYTHON_BIN" -m PyInstaller --noconfirm --clean "$ROOT/app-macos.spec"

APP="$ROOT/dist/PDFTranslate.app"
EXECUTABLE="$APP/Contents/MacOS/PDFTranslate"
if [[ ! -x "$EXECUTABLE" || ! -f "$APP/Contents/Info.plist" ]]; then
  echo "PyInstaller did not produce a complete application bundle at $APP" >&2
  exit 1
fi

echo "==> Smoke-testing the packaged executable"
file "$EXECUTABLE" | grep -q "$(uname -m)"
"$EXECUTABLE" --smoke-test

if [[ -n "${MACOS_SIGNING_IDENTITY:-}" ]]; then
  echo "==> Signing with the configured Developer ID"
  codesign --force --deep --options runtime --timestamp \
    --sign "$MACOS_SIGNING_IDENTITY" "$APP"
else
  echo "==> Applying an ad-hoc signature (Apple Developer ID not configured)"
  codesign --force --deep --sign - "$APP"
fi
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> Creating the DMG"
DMG_STAGE="$ROOT/build/macos/dmg"
DMG="$ROOT/dist/PDFTranslate-macos-$ARCH_LABEL.dmg"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"
cp -R "$APP" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"

for attempt in 1 2 3; do
  rm -f "$DMG"
  if hdiutil create -volname "PDF Translate" -srcfolder "$DMG_STAGE" \
    -ov -format UDZO "$DMG"; then
    break
  fi

  if [[ "$attempt" -eq 3 ]]; then
    echo "hdiutil could not create the DMG after $attempt attempts" >&2
    exit 1
  fi

  echo "hdiutil was busy; retrying ($attempt/3)"
  sleep $((attempt * 2))
done
hdiutil verify "$DMG"

echo "==> Done: $DMG"
if [[ -z "${MACOS_SIGNING_IDENTITY:-}" ]]; then
  echo "Unsigned distribution: users may need to right-click the app and choose Open."
fi
