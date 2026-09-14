# Workbook QA — SPIKE V1 three-rule replay

Audit date: 2026-09-14.  This is a read-only QA of
`outputs/01a086ad-0b77-7380-b6a6-2b62f505d12b/SPIKE_V1_三规则精确回测_20260914.xlsx`.
No workbook, formula, source output, or preview was changed.

Workbook SHA-256:
`650b9888d8fc3723ea53727468e2c8337c6cf66d3352df4f3ad5b7372b251002`.

## Structural and numeric checks — pass

The OOXML package contains exactly 11 worksheets, in manifest order:
`总体对比`, `时间分区`, `分周期`, `单规则消融`, `改善拆解`, `逐月`,
`标的明细`, `随机对照`, `原6253逐笔`, `前20逐笔`, `口径与来源`.

The current workbook's `原6253逐笔` sheet has 6,253 populated event rows
(plus one styled blank terminal row).  Its static numeric cells reproduce the
closed-event primary figures, subject only to decimal storage rounding:

| Arm | Closed rows | Sum of workbook net-R cells | Required result |
| --- | ---: | ---: | ---: |
| Default | 6,170 | 838.178470318796 | 838.1784703181975 |
| Triple | 6,237 | 1201.253789700808 | 1201.2537896987315 |

The maximum difference is below `2.1e-9R` and comes from values serialized in
the sheet; the displayed two-decimal values remain exact at display precision.

The same sheet has 83 default-censored rows and 16 triple-censored rows.  In
all respective rows the closed-net-R cell is empty (`I` or `N`), never zero.
Its 6,253 `R` formulae are exactly
`IF(OR(Ir="",Nr=""),"",Nr-Ir)`: 6,170 have both inputs and a cached delta;
83 have an empty input and no cached delta.  There are zero violations of the
required “either input empty => delta empty” condition.

`前20逐笔` has 439 populated event rows (plus one styled blank terminal row),
437 default-closed and 438 triple-closed rows.  Its two default-censored and
one triple-censored net-R cells are blank, and all 439 `R` formulas follow the
same blank-propagating rule (437 cached values, 2 intentional blank caches).

There are no formulas on the other nine sheets; `R` is the only formula column
in either event sheet.  `入场UTC` uses custom format
`yyyy-mm-dd hh:mm` on both event sheets.  The two event sheets freeze the
first four rows and have a visible 25-character date column.

## Preview inspection — visual boundary recorded

All 11 PNG previews named by
`results/workbook_previews/manifest.json` were inspected.  In the visible
viewports, title weight, dark header weight, stripe contrast, numeric sign
colour, and the Chinese labels are legible.  Wide tables naturally expose only
their left columns in a single 2160px screenshot; this is a preview viewport
limit rather than a workbook column omission.

The previews are **not provenance-bound to the audited workbook**: their
modification times are 18:33:12–18:33:20, while the XLSX was modified at
18:33:43.  Preview `10.png` also shows an incomplete left-side date such as
`3-09-14 01:00` and does not visibly show the normal row-4 headers, whereas
the current XLSX XML has D5 serial `45183.041666...`, header `入场UTC`, and
format `yyyy-mm-dd hh:mm` (which is 2023-09-14 01:00).  This is evidence that
the preview cannot be used to approve the final XLSX date rendering.  No XLSX
change was made; regenerate previews from the SHA above if final visual
sign-off requires a hash-bound render.

## Conclusion

The current workbook passes its structural, closed-net-R, censoring, delta
formula/cache, and date-format checks.  Final visual approval of the current
file remains limited by stale preview artifacts; no data or formula defect was
found in the workbook XML.
