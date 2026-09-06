# V21 — External rank-pressure mean gate (frozen before prices)

## Objective and one contrast

Overall profitable BTC1h direct-K1 / lower-timeframe exit goal is active and
unmet. V20 is complete and rejected, not a pending run. Test only whether a
fixed external four-asset price-position mean improves the original251 BTC
intentions under unchanged V18 management. Do not conjunct V20 or preparedV19.
This is one entry participation variable, not a new exit, model, or parameter
grid. Broad owner research authorization is implemented only in this offline
experiment; it does not authorize production, threshold-preset or order changes.

## Source-driven definition

ChartPrime Multi Asset Histogram KkoxM97D, MPL2; original source preserved at
exp-chartprime-public-confluence-audit-20260906-v1/sources/KkoxM97D.pine with
SHA58d49892627a886094b269c7b9d7ac15ae9ba1c0844696fc0cd85ab7856b3ae5.
Native1h HL2, default50 comparisons. Each source score=sum(+1 if currentHL2
>= each lag1..50 HL2 else-1). Require51 continuous complete hours, not nz(NA)=0.
Ties add+1 as source; a flat sequence can therefore score+50, not trend proof.
Fixed external ETHUSDT/SOLUSDT/BNBUSDT/XRPUSDT BinanceUM futures; original
source-list's first four non-BTC entries with existing archive support, not
ranked on profitability. ETH/BNB/SOL/XRP ordering has no effect on equal mean.
Fixed surviving major-coin panel is not representative of all crypto; no claim
of survivorship-free universal selection or four independent risk factors.

For own BTC K1 openT and decision/entryT+1h, use each external last hourly bar
openT-1h, availableT. Window[T-51h,T-1h] has51 complete native hours. Exact
same-hour alignment; no asof, stale carry, forward-fill or incomplete current
K1-hour OHLC. This predeclared one-hour buffer avoids instantaneous cross-venue
availability assumptions but is NOT an audited live-feed latency budget.
Normalized breadth_score=mean(four raw scores)/50; all four known required.
Accept iff own direction*breadth_score>0; knownzero/opposite abstain0, any missing
unknownNaN. Mean is NOT a3-of4 majority vote. No percentile/score strength/MA/
ATR/volume/structure/age threshold. Original ten-asset chart runtime not cloned.

## Population, source bounds, clocks

Freeze old251case/462owncontrol requests,154triples and97unmatched from V4,
original identities, directions, own clocks and four2023--2024 halfyears.
Each control gets its OWN external context; no case direction transfer beyond
the already-frozen matching contract. No rematching or selection on outcome.
External physical files extend through2026-04-30; use timestamp-only preflight
and full byte SHA, then skiprows/nrows/usecols OHLCV from2022-12-29 to at most
latest ownK1open, always<2025-01-01. No later price parse-then-filter chunks.
Audit receipt hashes, archive hashes, bounds and reads recorded. Hourly complete
means12distinct UTC-aligned5m rows; invalid OHLCV fails rather than repairs.
Source gaps create unknown when51-hour support unavailable. No new fetch.
Do not read originalBTC price archive: V18 pinned exits retain originalOKX BTC
execution; external Binance is an explicitly declared input, not replacement.

## Outcome lock and comparison

Commit config/plan/builders/tests before reading any real external OHLCV.
Freeze713 contexts, long externalhourtrace,62support rows and154triples before
outcomes. Count support>=80accepted,>=12perhalf,>=12active months,>=3perhalf.
These are inherited support guards, not a new power calculation. If any fail,
stop before even opening/hashing saved outcomes. Do not lower gates afterward.
If pass, read SHA-pinned V18 independent episodes and check original baseline
matched and serial parity. Accepted episode oldfields identical; abstain=no
entry/no fee0; unknown=NaN and conservative72h reservation, not fakeexecution.
Recompute both arms' case and control serial selections independently.
Keep K1extreme stop,72h ceiling,20bp roundtrip cost, native15mSMA40 trueflip,
old profitable fast5m50% partial and failed-confirm2 branches EXACTLY unchanged.
No raw intrabar simulation or linear construction of a changed exit path.

Primary D=paired all-intention candidate−baseline; I=caseD−same154matched
controlD.97unmatched remain unknownI, not dropped from opportunity denominator.
Require trade netpositive, eachhalfyearnetpositive, positive D/I with positive
95%month-block lowerCI and one-sided month-signflip p<.01, coverage>=.9 and
independent verification before any candidate upgrade. Original154/251=.6135
coverage still fails and cannot be repaired by filtering to154. Report completed
trade quality, whole-opportunity known denominator, matched excess and serial
separately.9999draws seed20260906 inherited; repeated development is exploratory,
not random assignment, pristine OOS, or evidence of stable future profitability.
Do not pretend supports713 are713 independent market regimes.

## Failure analysis and limits

Report avoidedlosers AND sacrificedwinners, acceptedlosses by unchangedexit,
grossloss versus cost-flippedloss, long/short and halfyear without posthoc gates.
Mechanism groups/MFE/outcome classes are explanatory hindsight, not entryinputs.
AUC/topdecile ranking are not applicable to this deterministic accept/abstain
rule without a fitted ranker; use fixed matchedrandom entries as nullcontrol,
plus all fixed opportunity effects. Do not claim default50 or fourcoins optimal.
Old BTC15m ETHmulti-factor, ETH15m BTCstack and altcoin1dbreadth already existed;
exact rank50fourcoin+251directK1+V18 contrast differs, family is not new.

## Reproduction and delivery

After source commitment:
`.venv/bin/python -m pytest tests/test_hourly_impulse_breadth.py tests/test_hourly_impulse_breadth_accounting.py tests/test_hourly_impulse_breadth_research.py -q`
`.venv/bin/python -m yoyo.evaluation.hourly_impulse_breadth_research`
Preserve started/failure/source/freeze receipts; never overwrite a results dir.
Independent saved-hour reconstruction verifies formula, exact external clocks,
own contexts/support; distinguish that from raw-source/Pine/live verification.
Produce analysis markdown and immediate HTML; registry/HANDOFF reference real
results, negative evidence included. No dependency change, training, TV save,
ACTIVE/frozen change, deployment, forward mutation or orders. Overall goal stays
active unless stable positive independent evidence actually meets the full goal.
