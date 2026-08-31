# Proven Regression Patterns

Use this as a cause map, not a substitute for inspecting the failing PDF.

| Symptom | Proven cause | Guard / regression location |
| --- | --- | --- |
| GUI stops at the last page on a large book | Unused dual PDF doubled pages; font subsetting and `garbage=3` recompressed the whole document while holding the GIL | Mono-only app path and large-document thresholds in `high_level.py`; `test_large_document_finalization.py` |
| Whole page appears upside down | A visually upright source used a reflected `1 0 0 -1` text matrix with negative font size; reflection was mistaken for rotation | Classify from baseline and normalize reflection; orientation tests |
| Rotated heading becomes one glyph per line | Source 90-degree baseline was rebuilt as horizontal text | Quarter-turn grouping/rendering in `converter.py`; orientation/style tests |
| Bold or italic disappears | Translation collapsed every run into the regular font | Validated `<s1..3>` pairs, variant selection, synthetic fallback tests |
| Formula moves, overlaps, or exposes `{vN}` | Ordinary-font math was translated as prose or translator damaged placeholders | Formula regions, stacked fraction detection, safe tag round-trip tests |
| Table labels stay English | Earlier pipeline protected the entire model table | Translate only matched cells; preserve unmatched tables and technical codes |
| Paragraph crosses the right edge | Width budget ignored first-line indentation | `paragraph_width_budget()` regression |
| Last table line crosses a row | Font shrank after ink was measured, or preserved code remained larger than prose | Recompute final ink, fit union, then shift inside cell bounds |
| Bullets vanish on Mac or Windows | Office encoded Wingdings/Symbol bullets as PUA characters absent from Go Noto and Times New Roman | `is_bullet_character()` preserves the embedded dingbat glyph; preservation tests |
| Translation service corrupts style/formula tags | Tags are translated, dropped, duplicated, or cross-nested | Reject segment, keep source, report partial; handoff translator tests |
| Vietnamese loses every stacked-diacritic letter ("Việt" renders as "Vi t") | `subset_fonts(fallback=True)` renumbered glyphs while the content stream addresses them by raw ID; only bit documents under the 200-page/50 MiB threshold | `should_subset_fonts` returns False; `test_large_document_finalization.py` |
| Lines of one paragraph print through each other | Leading was compressed to 0.75 against Vietnamese ink needing 1.10 em | `min_line_height_for_language`; `test_preservation_rules.py` |
| A paragraph prints over the one below it | Only its own box was the budget, and prose had no ink guard | `available_height_below` plus the prose fit loop; `test_inline_formula_layout.py` |
| A whole bullet list stays in English and sprawls over its neighbours | The bullet's tab set the paragraph size, so the body looked like a subscript and was preserved as a formula | `size_should_follow_body`; `test_inline_formula_layout.py` |
| A structurally damaged PDF fails while pikepdf opens it fine | Repair was gated on pikepdf rather than on the engine's own round trip | `pymupdf_can_round_trip`; `test_large_document_finalization.py` |
| The packaged macOS Intel app dies before the first page | cryptography 49.0.0 dropped the macOS universal2 wheel, so Intel compiles it against Homebrew's OpenSSL while PyInstaller bundles Python's older `libssl.3.dylib` (`Symbol not found: _SSL_get0_group_name`) | Intel-only pin in `requirements.txt`; `verify_engine()` in the packaged `--smoke-test` |
| An image-only scan is delivered as a finished translation | The scan was detected but the result never left the engine, and `is_scanned_page` only fires when one image covers half a page, which tiled scans never do | `page_has_image` plus `image_only_pages`; the no-extractable-text refusal in `translate_pdf.py`; `test_preservation_rules.py`, `test_translate_pdf.py` |
| A page that did translate is reported as an untranslated scan | `receive_layout` runs once per nested container and only the last call carries the text, so the page was judged before its own content arrived | Accumulate `segments_by_page`, decide `image_only_pages` at the end; `test_preservation_rules.py` |
| Every skipped segment tells the user their network is down | The skip reason reached `record_translation_failure` and was logged, then dropped; only a count travelled up | `TranslationReport.reasons` through `high_level.py`; `_describe_failures`; `test_translate_pdf.py` |
| A user cannot read or report the error they were shown | The detail label starved the filename column and clipped; no code, no log pointer | `app/errors.py` codes and the row detail dialog; `test_app_errors.py` |

When adding a new guard, reproduce the smallest failing geometry in a unit test
and validate the real document visually. Do not encode a filename-specific fix.
