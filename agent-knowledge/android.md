# Android Build and Release

The Android app in `android/` is a second product, not the desktop pipeline
recompiled. It reimplements layout preservation on PDFBox-Android
(`PdfLayoutPreserver.kt`) instead of PyMuPDF and pdfminer.six, uses spatial
heuristics where the desktop build uses BabelDOC/YOLOv8, reads text-based PDFs
only, and translates through `GoogleTranslateEngine`. Do not describe a desktop
fix as fixed on Android, or the reverse, without checking both.

## Tag Namespaces Must Not Cross

`v*` belongs to the desktop build. `.github/workflows/release.yml` triggers on
it and `app/update.py` downloads `PDFTranslate-windows.zip` from whatever the
latest `v*` release holds. Publishing an APK under `v*` makes every installed
Windows copy see an update it cannot fetch.

Android releases are tagged `android-v<versionName>` and published by
`.github/workflows/android-build.yml`, which refuses a tag that disagrees with
`versionName` in `android/app/build.gradle.kts`. `UpdateChecker` walks the
release list and takes the newest non-draft, non-prerelease tag with that
prefix; it must never fall back to `/releases/latest`, which returns whichever
product released last.

## Pinned Toolchain

Gradle 8.11.1, AGP 8.7.2, Kotlin 2.0.21, JDK 17, compileSdk/targetSdk 35,
minSdk 26. AGP 8.7 and Kotlin 2.0 do not run on Gradle 9, so the wrapper stays
on 8.x until AGP and Kotlin are raised together. `gradle-wrapper.jar` is the
published Gradle 9.7.1 jar and its checksum is verified in CI by
`gradle/actions/wrapper-validation`; a wrapper jar only fetches the
distribution named in `gradle-wrapper.properties`, so the versions differing is
deliberate, not drift.

`android/local.properties` must never be committed: `sdk.dir` overrides
`ANDROID_HOME` and breaks every other checkout.

## Signing

Release builds are signed from `ANDROID_KEYSTORE_*` environment variables or an
untracked `android/keystore.properties`, and are left unsigned when neither is
present. Never restore `signingConfig = signingConfigs.getByName("debug")` for
the release type: a debug keystore is generated per machine, so CI would sign
each release with a different key and users could not update in place. Full
procedure in `docs/android-release.md`.

## Translation Runs in a Foreground Service

`TranslationController` is a process-scoped singleton holding the queue,
settings and the translation loop; `TranslationService` keeps the process alive
and shows progress. Work must not be moved back into `viewModelScope` — that
tied a multi-minute run to the Activity. `translatePdf` takes an `isCancelled`
probe checked per page and per paragraph, and deletes its partial output before
rethrowing `TranslationCancelledException`, because a truncated PDF left on
disk is indistinguishable from a finished one.

## Windows Note

If the Gradle test worker fails with `Could not find or load main class ...`,
inspect the system `PATH` for an entry with an unbalanced double quote. Gradle
passes the whole `PATH` as `-Djava.library.path`, and a stray quote splits the
worker command line. This is a machine fault, not a project one.
