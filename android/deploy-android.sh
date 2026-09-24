#!/bin/bash
set -e

echo "=================================================="
echo "Building VI-Translate Unified Android App"
echo "=================================================="

# Export ANDROID_HOME if not set
if [ -z "$ANDROID_HOME" ]; then
    export ANDROID_HOME=$HOME/Library/Android/sdk
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "Building Unified App APK (:app)..."
./gradlew :app:assembleDebug

APP_APK="app/build/outputs/apk/debug/app-debug.apk"

if [ ! -f "$APP_APK" ]; then
    echo "Error: $APP_APK not found!"
    exit 1
fi

echo ""
echo "=================================================="
echo "Checking Connected ADB Devices"
echo "=================================================="

DEVICES=$(adb devices | grep -v "List" | grep "device" | awk '{print $1}')

if [ -z "$DEVICES" ]; then
    echo "No active adb devices/emulators found."
    echo "Build completed successfully. Unified APK ready at:"
    echo "   - Main App: $APP_APK"
    exit 0
fi

for DEVICE in $DEVICES; do
    echo "Installing to device: $DEVICE"
    adb -s "$DEVICE" install -r "$APP_APK"
    
    echo "Checking installation..."
    adb -s "$DEVICE" shell pm list packages | grep "com.vitranslate"
    echo "Successfully deployed to $DEVICE"
done

echo ""
echo "=================================================="
echo "Deployment Complete!"
echo "=================================================="
