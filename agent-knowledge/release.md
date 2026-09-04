# Cross-Platform Build and Release

This file covers the desktop product. The Android app releases under the
separate `android-v*` namespace; see [android.md](android.md). A `v*` tag is
read by every installed Windows build, so nothing but a full desktop release
may ever carry one.

Only publish when the user explicitly requests it. The authoritative version is
`APP_VERSION` in `app/update.py`; a release tag must be exactly `v<APP_VERSION>`.
The release workflow rejects a mismatch.

## Local Gates

- Run the complete validation gate in [validation.md](validation.md).
- Windows: run `build.ps1`; verify `dist/PDFTranslate-windows.zip`, payload
  files, SHA-256, and packaged `PDFTranslate.exe --smoke-test` exit code 0.
- macOS builds require Darwin and the target architecture. `build-macos.sh`
  builds/smoke-tests/signs the `.app`, creates a DMG, and verifies it.

## What the In-App Updater Depends On

Windows builds replace themselves from the release, so the published asset is
an interface, not just a download:

- The asset must be named `PDFTranslate-windows.zip` and hold the build at the
  archive root (`PDFTranslate.exe` and `_internal/` as top-level entries).
  `app/update.py` refuses anything else and falls back to the release page.
- The tag must be `v<APP_VERSION>`; a tag that is not dotted numbers is read
  as "no update" by every installed build.
- Never publish a partial or re-uploaded asset under an existing tag: installed
  apps download whatever that name points at and restart into it.
- To rehearse an update without publishing, point `PDFTRANSLATE_UPDATE_API` at
  a local JSON file shaped like the GitHub releases API.

## GitHub Flow

1. Commit only source, tests, docs, and version changes on a feature branch.
2. Push, create/update a PR, review exact head SHA, and merge to `main`.
3. Update local `main` with `--ff-only` and verify a clean worktree.
4. Create and push the matching annotated `v*` tag.
5. Wait for `.github/workflows/release.yml` to finish all jobs:
   Windows, macOS Apple Silicon, macOS Intel, then publish.
6. Verify the release is neither draft nor prerelease and contains exactly:
   `PDFTranslate-windows.zip`, `PDFTranslate-macos-apple-silicon.dmg`, and
   `PDFTranslate-macos-intel.dmg`.
7. Download artifacts and compare local SHA-256 values with GitHub digests.

`.github/workflows/macos-artifacts.yml` is an on-demand/branch build and does
not replace the tag release gate. PyInstaller is not a cross-compiler; never
claim a Windows-built Mac artifact was tested. Without a Developer ID,
`build-macos.sh` applies an ad-hoc signature: users may need right-click → Open,
and the DMG is not notarized.
