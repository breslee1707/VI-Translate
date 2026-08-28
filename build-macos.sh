#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
python="$root/.venv/bin/python"
app="$root/dist/PDFTranslate.app"
archive="$root/dist/PDFTranslate-macos.zip"

if [[ ! -x "$python" ]]; then
    echo "No virtual environment found. Run: python3 -m venv .venv" >&2
    exit 1
fi

echo "==> Installing app and packaging dependencies"
"$python" -m pip install -r "$root/requirements-app.txt"

if [[ "${1:-}" != "--skip-assets" ]]; then
    echo "==> Fetching the layout model and font to bundle"
    "$python" "$root/scripts/fetch_assets.py"
fi

echo "==> Running PyInstaller"
"$python" -m PyInstaller --noconfirm --clean "$root/app.spec"

required=(
    "$app/Contents/MacOS/PDFTranslate"
    "$app/Contents/Info.plist"
    "$app/Contents/Resources/app/fonts/BeVietnamPro-Regular.ttf"
    "$app/Contents/Resources/app/assets/icon.png"
)
for path in "${required[@]}"; do
    if [[ ! -e "$path" ]]; then
        echo "Incomplete build, missing: $path" >&2
        exit 1
    fi
done

echo "==> Creating $archive"
rm -f "$archive"
ditto -c -k --sequesterRsrc --keepParent "$app" "$archive"
echo "==> Done"
