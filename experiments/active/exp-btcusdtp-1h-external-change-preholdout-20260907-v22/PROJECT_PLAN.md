# V22 — External historical-rank change, one frozen participation contrast

## Question and design

The overall profitable BTC1h direct-K1/lower-timeframe-exit goal remains unmet.
V21 static four-asset mean gate is completed/rejected. Test whether the SAME
four-asset historical-rank mean CHANGES in the intended direction before K1,
relative to original V18 participation. No V21 conjunction, new exits,
majority/strength gate, rank length or lag grid. One fixed zero sign threshold.
This is paired retrospective simulation with concurrent matched random entry
times, not randomized treatment assignment or independent market observations.

## Exact feature and availability

Same ChartPrime KkoxM97D MPL2 rank50 formula and ETHUSDT/SOLUSDT/BNBUSDT/XRPUSDT
panel as V21. Each rank compares native1h HL2 against previous50: >= adds1,
otherwise subtract1. Source default50 remains a hypothesis, not an optimum.
At own K1 OPEN T (entry T+1h), current external row OPEN T-1h is available T;
previous row OPEN T-2h is available T-1h. Both must have full continuous51-hour
windows in the same segment. Union is52 complete hours OPEN T-52h..T-1h.
Exact adjacent UTC hours; no asof/forward-fill, no current K1-hour data.

sum_change = sum(current four integer ranks)-sum(previous four integer ranks).
delta=sum_change/200, range[-2,2]; direction*sum_change>0 admits, knownzero or
opposite abstains. Allfour paired assets required; missing any means unknown
NaN, not zero. Preserve all eight ranks and both clocks/window/counts. All
aggregate means/delta/score areNaN when pair support incomplete. Positive
rescaling breadth_score=sum_change/400 in[-1,1] lets unchanged sign bookkeeping
operate; explicitly NOT V21 absolute mean. Integer subtraction avoids numerical
cancellation gates. No clipping, rounding, amplitude filtering or price return
substitution. Validate diagnostics before cached outcomes are accessed.

Rank change includes rolling-window membership effects: constant current
price can still change rank when an old comparison exits. It is historical
relative-position score change, not measured flow or direct startup proof.
Four correlated major assets are not four independent risk sources.

## Prior-family check, not a novelty claim

scripts/research_altcoin_1d_k1k2_market_context.py:394-425 already defines
price/EMA13/SMA34 trend breadth and breadth-minus-shift5. Registered V3 config
contains change5 candidate thresholds; this was not a never-tested family.
scripts/research_btcusdtp_15m_multifactor_confluence.py:384-408 already computes
ETH return4/16/96, MA, RSI and ADX; scripts/research_ethusdtp_15m_causal_confluence_v17.py:429
already uses externalBTC15m/1h MA alignment. V22's exact fourcoin rank50 adjacent
hour difference at ownK1open was not found in that reviewed source. No old
daily threshold, old outcomes, or fitted feature weights are transferred.

## Population, fixed exits and evidence gates

Retain original251 BTC directK1,462 OWN-time random controls,154triples and97
unmatched, frozen V4 identities/directions/four2023--2024 halfyears. No pool
rematching, selected screenshots, current winners ranking or new price period.
Rank data comes ONLY from V21 saved pre2025 hourlytrace, SHA
870e898c0db830ad7c724bb93726f89b6842e6eb7462b3eac1c56bba03e853e6;
its pre-outcome freeze receipt SHA
bae01b79e34a0782598e18a9197db1853492fe6f04cb92d0b992fb4015700403.
Verify these bytes, then timestamp-only symbol/open_time before hourly OHLC
materialization. This reuses native-hour aggregation/ranks, NOT new raw5
aggregation, live feeds or exchange source authenticity verification.

Commit source/config/plan/tests BEFORE feature materialization. Freeze713 own
contexts plus support62/matched154 rows before any V18 outcome read/hash.
Inherited supports>=80accepted,>=12perhalf,>=12active months,>=3months/half.
If failed, save support evidence and STOP before outcomes, no threshold rescue.
These supports are not a newly calculated statistical power guarantee.

If supported, exactly reuse V18 original OKX BTC cached episodes: K1 extreme
hardstop,72h,20bp; true native15mSMA40 color transition, existing profitable
fast5m50% realization and original failed-confirm2 full-exit branches. No raw
intrabar replay, linearized exit changes or source substitution. Accepted old
episode fields exact; abstain=no entry/no fee/zero, unknownNaN conservative72h
serial reservation. Recompute both arms' single-position selections.

Primary all251-opportunity D=candidate-baseline; I=same154caseD-controlD.
97unmatched I remain unknown, never discarded or filledzero. Compare completed
trade mean, whole-known-opportunity mean, matched excess and serial separately.
Inherited9999draws seed20260906, month-block bootstrap/signflip. Require net
positive overall/eachhalf, positive D/I lower95%CI and p<.01, matching>=.9,
independent verification and genuinely independent chronological evidence
before adoption. Current154/251 coverage still fails; no lower threshold.
Repeatedly reused development and many prior candidates mean exploratory,
not untouchedOOS or family-adjusted proof. No profitable-system completion
claim can follow one positive developer-cohort result alone.

## Diagnostics, QA and delivery

If outcomes unlocked, report missed winners AND avoided losers, accepted losses
by existing actual exit, grossloss versus cost-flippedloss, direction/halfyear,
concentration without deleting winners. Outcome-conditioned labels explain
history only; never feed them into entries. Independent saved-hour verifier
must reconstruct ranks/windows/two clocks/delta/support, distinct from raw
source, Pine and economic verification. Existing sign-bookkeeping tests alone
do not certify the new feature. No trained ranker: AUC/topdecile are inapplicable;
fixed matchedrandom and originalV18 are the null/baseline comparisons instead.

Reproduce after source commit:
`.venv/bin/python -m pytest tests/test_hourly_impulse_breadth_change.py tests/test_hourly_impulse_breadth_change_research.py tests/test_hourly_impulse_breadth_accounting.py -q`
`.venv/bin/python -m yoyo.evaluation.hourly_impulse_breadth_change_research`
Refuse any existing resultsdirectory; preserve failures and timing receipts.
ReportMD then immediateHTML, fullnegative findings, registries and HANDOFF.
No holdout,2025+prices,package installation,training,TV,ACTIVE,deploy or orders.
