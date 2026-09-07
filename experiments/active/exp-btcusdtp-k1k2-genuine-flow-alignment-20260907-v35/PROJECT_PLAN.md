# K1/K2 Flow Clocks · V35

## Frozen before materialization

Continue the active profitability goal, but this step only verifies whether real
taker flow can be attached to the existing hourly K1/K2 decision clocks. This is
not a new profitable strategy or a new threshold search. Start with V4's original
251 hourly K1 mother cohort and its pre-wait matched controls; do not label it
the complete market-wide K1/K2 universe. V4 used SMA40 and first K2 gap1..8; these
historical settings are unchanged, not declarations of optimal parameters.

## Sources and exclusions

Hash lock six V4 CSVs and V34's summary, source manifest and full verification.
Read CSV usecols identity/direction/fold/clock fields only. Waiting terminal
statuses are outcome-free pattern metadata, but they occur AFTER K1: save them
separately and NEVER attach them as K1-time features or filter the K1 roster.
Requests are not executed trades. The generic request signal_time was rewritten
to K2 while signal_OHLC still refers to K1: use mother_* and k2_time explicitly.

V34 supplies only the exact 24 verified 2023–2024 Binance USD-M BTCUSDT5m files.
It is external confluence input for OKX-defined events, not a replacement price
or execution venue. No raw OHLC columns, 2025+ opens, fills, stop/target outcomes,
P&L, MFE, MAE, or old acceptance period are loaded. No downloads. Source hash
drift is a failure, not permission to rerun another source or alter old evidence.

## Single change: clock join

Each natural event window is [start,end) over 5m opens. K1 window is its native
hour at K1 close for ALL mothers. K2 native hour and K1-close-through-K2-close
waiting interval exist only at K2 close, for every original emitted request.
The latter includes K2; it is NOT the between-only period and is never backdated.
Window state uses exact calendar coverage, not row counts alone or forward fill.
Imbalance = sum(delta_quote_volume)/sum(quote_volume), then multiply by original
direction for orientation. This is taker-side trade amount, not OI or capital flow.

Known zero activity remains a known bar; wholly zero windows have undefined
imbalance. Missing or not-yet-delivered bars yield undefined whole-window amounts
and imbalance. Mixed known zero bars are counted but need not invalidate a
positive-total fully covered window. No threshold, optimization or ranking here.

Compare historical theoretical complete boundaries <= decision with a separate
observed-delivery mode. With no observed arrival stamps, the latter must remain
unavailable. Historical archive completeness is not live delivery evidence.

## Tests before real data

Independent deterministic pytest property cases (Hypothesis absent, no install):
future edit/append invariance, prefix/full equivalence, weighted aggregate oracle,
missing edge/middle, duplicate/shifted clocks, exact boundary, zero denominator,
late/unknown delivery, immutable inputs. Root tests verify event identity and
status clock contracts and confirm that future statuses never filter K1 windows.
After materialization, replay all events at eight quarter cutoffs exactly.
These adversarial null controls replace economic random controls for this
non-economic audit. No AUC, profit p, win rate, top-decile return applies.

## Deliverables and audience contract

Use data quality, source-driven development, property-based-testing guidance,
build-report and visualize-data; extract-approach records clock-semantic insights.
Product stakeholders; one portable HTML with Title, Executive Summary, window
definitions, original-mother status distribution, alignment findings, recommended
next step, further questions, risks and honest limits. Reproduction appendix is
required by owner. One full-width native categorical bar chart for CASE terminal
statuses, zero baseline, blue-root single measure, direct category labels; retain
counts/denominator/share in chart source. Control absolute count is not comparable
to cases due to three controls per mother, so show exact context in supporting
table instead. No profit visual or sign-distribution selection. A notebook checks
saved counts and hashes; missing Jupyter/browser validation is disclosed.

## Reproduction

Commit exact builder/config/plan before first real materialization:

```bash
.venv/bin/python -m pytest -q tests/test_k1k2_genuine_flow_alignment.py tests/test_k1k2_genuine_flow_audit.py
.venv/bin/python -m yoyo.evaluation.k1k2_genuine_flow_audit
```

Output data directory and summary are one-shot and never overwrite V34/V4.
Registry entry is active before run, then input-only acceptance may be recorded.
Overall goal stays active: no successful net strategy has yet been established.

## Next decision, not yet an economic run

Once clocks pass, freeze ONE causal flow hypothesis before outcome access and
keep existing costs/exits/matched mothers for its contrast. Do not search several
thresholds on known winners, substitute terminal 4h marks for pathwise exits, or
declare data quality a profitability result. New barrier/cost/preset changes and
any holdout use remain subject to owner authorization. No deployment or orders.
