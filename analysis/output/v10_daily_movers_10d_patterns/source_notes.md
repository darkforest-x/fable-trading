# Source notes

## Reporting job

- Question: 既有 v10 最近 10 日 Top20 扫描里有没有可复用规律？
- Audience: product stakeholder / project owner.
- Decision: 哪些观察值得进入 ETH 3m/5m/10m 双视图标注与建模方案。
- Delivery: portable HTML report generated from `artifact.json`.
- Material source query: `prepare_report_facts.sql`, actually executed against an in-memory SQLite table loaded from the existing `signals.csv`.
- Aggregation command: `.venv/bin/python scripts/analyze_v10_daily_movers_patterns.py`.

## Data quality profile

- `signals.csv`: 221 rows × 12 columns; 217 closed, 4 running; required fields have 0 nulls.
- `daily_rankings.csv`: 200 rows × 7 columns; 10 days × exactly 20 rows/day.
- Duplicate checks: 0 exact signal duplicates, 0 duplicate `(symbol, signal_time)`, 0 duplicate `(day, symbol)` rankings.
- Confidence range: 0.3003–0.8696, consistent with conf≥0.30 scan.
- Critical analytical risk: daily Top20 selection uses the completed day's absolute return.
- High analytical risk: 221 signals collapse to 107 symbol-days and include overlapping trades.

## Chart map

| Section | Question | Family / type | Dataset | Claim | Palette |
|---|---|---|---|---|---|
| Confidence | Does conf rank profit? | Comparison / horizontal bar | `confidence_first` | No monotonic ordering | blue sequential |
| Time of day | When do first signals fare better? | Comparison / horizontal bar | `hour_first` | 06–17 UTC beats 00–05 in selected cohort | orange sequential |
| Daily robustness | Is aggregate profit one-day driven? | Trend / discrete bar | `daily` | 9/10 daily means positive, but cohort is post-hoc | blue-orange diverging |

All chart datasets retain n, median, positive rate, and adjacent outcome fields beyond the plotted measure. 18–23 UTC has only n=2 and is explicitly marked underpowered.

## Omitted analyses

- No matched random control exists for these 10 days; therefore no detector incremental edge is estimated.
- No portfolio PnL, PF, capital usage, or overlap-adjusted return is reported because concurrent trades are not modeled.
- No new K-line read or model inference was performed; this is a secondary analysis of existing outputs.
- No holdout configuration was tuned or compared. The owner's request explicitly authorized review of the already-generated ten-day result.
- Holdout ledger: the original ten-day scan is global consumption **#10** because all dates are after 2026-05-04 and the owner explicitly requested it; this secondary read of the same outputs is not counted again as #11. The result is not eligible for tuning or promotion.

## Delivery verification

- Manifest validation: passed.
- HTML packaging: passed.
- Structural verification: passed.
- Browser screenshot verification: not run because no Chromium executable is installed in the packaging environment; the report is therefore not claimed as browser-verified.

## Required structure mapping

- Title: `title` block.
- Executive Summary: `executive_summary` block.
- Key findings with visual evidence: direction, confidence, time, repeats, robustness sections.
- Recommended next steps: `recommendations` block.
- Further questions: `further_questions` block.
- Caveats and assumptions: `caveats` block.
