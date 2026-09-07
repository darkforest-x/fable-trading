# V29 — Source-default Trend Classifier support

## Question and single changed variable

At the completed hourly K1 decision,does the ChartPrime Trend Classifier's
direction agree with the frozen request,with enough temporal coverage for a
separate economic test? This adds exactly one numeric-state gate to original
SMA251 identities and V24's744 own-clock controls. It is NOT K1-open background,
not the rejected VWMA288 cohort,and not a changed entry/exit/cost/TP/SL preset.
Support-only: no new or existing outcome labels read,including V24 labels.

## Source and causal formula

AtJtdaDe Pine1.0,ChartPrime,MPL2.0,default center10 and amplitude100.
Center=SMA10(EMA10(close)); step=SMA100(EMA100(high-low)). Long means center
strictly rises and close>center+step. Short means center<=previous center and
close<center-step. Otherwise known neutral. Three strength tiers reduce to
crossing the first band for the state gate; they are not three independent votes.
Original sourceSHA0e425fb43caeda0638bb671ee8e944f61844205833fb52f0dcdd8f8728ebbddd.
https://www.tradingview.com/script/AtJtdaDe-Trend-Classifier-ChartPrime/

Use the state at K1 close E=signal_time+1h. The original diamond offset=-1
is display-only and never changes availability. No future pivots or MTF data.
EMA adjust=False seeds first observation per contiguous source segment;
each outer SMA needs its entire10/100 window. Full state first known on bar100.
Missing hours reset both recursions; malformed OHLC fails,missing/warmup stays
unknown. Native Pine startup/infinite history parity is NOT asserted.

## Distinctness audit before data calculation

V1 already searched SMA/EMA,length20/30/40/60,cross-count,efficiency,ordinary
volume,range/body/slope and prior-breakout conditions. V3 already tested slope,
prior4h and extension<=1.5. V20/V25 tested persistent/current structure;
V21/V22 external breadth. This candidate's exact two-stage smoothing and
smoothed-amplitude distance LOWER bound were not implemented in V1–V28.
It remains correlated price/MA/volatility information,not an independent market
factor or a sophisticated learned classifier. No picking from101 winning labels.

## Frozen data and design

Reuse SHA-locked V20 saved18222 native-hour OHLC,2022-11-30 16:00 through
2024-12-28 22:00 UTC,and V24 identities/allocation. Timestamp-only preflight
before prices,then exact own-clock joins. Read V4 only identity columns to
confirm251 original mothers;fourhalf counts55/66/55/75;248 immutable triples
and3 unsupported mothers. All995 context rows remain. Controls evaluate their
own K1,not the parent's state. No additional controls or randomization needed.
All2023–2024 data are reused development,not independent acceptance evidence.
The72h fold embargo,20bp cost and original management definitions stay fixed.

## Feasibility gates fixed before execution

Inherit V25's non-economic support contract: >=80 accepted cases,>=12 each
half,>=12 active months,>=3 months each half. Show known/unknown/abstain and
matched-triple support separately. Adequate coverage does not prove power,
profitability,precision or superiority to background. No lower gates after
seeing counts. If failed,report support insufficient without reading returns.

## Validation and outputs

Commit builder/config/tests/plan before real support run. Verify parent input
SHA/source chronology and classifier source pins before and after computation.
Save complete hourly trace,case/control contexts,62 count rows,251 matched
rows,started/frozen/summary receipts. Synthetic tests cover prefix invariance,
gap reset,boundary equality,flat-slope asymmetry,OHLC/clock integrity and own
control attachment. Independent stdlib auditor does not call feature helpers.
Archive every failure;do not overwrite successful output directories.

Report source MD immediately converted to HTML then one canonical portable
artifact. Technical audience,methods-first series;one monthly count chart with
all24 months and exact denominators. Browser limitations disclosed.
AUC,top-decile returns,profitability,p-values and execution win rate are not
applicable to support-only verification. Null comparator: original ungated
counts plus same-clock random background state rates; independent numerical
reconstruction and synthetic temporal/tamper tests challenge correctness.

## Non-actions and next boundary

No new training,dependency changes,holdout/2025+ price reads,TV/ACTIVE changes,
deployment or real orders. Passed support permits only a separately frozen
economic design,not automatic promotion or a claim that orange truly means chop.
