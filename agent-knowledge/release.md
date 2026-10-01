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
  The smoke test also starts the automatic browser transport on local HTML.
  QA the installed adapter and the embedded fallback separately. The Windows
  payload retains WebView2 interop DLLs and Python.Runtime.dll for fallback;
  users with a working installed browser don't need WebView2 Runtime.
- macOS supports installed browsers and built-in WKWebView fallback with
  bundled PyObjC dependencies. Packaged/source smoke uses local HTML in a child.
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
- Never publish partial assets. Keep published tags/assets unchanged unless the
  user explicitly requests replacement: installed apps download whatever that
  name points at and restart into it. Follow the replacement procedure below
  for that exceptional, user-authorized operation.
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

Before tagging, dispatch `release.yml` on the feature branch to build and run
the full test/dependency/Ruff gates for all three targets without publishing.
The tag run repeats those gates before the publish job can run.

`.github/workflows/macos-artifacts.yml` is an on-demand/branch build and does
not replace the tag release gate. PyInstaller is not a cross-compiler; never
claim a Windows-built Mac artifact was tested. Without a Developer ID,
`build-macos.sh` applies an ad-hoc signature: users may need right-click → Open,
and the DMG is not notarized.

## Explicitly Authorized Same-Version Replacement

On 2026-10-01 the user explicitly chose to replace the GitHub v0.3.4 assets
after being told that existing v0.3.4 installs must download again manually.
That authorization is specific to this replacement, not future releases.

Preserve the original annotated tag object/commit, release metadata and all
three downloaded assets with verified digests. Run the full native matrix
preflight on the replacement source, merge and require the same tested tree.
Set the existing release to draft before moving its tag; force-push only that
exact tag with a lease on the previously verified tag object. The tag workflow
must repeat all checks, replace all three assets while draft, then publish only
after uploads finish. Verify every new digest and the downloaded Windows smoke
test. Restore the saved original source/assets if replacement fails.
