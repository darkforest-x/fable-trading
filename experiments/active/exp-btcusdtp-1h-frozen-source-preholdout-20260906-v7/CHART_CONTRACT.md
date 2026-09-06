# V7 report chart contract

2026-09-06, after the frozen V7 outcome run and before chart implementation.
Presentation only; no new entry/exit rule or inferential test is selected here.

- Question: does the mean strategy/random-entry difference persist across time?
- Takeaway: aggregate slight excess is not stable net profitability; monthly
  variation and all denominators must remain visible, including losing months.
- Surface: existing full technical portable HTML report, canonical native
  artifact; retain both existing V1 and V6 charts and all historical sections.
- Family: time comparison, two-series line. Twenty-four consecutive UTC months
  in 2023-2024, two rows per month; no missing-month interpolation.
- Population: only same-month fully paired case requests and their mean of
  three observed controls. Do not mix all286 case means with matched283 means.
- Fields: month, strategy/control series, mean net bp, matched request count,
  all request count, coverage, matched excess. Read only saved case-request and
  matched-request outcomes; exact SQL used to aggregate must appear in source.
- Mean returns are per-entry nominal returns, never cumulative account equity.
- Palette: hard two-root cap native blue/orange plus neutral zero/reference; visible
  series legend and source/tooltip labels provide non-colour identification.
  Native long-form renderer rebuilds series and does not honour explicit gold
  series colour; accept the documented blue/orange pair, not a fake gold claim.
- Footprint: full-width native report chart after V7 matching narrative;
  default shared responsive mobile stack. No bespoke CSS or chart runtime.
- QA: standard canonical artifact validator and portable delivery verifier.
  Preserve exact historical chart specs/datasets. Disclose structural-only QA
  if no compatible installed Chromium; do not install browsers or substitute
  custom browser automation. Exact gate/fold/diagnosis lookup stays in tables.
