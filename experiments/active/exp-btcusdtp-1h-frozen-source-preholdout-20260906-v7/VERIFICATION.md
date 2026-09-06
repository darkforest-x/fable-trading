# V7 saved-ledger verification

2026-09-06. Post-run independent read-only review and root recalculation, not a
second raw-price replay, prospective evidence, preregistration or new selection.

## Lineage and stage order

- Builder `ad8f5c816ef01d426f57d3b7281313628381795f` committed
  2026-09-06 12:46:36+08:00; started12:46:44.357199; outcomes12:46:47.960706.
- All18 frozen sources agree across current bytes, committed bytes, started
  receipt and summary. ConfigSHA256
  `0352432f3667494f07b64fd5a8da30009f32deb89d80fab5f85322ac29ab1d83`.
- Outcome gateway's support/case/control request hashes agree with saved bytes.
  Support-stage `pnl_computed=false` and completed-summary `outcomes_computed=true`
  describe two different stages. Support passed before any P/L evaluation.
- First development price materialization,219551rows through2024-12-31 23:55UTC.
  No2025+prices, noholdout>=2026-05-04; this configuration holdout use0.
- Summary19821bytes SHA256
  `8303b6a3fc18f5d6e12e91b95a331717c7306ce90b3be3ddcfca54db309b4984`.

## Requests, sources and controls

- 959zone IDs unique and completely observed:187expired,486first-release
  unqualified,286request_emitted. Of772first releases,286qualify(37.05%).
- The486 first-failure codes are263close_location,222shape,1ATR/range unavailable.
  These are ordered first failures, not independent marginal filter counts.
  No subsequent prices of rejected releases were inspected to claim missed wins.
- 673known nonentry zeros plus286trade returns reconstruct the zone ledger;
  no missing outcome filled zero. Zone mean-5.304810bp is not per-trade mean.
- No new source starts before the prior causal terminal; independent arm→exit
  single-pending selection reproduces all959selected zones and286trades.
- 283/286=98.951049% requests match exactly3 controls,849total. Allkeys, current
  risk/ATR transfer, inherited direction and case-time exclusion verified.
  Control times never reused. Three unpaired cases mean-53.152575bp remain in
  all286strategy results. No hourly MA-side/slope/source-success match filter.
- All1135case/control executions close, no rejected/censored outcomes. Real
  entry/exit clocks,20bp cost,K1extreme stop and72h cap independently pass.
  Transition exits have a contiguous observed5m aligned→opposite edge.

## Exact economic and failure aggregates

- Cases286:72wins214losses; gross+2.212192772bp,net-17.787807228bp,
  PF0.608716750,winrate25.174825%. Serial identical, not compounded equity.
- Allcase net by halfyear:n69/-26.282025,n55/-25.188519,n78/+5.179308,
  n84/-27.291317bp. Exact-three matches byfold66/55/78/84.
- Matched-request CSV retains all286rows, including3unmatched rows with
  unknown control/excess fields. Paired283case net-17.412915697/control-22.455271256,
  excess+5.042355559bp,median-6.027884510,SD104.406094869,
  CI95[-3.871037215,14.019316707],month p=.1514.
- Net meanCI95[-28.550134303,-7.553765760],month p=.9981.
  Both one-sided positive-effect tests,9999draws/seed20260906, exploratory.
- Extra10bp mean-27.787807228; removing largest2winners-21.348961123.
  Largest2 comprise12.32948% of all positive profits, not of negative total.
- All286entry states aligned, no management-chain reset; earliest exit10min,
  zero5min exits. Medianhold135min,mean156.3636min. Seventeen<=30min alllose:
  sevenhardstop/tenflip. Fastflip is10/263=3.80% of all colour exits.
- Outcomes:23hardstop mean-122.637468bp;263transition exits mean-8.618445bp,
  including191losses. All23hardstops lose. No72h timeout.
- Mutually exclusive loser taxonomy,precedence hardstop/costflip/giveback/early:
  23hardstop(-122.637468bp),23costflip(-11.598226),14giveback(-32.183562),
  10early(-69.104532),144other(-60.920663). Counts sum214.
- Their shares of total negative-return mass are21.69/2.05/3.47/5.32/67.47%.
  These are not shares of net total after winners, and do not establish causes.
- 191/214losses already nonpositive before fees;23fee flips(10.75%oflosses).
  The tiny positive aggregate gross return still cannot cover20bp costs.
- 186/214losses(86.92%) never reach1R during actual holding;137/214(64.02%)
  never reach.5R. LosingMFE median.3414R. Recorded MFE is not future after exit.
- 28losers reached1R:13costflips+1hardstop+14puregiveback. They are13.08% of
  losses,31.46% of89all trades reaching1R. Do not add overlapping flags to taxonomy.
- Netdistribution:SD106.418806bp,median-36.658904,IQR64.340108,
  p5/p95=-119.176949/+218.929928,min/max=-369.780484/+526.397993.
  No outliers removed. SciPyShapiroW=.7927927001,p=8.8601967e-19.
- Net/excess monthmeanlag1ACF=.343695187/.470981295. Monthly clustering is an
  approximation, not a claim of independent months or guaranteed coverage.
  Reused history means unadjusted exploratory p-values cannot confirm a new edge.
- Frozen range/ATR singlefeature AUC=.585345275; topdecile gross-18.504953bp,
  net-38.504953bp. No model training or modelvalAUC applicable.

## Recalculation from saved results

The main frozen driver defines all outcome/matching/inference tables. For a
read-only core numerical check without any raw-price load:

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
from pathlib import Path
import pandas as pd
from scipy import stats
p=Path('experiments/active/exp-btcusdtp-1h-frozen-source-preholdout-20260906-v7/results')
c=pd.read_csv(p/'case_trades.csv.gz')
r=pd.read_csv(p/'control_trades.csv.gz')
m=pd.read_csv(p/'matched_request_outcomes.csv')
assert len(c)==286 and c.closed.all() and len(r)==849 and r.closed.all()
assert len(m)==286
matched=m.loc[m.excess.notna()].copy()
assert len(matched)==283 and matched.assigned_controls.eq(3).all()
assert not r.entry_time.duplicated().any()
print(c.net_return.mul(10000).describe(percentiles=[.05,.25,.5,.75,.95]))
print('PF',c.loc[c.net_return>0,'net_return'].sum()/-c.loc[c.net_return<0,'net_return'].sum())
print('means bp',matched[['event_net_return','control_mean_return','excess']].mean()*10000)
print('Shapiro',stats.shapiro(c.net_return))
print(pd.read_csv(p/'diagnosis_loss_taxonomy.csv'))
PY
```

No assumption of Gaussian/IID trades was introduced. Formal normality check
describes shape, not a selector for another significance test. The planned
month bootstrap/signflip remains unaltered; no power-from-observed-p claim.

## Limits and decision

This verifies saved ledgers and implementation contracts, not independent
exchange execution or future profit. No raw-price replay or profit optimization
was done by reviewers. Static source failure rates cannot label rejected
releases as missed profitable trends. Strategy+management still negative;
only fixed-request/fixed-control exit comparison is a next hypothesis, not a
positive candidate. No TradingView, model, deployment or live account changes.
