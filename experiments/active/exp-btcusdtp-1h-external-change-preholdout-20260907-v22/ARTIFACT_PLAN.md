# V22 technical report artifact plan

Audience: owner reviewing whether an entry filter improves the existing hourly
impulse strategy, rather than merely reducing exposure. Delivery is one local
portable HTML technical report with the complete Chinese Markdown source.

The decision is rejection or further independent validation; there is no live
trading, deployment, model promotion or parameter optimization in this artifact.

## Evidence and visuals

1. Report the unfiltered V18, static V21 and change V22 side by side, explicitly
   separating executed-trade mean from all-opportunity mean. Tables retain exact
   denominators and four chronological half-years; no redundant return bar plot.
2. Native bar chart: accepted original case opportunities by half-year, four bars,
   zero baseline, fixed 12-opportunity support reference. Query the saved 713-row
   context in SQLite and reconcile its eight population/fold groups with summary.
3. Native categorical bar chart: all 251 paired net-return changes, including
   zero and extreme values. Unequal-width bp bins are categorical counts, never
   a probability-density histogram. Query actual saved paired observations.
4. Tables and narrative cover executed exit pathways, avoided losses/missed wins,
   matched-case D versus matched-control D, incremental I, serial execution,
   statistical limitations, exact completed-hour availability and reproduction.

All narrative sections remain in the canonical artifact. Sources are local
frozen tables/receipts and original source URLs. No invented SQL, hand-built
interactive UI or image-only quantitative charts. Official portable validation
and packaging follow Markdown conversion. If no compatible browser is available,
report structural_only; do not claim mobile, browser or source-dialog QA.

## Methodology guardrails

No observations are trimmed, no extra gate is selected from these results, and
no Shapiro result changes the preregistered calendar-month block inference.
These repeatedly inspected 2023--2024 observations are development evidence,
not a fresh holdout. Missing matched controls remain unknown. Independent
saved-hour verification does not certify raw-price aggregation or executable
Pine behavior. The cost assumption remains the frozen 20 bp round trip.
