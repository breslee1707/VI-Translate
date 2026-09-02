# Building and Releasing the Android APK

The Android app lives in [`android/`](../android). It is a separate product
from the desktop build and releases under its own tag namespace.

## Toolchain

| Component | Version | Why it is pinned |
| --- | --- | --- |
| Gradle | 8.11.1 | AGP 8.7.2 and Kotlin 2.0.21 do not run on Gradle 9. |
| Android Gradle Plugin | 8.7.2 | Matches the Compose and Kotlin versions in `libs.versions.toml`. |
| Kotlin | 2.0.21 | Paired with the Compose compiler plugin of the same version. |
| JDK | 17 | AGP 8.7 targets 17; a newer daemon JVM fails to load it. |
| compileSdk / targetSdk | 35 | Android 15. |
| minSdk | 26 | Android 8.0. |

Do not commit `android/local.properties`. `sdk.dir` in that file overrides
`ANDROID_HOME`, so a committed one breaks every checkout but the author's.

## Building locally

```bash
cd android
./gradlew assembleDebug testDebugUnitTest lintDebug
```

The debug APK lands in `android/app/build/outputs/apk/debug/app-debug.apk`.

On Windows, set `ANDROID_HOME` to the SDK root and use `gradlew.bat`. If the
Gradle test worker dies with `Could not find or load main class ...`, check the
system `PATH` for an entry containing a stray double quote: Gradle passes the
whole `PATH` as `-Djava.library.path`, and an unbalanced quote splits the
command line.

## Signing

A release APK is signed with a key you generate and keep. The build never falls
back to the debug keystore: that key is generated per machine, so a CI-signed
release would carry a different signature every run and no one could update over
the previous install.

Generate the key once:

```bash
keytool -genkeypair -v \
  -keystore vitranslate-release.jks \
  -keyalg RSA -keysize 4096 -validity 10000 \
  -alias vitranslate
```

Keep the `.jks` file and its passwords somewhere durable and private. Losing
them means every future release has to be installed as a new app.

**Locally**, put an untracked `android/keystore.properties` beside the build
file:

```properties
storeFile=/absolute/path/to/vitranslate-release.jks
storePassword=…
keyAlias=vitranslate
keyPassword=…
```

**On CI**, add four repository secrets:

| Secret | Value |
| --- | --- |
| `ANDROID_KEYSTORE_BASE64` | `base64 -w0 vitranslate-release.jks` |
| `ANDROID_KEYSTORE_PASSWORD` | the keystore password |
| `ANDROID_KEY_ALIAS` | `vitranslate` |
| `ANDROID_KEY_PASSWORD` | the key password |

Without `ANDROID_KEYSTORE_BASE64` the workflow still builds, but produces an
unsigned APK and the publish job refuses the release.

## Releasing

Android tags are `android-v<versionName>`. **Never release the Android build
under a `v*` tag.** That namespace belongs to the desktop product:
`.github/workflows/release.yml` triggers on it, and `app/update.py` downloads
`PDFTranslate-windows.zip` from whatever it finds there. A `v*` tag carrying
only an APK tells every installed Windows copy that an update exists and then
fails to deliver it.

1. Set `versionName` (and bump `versionCode`) in
   [`android/app/build.gradle.kts`](../android/app/build.gradle.kts).
2. Merge to `main`.
3. Tag `android-v<versionName>` and push it.
4. `.github/workflows/android-build.yml` checks the tag against `versionName`,
   builds, verifies the signature with `apksigner`, and publishes
   `PDFTranslate-android-<version>.apk`.

The in-app update check reads the same namespace: `UpdateChecker` walks the
release list and takes the newest non-draft release whose tag starts with
`android-v`.

## Known limits of the port

The Android build is not the desktop pipeline recompiled. It reimplements
layout preservation on PDFBox-Android instead of PyMuPDF and pdfminer.six, and
it uses spatial heuristics where the desktop build uses BabelDOC/YOLOv8 layout
models. Text-based PDFs only — there is no OCR. Complex mathematics can still
show misplaced superscripts, broken fractions and missing TeX/AMS glyphs.
