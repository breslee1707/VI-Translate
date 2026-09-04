# Android Build and Release

The Android app in `android/` is a second product, not the desktop pipeline
recompiled. It reimplements layout preservation on PDFBox-Android
(`PdfLayoutPreserver.kt`) instead of PyMuPDF and pdfminer.six, uses spatial
heuristics where the desktop build uses BabelDOC/YOLOv8, has no counterpart to the
desktop OCR mode (ML Kit text recognition would be the port), reads text-based PDFs
only, and translates through `GoogleTranslateEngine`. Do not describe a desktop
fix as fixed on Android, or the reverse, without checking both.

## Tag Namespaces Must Not Cross

`v*` belongs to the desktop build. `.github/workflows/release.yml` triggers on
it and `app/update.py` downloads `PDFTranslate-windows.zip` from whatever the
latest `v*` release holds. Publishing an APK under `v*` makes every installed
Windows copy see an update it cannot fetch.

Android releases are tagged `android-v<appVersionName>` and published by
`.github/workflows/android-build.yml`, which refuses a tag that disagrees with
`appVersionName` in `android/app/build.gradle.kts`. `UpdateChecker` walks the
release list and takes the newest non-draft, non-prerelease tag with that
prefix; it must never fall back to `/releases/latest`, which returns whichever
product released last.

The Android line started at 0.1.0 while the desktop is past v0.2.5, so a
desktop tag compares *higher*. The prefix filter, not the number comparison, is
what keeps a Windows release from prompting Android users. `UpdateCheckerTest`
pins this; do not "simplify" it away.

`versionCode` is derived from `appVersionName` as
`major * 10000 + minor * 100 + patch`. Never set it by hand: Android refuses to
install over an equal or higher code, and the failure surfaces on a user's
phone as "app not installed", nowhere in CI. Minor and patch stay below 100.

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

## Layout Rules the Port Learned the Hard Way

`PdfLayoutPreserver` strips the whole text layer and writes the translation
back, so anything the collector misses is erased for good and anything it
misjudges is drawn wrong. Four rules earned by comparing a rendered page
against its source; each has a test in `PdfLayoutPreserverTest`.

- **A wrapped line is bounded by its neighbour, not by the page.** The width
  used to be `cropBox - x - 40`, which on a multi-column page let a paragraph
  run over the column beside it. `computeRightLimits` takes the nearest block
  that shares the line and starts further right, then gives every block on the
  same left edge the tightest limit its column found. No overshoot allowance:
  a 5% one is twelve points of text on top of the next column.
- **A fraction needs a bar, and the bar must span what it divides.** Merging
  vertically stacked short-math blocks without a bar destroys tables — rows of
  a symbol column sit at the line pitch and all look like operands, so
  thirty rows collapsed into fifteen. That pass is gone. An en dash is also
  not a bar: it is how a table writes "dimensionless", and reading it as one
  merged the units above and below into `mm/N`.
- **Sub- and superscripts must be in the symbol test.** The collector rewrites
  off-baseline digits into `U+2080`/`U+2070` blocks, so any pattern deciding
  whether a block is math has to accept them, or `m'0` goes to the translator
  and returns as a Vietnamese word. Likewise `` is ASCII-only, so a Greek
  initial hides the function word behind it: `emax` needs explicit
  ASCII-letter lookarounds to be recognised.
- **Rotated runs are captured, redrawn rotated, and never regrouped.** Line
  grouping and fraction detection compare page coordinates, which mean
  something else once text reads up the page. A rotated run takes its origin
  from the text matrix, keeps the length it was drawn at, and is redrawn with
  `Matrix.getRotateInstance`. Before this, rotated table headers were stripped
  and never written back.

## Translation Is Per Paragraph, Not Per Line

The port sent one extracted line at a time to the engine. An engine given a
fragment translates the fragment: "advanced", orphaned at the end of a line
from "advanced equations", came back as the Vietnamese for "advanced
equipment". `groupIntoParagraphs` gathers lines back into the sentence they
spell before anything is sent, and `drawParagraph` writes the answer onto the
baselines and left edges the source used, so the layout does not move.

Three things that took a rendered page to work out:

- **The next line of a paragraph is not the next line on the page.** Blocks
  arrive in reading order across the whole page, so on a three-column layout
  the line after a left-column line is the middle column at the same height.
  Each paragraph follows its own column down instead: the highest unused line
  below the current one that starts at the same left edge.
- **The measure is what the lines were set to, not how far they could reach.**
  Measuring "did this line run the full width" against the gap to the next
  column made ordinary full lines look short, and 41 lines produced 39
  paragraphs. The measure is the widest line in the column.
- **A table and a paragraph are not separable by line spacing.** In the
  document this was built against, a table row and a wrapped line are both one
  pitch apart to a tenth of a point. What separates them is the spread of line
  widths: prose runs the full measure on every line but its last (0.93 of lines
  at full measure), a table's labels are each whatever length they happen to be
  (0.22). A column is treated as prose only when most of its lines run the
  measure *and* the measure is at least 8 ems — without the second test a
  column of one-em symbols looks like perfectly justified prose. A non-prose
  column is never merged, which costs the two-line table labels their join and
  is the right side to err on.

Vietnamese runs longer than English, so a paragraph rarely fits the line count
its source had. The spare space below it is spent before the type is shrunk --
type two points smaller than the column beside it is visible, a paragraph one
line longer in its own document's gap is not -- but one line of the gap is
never spent, because the blank line between two paragraphs is what says they
are two.

## Windows Note

If the Gradle test worker fails with `Could not find or load main class ...`,
inspect the system `PATH` for an entry with an unbalanced double quote. Gradle
passes the whole `PATH` as `-Djava.library.path`, and a stray quote splits the
worker command line. This is a machine fault, not a project one.
