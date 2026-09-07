# Validation Report — V28

## Overall assessment: share negative exploratory evidence

Source-first economic builder2c39bac froze288 mothers,one PCG64 seed20260907,
three controls each for284 supported mothers,4h primary and1/12/24h descriptive
horizons,20bp cost threshold and24-month inference before the actual run.
Coverage passed; economic continuation gates failed. No profitability claim.

## Calculation checks

-288 known primary own labels;283 complete paired labels. Four mothers lack
  support; one additional group has a control missing an internal native5m bar.
  All original mothers remain; no redraw, two-control average or unknown=zero.
-4h own gross mean−3.071467bp; cost-threshold mean−23.071467bp,
  month-bootstrap95% interval[−34.016765,−12.228428]bp,one-sidedp0.9999.
  Paired own−21.722737/background−18.843314/excess−2.879423bp,
  interval[−13.895981,+8.814398]bp,p0.6868. Denominators are not interchangeable.
- Fourhalf own means−29.501499/−21.990910/−5.136788/−33.401565bp.
 169 gross-negative,18 gross-positive but cost-nonpositive,101 cost-positive.
 24 own IQR outliers and21 excess outliers retained;7/24 positive months.
- Independent saved evidence audit reconstructed4560 labels,1152 pairs,
 852 seeded controls and24-month means/intervals/p-values;31 hashes and24
  builder sources verified. audit_complete.json passed atf308a82.
- Peer review found the original auditor checked derived returns but not each
  saved endpoint quote or diagnostic/decision linkage. The original receipt
  stays intact. A source-first stronger auditor and tamper regression tests
  now check stored endpoint quotes,diagnostic hash/order and summary decision.
  No frozen economic builder or result was rewritten.

## Source provenance and environment

Original reader checked the pinned archive hash before reading,not after.
Supplemental checker364f60c compared219551 pre2025 OPEN values with the
current pinned archive,bracketing that comparison with before/after hashes.
source_prefix_audit.json passed. This is post-run consistency evidence,not
proof that the original run observed an atomic source snapshot.
Physical archive341567 timestamp rows extends into2026; numerical price use
was limited to pre2025 OPEN. No2025+ prices materialized; no HLCV analysis.
This is not independent verification of exchange authenticity.

Actual run used existing systemPython3.9.6/NumPy2.0.2/pandas2.3.3/SciPy1.13.1;
version gates passed. Existing seaborn allowed the required diagnostic utility
without installing libraries or changing project dependency contracts.

## Methodology limitations

Fixed-clock signed price changes minus20bp are not executable K1-stop PnL.
No stop path,TP,funding,slippage,sizing or single-position conflict simulated.
Reported positive-label rate is not trading win rate. AUC/top-decile ranking
tests do not apply to this non-ranking experiment; old SMA is the reference
baseline,and contemporaneous matched controls test background advantage.
These are repeatedly used2023–2024 development observations,not independent
holdout. Monthly weak dependence and sign exchangeability are assumptions.
No cross-experiment selection correction or guaranteed power is claimed.
Holdout consumption0; no TV, production, deployment or real orders changed.

## Report QA

14 narrative sections,16 blocks,two native bar charts: chronological24-month
cost-threshold means and50bp full-support distribution. Source-backed SQLite
queries preserve sample counts,empty distribution bins and negative values.
Tables distinguish all-mother means from valid-pair means and retain all four
clock horizons. Official portable validation and package passed,structural_only.
Browser/source-dialog/mobile/light-dark rendering remains unverified because
no compatible installed Chromium was available; no browser was downloaded.
Original MD conversion,canonical manifest and actual delivery receipt retained.
