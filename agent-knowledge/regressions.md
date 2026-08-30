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

When adding a new guard, reproduce the smallest failing geometry in a unit test
and validate the real document visually. Do not encode a filename-specific fix.
