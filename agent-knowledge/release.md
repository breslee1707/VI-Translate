# Cross-Platform Build and Release

Only publish when the user explicitly requests it. The authoritative version is
`APP_VERSION` in `app/update.py`; a release tag must be exactly `v<APP_VERSION>`.
The release workflow rejects a mismatch.

## Local Gates

- Run the complete validation gate in [validation.md](validation.md).
- Windows: run `build.ps1`; verify `dist/PDFTranslate-windows.zip`, payload
  files, SHA-256, and packaged `PDFTranslate.exe --smoke-test` exit code 0.
- macOS builds require Darwin and the target architecture. `build-macos.sh`
  builds/smoke-tests/signs the `.app`, creates a DMG, and verifies it.

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
