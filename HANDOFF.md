## 2026-10-29 · After a 25-coin spike alarm, buying every later touch and holding works in sample; the close-time score exit does not

- `exp-monday-limit-alarm-entry-20261029-v1` (builder db5f169c42; the first launch built wrong windows from a pandas-3 unit change and was stopped at once, outputs in `failed_units/`): 129 alarms 2021-2026, entries at the level for every same-side touch after the alarm bar.
- Hold all, near edge -30 percent, 30-minute window, frac N20 3x: 2146 trades +80bp, 3.70x DD17 since 2021, every period positive (2025 1.31x, 2026 1.23x); control outside alarms -6bp 0.33x. The owner's exit at the touch close when the score < 0.5 closes 97 percent of alarm touches at -25bp vs +70bp held (1.41x).
- In sample (threshold 25 picked 10-27 on the same data), ~100 episodes, mostly longs; live needs orders on all untouched same-side levels within seconds of the alarm. Candidate for a forward paper account (owner decision). Learning: `a-market-wide-alarm-selects-touches-the-score-rejects.md`.

## 2026-10-29 · Owner's exact spike403.21percent baseline comparison

- Owner corrected the comparator: compare with screenshot+403.21percent, not the current paper-rule proxy+2.30percent. `exp-spike-booklet-close-compare-20261029-v1` reproduces every original booked settlement and metrics:1000→5032.0987915U,166funded,83.1325percentwin,21.1887percentDD,PF2.44995.
- Reuse BE0P18/B18 close-entry native fills with the exact same10slot5x/19percentMAE allocator,seed20261102 and stable symbol/time ordering:1000→1250.9170341U,+25.091703percent,106funded,77.3585percentwin,32.9479percentDD,PF1.19459. This differs slightly from the earlierBE17+25.41percent; do not mix them.219original signals,113already close past target.68ofthose were funded in the old curve;their old recorded2787.59U PnL is descriptive,not a linear attribution because allocation/compounding changes.
- Original nativeBE0 means and212per-path entry/exit-window/price checks pass. No new native execution/download/fitting; merely project saved fills through the original allocator. Original margin/mark-to-market/funding/close-latency limits and baseline information leakage remain. Learning:`a-displayed-baseline-is-a-specific-ledger.md`.

## 2026-10-29 · Spike after the completed touch5m close remains a research candidate

- Owner explicitly reopened scoped spike live-execution research. Rejecting a score-informed pre-touch limit does not reject a score available after the touch close. `exp-spike-close-execution-20261029-v1` freezes score>=0.6/order>=3/near-edge/P18/BE17/fill-derivedH1; no parameter search, account or execution-service changes.
- 219signals/181coins throughSeptember2026;113already close beyond target. Immediate market106fills averages+90.27bp after fees+10bp market/non-target slip;2025 n42,-64.88bp and2026 n36,+216.82bp. Favorable-market/rebound-retest70fills+87.67bp;wait another5m63fills+49.30bp. All219 opportunity means+43.69/+28.02/+14.18bp. Retest67rebounded opportunities yields31conservative fills averaging-45.43bp;5mwait loses40targets and3backstops. Both declared paths preserved; no-fill=0 in opportunity comparisons.
- HEMI2026-09-02:1.618=0.01115564,11:50Beijing close0.011607,target0.01364;close-entry net+17.3418percent,11:55entry net+15.9218percent,retestunfilled. ExcludingHEMI immediate full-history mean remains+74.62bp;excludingbest3+45.27bp. Win77.36percent but winnermean+3.66percent/losermean-8.52percent,so high win rate does not resolve tail losses.
- Conditional matched controls:93events/244randomdecisions/56weeks,signal+100.34bp/random+46.36bp,week-sign-p0.1718;2026 matched34excess+189.97bp,papproximately0.05,bootstrapCIcrosses0. Historical rules and2026already observed. Small-chase0–25percent group19trades+246.88bp is descriptive evidence only,not an approved or independently validated filter.
- Original level anchors matter:48/106closeentries stopdistance>=19percent,24>=20percent;HEMI21.19percent. BE17 can sit below a chased entry. The old10slot5x account projection(full+25.41percent,2025-22.98percent,2026+36.48percent,realizedDD32.83percent) is a reference,not an exchange-margin/MTM forecast. Live candidate must read fresh quotes after finalized5m,price-cap entries,check remainingroom and realmargin; noautomaticdeployment/positionsizingchange.
- Builder`efadc6a7d6` committed before native execution;825neworders perpath,145cachedrows perpath.1431newpositions/2862fills reconciled,max2.96e-12bp;11focusedtests. Rawfills/cancelreceipts/sourcepath-size-mtime retained,nohash/download/replay. `reproduce.sh` resumes cached outputs. Notion4088856479af8102a9c1ee1d9bbfe18a;three learning notes cover observation/execution clocks,priceanchors and small-cluster permutations.

## 2026-10-29 · allin3 touch-close market or delayed limit

- Owner specified score >=0.6 at the completed touch5m close: favorable close enters market; rebound rests at1.618 until opposite target first or original week expiry. Owner explicitly chose multiple waiting levels, first fill cancels others, one full-equity3x position. Current BE17/P30/H1/target unchanged; no paper engine or production changes.
- `exp-allin3-close-hybrid-20261029-v1`:386symbols, both native paths complete,777eligible score events/737admitted/548independent fills(185cached markets,363limits). With real2/5bp fees and original10bp market/non-target slip: first seed315/316funded, ending5939.78/7424.77U, net refills5586.82U, trading-647.04/+837.95U, realized DD99.7466percent. Last open trade marked at data end contributes1767.51/2209.39U; without that mark realized profit is-2414.54/-1371.45U.
- Fragility is material: ten fixed same-time tie seeds yield net-649.62..43902.16U across both paths. Fees-only yields60681/77506U net, but last refill shifts fromMarch31 toJune11, changing later principal; do not call it robust profits or a linear cost effect. Same funded IDs across cost cases checked.584complete conditional random groups: signal-29.53/-28.11bp per opportunity vs random+21.93/+22.02bp,p0.178/0.185;2026 remains negative excess. Cash overlay caps loss at margin; not native exchange margin/funding/MTM, zero-latency peer cancellation assumed.
- Builder committed first; only new native orders replayed, no second engine.3656new positions fee-reconciled,max3.64e-12bp;82focused and77boundary checks pass. Early empty-cache and optional market-time failures retained; market time resolved only after native fill-index assertion. Notion experiment4088856479af815d88ddf57c8b4837d2. Learnings: pending-order cancellation, idle-state skipping, refill-threshold sensitivity and optional timestamp contracts in `docs/learnings/`.

## 2026-10-29 · Seconds-level entries (Binance aggTrades) do not help

- `exp-monday-limit-tick-entry-20261029-v1` (+ framework fills `exp-monday-limit-tick-accounts-20261029-v1`): last five months, frozen score at the instant of the first trade through the level, market fill after 100 / 300 / 1000 ms. Latency costs a median 4bp (p90 44-57bp) plus the taker fee; the bigger problem is selection: the instant score passes fewer and weaker touches than the resting causal score (v3 +128bp vs +205bp per trade even with no latency).
- Resting plus instant adds ~5 trades and lowers every account (v3 1.64x vs 1.70x, v3add20 2.68x vs 2.76x); spike instant market loses. The paper engine's resting causal plan stays the best executable variant. Learning: `the-touch-instant-knows-less-than-the-bar-before.md`.

## 2026-10-29 · Qualification on the 1m strategy account verdict

- Explaining the prior result exposed an important accounting boundary: native Nautilus trade fills feed `monday_limit_return_opt.frac`, a separate simplified margin overlay. It uses realized equity/10 at3x, an MAE threshold of32.333percent for liquidation, applies the loss and releases the slot at the original exit timestamp, and measures drawdown only on realized exits. This is not native timestamped liquidation or mark-to-market account validation. The direction/magnitude of its bias has not been measured; do not state the0.612x result as a reliable real-account forecast.
- The saved162-combo selection still fails under its documented assumptions;59 combos have positive independent mean returns. The model label is near-edge net-positive classification, not far-target return or account risk. A documented example(top10percent/noEMA/opposite1618/P18) has1631 trades,+72.64bp mean but95.6percent of summed net in2025H2, and0.551x in the simplified account. These facts identify target/accounting mismatches, not proof that lower leverage or a new label will fix it. Before further optimization, validate the account semantics; no new replay/training or production change made here.
- Raw table correction: simulated liquidation medians range12–89 across all162 combos, not12–25 as the earlier prose states. Learning: `native-fills-do-not-validate-a-margin-overlay.md`.

## 2026-10-29 · Single5m wick risk filter remains unproven

- Owner approved the proposed natural risk-distance filter. `exp-5m-wick-risk-filter-20261029-v1` keeps short close entry, exact needle-high stop, far internal0.618 target, and maker2bp/taker5bp. Frozen risk distance >=63.621485488253036bp; first-touch/reclaim/volume assessed separately without combinations. Existing native outcomes only, zero replay or new trades.
- Candidate:444 completed+1censored, all-history mean-0.203R.2025 n50,+0.051R after fees but-0.133R under10bp/market-fill stress.2026 n26,+0.459R/stress+0.238R; matched24 signal+0.588R vs random-0.610R,p0.136. Removing XRP gives-0.069R/entry; removing week2026-01-12 gives-0.016R.2026 absolute mean week-bootstrap95[-0.721,1.888]R. No stable edge established; no account-sizing or combined-filter stage.
- Threshold is pre2025 but feature was selected after seeing2026, so this remains retrospective.81 saved numeric comparisons,2 focused admission/period tests and1687-row exact ledger projection check pass. No production, training, paper-engine or service changes. Learning: `multiple-winning-symbols-can-share-one-profitable-week.md`. Notion72 receives the follow-up findings; original failures remain preserved.

## 2026-10-29 · 1m strategy design fails its pre-registered 2025 selection

- `exp-monday-limit-1m-design-20261029-v1`: 1m model trained before 2025 (2025-to-date AUC 0.688 vs 0.648 base), resting limits, 6 targets x 3 stops x 3 cuts x 3 EMA order filters, frac 10 slots 3x. On 2025 no combo qualifies (best 0.61x, all drawdowns > 50 percent, 12-25 liquidations each), so nothing was tested on 2026. Selection rule unchanged; the full 2025 table is in summary.json (the first summary is kept as summary_first.*).
- Agreed stop rule applies: no further Monday 1.618 variants; forward paper data is the remaining test. Learning: `a-failed-pre-registered-selection-is-the-result.md`.

## 2026-10-29 · Notion72 single5m wick: examples found, standalone rule loses after fees

- Owner confirmed close entry, stop at the signal candle extreme, target at the far internal0.618. `exp-5m-single-wick-1618-majors-20261029-v1`: existing12-major Binance histories; six real1m and six5m, NY Monday anchors. One native historical run,43530 independent signal/control positions,87060 fills,zero dual-touch ambiguities. Maker2bp/taker5bp; slippage stress separate.
- Requested upper-wick shorts:5415 candidates,205 targets,2 censored;134 targets have no later5m retouch through the full exit candle,39 also are the first weekly touch. These strict path tags are outcomes, never entry filters. All first-weekly shorts:782 candidates/53targets,6.79percent target rate. Full short rule mean-1.074R; first-weekly mean-0.617R. Matched random full-history mean-1.627R versus matched signals-1.094R;2026 short excess p0.0616. Independent risk units, not a funded account.
- Feature thresholds/orientations from pre2025 only. Natural short risk distance>=0.636percent:2026 n26,6targets,+0.459R after fees(+0.238R under10bp/market-fill stress),but paired p0.136 and pre2025 negative. Deeper reclaim helps; long upper-wick fraction alone has2026 AUC0.549.48 AUC oracle checks and real DOGE future-mutation check pass. No fit/promote/production changes.
- Original DOGE reference: Binance5m Sept30 21:30 Shanghai high0.09815,close0.09745,upper1.618=0.097892,target0.092948. At data end Oct1 08:00 Shanghai it is unresolved,excluded from completed performance; original OKX venue not independently reproduced. Raw ledgers,205 short targets,134 pristine/39 first-weekly examples and annotated win/loss charts retained locally. Summary/feature/source receipts registered. Initial chart-audit failure from selecting the censored reference out of the closed subset is retained and corrected.
- Learnings: `single-wick-admission-must-not-require-future-no-retouch.md`, `paired-controls-must-share-evaluation-cutoff.md`, `tight-wick-stops-magnify-fees-in-risk-units.md`.

## 2026-10-29 · Range Fanshen V2.40 wick circles

- Private Pine `周一高低点区间＋翻身 · V2.40` saved on the existing SOLUSDT.P 12h layout. Based on V2.37: closed-bar upper/lower 1.618 wick touches use narrow hollow green ellipses (`#00B43B` sampled from Owner image), default on with size and visibility controls. Body touches excluded; Monday range/timezone and original signal/trade modules preserved.
- Defaults match the observed Owner chart: Monday price labels off, historical Fib ratios `1.618`, historical lines white. Native off/on verified, oscillator pane collapsed, editor/logs closed and layout saved; old private script restored/preserved. Ten local checks and twelve native fixture groups passed. No browser session, service restart or trade changes.
- Source: `yoyo/evaluation/pine/monday_range_v2_40.pine`; receipt: `artifacts/range-wicks-v2-40-20261029/delivery.json`; raw native QA: `output/playwright/range-wicks-20261029/`. Learning: `pine-wick-rings-need-screen-sized-glyphs.md`.

## 2026-10-29 · K-line rendering and layout work reduced without changing the layout

- Owner clarified: performance only, preserve the existing layout. No CSS or visual structure changes. Stitch GEMINI_3_8_FLASH edit timed out; known completed source was missing from list/get_project, and the isolated browser was not signed in. Recovery evidence and the explicit scope decision are retained; no new design was claimed or applied.
- OHLC and six-MA readouts retain their DOM nodes and update only changed fields. Drawing tool chrome skips unchanged state on candle ticks. Paper-chart interaction options initialize once per instance. Layout-height observers reuse measured border boxes, collect reads before writes, and skip unchanged CSS values.
- Display-only MA state is cached immediately before the old tip. Repeated tip replacements are recalculated once; time/close prefix changes, pruning, prepends and period changes rebuild the state. Fresh OHLC, volume and confirmation fields are never cached as indicator data. Long-history fill markers use binary lookup followed by an exact interval check, preserving gaps and boundary behavior.
- Same desktop benchmark: 180 repeated same-candle readouts went from 180 legend subtree replacements / 46.9ms to zero / 0.2ms; 60-step pan layout count 68 to 13. Synthetic 20,000-row live-tip benchmark median full MA calculation 52.1ms vs cached 6.85ms. These are local samples, not universal FPS or latency guarantees. Raw benchmarks and reproduction scripts: `output/playwright/chart-performance-20261029/`.
- Validation: 100 targeted Node tests and 6 asset-delivery tests pass. Public Chrome 24 and iPhone-sized WebKit 23 checks pass: stable DOM with exact OHLC/MA, desktop pan/zoom, touch drawings, period changes preserving the chart, focus gestures, light/dark 1440/1024/390/320 layouts and drawer bounds. No page errors or API writes. Mobile range shift tested via chart API; no physical iPhone performance claim. Two mobile harness errors (same-document initialization and pre-frame range read) are retained and corrected.
- Both task browsers closed; all 16 recorded processes exited. No service restart. Paper worker alive/queue 0/no error, fib stream connected, patrol heartbeat current with four enabled/error-free schedules. Unrelated backend/archive changes remain untouched. Learning: `chart-performance-caches-must-track-input-fields.md`.

## 2026-10-29 · 1m entry score: better ranking, worse paper accounts out of sample

- 1m features (`exp-monday-limit-1m-features-20261028-v1`, 70010 / 70020 events) and fit (`exp-monday-limit-score-1m-fit-20261028-v1`, train <2026): 2026 AUC 0.702 vs 0.661 causal 5m base, top decile +45.8bp vs +21.4bp, gain far above shuffled-feature refits; leak check passed (decision bar never reaches the level). Strongest feature: distance to the level one minute before the touch.
- Paper accounts resting on the 1m score (`exp-monday-limit-paper-accounts-1m-20261028-v1`): much higher multiples in 2020-2025 (training period: v3add20 20.4x, v3add 14.8x) but in 2026 every account is worse than with the frozen causal score (v3add20 0.68x vs 1.33x, spike 0.68x vs 1.19x, v3 0.42x vs 0.65x). Do not switch the paper engine to it. Model files stay in the experiment directories; frozen model and paper engine unchanged.

## 2026-10-28 · A-share Monday 1.618 private Pine V1.1

- Saved `周一高低点 · 1.618 A股 V1.1` in native TradingView and on the existing SSE:601212 12h layout. New source `yoyo/evaluation/pine/monday_high_low_1618_ashare_v1_1.pine` (c0a9c6d303); original V1.0 source/private script preserved and hidden on this chart.
- Shanghai calendar; final range comes from confirmed Monday daily OHLC including auctions. Closed minute bars supply the developing preview and matching extrema anchors; unlocated daily extrema explicitly show 日线定位. Monday holidays do not borrow Tuesday. Historical weekly segments remain.
- Twenty native fixtures passed. SSE:601212 1m/3m/5m/12h/1D agree at H4.96/L4.77, extensions5.07742/4.65258; SZSE:002714 30m/12h agree at H42.18/L40.92, extensions42.95868/40.14132. Initial strict minute continuity failed on native11:31/15:01 closes and missing auction extrema; failed evidence retained. Live forming Monday/Bar Replay not separately tested; no profitability claim.
- Receipt: `artifacts/monday-high-low-1618-ashare-pine-20261028/delivery.json`; raw native QA: `output/playwright/monday-1618-ashare-20261028/`. Existing app reused, no browser to clean, services and patrol heartbeat retained. Learning: `ashare-daily-extrema-cannot-be-rebuilt-from-minute-bars-alone.md`.

## 2026-10-28 · Eight paper accounts with their full rules, resting vs close-time entries

- `exp-monday-limit-paper-accounts-20261028-v1` (adapter + builder cd5f85adcf): breakeven +0.17 percent (v3lock +0.5), add legs, each account's sizing; leaky / resting on the causal score / close inside / close both sides.
- From 2020, resting on the causal score (what the paper engine does): v2_60 1.36x DD55, v2_5 0.10x, v3 1.04x DD86, v3add 1.05x DD94, v3add20 6.6x DD83, v3lock 1.13x DD85, spike 1.02x DD61; allin3 100x from 9.5 percent of starts. Leaky backtests 15x / 2.4x / 89x / 203x / 328x / 91x / 4.7x / 95 percent.
- Close-time entries cut drawdown for the v3 family and spike (v3 2.53x DD60, spike 1.25x DD33) but lose for v2 and in 2026. 2026: only spike (1.19x resting, 1.36x close) and v3add20 resting (1.33x) are above 1. In sample.

## 2026-10-28 · Market entry at the touch close (inside / beyond) and target choice

- `exp-monday-limit-close-reclaim-20261028-v1` (builder eb6dfe553e): market at the touch 5m close scored by the touch-bar score, five targets, -30 / -18 percent from the level, matched random entries. v3-like frac N20 3x from 2020: inside only 1.78x DD44, inside + beyond 3.39x DD49 (resting plan A 3.87x); 2026 0.80x / 0.72x vs A 1.14x. Signal +70bp vs random -60bp; unscored touches -14bp.
- Targets: nearer raises win rate (near edge 65 percent) but not the mean (-9bp); opposite 1618 has the best mean (+65bp) and is the worst in 2026; no target is positive in 2026. Closes beyond the level are positive at the market price (near edge +102bp), so the inside-only filter was wrong. Spike filter both sides 1.25x DD33 (2026 1.36x).
- The eight-account rerun (`exp-monday-limit-paper-accounts-20261028-v1`, adapter cd5f85adcf) started automatically after this summary. Learning: `do-not-drop-a-subgroup-on-outcomes-measured-at-another-entry-price.md`.

## 2026-10-28 · Causal score refit and the paper strategies on live-executable scores

- Refit (`exp-monday-limit-score-causal-refit-20261027-v1`): same rows/label/estimator, bar features from the 5m bar before the touch; the touch-bar refit reproduces the frozen coefficients exactly. Trained before 2026, tested 2026 to date: AUC 0.661 (p 0.001), top decile +21bp vs frozen causal +17bp. New model files only in that directory; frozen model and paper engine unchanged.
- Strategy level the refit does not help: v3 base A 2026 1.00x vs 1.14x for the frozen causal score (`exp-monday-limit-layered-refit-20261027-v1`).
- Paper strategies resting on the previous-close score (`exp-monday-limit-strategies-causal-20261027-v1`), leaky / frozen causal / refit: v3add20 from 2020 317x DD35 / 6.9x DD84 / 6.5x DD73 (2025 0.60x frozen); spike 4.65x DD21 win 85 / 1.02x DD62 win 78 / 0.70x; allin3 reaches 100x from 95 / 8 / 0 percent of monthly starts. Every multiple quoted for these accounts before 2026-10-27 is invalid. In sample except 2026 for the refit. Learnings: `causal-refit-improves-ranking-but-not-the-strategy.md`, `pgrep-f-waiters-match-their-own-shell.md`.

## 2026-10-27 · Chart measurement, trend lines, parallel channels and selected-symbol score

- The existing indicator popup now contains selection, measurement, two-point trend lines and three-point parallel channels. Mouse drag, touch placement, endpoint adjustment, deletion, clear/undo and keyboard placement work. Drawings persist per symbol in this browser using UTC/price anchors across reloads, history updates and timeframe changes. Measurement shows price/percent change, candle intervals, elapsed time and inclusive OKX contract volume; incomplete coverage and forming candles are explicit. Storage failures retain temporary drawings without overwriting unreadable originals. No API writes, new polling or chart autoscale contribution.
- The symbol card uses the ribbon's exact NY-week/symbol/side/touch-candle score. Missing scores hide the block; zero remains visible; only actual values above 0.5 highlight. Four-decimal text is enlarged to 22px desktop / 20px mobile, with the exact score in the accessible tooltip. Selecting another symbol immediately refreshes the score. Strategy and scoring calculations are unchanged.
- Stitch GEMINI_3_8_FLASH, project 8978791379095391159: drawing screen 9fcc51e5d9214869872995f628e3d4aa; subsequent score edit returned session 9550731287539791275 / DOM event 180e69e1. The downloaded screen was stale, so the exact verified-context replacement was applied and rendered before implementation; no new screen ID was invented. Design sources are in docs/design/stitch-20261027-chart-drawings/ and docs/design/stitch-20261027-symbol-score/.
- Validation: 128 focused Node tests and 6 asset-delivery tests passed. Chrome/WebKit drawing fixtures passed 21/18 checks, public drawing checks 9 each, score scenarios 12 each (both themes, desktop 1280/1024 and mobile 393/320), live score checks 3 each, and storage/cancellation checks 6. Real AEHR score 0.3065 matched the ribbon. Initial immediate-pan bug is fixed; two fixture timing/endpoint mistakes and their failed logs remain preserved. Physical iPhone rendering remains unverified. Raw evidence: output/playwright/chart-drawings-20261027/.
- Both owned browsers closed; all 16 recorded PIDs exited. No service restart. Paper worker alive, queue 7, no error; market stream connected; patrol heartbeat current with four enabled/error-free schedules. General scan was in progress (1754/2024, zero errors), with market_ready false and model_ready false retained. Unrelated edits preserved. Luna delegation stopped because its actual runtime model/effort could not be verified; main completed the work.
- Learnings: drawing-anchors-must-outlive-logical-indices.md; drawing-completion-must-not-block-the-next-pan.md; local-storage-read-failures-must-survive-memory-caching.md.

## 2026-10-27 · Monday1618 layered live entries (rest / close check / late market)

- Owner asked how to run the spike score live when it is only known at the touch close. Layers run in NautilusTrader (`exp-monday-limit-layered-20261027-v1`, builder 44514649f1, summary fixes cec75c58c5 / 32cf41028f; adapter gains fill_close_exit reason 14 and close-time market entries on the reclaim rule, 157 adapter tests pass).
- Causal plans are far below the leaky backtest: frac N20 3x base legs from 2020, leaky v3_be0 109x DD30 vs A (rest on previous-close score) 3.9x DD77, ABC 4.5x DD60; 2025 is a losing year for A (0.61x). Per-trade: A +146bp vs +238bp.
- B (exit at touch close when the touch-bar score < 0.5) cuts tails and drawdown but loses per trade (-372 vs -190bp on those 427). C (market at touch close for missed high-score levels) earns +64bp on 227, a fifth of the leaky limit fill. 1m layer not worth a run (<= 53 events). All in sample; paper accounts unchanged. Learnings: `the-touch-bar-score-leak-turned-4x-into-109x.md`, `mixed-datetime-units-break-event-replays-silently.md`.

## 2026-10-27 · Simulated strategy terminology

- The homepage selector, popup, analytics filter, strategy booklet and related messages consistently use `策略`. Simulation disclosures, account identifiers, stored preferences and all accounting values are preserved. Booklet text generators and current display projections use the same wording.
- Stitch copy revision: `f7e5fa6f00394d0d95064106ece0f1ce` (`GEMINI_3_8_FLASH`), source in `docs/design/stitch-20261027-strategy-labels/`. 53 targeted frontend tests pass; public Chrome and mobile WebKit checks pass. Raw QA: `output/playwright/strategy-labels-20261027/`. Both owned browser sessions closed, all 16 recorded processes exited; paper worker, market stream and four patrol schedules remain healthy.

## 2026-10-27 · Spike historical orders redrawn with real 1m candles

- Delivered all 85 previously exported 2026 spike orders as annotated 1920x1080 1m PNGs in a 10.33MB ZIP to the original Telegram group: https://t.me/c/3823416414/3430. Output: experiments/active/exp-spike-2026-1m-trade-charts-20261027-v1/. Original 5m delivery remains preserved.
- Source cache reused 86 of 89 symbol-days; only three missing official Binance UM days downloaded. All 5450 plotted minute candles have continuous coverage; 1090 aggregated 5m bars checked. DOOD has one disclosed open difference before the trade: a zero-volume first minute carries preceding close, while original 5m opens at first positive-volume minute. Other OHLCV and every execution interval agree; plotted source prices were not modified.
- All 31 original order fields remain unchanged, including net outcomes and 58 funded flags. Original 65 same-5m exits include 60 targets. E/X use original 5m time intervals; only six confirmed close exits have exact boundary marks. This is a display update, not a 1m execution replay; original score lookahead remains unresolved and is stated on every image.
- Eight targeted tests pass; all 85 PNGs and ZIP CRC verified; five representative images inspected, including same-bar target, multi-bar loss, long confirmation close and DOOD. Luna max read-only source/marker review completed; two label/zero-volume validation refinements incorporated. Builder commits preceded rendering; initial source-parity failure and detailed source/validation/delivery receipts remain local. No browser, service, paper account or live trading changes.
- Learnings: finer-candles-do-not-create-finer-fill-timestamps.md and empty-leading-minutes-need-explicit-ohlc-parity-evidence.md.

## 2026-10-27 · Today-only record drawer and historical entry

- The 1.618 drawer defaults to Asia/Shanghai today: touches use candle time, closed positions use final exit time. Older touch events remain only while that exact signal_id has a funded, filled, positive remaining position in any account. Shadow rows, unfilled adds, and zero remaining amounts cannot retain an old touch. The old held touch carries a small holding badge.
- Added the history icon and return-today control within the same drawer. History reads paginated older touches/closes with search, scope and retry, including records beyond the former 100-row page. Account changes and visibility cancel stale reads. Selecting a historical trade pins its chart even outside the recent snapshot.
- Snapshot recent signals/closed rows, all-time stats, latest funded fill, ribbon and chart consumers retain their original projections. Optional list_period=today adds independent record_signals/record_closed fields; /api/monday-reclaim-paper/history is read-only. No strategy, fee, model, sizing, order or ledger-write changes.
- Stitch GEMINI_3_8_FLASH project 8978791379095391159, source 8bd00fba024e47a6998004836e10165d, produced screen 678fa6592e6b4343a02a3a4b745a55b5 before implementation. HTML/image/provenance: docs/design/stitch-20261027-record-history/. Synthetic placeholders only.
- Validation: 70 Python engine/history tests, 81 focused frontend tests and 6 asset tests passed. Chrome and iPhone-sized WebKit each passed 22 interaction checks (1440/1024 or 390/320, both themes, 123-row history, exact old holding, exit-date grouping, failed-read retry, search and chart selection); each passed 7 real spikeok.com checks. Eight live accounts reconcile today + history counts to complete-ledger counts; six invalid GET filters return 422. Evidence: output/playwright/record-history-20261027/. Initial harness failures (same-document navigation, transient transform dimensions, row-link overlay) are preserved. WebKit's already documented backdrop rendering limitation remains; no emulator-specific CSS workaround shipped and actual iPhone material is unverified.
- Restarted the monitor to load the GET endpoints. Paper worker alive/queue 0/no error, fib connected, patrol heartbeat current with four enabled/error-free schedules. Both task browser sessions closed and all 12 recorded PIDs exited. Luna max runtime verified from its turn_context; delegate owned backend/tests only. Unrelated workspace edits preserved.
- Learning: docs/learnings/today-records-must-not-filter-only-the-recent-page.md.
- The preceding 920-chart TG delivery remains an original-history artifact. The new score lookahead audit applies to its performance figures; a correction was acknowledged at https://t.me/c/3823416414/3428. Receipt: experiments/active/exp-paper-2026-trade-charts-20261027-v1/telegram-audit-correction.json. No new backtest was run.

## 2026-10-27 · Monday1618 entry score reads the touch bar (lookahead)

- Found while building the owner-requested pre-scored arming replay: the frozen entry score's vol_ratio, liquidity and toward24 are computed at ledger `entry_i`, which equals the touch (fill) bar in all 58,740 events. The fill happens inside that bar, so its close and full-bar volume are future. The stored scores are reproduced exactly at the touch bar.
- On the bar before the touch, with the frozen model unchanged: score >= 0.5 goes from 2108 to 2293 events, 1865 in both. The 243 dropped events are the best of the old selection (v2 near-edge +298bp, win 85%). The selected mean falls from +186bp to +161bp; from 2025 it falls from +167bp to +140bp.
- Every score-filtered, -targeted or -sized result is overstated in sample. That covers v2 top 40, v3, v3add, be-lock, spike-HW, langlang/allin3, return-opt v1/v2 and v3add20. The paper engine already scores on the bar before the touch, so it is causal, but its expectations come from leaky backtests. No paper or model change was made.
- Evidence: `exp-monday-limit-score-leak-audit-20261027-v1` (builder 8cee0529d6; the first run failed on JSON serialization, and its log is kept). Learning: `ledger-entry-i-is-the-fill-bar-not-the-submission-bar.md`.
- Owner follow-up, same day (`exp-monday-limit-partial-bar-score-20261027-v1`): scoring at minute 3 or 4 inside the touch bar and only then resting the limit recovers 14 or 8 of the 243 lost fills. 194 or 222 of them had already touched by then, so the leaked edge arrives with the touch itself. Learning: `touch-bar-information-arrives-with-the-touch.md`.
- The arming replay is paused, and its builder `yoyo/evaluation/monday_limit_prescore_arm.py` plus config are uncommitted. It cannot match the stored scores by design. Next, pending owner decision: rerun v3/v3add once in NautilusTrader with causal scores, recompute the return table, then run the arming replay. A causal refit of the model would change the paper accounts' frozen model and needs the owner.

## 2026-10-27 · Reliable coin artwork and bounded homepage/API work

- Replaced guessed external coin-image URLs across the ribbon, list, records, market and inspection views with a shared manifest pointing to 433 existing verified OKX assets. Same-origin content-version URLs reuse the native app's files without duplicating them. LAYER now uses its real asset; unavailable DGAI/SHEIN use stable DG/SH badges. One bounded retry uses a distinct URL, and successful loading clears the hidden state. Public WebKit fault injection first proved identical-src retry ineffective; the corrected Chrome/WebKit runs recovered after exactly one retry. Ribbon polling retains the same image nodes.
- Closed watch/record drawers skip list construction; record rules remain independently available. Feeds emit one settled snapshot rather than repeating the same render. Backend reads only the latest 100 closed rows and their exit legs, separately computes complete-ledger statistics and latest funded fill, uses supporting indexes and avoids duplicate JSON traversal. No fill, fee, threshold, sizing, account or trading rules changed.
- In a fixed 20,000-position fixture, old/new complete snapshots were identical: median 204.3ms → 6.7ms (30.6x). Alternating response-encoding comparison on the same captured payload: 54.0ms → 16.1ms. These are isolated comparisons, not a whole-site speed claim; live HTTP samples varied with scanner warmup and are retained.
- 759 frontend tests and 111 relevant Python tests passed. Seven pre-existing frontend failures were outdated expectations (display endpoint, spike default, navigation/theme) or a missing chart-palette test dependency; updated against existing approved behavior. Chrome local QA passed 23 checks; iPhone-sized WebKit passed 22. Both public browsers passed 15 checks each, including all eight analytics tracks, cache headers and injected 503 recovery, with no page exceptions or application writes. Initial QA selector/document-reuse/relative-URL failures are retained. Physical iPhone was not tested. Raw evidence: `output/playwright/logo-performance-20261027/`.
- Official Stitch GEMINI_3_8_FLASH project `8978791379095391159`, source `48f7cd3b238047f0bc613828155e30c7`, produced `8bd00fba024e47a6998004836e10165d`; retrieved image/code before UI edits, saved in `docs/design/stitch-20261027-coin-reliability/`. Preserved current layout; generated sample values/layout changes were not adopted. Luna delegation stopped when actual model/effort could not be verified; main completed the work.
- Monitor restarted twice to load asset delivery, read-only query and encoding changes; existing unrelated Bark changes were already loaded before restart. Paper worker alive/queue0/no error, fib connected, patrol1266 heartbeat current with all four plans enabled/no error. Scanner remains warming, with BAC/CEG 1H/4H returning no closed history; source-status evidence retained, no synthetic data or eligibility changes. `model_ready=false` remains expected. Task browser sessions were closed and owned process cleanup checked.
- Learnings: `coin-artwork-must-have-a-verified-local-source.md`, `ledger-pagination-must-preserve-full-summary-and-latest-fill.md`.

## 2026-10-27 · Remove visible ribbon timestamps

- Owner requested one more reduction: recent coin chips now show only the ticker and exact numeric score. Timestamps remain in the native tooltip, with no visible time column. Width is content-based 76–94px, down from 104px, while tap height stays at least44px. Existing account linkage and fixed shortcuts are unchanged.
- Reused the owner's already-loaded Stitch canvas. GEMINI_3_8_FLASH edit of1452f40e63a04f53b6b5e635ad78079c returned48f7cd3b238047f0bc613828155e30c7 without timeout; retrieved design image/code before implementation. Sources: docs/design/stitch-20261027-ribbon-no-time/.
- Validation:17 watchlist tests and23 public-browser checks each in Chrome/iPhone-sized WebKit passed. Verified both themes,1440/1024/390/320 widths, no visible time/direction/score labels, intact four-decimal scores, tooltip time,44px tap targets, no overflow, coin navigation, and no page exceptions/API writes. Evidence: output/playwright/ribbon-no-time-20261027/. Task QA sessions closed and owned processes checked; owner's Stitch tab remains intact. No backend restart or trading mutation; service/patrol health retained in final-service-check.json.

## 2026-10-27 · Compact coin ribbon and current-holding account linkage

- Recent coin chips are 104px wide (previously 146px): ticker above the numeric model score and colored time. Removed visible upper/lower labels and the score prefix. Missing scores use a dash with the original reason in accessible context; exact occurrence joins, four decimals, true zero and pinned BTC/ETH/XAU remain intact.
- Coin navigation now selects an account with funded, filled, remaining exposure in that symbol. A current holder stays selected; otherwise the persisted account order breaks ties; no holder selects spike. Shadows, pending adds and zero remaining notional do not qualify. The chart chooses a current open position before newer closed history. This runs on coin selection only, preserving later manual account choices and the current timeframe during polling.
- Stitch GEMINI_3_8_FLASH project 8978791379095391159, source 040e7b6c98104d0f9bb77ec995dabb1a, produced 1452f40e63a04f53b6b5e635ad78079c. One edit request timed out at 300s; automated browser recovery stayed blank and list/get_project omitted known completed screens. Owner opened the actual canvas; list expanded from 218 to 224 and official get/download recovered the completed design before application edits. No duplicate generation. HTML/image/provenance: docs/design/stitch-20261027-compact-ribbon/.
- Validation: 55 focused Node tests and 5 asset-delivery tests passed. Chrome and iPhone-sized WebKit each passed 15 fixture checks (current-holder preference, custom ordering, shadows/pending adds, partial exposure, newer closed history, timeframe/manual-selection preservation, both themes at 1440/1024 or 390/320). Both public spikeok.com checks passed 7 assertions, including actual SHEIN-to-v3 and unheld POPMART-to-spike linkage. No page exceptions or API writes. Existing external icon 404s, rapid-switch WebSocket cancellation and a temporary 12H refresh notice are retained in raw QA; final public 15m charts rendered normally. Physical iPhone was not tested. Evidence: output/playwright/compact-ribbon-20261027/.
- Both QA sessions closed; all 11 recorded PIDs exited. No backend restart or account mutation. Paper worker alive/queue0/no error, fib connected, scan2008/2008/errors0, market_ready true/model_ready false, patrol1266 heartbeat current. Owner's browser tabs were left intact. Luna research delegate stopped because actual runtime model/effort could not be verified; main completed recovery and validation. Learnings: stitch-recovery-needs-a-loaded-canvas-not-a-navigation.md; coin-navigation-must-use-current-funded-positions.md.

## 2026-10-27 · Reorderable account picker, compact P&L and ribbon scores

- The account picker starts with spike, followed by the other seven accounts. Separate six-dot handles support mouse/touch dragging with lift and neighbor movement, auto-scroll, Alt+Up/Down, cancellation and local persistence. Saved order is independent of the active account and book; unknown/duplicate saved IDs are discarded and new accounts are appended. Storage denial is visible. The list uses compact name / signed realized P&L / book rows instead of long subtitles.
- Picker amounts reuse the existing complete account summary: equity minus initial equity minus actual net refill cash for allin3; floating P&L, shadow trades and the truncated closed page are not summed. Missing values remain missing; zero remains zero. The ribbon now shows four-decimal model entry scores with a compact score label, joined by NY week, symbol, side and touch candle. Pending, missing, feature-error and excluded signals remain distinct. Scores are independent of the selected account and are not labeled as win probabilities.
- Official Stitch GEMINI_3_8_FLASH project 8978791379095391159, source b28188b2e438404593e684445325de54, produced 040e7b6c98104d0f9bb77ec995dabb1a before implementation. HTML/image/provenance: docs/design/stitch-20261027-account-order-scores/. Only synthetic examples were supplied; generated balances, fixed-asset scores, extra navigation and period omissions were excluded. WebKit QA reproduced the existing top-layer backdrop limitation; the picker now shares the drawer's background-blur fallback and removes it on close.
- Validation: 51 focused Node tests and 5 asset-delivery tests passed. Chrome and iPhone-sized WebKit each passed 18 checks; a further 9 trusted Chromium touch checks covered actual touch pointer events, touch cancellation, clipped-list auto-scroll, storage denial and no selection side effects. Both real spikeok.com checks passed for default order, eight handles, live P&L, exact touch scores and blur cleanup, with no page exceptions or API writes. The first short-viewport touch harness tried clicking a trigger covered by its own popover; closing via Escape corrected the harness, with initial failure retained. Physical iPhone rendering remains unverified. Evidence: output/playwright/account-order-scores-20261027/.
- Both task browser sessions closed; all 11 recorded PIDs exited. No backend restart or account mutation. Paper worker alive/queue 0/no error; fib stream connected. Scan and patrol cleanup snapshots are in final-service-check.json; existing model_ready false is unchanged. Unrelated files preserved. Requested Luna delegation stopped before reading files because actual runtime model/effort could not be verified; main completed the audit.
- Learnings: account-ordering-must-not-change-strategy-selection.md; signal-badges-must-join-the-occurrence-not-symbol.md.

## 2026-10-27 · Default spike and continuous historical backtest charts

- Owner selected the historical win-rate basis and spike as the default account. The root entry, route defaults and startup warmup now agree on spike / 15m. With no selected funded trade, the chart opens BTC-USDT-SWAP; explicit account, symbol, period and missing-trade links keep their existing semantics. This is a fixed presentation default, not a live performance-ranking rule or a strategy promotion.
- All eight strategy books now have compact conventional tear sheets: realized equity / cumulative return toggle, aligned underwater drawdown, monthly heatmap, yearly bars, trade-return and holding-time distributions, long/short breakdown and recent historical trades. The 75vw desktop / full-width mobile glass drawer keeps the left chart visible. Historical charts are separate from live forward equity; allin3 uses trading P&L and separates net cash additions. No daily mark-to-market path exists, so no synthetic floating drawdown, Sharpe or benchmark is shown.
- Reused saved Nautilus fills and the original capital allocators with optional observation hooks to export missing event ledgers, preserving original fees, slippage and parameters. Existing MAE extraction ran once on reused bars; no new per-candle fill replay or downloads. Preselected the original first seed/origin, not an optimized seed or a splice of period endpoints. Historical BE0/current protection, Binance/OKX, liquidation, and v3add child-sizing differences are explicit. Period-summary medians remain distinct from single-path results. Builders were committed before generation: b82a9b2b68, fb953e4da8, 4bc6357846. Registration: exp-strategy-booklet-paths-20261027-v1; manifest and chart-index retain provenance, raw ledgers/caches remain local.
- All eight source candidate counts match; per-event cash identities reconcile within 6e-11. The original 5m final multiple 1.1092591944597592 and maximum realized drawdown 38.12866608347949 match exactly. Initial manifest serialization failed on a NumPy integer after saving ledgers; the fix reused saved fill caches and did not repeat candle/MAE extraction. Initial log and successful retry remain in the experiment directory.
- Stitch GEMINI_3_8_FLASH project 8978791379095391159, based on a4e9abe06abd4685af45da545b6003d1, produced 3db5c4dc931244f8a1ea859d4acb8d49 before UI changes. HTML, image and source identifiers: docs/design/stitch-20261027-backtest-tearsheet/. Generated numbers, R units, benchmark, revision claims and v3add20 default were excluded. Conventional layout references: TradingView strategy reports and QuantStats.
- Validation: 68 focused Node tests and 12 Python trace/projection/asset tests passed. Chrome and iPhone-sized WebKit each passed 64 checks across eight accounts, both themes, desktop 1440/1024 and mobile 390/320, complete curves and endpoints, chart toggles, refills, internal heatmap scrolling, transparent drawer, missing-history retry, explicit bookmark preservation and no application writes/page exceptions. Real spikeok.com checks passed in both browsers for the default and spike/v3add20/allin3 curves. The intentional HTTP 503 failure/retry evidence is retained; physical iPhone rendering remains unverified. Evidence: output/playwright/backtest-tearsheet-20261027/.
- Both task browser sessions closed; all 11 recorded PIDs exited. No backend restart or trading writes. Paper worker alive/queue 0/no error; fib connected; scan 2008/2008/errors 0; market_ready true/model_ready false. Patrol PID 1266 heartbeat 5.77 seconds old, four schedules enabled/no errors; existing partial availability 3/10/203 for 15m/1H/1D retained. Unrelated changes preserved. Luna delegation stopped because actual runtime model/effort could not be verified; the main agent completed source lookup and implementation.
- Learning: docs/learnings/backtest-tear-sheets-need-event-ledgers-not-period-endpoints.md.

## 2026-10-27 · Web integration: v3add20, eighth paper account

- v3add20 is selectable in the homepage account picker, deep links and analytics. Parent 6060 and add leg 6061 retain their own labels and score-based targets, while analytics validates the account ID 6060. The other seven accounts remain separate; missing accounts do not fall back to another track.
- Current rules read the API configuration: margin per fill is realized equity / 20, at 3x; parent and filled adds share the 20-leg and available-margin limits. Pending adds explicitly recalculate at fill instead of inheriting the parent notional. Shadow slot/capacity reasons are visible. Full and partial close estimates for both legs cap the loss at the corresponding closed margin; no order was submitted.
- Added the account's existing compact strategy book. Historical charts project corrected return-opt v2 results (source 9a781c29d1): period multiples, drawdowns, funded counts and capacity skips, with fixed-slot/linear comparisons and the from-2020 aggregate. Historical BE0/Binance/MAE-liquidation assumptions remain distinct from current +0.17 percent/OKX/margin caps; overlapping periods and in-sample selection are explicit. No saved continuous historical path exists for this version, so no curve was fabricated. Live forward equity uses the complete analytics ledger. No new backtest ran; the projection builder was committed first as c3c531b5f1.
- Stitch GEMINI_3_8_FLASH project8978791379095391159, based on7b4880f001bf4b5c9de311fb22658de9, produced a4e9abe06abd4685af45da545b6003d1 before UI edits. HTML/image/provenance: docs/design/stitch-20261027-v3add20-account/. Generated placeholder figures and trading claims were excluded.
- Validation:56 focused Node tests,7 v3add20 backend contract tests and5 asset-delivery tests passed. Chrome and iPhone-sized WebKit each passed36 fixture checks, then real spikeok.com account/book/analytics checks. Covered both themes, desktop1440/1024 and mobile390/320, transparent drawer host, no overflow, pending sizing, both legs/directions and full/partial margin caps, missing-account isolation and zero application writes/page exceptions. Initial offscreen content-visibility read failure is retained; scrolling the pending section into view fixed the harness. Evidence: output/playwright/v3add20-account-20261027/. Physical iPhone rendering remains unverified.
- Temporary Stitch tab and both task QA sessions closed; all11 recorded PIDs and the associated WebKit service processes exited. No backend restart. Paper worker alive/queue0/no error, fib stream connected, scan2008/2008/errors0, market_ready true/model_ready false. Patrol1266 heartbeat0.77s old; four schedules enabled/no errors; existing partial availability3/10/203 for15m/1H/1D retained. Unrelated edits preserved. Luna delegation stopped because actual runtime model/effort could not be confirmed; main completed the contract audit.
- Learning: docs/learnings/scale-in-account-variants-need-fill-time-sizing.md.

## 2026-10-27 · Full symbol catalog and compact strategy booklets

- The homepage All tab now reads the full live OKX USDT perpetual catalog from `/api/research/paper-desk/symbols`, sharing the market terminal's volume ordering. Untouched contracts support search and favorites; upper/lower tabs and the recent ribbon retain the shared Monday touch policy. Expired prices/turnover stay missing and last-good catalog survives an explicit error. Public check returned 507 contracts, BTC first.
- Each of the seven existing account rows has a separate book button. Selecting an account and opening its book are independent. Native modal slides from the right at 75vw on desktop and full width on mobile, with Escape/backdrop close, focus return and paused requests after close. Compact horizontal chapters, charts before rules, two-column desktop charts, current API rules and complete-ledger forward equity. allin3 contributions remain distinct from trading profit.
- Historical content projects saved research only: original v2 and spike equity plots, spike return distribution/floating loss/frequency, per-period win rate, drawdown, exits, v3add fills and allin3 time-to-target statistics. Current-version continuous paths absent from saved outputs remain explicitly missing; earlier v3 P20/BE0 curves are expandable references with parameter/anomaly caveats. Old invalid slot multiples are excluded; unaffected linear rows are used. Matched random controls for the original spike baseline are shown with their different exit semantics. No new backtest/replay was run. Projection builder was committed before each generation (646178abda, 25910e9279, e9fb234b65).
- Owner's refinement: viewport host excluded from generic dialog card material, fixing the white left quarter. Shared reading-glass alpha .58 light/.65 dark with 24px blur; book, picker and list reuse the token. Actual WebKit screenshots exposed a transparent-modal blur limitation, addressed by blurring only the read-only background while open and restoring it on close. Reduced motion/transparency respected. Physical iPhone rendering has not been tested.
- Stitch GEMINI_3_8_FLASH, project8978791379095391159: initial b28188b2e438404593e684445325de54 and compact refinement7b4880f001bf4b5c9de311fb22658de9, each before its corresponding UI edits. Design HTML/images/provenance in docs/design/stitch-20261027-strategy-booklet/; synthetic design values excluded from implementation.
- Validation:79 focused Node tests passed; final focus change additionally passed9 booklet/loader tests. Chrome88 and iPhone-sized WebKit85 scenario checks passed, plus final real-public-data checks in both engines, transparent host, exact width, heading focus, full-keyboard close navigation, source controls, glass cleanup and no application writes/page errors. Public17 static assets match local files. Initial harness/retry/keyboard failures and direct unauthenticated asset403 are retained; browser-authenticated recovery checks passed. Evidence: output/playwright/strategy-booklet-20261027/.
- Both task browser sessions closed; all11 recorded PIDs exited. No service restart or order writes. Final worker alive/queue0/no error, fib stream connected, scan2008/2008/errors0, market_ready true/model_ready false. Patrol1266 heartbeat2.64s old; four schedules enabled/no errors, existing partial availability3/10/203 for15m/1H/1D retained. Unrelated changes preserved. Requested Luna delegation stopped because actual runtime model/effort could not be confirmed; main completed evidence lookup.
- Learnings: fullscreen-drawer-hosts-must-not-inherit-card-surfaces.md; strategy-books-need-versioned-equity-evidence.md.

## 2026-10-27 · Return-first v2 on the fixed simulator; eighth paper account v3add20

- `exp-monday-limit-return-opt-20261027-v2` (builder 9a781c29d1) reruns v1 on the fixed slot simulator and adds `frac`: margin = realized equity / N per fill, at most N open, margin in use ≤ equity. In sample, same fills:
  - v3add frac 20 × 3x: 11.62 / 8.26 / 3.42 / 4.41x (2020-24 / 2025 / 2026 / last 5 months), DD 15 / 18 / 35 / 13; 312x from 2020 with DD 35.
  - Fixed 20 slots × 3x: 6.33 / 4.83 / 2.70 / 3.15x; 76x from 2020 with DD 23.
  - v3 linear 1x (the v3 paper sizing): 3.86 / 9.31 / 2.47 / 2.95x; 86x from 2020 with DD 35.
  - The acceptance set in the config holds. frac beats both in every period except 2025 against linear.
  - v3add linear 1x (the v3add paper sizing): 200x from 2020, but DD 50 in 2026.
  - Highest geometric mean under DD 40 is v3add frac 30 × 5x, but it has 24-50 liquidations per period; not adopted.
- Paper account `v3add20` (commit 3ee9bf910e; tracks 6060, add legs 6061; 1000 USDT) runs the v3add rule. Each leg posts realized equity / 20 at 3x, with at most 20 funded legs and margin in use ≤ equity. The loss is capped at the margin. A parent with no slot or margin is a shadow row (`no_slot` / `no_capacity`); such an add is cancelled (`add_no_slot` / `add_no_capacity`). Snapshot top-level `v3add20` holds the config; analytics accepts `track=v3add20`. Breakeven +0.17 percent as in the other paper accounts (the backtest v3add used breakeven at entry).
- Monitor restarted once (PID 93318); only the paper file had changed. Paper worker alive, queue 0, no error; v3add20 1000 USDT with no trades; the other seven accounts are unchanged. Scan 2008/2008 without errors, market_ready true, model_ready false as before; fib stream connected; patrol PID 1266 running.
- Web UI integration is complete for v3add20; see the delivery entry above. The account picker, strategy book, analytics and parent/add ledger are connected. Both 6060 and 6061 close estimates read the configured leverage for their proportional margin-loss cap.

## 2026-10-27 · Minimal home order ticket, default manual paper account

- The homepage 1.618 manual ticket now defaults to the uniquely named 梭哈账户, resolved to its actual account ID. This is the manual paper desk, separate from the automated allin3 track. Other quick-ticket callers retain the API default. Missing/ambiguous names require explicit selection; switching accounts invalidates the old quote. Pending receipts keep their original account and symbol instead of taking the new default.
- Compact header with holdings/back action; one inline environment/account row, direction buttons, unboxed fixed entry price, editable leverage, one allocation slider, two amount metrics and one preview action. Removed large section tabs, refresh/max-leverage actions, percentage presets, recent-order list and duplicate context. Custom amount, TP/SL and detailed budget are under More settings, with active options summarized when collapsed. Review replaces the editor and explicitly shows the account/symbol; the second confirmation remains required. Shared glass-sheet tokens reduce chart distraction without changing global chart materials.
- Stitch GEMINI_3_8_FLASH project8978791379095391159 edit of0dc09018ec5241fc90f42a3909fa0ef4 produced33be10a38bf34bf798c96a02cf5e4a83 before UI implementation. The MCP edit timed out after300s, but opening the logged-in project synchronized its completed screen; retrieved through official list/get/download without duplicate generation. Design HTML/image/provenance: docs/design/stitch-20261027-minimal-home-ticket/. Only synthetic design numbers supplied.
- Validation:17 Node tests (frontend_monday_live, frontend_monday_quick, frontend_monday_ledger) and5 Python asset-delivery tests passed. Chrome/iPhone WebKit each passed31 fixture checks covering sizing, optional exits, two-step previews, account invalidation/default reset, holdings, missing preference, error recovery and original receipt identity. Public spikeok.com checked the real account list with synthetic sizing at desktop1440/mobile390/320 in both themes: no horizontal overflow, primary action inside the viewport, no page exceptions or trading writes. Initial harness selector and premature ready-check failures are retained; corrected runs passed. Raw evidence: output/playwright/minimal-ticket-20261027/. Physical iPhone rendering remains unverified; WebKit emulation does not reproduce backdrop blur.
- Temporary Stitch tabs and both QA browser sessions closed; all12 recorded QA PIDs exited. No service restart. Paper worker alive/queue0/no error, fib stream connected, patrol1266 heartbeat current/four plans enabled/no error; overall market/model readiness remain false as before. Unrelated changes preserved. Luna delegate stopped because runtime model/effort could not be confirmed; primary completed the contract lookup. Learning: default-account-preferences-apply-only-to-new-drafts.md.

## 2026-10-27 · Warning: slot-sizing results are overstated about 2x (starting-capital error)

- `simulate` in `yoyo/evaluation/monday_limit_v3_slots.py` and `monday_limit_spike_opt.py` starts with `reserve = 1000*(N-1)/N` and `bal = [1000/N]*N`. The account therefore starts with 1000 × (2N-1)/N (1950 USDT at N = 20, 1900 at N = 10) but reports `total / 1000`. The first doubling resize also fires almost at once.
- A read-only check on cached fills put every slot USDT in one 1000 USDT pool, with slots starting empty. Committed vs corrected (10 tie orders, median):
  - v3add 20 × 3x: 12.62 / 9.29 / 5.43 / 6.19x became 6.33 / 4.83 / 2.70 / 3.15x (2020-24 / 2025 / 2026 / last 5 months). Run continuously from 2020 it is 76x, not 152x. Drawdowns are about the same.
  - v3 20 × 3x: 9.08 / 6.69 / 4.02 / 4.07x became 5.39 / 3.48 / 2.01 / 2.12x.
- Affected: `exp-monday-limit-v3-slots-*`, `exp-monday-limit-spike-opt-*`, `exp-monday-limit-spike-more-*`, `exp-monday-limit-return-opt-20261027-v1` and the slot rows of `exp-monday-limit-be-lock-compare-20261027-v1`. Comparisons between variants made on the same simulator keep their direction, but the absolute multiples and any ranking against linear or all-in sizing do not hold.
- With the correction, v3add 20 × 3x (76x from 2020, max DD 23 percent) no longer beats v3 linear 1x (86x, max DD 35 percent); their geometric means over the four periods are about equal (4.0).
- Not affected: linear sizing, the spike-HW booklet `slot_replay` (equity / slots), Langlang / all-in `path_run`, and every paper account.
- Fixed in 9a781c29d1 (both simulators; regression tests in `tests/evaluation/test_monday_slot_simulators.py`). The return-first comparison was rerun as `exp-monday-limit-return-opt-20261027-v2`. The other slot studies are flagged in the registry and not rerun.

## 2026-10-27 · Web integration: v3lock and allin3

- Seven accounts are selectable on the homepage and analytics: 60m, 5m, v3, v3add, spike, v3lock and allin3. Cold links, ledger scopes, internal analytics IDs (4060/5060), targets and close-dialog identities stay account-specific. Missing accounts remain unavailable. v3lock uses its own 50 bp offset and stored long/short protection price; original v3 and allin3 retain 17 bp.
- allin3 shows stake count, cumulative stake-budget capital, multiple and expandable refill records. Realized trading P&L removes net refill cash from balance changes; the stake-budget denominator is not substituted for cash flow. Analytics shows period refill cash/count and explains the equity curve's additions; the saved pre-refill low remains visible. Busy shadow rows are explicit. Close estimates use the configured 3x leverage to cap each tranche's loss at its margin; no POST/order was sent in browser QA.
- Official Stitch GEMINI_3_8_FLASH project8978791379095391159 generated screens071103a8b81847049747df436aeec779 (v3lock) and b218f8c044f442c7bb5650a93a42d7f3 (allin3) before their UI edits. HTML/images/provenance: docs/design/stitch-20261027-v3lock-account/ and docs/design/stitch-20261027-allin3-account/. Design numbers are synthetic; actual execution semantics come from the backend.
- Validation:72 focused Node tests,7 allin3 backend contract tests and5 asset-delivery tests passed. Local/public Chrome and iPhone WebKit checked all seven accounts, analytics synchronization, deep links, empty state, light/dark and320/390 widths. Read-only fixtures checked v3lock long100.5/short99.5 protection, allin3 refill97.90→1000 with trading P&L−902.10 and multiple0.50x, rendered curve low/refill, busy shadows, missing accounts and full/half/quarter close caps−1000/−500/−250. Expanded mobile capital details initially exceeded available height; a measured summary/context height budget now keeps the scrollable body above the dock, verified locally in both browsers and publicly in WebKit. Browser page exceptions/application writes:0. True-device backdrop rendering remains unverified. Raw QA: output/playwright/v3lock-account-20261027/ and output/playwright/allin3-account-20261027/.
- This frontend task did not restart services. A concurrent backend allin3 deployment briefly interrupted early v3lock QA; recovery and fresh public checks passed. Initial disclosure-read timing failure and old expanded-layout screenshots are retained. The two task browser sessions were closed after each phase; final cleanup verified every recorded PID exited. Paper worker/1.618 stream and patrol worker1266/four schedules checked after cleanup; overall market/model readiness is reported separately in the raw health receipt. Unrelated archive/Bark/config changes preserved. Requested Luna delegation stopped because actual runtime model/effort could not be confirmed; primary completed integration.
- Learnings: paper-account-variants-need-their-own-protection-config.md; refilled-paper-equity-is-not-trading-profit.md; floating-account-details-need-a-remaining-height-budget.md.

## 2026-10-27 · Seventh paper account allin3 (high-payoff all-in 3x)

- Owner asked for a paper account running the high-payoff setting next to the others. Commit 144e38a3e9: API key `allin3`, internal track 5060. Score ≥0.6 touches only; opposite 1.618 target; 60m confirmation; -30 percent backstop until the confirmation; breakeven +0.17 percent. All-in at 3x: notional = 3 × realized equity. One position at a time: later touches are `busy` shadow rows. A loss is capped at the margin.
- Blow-up rule as in `exp-monday-limit-langlang-20261027-v1`. A close that leaves the account below 100 USDT counts as a blow-up: the rest counts as lost, equity resets to 1000 USDT, `tracks.stakes` rises, and a `refills` row is written (`equity_before`, `equity_after`). A backstop at 3x already loses about 90 percent, so liquidation is not simulated separately.
- Snapshot `tracks.allin3` adds `stakes`, `injected` (1000 × stakes), `multiple` (equity / injected) and `refills`; top level `allin3` holds the config. Analytics treats a refill as cash added after the exit at the same time: the low and the drawdown stay on the curve, the refill is not profit, and `summary.refill_count` / `refilled` report it. `ledger_difference` stays 0.
- Monitor restarted once (PID 84756). Only the paper / analytics files had changed since the previous start. Paper worker alive, queue 0, no error; allin3 1000 USDT, stakes 1, no trades. Fib stream connected; scan back to 2008/2008 without errors and market_ready true; model_ready false as before. Patrol PID 1266 still running.
- Web UI integration is complete for allin3 and v3lock; see the frontend delivery entry above. Stake count, multiple, refill records and the configured close-sheet margin cap are connected.

## 2026-10-27 · Sixth paper account v3lock (+0.5 percent profit lock)

- Owner asked for a v3 +0.5 percent profit-lock paper account next to v3 as a forward check of `exp-monday-limit-be-lock-compare-20261027-v1`. Commit a254f02183: API key `v3lock`, internal track 4060, own 1000 USDT. Same v3 entry, score gate, targets, sizing and -30 percent backstop. A reclaim moves the stop to entry +0.5 percent instead of +0.17 percent; a reclaim close short of that price exits at the close (`reclaimed_inside_be`). Snapshot carries `v3lock: {breakeven_offset_bp: 50}`; analytics accepts `track=v3lock`.
- Monitor restarted once (11:43, PID 82243). Only this file had changed since the previous start; the uncommitted Bark edits were already loaded. Paper worker alive, queue 0, no error; v3lock 1000 USDT, no trades. Fib stream connected, scan 2008/2008 without errors, model_ready false as before.
- Web UI integration is complete for v3lock; see the frontend delivery entry above. Homepage/analytics selectors, cold links, ledger rules and close identity use its own account and 50 bp protection configuration.

## 2026-10-27 · Complete dark-theme coverage for shared and lazy components

- Reproduced unreadable dark text, white tables/steps and stale dialog palettes in secondary study tabs. Playbooks now use live semantic colors; reader figure/badge ink uses opaque page colors, not translucent glass. Body-level RektProof dialogs share the reader palette instead of copying computed colors, so an already-open figure follows theme changes. Removed a self-referencing orderflow border token.
- Shared opaque fallback colors now belong to the light/dark palette, including shadow hosts and prefixed backdrop support. Reduced transparency no longer forces white cards. Completed dark styling for YOLO insight/readout/dropdown, Bootstrap outline/hover/active states and select arrows, native options, keyboard hints, Monday ticket/holding/error states and floating fallback materials. Normal chart glass opacity, trading colors, original image pixels and the separate crimson alarm remain intact; no trading/data behavior changed.
- Official Stitch GEMINI_3_8_FLASH project8978791379095391159 screenefac7ed28cc04934b5eeefbd6f6ff584 produced design HTML/image before edits, based on2acc1f5244714b8bb6ca736e54b25bc7. Source/provenance in docs/design/stitch-20261027-night-coverage/. Generated performance/trading examples are not application facts or execution authority.
- Validation:27 focused frontend tests and5 asset-delivery tests passed. Desktop audit visited32 route entries/29 resolved workspaces, followed by loaded workflow/books and nested component checks. The remaining signal reset/hover and reader badge contrast findings were corrected and their focused scan passed. Chrome and iPhone WebKit verified light/dark roundtrips, still-open diagram colors, chart menu, shadow settings, home ticket transparency, persisted reload and mobile320 width; no page exceptions or application writes. Chrome also verified reduced-transparency light/dark and shadow surfaces. Public spikeok.com on iPhone WebKit loaded the new reader colors. Raw evidence: output/playwright/night-mode-20261027/. Initial QA hidden/ambiguous tab selectors and cold-route timing failures are retained; corrected readiness/unique selectors passed. True iPhone backdrop rendering remains outside this browser-emulation verification.
- No service restart. Task sessions night-mode/night-mode-webkit closed and all recorded browser processes checked; paper/patrol health checked at cleanup. Unrelated archive/Bark/config edits preserved. Luna audit stopped because actual runtime model/effort was not verifiable; primary completed the audit. Learnings: theme-colors-must-remain-live-across-component-boundaries.md and glass-fallbacks-and-foreground-ink-need-opaque-theme-tokens.md.

## 2026-10-27 · Home 1.618 manual limit ticket and partial exits

- Moved the old Monday quick-order entry to one floating icon on the 1.618 home chart. Clear glass ticket starts in paper mode and offers live mode, a fixed lower-long / upper-short 1.618 entry, optional manual TP/SL, mirrored range0.618 or opposite Monday-extreme TP shortcuts, and compact holdings. Manual closes support preset/custom percentages; free live positions can edit/remove TP/SL. Existing strategy lots retain their original management contract. Chart focus suspends/restores the draft; mobile hides other floating controls while editing.
- Server-supplied completed ranges and tick rounding bind paper/live direction, price, week and expiry. Weekends are allowed. During Monday development, manual tickets use the previous completed range shown by the chart until the new range closes; automatic scanners retain their causal reader. Home live orders use post-only and paper confirmation rejects a crossed quote. Partial close receipts update cumulative reductions and resize remaining native protection; replacement protection is verified before retiring the old protection. Persisted POST boundaries prevent replay after ambiguous receipts.
- Official Stitch GEMINI_3_8_FLASH project8978791379095391159 screen0dc09018ec5241fc90f42a3909fa0ef4 produced HTML/image before implementation; provenance in docs/design/stitch-20261027-home-ticket/. Design examples and all browser trading responses are synthetic. Final focused Python suites:103+123 passed;16 related frontend tests passed, following the earlier focused browser/asset suites. Chrome and iPhone WebKit390/320 verified fixed entry, mirrored targets, draft restore, preview,25percent partial close, TP/SL editing, old-entry removal, no overflow and zero page exceptions. Evidence: output/playwright/home-ticket-20261027/. Real iPhone backdrop rendering remains unverified; the previously established Playwright WebKit backdrop-rendering limitation remains.
- Reload exposed the pre-existing missing-zero-USDT startup failure. OKX documents non-zero currency rows only; the manual idle reader now accepts an explicit empty USDT collection only with no open positions, while malformed receipts and other execution readers remain strict. New entry defaults return a clear insufficient-USDT409. Worker restarted successfully with free-manual-20261027-v3 / free-management-20261027-v2, heartbeat current and no errors. No real order, leverage or protection mutation was sent by QA. Monitor reloaded; paper worker running/no error and patrolPID1266 current with four enabled/error-free schedules. Overall market/model readiness remains false as before.
- Task browser sessions home-ticket/home-ticket-webkit closed; all recorded browser PIDs exited. Luna delegation stopped because actual runtime model/effort could not be verified; primary completed work. Learnings: fixed-chart-orders-must-bind-server-range.md, manual-partial-exits-need-remainder-ownership.md, empty-currency-balance-must-not-kill-manual-worker.md. Unrelated Bark edits and archive changes remain outside this commit.

## 2026-10-27 · Global spike alarms and full-screen abstract red motion

- Connected Claude's `/api/spike-alerts/stream` once per document, without polling. Full active snapshots survive route changes/reconnect/version resets. Explicit ID acknowledgement syncs across pages; failed acknowledgement stays visible. Six-hour expiry follows server time plus monotonic elapsed time. User-enabled Web Audio loops while an alarm is active and stops on acknowledgement, expiry or page exit.
- Owner rejected the initial glass card and then the physical siren icon. Final UI fills the viewport with a fixed crimson theme, 1.8s alternating light curtains/breathing, a 2.1s sweeping wave and staggered abstract rings. No lamp object, white border or enclosing card. Self-hosted Motion14.6.0 (MIT) owns transform/opacity animations; hidden, closed and reduced-motion states release them. The normal entry is one 44px bell icon. Removed the history/status pill and its stylesheet; demand-driven history still loads.
- Official Stitch `GEMINI_3_8_FLASH`, project `8978791379095391159`: initial `9ed933aed1ec466da45ef359e4d52fb4`, red revision `bb671eafc4fc4cc893133673ca1301a0`, final abstract design `b8f0df9af24b482c874dc308a5bd50dd`. HTML/images and provenance retained in the three `docs/design/stitch-20261027-*alarm/` / `*-red-siren/` folders before each implementation. Generated price-change/latency/risk-system claims were omitted; no private account material supplied. Animation source/license are in `static/vendor/motion-*`.
- Validation: final 46 focused frontend tests and 18 backend/asset tests passed. Chrome and public iPhone WebKit verified 1440/390/320 layouts, full viewport and zero border radius, icon-only entry, no history pill, actual animation phase changes, multiple alerts, explicit acknowledgement, sound enabling and animation cleanup/reduced-motion restart. Earlier real test ID1 reached desktop/mobile via SSE; mobile acknowledgement closed desktop and stopped its actual audio buffer. Later visual QA uses browser-only fixtures with intercepted acknowledgements. Old backend test cases were not replayed. Evidence: `output/playwright/spike-alarm-20261027/` and `output/playwright/red-siren-20261027/`.
- A real 320px ring overflow and global glass border override were fixed. A QA media-event timing assertion was corrected to wait for the event; WebKit reload-time access-control failures remain in earlier logs, followed by a clean fresh-document public pass. Existing external coin-icon404 remains unrelated. Final settings-opacity check also protects readability against concurrent chart-glass styling.
- Both task sessions closed and recorded process cleanup verified. Alert/paper workers and patrol heartbeat/schedules checked after cleanup; no service restart or trading actions. Luna delegation stopped because runtime model/effort could not be verified. Learnings: `global-alarms-need-authoritative-state-and-local-expiry.md` and `fullscreen-alarms-need-their-own-visual-and-motion-boundary.md`.

## 2026-10-27 · Quiet fill markers and double-tap chart focus

- Removed large entry/partial-close/exit badges from the paper chart; retained smaller directional triangles, price lines and original fill geometry. Double-click or touch double-tap anywhere on the chart layout (including axes and non-control panel space) hides/restores all paper floating chrome and the global alarm settings dock. Form controls keep native activation; active alarms remain independent. Open panels and modal input values survive; leaving the workspace resets focus. Canvas and live feed stay mounted; Escape restores tools.
- Official Stitch `GEMINI_3_8_FLASH`, project `8978791379095391159`, screen `86d19d3dabb141408620cf1ad0ce4553`, supplied verified HTML/image before implementation. Sources: `docs/design/stitch-20261027-chart-focus/`. Glass fill is now 16–18% light / 22–24% dark with 14px blur; prompts used synthetic data only.
- Validation: 62 focused frontend tests and 5 asset-delivery tests passed; final focus/marker subset 13 passed. Public Chrome and iPhone WebKit at 390/320 verified hide/restore, axis gestures, panel/modal restoration, theme material values, unchanged chart/time range and route reset with zero JS exceptions/application writes on fresh-document runs. Initial warning-tint mismatch was fixed; an early WebKit one-bar range delta came from the first forming candle arriving during bootstrap, so final QA waits for the current hour's bar. Reload-time WebKit access-control failures remain in earlier logs; fresh-document public QA passed.
- Visual limit: WebKit's independent red/blue-stripe backdrop test also failed to render blur, both headless and headed. CSS supports/computed values cannot certify actual iPhone material. Chrome renders the glass; no speculative WebKit-specific workaround shipped. Raw QA: `output/playwright/chart-focus-20261027/`. Learnings: `chart-focus-must-release-modal-inertness.md`, `backdrop-support-needs-rendered-proof.md`.
- Task-owned Chrome/WebKit sessions and the Stitch tab closed; all 15 recorded browser PIDs exited. Paper worker alive/queue0/error null; patrol heartbeat current, four schedules enabled/no errors. Market/model readiness remain false as before.
- Frontend-only; no backend restart or trading/account/strategy mutation. Unrelated simultaneous alarm and history-status changes are preserved. Requested Luna mapping was stopped because actual runtime model/effort could not be confirmed.

## 2026-10-27 · Paper chart 12H and quiet historical levels

- Added 12H between 4H and 1D. UI routes, cold warmup, native history/live candles, WebSocket, IndexedDB restore, older-page browsing, moving averages and fill geometry use the same 43,200,000ms period; native OKX mapping is `12Hutc` (Beijing 08:00 / 20:00). No scanner interval or strategy changes. The running monitor already accepted the new endpoints during validation; this task did not restart it.
- Older weekly segments keep their original prices, anchors, expiry and fade; inline Chinese labels and prices appear only on the API-selected display range. This preserves the latest completed-week labels while NY Monday is still developing. New-week anchors demote prior labels without removing their lines; panning does not redefine the current group. Optional internal Fibonacci lines follow the same rule.
- Official Stitch `GEMINI_3_8_FLASH`, project `8978791379095391159`: 12H screen `85cb39fc50b242b2a768948430fc8522`; historical-label refinement `edc75895cf3640c090680fdeb1e1fd18`. Images, HTML and provenance saved before implementation in `docs/design/stitch-20261027-chart-12h/` and `docs/design/stitch-20261027-history-lines/`. Existing native controls and display-range semantics were retained.
- Validation: 93 focused frontend tests and 36 Python candle/history/asset tests passed; 33 related frontend tests rechecked after label refinement. Real desktop and public iPhone WebKit verified 12H switches/reload, six averages, UTC live/closed data and history beyond 720. Public desktop/iPhone verified old lines with no labels, active labels, optional levels, both themes and mobile 390/320. First desktop direct live-endpoint check failed once; immediate inspection returned 200/no error and the unchanged full QA passed on repeat. Failure log retained, cause unclassified. Raw evidence: `output/playwright/chart-12h-20261027/`.
- Public label QA had zero JS exceptions and application writes. Temporary sessions `chart-12h` / `chart-12h-webkit` closed; all 11 recorded browser processes exited. Paper worker alive/queue0/no error; patrol PID1266 heartbeat current and four schedules enabled/no last error. Market/model readiness remain false as before. Learnings: `chart-periods-require-one-native-time-boundary.md` and `chart-labels-follow-the-active-display-range.md`.

## 2026-10-27 · Fifth paper account spike connected to the web UI

- Home and analytics selectors now expose all five independent accounts: 60m, 5m, v3, v3add and spike. spike uses API key `spike` / internal track `3060`; cold links, chart selection, account totals and the existing paper-close identity remain aligned. Missing spike stays unavailable instead of falling back to v2.
- Original signal `ema_order` now travels through the read-only position JOIN, including closed trades after their signal leaves the latest-100 page. Touch rows and spike rows show the original 0–4 score; missing remains unknown. Shadow labels distinguish low order, missing order and full slots; shadow rows have no close action and remain excluded from funded accounting. Rules read spike's frozen configuration and identify OKX 15m; no Binance line was created. The paper-close estimate also applies the engine's configured per-tranche margin-loss cap.
- Official Stitch `GEMINI_3_8_FLASH`, project `8978791379095391159`, screen `f56301aa72694cb58f4450afb088c191`, based on compact screen `85d6bca11ccc4eeda602a85fa14a4590`, produced image/HTML before UI implementation. Source assets: `docs/design/stitch-20261027-spike-account/`. Generated strategy/performance claims and placeholder amounts are not execution authority.
- Validation: 49 focused frontend tests and 59 paper/analytics/asset Python tests passed; final small copy adjustment rechecked 25 relevant frontend tests. Chrome verified all accounts, reverse analytics selection, cold reload, 1440/390/320 widths and both themes. Browser-only fixtures verified funded/shadow filtering, all three causes, zero vs missing order, historic order display, close-sheet identity and missing-account isolation. Public spikeok.com passed Chrome 1440/390 and iPhone WebKit 390/320 with no JS exceptions or application writes. Initial QA rule checks needed a fresh document and explicit disclosure state; failure logs retained. Existing external icon 404/preload/OKX navigation-socket warnings were observed. Evidence: `output/playwright/spike-account-20261027/`.
- Restarted the monitor once for the read-only field projection; unrelated pre-existing Bark edits were already loaded before this restart. Final paper worker alive, queue 0, no error; spike still 1000 USDT with zero trades. Patrol PID1266 heartbeat current, all four plans enabled without errors. Main scanner rebuilding after restart with zero errors; overall readiness remains false and model_ready false. No account balances, orders, strategy thresholds or model eligibility were changed by this task.
- Temporary browser sessions `spike-account` / `spike-account-webkit` closed and all recorded descendant PIDs exited. Luna delegation stopped because actual runtime model/effort could not be confirmed. Learning: `docs/learnings/original-signal-filters-must-survive-ledger-pagination.md`.

## 2026-10-26 — 12H BTC/ETH/XAU partial recovery baseline completed

- Native Nautilus1.231.0, no initial backstop, touch12H Beijing08/20, original Monday70/15/15 targets; failed close waits fee recovery. Current maker2bp/taker5bp.
- BTC/ETH/XAU full n171/118/66; realized+69.7585/+97.6492/+12.5420R, open marks-490.2579/-95.5992/-17.8941R; total-420.4994/+2.0500/-5.3521R. Common2025-04-14 total-24.9645/-51.9280/-5.3521R; all matched excessCI spanszero.
- PureBE145/98/58; currently underwater4/4/1. Worst priceMAE-618.27/-166.79/-32.61R; sourceextremes verified. Highclosedwin rates do not imply safe return. No initialrisk budget, leverage or accountreturn; funding/margin/liquidation unmodeled.
- Results `experiments/active/exp-monday-12h-partial-recovery-20261026-v1/`; positions.csv/legs.csv.gz/fills.csv.gz retained. 3936nativelegs7872fills reconciled.7behavioralpass; broadboundaries170pass8unrelatedexistingfail, causes in validation.json. No historical rerun needed.
- Notion https://app.notion.com/p/4058856479af81dfa870d53c060ff658 ; no production eligibility. Owner wants data first, backstop study later; no stop sweep run.

## 2026-10-26 · OKX gold recovery limits: 1H/4H and Monday-edge comparison

- Owner fixed XAU-USDT-SWAP, first weekly-side resting outer1618, touch-candle4H close; added1H then Monday-edge entries. Same1percent backstop/far Monday target. Reclaimed closes protect fee-neutral STOP_MARKET; failed reclaim waits at fee-neutral LIMIT with backstop. Fee-band recovery is recorded; equal fee-stop closes exit by native market after a rejected-amendment minimal reproduction.
- NativeNautilus1.231.0:1360positions2720fills checked,366signalrows,183distinct signals,allresolved.8behavioral+75boundarychecks pass. Initialnative execution failed on equalquote amendment;failed-equal-stop.log retained. Corrected builder029afa5c76 preceded valid history run; no secondengine.
- 1618 4H/1H net-9.331/-5.273R(n66each),backstops15/9,BE48/55,TP3/2. Mondayedge4H/1H net-1.827/+0.208R(n117each),backstops27/19,BE75/88,TP15/10. Every absolute mean weekCI spanszero. Edge1H isflat and has no matchedrandom edge;1618 beats random relatively while stilllosing absolutely.
- ExactOKX data2025-04-09..2026-10-26,162780continuous5m,30334cached minute-derivedbins reused. Public instrumenttick0.1 and fee group4 Lv1maker2bp/taker5bp readOct26.1R=entry1percent;independentR,not fundedaccount. No funding/slippage/capacity;OHLC syntheticpath,zero samebarSL/TP ambiguities. Data loader moved from data to evaluation to preserve lower-layer importboundary;no data replay.
- Results `experiments/active/exp-xau-monday-recovery-20261026-v1/`;Notion4058856479af81808638d7bdc4be437e. Learning `docs/learnings/recovery-limit-and-protective-stop-have-different-fee-prices.md`. Alltraining/productionfalse;no services,models or liveorders changed. Luna return couldnotverify model/effort;delegate stopped and primarycompleted work.

## 2026-10-26 · Native chart history and every visible NY week

- BTC/ETH/XAU now page native OKX history on left-edge browsing without a total candle cap; other symbols retain 720 closed candles. Shared analytical/latest snapshots and bounded IndexedDB storage remain unchanged. Home adds native UTC daily bars for long-range browsing. No order, account, notification or strategy setting changes.
- Historical NY Mondays use the existing complete confirmed 5m anchor reader and DST calendar. Every visible week can load its high/low and outer 1.618 lines; internal levels obey the existing toggle. Each segment expires at its own next NY Monday, historical lines are muted, and autoscale ignores offscreen weeks. Missing Monday data stays explicit and retryable.
- Official Stitch `GEMINI_3_8_FLASH`, project `8978791379095391159`, screen `b6adf7933bf9478c8113d8d2d9a20660` produced image/HTML before UI implementation. Timeout recovery required loading the authenticated project after releasing this task's paused browser debugger; no duplicate generation. Source assets in `docs/design/stitch-20261026-weekly-history/`.
- Validation: 33 Python tests, 43 history/chart frontend tests and 50 related feed/cache/paper/patrol frontend tests passed. Chromium and iPhone WebKit verified 720→1020 historical prepend with unchanged timestamps, multiple weekly segments, internal toggle, coin/day-period switching and altcoin cap. Injected 503 retained candles; retry added another 300. Public Chrome/WebKit verified 1021 displayed rows and 10/7 weekly groups, no overflow or JS exceptions; only automatic Cloudflare `/cdn-cgi/rum` POST, no application writes. Earlier WebKit reload sweep emitted navigation-time access-control errors; a fresh-document run passed with zero errors/writes.
- Data boundary evidence: daily paging read BTC/ETH 2490 closed days back to 2020-01-01; an earlier page returned OKX code 0 / empty data (also verified directly). XAU returned 565 days to its 2025-04-09 listing boundary. Empty pre-listing-date gaps remain retryable, not a fabricated all-history completion. These are current source observations, not a new imposed cutoff. Evidence: `output/playwright/weekly-history-20261026/`.
- Web service restarted once to install read-only history endpoints; existing uncommitted Bark changes were already loaded before restart and remain outside this commit. Paper worker alive/queue0/error null; patrol PID1266 heartbeat current and all four plans enabled. Main scan restarted normally with no errors; overall health remains non-ready while warming and model_ready remains false under the existing no-active-model state. Learning: `docs/learnings/chart-history-must-not-expand-the-live-snapshot.md`. Temporary Chromium/WebKit sessions and QA server closed; all recorded descendant PIDs exited. Task-owned Stitch tab/group closed, preserving the three original Chrome tabs.

## 2026-10-26 · Mobile SPIKE menu: Safari touch navigation restored

- Reproduced with WebKit 26.6 / iPhone 15 touch: tapping a menu anchor focused the containing `main` before click; the global focus dismissal hid the anchor and retargeted click to the background. Chromium mouse and touch emulation did not reproduce it. Guard the active pointer's menu through native activation, defer click dismissal until the next task, and remove Monday's duplicate synchronous close handler. Existing appearance and routes remain intact.
- Official Stitch `GEMINI_3_8_FLASH`, project `8978791379095391159`, screen `a79f437582af48efb7c69b890f97443b` produced verified image/HTML before implementation; assets/provenance in `docs/design/stitch-20261026-mobile-menu-fix/`.
- Validation: 33 focused navigation/paper tests and 5 asset-delivery tests passed. The additional Monday suite passed 19/22; all 3 failures also reproduce with the unchanged HEAD source because old assertions expect `/api/monday-fib?compact=true` instead of `/api/monday-fib/display`. No assertion was weakened. The new Safari regression fails before the fix and passes after it.
- Browser: WebKit touch at 390/320 and Chromium at 1440/390 verified all four home destinations, Back, same-route closure, outside tap, Escape/focus return, Monday menu and More. Local runs had zero JS exceptions and zero write requests. Public `spikeok.com` direct WebKit taps to records and inspection passed with zero JS exceptions; the rapid public route sweep reached its final check but recorded API access-control exceptions, retained as a separate limitation. Evidence in `output/playwright/mobile-menu-20261026/`.
- Both temporary browsers closed; PIDs96539/97314 and failed-start PID96988 exited. No backend restart. Paper worker alive/queue0/error null; patrol PID1266 heartbeat current and four plans enabled. Learning: `docs/learnings/touch-focus-fallback-must-not-dismiss-a-pending-link.md`. Luna delegation was stopped because returned metadata could not verify runtime model/effort; primary completed diagnosis and fix.

## 2026-10-26 · Compact account selection and latest-touch list

- Owner requested scalable version selection, denser coin rows and search without a dedicated row. Official Stitch `GEMINI_3_8_FLASH`, project `8978791379095391159`, screen `85d6bca11ccc4eeda602a85fa14a4590` preceded implementation; design PNG/HTML/provenance in `docs/design/stitch-20261026-reclaim-compact-controls/`.
- Replaced the four horizontal account buttons with one native dropdown beside the ledger shortcut; analytics uses the same compact pattern and shared selection handler. Context height is 115px (previously 163px). Account identities, backend IDs, balances, chart semantics and deep links are preserved.
- Latest-touch list now uses a 48px title/search/close header and 48px filter area (previous combined height 158px). Search expands within the header. Coin rows are 56px (previously 90px), with time/warning visible and TV/inspection/favorite/remove in a disclosure tray. Action hit areas stay 44px. Keyed refresh retains disclosure, focus and scroll. Escape collapses search without Chromium clearing only the input and leaving a stale filter.
- Validation: 34 existing frontend tests and 5 asset-delivery tests pass. Browser checked all four accounts and analytics in both directions, reload, all list filters, local favorite/remove/undo, chart selection, search/clear/Escape, and changing-quote refresh. Layouts 1440/1000/390/320 in both themes have no horizontal overflow; analytics filters stay in one row. Local QA recorded zero JS exceptions or API write requests. Public `spikeok.com` verified dropdown keyboard typeahead, linked analytics and inline search at 1440/390. Raw evidence: `output/playwright/reclaim-compact-20261026/`.
- Temporary browser `reclaim-compact` closed and PID93372 exited. Paper worker alive, queue0/error null; patrol PID1266 heartbeat current and all four schedules enabled. No backend restart or strategy/account writes. Learning: `docs/learnings/inline-search-collapse-must-cancel-native-escape.md`.

## 2026-10-26 · Signal center: four independent paper accounts connected

- Completed the pending v3/v3add UI after official Stitch `GEMINI_3_8_FLASH`: project `8978791379095391159`, screen `571f390de49b49fcbf11ae0c054d3c95`, base `f6ca485dbc9440bcabc2a9058a9d4768`. Actual screenshot/HTML and adaptation provenance: `docs/design/stitch-20261026-reclaim-four-accounts/`. Reused existing Apple Glass/navigation; synthetic design text is not strategy authority.
- Home and analytics now select `60`, `5`, `v3`, `v3add`; account labels, independent balances, fills, curve and deep links stay aligned. Analytics validates internal IDs `1060`/`2060`; cold startup reads the selected account. Missing named accounts show unavailable rather than v2 fallback.
- v3/v3add show score-dependent target labels, recorded backstop and `be_price`, plus `backstop`/`reclaimed_inside_be` exits. Charts retain original entry, use actual BE protection and end the backstop at its activation time. Old null BE prices keep their recorded legacy entry stop. v3add labels main/add legs, shows pending adds separately with parent identity, limit, original parent notional ceiling and expiry; pending orders are excluded from position/closed counts and PnL. Scope/search filters and existing paper-close identity retain account semantics. Shared raw touch score labels explicitly remain v2.
- Validation: 44 focused frontend tests and 51 paper/analytics/asset Python tests pass. Real browser checked all four accounts, reload/deep link, analytics switch, 1440/390/320 widths and both themes; synthetic browser-only pending/filled legs checked parent BE price, targets, scope/search and close-dialog identity. Zero write requests or JS exceptions. Public `spikeok.com` browser verified new v3add view and analytics. Command-line public fetch receives Cloudflare1010; browser works. Observed existing preload/OKX socket warnings during rapid navigation, no application exceptions.
- Raw QA in `output/playwright/reclaim-v3-20261026/`. Static bytes are versioned automatically; no service restart, orders, balances, strategy or fees changed. Paper worker alive/queue0/error null; patrol worker1266 heartbeat current and four enabled schedules retained. Temporary QA browser and Stitch tab cleaned up at task end. Learning: `paper-account-ui-must-preserve-execution-identities.md`. Requested Luna delegation could not verify runtime model/effort from its creation return and was stopped; main completed integration.

## 2026-10-26 · Confirmed 5m pin stop, Monday key targets and same-risk sizing

- Owner requested original Monday keypoint TP instead of fixedR, retaining confirmed5m entry and bearish-pin-low stop, and asked whether larger same-risk sizing increases profit. Reused632symbol prior admitted events/scores and original five5m order target prices, including mirror/controls. Only target geometry changed; fixed pin and original post-entry feeBE both retained. Target already passed or fee-insufficient at entry is skipped, never a fake instant TP.
- NativeVectorBT once:106346events,1063460opportunity columns,1058088executed positions,2116176fillrecords.8focused behavioral tests pass; every closed position fee/PnL/geometricR/budgetR reconciles.100USDT illustrative total planned stop loss includes both taker fees and adverse stop slippage; entry price already includes entry slippage. Quantity=budget/plannedunitloss; unchanged Rbaseline linearly reweighted from saved fills, no replay. Builderddf687dd06 committed first.
- Scoredaltlong5883opportunities. Fixed-pin targets near/0.5/0.618/far/opposite1618 enter5395/5741/5769/5824/5845;TP1919/1470/1404/1189/974;SL3472/4263/4357/4626/4858;unfinished4/8/8/9/13. Mean perclosedtrade at100USDTplannedrisk=-21.38/-22.65/-21.61/-22.05/-18.63USDT.2026=-19.38/-25.38/-25.22/-33.25/-31.54. All10keypoint/BE combinations negative fullhistory,2025and2026 on same-risk basis;fullhistory opposite1618 equal-notional+21.24bp does not contradict negative risk-weighted PnL.
- Near target skips488(471passed/unreachable,17fee-insufficient); other targets skip142/114/59/38. Primary selection counts pre-entry skips0 and omits unfinished; pertrade metrics exclude skips.2025selectsopposite1618feeBE,2026mean-23.09USDTperresolvedopportunity(95percentweekCI-30.74to-16.67),versuscached2025selected5RfeeBE-20.89. Matchedsignal/random-23.49/-39.84 gives+16.36relativeedge but both lose. Supplementary entered_closed_pairs.csv has consistent executed-only denominators.
- Median notional per100USDTplannedrisk5077to5511;99percentile~22k;maximum~30.8k. Same entry/stop implies exactly same size regardless of target. Recorded overlap as high as137positions and~471600USDTnotional across fixed-target arms; this is independent exposure, not a feasible cashaccount. Stops ordinarily lose100USDT, observed worst~101.50 fromgaps. Funding/margin/capacity remain unmodeled. Originalentry-week targets do not roll nextweek; sourcegap/end censoring preserved.
- Results and reproduction `experiments/active/exp-monday-5m-confirmed-keypoints-20261026-v1/`; Notion `4058856479af81bfac30ddc49688ef3e`. Learning `fixed-risk-sizing-must-include-fill-costs.md`. All production/training flagsfalse; no live/score changes, no additional report. Luna delegate actualmodel/effort could not be verified from tool output and was stopped; main performed work.

## 2026-10-27 · Lottery version (very high payoff, blow-ups accepted)

- Owner wants a small-stake version: blow-ups acceptable (about 5 in six months), aiming at runs of tens to thousands of times the stake. Replays use existing fills (`exp-monday-limit-lottery-20261027-v1`): one position at a time, all-in or half-in margin at 3-20x, restart after a blow-up, 39 rolling half-year windows from 2023.
- Best family: v3 score ≥0.6, opposite 1618 target, −30 percent backstop.
  - All-in 5x reaches 10x / 100x / 1000x in 47 / 15 / 10 percent of windows, with 3.8 blow-ups on average (13 at p90).
  - Half-in 5x: 25 / 11 / 1.5 percent with 0.4 blow-ups.
  - 10-20x blows up 10-20 times per window and loses.
- Caveats:
  - all 100x+ cases come from one streak in mid to late 2025; held accounts gave it back with many blow-ups;
  - 2023-2024 windows peak at 1.4-25x;
  - a withdrawal rule is needed and was not modeled yet.
- Learning `lottery-odds-from-overlapping-windows-can-be-one-streak.md`.

## 2026-10-27 · Full-screen spike alarm: backend ready, page pending Codex + Stitch

- Owner wants the spike alert as a full-screen alarm with sound on the web page. UI goes through Stitch (`GEMINI_3_8_FLASH`, project 8978791379095391159) and Codex. Claude did not write UI.
- Backend (commit `2f54a8f757`, monitor restarted, verified):
  - `GET /api/spike-alerts` returns `active` (unacknowledged alerts of the last 6 hours) and `alerts` (last 50).
  - Each alert carries `id, side (lower/upper), n, symbols, first_ms, last_ms, created_ms, text, telegram, bark, ack_ms, test`.
  - `POST /api/spike-alerts/{id}/ack` with same-origin headers and `x-spike-action: ack-spike-alert` is idempotent and shared by every open page.
  - `POST /api/spike-alerts/test` with `x-spike-action: test-spike-alert` creates a local-only alert: never pushed, no cooldown, `test=1`.
- Owner rejected 5-second polling and asked not to load the backend. Commit `ff2535d47d` adds `GET /api/spike-alerts/stream` (server-sent events). It pushes `event: alerts` with `{version, now_ms, active}` on connect and on every create, ack or test, plus a `: ping` comment every 25 s. Pages wait on asyncio queues woken by the alert thread, so there is no polling; at most 20 streams.
- Page contract for Codex:
  - open one `EventSource('/api/spike-alerts/stream')` (it reconnects by itself) and do not poll; when `active` is non-empty, show a full-screen overlay over every page;
  - show direction (lower = 急跌做多方向, upper = 急涨做空方向), coin count, coin names, Beijing time and "只通知，不动仓位";
  - loop an alarm sound until the user clicks 知道了, which posts the ack;
  - browsers block autoplay audio, so the page needs a one-time "启用报警声音" click per session and must show that state;
  - test with the test endpoint.

## 2026-10-27 · Spike alert threshold against trade quality

- Owner asked which threshold makes the trades after the alert more accurate. Cluster size at fill against trade outcomes (`exp-monday-spike-alert-quality-20261027-v1`):
  - v2 near-target trades are not better in clusters (2025+ worse at ≥8);
  - v3 and spike-HW trades are better mainly in the largest market-wide crashes (50+ coins on Binance, about 25+ on OKX-listed coins). Example: OKX v3 ≥25 wins 50 vs 30 percent at +6.1 vs +1.3 percent per trade, but only 21 episodes (6 since 2025).
- Recommendation: 25 on the live OKX count. Owner approved: threshold now 25 (commit `05d2f592f3`; monitor restarted, `/api/spike-alerts` shows threshold 25, Telegram and Bark on, worker alive). Still notify-only.

## 2026-10-27 · Market-wide spike alert (notify only)

- Owner rule: ≥8 coins touching the same-side 1.618 within 5 minutes triggers a push.
- `yoyo/monitor/spike_alert.py` listens to live Monday-fib touches and pushes once per side per 30 minutes (Telegram + Bark). It keeps its own book (`spike-alerts.sqlite3`); `GET /api/spike-alerts` is read-only. Commit `19fcf17878`; monitor restarted, worker alive, both channels configured; 8 tests pass.
- Unrelated `tests/monitor/test_market_sync.py::test_observer_cycle_and_api` fails as before (market sync was retired on 2026-10-23).
- History (`exp-monday-spike-alert-history-20261027-v1`): at threshold 8 the alert would fire about twice a week on OKX-listed coins (2025: 97, 2026 to Sep: 75). Extreme events are clusters of 25 or more coins, about ten in 2026.
- Owner noted a human cannot react in time and wants the system to act. Entries already rest as limit orders (automatic in paper). Real-money execution needs an owner-approved automated program (Binance trade key, margin plan for many resting orders, kill switch). Claude does not place or change real positions.

## 2026-10-27 · Return-first version (in progress) and the cluster days

- Return-first sizing on existing v3 / v3add fills (`exp-monday-limit-return-opt-20261027-v1`). Settings chosen as the best 2020-24 multiple under the 40 percent limit broke in 2026 (DD 60-76). Settings that stay ≤40 percent drawdown in all four periods (in sample):
  - v3add, 20 slots × 3x: 12.6 / 9.3 / 5.4 / 6.2x, DD 9 / 19 / 39 / 9;
  - v3, 20 slots × 3x: 9.1 / 6.7 / 4.0 / 4.1x, DD 11 / 12 / 30 / 8;
  - compare v3 at 1x: 3.9 / 9.3 / 2.5 / 3.0x.
  - Profit lock at the reclaim (breakeven +50 / 100 / 200bp, `exp-monday-limit-be-lock-compare-20261027-v1`) raises the v3 win rate from 34-40 to 66-73 percent. Mean per trade and the sizing multiples stay the same (v3 20 slots × 3x: 9.1 / 6.7 / 4.0 / 4.1x at +17bp, 8.5-8.6 / 6.5-6.8 / 4.2-4.3 / 4.2-4.3x with a lock). The return-first rule therefore keeps its +17bp breakeven. A lock is an option only for the win rate.
  - Pyramiding (滚仓) for the high-payoff version (`exp-monday-limit-pyramid-20261027-v1`) is rejected in sample. Adds at the Monday edge / mid / far edge (/ opposite 1618) with a step stop lowered the total from 31.3 to 28.5R (opp1618) and from 38.1 to 34.4R (opp2618). The step stop alone costs 2.7 / 6.2R; the legs fill in 39-45 percent of events and win 18-27 percent of those. Time to 100x is not shorter. Best high-payoff setting stays score ≥0.6 opposite 1618, all-in 3x: 100x from 94.5 percent of monthly starts, median 566 days, 2 blow-ups. All-in 5x: 402 days, 7 blow-ups.
- Cluster days of the spike rule:
  - each filled inside a single 5m bar: 2026-08-22 13:10, 2024-02-29 01:30, 2023-11-10 00:10 (Beijing; all longs, market-wide flash crashes); 2024-01-03 20:05 (mixed); 2025-10-11 05:25 (6 shorts during the crash rebound).
  - 70-85 percent of those fills hit the target in the same 5m bar.
- An alert cannot help the entry; only resting orders catch it. Live trading would need margin for many resting 1.618 orders at once, which no backtest models yet.

## 2026-10-27 · spike-HW trade frequency

- 219 backtest trades on 111 Beijing trading days. Only 6 before 2023. Since 2023, 35 percent of weeks have a trade; active weeks average 3.2.
- 84 percent of trading days have one trade. The top five days (2026-08-22: 37, 2024-02-29: 30, 2023-11-10: 16, 2024-01-03: 7, 2025-10-11: 6) hold 44 percent of all trades. The 10-slot cap binds only on the top three.
- Gap between trading days: median 5.5 days, 90th percentile 51.
- Forward pace since 2025: 1.7 trades a week on the Binance universe (50 trades in about 30 weeks), but only 0.5 a week on OKX-listed coins (about 97 weeks). The OKX paper account alone cannot reach the 50-trade review in reasonable time, which is another reason to get Binance data.
- Chart `experiments/active/exp-monday-limit-spike-hw-20261026-v1/4_frequency.png`, numbers in `frequency.json`.

## 2026-10-27 · Decision: no other-level backtest; wait for forward paper data

- Owner: "先不跑其他点位，等模拟盘前向数据". Rules stay frozen (v2 60m/5m, v3, v3add, spike).
- No parameter changes until forward data accumulate. Review per account at about 50 funded closed trades (spike first). Compare win rate and mean per trade with the backtest restricted to OKX-listed coins, and check ema_order on all signals.
- Binance paper line still blocked by the network (US proxy exit 451, VPS unreachable).

## 2026-10-27 · Overfitting check: spike subsets against matched random entries

- Owner asked whether the strategy is overfit. Codex's ledger has up to three matched random controls per signal: same symbol, NY week, direction and volatility bucket, same limit distance and target percentage (O-H-L-C path only, 60m, near target). Paired excess per trade:
  - all signals +32bp, CI [25, 39];
  - score ≥0.6 +127bp, CI [80, 172], significant in 2020-24 and 2025-26;
  - spike-HW events (score ≥0.6, order ≥3) only +70bp, CI [−26, 170], p .07 (2025-26 p .19), with control win rate 75 percent.
- Reading: the order ≥3 cut mostly selects favourable weeks, not better entry moments.
- Booklet updated with this section. Registry `exp-monday-limit-spike-controls-20261027-v1`; learning `high-win-filters-can-select-good-weeks-not-good-entries.md`.
- Owner also asked whether other range levels (Monday edge, 0.618) work. The earlier hold-rate study found 1.618 no different from 1.272 / 1.414 / 2.0 or random levels. The limit-reclaim P&L at other levels has not been run: the Codex runner hard-codes 0.618 extension in `monday_limit_reclaim.prepare`.

## 2026-10-26 · spike-HW v1: booklet and fifth paper account

- Owner asked for a booklet on the highest win-rate version and for it to join paper trading next to the four existing accounts.
- Rule (frozen in `exp-monday-limit-spike-hw-20261026-v1/config.json`):
  - entry score ≥0.6 and 15m EMA order score ≥3;
  - near Monday edge target, 60m confirmation, −18 percent backstop (inside the 5x liquidation distance);
  - 10 slots at 5x, margin = equity / 10, loss capped at the margin.
- Backtest (existing near_P18 fills, worse path):
  - 219 trades over 181 symbols and 73 weeks; only 6 before 2023.
  - Win rate 85.4 percent (92.6 / 78.8 / 84.7 / 85.7 for 2020-24 / 2025 / 2026 / last five months). Mean +2.41 percent, profit factor 2.51, max losing streak 3.
  - 63 percent of trades fill and hit the target in the same 5m bar.
  - 1000U continuous: 4.65x, max DD 21 percent.
  - In sample.
- Booklet outside the public repo: `~/Documents/Codex/2026-10-26/spike-hw/outputs/index.html` (rebuild `python3 work/build.py outputs`).
- Paper: account `spike` (track 3060) in `yoyo/monitor/monday_reclaim_paper.py`, commit `fe704bfd56`. The EMA order score is computed from OKX 15m candles and every signal now stores `ema_order`.
  - Monitor restarted; all five accounts respond, worker alive. 53 paper and analytics tests and 5 frontend tests pass.
  - UI tab for v3 / v3add / spike still pending Codex + Stitch.

## 2026-10-26 · Spike subset: more win-rate levers; plan A against order ≥3

- Inside score ≥0.6 + order > 0, near target 60m, win rate by EMA order score: 1 = 75 percent, 2 = 73, 3 = 88, 4 = 89. Order ≥3 by period: 94 / 80 / 92 / 94 percent, 219 trades.
- Not levers: 5m confirmation (win rate 68 vs 81 percent) and side (flips by period). Score ≥0.8 wins 90 percent but has only 69 trades.
- Plan A (near target, −20 percent stop, 10 slots, 5x): 4.43 / 3.30 / 3.22 / 3.11x, DD 12-22, win 72-86 percent. The same plan with order ≥3: 2.44 / 3.09 / 3.19 / 2.69x, DD 9-18, win 80-88 percent.
- At 5x the −20 percent stop lies beyond liquidation (about 19 percent), so liquidation acts as the stop; an 18 percent stop or 4x would exit first (not run).
- The weak half is still the trades that do not fill and hit the target in the same 5m bar (2025-26 win about 50 percent, negative mean).
- In sample; candidates for a Binance paper shadow line. `exp-monday-limit-spike-more-20261026-v1`.

## 2026-10-26 · Spike subset: floating loss, tighter backstops, slots and leverage

- Spike subset = score ≥0.6 and EMA order > 0, 449 trades, Binance 5m.
- New native fills: 3 / 5 / 7 percent backstops for near and range 0.5 targets, 300 symbols per path, no errors (`exp-monday-limit-spike-stops-20261026-v1`). Analysis in `exp-monday-limit-spike-opt-20261026-v1`.
- Winners' floating loss before confirmation: median 2-4 percent, 75th 5-8, 90th 10-14. Losers' median 7.5-12.
- Near target, 2026 win rate and 1x multiple by backstop: none 81 percent / 1.53x, 30 percent 81 / 1.32, 20 percent 78 / 1.33, 15 percent 76 / 1.33, 10 percent 69 / 1.09, 7 percent 60 / 1.11, 5 percent 50 / 1.02, 3 percent 35 / 0.90. Stops under 15 percent kill winners.
- Slots and leverage that stay ≤40 percent drawdown in all four periods (in sample), 2020-24 / 2025 / 2026 / last5m:
  - near, 20 percent stop, 10 slots, 5x: 4.44 / 3.30 / 3.17 / 2.94x, DD 12-22, win 73-85 percent, 1-3 liquidations;
  - near, 20 percent stop, 5 slots, 5x: 4.77 / 5.17 / 3.85 / 3.56x, DD 30-40;
  - range 0.5, 30 percent stop, 5 slots, 3x: 6.66 / 5.57 / 3.92 / 4.96x, DD 22-30, win 58-70, no liquidations.
- Strict selection on 2020-24 picked 3 slots at 5x, which had a 63 percent drawdown in 2026. Nothing adopted.
- Learning `flash-wick-entries-need-room-and-leverage-picks-overfit.md`.

## 2026-10-26 · Live venue will be Binance; paper stays on OKX for now

- Owner: live trading of the 1.618 strategy will use Binance USDT-M. Paper trading stays on OKX until Binance data is reachable ("模拟盘暂时就用 okx"). After that, a Binance paper line is added and the OKX line kept as the comparison.
- Blocker: from this machine fapi.binance.com returns 451, because the proxy exit is Los Angeles. Both VPS hosts time out on SSH and port 8642. Fix needs the owner (proxy rule for Binance domains through a non-US node, or VPS repair). Do not use www.binance.com/fapi to get around the regional block.
- Comparisons of OKX paper with the backtest must restrict the backtest to OKX-listed coins (2026 v3: 263 of 829 trades) and compare per-trade results.
- Fee assumption stays OKX Lv1 (maker 0.02 / taker 0.05 percent). The Binance account tier must be read before live trading. The CLAUDE.md fee rule names OKX, so update it with the owner when the Binance tier is known.

## 2026-10-26 · Order-score subset with backstop and larger targets; OKX cross-check

- Score ≥0.6 and order > 0, from existing grid/levels fills, worse path, linear sizing. Trades: 143 (2020-24), 131 (2025), 175 (2026). With a −30 percent backstop (2020-24 / 2025 / 2026 / last five months):
  - near target: 89.5 / 72.5 / 80.6 / 87.1 percent win, 1.52 / 1.37 / 1.31 / 1.36x;
  - opposite 1618: 60 / 40 / 49 / 64 percent win, 2.70 / 2.72 / 2.13 / 2.50x, DD 11 / 13 / 32 / 10.
  - Farther targets earn more and win less. Full v3 still earns more (2026 2.59x).
- OKX check of the 2026 same-bar trades:
  - Most of them are on coins without an OKX swap (60 of 102).
  - Of the checked ones, the OKX wick reached the entry in 33 of 40 and also closed beyond the target in 25.
  - 2026-08-22 05:10 UTC was a real flash crash on both venues, shallower on OKX.
  - Only about a third of v3 trades are on coins OKX lists now. Per-trade results on those are similar.
- Owner asked to use whichever exchange earns most. On current evidence that is Binance (the backtest venue, about 3x the coins). No OKX-history backtest exists. Binance REST returns 451 from this machine. Real-money venue and any paper-engine data switch are owner decisions.
- Experiments `exp-monday-limit-order-targets-20261026-v1`, `exp-monday-limit-okx-wick-check-20261026-v1`; learning `backtest-venue-universe-is-not-the-trading-venue.md`.

## 2026-10-26 · EMA order score: equity replays and the near-target subset

- 1000U replays on the existing v3_be17 fills (worse path, linear sizing, gross ≤1x, 20 tie orders), 2020-24 / 2025 / 2026 / last five months:
  - v3 as is: 3.87 / 9.31 / 2.59 / 2.97x, DD 13 / 19 / 33 / 17. Reproduces the earlier replay.
  - Only order score > 0: 3.02 / 3.59 / 1.81 / 2.44x, DD 10 / 14 / 35 / 10. Lower return in every period.
  - Double size when order score > 0: 4.94 / 17.12 / 3.01 / 5.19x, DD 21 / 27 / 51 / 19.
  - Controls: doubling the order = 0 trades gives 4.35 / 16.40 / 3.09 / 3.25x (DD 17 / 27 / 51 / 32); doubling everything gives 5.59 / 29.11 / 3.39 / 4.64x (DD 25 / 35 / 58 / 28).
  - Reading: a sizing tilt, not a filter, and in-sample.
- Near target, score ≥0.6, order > 0 (v2_be17 fills): 449 trades over 300 symbols and 117 weeks.
  - Win 81 percent, mean +229bp, average loss −760bp, worst −42.5 percent (no backstop).
  - 1000U only 1.68 / 1.49 / 1.48 / 1.34x.
  - Half the trades fill and hit the target inside the same 5m bar (flash wicks, 100 percent win). The rest won 50.7 percent at −90bp in 2025-26.
  - Depends on Binance wick fills; needs an OKX cross-check and forward paper data.
- Samples chart `experiments/active/exp-monday-limit-near-order-samples-20261026-v1/samples.png`. Learning `high-win-rate-subsets-can-be-same-bar-flash-wicks.md`. Nothing adopted.

## 2026-10-26 · Win-rate levers from the backtested trades

- Owner asked what else raises the win rate. Read-only analysis of existing fills joined to the causal feature table v2. Builder `yoyo/evaluation/monday_limit_winrate_levers.py`; results `experiments/active/exp-monday-limit-winrate-levers-20261026-v1/`.
- v3 trades (early / late): win 39.9 / 35.0 percent, breakeven flat 33 / 32, failed reclaim 24 / 29, backstop 0.5 / 2.4. The target dominates win rate. The same score ≥0.5 events with the near edge win 67 / 65 percent at 189 / 114bp, against 40 / 35 percent at 353 / 176bp for the v3 far targets.
- Strongest entry lever: the 15m EMA order score before the touch (side-aligned; > 0 means price reached the level before the short EMAs turned).
  - v3 late win rate 35.0 → 45.3 percent, keeping 37 percent of trades; mean 176 → 308bp. Order score 4 wins 63.7 percent.
  - v2 late 34.5 → 54.4 percent.
  - Higher in every score band and every year. Timing checked: the last 15m bar closes at or before the fill bar opens.
  - With score ≥0.6 and the near target: 89.5 / 77.5 percent.
- Smaller confirmed levers: 15m volume-ratio mean, close vs EMA200, 12-bar return, approach speed, 24h drawdown. Side flips by period. Deeper −5 percent legs win 84 / 71 percent but fill on 39 / 46 percent of score ≥0.6 events.
- Win rate and mean can diverge: v2 score 0.375-0.5 with order > 0 wins more but loses money late. Nothing adopted. Next candidates:
  - an equity replay of the order score as a filter or size multiplier on the existing fills;
  - logging the order score on paper touches;
  - a framework run of a post-reclaim profit lock for the 32 percent breakeven exits.
- Learning `win-rate-levers-and-profit-levers-are-different.md`.

## 2026-10-26 · Notion overview of the 1.618 system

- Owner asked for one page that consolidates every 1.618 test, what works, what is worth more research, and the logic now running. Notion page `4058856479af81e6bbdbf1d39bd4875a` (under task 1.618挂单策略, marked pending validation) groups the work in three lines: the level itself (rejected), candle-confirmation entries (rejected or inconclusive), and limit + reclaim (current, partly effective). The v2 page links to it. No code or experiment changes.

## 2026-10-26 · Win rate by weekday, hour and US news: no stable effect

- Owner asked which weekdays and hours win most, and whether news matters. Read-only grouping of the existing NautilusTrader fills: v2_be17 (22,708 trades, 33.0 percent) and v3_be17 (2,108 trades, 36.7 percent). Worse path, 10bp slippage, win = net > 1bp (breakevens at +0.17 percent are flat). Builder `yoyo/evaluation/monday_limit_time_news.py`.
- Neither weekday nor hour separates beyond noise. Week-preserving cluster permutations give weekday p .13 / .19, hour p .06 / .18 and 4h block p .27 / .12 (v2 / v3). Bucket ranks between 2020-24 and 2025-26 are anti-correlated. The worst 3h window chosen on 2020-24 does not hold in 2025-26.
- News: v2 trades within ±2h of CPI, payrolls or FOMC win 35.2 vs 33.0 percent (p .51). v3 has only 53 such trades (35.8 vs 36.7). The FOMC window (45 percent) and CPI (30 percent) are post hoc.
- Forward-only candidates: Beijing 21-24h (US cash open) is lower in both periods (v2 −3.4 / −8.3pp, v3 −12.7 / −6.7pp), but it was found by looking at both periods. No filter is adopted. Results in `experiments/active/exp-monday-limit-time-news-20261026-v1/`; learning `best-hour-tables-need-select-then-confirm.md`.

## 2026-10-26 · Paper: breakeven at entry +0.17 percent and a separate v3add account

- Owner: move the paper breakeven to entry +0.17 percent for v2 and v3, and add a separate "v3 + add at -5 percent" account for scores >= 0.6 while the original v3 keeps running. Commit `cb1410f351`; monitor restarted, worker alive, queue 0, no error.
- Breakeven: every track (60, 5, v3, v3add) arms its stop at entry × (1 ± 17bp) for confirmations decided after the restart. A reclaim close that has not cleared that price exits at the close (`reclaimed_inside_be`, taker + slippage). Stops armed before the restart keep the entry price (`be_price` null).
- v3add has its own 1000 USDT (account track 2060; add legs are track 2061). The entry rules are frozen v3. For scores >= 0.6 an equal-notional limit rests 5 percent beyond the entry from the fill until the parent's confirmation close. The add shares the parent's reclaim test (on the parent entry), breakeven, backstop and target. It is cancelled when the parent closes first, except when the parent backstop fires in the minute that reaches the add. Gross across both legs is capped at 1x.
- API: `tracks.v3add` (rule `v3add`, resting adds in `pending_adds`, unfilled adds left out of `closed`); `analytics?track=v3add`; `breakeven_offset_bp` and `v3add` in the snapshot. At restart 60m 998.87, 5m 1003.42, v3 and v3add 1000; there were no open positions.
- UI: the frontend still reads only 60 and 5. A v3/v3add view goes through Codex + Stitch; the contract is the four track keys above plus `pending_adds`.
- Tests: 46 paper and analytics tests and 5 reclaim frontend tests pass. Registry `exp-monday-reclaim-paper-v3add-20261026-v1`; learning `linked-add-cancel-must-respect-price-path-order.md`.

## 2026-10-26 · Fee-covering breakeven and high-score scale-in tested (native, full history)

- Adapter: the reclaim rule now accepts per-entry `be_offset_bp`. A reclaim close that has not cleared the offset price exits at that close (reason 12). Scale-in legs (`parent`, `reclaim_ref`) share the parent's confirmation clock, reclaim test, breakeven, backstop and target. They expire at the parent's decision and are cancelled when the parent closes unfilled. Commits `0975b885fb`, `274542d217` (same-bar race fix); 9 tests; regression 860 passed (4 old unrelated failures).
- Breakeven offset barely matters. v2 2026 goes from 2.12x (0bp) to 2.18x (17bp); v3 is flat (2.63 vs 2.61). The offset closes 1-3 percent of trades at the reclaim close. Covering fees is harmless and turns the -0.07 / -0.17 percent breakeven exits into about zero.
- Scale-in at -5 percent for v3 scores >= 0.6 fills on 39-51 percent of events.
  - The add earns +3.7 to +9.5 percent per fill, even though those parents average negative.
  - Replays 2020-24 / 2025 / 2026 / last5m: 4.46 / 11.86 / 3.29 / 5.23, against v3 alone at 3.64 / 8.93 / 2.63 / 2.96.
  - The cost is drawdown: 2026 rises from 34 to 50 percent.
  - The -10 percent add fills less (15-22 percent) and gains less.
- Paper engine unchanged at the time (v2 tracks breakeven at entry; v3 as frozen). The owner adopted both for paper the same day; see the entry above. `experiments/active/exp-monday-limit-be-scalein-compare-20261026-v1/`.

## 2026-10-26 · Confirmed 5m entry, known pin R and frozen score filter

- Owner asked why the prior39617R trade only earned3.62percent, then requested confirmed5m market entry, entry-to-bearish-pin-low1R and existing score filtering. Interpretation used immediate next5m close reclaim after the pin, followed by next open entry; long bearish pin/lower wick, short green/upper mirror separately. Stop remains original pin extreme; pre-entry confirmation cannot immediately arm BE. Six R targets x fixed/first post-entry completed5m fee-room protection.
- Builder758bbc5a83 committed before one nativeVectorBT run. All632crypto completed:106346admitted signal/control events,33860signals,1276152parameter positions,2552304native fillrecords (boundary marks flagged).10behavioral tests pass, every closed position PnL/R/per-fillfee reconciles. No second-framework replay, downloads, hashes or production changes.
- Eligiblealt70722source signals ->33688admitted ->33534scorable ->12619scorepassed; longs18401 ->5883scorepassed. Frozen5m threshold0.22621664169752762. Recomputed current deployed pre-touch formulas; old research cached some final touch-close features.154eligible score failures (112short history,42hourly gaps) retained explicitly. HistoricalBinance quote-volume approximation/universe differs from liveOKX; pre2025 overlaps model fit.
- Scoredlong full-history equal-notional best7R/fixed pin stop:5883entries,690TP,5128SL,65censored; mean+74.8282bp but meanR-0.19792 and median-1.09999R. Risk median1.7836percent, mean3.2118percent; medianhold220min/p99~58.6days. Top1percent contribute140.0percent net; other99percent mean-30.2398bp.2025within-window-112.7441bp,2026-52.3246bp. No profitable fixed-risk candidate across12arms.
- 2025selection chooses5R/feeBE, mean-22.3791bp;2026mean-31.5647bp (weekbootstrap95percentCI-51.2306to-5.4779bp), matched excess-0.4136bp/CI-21.4074to20.1333bp. Full5RfeeBE has173TP,1585initialSL,4120BEexits,5censored;3971fee-neutral before slippage,149gaploss,averageBEexit-10.1839bp. Entry/stop taker5bp,TPmaker2bp;10bp adverse slippage permarketfill, funding not modeled. Long7Rscored all vs unfiltered+74.8282 vs+9.5417bp does not survive recent-period comparison.
- OldTAC sample now enters0.004710706 with pinlow0.00438,1R=0.000330706 (7.0203percent), score0.138566 so excluded. This removes the meaningless tiny ex-post denominator but does not make the strategy profitable.
- Results/reproduction `experiments/active/exp-monday-5m-confirmed-score-20261026-v1/`; Notion `4058856479af81178a95f4fd1978a123`. Learning notes `live-score-parity-requires-window-end-parity.md` and `known-risk-and-equal-notional-means-answer-different-questions.md`. Local registries research rejected, eligibilityfalse. Fullhistory previously exposed, conditionalrandomcomparison and independentnotional/R only; no funded account claim.

## 2026-10-26 · 5m 1.618 causal wick-R targets and initial stops

- Owner explicitly selected touch-limit entry with percentage protection until the touch candle closes; wick R becomes available only at that close. Researched targets 1/2/3/5/7/10R x initial stops 0.25/0.5/1/2/3/5/10/20/30 percent and none. Next5m close confirmation unchanged; fee-neutral stop activates only after enough completed-close room. No production/score/capital settings changed.
- VectorBT1.2.0 native barrier kernels and independent portfolio accounting completed632crypto,71060signal events and261157signal+filled-control events:15669420parameter positions/31338840fill rows. Original baseline read from saved Nautilus results, no second-framework replay. The9zero-wick cases are controls, no signal exclusions by future wick. ExistingBABY/DASH bad pairs excluded; missing/corrupt path boundaries censored.
- Altcoins70722signals: full-history highest is no initial stop+5R at-5.8897bp/trade after fees and existing10bp non-target slippage. Best finite stop20percent+5R is fee-only+2.0414bp, afterslippage-5.9137bp; original near-Monday-edge baseline+1.1197bp. At20percent+5R:24317TP,337initialSL,26701failedconfirm,17098feeBE-stop (16554fee-neutral,544gaploss),2252entry-stop fee losses,17censored;23122feeBE activations. This is all original first-touch signals, not the score-gated paper account.
- 2025 selects20percent+3R;2026-3.6088bp versus original+5.9699bp. Matched2026 timing excessCI+3.6560to+10.6171bp,p~.0008 does not make absolute return positive. Only-long full best noSL+5R+0.9672bp, but same2025-9.4939bp; finite long30percent+5R+0.6705bp. No stable profitable recommendation.
- Mean+4.413wickR vs median-.251R and negative notional return is a denominator effect:top1percent83.9percent of netR. LargestTAC event~39617R corresponds to3.618percent net notional. Source candle and accounting read directly; never use ex-post wick to claim executable fixed-R sizing.
- Inputs/config/ledgers/results: `experiments/active/exp-monday-5m-wick-r-20261026-v1/`.11behavioral checks passed; each new registry record validates. Global registry has an unrelated existing `tradingview-candle-probe-20261023-v1` unsupported holdout_status and remains unchanged. Initial pandas read-only-view preparation failure happened before completed simulations; fixed and retained logs. Notion experiment `4058856479af8147bd92f6fb9259b44e` updated, research only. Learnings: `wick-risk-is-not-known-at-touch-entry.md`, `ex-post-wick-r-means-can-disagree-with-money-returns.md`.

## 2026-10-26 · Monday rollover display and compact paper ledger

- Stitch-first completed with `GEMINI_3_8_FLASH`, project `8978791379095391159`, screen `6798eaf559a24559819826c8c01df7e7` (base `77f940d44558444db5ef189e93e9dce0`). Verified screenshot resource `adfa07e0b4a04f83ba689f9ea5910a56` and HTML `a6a8184dee0d477db42c6abc715946b8`; local source/evidence in `output/playwright/rollover-ledger-20261026/`.
- Added display-only prior-week fallback during a developing NY Monday. `/api/monday-fib/levels` returns the prior completed anchor with its original expiry and explicit current_week; `/api/monday-fib/display` projects prior stored touches without mutating current monitoring/ordering state. Both Monday and paper lists use the same display week for dismissals. Actual API returned 452 prior touched symbols; old rays stop exactly at the new Monday start, including DST transitions. Developing with no previous history remains a normal chart state.
- Loaded same-symbol funded trades maintain independent drawings keyed by trade ID; selecting another trade changes focus, not the historical fill/exit lifespan. Actual AEVO entry, shaded position, and manual close were verified on spikeok.com. History remains bounded by the API's existing recent records and loaded candle window.
- Ledger header is one 52px row with small expandable search, tabs, scope and close. Position amounts use remaining notional USDT. Negative P&L uses its own red span regardless of direction. Outside pointer/focus, Escape and navigation dismiss the SPIKE menu. Primary 1.618 links to paper home; duplicate signal-center paper tab removed. Explicit TV/inspection/select controls remain separate.
- Validation: 78 focused frontend tests and 45 backend/asset tests pass. Browser verified 320/390/1470 widths, both themes, no horizontal overflow, search filtering/Escape, outside menu close, navigation and public historical chart. Browser console had no JS warnings/errors. Existing LibreSSL warning only in Python tests.
- Monitor gracefully reloaded; paper worker alive/queue0/error null, all three account equities unchanged across reload. Four enabled patrol plans and worker PID1266 heartbeat retained. Market ready true, model ready false (existing model gate). No trades, thresholds, account reset or notification settings changed. Existing unrelated Bark changes in monday_fib.py/service.py remain outside this commit. iPhone reinstall remains paused.
- Learnings: `previous-week-display-is-not-current-trading-authority.md`, `pnl-color-must-not-inherit-trade-direction.md`.

## 2026-10-26 · Patrol wallpaper interaction and score-first paper ledger

- Owner approved direct implementation after this task's official Stitch edit (`GEMINI_3_8_FLASH`, project `8978791379095391159`) timed out at 300 seconds and no new artifact could be verified. Reused existing verified patrol `d70c4b10a20f478bae161de14e5e5c49` and ledger `77f940d44558444db5ef189e93e9dce0` designs; no claim that list absence proves generation failure. iPhone reinstall remains paused.
- Patrol now shares Monday's wallpaper gesture options and extracted grip-only floating layout, with independent persisted panel positions. Native vertical touch pan/pinch and desktop drag work; late weekly-level responses do not reset the viewport. Default scale favors visible candles; explicit complete-range/detail toggle retains plain anchored price rays. OHLC moves to the bottom, MA values are optional, dark search/readout contrast is corrected. Opening a stored finding now synchronizes its timeframe before returning to the cross-period overview.
- Removed the duplicate Monday chart menu entry. Paper touch/open/closed rows show and sort raw finite entry scores descending, with strictly >0.5 highlighted and missing scores last. Row background selects the current wallpaper, positions preserve exact trade identity; only the coin-name link opens TradingView. Separate inspection links carry symbol and chart period. Expand reveals shared favorites and details; polling retains expanded rows, focus and scroll.
- Closed scores come from a read-only LEFT JOIN to the original signal, not the capped recent-signal page. No score threshold, sizing, fills, account values or v3 strategy changes. The service was gracefully reloaded to activate the snapshot field; paper equity values were unchanged across reload.
- Validation: 126 focused frontend tests and 27 paper backend tests pass; patrol subset rerun after timeframe fix. Browser checked 320/390/1470 widths, 71px compact touch rows at 320px without horizontal overflow, both themes, raw high-score order in all tabs, blank selection, exact closed trade, TV-only popup, inspection route, favorites toggle/restore, menu, grip drag/reset, native touch pan/pinch and real TAO complete-range/detail toggling. Public site verified, no JS errors. Screenshots: `output/playwright/patrol-ledger-20261026/` (local evidence only).
- QA observed a separate cold-start issue: discoveries/latest rebuild in-memory indexes over historical results after restart (latest cursor 546336); a discoveries request exceeded 30 seconds, then warmed requests returned HTTP200 in 0.64 seconds. No detection data was cleared or scan modified. This startup performance issue is observed, not claimed fixed.
- Temporary QA and TV tabs closed; viewport override reset and original public light theme restored. Final service check: monitor alive; paper worker alive/queue0/error null; patrol worker1266 heartbeat current and four enabled plans retained. Existing market/model readiness is false. Learning notes: `wallpaper-charts-must-share-gesture-and-scale-ownership.md`, `closed-ledger-scores-must-follow-original-signals.md`.

## 2026-10-26 · v3 frozen and paper-traded as a separate track; UI still v2-only

- Owner: freeze v3 as recommended and run it next to v2. `yoyo/monitor/monday_reclaim_v3.json` holds every v3 parameter:
  - entries only at score >= 0.5;
  - target at range 0.618 for scores 0.5-0.6 and at the opposite 1.618 from 0.6;
  - a -30 percent backstop until the 60m confirmation, which a reclaim replaces with the entry stop;
  - notional `min(0.05*(score-0.226)/0.1088, 0.5)` of v3 equity, gross <= 1x.
- Lower scores are kept as v3 shadow rows with the near target. `yoyo/monitor/monday_reclaim_paper.py` (commit `f6a97f8c63`) stores v3 under internal track id 1060.
- API: snapshot `tracks.v3` (plus `rule`, `confirm_minutes`, positions carry `backstop`; exit reason `backstop`), top-level `v3` config, `/api/monday-reclaim-paper/analytics?track=v3` (its `track` field reads "1060").
- v2 tracks and their tests are unchanged. 8 new v3 tests; 35 Python + 37 frontend reclaim tests pass. The monitor was restarted (Monday stream reconnected, 507 contracts, worker alive) and v3 started at 1000 USDT.
- UI: the signal-center page still shows only the 60m and 5m tracks. Adding a v3 view goes through Codex + Stitch (project `8978791379095391159`, `GEMINI_3_8_FLASH`). Mark v3 paper only and show the backstop and the score-dependent target.
- Registered as `exp-monday-reclaim-paper-v3-20261026-v1`. Paper only, no orders.

## 2026-10-26 · Limit-reclaim: full-history backstop / ladder / entry-score v2 results

- All trades are framework fills (NautilusTrader, both intrabar paths, worst per trade, 10bp slippage on non-target exits). Two bad source bars were dropped from every variant: BABYUSDT 2026-06-05 09:30 UTC (high 1.30) and DASHUSDT 2020-04-14 07:35 UTC (low 0.10).
- Backstop, full history: no stop is best in every period. Among stops, -20 to -30 percent are close and -10 to -18 percent are worse everywhere. Near target, threshold 0.226, linear sizing, 2020-24 / 2025 / 2026: -20 percent gives 2.93 / 2.97 / 2.00, and no stop gives 4.58 / 3.18 / 2.14. Threshold 0.5 cuts drawdown in every variant.
- Take-profit by score band (score >= 0.5):
  - Choosing the target on 2020-24 picks the opposite 1618 in every band.
  - Choosing on 2025 picks range 0.618 for 0.5-0.6 and the opposite 1618 from 0.6.
  - Equal ladders across key levels land between the near target and the single far target, and never beat the far target on return.
  - Best plan with a backstop: -30 percent stop, range 0.618 for 0.5-0.6, opposite 1618 from 0.6. Results 2020-24 / 2025 / 2026 / last five months: 3.74 / 8.97 / 2.56 / 2.96, drawdown up to 34 percent.
  - The more balanced option, range 0.5 for every band: 3.08 / 5.60 / 2.42 / 2.45 with drawdown at most 27 percent.
- Entry-score v2: the richer feature groups and LightGBM raise AUC but not top-band profit in 2025-2026. Refits that include 2025 do worse on 2026. Keep the frozen 10-factor score. Learnings: `richer-score-features-raised-auc-not-top-band-profit.md`, `far-stop-parity-exposes-bad-source-bars.md`.
- Pending owner decision: freeze v3 and add it as an isolated paper track. Paper engine still runs v2 (equal 10 percent, near target, no backstop). No live action.

## 2026-10-26 · Compact paper ledger and visible touch scores

- Owner paused the iPhone reinstall and requested the screenshot's paper-ledger fix. Header search and title now share one row; isolated ledger tabs avoid old global `.rp-tabs` styles; scope remains available and explanations move below the records. Measured top occupation 175.4px → 107px, preserving 44px controls and 16px mobile search.
- Collapsed touch rows show raw model scores. Strictly `>0.5` gets blue bold text and a small marker; exactly 0.5 remains neutral, unknown is `—`, values that round onto 0.5 retain their distinguishing precision. No score gate, execution, account or backend change.
- Stitch returned verified HTML/image, screen `77f940d44558444db5ef189e93e9dce0`, explicit `GEMINI_3_8_FLASH`; provenance and assets in `docs/design/stitch-20261026-reclaim-ledger/`. 30 focused tests passed; desktop, 390px and 320px checks covered search, tabs/scope, detail expansion, no horizontal overflow, both themes and mobile drag dismissal. Public browser shows LIGHT 0.9356 highlighted and three lower scores neutral, no console errors. Evidence: `output/playwright/reclaim-ledger-20261026/`. Learning: `floating-ledger-density-needs-isolated-control-styles.md`.
- QA tab closed and viewport override reset; owner's public page remains open with the updated ledger. Monitor service and paper worker alive (queue0/error null); patrol worker1266 heartbeat current and four enabled plans retained. Existing market/model readiness remains false.
- iPhone reinstall was **not completed**: native 0.9.1/build37 rebuilt and signed; previous local profile expired 2026-10-25 21:07 Beijing, fresh profile expires 2026-11-02 00:12 Beijing. Phone still reported installed 0.8.26/build35 before the transport failed. No uninstall, local data deletion or Keychain reset occurred. Owner said to stop installing; do not resume without a new request. App backend record count and 7/30/all-time P&L/fees were already zero from the prior archive epoch, verified again; no redundant ledger reset was made. Private diagnostics: `output/qa/spike-reinstall-20261026/`.

## 2026-10-25 · Plain Fibonacci annotations and paper indicators

- Weekly high/low and 1.618 rays now carry plain name/price text above the line, with no filled pill or axis badge; optional internal 0.5/0.618/0.705 rays also show their prices. Shared rendering applies to Monday, paper and patrol while retaining actual Monday extreme anchors and price precision.
- Paper SPIKE menu keeps navigation without its chevron. Five complete recent altcoin chips precede fixed BTC/ETH/XAU at desktop widths; smaller desktops use a second row, mobile keeps scrolling. The bottom indicator popover independently toggles six MA and internal range levels, both initially off. Escape/outside/focus-leave close it; updates/resume preserve options and reuse the canvas. The dock material is a sibling surface so it does not block the popover's real backdrop blur.
- Owner explicitly authorized direct implementation for this small change after the current Stitch edit timed out at 300s. No claim was made that the missing list entry proved generation failure; no retry or substitute design was submitted after that waiver. Existing clear-glass design remains the base. During cleanup the Stitch web log showed a later completion narrative for the requested five-chip/indicator draft; its image/code were not retrieved or used, so this is not claimed as artifact-verified delivery.
- Validation: 57 distinct focused Node tests and 5 asset-delivery tests pass. Public browser verified actual six MA, nine priced rays, default-off controls, same canvas across toggles, Escape focus return, outside dismissal, preserved scrolling through polling, fixed-major coordinates and no drag-induced selection. Checked 1470/1421/1420/701/390/320 widths with no horizontal overflow; 1421 still fits five complete chips, 320 switches retain 44px targets. Both themes verified; no console errors. Screenshots: `output/playwright/reclaim-indicators-20261025/`. Learning: `chart-overlays-must-not-mask-price-action.md`.
- Frontend only; no backend restart or account mutation. Monitor service and paper worker remain alive (queue0/error null); pre-existing market/model readiness remains false. Original dark appearance restored after QA. All task-created tabs were closed; the disconnected original test tab required its exact native tab-group close control. Owner's normal tabs and Stitch board remain open.

## 2026-10-25 · Clear paper glass and fixed quick symbols

- Paper header is now only SPIKE + menu; removed the badge/manual refresh and moved the binary theme button to the upper right. Automatic data polling remains. Recent visible Monday touches (bounded to 20) scroll separately from fixed BTC/ETH/XAU shortcuts; exact XAU-USDT-SWAP is used. Mouse dragging and native touch scrolling do not select a coin; keyed updates retain scroll offset, and fixed symbols are not duplicated in the scrolling lane.
- Stitch-first: project `8978791379095391159`, explicit `GEMINI_3_8_FLASH`, verified new screen `7908551d2e9a4ccc8a684a8da56dd952`; image/HTML/provenance in `docs/design/stitch-20261025-reclaim-clear-glass/`. The edit call timed out, but later exact assets were recovered after the owner reported seeing completion. Implementation began only after retrieving both artifacts. Clear pill/panel material tokens replace stacked white fills and opaque dark overrides; all paper panels retain readable text and fallback/reduced-motion behavior.
- Validation: 27 focused frontend tests and 5 asset-delivery tests pass. Public browser verified BTC, ETH and XAU real charts and loaded logos, pointer-drag/touch scroll without symbol changes, fixed shortcut coordinates, retained offset through refresh, relocated light/dark toggle, list/analytics panels and 320/390/701/1024/1101/1470 widths without overflow/header collisions. Mobile sheet remains 633px at 844px viewport. No console errors. Screenshots: `output/playwright/reclaim-clear-glass-20261025/`; learning: `glass-opacity-must-be-checked-after-cascade.md`.
- Frontend only; no backend restart or account mutation. Service alive and paper worker alive/queue0/error null; existing market/model readiness remains false. Temporary browser/viewport cleaned up and original dark preference restored.

## 2026-10-25 · Public chart cache and parallel homepage loading

- Backend already has confirmed-candle SQLite retention (4096 bars per series), a 2-second open-tip cache, bounded chart-memory views and completed Monday-anchor memory. This change leaves those services, rate budgets and trading paths unchanged. The browser now shares public history between Monday and paper charts and persists a strict closed-OHLC/anchor whitelist in IndexedDB (12 × 720-bar views, 32 anchors, 24-hour/current-NY-week bounds). Cached history paints before fresh requests and uses the existing stale-history warning; private paper/account data is never persisted.
- Homepage paper snapshot and chart assets begin during boot; selected levels/candles overlap, and passed anchors eliminate the second levels GET. Identical live snapshots/heartbeats no longer redraw the entire history/MA set. Fresh network data wins over delayed disk restores; empty/loading replies retain a clearly stale history preview.
- Validation: 127 related frontend tests passed, plus 22 focused final cache/chart tests after adding cached-anchor recovery, and 5 asset-delivery tests. Public browser verified real IndexedDB contents, refreshed history with both candle/anchor endpoints blocked, online recovery, and same-symbol/period paper-to-Monday reuse without another history GET. One before/after navigation sample: HTML end 2646/1620 ms; candle request start 4382/1994 ms, reducing post-HTML dependency wait from 1736 to 374 ms. Network TTFB varied; these are request timings, not a universal render-speed benchmark. Screenshot: `output/playwright/candle-cache-20261025/online-restored.png`. Learning: `chart-cache-must-separate-history-from-live-truth.md`.
- Cleanup: temporary URL blocking removed and task browser tab closed; no console errors on restored online pages. Local monitor service alive, paper worker alive/queue0/error null, patrol PID1266 and all four enabled plans retained. Market/model readiness remain false as before; no backend restart or account mutation.

## 2026-10-25 · Binary appearance and paper homepage

- Naked `https://spikeok.com/`, invalid routes and the retired market route now enter `#signals?pane=reclaim&track=60&tab=events&scope=funded&period=5m`, following the latest funded paper fill without pinning a prior coin or trade. The pane is selected before lazy loading to avoid flashing signal records. SPIKE home links point to the same paper workspace. Owner's latest correction supersedes the earlier retain-menu request: patrol navigation now contains only pool and K-line detail; old history/model links fall back to the pool, with stored results unchanged.
- Exactly two themes, light/dark, controlled by one sun/moon icon. One prepaint controller persists `spike.theme`, synchronizes same-origin tabs and tolerates blocked storage; legacy system values fall back to light. Shared semantic tokens cover glass, controls and charts; Monday, paper, patrol previews and analytics recolor existing canvases without replacing data/ranges. Mobile App and independent Rotation/VLM surfaces use the same controller. App assets built and copied into iOS/Android projects; no installed phone binary was replaced.
- Stitch first: project `8978791379095391159`, explicit `GEMINI_3_8_FLASH`, screens `2acc1f5244714b8bb6ca736e54b25bc7` and `7b2313c3cb864ef384fec05bf046168f`; required PNG/HTML/provenance in `docs/design/stitch-20261025-night-mode/`. The first call timed out and list responses omitted the completed screen; browser board identity plus official exact-screen retrieval/download recovered it. No inferred waiver or duplicate generation was used.
- Validation: 106 focused frontend tests pass; final chart-label and route changes pass 17 targeted regressions. App TypeScript/Vite build and Capacitor copy pass (existing large-chunk warning). Public root entry, title, one visible theme button, both themes, theme persistence/cross-tab sync, patrol's two tabs, Monday chart and analytics verified. Public paper and App layouts checked at 320px, plus 390px App and desktop; no horizontal overflow. App UI checks used an unconnected local service state, not live account mutations. Screenshots in `output/playwright/theme-20261025/`.
- No backend or trading changes/restart. Paper worker alive/queue0/error null; independent patrol PID1266 and all four enabled plans remain active. Aggregate health still reports market_ready/model_ready false while service_alive is true and the legacy scanner is progressing with zero scan errors; this frontend delivery does not alter those gates. Learning notes: `stitch-list-absence-does-not-prove-generation-failure.md`, `theme-switching-must-preserve-chart-and-route-state.md`.

## 2026-10-25 · Paper blank-hit navigation fix and compact left chooser

- Reproduced the unintended TradingView popup with a native click in the 99px blank extension of a flex-grown coin anchor (145px box versus 46px text). Coin identity/quote are now one local selection button; only a separate 44×44 `TV ↗` action navigates externally. Row whitespace selects locally. Inspection, favorites and weekly hide/undo retain shared Monday policy.
- The coin title opens a 300px left floating list below a 290px compact context card; list defaults closed and closes after selection, outside chart tap or Escape. Trade ledger and watch list are mutually exclusive. A content-sized top strip shows the five latest visible Monday touches with logos, side/time and local chart selection; search and list filters do not change that strip. Mobile strip scrolls horizontally and the list retains the 75dvh direct-finger sheet. Keyed DOM updates preserve controls/focus/scroll during live quotes and defer mutations while pointers are down.
- Stitch-first source: project `8978791379095391159`, explicit `GEMINI_3_8_FLASH`, screen `7a1de01ec1304ac6baa2e941ce351cfd`; PNG/HTML/provenance in `docs/design/stitch-20261025-reclaim-compact/`. An initial timeout produced a completion narrative but unchanged canvas/export; an explicit delivery repair returned the verified new image and code.
- Validation: 75 focused Node tests pass. Native blank click selected AEVO without any popup; the explicit TV action opened the correct external URL. Browser checks verified favorites/hide/undo/search/exact inspection links, Escape focus return, outside dismissal, preserved DOM node/focus/51px scroll across polling, independent full-viewport chart, recent-chip selection and horizontal touch scrolling without accidental selection, trade ledger and analytics. 1470/1024/701/390/320 widths tested without overflow or header overlap; 390×844 drawer=633px and 50px finger displacement=50px translation; long downward drag fully dismisses. Public `https://spikeok.com/#signals?pane=reclaim` renders the new controls and chart without console errors. Evidence in `output/playwright/reclaim-compact-20261025/`.
- Frontend only; no monitor restart, paper-close request or account mutation. Paper worker alive/queue0, market stream connected; independent patrol worker1266 and four enabled plans remain active. Learning notes: `flexible-layout-must-not-expand-external-hit-targets.md` and `stitch-completion-text-does-not-prove-a-revised-design.md`.

## 2026-10-25 · Paper latest-touch list parity and complete-ledger analytics

- Owner corrected the previous monitor-list interpretation: paper must match the Monday 1.618 page. Default is touched contracts only, newest occurrence first, with all/upper/lower/favorites. The prior 507-contract distance-sorted list is superseded. `monday-list-model.js` now owns both pages' exact filter/order, history-versus-live timestamps, TradingView/inspection links, shared favorites and per-week hide/undo. Re-entering either page rereads preferences for same-tab synchronization. Logos, symbol-to-wallpaper selection and untouched favorites are retained.
- Added a glass `统计分析` panel beside account equity; 60m/5m tracks remain isolated; 7 days/30 days/all. Full-ledger read-only GET `/api/monday-reclaim-paper/analytics` returns realized P&L including partial closes, separately fresh-quote floating P&L, settled fees, fully closed trade count/win rate/net profit factor, realized drawdown, current exposure, stepped realized equity and side/symbol/exit-reason attribution. It does not use the UI's 100-record page cap. Cash uses tranche timestamps; whole-trade outcomes use final-close timestamps. Legacy closed rows without tranches are counted once; simultaneous cash events are grouped; missing fields/ledger differences are disclosed.
- Stitch first: explicit `GEMINI_3_8_FLASH`, project `8978791379095391159`, completed screen `f6ca485dbc9440bcabc2a9058a9d4768`. HTML/PNG/provenance in `docs/design/stitch-20261025-reclaim-analysis/`; synthetic design values never enter application data. Mobile analytics uses 75dvh, direct-finger dismissal, 44px controls and a two-row filter layout at 320px. ECharts loads only on analytics open; closing/leaving stops its feed and disposes its chart.
- Validation: 86 focused Node and 27 backend tests pass. Expanded startup checks exposed a pre-existing speculative warmup mismatch for pinned untouched coins; warmup now honors the exact pin and preloads the shared list model. The old candle reuse test now explicitly permits the required Monday-anchor GET while still prohibiting a duplicate candle GET. Browser QA on an isolated synthetic account verified +10 USDT all-time versus 0 over 7 days, correct partial-exit/whole-trade counts, independent empty 5m outcomes, all-time fees, stepped curve hover, 390/320 layouts, 50px exact tracking and dismissal. Actual Monday/paper first 50 rows match; favorites synchronize both ways; hide/undo/search/chart selection verified. Analytics made 0 requests after closing over more than one poll interval.
- Public page verified with real paper data, curve and breakdowns; no console errors. Graceful monitor reload PID72483 activated only the read-only endpoint, with both paper equities unchanged across reload; worker alive/queue0. Independent patrol worker1266 and four enabled plans retained. Evidence and disposable QA fixture in `output/playwright/reclaim-analysis-20261025/`; no production close was executed for this QA. Learning notes: `same-list-means-shared-membership-and-order.md` and `partial-close-analytics-need-two-clocks.md`.

## 2026-10-25 · Limit-reclaim v2: score-weighted sizing beats equal sizing at a lower drawdown

- Owner: larger positions for higher scores, best return at the same total drawdown. `experiments/active/exp-monday-limit-reclaim-sizing-20261025-v1/` (arithmetic on framework fills; scale chosen on 2025, tested on 2026; 20 random orders of same-time fills).
- Linear sizing, position = 0.46 percent of equity for every 0.01 of score above 0.226 (score 0.30 -> 3.4 percent, 0.375 -> 6.8, 0.45 -> 10.3, 0.60 -> 17.2): 2026 median 2.04x with DD 20.6 percent versus current equal 10 percent 1.48x with DD 27.8; better in 20/20 orders; last five months 1.67x vs 1.40x (17/20). Tier 1/2/3 and per-decile Kelly lost. Paper engine unchanged (equal 10 percent) pending owner decision. Gross stays <= 1x; single positions reached 32 percent; extrapolated scores such as LIGHT 0.936 would get 33 percent.

## 2026-10-25 · Paper monitor universe and manual partial/full exits

- Added the missing monitored-universe list to `#signals?pane=reclaim`: 507 contracts from public Monday compact snapshots, logos, search, all/touched/current-track funded holdings, nearest 1.618 distance and honest stale/missing states. Desktop defaults open; mobile uses a 75dvh sheet with direct finger tracking. Symbol selection is independent of execution history, pins its real chart, and clears unrelated trade overlays. Follow-latest restores latest funded fill.
- Owner additionally requested manual partial/full paper closing. Open funded rows now offer a glass close sheet with 25/50/75/100 percent and custom remaining-position percentage. The backend records tranches, preserves original entry/notional, realizes only the closed fraction, allocates original maker cost and current taker cost, frees remaining exposure capacity, and retains original target/activated stop. Full closure aggregates the whole trade; partial fills appear on the chart and in the ledger. Historical records and sizing/score/fee constants remain unchanged.
- Same-origin POST `/api/monday-reclaim-paper/positions/{id}/close` requires an action header, request UUID and expected position revision. Durable idempotency plus one writer lock prevent repeat requests/automatic exits from double settlement. Fresh public quote required; overdue unresolved confirmation refuses a later manual fill. Same-minute automatic exits cannot predate an earlier manual reduction. No exchange order API is involved.
- Stitch first, explicit `GEMINI_3_8_FLASH`: watchlist screen `0d0dbd6c39834be2b78d6f44a5ec0f9d`, close screen `57fe402b0685484680381d5f41a514ce`, project `8978791379095391159`. HTML/PNG/provenance in `docs/design/stitch-20261025-reclaim-{watchlist,close}/`; synthetic examples only.
- Validation: 74 focused frontend tests and 18 backend tests passed. Isolated temporary-account browser QA actually executed long 25%, custom 12.5%, remainder-full, and short 50% closes; quantities, fees, equity and per-fill history matched. Production account was only previewed, never closed for QA. Desktop and 390/320 layouts have no overflow; 44px buttons; watch sheet=633px at844px viewport; both sheets track a 50px finger displacement exactly. Evidence and temporary QA fixture: `output/playwright/reclaim-watch-close-20261025/`.
- Before activation, SQLite online backup retained in that local evidence directory. Graceful monitor reload installed migration/API (PID69511); both real paper equities unchanged, original open notionals fully retained, revision0, worker alive/queue0, stream connected. Public assets and UI verified. Learning notes: `monitor-universe-is-not-an-execution-ledger.md` and `partial-exits-need-a-tranche-ledger-and-idempotent-writer.md`.

## 2026-10-25 · Limit-reclaim v2: other targets, partial exits and 5m confirmation rejected

- Owner asked whether targets other than the near Monday edge, partial exits across them, and the 5m confirmation were tested. Framework O-L-H-C replay finished (626/626), worst path per trade, 10bp slippage, frozen entry score. `experiments/active/exp-monday-limit-reclaim-allexits-20261025-v1/`.
- 60m, top-40 percent score, from 2025: near edge +13.2bp, win 35.6 percent, 1000U 3.80x, the best compounding; far targets keep a similar mean (+10.6..+16.0bp) with win rates down to 11 percent and 2.1..3.5x; six equal-weight partials +11.6..+14.1bp, 2.0..3.3x. Last five months every variant 1.26..1.55x. Far targets only help longs. 5m confirmation is worse for every target (near 1.11x from 2025, 1.02x last five months). Rule unchanged: 60m confirmation, near-edge target. The 5m paper track stays research only. No live action.

## 2026-10-25 · Monday paper monitor becomes a full viewport chart

- `#signals?pane=reclaim` now uses the Monday chart as a full viewport background. Compact glass navigation, trade context, collapsed account disclosure, collapsible records and a bottom chart dock replace the dashboard. Default follows the latest funded fill in the selected 60m / 5m track; selecting a record pins its ID. Periods 5m/15m/1H/4H, six MA off by default, full view and latest candles remain available.
- Stitch first: project `8978791379095391159`, screen `9b7f625827da4e60a41b5bf1361c7829`, explicit `GEMINI_3_8_FLASH`. The edit timed out at 300s; a browser reload showed the completed draft and original prompt, then official list/download recovered image and HTML. Assets/provenance: `docs/design/stitch-20261025-reclaim-wallpaper/`. No duplicate generation and no private runtime examples uploaded.
- Long/short entry arrows and price labels anchor to their actual candle, entry/target area begins at the fill, closed trades have exit markers. Entry-price stop appears only after backend activation; no invented initial stop or R. Current-week Monday levels remain current when inspecting an old fill; trades outside loaded candles receive an explicit notice. Charts use independent controller factories, so leaving either page cannot destroy the other chart.
- Read-only API addition `tracks[track].latest_funded` computes latest fill before the exit-ordered history is truncated to 100. No fill, fee, sizing or equity changes. Graceful monitor reload installed the field (new PID64825); paper worker healthy, queue0, both realized equities unchanged. The legacy scanner restarted from persisted checkpoints with0errors; independent patrol worker1266 and four enabled plans retained.
- Validation: 87 focused frontend tests and9 backend tests pass. Desktop1470x671, mobile390x844 and320x740 charts match the viewport with no horizontal overflow and44px controls. Real AEVO/LIGHT trades, 60m/5m state difference, history pinning through polling, MA/period toggles, pan/pinch and50px direct-finger drawer tracking plus dismissal verified. Leaving for Monday produced0 paper GETs; both chart pages work after switching back. Public JS/CSS match local and public page has no console errors. Evidence: `output/playwright/reclaim-wallpaper-20261025/`.
- Learnings: `shared-chart-reuse-needs-independent-lifecycle-owners.md`, `latest-fill-cannot-be-inferred-from-an-exit-paginated-ledger.md`. A requested Luna/max audit was stopped before work because the tool did not expose verifiable actual model metadata; primary agent completed review directly.

## 2026-10-25 · Monday 1.618 paper frontend connected

- Added `#signals?pane=reclaim` to the signal center. Read-only `GET /api/monday-reclaim-paper` powers independent 60m main / 5m research account cards, funded all-time statistics, open / closed / shared touch records, funded / shadow filters, symbol search and exact-symbol Monday chart links. No execution or backend strategy changes.
- Stitch first: project `8978791379095391159`, screen `481ac4e5b140422aaa749326a582b43d`, explicit `GEMINI_3_8_FLASH`; image, HTML and provenance in `docs/design/stitch-20261025-reclaim-paper/`. Generated R metrics and target labels were corrected against the API producer before implementation. Long target is Monday low; short target is Monday high.
- Position allocation owns account P&L; signal `taken` only means score eligibility. API `shadow_closed` actually includes all closed rows and is deliberately not used as a shadow count. Equity excludes floating P&L. Recent lists are bounded at 100 while headline funded statistics use all history. Missing data stays unavailable; fees and slippage sensitivity are separately disclosed.
- Page-owned GET polling pauses when hidden, deduplicates reads, cancels abandoned reads and discards late responses. Errors retain the last snapshot. Stable row markup preserves expansion/focus; rendering is bounded to 50 rows per batch. Desktop and 390/320 layouts verified, main controls >=44px, no horizontal overflow; actual AEVO 5m chart navigation, LIGHT fee details, search, shadow empty state, offline retention/recovery and old signal panes verified. Hidden pane made 0 paper reads during four legacy refresh cycles.
- Validation: 66 focused Node tests and 8 existing backend paper tests passed; final schema hardening passed the 10 paper-contract tests. Public JS/CSS matched local and the public page displayed live data with no console errors. Test evidence in `output/playwright/reclaim-paper-20261025/`; fixed-header full-page screenshots can stitch at the wrong offset, so `public-desktop.png` is a normal viewport capture. No monitor restart required. Patrol worker1266 / four enabled plans remained healthy.
- Learning: `docs/learnings/paper-ledgers-must-separate-score-eligibility-from-capital-allocation.md`.

## 2026-10-25 · Shared candle palette, compact patrol list and exact inspection links

- Stitch first: project `8978791379095391159`, screen `dd8d13e22c2541e193ed1c35e9c3e4ad`, explicit `GEMINI_3_8_FLASH`; HTML/image/provenance in `docs/design/stitch-20261025-candle-unification/`. A timed-out edit was recovered from the completed browser board and official screen list (187→188), without duplicate generation.
- Shared display palette now matches Monday: green `#169873`, rose `#d7355d`, including wicks and volume. Patrol details/previews, market, position and range/research views use the same definition; frozen model rendering and risk annotation semantics are unchanged.
- Patrol rows reduced from 120px to 69px on desktop, 98px on mobile with 44px touch targets. Repeated scores and empty period buttons removed; public turnover order, logos, favorites and chart links remain.
- Monday inspection links open `tab=results` with the exact contract/current period; row cache invalidates on period changes. Explicit missing contracts cannot silently fall back to another coin; previous chart/tail state is cleared before loading a new selection.
- 145 relevant frontend tests pass. Real Monday LIGHT/1H click reached LIGHT/1H details. Public USELESS/1H chart verified; public CSS matches local. 390×844 sheet=633px, 320×740 sheet=555px, no overflow. Evidence/screenshots in `output/playwright/patrol-color-20261025/`. Initial latest-catalog read after another task's monitor restart timed out, then automatic retry recovered; no backend change or restart in this UI task.
- Learnings: `derived-chart-links-must-invalidate-on-period-change.md`, `display-candle-palette-needs-one-shared-source.md`.

## 2026-10-25 · Limit-reclaim v2 paper engine live; signal-center UI pending Codex + Stitch

- Owner: run the v2 rule as simulated live trading in the signal center, 60m confirmation (recommended) plus an isolated 5m research track, both resting the limit at the Monday 1.618. Owner split the work: backend paper engine and API here; **UI goes to Codex with Stitch first** (project `8978791379095391159`, explicit `GEMINI_3_8_FLASH`), then integration. No UI was written here.
- Backend `yoyo/monitor/monday_reclaim_paper.py` (commit `791941594f`), wired into `yoyo/monitor/server.py` as a `MondayFibService` listener; listener calls only enqueue, a worker thread does the REST reads and SQLite writes. Rule = backtest: fill at the level, confirmation bar close back inside -> stop to entry, otherwise exit at that close (taker), resting target at the near Monday edge (maker); a stop beats a target in the same minute; in the fill minute a target counts only if the close is beyond it. Alts only (BTC/ETH/XAU/XAG/PAXG/XAUT excluded). Frozen entry score `yoyo/monitor/monday_reclaim_score.json` (= `exp-monday-limit-reclaim-recent-20261025-v1/score_model.json`, threshold 0.2262); signals below it run as shadow positions (`taken=0`) that never touch equity. Each track 1000U, 10 percent per trade, gross <= 1x, maker 2bp / taker 5bp, 10bp slippage reported as `net_slip_bp`. 8 tests in `tests/monitor/test_monday_reclaim_paper.py`. Paper only, no orders.
- State: `~/Library/Application Support/Fable/ImpulseMonitor/monday-reclaim-paper.sqlite3` (tables `signals`, `positions`, `tracks`). Registered as `exp-monday-reclaim-paper-20261025-v1`. Monitor restarted 16:40 CST (pid 54927); Monday stream reconnected, 507 contracts. First paper trade 16:46: LIGHT long, 40 percent three-minute crash, filled-minute target, +458.5bp, both tracks 1004.59 (checked against OKX 1m). Its score 0.936 is far outside the training range.
- **UI contract for Codex** — `GET /api/monday-reclaim-paper` returns `now_ms`, `strategy`, `paper_only`, `model{threshold, trained_before, features}`, `sizing`, `fees_bp`, `worker{alive, queue, error}`, `tracks{"60","5"}` and `signals` (latest 100: `symbol, side, level, target, high, low, candle_ms, status, score, taken, features, error`). Each track: `confirm_minutes, equity, start_equity, started_ms, stats{taken_closed, wins, mean_net_bp, shadow_closed, shadow_mean_net_bp}`, `open[]` (position + `score`, `mark`, `unrealized_bp`; `stage` = `pending_confirm` until `decision_ms`, then `breakeven` from `be_from_ms`) and `closed[]` (latest 100: `exit_ms, exit_price, reason` in target/breakeven/failed_reclaim, `gross_bp, fee_bp, net_bp, net_slip_bp, pnl`, `taken`). Keep the 60m and 5m tracks visually separate, label the 5m one research, label shadow rows, and state paper only. Stitch receives placeholder numbers only.
- Still running: all-exits OLHC replay `exp-monday-limit-reclaim-olhc-allexits-20261025-v1` (range 0.5 / 0.618 / far edge / opposite 1.618 targets and the 5m confirmation track), about 5 hours from 16:15 CST; analysis follows when it completes.

## 2026-10-25 · Patrol K-line wallpaper and volume-ranked coin browser

- Stitch first: official project `8978791379095391159`, screen `d70c4b10a20f478bae161de14e5e5c49`, explicit `GEMINI_3_8_FLASH`; design image/HTML retained in `docs/design/stitch-20261025-patrol-wallpaper/`. A 300s edit timeout was recovered from the completed browser board and the refreshed official screen list; no duplicate generation.
- Results chart fills the viewport beneath floating glass navigation, coin browser, title and tool dock. History and model menus remain fully available; history loaded 1–20 of 1766 records during QA. Panels animate without resizing the canvas. Mobile coin sheet is 75dvh and follows touch movement, with rebound/dismissal.
- Public 24h USDT turnover drives default coin ordering; logos, per-row favorites, embedded search and exact-contract/timeframe Monday links are connected. Missing pinned Monday data no longer silently selects another contract. Existing anchored Monday/1.618 price rays remain.
- 138 relevant Node tests passed. Browser QA: desktop1470×671 canvas equals viewport, panel collapse preserves dimensions; 390×844 sheet633px, 45/120px touch movement matches transforms, short rebound/long dismiss; 320×740 no document overflow; search, favorites, same-coin1H link, chart touch pan, settings and retained menus checked. Screenshots/raw log in `output/playwright/patrol-wallpaper-20261025/`.
- `https://spikeok.com/` serves the updated CSS byte-for-byte and the real public chart was verified. Static update needs refresh, no service restart. Independent patrol worker1266 alive, four plans enabled. Other task's Monday/Bark Python changes are excluded from this UI commit.
- Learnings: `wallpaper-chart-layout-must-decouple-canvas-and-panels.md`, `chart-deep-links-must-preserve-explicit-selection.md`.

## 2026-10-25 · 5m wick stop versus 60m reclaim: wick stop rejected

- Owner's MUBARAK example (stop under the 5m wick, about 20R). Framework replay of all 626 alts, both intrabar paths, worst per trade: win 15.6 percent, per notional -13.5bp (60m reclaim +2.1). R edge exists only for stops under 0.3 percent and collapses with slippage: mean +4.09R at 10bp, +2.15R at 20bp, +0.21R at 30bp. 1000U at 1 percent risk: 1.8e17x with 99.7 percent drawdown at 10bp, ruined at 20bp. Not adopted; the near-edge 60m reclaim with the entry score stays the draft.
- Closer targets also rejected: 50 percent target win 42 percent but mean -1.5bp; 100 percent target stays best. `experiments/active/exp-monday-limit-wick-compare-20261025-v1/`, `exp-monday-limit-reclaim-optimize-20261025-v1/`. No live action.

## 2026-10-25 · App 0.9.1 UI complete; native installation pending device

- Recovered actual Stitch screen `f5d3f95247be479983f598ecb45c032b` from Owner browser, then official get_screen/download_file. Earlier “not ready” conclusion was incorrect; saved HTML/screenshot in `yoyo/discipline_app/design/glass-0.9.1/`. No repeat generation or waiver.
- Implemented continuous page/pill swipes, offscreen content exchange, re-grab, edge resistance, velocity release and reduced-motion tracking; equal-width market/limit with sliding highlight; exact-price high/low and upper/lower 1.618 labels; ambient canvas and translucent cards/dock/sheets. No additional hidden chart subscriptions, order or risk changes.
- 88 frontend tests passed; 320/390/1280 browser QA and actual touch dispatch verified drag, commit, rebound, edge, re-grab, chart pan/pinch, reduced motion and sheet dragging. Fixture order writes0; visible chart streams1, hidden0. No console errors. Raw checks/screenshots: `output/playwright/app-glass-0.9.1/`.
- iOS/Android0.9.1(37) build/signature verified; all437 production web assets identical in both native bundles. Mac web `http://127.0.0.1:8897/` serves new assets. Existing public API health works; its root is not a public web preview. iPhone Air unavailable; **not installed**, no device frame-rate claim. Receipt `releases/build-0.9.1.json`.
- Stitch waiting heartbeat `app` paused after recovery. Task-created browser977883211 and fixture39645 closed; Owner tabs retained. Four patrol schedules enabled with no schedule errors; worker1266 heartbeat current. Independent App service36025 preserved. Statistics reset below remains complete: do not repeat it.
- Learnings: `generation-list-absence-is-not-generation-failure.md`, `gesture-capture-transfers-are-not-cancellations.md`.

## 2026-10-25 · Correction: Stitch draft recovered; App UI implementation resumed

- Prior “new design not ready” claim was incorrect. Owner showed the completed board in the existing browser. DOM ID `f5d3f95247be479983f598ecb45c032b` resolves through official get_screen; design and HTML saved under `yoyo/discipline_app/design/glass-0.9.1/`. No new generation or waiver. Four UI fixes are now in progress. Statistics reset below remains complete; do not repeat it.
- Learning: `generation-list-absence-is-not-generation-failure.md`.

## 2026-10-25 · App statistics reset delivered; UI waiting for Stitch

- Owner requested clearing App data plus finger-following menu navigation, prices on 1.618 lines, a corrected market/limit segment and stronger glass. Reset scope announced as statistics and completed records, preserving the real account, execution ledger and confirmed fixed capital. Source commit `4105b3a723` pushed; 16 historical closed records archived privately and excluded through a display epoch. **Do not repeat the reset.** Receipt: `~/Library/Application Support/SpikeDiscipline/statistics-reset-20261025-receipt.json`.
- An authenticated empty USDT balance was incorrectly rejected as a read error, leaving a stale displayed value. Valid zero-balance responses now update the account; nonpositive equity still blocks opening. Malformed responses remain errors. Local/public authenticated APIs verify equity/PnL/fees/count=0, empty curve/history, fresh account and no private error. Complete risk fields, closed ledger, receipts and fixed capital remain intact; exchange command count did not change. Independent App service PID36025 reloaded; research monitor/patrol services untouched.
- Validation: 234 broad Python tests passed, followed by 28 focused tests after adding unresolved-command and account-scope guards. Learnings: `statistics-reset-must-not-reset-execution-history.md`, `zero-balance-is-data-not-a-connection-failure.md`.
- **UI remains pending.** Official Stitch edit of screen `9cd418117dd24ff0802728fdeb596a18`, project `8978791379095391159`, explicit `GEMINI_3_8_FLASH`, requested05:58:26UTC, timed out300s. Recovery list/project reads show no new design and the browser project page remained blank. Owner explicitly answered **继续等待 Stitch 新稿**. Do not implement UI under a waiver or blindly resubmit. Request provenance: `yoyo/discipline_app/design/glass-0.9.1/request.json`.
- Thread heartbeat `app` (等待 App 玻璃设计并继续实现) is ACTIVE every15minutes to recover the design and complete implementation; pause it when finished. Temporary Stitch Chrome tab977883201 closed; the owner's two original tabs retained.
- Located causes: `useSwipeNav.ts` clears the live offset before navigation and substitutes a16px incoming animation; its pill does not track the drag. `style.css` leaves order-type children `flex:none` while Apple Glass expands the container. `FibRangePrimitive.ts` only draws `level.title`. `performance.css` overrides the navigation to opaque white/no blur, while the white canvas hides glass contrast. Once Stitch supplies the new design, implement and verify these changes in `mobile/src`, preserve trading semantics, build/sync both platforms and check native installation. Current iPhone Air is unavailable; latest existing native package0.9.0(36) was not installed. Shared main only.

## 2026-10-25 · Mobile patrol preview uses 75% height and swipe dismissal

- Owner requested exactly 75% screen height and finger-following dismissal. The preview now uses 75dvh on mobile, including short screens. Grip/header/noninteractive metadata follow downward drags; short pulls rebound, long pulls or quick flicks close through the existing dialog lifecycle. Chart pan/pinch, controls, Escape and backdrop dismissal remain functional; desktop geometry is unchanged.
- Stitch GEMINI_3_8_FLASH screen `57a4f7f2e33e46b3b0ecf390afe4f2b5`, project `8978791379095391159`; source in `docs/design/stitch-20261025-patrol-sheet/`. Gesture ownership and cancellation learning: `docs/learnings/chart-sheet-dismissal-needs-gesture-ownership.md`.
- Validation: 66 focused frontend tests passed; real Chromium touch events verified 50px rebound, 150px dismissal, quick flick, chart pan and two-finger zoom. Mobile390x844/375x667/320x568 render633/500.25/426px sheets without overflow; desktop1440 stays936px wide. Close removes canvases/levels, unlocks scrolling and restores focus; reopen resets position. Public `spikeok.com` serves the new sheet and four reference levels. Screenshots and checks: `output/playwright/patrol-sheet-*`. Static-only change; no service restart or patrol schedule mutation required.

## 2026-10-25 · Patrol charts show detection-week Monday levels

- Owner requested Monday high/low and upper/lower 1.618 on patrol K-line previews. Both the popup and selected-shape full chart now draw the four native price lines with a wrapping value legend. Default preserves candle detail; “完整区间” fits distant levels. Short mobile screens expand the sheet to preserve chart height.
- Stored detection close time owns the NY week; reuse `monday_fib.week_window` / `baseline` and the shared public candle hub, one selected Monday 24-hour read. Developing or incomplete Monday is explicit and unplotted. No scanner tasks, model inputs, alerts or trading semantics changed. Stale/closed chart replies are discarded and overlays removed.
- Stitch GEMINI_3_8_FLASH screen `85b777925254414792e48fd5e95308f4`, project `8978791379095391159`; design source in `docs/design/stitch-20261025-patrol-levels/`. Implementation commit `d2139c7033`. Learning: `docs/learnings/historical-chart-overlays-must-use-detection-clock.md`.
- Validation: 134 Python and 56 frontend tests passed. BAT 5m 2026-10-25 13:05 Beijing: high0.20091, low0.17062, upper0.21962922, lower0.15190078 exactly match the existing 1.618 page. Desktop1440/mobile390/small320, all-level fit, failed-read retry and close cleanup checked; public mobile screenshot and raw checks under `output/playwright/patrol-levels-*`. Public and local level APIs match. Service reloaded after2008/2008,0errors; new scanner PID31230 completed another2008/2008,0errors. Four patrol schedules unchanged; independent worker PID1266 heartbeat advanced.

## 2026-10-25 · Limit-reclaim: failed-stop samples, post-exit recovery, score sizing

- Failed reclaims: median -0.46R, mean -0.82R, 8 percent worse than -2R. After the exit, price returns to the entry as often as it moves the same distance further (51 vs 50 percent within 2h), so no re-entry edge.
- Entry score (logistic, fitted before 2025 on worst-path wins; volatility, volume spike, approach speed, opposite side first, 24h move, other coins touching): 2025+ AUC 0.654; top quintile +25.7bp, bottom three negative; 1000U from 2025 trading only the top 40 percent 4.0x (DD 33 percent) versus all 0.15x.
- Running in background: target 50/75/100 percent replays and a 5m wick-stop near-edge replay (owner MUBARAK 20R example), both intrabar paths. No live action.

## 2026-10-25 · Owner-requested audit: execution completion is not strategy validation

- Independent cached-ledger reconciliation confirms 626 5m alts / 69,990 unique matched trades: OHLC +21.2553bp, OLHC +20.6209bp, per-trade minimum +9.7168bp. Shorts +24.8202 -> +1.3537bp. Of 1,792 original short same-bar targets, 247 remain same-bar but 1,154 eventually hit target. Stored monotone-OHLC parity is 3,158/3,158; four monotone-segment regression tests pass. No historical rerun.
- Correct interpretation of older entries below: the fixed-path profit is not validated executable edge. Per-trade hindsight minimum is a diagnostic scenario, not observed market order or a rigorous portfolio lower bound. The 150.0858x / 0.118965x figures use hand-written allocation on framework fills, realized equity only, row-order allocation at timestamp ties, and costs booked at exit; no mark-to-market margin account validation. Fast-long 1x year2025 is 0.969728x, so it is not positive every year. No production promotion.
- Scope: original630alts had70,724filled /70,694closed /30censored; paired path replay excludes four1m-source alts. Original +30.3535bp control advantage is conditional on54,374complete matched groups and OHLC semantics; it has not been established for the revised paths. Fee-inclusive BE has632done but also retains the original path assumption; execution completion alone does not resolve this issue. Learning: `docs/learnings/per-trade-adverse-paths-are-not-account-profit-bounds.md`.

## 2026-10-25 · Limit-reclaim floating profit before breakeven exits

- 69,990 alt trades (60m, near edge): 24,656 breakeven exits, of which 96 percent were at least +0.5 percent in profit and 82 percent at least +1 percent before returning to entry (median peak +1.92 percent); 44 percent had covered half the distance to the target, 6 percent 90 percent. Failed reclaims: 29 percent reached +1 percent, 6 percent half way. Measured between fill and exit bars only. A closer target or profit lock needs a framework run. `experiments/active/exp-monday-limit-reclaim-mfe-20261025-v1/`, builder f53ea69f5e.

## 2026-10-25 · Codex 1.618 limit-reclaim: short same-bar targets fail the O-L-H-C replay

- Adapter defect fixed (ca571cf122): rising synthetic intrabar segments were replayed open-high-low-close, so an O-L-H-C replay changed nothing for shorts. New opt-in `monotone_segments`; default unchanged; 297 adapter tests pass. O-H-L-C parity sample: 3158/3158 trades identical to Codex.
- Monotone O-L-H-C replay of all 626 5m alt symbols (60m, near edge): of 1792 short same-bar targets only 247 stay same-bar; their mean +398bp -> -51bp. Shorts +24.8 -> +1.4bp per trade; worst path per trade +9.7bp overall (Codex as recorded +21.3).
- 1000 USDT, 10 percent per trade, gross<=1x, 10bp: as recorded 150x; worst path unfiltered 0.12x, any_strong 0.33x, vol_spike 0.11x, vol_top 5.6x (DD 57 percent), fast_long 2.2x (<=3x 4.9x). 100x not supported. `experiments/active/exp-monday-limit-reclaim-path-compare-20261025-v1/`. No live action.

## 2026-10-25 · Limit-reclaim 1000U replay: bug fixed, result hinges on short same-bar targets

- My equity replays booked exits sharing their entry timestamp before the entry, zeroing 1868 same-bar targets (58 percent of profit). Fixed in 6327c20bd3; the 2026-10-24 review numbers (0.98x/1.25x) are superseded: as recorded, 2 percent per trade gives 11.8x, 5 percent with gross<=3x 148x.
- But 1536 shorts that fill and hit the target in the same 5m bar carry 47 percent of recorded profit and rely on Codex's fixed O-H-L-C path. Filtered replays (10 percent per trade, gross<=1x, 10bp): as recorded unfiltered 97x, vol_top 121x, any_strong 266x; conservative (without those shorts) unfiltered 0.43x, vol_top 8.6x, any_strong 1.7x, fast_long 2.2x (unaffected; <=3x 4.9x, positive every year).
- Decisive next step: framework replay with adverse intrabar order before trusting any multiple. `experiments/active/exp-monday-limit-reclaim-filtered-replay-20261025-v1/`; learning notes updated. No live action.

## 2026-10-24 · Limit-reclaim fast-cut hybrids rejected

- Exit at the 5m or 15m check if not reclaimed, otherwise manage by 15m/60m (assembled exactly from framework fills). All alts: 5->60 win 18.6/19.9 percent, +8.9/+14.3bp versus plain 60m 23.7/25.5, +25.0/+19.9; 15->60 is closer but still lower. The early check cuts ~40 percent of trades, including many later reclaims that hit the target; same in the strong entry subgroups. `experiments/active/exp-monday-limit-reclaim-hybrid-20261024-v1/`, builder 3447d8dd74; learning note added; no live action.

## 2026-10-24 · Limit-reclaim pre-touch price/volume patterns; OHLC same-bar caveat

- Codex 5m bars via load_job (0 ledger mismatches). Volume in the hour before the touch ≥8x normal: 60m win 41/50 percent, +42/+56bp. Reaching the 1618 within 30 minutes of crossing the Monday edge: win 52/64 percent, +164/+160bp overall, but shorts take 32/49 percent of wins inside the fill bar under the fixed O-H-L-C path, so they are likely inflated; longs alone 47/46 percent, +180/+60bp, +116/+71 over random. Monday closing at the far edge and few other coins touching in 24h help a little; liquidity and 24h move are unstable.
- Next before any use: framework replay of the fast-spike subset with adverse intrabar order. Learning note added. `experiments/active/exp-monday-limit-reclaim-patterns3-20261024-v1/`, builder 79a4a9436a; no live action.

## 2026-10-24 · Limit-reclaim patterns at 5m/15m confirmation, trend and last-week features

- Owner wants faster confirmation than 60m. Same alts ledgers, split 2025-01-01: top pre-touch volatility win 19/20 (5m), 24/26 (15m), 32/36 percent (60m); plus opposite 1618 already filled 32/34, 40/41, 49/52 percent (+61/+45, +88/+50, +103/+72bp). Shorter confirmation lowers win rate because breakeven arms earlier, but the ordering holds.
- New: previous week's same-side 1618 won → higher win rate in both halves; coin 4-week trend with the trade adds a few points; BTC 4-week trend is unstable. Near Monday edge stays the most stable target at every volatility level. `experiments/active/exp-monday-limit-reclaim-patterns2-20261024-v1/`, builder f2b268aeab; no live action.

## 2026-10-24 · Win-rate patterns in Codex 1.618 limit-reclaim (alts, 60m, near edge)

- Eleven entry-time features, split at 2025-01-01. Holds in both halves: high prior-14-bar volatility at the touch (top fifth win 32/36 percent vs 19/21 bottom, +82/+70bp, beats vol-matched random by +54/+75); the opposite 1618 already filled this week (win 36/35 vs 22/24); the coin's earlier win rate >=35 percent (27/33). Monday range <3 percent of price loses after fees; wider ranges win less but earn more because the target is 0.618 of the range away.
- Top-volatility plus opposite-side-first: win 49/52 percent, +103/+72bp (n964/1507), chosen after seeing both halves. Side, hours since Monday and UTC hour are unstable. Shorter confirmation converts targets into breakeven exits (target 14/18/25 percent at 5/15/60m). `experiments/active/exp-monday-limit-reclaim-patterns-20261024-v1/`. Builder 940fa4fc9d; no live action.

## 2026-10-24 · Review of Codex 1.618 limit-reclaim snapshot (536/633)

- Read-only over the framework ledgers in `output/monday-partial-snapshot-20261024-2224` (Codex run still going). Alts 5/15/60m confirmation x 5 targets: mean net +5 to +22bp per trade, median -7bp; best 60m + near Monday edge +22.1bp, 25 percent targets (+467bp), 35 percent breakeven (-7bp), 40 percent failed reclaim (-227bp). Top 1 percent of trades = 70 percent of profit; complete matched groups +5.7 vs random -25.2.
- Equity replay of those fills from 1000 USDT (2020-2026): 2 percent per trade 2.6x but needs up to 31x gross exposure; gross capped at 1x 0.98x, 3x 1.25x (1.08x with 10bp exit slippage); 10 percent per trade 0.44x; 25 percent ruined. Charts and summary `experiments/active/exp-monday-limit-reclaim-review-20261024-v1/`. Builder eef486024f. No live action.

## 2026-10-24 · 区间1.618 3/5-minute hold and 1m pin-hold study rejected

- Limit at the first weekly Monday 1618, no stop, market exit after 3 or 5 minutes (NautilusTrader 1m only, maker .02/taker .05): 3m OKX BTC net -3.7bp, Binance six majors +2.9bp CI[-2.9,8.6] later -1.5; 5m -2.7bp and +4.9bp CI[-3.7,12.9] later -0.5. Fees (7bp) eat the gross; fails like 30/50 minutes.
- Owner chart (BTC 1m pin through the long 1618 on 2026-10-24, then no retest for an hour): OKX BTC 6/91 first touches qualify, 4/6 reach the Monday edge before 2.0 vs 63.2 random-walk odds from the 1h close; Binance majors 116/1035, 66.4 vs 69.5. The hour only moves price away from the stop. `experiments/active/exp-monday-1618-pin-hold-20261024-v1/summary.json`; hold results under `exp-monday-1618-hold{3,5}-20261024-v1/`. No live orders, training/production false.

## 2026-10-24 · Web paper desk cleanup

- Owner requested deleting the `区间练习` paper account, removing the entire page-level `模拟开单` title row and retiring the all-symbol Monday 1.618 campaign. The account and dependent ledger rows were removed after a private consistent SQLite backup under the monitor runtime's `research_workspace/backups/owner-account-removal-20261024T120417Z/`. Its two open simulated positions were removed with the account, with no fabricated closing fills. Two other accounts and their surviving database relations were compared against the backup before commit and were unchanged. Maintenance receipt and script remain local, outside Git.
- The web desk uses public OKX quotes and local paper fills, not OKX's official demo trading account. Native App historical replay remains a separate Nautilus-backed path. No exchange orders, execution settings, research results or production eligibility changed.
- UI reuses existing style after owner explicitly replied `允许，直接做这两处删减` following Stitch timeout. Provenance: `docs/design/stitch-20261024-paper-desk-cleanup/manifest.json`. Desktop1440/mobile390 title height is0 and content begins at the main container's top; no bulk button or bulk module load, no horizontal overflow. Other controls remain.
- Backend no longer registers campaign routes or starts its worker. Startup atomically disables persisted campaign configs, advances their generation fence and cancels only campaign pending orders before the ordinary paper matcher starts. Manual positions/orders and campaign history survive retirement. Tests:199 paper Python regressions;142 paper frontend;5 module loader;7 focused API/asset checks (API cases overlap the199). Loader expectation now includes the existing Monday candle-feed dependency already present in HEAD.
- Local service8766 reloaded gracefully after its prior scan completed2008/2008 with0errors. Retired route returns404 and public spikeok.com serves the updated paper script. Four YOLO patrol schedules remain enabled with advancing independent worker heartbeat. Temporary browser and remaining post-reload checks are recorded in local `output/paper-desk-cleanup-20261024/`.

## 2026-10-24 · App independent historical paper replay 0.9.0

- Owner requested mobile paper accounts, arbitrary historical replay, manual open/close, statistics and one-click historical Monday 1.618. New entry is the 1.618 page top-right. Separate `/v1/replay/*` routes and runtime `replay/sessions.sqlite3`; no real-account resolver or exchange write path. New time/symbol/period creates an independent practice; old accounts restore from an atomic command journal.
- Native matching uses pinned NautilusTrader 1.231.0 streaming HEDGING and existing native fee/fill adapters. Explicit O-H-L-C path, 1x reserve, maker .0002 / taker .0005, optional user SL/TP; no funding/slippage/liquidation simulation. Historical limit orders expire next New York Monday, conservatively before any bar crossing expiry. Weekly range is unavailable before all Monday hours close; gap/future/source guards fail closed.
- Stitch GEMINI_3_8_FLASH project8978791379095391159 screen088907fa0dc84836a55940f8fca73b7d supplied design first; provenance `yoyo/discipline_app/design/replay-0.9.0/`. UI and native delegates were verified gpt-6-luna/max. Code/commands/semantics in app README, learning in `docs/learnings/replay-needs-a-durable-causal-command-journal.md`.
- QA:223 app Python / 86 front-end /14 native /73 boundary tests; real local BTC and public online causal history; native worker durable restore;320/390/desktop browser trading/retry/session tests with0 non-replay writes. Version0.9.0 build36; latest delivery/install/service status lives in `yoyo/discipline_app/releases/build-0.9.0.json`. No backtest rerun, model/live policy or unrelated service changes.
## 2026-10-24 · Paper-only all-symbol Monday 1.618 switch

- Owner requested a paper-desk all-symbol limit feature and explicitly chose a persistent weekly switch. Existing UI reuse was authorized after Stitch GEMINI_3_8_FLASH edit timed out and 10 recovery reads found no new design; provenance `docs/design/stitch-20261024-paper-monday-bulk/manifest.json`.
- `monday_bulk.py`/API coordinate current live OKX USDT swaps with ready, fresh, untouched current-NY-week Monday levels. Upper short/lower long; explicit per-order notional/leverage/TP/SL; preview reserves collateral+maker fees sequentially. Completed Monday only, next NY Monday cancels old orders, weekly switch remains enabled. Default off; no owner account activated.
- Existing paper ledger owns fees/fills/risk. Unique account/week/symbol/side records survive retries and restarts; generation+expiry checked inside placement and fill transactions. Stop cancels only feature-owned pending orders, preserves manual orders and all positions; same-direction existing positions skip. Explicit internal bulk authority bypasses manual 30-symbol/50-order caps, preserving margin and exchange-size gates. No live writes, model promotion or research baseline reruns.
- UI entry: 模拟交易 → 全币种 1.618. Amount/leverage blank; exit choice explicit; status/search/per-symbol skip reasons. 224 Python checks, 131 existing JS regressions, final 26 targeted Python checks pass. Disposable real-browser flow: 10 opportunities/7 pending/3 skipped, reload persistence, all 7 cancelled on stop, account isolation, search, polling draft preservation and lost-response idempotent retry pass; 390/320 dialog overflow fixed and checked. Evidence `output/playwright/paper-monday-bulk-20261024/`; learning `docs/learnings/paper-campaign-stop-must-fence-placement-and-fills.md`. Service reloaded and actual page checked read-only: three owner accounts remain disabled/version0, worker running, four patrol schedules enabled. Browser PID29342 and disposable port8796 closed; earlier research processes remain running. Separate real executor heartbeat is advancing but reports `position ownership changed; no discretionary mutation sent`; owner notified, no real-order/position change or executor restart performed.

## 2026-10-24 · Notion task71 SMA50 retest native study running

- Owner task71 `4038856479af80fe93cfe1675ac5b80c` andoriginalETH5mimage read. Momentum1.0source`3d68856479af80519a3df8b7282ffaac` defaultsSMA50(close). Ownerconfirmed laterbar wicktouchMA/closeinside, entryatthatclose withtakerfee, firstweeklydirectiontouch andexpiry nextNYMonday00exclusive. ShortSL originaltouch5mhigh, TPoppositeMondaylow; longmirrored. No newvolume/oscillator/body thresholds, no BE/split.
- Builder`aab81a7be1` precedeshistory. `experiments/active/exp-monday-ma50-retest-20261024-v1/` containsfixedconfig; runner`monday_ma50_retest` reuses633frozenMA6 sourceidentities andexistingNautilus1.231.0, withledger-only`monday_ma50_analysis`.13focusedtests pass. Crypto gap/SMAreset/weeklyprefix andgoldquality boundaries retained; no-confirmation/invalidentity/uncensored opportunities kept. Completed-close price iszero-latency assumption; independentRnotaccountreturns.
- NativePID23567/session27105 launched2workers at10:13UTC from68f911fc40, withautomatic633asset ledger validation/analysis. Read `continuation.json` and `run-state.json` beforelaunch/resume. New study hasitsownprocesslock andsuccessmarkers; do not touch runninglimit/reclaimEXP-206. NotionresearchEXP-207`4038856479af81758f5cea9648662569` created/readback; nativebatchnowrunning, no fullresults yet. Ownerrequestedtestonly; no newHTML. Originalbrowserresearchtab977883163closed, allfourusertabs retained. No live/model/productionchanges.
- Validated partial snapshot at10:23UTC covers10assets,4774nativepositions/9548fills; full BTC185/ETH188/XAU391 signals all resolved. Net meanR BTC-.137181/ETH-.456150, goldgross+.038671; wins17.84/21.28/21.99percent. Complete matched meanR signal/control BTC-.132322/-.547870, ETH-.438372/-.134282, XAUgross+.032783/+.006291, all excess95percent intervals includezero. Later2023+ meanR-.660065/-.717787/+.018118gross. Firstgroup complete does not mean full633pool complete. `analysis-partial/` is only this snapshot; do not keep regenerating whilebatchruns. BTC/ETH actualfills match candidateconfirmationprices/times exactly for1485positions. NegativeR but positiveallperiodnotionalmeans are position-weighting differences, notaccountreturns; see existing positive-price-returns learning. Allpool analysis remains automatic after633.

## 2026-10-24 · Notion 1.618 limit/reclaim study running after external-disk recovery

- Source task70 `4038856479af8029a91ec6bfe4a6002f` is separate from completed MA6 and pending4hN5 pullback. Owner confirmed: inspect immediate next timeframe candle CLOSE aftertouch; no initial stop duringwait; if strictly reclaimed, arm exactentry stop; otherwise marketexit. Targets separatelyfull: nearMonday,near+.5range,near+.618range,oppositeMonday,opposite1618;shortmirrored. Restingtargets fromfill mayexitbeforeconfirmation andwillbe countedseparately.
- New experiment `experiments/active/exp-monday-limit-reclaim-20261024-v1/`, immutable originalsources:633assets,6crypto+spotXAU real1m support1/3/5/15/60minutes;626othercrypto5m support5/15/60only. GoldHistData clock/conflict rules reused; unknownfees/grossonly. Actualmaker/taker perfill forcrypto; no initialR, reportentry-notionalbp. Fixedmatrix and3matchedrandom attempts, unfilled/censored retained.15focusedtests pass; native longhistory streamsoneengine perasset. Code/config committedbeforehistoricalrun; successfulidentities cannot silently replay. No model/service/livechange.
- Owner confirmed Elements SE connected; direct original-file read recovered. Six-worker `--pending-only` run is active in `native-resume.log`; the first BTC/1000SHIB successes remain frozen. Read done/error markers before resuming; never launch a duplicate pool or replay completed assets. Initial builder4b4f1e9002; gold missing-minute boundary repair18d2da8b34 preceded any gold run; CLI81df87c1f2 skips successes. `builder-epochs.json` records this distinction. Current focused19/native56 tests passed; ledger-only validation reconciled initial28,570positions/57,140fills without execution replay.
- Running research Notion EXP-206 is `4038856479af815aaf36ed15b2bd3cd7`, linked to task70 and read back. Full analysis must wait for633successful assets, compare identical six-crypto source coverage across timeframes, and keep goldgross separate from cryptonet. Summary/CSV analysis builder is being finalized; no full-pool profitability claim yet. Current job state and next commands are in `continuation.json`.
- Analysis1b32d7e8c7 processed46completedassets/263050nativepositions/71280signalconfigurations/55374strictpairs as explicitlypartial. Current346f7f3839 adds unpaired-attempt counts/basis and6coin source-bound verification;12statistics tests pass and actualSHIB5m/0.5cell reconciles224signal attempts=223paired+1unpaired. Independent Luna review found no blocking statistical issue; paired means are complete-match conditional, not all-opportunity expectancy, and Holm is25per-scope/period only. One-shot finish process19856/session53243 waits on native17322/session8028, then validates633ledgers andwritesfullanalysis. Read `postprocess-state.json` first; do not launch duplicate native oranalysisjobs. Completion still requires finalfinding review/Notion sync; no fullpool result yet.

## 2026-10-24 · Notion MA6 retest native study complete; detailed report delivery

- Owner confirmed cumulative third-new-extreme invalidation, lowestMA minus0.5percent/highest plus0.5percent, both single-target exits andequalhalves. Native633/633complete:72429opportunities,28105orders,45186nativelegs,90372fills includingrandom. Frozen successful629assets were not rerun;commit0dd3963687 fixes post-quantization passivegeometry andonly4failed jobs retried. Sixfinalfocusedtests passed;earlier20focused/78management regressions retained. Config's launch-time status text is historical;summary.json is current completion authority.
- BTCbestMondayfull33closed mean+.354R;ETHbestfar27closed+1.092R;XAUfar16closed+.438Rgross. Altfar6353closed11.33percentwins,+.108R;half+.086R,130crosscoin longestlosses versus30singlecoin. Strictalthalf4075groups excess+.041R,CI[-.010,.102],Holmp1;positivefullpoolmean doesnotprove stableedge. Goldnetunknown;Rnotcashreturns. NoBEtestimplied. Independentverifiedgpt-6-luna/max reviewer reconciled all12allperiodstats,36strictcontrols,halfleg arithmetic andfees.
- Detailedreport and64real5-panel candle/MA samples in `artifacts/monday-ma6-retest-20261024/`; delivery.json records finalHTML/CSV/QA/reproduction. Sourceapp `reports/monday-ma6-retest-20261024/` reusesapprovedfirstStitch. Browserledger retains allrows withcommoncolumns;fullfield7CSVs separatelyavailable. Notionrecord4038856479af814ab65ce6d734c65332 created/readback. No native reruns needed. Separate earlier4hN5pullback entry/expiry definitions remain unanswered;do not conflate orpauseitsfollowup. No model/training/livechange.

## 2026-10-24 · Top200 causal pattern review and three admission comparisons completed

- Owner asked for200per-trade small-timeframe candle/volume/MA analyses, candidate entry schemes andframework validation. `experiments/active/exp-monday-top200-patterns-20261024-v1/` contains frozen config,source-size/mtime coverage,200features,ninevariant results,year breakdown andconcentration sensitivity. Original image folder nowalsohas `01_逐笔K线成交量均线分析.csv` (200rows), `02_三类入场条件框架验证.csv` (144rows), `03_去除两大集中行情周的敏感性.csv` (36rows). Small companionZIP inparent; original200images/ZIP untouched. No newHTML requested.
- 147/200touch5m volume peaks,ratio median4.61;only18pre-entry closes beyondall6MAs,111against all6,96all slopes againsttrade.75coin/week/side groups are NOTindependent market events:38weeks,113rows concentrated in2021-04-19/2025-10-06.143far-target rows andholdingmedian161.67h.130rows have>5percent adverse excursion evenexcludingentry/exitbars.29timed entries cannot usecompleted touchbarOHLCV atentry. Quantitative reviewcovers200;manualimages reviewedprevious1/38/51/100/136/200 andcurrent6/73.
- Threeindependent filters: touch5mvolume maxprevious20+self; lastcomplete5mclose beyondsixMAs; lastcomplete5mwickretest of20MApair withbodyinsideanddirectionalclose. Appliedseparately tooriginal5mengulf-near and1hbodyN3near/far. Originalentrytimes,SL/TP/costs unchanged.631assets,81857configuration signals/62923distinctentries,323105frameworkrowsincludingcontrols. ReuseNautilus/VectorBTfills,zeroexecutionreruns.9variants allnegative after2023;5mengulf+volume mean-.178R vsbaseline-.292;1hnear+sixMA win64.68percent,payoff.432,mean-.073R. Farretestall+.008R failslater-.110R. Removingtwopeakweeks leavesall9negative. Do notcallthese newwaiting-for-retest entrystrategies.
- Buildersddce57695b/190b65ae76/80463288e1precederespectiveoutputs;6tests,81857financial-row equalities,18aggregatebaselinecomparisons and62fullsource/reviewMAchecks pass. Notion4038856479af816aaaf5c6ab29a738ab synced/readback. No browser/service/model/livechange. Earlierpendingpullback andNotionMA-retestdefinitions belowremainunanswered andwereNOTassumed.

## 2026-10-24 · Top200 Monday winning-trade candle folder delivered

- Owner requested highest-return200completed trades, small-timeframe candles, one folder for manual review. Used completed Monday1.618 families only; no new execution. Ranked cached net percentage of entry notional, not R/leverage; same symbol/side/UTCentry/price keeps best measured exit, different confirmation times remain separate.167617distinct positive entries yield200rows/66coins/75symbol-week-direction clusters.171long/29short;143far-target,29timed-grid,18canonical parameter,10hourly-body records. Gold unknown costs remain in a separate grossCSV.
- Folder: `artifacts/monday-top200-review-20261024/收益率前200笔_5分钟复盘/`.200numbered3200x3200 PNGs each show5m trigger,entry,exit candles/volume/sixMAs and complete original5m close/range path. Chinese200row worksheet has3blank personal-note columns;200OHLCV+MA CSV.GZ files allow later zooming. Archive and delivery/selection/source/chart manifests are in the parent directory. Only small metadata tracked; no newHTML requested.
- Selection builder53961e44ae precedes selection; renderer870a1dbbf9 precedes final images. Original per-symbol identity manifests avoid slow full-universe negative file stats on the external drive; input sizes/mtimes checked without content hashes. All200cachedgross/net reconciliations,entry/exit price-in-bar andMondayanchor checks pass;all200PNGs decode. Ranks1/38/51/100/136/200 visually inspected.3focusedtests pass. New registry rows validate; global registry remains blocked by pre-existing unrelated tradingview-candle-probe-20261023-v1 holdout_status=not_applicable, unchanged.
- Preserve source timing and execution caveats:29timed-grid rows retain5m fill-clock uncertainty;extreme source wicks are not silently removed. These are outcome-selected winners and ex-post best exits, not an estimated win rate or deployable selector. No fees,SL/TP,models,services or live state changed;no browser created. Pending Notion/retest rule definitions below remain unresolved.

## 2026-10-24 · Notion six-MA retest research awaiting rule definitions

- Owner then supplied parent task4028856479af8093b122e52fa6efa873. Re-read parent and child: parent frames1618 as observation, lower-timeframe confirmation as entry, and questions whether15m is too late relative to5m. Added source context to the same config; it supplies no numeric stop buffer, new-extreme counting or target allocation. Do not treat brainstorm questions as confirmed density thresholds, ML training authorization or a5m executable strategy. Parent connector leaves two drive/figma blocks unread; comments endpoint returned only an unsupported-feature status.
- Owner requested detailed backtest and HTML for Notion page4028856479af80039ef9fa7bf3703760, titled 小级别确认. Text and both WIFUSDT15m images read; preserve exact source in `experiments/active/exp-monday-ma6-retest-20261024-v1/config.json` (source commit d5914b2796). Existing633asset universe and Nautilus1.231.0 available; six MAs are SMA/EMA20/60/120. Do not use the superseded fixedEST gold clock.
- Three async questions remain unanswered: three new lows/highs cumulative or consecutive; stop reference and numeric buffer; separate full exits at oppositeMondayedge/far1618 versus split allocation. The second image visually favors the lowerMA envelope as long stop reference, but supplies no numerical buffer. `run_enabled=false`; no signal results, fills, profitability or completed HTML yet. Finalize causal pending-limit repricing/range invalidation and monitoring expiry before execution; existing adapter supports fixed resting entries but these new semantics still need integration.
- The old229-symbol tail lacks WIF. Its source example is available in existing `data/crypto/market/research-spike_v128_recent_20260923/series/WIFUSDT.csv.gz`, physically read89904rows through2026-09-23. Config records this supplementary source; overlap/continuity audit must precede merge, preserve gaps, and limit to the same eligible universe. No new download and no content hash audit performed.
- Requested deliverable remains detailed portable HTML using the original Stitch template, all opportunities/trades and timestamps, funnel, costs/payoff/PF/streaks/exposure-limited excursions, matched controls and seeded six-MA candle renders. No new design required. Temporary Chrome Notion tab977883141 closed and original two user tabs retained. No trading/service/browser-settings changes.

## 2026-10-24 · Chinese parameter ranking readout delivered

- Owner found the English metrics table unclear. Added the first16 data rows as Chinese descriptions:5unique configurations,7negative means. Same case/config acrossasset/time/basis is not a distinct strategy; v20 inactive forcase3/4. Top83.33percent are5wins/6goldgross trades, not robust optimum.
- Reused frozen open-priority fills; all243 canonical metric rows reconciled, addedpayoff/averagewinloss/fees/TPnetloss/holdingtime/singlecoin andmarket streakbounds. No signal or execution replay. Builder7d21a31262 precedes generation. ChineseCSV and reviewedquery in `artifacts/monday-parameter-readout-20261024/`.
- Same originalStitch report/app; new portableversion `artifacts/monday-alt-n5-report-20261024/周一1.618-详细回测分析报告-中文方案版.html`. PriorHTML preserved. Original79430signals/9065altrows/157images retained. DropdownGROUP1_GROSS corrected; mobile390/desktop1440/tablepagination/periodcomparison verified. File-protocol browser limitation unchanged. BrowserCSVevent succeeded but returnedpath notreadable locally; separatelygenerated localCSVs verified.
- Pullback entryprice/expiry still unanswered; no changes to execution defaults or qualifications.

## 2026-10-24 · Detailed Monday HTML delivered using original Stitch template

- Owner explicitly authorized reusing the first Stitch design; no new design required. Reused screen5230353fc774421d9260d60091ad8a89 and original app identity. Added full altcoin N5 diagnosis,12samples24charts,all9065rows with excursions; integrated663prior study metrics,chronological selections,target/BE transitions,BTC35rows6samples12charts andAug19. Original79430rows/60samples120charts retained unchanged.
- Portable HTML: `artifacts/monday-alt-n5-report-20261024/周一1.618-详细回测分析报告.html` (101572745 bytes). Reproducer `python -m yoyo.evaluation.monday_report_integrate` then installed Data build/export. Source commits3fb9a44765/377821ec5a precede final export. Receipt `html-delivery.json`; no trade replay.
- Desktop1440/mobile390, source scope, timeframe filters, losses/full gallery, study switches, images anddownload verified. Mobile headergrid corrected.24censored displayCSV outcomes blank; raw ledger preserves boundaryfields with explicitwarning. Browser blocks file protocol: directofflinebrowser check notperformed and no bypass; HTTPpreview verified, exporter confirms self-contained andstaticinspection findsnoexternalscripts.
- Only addedpullback test remains: entry1.618 versus50percentretracement,andexpiry nextNYMondayrangecompletion versus12h/20h unanswered. No assumedthresholds, no newfills. NativeNautilus fixedcomparison onceafteranswer, originalpinSL/nearTP,allopportunities andR0/newR/costcontrols. Report shows pending; heartbeat must not keepwaitingforStitch.

## 2026-10-24 · Focused altcoin N5 HTML evidence ready; design and pullback rule pending

- Owner explicitly requested detailedHTML ofalt4hN5 pluswaitingforpullbackentry. Frozen9065rows/9041closed/24censored/620symbols retained withouttrade replay. Addedexit-truncatedMFE/MAE andcomplete-prior-exit-barbounds:3356SL include3230positivegrossMFE,2999fee-positivehypotheticalMFE,489oneR,486oneRincompletepriorbars.2023+2809SL include2700positiveMFE,396oneR/394priorbar. DoesnotestablishBEpolicyimprovement.
- FullmeanR weekly95percentCI[-.047488,+.030036];later[-.058414,+.031468],alreadyexploredhistory. PlannedRRbelow.25 has2499trades86.3545percentwins yetmean-.002658R;strictpaired1829signal-.003341/random-.035454. All6519strictpairsretained. Originalfields/outcomes reconciled,12seededdirection/outcome samples24PNGs,60confirmationcandles checked;twofigures visuallyinspected.4.7MBZIP andfullCSV in `artifacts/monday-alt-n5-report-20261024/`.
- NewfocusedStitchgenerationGEMINI_3_8_FLASH timedout300s;8further30srecoveryreads returnoldreportonly. TemporaryIABprojectstatuspage showsSignIn/snagererror;createdtabclosed,originalusertabsretained. NoUIimplementationornewHTML. OwnerpriorrequirementstillwaitStitch remains. Newcontinuation.json links oldreport continuationandallpreparedqueries. Donotresubmitgeneration.
- Pullbackquestionspending: returnto1.618versus50percentretracement, andexpiry nextNYMondayrangecompletionversus12h/20h. Noanswerreceived,noexecution. KeeporiginalpinSL/nearMondayTP; earlierdirectlimit1Rquestion isdifferent unresolvedrule. FixedcomparisonwilluseNautilusonceafteranswers, preserveunfilledandcensored,originalriskR0/actualriskR andmatchedcontrols. Buildersb6a76fc4cd/aed4de6b57 and reviewde577c45dd precedeoutputs. Noqualificationorlivechange.

## 2026-10-24 · Proposed small-timeframe framework research queue

- Owner asked for additional strategy variants and whether frameworks can optimize parameters. Prepared10families in `experiments/active/exp-monday-smalltf-queue-20261024-v1/config.json`: fixedRTP,minimumplannedRR,cost-riskfilter,holdingdeadline,near/mid/fartarget,partialfraction,BEtrigger,stopbuffer,failedlevelcloseexit,retestlimitentry.
- FirstbatchF1-F4=72newparameter slots plus4cachedoriginalbaselines(5mengulf,15mvolumeN3,1hstrictN3,1hbodyN3).Eachfamilyreturns to unchangedbaseline;no cascadingwins/jointsearch. Proposed objective earliermean netR0, laterhistorical evaluation;also showwinranking,counts,payoff,PF,cost,excursion,streakandmatchedcontrols. Goldvolume/cost unavailable remainexplicit. Native dynamic-exit adapters mustbeextendedbeforetheirhistoricalruns.
- Preparationonly: run_enabled=false, no newthreshold orTP/SL range approved, no newhistoricalexecution. AGENTSline275requiresowner toconfirm newranges. Full package isconcreteforoneconfirmation;previouslyauthorizedgridsandnativefillsalreadycachedmustnotbereplayed. Notaproductionchangeorstandalonereport.

## 2026-10-24 · Altcoin N5 payoff and consecutive-stop diagnosis

- Frozen case4-n5-v20:9065signals/620symbols,9041closed/24censored;5578wins/3463losses. Net payoff0.608088,break-evenwin62.1857percent versus61.6967percent observed. Gross+259.810416R minusfees333.054166R givesnet-73.243750R.5685targetsinclude107netlosses;3356SL. Near-Monday full TP andoriginalpinSL unchanged;68.2336percent plannedRRbelow1,median0.547401.
- Market exit-order longestSL/negativeR streak52 across52symbols,Beijing2025-08-01 03:05to08-02 09:15 exit5minintervals. Allwithinintervalpermutationsbound52/52;singlecoinAUCTION10inbothentry/exitorder. Notfundedaccountdrawdown.144exhaustivetiechecksandcanonicalcount/fee/pairedmetrics pass,noexecution replay. Buildera5bb4c7582 precedesoutputs.
- Detail source `experiments/active/exp-monday-vectorbt-parameters-20261023-v1/alt-case4-n5-detail-20261024/`:full9065CSV,group/year/month/side/hour/coinmetrics,quantiles,alllossruns,52originalrows,definitions,summary/validation,1.9MBZIP. Notion4028856479af8111b1afdaa5158ce6d2 appendedandreadback. PendingHTMLcontinuationnowreferencesdetaildata;Stitchlistagainshowsunchangedoldreportonly. Learning aboutcrossassetlossordering saved. No newdefault,productionqualification,UIorservicechanges.

## 2026-10-24 · Explicit Monday HTML update awaiting Stitch

- Owner asked where the HTML is, annotating BTC case4 N5 win65.71%. Default no-unsolicited-report does not cancel the earlier explicit HTML request. Old complete baseline HTML remains `artifacts/monday-cases-report-20261023/monday-1618-report.html`; it lacks subsequent parameter/exit research and must not be presented as current.
- Prepared canonical update without any trade replay: `yoyo/evaluation/monday_report_update.py` (builder082a1d3d2d), stable report appID unchanged. `artifacts/monday-cases-report-20261024/continuation.json` is the resume contract. Existing79,430signals/60samples preserved;663new metric rows checked against frozen sources;35BTCN5 native trades,6seeded win/loss samples/12PNG and13CSV31.5MB companion ZIP ready. New HTML is NOT built or complete.
- BTC N5:23/35positive=65.71%,mean netR-0.051744,payoff0.453813,PF0.869809;2023+20trades60%wins,-0.221301R. All12SL trades previously had positive gross MFE;5 reached1R,also true using only completed pre-exit bars. No other exit policy simulated.
- Stitch edit screen5230353fc774421d9260d60091ad8a89 in project8978791379095391159 usingGEMINI_3_8_FLASH timed out300s. Three project recovery reads andscreenlist expose only olddesign;latestprojectupdate19:33:56UTC. Owner previously explicitly required waiting, no waiver. Do not silently use olddesign fornewlayout or resubmit samegeneration. Data source buildStatus paused; UI source andoldcompiledHTML unchanged. Continue once new image andcode appear; exact remaining steps in continuation.json. No test server/browser created; claimed existing userStitchtab left intact.

## 2026-10-24 · Aug19 Beijing review of Monday1.618

- Owner requested8.19 retrospective;context fixes2026/Beijing. NYMondayAug17 is BeijingAug17noon-Aug18noon. BTC upper65432.5044 first touches22h17 andETH1938.8884 at20h49 (verified1m intervals), then closes68523.7/2085.16 onAug19;day gains5.71/8.78percent. Both lower levels untouched. Observed63firsttouch symbols:53upper,10lower;38upper at23h. Coverage is not the entire market.
- ETH15m20h45 pin meets21-bar volume maximum andthree body holds;native cached entry21h45at1931.74,SL1942.79/TP1918,SLduring22h50–22h55,net-1.175318R includingtaker0.05percent each side. Original3controls same mean. ETH1h pin itself valid butstrict nextwick andlater body confirmation fail;BTC15m volume/confirmation andhourly/fourhour pin fail. Preserve valid losers;do not retrospectively relabel acompleted15m signal usinglater1h information.
- No new execution:cachedVectorBT timedoutcomes andNautiluscase2 fills projected once forreview;source path/size/time,completeMonday/day andanchor equality,ETH1signal+3controls,invalidgoldMonday assertions pass. GoldAug17 anchor conflicts rejected;no XAU conclusion. BTC/ETH15m PNG shows allwicks andAug20 follow-up in gray,visually checked. Source08ae155e12 beforegeneration;canonical `experiments/active/exp-monday-aug19-review-20261024-v1/`,rawOHLC/cachedoutcomes local,statistics/candletrace/firsttouches tracked. Notion4028856479af81569831cf3326ab4cf0 read back. Learning saved. No parameters,training,production,services or live actions.

## 2026-10-24 · First Monday1.618 limits: four timed exits with clock bounds

- Owner explicitly confirmed first weekly directional resting limit at1.618, no candle confirmation or early stop; timed market exit after5/15/60/240minutes. Same registered633-asset universe, only holding time changes. Completed NYMonday levels known before orders; original missing/conflicting-data exclusions retained. Native VectorBT1.2.0 crossing/gap helpers plus Portfolio.from_orders only, no second engine or custom per-bar fills. Builder a3d5eaa01c, analysis5ca58ed85a committed before generation.
- 65697 first touches,65644 with all four expiry intervals observed:BTC289/ETH299/XAU622/alts64434;262716 available signal-horizon records. Native1531141 unique scenario positions/3062282fills include controls and clock-bound scenarios.13focused tests and complete input/fee/2-fill/native-record/first-touch/cohort checks pass. Existing1m refinement requires matching source OHLC and all expiry windows; BTC171,ETH167,XAU622,alts667. Intrabar entry belongs to a1m/5m interval: grid exit at interval-open+h is a reference, not exact elapsed h. True elapsed exit OHLC bounds are identification limits, not confidence intervals or tradable oracle prices.
- Shared5m grid wins/means:BTC151/289=52.25percent,+2.4387bp net;ETH154/299=51.51percent,+19.7065bp net;XAU354/622=56.91percent,+0.4751bp gross;alts31080/64434=48.24percent,-9.5079bp net. True-clock win bounds BTC37.02-69.90,ETH37.79-69.90,XAU45.82-66.56,alts24.78-78.65percent. Maker0.02percent entry/taker0.05percent timed exit actual notionals, XAU unknown costs stay null. Binance histories with current OKX fee scenario; no queue, tick schedule, slippage, funding, margin or liquidation.
- Alts15/60/240m grid wins55.25/56.73/55.89percent,mean netbp-0.0418/+13.9905/+12.9692;2023+ meanbp+1.2590/+10.7457/+11.8415. Strict paired1h64015 signals mean11.8067bp versus same-week/side/lag-vol random-18.3386bp; reference absolute positive Holm p.087, versus random p.030, neither meets.01. Grid uncertainty remains. Firstgroup summarized separately; combined firstgroup is gross only. AUC/top-decile/R not applicable to this no-stop fixed-entry study.
- ETH2020-03-13 02:35UTC5m high323 agrees with official Binance API, but does not prove available size/queue. Removing only largest winning reference trade diagnostically changes5m mean19.7065 to4.0383bp; raw sample remains. Unlimited alt short worst5m-107.35percent/4h-790.46percent, no funded-account claim. No verified profit, production/training qualification or default change.
- Canonical `experiments/active/exp-monday-limit-timed-vectorbt-20261024-v1/`: summary,120-row metrics/Chinese stats,weekly inference,year/direction/precision,extremes,validation/completion,source quote receipt.262716-row `逐笔挂单定时平仓.csv`, raw fills and coverage retained locally. Notion4028856479af815db17cc08424df4245 read back. Learning `intrabar-limit-fills-do-not-have-an-exact-holding-clock.md`. No UI/browser/service/live changes.

## 2026-10-24 · App 0.8.26 — faster candles, local watchlist and owned positions

- All chart periods keep open-candle websocket updates. A 3-second silence starts 2-second REST fallback; manual refresh joins an in-flight snapshot. Signal/automatic-stop calculations remain closed-bar only. Current-price line dashed, Monday reference lines solid.
- Local coin logos, persistent favorites/filter, week-scoped hide/delete and undo added to touched contracts; no search autofocus. Recently completed rows compact and expandable. Page-top pull refresh waits for visible state/chart/statistics reads, with success/failure states and touch ownership separate from chart panning, horizontal navigation and draggable sheets.
- Owner selected inheriting current shared exchange leverage. External activity hidden and not a blanket entry gate. Exact App entry/close/protection child fills reconstruct owned remaining cost, fees and PnL; stops/TP sizes cap to owned quantity. Persist receipts and old protection generations. Fixed legacy cutoff recovers stale merged-lifecycle closes without attributing excess external exits to App. Read-only production clone recovered the observed stale lot with zero writes; service reload then showed no private error. Exchange collateral/liquidation remains shared.
- 213 Python + 82 frontend + 5 asset tests pass. Browser320/390 native-touch/favorites/delete/restart/compact-history/slow-refresh/failure acceptance passes with zero trading writes; earlier shared-leverage UI and four-period canvas stroke checks pass. iOS/Android0.8.26(35) built/signed,437 bundled files identical. Installed and launched on connected iPhone; device version/build read back. Physical haptic feel/native frame rate not measured. QA `output/playwright/price-line/`, build receipt `yoyo/discipline_app/releases/build-0.8.26.json`. Stitch GEMINI_3_8_FLASH sources under the three versioned design directories. Two learning notes saved. Test browser44620 and fixture44582 closed.

## 2026-10-24 · App 0.8.24 — shared finger-tracking sheets and haptics

- All six bottom-sheet flows use one frame-coalesced controller: handle/title drag tracks 1:1, shade follows displacement, release exits or rebounds from the visible position, and regrabbing a rebound does not jump or inherit flick distance. Reduced motion keeps direct manipulation and skips settling. Dialogs portal to the App root so the chart page cannot clip them or place navigation above list rows. Body scrolling, focus trapping and submission locks remain intact.
- Native Capacitor haptics now cover sheet open/close, contract/period selection and existing slider detents, with throttling and the persistent opt-out setting. No continuous drag or market-refresh vibration. Mobile web adds optional standard Vibration API feedback and a More-menu toggle; unsupported browsers stay silent and disable the control. No execution or sizing changes.
- Owner explicitly waived Stitch after its 300-second GEMINI_3_8_FLASH timeout.82 frontend checks and5 asset-delivery checks pass. Browser320/390 covers six sheets, real touch events, interrupted rebound, body scroll, reduced motion, list selection behind the former nav overlap, opt-out and delayed-preview submission locks;0 JS errors and0 trading writes. Web320/390/1440 retains75% mobile quick-ticket height and verifies toggle persistence/unsupported fallback. Evidence `output/playwright/app-haptics/`; two learning notes saved.
- Production assets and iOS/Android0.8.24(33) built and signature-verified; bundled assets match. APK `yoyo/discipline_app/releases/spike-discipline-0.8.24.apk`. iPhone remains unavailable: not installed, physical haptic feel and native frame rate not verified. No backend restart. Test browser PID38087 and fixture PID38047 exited; active test streams0. Monitor HTTP200, execution heartbeat1.27s old without errors, four enabled patrol schedules confirmed after cleanup.

## 2026-10-24 · Near/far50/50 exits with TP1-triggered fee breakeven

- Owner authorized the quoted two-arm comparison. Frozen case3 bodyN3 entries: hourly first weekly touch, pin body strictly inside1.618, following3 bodies at/inside and wicks unrestricted. Both arms near50%/far50%, original pin stop; only actual TP1 fill enables remaining-leg own-cost BE in the second arm. XAU price BE and gross only. No new filters, maximum holding period, target fraction search or default/live changes.
- 633assets,19048signals,74161 total signal/control parents. Cached native near/far fills form the exact hold-stop mixture;40328 non-TP1 parents unchanged. New BE paths run once in Nautilus1.231.0:33833parents/135332fills. Actual maker0.02%/taker0.05% fees; gold unknown costs stay null. Native TP1 matches prior fills, equal quantities and actual commissions/PnL audited;48 related tests and full ledger validation pass. Summary/export uses the VectorBT environment to read completed results, no second simulation.
- Hold→BE: BTC83 win22.89%→39.76%, mean netR-0.14886→-0.14222;ETH79 win22.78%→41.77%,R-0.23612→-0.28463;XAU136 win36.03%→50%,mean grossR+0.15616→+0.05158. Alts full closed18698→18722 win29.05%→48.35%,mean netR-0.094484→-0.104327;unresolved52→28. On identical18698 closed parents, BE mean-0.104707R, effect-0.010223R (weekly95%CI[-0.025688,+0.005483]);5257 original far SL runners protected,2197 eventual far TP runners cut, gains+2744.911R versus losses-2936.068R. No demonstrated expectancy gain; all15 policy-improvement Holm p=1.0. Firstgroup298 mixed gross mean+0.014555→-0.043759, not net portfolio return.
- 2023+ BTC42 net-0.49784→-0.48869,ETH44 -0.25828→-0.32635,XAU25 gross-0.03469→+0.05880;alts fullclosed-0.11339→-0.11966, common15153 effect-0.006760R. Gold recent improvement has small sample and unknown costs. Prior data already researched. Matched random controls retained by actual NY entry week, symbol/side/vol bucket and3 closed controls. Stop gaps may lose beyond BE. Small TP1 cannot always arm cost BE immediately;BTC1/alt188 closed parents deferred to a later observed5m close/open point. No silent filter.
- Canonical `experiments/active/exp-monday-tp1-breakeven-20261024-v1/`: summary,metrics,yearly,be-changes,policy-inference,validation,completion;Chinese19048-row `逐笔分批保本对照.csv` and60-row `分批保本统计.csv`. Native legs/fills retained. Builder1906c3f78a,delivery1b5dfab004;reproduction commands in completion.json. Notion4028856479af816d9168dcf69089eb2f read back. Learning notes cover fee-dependent native execution and common-resolved comparisons. No UI/browser/service changes.

## 2026-10-24 · 75% quick-order sheet with continuous drag dismissal

- Mobile web quick ticket height is 75% of the visual viewport (633px at 844px, 300px with a 400px keyboard viewport). Handle/title drags now track finger displacement 1:1 with no easing during drag; backdrop fades proportionally. Long drags/short flicks exit from their current offset, short/canceled drags return to rest, regrabbing a rebound resumes its visual position. Reduced motion retains tracking and skips settling. Body scrolling, 44px stop controls, holdings/orders and desktop sizing retain their existing behavior.
- Exit retains the trading owner until animation completes; page teardown remains immediate and cancels animation/capture. No order logic or backend changes. Removed mobile sheet/backdrop blur during this refinement. Stitch GEMINI_3_8_FLASH screen `66da1690d7fc497295a913c4a9ee2d07`, source in `yoyo/monitor/design/quick-sheet-75-20261024/`.
- 21 focused frontend checks and 5 asset checks pass. Browser widths 320/390/1440 verify exact 75% mobile height, 20/45/90px mouse tracking, 100px native touch-event tracking, short rebound/touch cancellation, closing/reopen, single body scrolling to holdings, keyboard input and reduced motion; 0 JS errors and 0 real trading writes. Read/preview defaults use synthetic intercepted responses. Evidence `output/playwright/quick-sheet-75/`; learning `docs/learnings/sheet-dismissal-must-start-at-the-finger-offset.md`. Static assets update on refresh; no service restart. Test browser PID 31839 exited; monitor HTTP 200, execution worker running without errors, heartbeat age 1.2s, and 4 enabled patrol schedules confirmed after cleanup.

## 2026-10-24 · Live forming candles across all web Monday chart periods

- Web Monday chart now displays native forming OHLC for 1m/5m/15m/30m/1H/4H/1D (daily remains 1Dutc). One selected public OKX business WebSocket updates the tip; a display-only GET tail falls back to three-second polling with the shared two-second transient cache. Switching/pausing retires sockets, requests and callbacks; reconnect repairs history. Slow REST cannot overwrite newer WS data, and an unconfirmed candle is never promoted merely by crossing its time boundary.
- Existing `MondayCandles.snapshot()` and its `candles`/`last_close_ms` contract stay closed-only for automatic wick stops. Display merges happen only in the chart. Existing signal/stop rules and independent intrabar first-touch notifications are unchanged. Same canvas, incremental candle updates and existing historical/follow viewport behavior; no account/trading mutations or native App build changes.
- Stitch GEMINI_3_8_FLASH screen `326ae6e7a6a647d7b160244661677b60`, references in `yoyo/monitor/design/live-candles-20261024/`; only normal candle styling was adopted. 64 frontend and56 Python checks pass. Synthetic browser320/390/1440 covers all seven periods, one socket per selection, stable historical view/canvas, fallback/reconnect and pause cleanup with0 JS errors/writes. Real BTC4H browser received3 live WS data frames, current forming bar and HTTP200 fallback seed; all seven deployed local endpoints returned current forming bars and no-store. Evidence `output/playwright/live-candles/`.
- Existing monitor service restarted to load the separate read endpoint. Execution worker was not restarted; no orders, cancellation, position changes or strategy settings were sent. Final17 clock/feed regressions pass, including stale REST not resetting the live clock. Test browser `live-candles`/PID29588 closed and exited; monitor200, execution heartbeat1.15s old with no errors, four patrol schedules enabled. Refresh the website to load the new assets. Learning: `docs/learnings/live-chart-tips-must-not-widen-closed-data-contracts.md`.

## 2026-10-24 · Latest-touch list favorites and deletion

- Add independent star/delete buttons to every web Monday touch row and a fourth Favorites filter. Delete locally removes current NY-week/symbol/side occurrences with inline undo; refresh/history reconciliation cannot revive them, while new sides/weeks remain eligible. Favorites persist by symbol, support search and cross-tab updates, and show an honest waiting state before the new week's first touch. Storage failure is visible; no monitoring/subscription or trading mutations.
- Stitch GEMINI_3_8_FLASH screen `dc72d0d4db4b47c6bb9536892b483641`, references in `yoyo/monitor/design/fib-list-actions-20261024/source.json`.46 relevant frontend tests and5 asset-delivery tests pass. Browser 320/390/1440 verifies40px action targets, zero horizontal overflow, no search autofocus, unchanged chart on favorite, deletion/undo/reload, week rollover and another-tab updates; zero JS errors/API writes. Evidence `output/playwright/fib-list-actions/`; learning saved.
- Static asset versions update on refresh without backend restart. Personal list preferences are device/browser-local; native App binaries are unchanged. Test browser `fib-list-actions`/PID26362 closed and exited; monitor alive, four patrol schedules enabled, execution heartbeat0.5s old with no errors after cleanup.

## 2026-10-24 · Near versus far Monday targets on fixed body-confirmation entries

- Owner authorized both TP interpretations. Near: long Monday low / short Monday high. Far: long Monday high / short Monday low. Original NY pin-week anchors frozen. At each N, entries, pin stops, fees and random entry times stay identical; original near-rejected candidates are not added. Native VectorBT limit exits, no expiry, partials, breakeven or new RR/cost filters.
- 633 assets / 10 configs done.381149 new far unique native positions,381780 near rows reused;199692 configuration-signal rows include repeats.7 focused tests pass;75 frozen near metric rows reproduce;381780 paired entries/stops,213392 invariant original SLs and762298 native fills audited. No replay through a second engine. MFE stops at actual exit; unresolved trades excluded from win/loss.
- N3 meanR near→far: BTC83 -0.2587→-0.0391 net;ETH79 -0.2522→-0.2201 net;XAU136 +0.0346→+0.2777 gross. Alts18733/18698 closed -0.0867→-0.1004 net,17/52 censored; both-closed18698 cohort -0.0885→-0.1004. NearTP→farSL BTC18/ETH19/XAU33/alts5271; far positions reaching1R thenSL22/24/39/5407. Firstgroup298 combined gross -0.0770→+0.1061. R and nominal price-bp weight risks differently; BTCfar all-history meanbp+13.59 despite negative meanR.
- Separate pre2023 selection: near BTCN4/ETHN2/XAUN4/altN5, far BTCN4/ETHN4/XAUN5/altN5. All selected2023+ meanR negative; far later BTC-0.1488,ETH-0.2248,XAU-0.0055gross(14trades),alts-0.1164 versusnearalts-0.0688. Both selected alt policies outperform matched random (Holm0.004998 across10 comparisons) but still lose in absoluteR. No default change, training/promote/live.
- Canonical `experiments/active/exp-monday-near-far-targets-vectorbt-20261024-v1/`: summary.json,metrics.csv,year-direction.csv,target-transitions.csv,validation.json,completion.json. Chinese `逐笔近远止盈对照.csv`19048 pairedrows and `近远止盈与参数统计.csv`150 rows. Reuse via `scripts/backtest-monday-targets`, validate/export via `monday_target_delivery`. Notion4028856479af81849260fb6d15a6dbf8 read back. No UI/browser/download/dependency change.

## 2026-10-24 · 1h Monday body-only confirmation and detailed excursion statistics

- Owner changed case3: following hourly wicks may cross1.618, bodies may not break it. Implemented open AND close at/inside (equality allowed), original pin body strictly inside, first weekly touch per side unchanged; no volume. Entry afterNcomplete hours at nextopen, original pin stop and nearMonday edge limit target. NewN3 compared with immutable strictN3, thenN1–5 separately using VectorBT.
- 633assets/6configs done:156134new native framework positions,225540cached geometries;110508configuration-signal rows include repeats.15tests pass,915strict-subset checks,15strict baseline metric rows reproduce,312268new native fills verified. No second-framework replay. MFE/MAE truncated at exit path, never counts post-stop recovery; prior-exit-bar lower bounds and fee-adjusted MFE retained.
- NewN3: BTC83trades mean net-0.2587R, ETH79 -0.2522R; XAU136 mean gross+0.0346R (unknown costs); alts18733 net-0.0867R vsold-0.0518R,17censored. BTC10/ETH12/XAU14/alts2020trades reached1R thenSL. Alts114TPfills still netloss aftercost. Firstgroupcombined298 mean gross-0.0770R, not combinednet.
- Earlier-selected N: BTC4 later36 -0.0805R;ETH2 later55 -0.2199R;XAU4 later17 -0.1408grossR;alts5 later12131 -0.0688netR, better thannewN3 -0.0953 but worse thanoldstrictN3 -0.0612. Alts outperform matchedrandom (Holm0.002499) yet lose absolutely. No default change, training/promote/live.
- Canonical `experiments/active/exp-monday-1h-body-hold-vectorbt-20261024-v1/`: summary.json, metrics.csv, year-direction.csv, completion.json, validation.json. Chinese19048-row `交易明细-新规则3根.csv` includes UTC/Beijing entry/exit and excursion times;90-row `参数与盈亏统计.csv`; rawfullledger retained. `scripts/backtest-monday-body`, `monday_body_validation`, `monday_body_export` reproduce/reuse. Notion4028856479af8182a016d29a22acc1cb read back. No browser/dependency/download/UI change in this study.

## 2026-10-24 · Compact quick ticket with holdings and order management

- Mobile web Monday quick ticket now occupies 65% of the visual viewport, with 44px/16px stop inputs. A single scroller contains entry, summary/action, then holdings/orders; inline actions support explicit close, stop amendment and pending-order cancellation in paper/live modes. Strategy lots retain their existing management owner; unmatched external positions/orders remain visible with a clear read-only reason.
- Free-manual management uses durable preview/confirmation commands, exact account/order/position/protection revalidation and one persisted POST boundary per action. Timeout/restart only reconciles the original identity, including OKX 50004; TP remains intact. Paper stop amendment preserves the latest TP atomically. No actual trading commands were issued during implementation or QA.
- Stitch GEMINI_3_8_FLASH screen `b8b04caa2ba8475a9efab100e295dff9`; references in `yoyo/monitor/design/quick-ledger-20261024/source.json`. Broad Python run: 456 passed; subsequent focused management/dashboard run: 61 passed; final timeout/trigger-basis cases: 27 passed (overlapping suites). Final frontend checks: 25 passed. Browser 320/390/1440 verifies dimensions, one scroll owner and six intercepted management paths; keyboard viewport 400px gives 260px sheet and visible stop input. Evidence `output/playwright/quick-ledger/`; two learning notes saved.
- Updated only the existing monitor and manual execution services after checking no pending free/management commands or unconsumed strategy confirmations. New management revision `free-management-20261024-v1`, fresh worker heartbeat, empty execution errors, account ready, both management gates ready, and four patrol schedules still enabled. Test browser `quick-ledger` closed. Refresh the web page to load new assets. Native App binaries are unchanged.

## 2026-10-24 · Touch list opens without search autofocus

- Opening the web Monday contract list focuses its close button instead of the search input, avoiding automatic mobile keyboard activation. Explicit search tap, filtering, Escape and launcher focus restoration remain functional.
- Stitch GEMINI_3_8_FLASH screen64a55e101ddc40d6ae52c603561283a4; source references in `yoyo/monitor/design/fib-list-focus-20261024/source.json`.16 existing frontend tests pass; browser390/320/1440 focus/search/reopen/keyboard checks pass with0 write requests. Evidence `output/playwright/fib-list-focus/`. Static asset update, no service restart.

## 2026-10-24 · Monday chart inline numeric reference prices

- Owner requested a narrower right price axis without high/low/1.618 badges. Monday levels now opt out of reference axis labels; the shared weekly primitive draws plain numeric prices inside the existing rays. Prices, week boundaries, candle precision, colors and other renderer consumers remain unchanged. Dense optional internal lines retain their quiet presentation.
- Stitch GEMINI_3_8_FLASH screen3998c8fa2b084856955af9e19f6e3f44, source in `yoyo/monitor/design/fib-inline-prices-20261024/`.15 focused frontend tests and5 asset-delivery checks pass. Same synthetic INIT data: right axis136→74px at320/390 and136→102px at1440; four main numeric values present, zero reference axis badges or write requests. Evidence `output/playwright/fib-inline-prices/`; learning saved.
- Static asset content versions update without restarting services. Temporary browser18350 closed; no live actions or private-account fixtures. User can refresh the web monitor to load the change.

## 2026-10-24 · Hide secondary live-account tabs

- Remove the live-account tabs from Signals and Pattern Patrol, plus the Signals context shortcut; omit the hidden tab from patrol keyboard cycling. Existing account routes, account status bands and execution services remain unchanged.
- Stitch screens3fd920aaf4534bd5bcda8f681835f676/4592a8c56df3466da2db7651fced8bb7, GEMINI_3_8_FLASH, sources in `yoyo/monitor/design/hidden-live-tabs-20261024/`.51 frontend and5 asset-delivery checks pass. Live local browser at1440/390 verifies both menus and keyboard cycling, no JS errors, zero write requests; raw evidence `output/playwright/hidden-live-tabs/`. Browser17484 closed. Static content versions update without a service restart.

## 2026-10-24 · App 0.8.23 — My System in Discipline

- Add a local read-only My System card directly below the Discipline heading: only enter outside the range at1.618; wait for signal confirmation; take profit/stop loss promptly or take partial profits. Source: Owner Notion task68, `https://app.notion.com/p/4028856479af80e28a01d52495d5eba0`, edited2026-10-23. No older research draft rules or execution-policy changes.
- Stitch GEMINI_3_8_FLASH screen549b2f58f37545b2867dc1d05192e53e; design/code in `yoyo/discipline_app/design/my-system-0.8.23/`. Built assets verified at320/390px, no overflow/blur, offline tab return retains principles, existing discipline sections retained. Vendor-prefixed override ordering fixed and learning saved.
- TypeScript/Vite, iOS/Android0.8.23(32) builds and signatures pass; native/web asset copies match. Web updated without backend restart. iPhone unavailable, not installed. QA `output/playwright/app-my-system/`, receipt `yoyo/discipline_app/releases/build-0.8.23.json`. Zero order writes/live commands; temporary browser15248 and fixture15153 exited, active streams0. Live heartbeat age1.54s after cleanup.

## 2026-10-24 · App 0.8.22 — cached chart and continuous gestures

- Keep the 1.618 Canvas mounted; restore current NY-week public snapshot, selection and period locally. Cached/offline states cannot enable quick entry; old-week anchors disappear. Pause candle feeds while hidden or covered, coalesce bounded candle disk writes into idle time. No execution/risk policy or private-account cache changes.
- Menu swipes commit without the outgoing timer/remount; chart gestures stay separate. Shared sheets use a wide grab/title area and continue from the current drag offset, with short-drag rebound, no replayed entry animation, reduced-motion support and busy locks. Remove modal/backdrop/dock blur. Stitch timed out after 300s; Owner explicitly waived it for this turn.
- 64 frontend tests passed. Synthetic 320/390 browser checks: same Canvas on return; 0 hidden/covered streams; cached reload/offline/NY rollover; navigation cancel/chart exclusion; title drag/rebound/Escape/reduced motion; 1R wick=106 and busy dismissal blocked; 0 order writes. Fixed 2s slow network: old return 2327ms, new 48/40/26ms. Browser latency only, not native FPS. Raw QA `output/playwright/app-performance/`.
- iOS/Android 0.8.22(31) builds and signatures verified; device unavailable, not installed. Web assets updated without backend restart (backend remains 0.8.20). Build receipt `yoyo/discipline_app/releases/build-0.8.22.json`. No live commands. Existing services retained.

## 2026-10-24 · Monday1618 VectorBT parameter grids completed

- Owner explicitly selected VectorBT and two separate one-variable grids: case2/3/4 confirmation bars1–5; case2 prior-volume10/20/30/40 with confirmation3. Stop pin extreme, target near NYMonday edge, first touch, real OKX fees unchanged; XAU gross only and no case2 volume.
- 633assets,18unique configurations;220733configuration-signal rows (overlap across configs),546277new native framework positions,126507reused cached geometries,2462baseline signal checks.8focused tests pass. Empty frozen candidate CSVs handled. Exact-price inclusivity/open-before-intrabar priority fixed;633asset opening audit finds0affected historical fills, so no fill replay.
- Select using pre2023 meanR, evaluate2023onward, exclude6cross-cut signal rows. All selected alt variants remain negative: 15mN3 -0.1061R;1hN5 -0.0461R versusbaseline -0.0612;4hN4 -0.0430R versus -0.0556;volume30 -0.1038R versus -0.1061. BTC4h keeps originalN3, later31trades +0.2531R but -4.14bp. Selected XAU settings lose later. No default change/promote.
- Controls now verify actual NY entry week rather than trusting pin-week labels;5014configuration-signal rows cross weeks and cannot use old pin-week controls. Report paired signal/control R on the same complete cohort. Alt1h/4h outperform random (Holm0.008996) yet are not profitable. Previously explored history, not a fresh holdout.
- Canonical: `experiments/active/exp-monday-vectorbt-parameters-20261023-v1/summary-entry-week.json`, `metrics-entry-week.csv`, `all-trades-open-priority.csv.gz`, `completion.json`. Initial summary/metrics preserve pre-entry-week-audit descriptions; use canonical files. `scripts/backtest-monday-parameters` validates unchanged dependencies and returns cached completion with0replays. No extra HTML, downloads, dependency installs, browser sessions or live actions.

## 2026-10-23 · 区间1.618 limit + hold with 15m wick stop rejected (NautilusTrader only)

- Owner: "止损改成影线低点重新跑". Same limit at the first weekly Monday 1618 and 30/50-minute hold; stop = low (long) / high (short) of the 15m bar containing the touch, placed at that bar's close (price cannot pass it inside the bar). Adapter gained a one-time native stop tightening (`stop_update_i`/`stop_update`, reason 9); a close already at the wick extreme exits at that close. 250 adapter tests pass.
- 30m: OKX BTC n91 net -1.7bp CI[-13.2,10.5], later -6.1; Binance six majors n1033 net +3.2bp CI[-6.9,13.6], later -3.2. 50m: -2.3bp and +3.0bp, later -11.3/-4.6. About half the trades stop out (median wick risk 18/30bp); slightly worse than no stop, better than the 2.0 stop. Fails.
- Touch bars closing back inside the level earn +24/+47bp and those closing beyond lose -42/-70bp, but that is known only after the fill, so it is not an entry filter. Results `experiments/active/exp-monday-1618-hold{30,50}-wickstop-20261023-v1/native_summary.json`. No live orders, training/production false.

## 2026-10-23 · App 0.8.21 — minimal Monday chart

- Replace chart-wide price lines and range fill with time-bounded native primitive rays, starting at NY Monday close. Solid lines; plain 高点/低点/1.618 text; faint unlabeled midpoint; no band or reference price-axis labels. Candle/session/1R semantics retained. Settings gear exists only on Statistics; non-chart page background is now solid white.
- Stitch screen332e4a5b46a14da5b5787c27cd1d5b50, source under `yoyo/discipline_app/design/minimal-fib-0.8.21/`.59 frontend tests and320/390 browser checks passed, including4H close between bars, offscreen ranges,0 range fills, settings counts0/0/0/0/1, and existing wick quick-stop. Raw QA `output/playwright/app-minimal/`; build receipt `yoyo/discipline_app/releases/build-0.8.21.json`.
- Web assets updated, iOS/Android0.8.21(30) builds/signatures verified. iPhone install failed CoreDevice1011; not installed. Backend remains0.8.20 without restart or trade commands. Temporary browserPID5725 and fixturePID5650 closed after QA.

## 2026-10-23 · 区间1.618 limit + 30/50-minute hold rejected (NautilusTrader only)

- Owner rule: rest a limit at the first weekly Monday 1618 (short above high, long below low), market exit after 30 or 50 minutes; variant adds the 2.0 stop. NautilusTrader 1.231.0 1m via `nautilus_independent.run_native` (limit entry, terminal market close), real fees maker .02 entry / taker .05 exit.
- 30m: OKX BTC n91 net -1.9bp CI[-13.6,9.4]; Binance BTC/ETH/SOL/BNB/XRP/DOGE n1033 net +5.4bp CI[-5.0,15.1], later half -3.0. 50m: -2.5bp and +5.7bp CI[-6.4,17.7], later half -7.1. 2.0 stop makes both worse. Fails both; earlier-half gains do not persist.
- Owner: backtests use only the framework, once; no parallel direct bar computation (rule added to CLAUDE.md/AGENTS.md). hold50 direct-computation outputs were discarded unread. Results `experiments/active/exp-monday-1618-hold30-20261023-v1/native_summary.json`, `exp-monday-1618-hold50-20261023-v1/native_summary.json`. No live orders, training/production false.

## 2026-10-23 · App 0.8.20 — wick stop before 1R sizing

- 1.618 quick tickets now pass the selected chart period, read the latest closed current-week wick touching 1.618, fill long low / short high, then apply the existing 1R and exposure limits. Public wick selection remains available when opening is blocked; manual edits invalidate pending results. No execution policy changes or real trade commands.
- Shared pure selector `yoyo/data/monday_wick.py`; App uses its own chart candle sessions (daily UTC+8), checks continuous history, and refuses missing candles/no qualifying wick. Stitch screen `7debb7b7355b489f9ea600fc72c32df0`, source under `yoyo/discipline_app/design/wick-stop-0.8.20/`.
- 209 Python / 56 frontend tests passed. 320/390 browser checks cover long/short, timeframe, manual-over-late-reply, missing wick and zero allowance. Synthetic order writes 0; temporary browser PID1616 and fixture PID1640 exited. Raw QA in `output/playwright/app-wick/` and release receipt `yoyo/discipline_app/releases/build-0.8.20.json`.
- Service/public web updated to0.8.20. iOS/Android builds and signatures verified, iOS0.8.20(29) installation failed CoreDevice1011: phone unavailable. Existing live position was already awaiting-exchange before reload; latest state still blocks opening until protection reconciliation completes (1 position/0 pending, fresh heartbeat). This task did not repair or modify that position.

## 2026-10-23 · Four Monday reversal cases: requested offline HTML report

- Owner explicitly requested detailed HTML with winning/losing K-line samples. Complete local file `artifacts/monday-cases-report-20261023/monday-1618-report.html` (72.4MB, all figures/data inline); authored app `reports/monday-1618-cases-20261023/`. No public publication.
- Reused frozen Nautilus1.231.0 results:79430signals,79383closed,47censored,19case/group scopes. Case1 gold corrected347 replaces obsolete361; case2 gold unavailable volume. First touch forcase3 now explicitly owner-confirmed, no semantic change/replay.
- Detailed rules/entry timestamps,NYMonday/UTCbar alignment,fees/geometry,year/hour/weekday/direction/2023split,weeklyCI,strict3-control matched differences,independentR ledger versus account distinction.60deterministic strata(seed20261023) × local/full =120original-timeframe source PNGs, fullCSV download and filterable ledger.
- Stitch generation timed out but get_project screenInstances exposed screen5230353fc774421d9260d60091ad8a89 omitted by list_screens; design and HTML recovered with GEMINI_3_8_FLASH, no waiver/retry. Learning saved. Report uses canonical protected Data runtime with authored content/theme only; source aa109b7321.
- Statistics arithmetic test1passed inisolatedNautilus; counts reconcile,120PNGs decode,desktop/mobile390 checks,loss/full toggle,gold missing-state,offline0resource requests andCSV79430rows verified. Details/cleanup inreceipt. Notion case4 reportnote andcase3 explicitfirsttouchupdated. No source-data downloads/newdeps/nativebacktest reruns/liveorders/training.

## 2026-10-23 · Mobile Monday order drawer, wick stops and equity exposure

- Owner confirmed current chart timeframe: newest closed current-week lower/upper 1.618 wick supplies long low / short high. Missing history or no eligible wick stays explicit. Demand-only weekly backfill is separate from scanning.
- Both quick paper/live modes now use the Stitch bottom drawer, visual viewport keyboard handling, independent form scroll, fixed action, background inertness, focus return and handle-only dismissal. Desktop keeps a floating ticket. Saved mobile drag offsets cannot move the drawer offscreen; delayed defaults cannot overwrite manual stops.
- BTC and ETH each have independent 10x total USDT equity allowance; alts share 5x. Gross positions, opening pending orders and unresolved local intents consume allowance. Default100percent remaining; stop distance has no1R cap. Contract leverage defaults to instrument/quantity-tier maximum and changes collateral only. Paper reuses its existing ledger, account and durable receipts; legacy full-desk presets unchanged.
- Code88e62200f0, Stitch0e1cb04622f444cea23f0fa2a9f46919/GEMINI_3_8_FLASH.727 broad Python checks and37 final touched-backend checks (overlap),52frontend checks pass. Browser390/320/1440 plus keyboard viewport/offset, stale-stop/manual-edit, focus/swipe and intercepted confirmation verified. Real read-only BTC max100x with10x equity, paper altmax20x with5x equity, ETHFI15m wick. Zero live tickets/orders; both services updated, manual revisionv2/errors empty, market workers stopped. Existing scan/patrol/startup state in receipt. Browserfib-drawer/PID96571closed/exited.
- Evidence `artifacts/monday-drawer-sizing-20261023/receipt.json`; two required learnings saved. Unrelated README deletion, historical deletions and MondayBark edits preserved.

## 2026-10-23 · Case4 4h wick plus three inside closes: BTC exploratory, alts negative

- Owner case4:4h pin body strictly inside1618, wick touches; next3complete contiguous4h closes at/inside level, equality allowed, opens/wicks may cross. First weekly-side touch inherited. Enter pin+16h, originalpin SL/nearMondayedge TP. UTC4h versusNYMonday winter boundary handled using original5m firsttouch and knownprefix; no volume condition.
- Reused Nautilus1.231.0 native5m adapter,633symbols13156signals13132closed24censored,44824positions89648fills all checked.18focused tests; input/code stamps unchanged andBTC/XAUcache reused. ActualBTC truncated before370775futurebars preserves all29prefix signals. `scripts/backtest-monday-4h --workers 3`; builder8fdc36e4d6.
- BTC57 netwin59.65percent/netmean+.27814R,random-.21000R,PF1.670; absoluteCI[-.1362,.7504]R crosses0, matched37p.0305 exploratory. Earlier/later netR+.30793/+.25315, but later equalnotional-4.14bp. ETH59 netmean+.06877R,random-.36574R; later32-.21910R. Gold8grossmean-.26214R,random-.35984R,brokercostunknown. Group1 124grossmean+.20977R vsrandom-.23769R,absoluteCI crosses0; no mixed net figure.
- 630alts13008closed24censored,netwin57.47percent,netmean-.04906R,random-.18991R,mean-11.78bp,PF.888; earlier/later netR-.01840/-.05562.10450fullmatches excess46.06bpCI[31.94,59.84],p.00050 but absolute net loss.2600signals lack3controls; absolute results retain them. One ambiguous signal:stopfirst-639.35R versus native-638.14R.
- Screenshot weekBinance counterpart pin2026-01-28 12UTC confirms at01-29 04UTC,entry88003.2 already belowMondayhigh88983.3; skipped rather than retrospectively opened. ScreenshotOKX prices differ. Strict240observedminutes in each4h makesgold sparse; no synthetic gaps filled.
- Results/receipt/cases1-4comparison in `experiments/active/exp-monday-4h-close-hold-20261023-v1/`. Notion https://app.notion.com/p/4028856479af81878f9bc0615e838cfe write/readback confirmed. Research only,training/productionfalse. No newdata/dependencies/browser/live actions; unrelated stagedREADME deletion preserved.

## 2026-10-23 · 1.618 homepage, current-week lines and free manual entry

- Owner confirmed current NY Monday range and 1.618, plus free manual orders matching the paper ticket choices. Homepage/old #market redirect to1.618; Market navigation removed. MarketSync/MarketContext no longer start; retired context/workstation routes410 prevent old tabs restarting collection. Shared monitor, Monday stream, four patrols and execution stay enabled.
- Weekly lines and axis labels use server NY week bounds, including DST; prior-week candles remain accessible without current-week price/scale contamination. VICR actual current-week H210.15/L188.15, outer223.746/174.554 verified.
- Former live tab was disabled with no module. New explicit preview/confirmation supports side, notional, leverage, market/limit and optional fixedTP/SL in existing manual account. Separate durable ticket ledger, same locked worker, no strategy3percent/3R sizing. Unknown submissions only GET-reconcile; existing same-symbol inventory blocks leverage changes. Actual feeGroup matched to instrument; read-only real plan passed. Native limit exits wait for full fill or remainder cancellation.
- Code `1e194fa0ed`; Stitch919dd584e7254fcc894df9399f23861e/GEMINI_3_8_FLASH.404Python+52frontend pass; browser1440/390/320, real chart bounds and intercepted order workflow verified. Zero real free tickets/orders. Both services updated; manual readinessready, Monday streamconnected,4patrolsenabled. Shared scan has4TEST002-USDT-SWAP errors; no active model expected. Temporary browserclosed/PIDexited. Evidence `artifacts/monday-home-live-20261023/receipt.json`. Unrelated stagedREADME deletion and MondayBark edits preserved.

## 2026-10-23 · Case3 hourly wick plus three untouched bars: overall fails, gold exploratory

- Knowledge record https://app.notion.com/p/4028856479af81469da0f0a3173497c2; write/readback confirmed after transient connector recovery.

- Inherited first weekly-side touch fromcase2, no volume condition. Pin body strictly inside1618; next3complete contiguous hourly high/low ranges strictly inside. Enter nextopen after thirdclose(pin+4h); stoporiginalpin/targetnearMondayedge.633symbols10662signals10651closed11censored; Nautilus1.231.0 41820positions83640fills all checked,17focusedtests. `scripts/backtest-monday-hour --workers 3`.
- BTC49 netmean-.29560R vsrandom-.15560R; ETH47-.26860R vsrandom-.09802R. XAU66grossmean+.08766R vsrandom-.26539R; matched grossbp p.00850 is exploratory/unadjusted, absolute meanCI[-6.52,14.80]bp crosses0, brokerfees unknown,2023on only12trades. Group1 combined162grossmean-.08899R,random-.14099R,p.296. No stable net profitability established.
- 630alts10489closed11censored,netwin55.29percent,mean-.05177R vsrandom-.18013R,mean-12.06bp. Matched10203excess+34.94bpCI[21.28,48.06]p.00050 but absolute net negative in both periods(-.01188/-.06120R). Winners average+.748R,losses-1.041R; high winrate is insufficient. One ambiguous signal,stopfirst total-546.14R vsnative-542.97R.
- Existing sources/costs unchanged; spot correctedNY clock and conflict handling reused. Eight no-signal assets retained as zero signals, not omitted universe. Raw ledgers,comparison withcases1/2 andreceipt in `experiments/active/exp-monday-1h-wick-hold-20261023-v1/`. No data downloads, dependencies, browser sessions, live orders or model changes. Training/productionfalse; unrelated stagedREADME deletion preserved.

## 2026-10-23 · Case2 15m volume wick finished; case1 gold clock corrected

- Owner volume rule: pin volume is maximum of itself plus prior20bars, ties allowed. First weekly-side touch, pin body inside,3following closed15m bodies strictly inside, nextopen entry; SLpin extreme/TPnearMondayedge unchanged. Native Nautilus1.231.0 at5m,39870positions79740fills all checked;632crypto11232signals,11225closed7censored. `scripts/backtest-monday-volume --workers 3`.
- BTC47 netmean-.00046R/ETH40-.06557R; combined87-.03039R vsrandom+.03602R, notional+11.64bp but2023on45trades-4.70bp.630alts11138closed7censored netwin44.60percent,netmean-.08565R vsrandom-.17696R,notional-9.35bp. Full matched9463excess+6.27bp CI[-5.01,17.87] p.144; no stable positive edge. Same week controls still inherit weekly trend. Base volume, not screenshot USDT turnover. Existing spot XAU volume all zero: case2 unavailable, not counted as a passing zero-signal test.
- **Correction to prior section:** local XAU archives follow NY wall time (independent UTC5706return alignment .945 vs fixedEST -.004). Prior case1 gold361 and group1 823 figures below are superseded. Corrected gold347 grossmean-.02240R vsrandom-.15332R, later97-.64167R; combined809grossmean-.12701R, paired p.712.1388gold native positions/2776fills. Crypto fills unchanged; old files preserved. Default `scripts/backtest-monday-wick` now uses corrected clock; `--historical-v1` explicitly selects old version.
- Results `experiments/active/exp-monday-15m-volume-rejection-20261023-v1/` and `exp-monday-5m-wick-gold-clock-20261023-v2/`; Notion https://app.notion.com/p/4028856479af81a8a5ebc599e387a7c4 and https://app.notion.com/p/4028856479af814384bedbe1746f03da .16focused tests pass, inputs/code stamps unchanged, no new dependencies/data/browser/live action. Fees currentOKX maker.02/taker.05percent on Binance history; spot net unknown; training/production false. Original unrelated changes preserved.

## 2026-10-23 · Monday5m wick-engulf groups rejected; watermark and click targets delivered

- Owner-confirmed next5m open, exact pin-extreme stop, near Monday edge TP; immediate adjacent engulf, all events. Nautilus1.231.0 reused through `scripts/backtest-monday-wick --workers 3`;177273positions/354546fills, every path audited, unchanged cache rerun verified.
- BTC232/ETH230 netmean-0.784/-0.551R; XAU361 grossmean+0.045R but2023on103trades-0.815R. Group1 combined823grossmean-0.096R; relative random not significant(p.697).630alts43566closed+5censored: win14.89percent,netmean-0.275R vsrandom-0.368R; matched excess+5.50bp[2.99,8.06],p.00050, absolute net still negative in both time segments. No promotion.
- Existing Binance UM5m:226symbols through2026-09-22,406through2026-04-30; currentOKX0.02maker/0.05taker percent scenario. HistData fixedEST, not NY source clock;746conflicting minutes/415five-minute intervals excluded;13affected Monday anchors rejected. Spot costs unknown, gross only. Raw fills, quality receipt and summary in `experiments/active/exp-monday-5m-wick-engulf-20261023-v1/`; Notion https://app.notion.com/p/4028856479af81e884acd6c1559ecfea .
- UI whole-chart symbol/timeframe watermark synced; only tight name-arrow area opensTV, other row areas select chart. Stitch abf5e21f9f8a41ab9b3ddea62b106df4 modelGEMINI_3_8_FLASH.61frontend+77asset/layer+26native/signal tests pass;1440/390/320browser checks and real loaded5m screenshots. Task browser closed;monitor alive,4patrols enabled,final scan ongoing and modelnone expected. Evidence `artifacts/monday-fib-watermark-20261023/receipt.json`.
- Builders f6e75ff12b/491af0dcaf/713a6c4711/1568de6407. No formal report, no new data, no live orders or model changes; original stagedREADME deletion and unrelated dataset edits preserved.

## 2026-10-23 · 区间1.618 methods 16-17: implied vol and level/anchor comparison rejected

- DVOL (Deribit public hourly, 2021-03-24..2026-09) on 470 BTC/ETH touches: calm-regime hypothesis wrong sign (AUC 0.481/0.451), model later AUC 0.523. Post hoc only: realized/implied ratio AUC 0.552/0.554, small sample.
- Same barrier proportions for 1.272/1.414/1.618/2.0/random and Monday NY, Monday UTC, previous-week anchors: Binance alts 2020-2025 (499696 events) and OKX 2025-26 all 28-40 percent; Monday NY 1618 minus rest -0.33pp [-1.5,0.9] and +1.2pp [-1.3,3.6]. The 1618 line is not special. Seventeen methods total; Notion task and knowledge base updated.

## 2026-10-23 · 区间1.618 methods 14-15: Beijing 04-08 and US release windows rejected

- Binance alts 2020-01..2025-05 (503 USDT perps incl. delisted, 12000 vision archives via yoyo/data/binance_um_vision_listing.py): 40289 touches 36.7 percent; Beijing 04-08 36.4 percent (n5447), gap -0.35pp, p .568. The OKX timing pattern does not replicate.
- US CPI/payrolls/FOMC +-2h (536 official events; BLS archive filenames and federalreserve.gov pages saved under exp-monday-1618-macro inputs): frozen "breaks more" fails. Crypto shows the opposite in two time-disjoint sets (Binance alts 42.0 vs 36.5, n1473; OKX 2025-26 50.4 vs 38.7, n335) but it is post hoc; XAU 32.4 vs 36.3 (n71) n.s. Forward observation only. Notion task and two knowledge-base records updated.

## 2026-10-23 · Monday 1.618 full-screen glass workspace

- Owner-requested chart wallpaper now defaults to latest touched contract; Monday H/L and both outer1.618 solid lines, optional internal lines. Compact glass controls; no Telegram module. Manual selection pins the chart; follow resumes latest. Existing backend/subscription untouched.
- Final Stitch screen `fd410c9bd37443dd8759a713c57e2d47`, model GEMINI_3_8_FLASH. Five movable modules use dedicated grips, viewport bounds, separate desktop/mobile position memory, double-click/Home reset. 18percent white tint/18px blur, reduced-motion support.
- Source `957306d137`; 48 frontend +2 asset checks pass. Native browser1280/390/320 verified, actual drag/reload/reset and route isolation; touch pointer logic unit-tested, no physical mobile device claim. Temporary IAB1 closed, viewport reset, monitor scan2008/2008 errors0 and four patrols enabled; older patrol rounds still partial. Evidence `artifacts/monday-fib-wallpaper-20261023/receipt.json`; original dirty work preserved.

## 2026-10-23 · 区间1.618 method 13 order-book depth: rejected, series closed at 13

- Binance bookDepth (touch days only, 2041 archives, 14 missing) for 12 majors 2023-01..2026-09: front/behind notional within 1/2 percent at the last snapshot <= touch-30s. Primary later AUC 0.524 p .091 (early 0.495); model later AUC 0.514, top30 percent 33.5 percent. Evidence `experiments/active/exp-monday-1618-book-20261023-v1/summary.json`; Notion task and knowledge base updated. Thirteen methods across price, structure, confirmation, breadth, volume profile, retest, small bounces, gold history, session, positioning/flow and order book all sit at driftless odds.

## 2026-10-23 · 区间1.618 methods 6-12 (owner "想办法", goal mode): all rejected

- Positioning/flow (Binance 12 majors 2212 touches; OI, long/short, taker, funding via yoyo/data/binance_um_vision_fetch.py, 19068 archives, inputs local) later AUC 0.514, OI-build hypothesis p .19. Volume-profile/swing confluence AUC 0.512. Retest after 0.236R pullback 37.7% (3497). 1m small bounces 0.1/0.2/0.3R = driftless (78.7/64.6/55.8 vs 79.3/65.6/56.0). XAU spot 2011-2026 685 touches 35.9%, hold and break both n.s. Thin sessions slightly worse; post-hoc 20-24 UTC 47.5% held 41.4% on Binance OOS (n=111, p .27), forward observation only.
- Twelve methods total: first weekly 1618 behaves like a driftless barrier; not judgeable with price, structure, flow, session or gold history. Notion task updated (当前阶段/下一步), 13 knowledge-base records in Spike · 研究实验. Experiments `exp-monday-1618-{flow,confluence,second-touch,reaction-curve,xau-spot,session,hour-oos}-20261023-v1`; validity touch() gained opt-in monday_min_bars (default unchanged). No Pine/default/live change.

## 2026-10-23 · Notion 区间1.618测试 completed: validity not judgeable from price

- Owner TradingView AlGc61US read-only: BTC 32 + XAU 8 positions, entries at weekly 1618 (BTC 30/32, XAU 8/8 within 0.03R), mostly first-touch 4h bar; BTC stops about 2.0 ext (median 1.89/-0.99), XAU 1.75/-0.72. All 40 TP-first with hindsight targets; drawn 78%/100% vs undrawn 19%/0% objectively valid = selection, not edge. Chart restored to BTC 4h.
- 177 OKX swaps 10130 first touches: edge-before-2.0 validity 39.1% vs driftless 38.2%, mean +0.03R fee0. Touch-time logistic later AUC 0.520; post-touch states only match driftless odds of nearer targets (63.6 vs 63.1); BTC 49% discovery fails Binance 1m 2022-11..2025-05 (31.5%, -0.18R); breadth+BTC context+LightGBM later AUC 0.516. All pre-registered gates fail. Same-week random controls are trend-biased (-0.24R), not an absolute null.
- Experiments `exp-monday-1618-{owner-drawings,validity,post-touch,oos-1m,breadth-gbm}-20261023-v1` summary.json; builders 96a6d871e9..cffd3a9a86; learnings confirmation-win-rate-must-be-compared-with-driftless-barrier-odds, same-week-random-controls-inherit-the-weeks-trend, hand-drawn-chart-positions-are-hindsight-labels. No Pine/default/live change; training/production false.

## 2026-10-23 · Spike App 0.8.19 — full-screen 1.618 and shared Apple Glass

- Default central 1.618 tab uses the existing NY Monday monitor's latest touched USDT perpetual; native Lightweight Charts supports dragging/scaling, Monday band, solid Monday high/low and extension lines without level labels. Market-only public projection, validated week/freshness; no trading/account fields exposed.
- Quick entry reuses the contract ticket and server 1R, including existing-position protection semantics. No order sent during development or acceptance. Unified #0071E3 controls and glass material across contract/rules/fib/positions/statistics/settings; finger-following navigation, axis lock, edge resistance and reduced-motion support; charts and controls excluded.
- Stitch designs and actual HTML retained under `yoyo/discipline_app/design/fib-home-20261022/` and `design/apple-glass-20261023/` (final motion screen9cd418117dd24ff0802728fdeb596a18).192 Python/55 frontend tests pass;320/390/1280 browser checks, touch input,0 synthetic new orders. iOS/Android builds and signatures verified. iPhone unavailable: package0.8.19(28) is not installed.
- Service0.8.19 is running; existing1 position remains exchange-verified,pending0,blockers empty. Monitor scan2008/2008 errors0 and4 patrols enabled; no active model expected. Test fixturePID60064/port18922 closed,temporary Chrome tab closed; real preview at127.0.0.1:5199 retained. Build receipt `yoyo/discipline_app/releases/build-0.8.19.json`; other sessions' dirty changes preserved.

## 2026-10-23 · Notion 区间1.618测试 #1 two-wick engulfing rejected

- Owner Notion task https://app.notion.com/p/nick-wiki/1-618-4018856479af8096aa78c08edd94f6e2 rule1: after first weekly 1618 touch, two completed15m wicks reach the key with open/close inside, then first later reversal engulfing enters at close. Only admission changes versus v8; 0.7percent SL, four25percent targets, any-TP breakeven and fee0 unchanged.
- BTC OKX two years: 90 touched sides, 66 entries, 7 wins 10.61percent, -26.22R, mean-27.81bp, PF.556. Matched random 14.09percent, -18.18bp, p.690; v8 body 18.31percent, -11.96bp, per-opportunity -20.39 vs -9.43bp p.746. Both halves and both sides negative. Median wait 367min; 25/66 engulf closes still beyond1618 (post hoc only).
- Nautilus1.231.0 2904 legs 5808 fills, all oracle checks, 66 independent wick/engulf rebuilds, 726 cash checks; 39 tests. Builders b06bbf29cd/5ebcda4694; `experiments/active/exp-btc-monday-fib-wick-engulf-20261023-v1/rule_comparison.json`. Notion task status 进行中, sub-task line checked; knowledge-base EXP record not created (no API in session). No Pine/default/live change.

## 2026-10-22 · Monday 1.618 monitor — Apple Glass delivered

- Update ca7aff1d6b: Owner requested hidden untouched rows, emphasis on new touches and click-to-expand per-contract K lines. Stitch20fd581767514b34af5fb3efe2d42e6d source retained. Occurrence-time descending sort; live-only15minute display highlight, historical recovery excluded. Existing15m closed-candle API and renderer reused with9 monitor-anchored levels and Monday band; one open chart, stable DOM/zoom, request cancellation and symbol/week checks.44 tests pass; desktop1280 and mobile390 verified onBTC/ETH/SNDK. A real MRVL touch arrived and moved to top while SNDK chart stayed open. No backend/TG/trading changes; prior validation below remains historical.
- Owner-approved Apple Glass screen `07032a6d28ba421790a667c5dc50eccc` recovered from browser DOM and direct Stitch get_screen. The list API omitted the completed screen; prior absence conclusion was incorrect. Actual HTML/CSS preserved in artifacts/monday-fib-monitor-20261022/stitch-apple.html; rejected Obsidian source remains unused. No duplicate generation or design waiver.
- Frontend a473791648 adds the menu, lazy assets, glass tokens, locally bundled source font, searchable/paginated full contract list, state filters, Monday H/L details, quote freshness, touch receipts and independent TG switch. API and monitoring semantics unchanged; no backtest or trading changes.
- 37 frontend tests and 2 static-asset tests pass. Desktop1280x1000 and phone390x844 verified with no horizontal overflow or console errors; search/empty/missing-history/details/pagination and route style isolation checked. Screenshots and frontend_smoke.json retain evidence. User-requested final IAB tab retained; no temporary browser process remains. The existing Owner Stitch Chrome tab was not closed.
- Live snapshot507 contracts,494 full Monday anchors,13 missing full Monday. Original scanner alive/errors0; four patrol plans enabled at15/15/240/1440minutes with current worker heartbeat. The older4h/day rounds remain partial, not claimed fully complete. Subscription was off initially, then activation at1792682048630 occurred outside agent actions; current on state preserved. No real test message or agent subscription write. API records natural live notifications separately.
- Receipt and registry updated; stitch-1-618 follow-up PAUSED, implementation complete. Shared main workspace and other sessions' dirty edits preserved. No formal report generated.

## 2026-10-22 · Monday 1.618 first-touch win rate and failure attribution

- Owner image short84375.8→TP8081923.8→TP2081166 confirms deep-side0.618 target; NYMonday H83149.8/L81166 matchesOKX exactly. Initial provisional Binance result13.48percent preserved separately; screenshot-matched OKX15.73percent is primary.
- Two years2024-10-22to2026-10-22Shanghai,fee0,firsttouchperside/week,0.7percentstop,80/20targets,noBE;90fills89closed14wins75losses,-19.47R,oneopen. Long19.57percent/short11.63percent;matchedrandom12percent,p.329. Four partial-then-runner-stop trades remain winners;tenreachboth targets.
- Of75full losses,32had>=1R MFE,14>=2R;firsttargetmedian5.02R/3.514percent. Only2stopentryminute,8within5minutes;median154minutes.30entry-minute bodiescloseoutside. Poststop24h6/75 and7day33/75 reachTP80;future descriptions are not entry filters.
- Owner approved single-variable1/1.4/2percentSL. Win23.60/29.21/37.08percent butsame-notionalmean-8.38/-9.91/-6.89bp,allfull-periodnegative versusoriginal-15.32bp.2percentrescues19oldlosses;early-27.61/later+12.48bp. No default promotion.
- Nautilus1.231.0,OKX7920positions15840fills,allnative/oraclepaths plus960unfilledlegs;104tests,8prefixes,900matches and990parentcashchecks. OHLC/OLHC/stopfirst strategy resultsidentical. Original36.10RcashDD,19lossclosingstreak;notfundedaccount. Existing1marchives,onlymissingtail,60overlap,nohash. Sourceb01c8cac9a/8962da9d64/abdf76e830. Receipts in both `exp-btc-monday-fib1618[-okx]-20261022-v1` directories;rawfills andfailedpreflightcontext retained. Notion https://app.notion.com/p/4018856479af81c3b258c4ff043d7492 . No report/Pine/default/model/live changes.

## 2026-10-21 · V2.31 entry directions 2 and 3 completed, no upgrade

- Owner selected confirmation-then-retest and confirmed1H structure as separate studies on the original five-year BTC/ETH V2.31 baseline, not sweep+MTF. Original exact-wick stop, confirmed15m2/2 close exit and20bp unchanged.
- Retest: fixed30minutes at original key after full admission. BTC293/485 andETH221/385 fills; net-1226.63/-594.09 new-riskR, each-filled-24.42/-20.87bp versus original-23.44/-19.86bp. Missed79/104 and72/93 original winners. Per-original-opportunity-14.75/-11.98bp versus same-protocol random-11.09/-14.94bp; original-risk bridge-402.28/-201.94R. Different sizing measures are not funded-account PnL. All ten symbol-years netR negative.
- H1 confirmed2/2 highs/lows: aligned/counter/mixed six full-period cells netnegative. ETH aligned109trades-82.47R,mean-4.16bp;earlier+4.46/later-14.71bp. Across both studies eight excess tests none passesHolm p<.01;bestETHalignedp.4776. Mixed structure is not automatically a trading range.
- Native2036positions4072fills and every path audited;1740 original paths reused for H1,16prefix checks;35native+8feature/accounting+72boundary tests pass. Fixed limit-entry same-bar protection before research. First audit ETH floating-tick equality failure preserved;corrected audit reused all native fills without replay. Builders9c3126c65d/130d2e272e. No fresh data,Pine/default/live/model changes or report;training/productionfalse.
- Evidence: `experiments/active/exp-range-v231-retest-20261021-v1/summary.json` and `exp-range-v231-h1structure-20261021-v1/summary.json`;both delivery_receipt.json inventory local raw results. Notion https://app.notion.com/p/4008856479af8121b098c7973488d026 and https://app.notion.com/p/4008856479af8149ab51c83cbe421561,readback verified. Original hypothesis LRN-25 linked to both failures;space/cost direction1 remains unexecuted.

## 2026-10-21 · BTC chart-opportunity correction and Monday endpoints

- Owner clarified “no losses” means favorable space after the signal. Prior10.15percent was fixed10R realized net win rate and answered a different question. Added Monday high/low; both directions and latest3completed15m stop unchanged.
- Read all7 manual TradingView positions. At0.2percent buffer all7 had >20bp before initialSL; at0.1percent6/7. Two Oct1 Monday-high shorts were missing in v1; Oct20 evening entry advances23:00 to22:45 Beijing. Manual drawing prices and causal-close prices retained separately.
- September1 through October21 midnight Beijing:69 signals,42/69 (60.87percent) cover20bp versus44/69 matchedrandom. At least1R31/69 versus36/69;2R22/69 versus20/69. Conservative excludes stop-touch5m;20bp count unchanged at upper bound. Seven examples are not the full cohort, and MFE is not an executable exit.
- Two years981signals (+173;17earlier entries), eight fixed-exit cells still netnegative;0.2percent/10R net-449.79R versus random-431.98R, win9.58percent. Reused8024 execution rows; only7672 new native paths, all oracle parity passed;11tests,14prefixes,14drawing-buffer rows; stop-first changes no signal payoff.
- Evidence `experiments/active/exp-btc-range-wick-fanshen-20261021-v2/delivery_receipt.json`; code51fc52ef7b/667d45104f. Notion EXP-151 https://app.notion.com/p/3ff8856479af810aa605d0cc9500cb96, task65Done; v1 preserved. No report requested. No Pine/default/live/model changes, training/productionfalse. Existing TradingView used, no temporary tabs/sessions created, properties dialogs canceled.

## 2026-10-21 · BTC Fib wick / 15m check-cross study completed

- Owner task65 confirmed both directions and latest3completed15m extrema at entry. Two years to Beijing2026-10-21; causal pre-touch Monday levels, first close with both touch and arrow known. Eight0.1/0.2percent buffer by2/3/5/10R targets all netnegative after20bp.
- First-year and full descriptive choice0.2percent/10R:808trades net-323.13R versus matchedrandom-442.82R; later371 net-118.22R versus-221.09R, excessp.1247. Pure-arrow2753trades mean-17.32bp versus combined-16.26bp, not proof of filtering edge. Both directions lose.
- Only0.618/0.2percent/10R is full-period positive among48ratio/exit cells:205trades+32.23R versusrandom-57.05R; first/later+9.57/+22.65R. Net95percentCI[-78.64,+144.77]R and Holm48p1.0, retain as unvalidated observation, not a promoted strategy.
- Reused Nautilus1.231.0:56976positions113952fills, all native/oracle identities passed,14244stop-extrema checks,808event/3561matching audits,14prefix checks; initial86tests and final7candidate tests passed. Timestamp-unit empty preparation preserved and fixed before native replay. Commits dda785c122/9e6e41b29d/f99765323d; no unchanged replays.
- Evidence `experiments/active/exp-btc-range-wick-fanshen-20261021-v1/delivery_receipt.json`; Notion EXP-149 https://app.notion.com/p/3ff8856479af817cb72dd5896e7896bd. IndependentR, not account returns; no funding/slippage/capacity. No Pine/default/live/model change; training/productionfalse. No temporary browser/tab created.

## 2026-10-21 · V2.13 delivered; all loaded Monday history retained

- Owner removed the 12-week limit. New source `yoyo/evaluation/pine/monday_range_v2_13.pine` at `36493d4000` retains all loaded weekly records and redraws the visible period. Shared line/label budgets prioritize boundaries; overflowing viewports show a zoom-in hint without deleting data. V2.12 source/private preserved.
- BTC15m native retains 18/18 weeks from June22; July1 view shows June29 range. Main-chart history availability remains separate. Common October1–18UTC 72 signals and both signatures unchanged. Daily retains357/357; wide view draws178/289 without GC, then pan to2019 restores28/28 weeks and140 historical Fib lines fromDecember23. Two source-scope checks pass; trading engine unchanged.
- Private V2.13 revision4 saved01:10:15Shanghai with1146-line clean source; QA removed, BTC15m restored and layout saved, editor/logs closed. No temporary browser/tab or orders. Receipt `output/pine/monday_range_v2_13_delivery.json`; learning `docs/learnings/pine-history-retention-is-separate-from-drawing-retention.md`. No signal-quality or profitability claim.

## 2026-10-21 · Both existing execution sessions recovered after reboot

- Mac reboot at 00:41:20 Asia/Shanghai removed the two session-only jobs; website, monitor and tunnel started normally. Owner approved signal recovery with unchanged parameters, then requested recovery of the separate manual-account service. Original private plists were bootstrapped; signal PID 5467 and manual PID 6810 both have PPID 1, runtime locks and advancing heartbeats. No login autostart was installed.
- Signal GET-only preflight verified 14 owned positions and 14 stops. Normal scan catch-up cleared OL/POPCAT stale checkpoints without changing gates; errors=[], worker_error=null and accepting_entries=true observed. Manual preflight verified distinct account identity, 2 owned lots/2 OCO protections, 0 unconsumed confirmations and 0 pending management commands. Readiness is ready; the Owner browser displays “可预览订单” and verified COMP/TRUTH TP/SL. External SOL/ZEC remain owner-managed; manual trade count and economic/state fields remained unchanged.
- Original configurations, cutoffs and per-order manual confirmation preserved. Private backups/receipts: `OKXExecution/recovery-20261021-reboot-1792515039/` and `OKXManualExecution/recovery-20261021-reboot-1792515302/`. YOLO list timeout no longer reproduced: browser auto-updated 2829→2832 findings, error empty; public API 200 in 1.90/3.23s. Four original patrols enabled at 15/15/240/1440min. Exact transient timeout cause unproven; no UI/code changes or temporary browser sessions.

## 2026-10-21 · V2.12 delivered; historical cutoff and CE10246 resolved

- Source commit `e241800e2a`, unchanged trade/signal semantics; both key-level15m requests fixed at12000.20 local tests already pass. BTC15m native old-off3199 bars starts September17 17:00,new-off/on11999 bars starts June18 01:00;retained Monday weeks5→12,first September22→August4. Common October1–18UTC72 signals have identical direction/time/price signatures.5m confirmed request also works,key context June18 while chart data starts September14.
- Owner-created private V2.12 revision1 had only90 lines,causing CE10246. Full1088-line source restored;final clean revision6 saved00:52:05Shanghai,QA removed. BTC15m displaysV2.12,reference defaultoff,V2.11 source/private preserved and old chart instance hidden. Layout saved;editor/logs closed;no browser sessions/tabs or orders. One accidental code-text drawing created during focus recovery was removed;existing manual drawings preserved.
- Receipt `output/pine/monday_range_v2_12_delivery.json` now delivered/native validated;raw AX/screenshots retained locally. Requests do not override platform available-history limits. No repeated unsupported-period native test;history-only change is covered by existing local routing regressions. No new signal/profitability/production claim.

## 2026-10-20 ETH timed BE / RSI scaleouts — completed, no default promotion

- Owner Notion ideas tested in `exp-eth-timebe-rsi-20261020-v1`: frozen 3838 ETH signals + 3838 matched controls; same-source 1m execution. All five alternatives worsen full net R: original -3747.09; time25 -4325.56; first RSI3 -4053.96; first RSI5 -3898.26; combined3 -4269.69; combined5 -4306.25. Time BE waits after fifth 5m close until fee protection is feasible; only profitable opposite strong diamonds reduce original fifths.
- Owner follow-up `exp-eth-timebe-rsi-20261020-v2` skips the first opposite confirmed diamond before the profit gate. Second RSI3 -3954.09 and second RSI5 -3849.59 improve first-diamond sample net by 99.87/48.68R, still worse than original by 207.00/102.50R. Second5m has 39 total gross20R outcomes, but only35 of the original45 retained plus4 new; old tail gross retention75.3percent. Matched random -3796.05/-3648.74R; no proven edge. No Pine/default/live changes.
- Reused six v1 arms for follow-up, two new native arms only. V1 46056 positions/101973 fills/431.48s; V2 new15352/35806/229.81s; all native parity,139 tests. Core builder54bcdfa794, extensionb485d48683, analysis39ebf73734. Use cached artifacts; run modules `range_rsi_scaleout` / `range_rsi_second`. Delivery receipts inventory raw local data without hashes.
- Win-rate trap: time BE strict positive rate34.47percent includes1196 trades below0.01R; total net is worse. Independent R is not account PnL;20bp unchanged, no funding/capacity/slippage. Known historical data ends2026-09-22, October19 example not replayed. Yearly boundaries unchanged.
- Notion V1 https://app.notion.com/p/3ff8856479af8130b5bfe8894293287d and V2 https://app.notion.com/p/3ff8856479af8113b997da8c13d5d655 readback verified and linked from owner's ideas. Scoped registries pass; global registry still has pre-existing missing source_commit on spike-v8-total2-1h-native-20260914-delivery.

## 2026-10-20 ETH fixed-risk exit search — completed, no default promotion

- `exp-eth-exit-search-20261020-v1`:3838ETH15m-watch5m signals plus3838matched controls;20bp unchanged. Owner selected fixed risk and cumulative netR.3520planned configurations allnegative,best−533.75R;39separate boundary probes(37new) highest+441.97R versusmatchedrandom+470.46R,p=.537. No configuration positive inall2024/2025/2026 segments.
- Highest descriptive parameters:last3completed5m extrema plus1.2percent buffer;15mclose20R arms10ATR14;noBE;armedH1oppositeexit.68forcedyear/data-endclosures contribute387.06R;natural54.91R. Cash-onlydrawdown881.99R,max119.09days,69simultaneous signal positions. Not a fundedaccount or established entry edge;noPine/default/livechange.
- Reused C++ screen41.43seconds,selected5native configurations38380paths allparity.127tests. Fixed-target simultaneousquoteinterference reproduced andfixedthroughnativeFillModel;failurelogretained. Commits74a855b456/59c5cca285/7bfc6b84de/188fcbc0da. Rawresults inventoried in delivery_receipt.json;reuse cached runs.
- Notion https://app.notion.com/p/3ff8856479af81d698ead0e77d9f20db readbackverified;registriesupdated. Single-factor buffer improvementmostlylowercostR;earlyBE raiseswinbutreducesnet. No fresh forward validation.

## 2026-10-20 ETH close-confirmed partial exits — completed, default rejected

- `exp-eth-5m-partial-close-20261020-v1`: same3838ETH15m-watch5m entries plus3838matched controls;first5m-close1/3/5R reduces25/50percent once. Original stop/runner/cost unchanged, no extra BE.
- All6 full-period netR/bp/realizedR drawdown worse. Baseline−3747.09R,leastbad partial5R25−3857.07R;whole-position20R45→31.3R50 netwin9.56→17.43percent butextra loss250.31R.2024-selected5R50 laterextra loss264.01R. Do not upgrade Pine/default from this result.
- Source396ab2636,analysis555d916936;6native arms393.15s,cachehit,46056positions103828fills,50baseline probes,35behavioral72boundarytests. Allnative accounting/oracle/runner checks pass;raw ledgers and delivery_receipt.json retained. IndependentR,20bp,knownhistory,not account returns or unseen validation.
- Notion: https://app.notion.com/p/3ff8856479af811bb9abe0b999d12616 (readback verified). Registry rows updated;no new market download/orders/Pine changes. Reuse cached results.

## 2026-10-20 · V2.11 delivered: 15m observation for5m entries and15m rays

Owner confirmed5m chart uses15m watch,15m chart keepsH1 watch. Every later same-direction chart signal independently enters; no body filter. All-chart rays now confirmed15m,greenup/red down,defaulton. Stops,15m5R4ATR andarmedH1reverseexit unchanged;reference defaultoff. Private`周一高低点区间＋翻身 · V2.11` revision3 saved19:54:09Shanghai;V2.10source/private preserved.17tests;native1m/5m/15m/1H/4H shared30rays19bull/11bear signatures identical;15m18closed signatures matchV2.10;unsupported charts0frames. FinalQA removed,PEPE5m moduleon,8visibleframes,layoutsaved,editor/logsclosed. Code d0e3d9a380;receipt`output/pine/monday_range_v2_11_delivery.json`;Notion system page updated/readback verified. Noorders orprofitability claim;training/productionfalse.

## 2026-10-20 · ETH15m watch /5m signal-body-only filter rejected

Owner authorized same-direction signalK,enter atsame5m close. `exp-eth-5m-signal-body-20261020-v1` retains2211/3838trades;netwin10.76%,net-1739.94R,meanR-0.787 butmeanbp worsens-16.785to-16.961;gross10R/20R116/45to65/24.2024/2025bpimprove,2026worsens;2025plus-17.377versus-16.525baseline.1679rematchedpairs(75.94%) signal/random meanbp-17.796/-20.141,p.341;no stable advantage. Reused7676Nautilus rows;4128savedrows35fields exact,3tests,5partitionchecks;2.36s,0replays/downloads. Code479af940cd;receipt/delivery_receipt.json andrawlocaldecisions retained. Notion https://app.notion.com/p/3ff8856479af814e80a7d115f494f240 verified. NoPine/default/livechange;training/productionfalse.

## 2026-10-20 · ETH15m watch /5m two-body confirmation tested, no Pine change

Owner requests test first. `exp-eth-5m-double-confirm-20261020-v1` reuses Nautilus/data: H1/15m/15m-double4101/3838/1189trades,net-4041.40/-3747.09/-596.14R,mean-17.395/-16.785/-14.783bp;finalgross20R53/45/8. Double raiseswin9.56to13.12percent butall3yearsnegative,2026worsens;matchedrandommeanR-0.446beatssignal-0.501. Old45twentyR:25rejected,20passedbutonly7still20R,plus1new. Confirmatnextclosewithactual5barstop;new15mwatchcancels pending.15m5R4ATR andarmedH1reverseexit unchanged.22tests,6prefixes,10054nativepositions/20108fills,34scalarchecks;70secondsnativewall,baselinecache reused. Sourcef85b495ddb,analysis2d3cb43367;delivery_receipt.json bindsrawlocalledgers. Notion https://app.notion.com/p/3ff8856479af81918d81dd385fab4f80 verified. No indicator/default/livechange;V2.10 remains delivered version.

## 2026-10-20 · V2.10 delivered: chart-selected5m/15m entries

Owner removed the V2.9 entry-period selector:5m chart uses5m entry arrows;15m chart uses15m;allotherperiods showconfirmationcontext only. Delivered private`周一高低点区间＋翻身 · V2.10`,revision3,source`yoyo/evaluation/pine/monday_range_v2_10.pine` at2e2e2ca8a3. Reference switch defaultoff;currentPEPE15m on. H1watch,5m structure stops,15m5R4ATR andarmedH1reverseexit unchanged.16tests;native15m18closed/5m112closed signatures match prior respective modes;1H/4H engineoff,0frames. FinalQA removed,layout saved,editor/logs closed,no browser created/noorders. Receipt`output/pine/monday_range_v2_10_delivery.json`;Notion project updated/verified. OldV2.9source/privatepreserved. ETH fixed5m rejection research below remains separate and completed.

## 2026-10-20 · ETH fixed5m rejection study completed

Owner freezes research entries to5m; higher timeframes confirm only. Reuse `exp-eth-range-entry5m-20261020-v1` native4101ETH signals, same stops/15m exits/20bp costs. New `experiments/active/exp-eth-5m-rejections-20261020-v1`:10single vetoes all netnegative.2024-selected density filter fails later (-16.290vs-16.284bp). Rejecting risk<0.2% deletes35/53gross20R and worsens mean;watchordinal>=7 contains31/53gross20R. NotoutsideMonday improvesmean -17.395to-14.277bp butdeletes13/53gross20R;later gainHolmp=.170,notvalidated.16tests,3prefixes,40scalarchecks,8202native35fields preserved;23.51s,no replay/download. Notion https://app.notion.com/p/3ff8856479af81b09877d3d1c2c8ba9c verified. NoPine/livechange fromthisstudy.

Latest separate UI instruction: removeV2.9 entry selector;5m/15m charts automatically use own entryclock, allothercharts showcontext only. V2.10 implementation/TVvalidation inprogress. This display request doesnot rewrite the frozen5m ETH research.

## 2026-10-20 · ETH range-location and4H admission research complete

- `exp-eth-range-context-20261020-v1`:15predefined arms plus8explicit exploratory unions, only admission changes. Reused frozen OKX ETH5m entry/15m exit ledger; no native replay/download. Common4068trades after33missing-context exclusions; original4101included122tenR/53twentyR,common121/53.
- Baseline mean-17.640bp. H4alignment2478trades,-17.733bp,33twentyR;dayedgeANDH4 1452,-15.473bp,23twentyR. ExploratoryMondayedgeORH4momentum3229,-17.576bp,42twentyR:79%trades and79%twentyR retained. All22filters full netnegative; no upgrade.
- Frozen2024selection H4alignment fails2025+mean(-17.263bp vs-16.284baseline),keeps22/41twentyR. Daybreakout2025positive12.636bp but2026negative28.812bp. No stable filter claimed; only simple high/low position and4HStoch tested,not POC/reclaim/MA compression.
- Evidence `experiments/active/exp-eth-range-context-20261020-v1/delivery_receipt.json`;sourcea29f37ddee,supplementb78177f5bf.11tests,5prefix checks,115summaries,36922rematchedpairs,8202native rows34fields preserved;36.14s batch analysis. Notion3ff8856479af81a8b88aca8636f16000 readback verified. Raw outputs local;noPine/live/training change. Luna runtime metadata unavailable,delegate stopped and root completed.

## 2026-10-20 · Range 5m ATR management comparison complete

- Experiment `exp-range-management5m-20261020-v1`: 8 native runs, old15m ledgers reused. Primary0.05% net R worsens for all four coins; gross20R205→106. Secondary0.2% BTC full improves26.75R, but all16 coin/buffer comparisons in2025/2026 worsen. No default upgrade.
- Same H1 watch/5m entries/initial risk/fees; only5m activation/update and actual5mATR14 changed. 18tests,73036positions,146072fills,117bounded scalar checks pass;675.84s native wall. No Pine/live changes.
- Evidence: `experiments/active/exp-range-management5m-20261020-v1/receipt.json`. Notion `3ff8856479af819c8a12f7502d4e8ade` verified. Source82e2862d9c, analysisa94cb18c09. Independent R,not account return;all24newyearly groups negative.

## 2026-10-20 — 四币5m参数小范围研究完成，仍未找到净盈利配置

- 实验 `exp-range-5m-params-20261020-v1` 冻结13组单变量，只改5m止损窗口、价格缓冲、启动R或15m跟踪ATR倍数中的一项。复用原候选/随机/行情与20bp成本；2024选择、2025/2026再检验，非未见数据。
- 四币都选0.2%缓冲，保留5根、5R、4ATR。后段原版→候选每笔净R：BTC−1.355→−0.547，ETH−0.937→−0.396，PEPE−0.697→−0.354，ZEC−0.758→−0.519；候选匹配随机−0.643/−0.506/−0.484/−0.354。52个全期和156个年份组均负，不升级默认参数。
- 改善主要是初始风险变宽使费用R下降，费率未改。ZEC同笔数固定1000USDT名义金额后段反而恶化493.76USDT；R改善不等于同仓位金额改善。无账户收益或最优全局参数声明。
- 源8609ed6f74、审计fad3af01bf；24测试、728标量路径、36518缓存原版及36518新原生持仓/73036成交逐笔匹配；474734路径结构/经济恒等式、260汇总核验通过。筛选61.59秒，复用现有加速器后原生复核优选；全部逐笔留本机实验目录，回执登记路径/大小。
- Notion <https://app.notion.com/p/3ff8856479af81b4962fde7394d2abf1> 已完成并读回；`receipt.json` 为复现入口。V2.9及生产默认未改，training/production=false。Luna max返回不可核验已停止，主线程完成。

## 2026-10-20 — 区间翻身 V2.9 支持可选 5m 入场，已保存 TradingView

- 在 V2.8 基础上新增入场周期 15/5 分钟选项；新实例默认 15m，交易参考总开关默认关闭。每个严格晚于 H1 观察的同向信号独立入场，原 5m 结构止损与 15m 的 5R/4ATR 跟踪、启动后 H1 反向退出不变。
- 新私有脚本“周一高低点区间＋翻身 · V2.9”已保存，V2.8 源码和私有脚本保留；当前 OKX PEPE 15m 图已开启交易参考、选择 5m 入场并保存布局，23/23 可见交易框。未创建临时浏览器或执行订单。
- 18 项本地测试通过；原生固定用例验证观察、入场、管理时钟。5m/15m/1H 图共同已平仓区间的 112 笔记录签名一致，其中 79 笔为非 15m 边界入场；5m 图可用历史较短，总笔数不可直接比较。正式脚本无验收附录，不构成盈利结论。
- 源码提交 fbb42f1484；交付回执 `output/pine/monday_range_v2_9_delivery.json`；Notion 系统页已追加唯一键 `monday-range-fanshen-v2-9-20261020-delivery` 并读回验证。训练与生产资格均 false。

## 2026-10-20 — 四币5m入场单变量检验完成

- Owner确认四币仅15m→5m入场，H1观察、5根5m止损＋0.05%缓冲、15m的5R/4ATR管理及3R保本对照保持不变。实验`exp-range-entry5m-20261020-v1`；复用前版连续5m，无下载或参数调优。
- 2024-08-02至2026-09-22 14UTC；BTC/ETH/PEPE为OKX、ZEC为Binance。原版每笔净R由15m的−0.868/−0.656/−0.365/−0.331变成5m的−1.389/−0.985/−0.702/−0.764；5m笔数5169/4101/4574/4415。原版总净−7179.533/−4041.401/−3212.063/−3371.851R，匹配随机−7232.484/−4537.503/−3540.445/−2743.438R。
- 3R保本仅ZEC少亏27.252R，其余恶化；四币两方案均不如各自15m每笔净R，24分段全部负，拒绝默认升级。最终毛20R仍有52/53/53/47笔；PEPE最大浮盈509.692R、最终净189.097R，逐K核对；大赢家不代表整体盈利。成本固定20bp，独立累计R不是账户收益。
- 源f3b7a69c99先提交后运行，6测试、73036原生持仓/146072成交及独立候选/成本/结构止损/管理时钟检查通过。全部跟踪与保本仍只在15m收盘更新；候选ATR前向可见值与退出事件分离。首轮984.63秒，结果缓存；复现入口`python -m yoyo.evaluation.range_multicoin --config experiments/active/exp-range-entry5m-20261020-v1/config.json`使用隔离Nautilus运行时。
- 逐笔、成交、64行15m对照、周块推断、来源及验收保留实验目录。Notion新版本<https://app.notion.com/p/3ff8856479af817d8472dcadfc2be532>已写入并验证；前轮ZEC同步亦恢复，原失败记录保留并追加成功回执。无Pine、UI或生产资格修改。

## 2026-10-20 — 区间翻身加入 ZEC 同期补充

- `exp-range-multicoin-20261020-v2`：只新增ZEC，原BTC/ETH/PEPE三组均缓存命中，原48行摘要完全一致。入口`python -m yoyo.evaluation.range_multicoin --config experiments/active/exp-range-multicoin-20261020-v2/config.json`（使用隔离Nautilus运行时）。
- ZEC采用明确标注的Binance数据；OKX public instruments确认该合约2025-11-06上市，覆盖不了2024-08-02起的原共同区间。已向Owner说明来源差异，不得冒充OKX结果。225384根连续5m含预热，止于2026-09-22 14UTC；无行情下载或内容哈希。
- 1330笔，原版净−439.591R／3R保本−436.780R，随机−597.694R／−480.882R，周块名义p.238／.417。2026段+4.436R／+5.296R但全期负；不是账户收益，不升级参数。原版46笔毛10R、7笔毛20R、最大浮盈60.150R（逐K核对）。
- 源3789c275f5；3测试、5320原生持仓／10640成交的费用及收益恒等式通过。ZEC两组34.58秒，总流程61.40秒。原始逐笔、四币汇总、分段及验收均在实验目录。
- Notion连接连续3次HTTP传输失败，尚未同步；目标原跨币种研究页`3ff88564-79af-81fa-8e43-dd40dbdcbe52`，待同步唯一键`exp-range-multicoin-20261020-v2`。本地validation.json保留状态。无Pine、执行服务或生产资格修改。

## 2026-10-20 — Nautilus 原生交互预览与 BTC／ETH／PEPE 同期复测

- 原生PEPE K线成交图：`experiments/active/exp-range-multicoin-20261020-v1/preview/pepe-native.html`，16笔／32成交、4105根5m；保留原生缩放滑块，悬停开平仓／净R／最大浮盈R。静态PNG已检查；浏览器工具禁止file协议，所以未宣称浏览器交互验收。
- 共同区间2024-08-02至2026-09-22 14UTC，只用已确认月份及API延续；基线／BE3净R：BTC1373笔−1191.90／−1291.70；ETH986笔−647.17／−603.63；PEPE1231笔−449.48／−442.83。18个币种×规则×时间段全部净亏；不是账户收益。
- 同币同周同方向因果波动桶随机基线分别−1803.46／−754.05／−1001.63R；相对优势含止损距离导致的费用R差，不等于全部预测优势。20R赢家存在，不能据此升级；平均费用BTC1.067R、ETH0.734R、PEPE0.465R。
- 首次6组185.44秒、14360原生独立持仓／28720成交；新增2项测试、全账本经济恒等式及最大MFE逐K抽查通过。复用BTC缓存命令0.76秒含启动；无行情下载／依赖安装／Pine或引擎改动。
- 源f935e1ac4d；输入来源、逐笔、分段、随机对照和验收留在该实验目录。Notion：<https://app.notion.com/p/3ff8856479af81fa8e43dd40dbdcbe52>。生产／训练资格均false。

## 2026-10-20 — 区间翻身已接入 Nautilus 固定回测入口

- `scripts/backtest-range` 使用已安装隔离 NautilusTrader 1.231.0；配置 `experiments/active/exp-range-nautilus-20261020-v1/config.json`，默认复用数据与已完成结果缓存，仅输出小摘要。
- 原规则／3R保本各3,775笔（1,889信号＋1,886随机），合计7,550笔的23字段与旧账本一致，15,100次原生成交；8行为测试、72层间检查通过。首次两组51.51秒，缓存命令0.815秒含启动。
- 原生HEDGING保留同币多空独立；历史报价精度1e-10与策略止损1e-9分开。研究整数委托避免小数尾仓，收益按原风险归一；固定盘中TP尚未适配，不是资金账户收益或策略盈利确认。
- 源`0278e1eb81`；必要证据、失败尝试和运行版本在该实验`delivery_receipt.json`与`results/`。Notion原PEPE研究已补接入记录，AGENTS已登记复用约定。无下载、安装、Pine或实盘修改。

## 2026-10-20 — PEPE 3R 含成本保本单变量检验完成

- `exp-pepe-range-be3-20261020-v1`：原1,889笔信号、1,886配对随机不变，仅15m收盘3R推含20bp成本保本；原5R/4ATR及跟踪后H1反向退出保留。
- 原方案统一浮点修正后净−545.75R，保本−508.39R，仅改善37.36R；190笔救回+282.58R，27笔赢家受损−245.22R。10R保留44/54，20R保留13/15；2026恶化23.16R，不接受默认升级。
- 13测试、11,325完整标量路径、3,775旧账本parity通过；新增胜率主要来自保本按tick取整后的微利。独立交易总R不是账户收益，未改Pine或交易服务。
- 源59904e1645，结果及复现`experiments/active/exp-pepe-range-be3-20261020-v1/delivery_receipt.json`；Notion <https://app.notion.com/p/3ff8856479af81d8926dff01acc7626d>。

## 2026-10-20 — PEPE V2.8 止盈止损回测完成

- 实验 `exp-pepe-range-exits-20261020-v1`：364,633 根连续 OKX 5m（含预热），2023-06 至 2026-10-20，1,889 个独立信号、2,400 组；固定20bp成本。主目标等风险净R全负，等金额另有4组历史正候选，不能混用口径。
- 原版净−546.77R；等风险事后最好为5根5m极值＋0.2%缓冲、风险超过2%跳过、固定20R：1,789笔净−35.39R（含2笔边界计价+15.25R，仅自然退出−50.64R）。等金额事后最好为3根＋0.1%、上限1%、10R启动4ATR及已启动后反向H1退出：每单1000USDT名义金额累计+174.22USDT，但净R为−114.85R。
- 原止损下固定20R有107个毛20R，原跟踪只保留15个最终达到20R；大盈利确实存在。早期选参所得3根＋0.1%／上限1%／3R后3ATR，在2025和2026仍亏131.01R及148.81R；未确认稳定最优，不改Pine默认值。
- 已解决归档未确认行与微小币价浮点边界；原生146笔四签名、近期10笔逐字段、26测试、9600抽样路径及35842条选中完整路径通过。全库登记校验仍被已有缺source_commit条目挡住，本次登记单独验证通过。
- 结果、逐笔CSV、对照和复现命令：`experiments/active/exp-pepe-range-exits-20261020-v1/delivery_receipt.json`；Notion：<https://app.notion.com/p/3ff8856479af8120a33ae4b0ea3ae4c2>。无账户收益率、资金费或前向盈利声明；无下单、训练或promote。

## 2026-10-20 — Task62 V4 极值过滤和缩止损研究完成

- Owner授权蓝色K阈值/因果回看窗口及两种缩止损，冻结31,512组；原入场、四根结构窗口、0.2%成本及时间分段不变。新实验：`experiments/active/exp-eth5m-stoch-extreme-stop-20261020-v4/`。
- 原条件K>96.19/K<4、当根+前3根、1点缓冲/8点上限、20R/5R开仓价保本：261笔13个20R，目标率4.98%（原4.96%），净−1104.03点；匹配239对超额+469.79点，后段135对超额−72.16点。约九成信号及大赢家一起过滤。
- 缩缓冲至0.5/0.25点，原261笔自身净减少145.73/45.71点，新增交易另亏191.87/280.57点；上限降至4点只是删去170笔少亏780.53点，同时删去8/13个20R。
- 2024至少100笔选参仍选1点缓冲/8点上限，K96/4、当根+前2根、20R/5R含费保本；后段156笔净−771.67点，139匹配对超额+17.72点、p0.9384。全历史235个正收益组合均≤49笔，保留观察；无支持盈利升级的证据。
- 96测试、145834原路径、5824新增标量路径、104原汇总、40独立汇总、746905窗口、448143结构止损及全部正候选1279路径通过；`audit_v2`另复现5个止损归因等式。代码bfd5b5cd97/901d636250/34c3eb8ed0/674dbd6b8f；必要结果与复现命令在`delivery_receipt.json`。
- Notion V4：<https://app.notion.com/p/3fe8856479af812abe33d80718028f1f>，任务62已更新。复用历史数据，无下载/哈希；无账户曲线或前向资格，training/production=false。未修改指标、服务或账户。

## 2026-10-20 · Notion #62 V3 独立目标×保本网格完成

- Owner授权去掉反向强退、逐信号独立；26臂：10R/20R，各自无BE或0.5/1/1.5/2/3/5R推开仓价/含费价；入场、结构止损、8点跳过和20bp不变。
- 全历史第一20R＋5R开仓价BE，139/152目标，毛412R／1825.54点，净−2725.18R／−12858.61点。救284笔初始止损、丢13笔20R，改善仅24R／174.98点（p.7172）；随机配对超额不显著。
- 2024选出20R＋5R含费BE，在后段净−9053.79点，比不BE差849.10。最近24信号10R＋2R/3R开仓价BE固定1ETH净+6.06点，等风险−2.49R；全历史/开发/后段26臂均净负。
- 145834路径独立核验、11218旧版基线parity、104汇总组、156画框路径、96测试通过。一个20R持仓截尾，浮盈单列；原始账本和共同成熟样本均保留。
- 实验：`experiments/active/exp-eth5m-stoch-target-be-20261019-v3/`；Notion任务Done，研究记录 `3fe8856479af81be8af4c0e578af1db7`。training/production=false；无生产/账户/浏览器改动。当前结果未支持盈利退出升级。

## 最新更正：任务62逐笔10R/20R审计完成，V1共同早退限制已澄清（2026-10-19）

- Owner追问每单与大R后，V2 exp-eth5m-stoch-tail-forensics-20261019-v2确认：V1单仓基线本来已有33笔毛10R/4笔毛20R，最大40.07R。此前只报总亏损遗漏长尾；反向箭头与单仓是研究假设，不能替代Owner规则或宣称穷尽最佳退出。
- 2805合格信号中274/152个在原止损前先到10R/20R；反向退出在目标分钟之前截断212/141个。1R/2R保本分别扫掉36/15个潜在20R。10R/20R成功路径中位5.6/17小时；静态最大浮盈可跨数月，不能当可兑现收益。
- 12框已逐笔核对：17日22:50空单实际16.71毛R，18日05:10空单独立12.16R但被单仓排除；19日17:50画框27.77R按结构分母为17.13R，18:30已在5.92R反向平仓，21:38才达规则10R。1/5号画框即使用绘制止损也先止损后到目标。
- 同目标8/10/20R只切换反向早退，原风险/成本/控制不变；纯20R独立152赢/2652止损/1截尾，毛+388R、20bp后净−2749.18R，匹配正超额不显著。该臂不含保本，不能据此宣称完整Owner策略无效。V1原数字保留并限制其解释范围。
- 全2805行all_signals.csv、61710旧臂明细和12框保存；90测试、33654独立bracket、86标量极端/绘图/抽样、2805旧8R及5610触达身份核验通过。复现/口径见V2 delivery_receipt.json。Notion任务Done，旧页加更正，V2 3fe8856479af815597fbd1ed55474fd4完成回读。无新下载/哈希/报告/浏览器/生产操作。

## 最新进度：Notion #62 ETH5m Stoch退出研究完成，22臂均亏损（2026-10-19）

- Owner确认下一根5m收盘同向后市价入场，信号/前两根/确认共四根极值外加1点，实际风险大于8点跳过；保本触发无固定值。TradingView12个盈利框全部读取并联结确认收盘，重建后6个超过8点；原文05:35与05:05高点混合的问题已澄清，原图不改。
- 实验exp-eth5m-stoch-confirm-exits-20261019-v1：复用OKX分钟历史，只补近期缺口，1473045根连续分钟，无内容哈希。10907箭头→5800同向确认→2805合格，2804固定匹配随机。2024选退出，2025起后段，北京10月14–19日示例单列。22臂独立单仓，20bp固定。
- 22臂在前/后/示例各段均扣费亏损。2024选2R推开仓价，后段1734笔净−9241.86点、PF0.278，差于反向箭头−8929.32；后段1810成熟配对超额−1489.07点、Holm p0.4872。全历史事后最好1R推开仓价仍−13947.77点。单位每笔固定1ETH，非账户回报；费用/初始风险中位数0.998R。不接受退出升级。
- 98定向/边界测试、123398独立符号价格路径、44单仓队列、176汇总组通过；含费保本浮点尾差分类已修正，总金额与排名不变，初始汇总保留。全库登记存在旧条目spike-v8-total2-1h-native-20260914-delivery缺source_commit，本轮两条登记独立契约通过。
- Notion任务3fe8856479af8034b989fed2918245c2为Done，知识库实验3fe8856479af81fda7dcf91f09968c2a完成并回读；复现、原始结果及失败收据见实验delivery_receipt.json。training/production=false。无报告/Pine/模型/服务/交易写操作，无新浏览器会话，保留用户TradingView。

## 进行中：Stoch／WVF 独立入场与 Spike 回踩择时（2026-10-19）

- Owner已授权原超卖Stoch金叉、同根WVF绿柱及Spike有效回踩后等待的六组只做多比较；参数、原止损/退出/20bp冻结，独立占仓与匹配随机，不改生产。实验exp-spike-stoch-wvf-entry-20261019-v1。
- 正式路径正在paths_v1运行（本任务exec20707，主PID13535）；五币smoke_v2路径、独立金额/占仓/配对和57条极端交易通过，116定向/边界测试通过。全量收益尚未查看，不能提前判定有效。
- 路径builder b63f31f2ab，汇总/独立审计b53f577198，极端审计696a67398f；修复Python3.9审计ISO Z解析，失败收据保留。Notion实验3fe8856479af81ef8ca2d005a453d769运行中；候选3fe8856479af816fa6fcd0d688b8e6dc。完成后需全量汇总、独立核验、极端价格复查、更新登记/Notion及本节。
- 无下载/哈希/Pine/模型/服务/订单操作，不另建报告。Luna max实际运行档位不可核验，子代理已停止，由主线程执行。其他会话PID52965家族不是本任务，禁止停止。

## 最新进度：V13.1 规则拆解与启动／过热状态已完成（2026-10-19）

- Owner授权的前两项完成。638个Binance历史USDT永续／2552周期流，12个锚点臂＋3个因果状态各已知/低/中/高，共24队列。原再次突破、止损、反向退出、实际R、2R启动、1H2ATR/其他4ATR与20bp固定。177383原锚点、4489577联合锚点、8978456条路径记录；最终输出experiments/active/exp-spike-v131-rule-state-20261019-v1/paths_final/analysis_v3/。
- 原版21534笔1259.713421R、2025年起14653笔579.061938R复现。统一取消推进门最高1592.46R，但订单+31.5%、均R-3.9%、已平仓R回撤+37.1%；同成熟配对信号1547.45R对随机2203.57R。取消BB1006.11R、均R0.01375；简化锚点-7751.12R。无校正后正向规则增益。此处简化改的是原信号条件，仍用再突破入场，与上一轮首次突破入场不同。
- 中等压缩/推进是描述性候选。前期按配对增量超额选组，后段三方向总R145.42/57.68/36.98均低于原版579.06R且交易更少。1H中推进虽增量Holm p0.0288，信号本身较基线少3.07R，绝对超额不显著。首次读取主结果数值前另加双方同状态配对诊断，后段36项无校正后正超额；实际暴露仍可能不同。未接受升级，training/production均false。
- 全量独立6539303金额、215482944资格、323232792占仓决策、192个极端选择去重74条价格路径通过；1398730原版标量、177348随机身份、1728前缀、21818基线占仓身份通过。同状态816454成员/独立队列与2619汇总对照通过。39项定向测试通过；7个在157c61f4c9已存在的仓库失败仍保留，未声称全仓通过。
- 所有失败保留：END候选、浮点55%边界、CSV分位点解析、50个缓存NaN/None失败精确重算合并；两次汇总性能重启保留。最终builder记录、复现命令在delivery_receipt.json，完整发现findings.json；额外4条数值/统计/性能经验已保留。Notion3fe8856479af81a2b965d252bcb1d5c9已完成同步回读。历史复用、币池/tick非逐时点，无资金费、额外滑点、共享资金或前向验证。
- 本轮无下载、内容哈希、新训练、生产/Pine/服务/订单改动，也未额外创建报告。子代理实际模型档位无法核验即停止，由主代理完成。其他已讨论方向（结构退出、交易所/流动性、组合资金、执行/资金费）未在此实验执行；后续优先研究退出与入场追价的配合，当前不改实盘。

## 最新进度：V13.1 四阶段入场完整占仓比较（2026-10-19）

- Owner授权四种入口都试。638币、四周期全量完成；含预热177383原锚点，2023起175009，1418924条阶段/控制结果。原信号、首次突破、首次有效回踩、现有再次突破各自重放占仓；止损、实际R、2R启动、1H2ATR/其他4ATR、20bp保持。
- 全期净R依次1273.87/3410.60/1475.59/1259.71；2025后-64.11/1634.92/736.61/579.06。统一首次突破总R最高，但约5倍订单、均R由0.05850降至0.03192，已平仓R回撤4090.78对609.00（非账户回撤）。前期按周期选15m/30m突破、1H/4H即入，后期2080.94R；仍未建立新优势。
- 153077共同成熟配对队列中，首次突破信号3241.97R对随机6141.83R；后期1546.81对4534.09R，超额区间跨零。双方同非周日门、失败不补抽、零成交保留；与完整信号总R分开。没有获准升级，未改生产。
- 1H重新占仓基线3344笔326.263R；统一Jan2023后旧3338笔313.996R全部保留且逐笔对齐，新接6笔+12.267R。旧3357含19笔Dec2022，不可直接混比。
- 100定向/边界测试及数值修正后13项复验通过；5760431独立标量、892440占仓、128178旧确认标量、18237序列身份、125506随机身份、7497前缀、32极端案例、120跨汇总核对通过。失败smoke的混合arm与统计BLAS告警已分别在全量/正式统计前修复，保留证据。
- 实验exp-spike-v131-entry-stages-20261019-v1，代码、原始账本、配置、收据、registry与3条learning保留；无报告、下载、内容哈希、浏览器或服务/订单动作。Notion新记录3fe8856479af81baa0b7cb24537c6042已回读；前轮确认成本待同步恢复为3fe8856479af8174be16e44a0e3344d4。子代理运行模型无法核验即停止，由主代理完成。

## 最新进度：V13.1 确认筛选与追价归因（2026-10-19）

- Owner授权优先研究确认的筛选收益能否支付追价。638币完成；15m/1H成熟原始机会116667，原随机116574，四周期原21547闭合订单另作费用/距离诊断。确认平均追价约0.69/0.71原锚点R；距离、等待根数、费用R分组没有稳定统一过滤门槛。
- 逐笔分开取消机会、入场/退出价差、费用本金与风险缩量；事后已确认集合的提前入场收益仅为归因桥梁。1H恢复实际2ATR退出，原占仓集合保留；独立重叠事件不是账户组合。零成交随机机会保留，不补抽。
- 原随机档案允许UTC周日，但后续15m汇总过滤周日。保留原结果，追加双方同非周日门的敏感性；覆盖87.1%/88.8%。后期每原机会配对超额15m +0.00412R（95%[-0.00669,+0.01581]），1H -0.02149R（[-0.03861,-0.00507]）。未建立新策略优势，不改生产。
- 30定向+71层边界测试通过；2197588独立金额/分组检查、199659退出档案对照、12极端/等待前缀检查通过。结果及复现命令在experiments/active/exp-spike-v131-confirmation-cost-20261019-v1/；无下载/内容哈希/额外报告/服务与订单变更。两条learnings记录归因与控制资格教训。
- Notion创建返回传输错误，后续唯一键查询也失败，不能确认是否创建；notion_sync_pending.json保留待同步信息，恢复后必须先查重。下一研究候选为确认环节逐层比较，尚未执行。子代理实际模型档位无法核验，已停，由主代理完成。

## 最新进度：V13.1 原始订单浮盈浮亏分布（2026-10-19）

- Owner追问原策略/回测和入场后的浮盈浮亏。复用638币Binance档案、原始未剔除币池四周期闭合订单；实际634币21547笔。21444笔曾毛浮亏、至少21348笔曾毛浮盈，最终7311笔净盈利；10648笔确定曾达1R中3496笔最终净亏。浮盈和浮亏可重叠，不代表先后顺序。
- 1H仍为固定原入场trail2退出重放，保留19笔2022年12月原交易。旧exit_time是母策略终点，877/3357笔实际更早退出；本次先恢复真实保护触发点，再测路径。MAE在止损/跳空成交截断，止损当根MFE保留下/上界，未编造K线内高低先后。
- 五项边界测试、21547笔净R/收盘峰值对照及468项标准库独立统计检查通过；各周期原匹配随机收益保留。结果仅描述历史，不证明新退出规则或生产优势，无新参数/服务/订单操作。
- Builder ae053685db先提交后运行；原始结果experiments/active/exp-spike-v131-excursions-20261019-v1/results_v1/，两个registry与Notion原研究3fa8856479af8152ae1bca1fb534c805补充并回读。经验docs/learnings/exit-policy-archives-need-actual-exit-before-excursion-statistics.md；无下载、内容哈希、浏览器或额外报告。子代理模型档位无法核验后停止，由主代理完成。

## 最新进度：巡检卡片 65% K 线预览与实盘逐笔平仓（2026-10-19）

- 巡检发现卡片打开独立 K 线预览，桌面宽度 65vw、手机底部高度 65dvh；展示原检测快照、六均线、形态框与确认时点，关闭恢复原卡片焦点，可进入完整图表再返回。最新 Stitch 画板 adbadedba9a6456693160ba9eb124961（项目8978791379095391159，GEMINI_3_8_FLASH），代码/截图及来源在 docs/design/stitch-20261019-close-preview/。
- 实盘账户为数量核对通过的系统分笔增加平仓按钮和本人确认弹窗；复用现有持久化命令与锁定执行器，仅关闭选中 lot。确认回执丢失只查询原指令，不自动重复提交；未归属/状态未核对仓位不开放此操作。平仓 Stitch 请求超时后 Owner 明确允许沿用现有组件。
- 后端128项相关测试、前端重点44项及桌面/手机浏览器流程通过。最新实测1440px桌面预览宽936px，320×740手机预览高481px；模拟平仓取消不确认、丢失回执仅提交一次并查询到完成。所有交易浏览器请求均拦截为模拟回执，未进行真实平仓。证据 output/playwright/close-preview-20261019/，经验 docs/learnings/manual-close-ui-must-bind-to-owned-lot-and-persisted-command.md。
- 仅重载网页服务以接入 API，未重启执行器或修改计划。收尾4巡检仍启用、无计划错误，YOLO及两执行器心跳正常；网页扫描1141/1956、errors=0，market_ready/model_ready尚为false，不计完全就绪。本任务浏览器已关闭且8个归属进程退出。共享main混合改动未提交。

## 最新进度：Mac 代理访问 spikeok.com 已恢复（2026-10-18 17:15 北京）

- Owner 要求修复电脑端。按域名的原代理路径 TLS 失败；同一 SNI/证书校验、两个真实公网 IPv4 经代理均200，直接访问其中一个IPv4超时，IPv6失败。只加DNS、仅改直连以及Owner清理Chrome DNS缓存均未独立解决，不冒称已取得代理服务器内部DNS根因。
- Shadowrocket保留原节点，在default.conf为spikeok.com增加Host DNS server:1.1.1.1、always-real-ip、系统HTTP代理例外和精确PROXY规则；TUN继续代理实际IPv4。关闭该配置IPv6支持（这是全配置开关，其余新增项仅限该域名），不写死网站IP、不关闭证书验证、不启用HTTPS解密。原配置备份在仓外PublicTunnel/shadowrocket-before-spike-direct.conf；该备份已含本轮新增Host DNS项，全轮回滚还需移除该项。
- 普通域名首页、/api/status、/static/app.js、/api/market-sync均200，1.35–1.83秒；真实Chrome加载BTC行情、K线、六均线、494交易对，无加载占位，控制台warn/error为空。public_tunnel status的public_ready=true；start复用正式隧道身份，已退役Quick Tunnel/ngrok并核对两者LaunchAgent和plist均消失。固定入口仍为https://spikeok.com/#market?tab=overview 。
- 配置应用时Cloudflare连接发生短暂重连并返回502，随后原进程自动重新注册；验收在重连后进行。原始结果output/spike-domain-20261018/mac-final-health.json。含账户画面的验收截图置于仓外Codex visualizations目录，不加入Git。
- 收尾：四巡检enabled，周期15/15/240/1440分钟且无计划错误；YOLO13522心跳新鲜，扫描45264推进且errors=0；执行服务running、心跳正常、error_present=false、account ready、blockers=[]。未调用交易写接口或重启执行服务。关闭本任务Chrome675和失败导航留下的空白678，任务标签列表为空，保留用户日常浏览器及其内部DNS页。
- 本轮没有应用代码/UI改动，只更新运行设置和必要交接；定向网络及浏览器验证通过。经验docs/learnings/proxy-domain-success-needs-browser-and-address-family-checks.md。共享main混合改动未提交；仓内其他实验日志存在既有空白检查问题，不改动那些文件。

## 最新进度：spikeok.com 固定公网已接通，Mac 代理路径仍待恢复（2026-10-18 16:57 北京）

- Owner 完成阿里云 DNS 短信确认和 Cloudflare Tunnel 授权。注册局 active；NS 为 hugh.ns.cloudflare.com / laylah.ns.cloudflare.com；Cloudflare 域名已激活，Free 通用证书有效至 2027-01-16（受管理），已打开“始终使用 HTTPS”。域名仍在阿里云注册续费。
- 固定入口：https://spikeok.com/#market?tab=overview 。正式隧道 spike-workspace，UUID e9db24b3-a8ae-4675-9551-f82cf84a8dd7；根域 proxied CNAME 指向该 UUID.cfargotunnel.com。仅路由精确主机到原 Caddy 8780，保留同源/本机功能规则。com.spike.public-cloudflare 为 RunAtLoad/KeepAlive LaunchAgent，Mac 登录后启动；Mac 与网络仍需在线。
- Google 公共 DNS 返回 104.21.80.80 / 172.67.176.83。保留域名 SNI/Host 和正常 TLS 校验，两个节点的首页、/api/status、/static/app.js 共6项200；真实服务 spike Impulse Monitor，扫描错误0。Owner 明确回复“手机能正常打开”。正式隧道重启 PID46387→47558，第二次健康请求成功，同一 UUID/域名保留。原始证据 output/spike-domain-20261018/。
- Mac 的 Shadowrocket 按域名访问仍出现 TLS EOF/连接关闭，普通 curl/urllib 和 IAB/Chrome未通过。代理/DNS缓存是候选原因，未确认内部根因。没有改 Shadowrocket 配置、重启全局网络或关闭证书校验；VPS交叉请求因SSH握手超时未取得证据，web抓取工具也不可访问，不计为成功。HTTP重定向开关已保存，但本机HTTP探针失败，不冒称重定向端到端验收通过。
- 私有 named-endpoint.json 已按双节点HTTPS与Owner蜂窝证据记录 activated_at_ms，fixed-endpoint.json/access.txt 指向正式域名；public_ready 仍如实反映本机网络路径。旧 Quick Tunnel 和 ngrok 连接器暂保留；临时地址 https://weather-demo-jvc-explorer.trycloudflare.com/#market?tab=overview 。本机正常路径恢复后执行 .venv/bin/python -m yoyo.monitor.public_tunnel start，会复用正式身份、核验后退役旧连接器，不重新建随机地址。
- 收尾健康：4组巡检仍 enabled，周期15/15/240/1440分钟，无计划错误；YOLO13522心跳新鲜，扫描1960/1960、errors=0，执行32689与Shadowrocket33402存活且未重启。已关闭本任务Chrome域名详情655和授权/验收665，用户Cloudflare660保留。IAB临时错误页2因data URL策略不能取得关闭句柄，原生Codex也禁止控制；未绕过限制，该页未标记保留，交由工具回合清理，未声称已经核对关闭。
- 凭据只在 ~/.cloudflared（目录700、文件600）及 ~/Library/Application Support/Fable/PublicTunnel（700、凭据600），未入仓或命令参数。58项定向测试已通过；本轮仅更正状态提示文本、补实际部署记录。学习 docs/learnings/public-provider-migration-needs-pending-state-and-saved-identity.md。共享 main 混合改动未提交；未修改交易、巡检计划或应用界面。

## 最新进度：公网临时隧道再次恢复，固定域名待购买（2026-10-18 16:15 北京）

- Owner要求先恢复公网，再做长期固定地址，并确认尚无域名。ngrok仍实际返回403、`ERR_NGROK_725`；凌晨Quick Tunnel日志持续`Unauthorized: Tunnel not found`。本机8766及带正确Host/HTTPS头的8780状态均200。
- 仅`kickstart -k com.spike.public-tunnel`，新PID41432。当前入口为 https://weather-demo-jvc-explorer.trycloudflare.com/#market?tab=overview ，首页、状态接口、app.js均200；IAB实页看到行情、六均线和494币，控制台warn/error为空。初次导航工具超时，但随后同一标签加载成功。仅创建的标签1已关闭，IAB余0标签，未启动Chrome。
- 私有`fallback-endpoint.json`与`access.txt`已更新并保留旧备份；`fixed-endpoint.json`与原ngrok保持。既有`public_tunnel status`仍优先显示故障ngrok，当前临时入口以上述私有文件为准；本轮未修改应用代码。网关35892、网页55539、扫描55546、自动执行32689保持；4巡检enabled且无计划错误，YOLO13522心跳正常。
- 推荐自有域名 + 正式Cloudflare Tunnel → 现有Caddy网关；待Owner购买域名并完成账号授权后配置，尚未部署。已给出Cloudflare注册入口、核对首年/续费价及邮箱验证步骤；域名只解决固定名称，Mac及网络仍需在线，不承诺免费服务长期可用性。
- 原始检查`output/public-recovery-20261018-afternoon/`，经验`docs/learnings/stable-public-hostnames-require-persistent-tunnel-identity.md`。无交易操作、UI改动、计划改动或新自动化；共享main混合改动未提交。Luna子代理因工具未返回可核验实际模型档位而停止，由主代理完成。

## 最新进度：信号中心自动执行恢复（2026-10-18）

- Owner明确授权“查清为什么停了，确认没问题后恢复自动开单”，并确认八笔为其手动平仓。执行器在10月17日20:00更新后被放回Codex临时PTY（PID28449、父进程93565、会话54008），原LaunchAgent未接管。最后心跳10月18日00:15:57；对应聊天00:15:55取消订阅、00:16:55记录teardown/Shutdown。根因证据指向临时聊天会话绑定；未取得子进程精确退出信号，不冒称操作系统崩溃或交易所拒绝。
- 先用只读Client核验账户身份、原配置、时间、可用保证金、两次实际仓位、全部挂单及完整有界订单历史。八笔逐一确认唯一原开仓/手动平仓、方向/数量/时间及USDT费用回执，原始库完整备份后单事务补为closed/owner_manual_close并追加journal；未清账、未改cutoff、未重放订单。剩余USO持仓与止损一致，行情管理窗口可读。对账阶段交易所POST为0。
- 复用com.fable.okx-execution.session，通过launchctl kickstart恢复。新PID32689、PPID1、锁持有；多轮心跳推进，entries_enabled=true，实测accepting_entries=true，errors=[]、worker_error=null。账户API为running/ready、blockers=[]。原配置、风险参数、凭据、KeepAlive=false及会话级安装不变，不新增开机/登录自动启动。手动账户PID70369保留。
- 339项执行/边界测试通过；仅有既有LibreSSL警告。恢复后过期信号正常跳过，验收时尚无新增成交，不以服务就绪冒充已完成新单交易。网页服务存活、扫描零错；未启用浏览器测试。
- 私有原始证据/恢复脚本/账本备份在本机OKXExecution/recovery-20261018/，不入仓；经验docs/learnings/live-workers-must-outlive-chat-sessions.md，README补充独立托管与恢复验收。未改执行算法或界面、未提交共享main混合改动。子代理因无法核验实际模型档位已停止，主代理完成。

## 最新进度：ngrok 月流量耗尽，临时公网入口恢复（2026-10-18 02:05 北京）

- Owner反馈原行情地址挂了。原域名实际HTTP403、`ERR_NGROK_725`，正文为本月网络流量额度耗尽；本机页面及带正确Host/HTTPS头的Caddy状态接口200。原固定域名尚未恢复，重启本机服务不能解除供应商限制。
- 当前可用临时入口：https://later-bio-monsters-nail.trycloudflare.com/#market?tab=overview 。复用原网关，恢复原 `com.spike.public-tunnel` LaunchAgent，PID68026。原ngrok PID75107、网关35892、网页55539及扫描55546保持。`fixed-endpoint.json`保留；私有运行目录新增 `fallback-endpoint.json`，`access.txt`注明当前入口、原入口故障及临时限制，旧说明另存备份。
- 公网首页、状态接口、app.js均200；真实IAB看到行情、六均线、494个交易对，控制台error/warn为空。临时标签1已关闭，IAB标签0，无新Chrome。临时隧道重建会换域名，不能视为永久收藏地址；原域名需额度恢复或Owner自行调整套餐。现有 `public_tunnel status`仍优先显示固定入口；临时状态查上述文件和 `com.spike.public-tunnel.log` 最新地址。固定入口恢复后现有start会先核验再退役临时隧道。
- 四巡检仍enabled、15/15/240/1440分钟，YOLO13522心跳正常；手动21051心跳仍在，但ARX两旧单为position_mismatch并阻塞新开仓；自动执行保持stopped、最后心跳00:15:57，未擅自重启或处理仓位。跟单23135保留。仅恢复公网传输，无交易写请求，无应用代码改动或新AI定时任务。
- 原始验证 `output/public-recovery-20261018/`；经验 `docs/learnings/public-tunnel-process-liveness-does-not-prove-provider-quota.md`。子代理无法核验实际模型档位后停止，主代理直接完成；共享main混合改动未提交。

## 最新进度：选定 Stitch 原版工作台、导航与公开行情模块落地（2026-10-18）

- Owner 最新纠正要求推翻原功能布局，补首页高密工作台；本条取代下面“尚未选定最终新稿”的状态。最终来源是原项目 8450534110669085172 的 dcabed 市场、b94aba 巡检、89614c 账户及 483fbb 订单画板。四份真实代码已导出到 docs/design/stitch-20261017/selected-terminal/；8978791379095391159 青色方案已标为 superseded 并保留历史。
- 根 DESIGN.md 是从选定 HTML 提取的落地契约，不冒充原生文档导出：对应 Obsidian 文档节点仍没有可用内容。设计压缩包区分 active / superseded-cyan；当前规范以选中画板的布局、色阶、字体与六均线颜色为准。
- 新首页为全宽顶部导航、合约行情和六项数据条、左侧全市场/自选、中部六均线图、右侧强平与主动成交、底部共振矩阵与记录。重组为行情工作台、信号与巡检、实盘与风控、研究实验室、交易书架、系统运行六类；全站二级菜单按任务组织。巡检首屏四列12卡、实盘统计与持仓同屏，卡片到图表再返回保留条件；确认信号保留卡片布局。
- 新只读 /api/market-workstation 使用独立限流公共HTTP池与逐字段缓存，接资金费率、OI、标记/指数、合约面值、最近500笔成交、已发生强平。单位按合约面值换算，时间/去重/新鲜度独立校验，失败不展示旧投影；这是成交样本与历史强平，不是完整时段CVD或预测热图。没有加入执行脉冲，没有变更任何交易阈值或模型门。
- 修复市场首次挂载空节点、依赖加载顺序、共振表格失去样式、巡检键盘页签顺序、手机账户表格撑宽及隐藏的报价过期标记。公共导航、研究、书架、阅读、运行和VLM Shadow DOM使用共享布局/组件；没有把源截图或示例数字作为产品内容。
- 验证：461项前端、63项后端测试通过；27个页面入口真实读取，巡检卡片→六均线→返回、494币全市场涨跌/成交额排序、账户/持仓、390px手机菜单和320px账户表格内滚动已检查。全路由入口检查不等于每个写入流程验收。外部TradingView完整组件快速切页出现过contentWindow警告；本地市场Lightweight图表正常，未宣称第三方警告已修复。原始截图、测试与读状态在 output/stitch-redesign-20261017/，未另建报告。
- 网页通过原LaunchAgent正常重载45793→53878→55539；仅网页服务，本次未预览/确认/撤单/改仓、未启停实盘执行。收尾手动21051和巡检13522运行，四计划仍enabled 15/15/240/1440分钟。自动执行账本显示stopped、最后心跳00:15:57，早于末次网页重载；未擅自重启。网页扫描仍进行中，不能把service_alive等同于market/model已就绪。
- 本任务IAB18/19及新建Stitch中间标签977882601已关闭，IAB标签列表为空，视口复原；参考服务器18770/PID50963退出，Owner原Stitch与Chrome保留。主代理直接完成、未使用子代理，未切分支/建worktree，未提交共享main混合改动。经验见 docs/learnings/stitch-selection-must-be-verified-before-porting.md 与 public-market-samples-need-units-coverage-and-independent-freshness.md。

## 最新进度：Stitch重新生成全站方案（2026-10-17，Owner纠正）

- Owner明确要求让Stitch重新生成整体设计，并撤掉助手对配色、布局、密度、字体和组件样式的限制；不是直接复刻旧画板。最新要求覆盖本轮助手先前的“Less, but better”约束，尚未选定最终新稿，不按旧稿继续改代码。
- 已通过Owner已登录Chrome创建独立项目：https://stitch.withgoogle.com/projects/8978791379095391159 。只提供市场、信号、检测、账户交易、研究、学习、运行七类功能和子页面范围，让Stitch自主建立全站设计；要求桌面/手机与DESIGN.md。旧项目8450534110669085172保留。
- 新项目已生成10张页面画板（首批6张、补充4张）、设计主题板与完整DESIGN.md，覆盖核心交易、账户、研究、学习、运行及手机端。Stitch自行选择深色青色视觉方向；助手未做审美纠偏。生成完成不等于全部生产路由已经重构，现有前端本轮未替换。
- 输入保存在 `output/stitch-redesign-20261017/fresh-unrestricted-brief.txt` 与 `fresh-completion-brief.txt`。实际画布截图为 `fresh-stitch-account.png`，通过Copy as PNG取得原尺寸账户画板 `fresh-stitch-account-original.png`，均为Stitch示例数据设计。旧 `brief.txt` / `correction.txt` 属于已被Owner否定的受限旧迭代，不能继续当作新设计要求。
- 主代理直接操作，未委派子代理、未改交易逻辑、未实盘操作、未发布。保留Owner的Chrome新项目标签；临时IAB标签17已关闭，没有新建Chrome测试进程或停止后台服务。

## 最新进度：均线密集结果与实盘同屏、开仓恢复修复（2026-10-17）

- 由主代理直接完成，未委派。检测结果页改为图表 + 288px 实盘侧栏，窄屏在同页纵向排列。权益、可用保证金、浮盈亏、已核算净收益、开平仓笔数、当前持仓、每笔保护和最近订单直接可见；点击持仓看对应周期图表，返回订单定位账户区，保留形态上下文。
- 新增只读 `dashboard?view=workspace`，返回全部当前持仓/进行中请求及最近五笔记录，不携带交易所历史/原始账本。统计覆盖完整本地手动账本，完整USDT成交费用回执才计入净收益，外部合并平仓标为未归属。现场投影7788B，对照full45264B；这是单次传输大小，不是成交延迟保证。
- 修复旧预览慢返回作废新报价的服务端并发问题：每个检测引用在保存报价时核对请求代次。前端取消旧读取，实际确认请求不取消/重放。明确的确认前blocked恢复到待审核，GET检查恢复后自动重算预览，仍需逐笔确认。
- 交易所GET短暂失败、一般账户核验失败和旧单仓位冲突分开表达；本轮现场捕获两账户间歇GET读取异常，后来恢复。未移除真实仓位/成交核验门，也未声称外部网络已根治。已有订单可随时回到检测，状态继续在结果页更新。
- 修复持仓详情误用预览TTL显示“点位已过期”、状态轮询重建图表、账户刷新收起保护详情/丢失焦点、旧轮询超时取消新请求、持仓币种不在形态筛选内导致上下币失效、图表内外高度不一致。
- 验证：456项前端回归，329项执行层及层间边界检查通过。真实桌面/390px查看持仓与已有订单；隔离模拟验证预览阻塞恢复、确认前阻塞、无自动确认、立即返回、状态轮询复用图表（6次状态轮询仅2次初始挂载）。真实测试未创建预览、未确认/撤单/改仓。仅重载网页服务，执行和巡检进程保留。
- 原始验收：`output/ma-workspace-20261017/`。模拟画面独立标明，不作为真实成交证据。未提交共享main的混合改动。
- 收尾：临时IAB标签15/16已关闭，标签列表为空，视口恢复；隔离模拟服务18769已停止，控制台无error/warn。网页45793、手动21051、自动28449、YOLO13522、copier23135及Owner Chrome1277保留；四组巡检仍启用15/15/240/1440分钟。只读快照及心跳见 `cleanup-health.json`，账户展示缓存与执行就绪状态分开记录。

## 最新进度：全站少即是多与渐进展开（2026-10-17）

- 按 Owner 新要求直接实施，未委派；根 DESIGN.md 新增极简规范，覆盖旧密集参考的布局要求。原功能路由保留，公共导航移除自动本页索引与重复标题；搜索移入功能栏，主题/密度/本机状态统一为菜单。
- 市场范围与排序合为一行，494币全目录涨幅/跌幅/成交额排序保留；行情行缩至74px，移除重复记录入口。常驻周期/设置/放大，图表辅助开关进入设置；状态缩为一行，详情/提示音按需展开；历史与观察设置进入“更多”。六条源均线与交易数据口径不变。
- 所有 work-inspector 改为桌面/手机共用的内联展开，记录获得完整宽度，原节点/输入/事件不变；信号搜索和当前条件一直可见，卡片保留。均线密集的图表视角收进现有设置；学习/知识/内容的资料流程收起，重复英雄标题和容器减量；VLM 空态只保留一个上传入口。
- 新菜单外部点击/焦点离开关闭，Escape 回到入口且不关闭外层手机导航；修复移动菜单仍被旧顶栏样式隐藏、页签间距被高优先级旧规则覆盖，以及市场重挂载时开关/图例没有立即按当前状态初始化的问题。新增3项菜单/折叠状态行为测试，449项前端测试通过。
- 浏览器巡检27主路由的1440/1024/390px布局均无整页横向溢出；三旧入口可达。实测全市场排序、筛选条件保留、原图表放大/Escape、六均线、历史入口焦点、专用账户只读明细及VLM空态。原始截图、检查JSON、测试结果在 output/frontend-minimal-20261017/。这不等于重跑所有交易写入流程。
- 快速离页时捕获2次第三方TradingView嵌入脚本的 contentWindow 卸载告警，保留 browser-console.json；未确认正常停留使用受影响，本轮未改模拟开单图表加载器。VLM既有余额不足暂停提示仍明确显示。收尾账户快照状态见 service-health.json，不把服务进程存活冒充行情新鲜。
- IAB本轮tab14已关闭，核对标签0，viewport恢复；Owner Chrome1277与网页38026/手动21051/自动28449/YOLO13522/copier23135进程保留，四组巡检仍启用15/15/240/1440分钟。无预览/确认/撤单/扫描/计划写入，无服务重启，无整体提交共享main的混合改动。

## 最新进度：信号 / 均线密集 / 账户交互与性能回归（2026-10-17）

- 主代理直接处理，未委派。自动账户从每轮两次整面板替换改为固定控件和分区更新；内容不变不替换明细，变化时保留表格两轴滚动与焦点。页签使用 roving tabindex。读取增加15秒超时，文档隐藏时停止定时器并取消读取，旧响应不能覆盖恢复后的状态；权限拒绝清除隐藏值。过时快照不再显示绿色已连接。
- 信号表现图先比较数据与尺寸，再生成配置 / resize；观察器仅在可见表现分析子页启用，离开取消待执行帧，闲置60秒释放实例。浏览器离开94秒后 SVG 从2变0，返回恢复2张。读取阶段明确显示读取中，不再暂时显示0笔。统计口径和完整退出序列不变，记录页仍为卡片。
- 均线密集在首次目录 await 前建立导航版本；逆序响应不能清空后选周期。未检测周期清除旧形态和加载状态，异步切币不再把焦点抢回旧币种。专用账户返回改为同工作台页签操作，保留币种、周期、历史任务和视角，不强制改为最新总览。旧界面实测 CT/15m 变为 AERO/5m；修复后桌面 CT/15m、手机 APR/5m 均原样返回。
- 验证：446项前端测试通过，4个修改JS语法通过；真实浏览器检查信号搜索/清除/卡片详情/累计R与回撤、图表闲置释放和恢复、六条均线/连续切币切周期、专用账户历史与键盘End页签、自动账户轮询。自动账户表格焦点保留，页签nodeId86跨轮询不变；390px账户与检测页无整页横向溢出，信号两图重排为332px。
- 原始验收在 output/frontend-high-frequency-20261017/：frontend-tests.txt、browser-checks.json、service-health.json及三张截图。浏览器error/warn为空。IAB临时tab13已关闭并核对列表为空，viewport已恢复；Owner Chrome1277和网页/执行/巡检服务保留，未重启服务，未触发预览/确认/撤单/扫描/计划变更。沿用main，未提交共享混合工作区。

## 最新进度：前端性能专项优化（2026-10-17）

- 完成 Owner 要求的本轮七类性能优化及页面样式按需加载：494 币全市场仅挂载可见行并保留全部排序/搜索、70px 行高、完整滚动范围与键盘 Home/End；缓存上限 chart/context 各16、spark32，离开60秒释放；同 URL 读请求合并并按页面取消。
- 市场/检测图使用增量 series 更新，无变化不提交，较早历史修正/滚动窗口仍全量正确替换；六条源均线数值不改。检测十字光标使用时间索引，读数按帧合并且同一根数据不重写。日期格式器复用。检测结果不变时跳过币种列表构建。
- 检测内部模型/历史/巡检页不再轮询账户；结果顶部只读 summary，账户页继续完整读取。GET summary 保留原账户新鲜度及 entry_status，仅返回权益，未改变执行门、逐笔确认或两个账户隔离。实际未压缩响应398B vs full36827B；模型页78秒账户请求0次。
- 市场主图不等 context/history，研究数据仅打开历史页时读取；区域字符串/解析结果缓存且图表子节点独立保留。浏览器核对切换历史/笔记/最近记录主图 nodeId84不变。全局增强监听收窄到当前页面的结构变化，隐藏运行面板不再周期重建；两份专属CSS改按路由加载并保留原级联位置。
- 验证：434项前端测试通过，139项手动账户/执行回归与层间边界测试通过；10个JS语法通过。真实桌面与390px检验全市场涨跌排序/搜索/End末尾/移动抽屉选币、六均线、账户摘要到完整账户、市场分区与信号卡片。CSS固定行高在浏览器逐行实测70px；未安装额外解析依赖。
- 本机全市场空闲采样，原56.08秒主线程任务3195.2ms；最终60.78秒556.3ms，按时长折算约下降84%。初次优化样本61.18秒395.7ms（约89%）；不同页面访问状态有差异，不作为FPS或长期内存泄漏结论。原始结果在 output/frontend-performance-20261017/，无额外报告。
- 最终截图 `output/frontend-performance-20261017/market-final.png`。IAB 临时 tab12 已关闭并核对列表为空，viewport 已恢复；控制台无 error/warn。收尾再次确认账户/巡检服务可读，原四组周期与 enabled 保持。
- 网页 LaunchAgent 28425 正常TERM后恢复为38026以启用摘要接口；自动28449、手动21051、YOLO13522、跟单23135与Owner Chrome1277保留。两执行心跳新鲜无错，四巡检仍enabled、15/15/240/1440分钟；扫描1960/1960 idle。未发送预览、下单、确认、撤单或巡检变更请求。本轮主代理直接完成，未委派、未切分支/建worktree、未整体提交共享混合改动。

## 最新进度：V13.1 30m / 4H 各方向倒数20币（2026-10-17）

- 原15m/1H实验无30m/4H账本，本轮复用已迁移638币Binance5m历史补跑1276流；2023-01-01至2026-09-23 04:00 UTC，Nov2022预热、原回踩/止损/2R/4ATR/20bp。7045闭合、27截断；无下载、无新内容哈希。
- 全历史事后各方向剔除20币：30m多358.525265→496.184927R、空47.471190→182.025298R；4H多56.414181→107.222011R、空-16.971899→22.974670R。后验删亏损不构成后续优势；剔除后四组匹配子集超额仍为负。
- 仅用2025年前已结束交易选名单，再看后期：30m多269.073823→261.217051R（均R微升），空-95.790124→-85.186731R；4H多-48.837258→-48.479651R（均R/PF更差），空-32.226435→-34.071175R。4H逐币中位数仅1笔，不能把名单视作稳定差异。
- 28项测试、BTC/ETH旧15m的59笔和信号中心30m/4H的41笔生命周期对齐；1699逐币方向行、8名单、24比较组及13952独立标量核对通过。全期按名单重放占用，新增/替换均0，保留6575笔、808.406906R。未将30m/4H名单加入信号中心。
- 结果与80行中文剔除名单在experiments/active/exp-spike-v131-30m4h-bottom20-20261017-v1/results_v1/；builder31ad577166先提交后跑。原始stream本机保留，合并账本/CSV/收据登记；经验docs/learnings/live-timeframe-support-does-not-prove-backtest-coverage.md。研究与生产资格false，默认不另建报告。

## 2026-10-17 高频模块缺陷修复

- Owner要求继续查高频模块bug与交互细节，主代理直接完成，未委派。范围：市场放大图、信号筛选/页签/卡片、均线检测切币切周期、专用账户只读明细。
- 实际复现并修复：币种候选第一次向上少选末项；裸信号路由残留旧筛选，pane/layout切换缺少返回步骤；市场放大图的搜索快捷键穿透；账户5秒轮询丢失链接/表格焦点；检测切周期焦点掉到body、切币后展开状态标错。卡片详情按信号身份保留刷新焦点。
- 市场放大改为原生dialog，移动原图表节点，补币种标题、Esc退出提示、背景滚动/搜索隔离及路由退出清理。信号新增只清搜索的按钮。账户固定标题/入口、保留表格双向滚动与订单按钮身份，金额统一两位展示，表头/币种列固定、手机页签44px；计算和交易逻辑不变。
- 426项前端测试通过（新增9项），7个JS语法、2个CSS解析、定向diff检查通过。真实浏览器验收默认路由、页签返回、候选首末、清空、图表Esc/返回、桌面与320px、账户轮询焦点/滚动、检测周期焦点与栏状态；证据在 `output/frontend-bugs-20261017/`，未另建报告。
- 无交易/预览/扫描/巡检变更、无服务重启。核对信号10条、手动6条，手动请求confirmed6/preview11/superseded17不变；账户errors为空，四巡检仍启用15/15/240/1440分钟。没有整体提交共享main的混合改动。测试IAB tab10已关闭，标签数0，viewport恢复；Owner Chrome和后台服务保留。

## 2026-10-17 前端细节再整理

- 本轮按实页问题改市场、信号与均线工作台及共享焦点/读数组件。市场周期移入图表工具栏，搜索清空/Esc/方向键、全市场单 Tab 入口；手机选币后焦点回标题。共享导航焦点包含输入/选择框。
- 信号筛选移到卡片上方，桌面1487px三列卡片；手机折叠仍显示具体条件，提供清除筛选，卡片显示绝对收盘时间。尊重此前取消 TradingView 跳转的约定，去掉残留“点击展开K线”提示。
- 均线工作台可收起币种栏并记忆；合并工具栏，跟随行情/重新检测进入设置；手机去掉重复币种下拉与周期标题，页签横向滚动。六均线显示精度变为六位有效数字，源数值/计算不变，完整值在提示。
- 读取失败提示增加中文自动重试说明，恢复后由原刷新流程清除。验收中服务曾重连，确认后台恢复，未由本任务重启。
- 417项前端测试通过，JS语法/CSS解析与diff空白检查通过。实测市场494币涨跌排序、清空/Esc/方向键/首末项、信号筛选清除/详情、币种弹窗/焦点/栏展开、图表设置，检查1487/1024/390/320px及信号浅色。证据在 `output/frontend-details-20261017/`。只核验本轮改动路径，不代表重新验收全部业务流。
- 未提交订单/启动检测/修改巡检。收尾账户 errors=[]，信号10条、手动6条；手动请求 confirmed6/preview11/superseded17，四计划仍启用15/15/240/1440分钟。后台运行保留。

## 最新进度：全站共享动效层（2026-10-17）

- Owner指出整个前端缺少动画。本轮主代理直接完成，未使用子代理。新增 `workspace-motion.js/css` 与共享 120/220/280ms tokens：路由淡入、冷加载首个可用内容、信号最多六卡错峰、任务页签、原生弹窗进出、研究抽屉、导航滑动、说明展开、按压/悬停和加载提示；市场记录分区、信号工作区与 VLM Shadow DOM接入。顺带修正全局搜索框宽度和焦点样式。`DESIGN.md`同步动效契约。
- 动画不等待业务提交或焦点切换；不克隆表单/图表，不重绘K线，不给报价做数字滚动。轮询不重播卡片入场。快速切页撤销旧动画/首屏观察，减少动态效果覆盖 JS、backdrop、details伪元素；关闭弹窗立即停止拦截指针。
- 验收：414项前端测试、2项资源缓存测试通过；3份CSS解析及JS语法通过。真实浏览器检查27路由入口及共享动效资源，另实测市场记录、信号卡片与刷新、均线页签/方向键/新建检测弹窗（未启动）、VLM页签、390px导航抽屉/焦点/筛选、原生说明与reduced-motion。保存计算样式中间帧（非仅截图断言），证据 `output/frontend-motion-20261017/browser-motion.json` 及同目录截图/测试。27入口检查不等于重做每页全部业务流程；外部TradingView仍有原有contentWindow异常，未修改其组件。
- 收尾IAB临时tab7关闭，tabs为空，viewport与媒体模拟恢复；Owner Chrome1277、网页21018、手动21051、自动97511、YOLO13522和跟单23135保留。未重启服务或发交易操作；最终心跳正常、errors为空、4巡检仍enabled且15/15/240/1440分钟。期间运行账本自然变化至自动10/手动6、confirmed6，不能宣称账本未变；本轮没有确认/预览新单。经验 `docs/learnings/motion-must-follow-user-intent-not-polling-renders.md`。共享main混合修改未整体提交。

## 最新进度：均线密集手动开仓阻塞修复（2026-10-17 19:23 北京）

- Owner反馈持续“正在核对账户与原订单”。实际有两层原因：手动worker仍报account-isolation旧revision，而API比较已更新的自动信号revision；另SAND系统212张后被外部加1744张，再以reduce-only单全平1956张，OCO canceled，本地仍exit_needed。真实只读GET确认当前无仓位，没有为修复发交易所POST。
- 新增manual_reconciliation，只在唯一系统lot、两次账户平仓读、原保护结束、无该币待成交单、入场后的完整reduce-only成交证明均齐备时结束本地旧单；保留完整回执，不分配外部合并平仓价格或PnL。其他冲突继续阻塞。手动与自动revision分离，手动为manual-flat-reconcile-20261017-v1。
- 新增GET readiness，账户连接/执行就绪/具体阻塞分开显示。工作台顶部常驻账户状态；票据阻塞说明放顶部，转到持仓与订单，GET轮询恢复不自动创建或确认订单。订单风险/保证金/张数移到设置旁，手机先参数与金额再图表。
- 312项执行层与边界回归、408项前端回归通过；后补2项版本/API测试后的20项对账专项、最终55项前端定向检查通过。真实浏览器FIL两次预览均到可确认按钮，未点击确认；3次原确认、3条执行记录保持，preview11/superseded17由这两次只读报价预览产生。SAND已closed/external_flattened，errors=[]，自动账户9条保持，四巡检15/15/240/1440仍启用。
- 原手动91058正常SIGINT退出后通过原LaunchAgent启动21051；网页原20789正常退出，KeepAlive重启21018（此前本轮网页19286→20789）。自动97511、YOLO13522、跟单23135、Owner Chrome1277保留。无配置/阈值/仓位/巡检变更，原确认不清空。测试IAB tab6关闭，viewport恢复，临时标签0。截图和健康证据在output/manual-entry-20261017/。未整体提交共享main混合改动，未使用子代理。

## 最新进度：全站终端布局深化与市场第二轮优化（2026-10-17）

- Owner要求继续重构全部页面，并继续优化市场；本轮遵守“暂不使用子代理”，由主代理直接完成。共享 main，无分支/worktree，无服务重启或交易操作。
- `workspace-terminal.css` 统一主记录区 + 264px 侧栏、表格/详情、阅读画布和移动布局；各模块模板明确布局区域。`workspace-panels.js` 提供稳定 DOM 的任务标签、键盘切换、手机筛选折叠与说明状态记忆。覆盖信号、数据集、模型、因子、实验、回测、策略、YOLO/VLM、知识/内容/书架/阅读、个人交易/模拟/Arthur、通知/运行。平台与运行页分区化，个人交易把当前任务提前；模拟运行设置右置，结果列表有界；Arthur 处理记录与采集依据分栏。没有修改交易参数、准入门或执行逻辑。
- 市场增加真实 MA/成交量系列开关和重置，优化时间轴、报价层级及记录区；窄图初始/重置显示 60 根，桌面 100 根。继续使用现有真实行情、观察记录和版权标识。修正体系业务入口的 `research` 路由白名单遗漏、历史入口返回路径、手机长状态标签溢出和模型表头列对齐。
- 验收：401 项前端测试通过，15 个相关 JS 语法、3 个共享 CSS 解析及定向 diff 检查通过。27 个主路由在 1487px / 390px 实际访问，最终两组整页横向溢出均为 0；4 个关键路由另验 1024px。3 个历史入口保留运行管理上下文。实测市场图表开关、平台/运行分区、Arthur 键盘页签、筛选折叠保值、知识草稿跨页保留、深浅主题及模拟区间图；临时草稿通过刷新清理，未保存记录。截图、布局、交互原始记录和测试输出在 `output/site-terminal-20261017/`，无另建报告。
- 限制：外部 TradingView 嵌入仍出现第三方 `contentWindow` 异常；内置区间图已加载真实 OHLC，测试后恢复原图表偏好。VLM 余额不足与 Arthur 心跳缺失仍按后端实情展示，没有借 UI 调整清除异常。
- 收尾临时 IAB tab3 已关闭，viewport 已恢复，工具会话无残留测试标签；未新开 Chrome，Owner Chrome PID1277 保留。原 7 个进程仍在，信号/手动账户不同，账本 9/3，confirmed3、preview10、superseded16 均未变化；4 巡检仍 enabled，周期 15/15/240/1440 分钟。手动账本原有归属错误仍保留，未处理仓位。最终健康证据 `final-after-browser.json`。
- 规范同步到 `DESIGN.md`；必要经验 `docs/learnings/workspace-redesigns-must-preserve-state-and-verify-route-edges.md`。未整体提交共享目录的混合改动。

# HANDOFF — 给下一个会话/模型的执行路线图

## 最新进度：V13.1 逐币多空历史排名（2026-10-17）

- Owner已明确授权把四组全历史倒数20币用于信号中心（非前期名单/合并名单）。新增`signal-universe-20261017-bottom20-v1`，保留原全周期10币；扫描新记录、通知发送前、自动源、准备/提交和等待重试均按周期方向过滤。旧记录/仓位继续退出，手动账户保留原范围。377项后端、40项前端通过；广门禁另有8项既有文档/归档路径/旧登记失败，证据保留。自动97511已退出，新28449运行新revision；网页原21018已退出，LaunchAgent新28425、扫描28432；手动21051保持。原账户配置、执行/通知切点、10/6条账本及原订单ID保留；四巡检仍enabled，15/15/240/1440分钟。实时API核对四份20币名单、5条旧记录不标新鲜，扫描当时474/1960零错。原始证据`output/signal-bottom20-20261017/`；Notion追加唯一键`signal-center-bottom20-20261017-v1`，研究证据等级保持不确定。无测试订单/通知，无浏览器测试，无整批提交共享混合改动。
- Owner追问剔除收益倒数20币：按各周期多/空独立全历史名单剔除后，15m多/空+243.22/+658.09R、PF1.080/1.213；1H多/空+212.59/+289.99R、PF1.225/1.395。此为事后删亏损。用2025年前已闭合交易选名单再看后期，总R变化依次+39.06/-15.54/+10.22/-6.97；15m空头平均R/PF仅微升，1H空头均下降，不能冻结为已验证黑名单。另存每周期多空合并一份20币名单的结果。原始bottom20_20261017 JSON/CSV与独立1044项复核同目录，builder先提交b3c4d79a6f；经验`bottom-symbol-exclusions-need-future-period-denominators.md`，无生产规则变更。
- Owner追问统计含义：已补算集中度与2025切点前后持续性，原始`ranking_diagnostics_20261017.json`及独立复核同目录。15m多头去掉最大单后从+16.71R变-37.21R；1H空头去掉最大1%盈利交易仍+112.50R。前段累计R前十币在后段15m多/空为-10.07/+12.79R，1H多/空为-6.78/+0.11R；前后逐币平均R排名相关-0.146至+0.120。仅描述性回看，匹配对照稀疏，不支持把全历史榜直接变白名单。经验`symbol-profit-ranks-need-chronological-persistence.md`，原资格不变。
- Owner要求15m/1H按币种、方向汇总并按累计净R降序。复用原始结果，共2307行分组、14502笔已结束交易；15m多/空5372/5773笔、+16.706171/+471.305497R；1H多/空1765/1592笔、+111.077235/+198.932209R。完整CSV与可筛选Excel在`experiments/active/exp-spike-v131-symbol-summary-20261017-v1/results_v1/`，保留原成本20bp，资格均false。
- 15m只取both/closed，避免重复计单向实验，20笔censored不算收益。1H是原retest/trail2固定入场退出重放，未重放提前退出后的新增入场；原3357笔包含19笔2022年12月入场（-3.986849R），本次保留并注明实际窗口，不能统称2023年起的同窗结果。
- 原summary对账、独立标准库2307组复算、Excel2311行公式核对及导出结构/视觉检查通过。保留原匹配对照，缺失不补；15m多/空713/787对，1H多/空296/291对。RAVE15m多与ALPINE1H多榜首分别仅3/2笔，主要由单笔53.92R/25.62R贡献，净R公式与原风险对齐，不构成可成交或泛化验证。
- Builder先提交`1c76f9da4a`再运行；两个registry登记，Notion原研究`3fa8856479af8152ae1bca1fb534c805`追加唯一键补充并回读确认。经验`archive-grouping-must-check-actual-trade-window.md`。未做新行情回测、内容哈希、浏览器测试或运行服务变更；没有新报告。

## 最新进度：按 Owner 重新贴出的终端图纠正市场页构图（2026-10-17）

- Owner 指出上一轮网页与概念图不一致。本轮以 `docs/design/references/linear-owner-target.jpg` 为直接视觉基准，新增 `market-terminal.js/css`，将市场页真正拆为 70px 功能栏 + 280px 行情列表、报价/周期工具栏、主 K 线、右侧依据/价位、底部记录。1487×1058 实测主图 x366/y202、829×536，记录 y754；更新 `DESIGN.md` 明确构图优先于只统一 tokens。本轮纠正的是市场页，不把此前 27 路由测试冒充全站逐图还原。
- 搜索、自选增删、六币真实报价和趋势、切币、15m/1H/4H/1D、图表缩放/十字线、时间过滤、本地笔记、移动抽屉与返回导航接入现有页面生命周期。15m 复用已有 context 行情；日线仅用六根连续完整 UTC 4H 聚合，MA 不足显示缺样；过期/更新失败有可见状态。保留图表署名及图标许可证，不复制概念价格或示例记录。原观察设置、收益口径和研究功能保留，未修改后端交易/阈值/巡检。
- 400 项前端测试全过，JS 语法和 CSS 解析通过。实际验收切币/周期、自选、搜索快捷键、笔记保存并恢复空内容、时间过滤、标签浏览器返回、跨工作区回来后自选按钮、390px 抽屉及 1024px 排版；无 console error/warn。修正中屏标题挤压、返回后的重复事件绑定与图表过期提示。最终截图 `output/linear-fidelity-20261017/market-final.png`、`market-mobile.png`，原始布局/测试/健康结果同目录；未生成额外报告。
- IAB 本次临时 tab2 已关闭，viewport 恢复，核对只剩 Owner tab1；无新 Chrome。原七进程全部保留，两个账户不同，账本仍信号9/手动3、confirmed3；四巡检仍 enabled，15/15/240/1440 分钟。两执行心跳新鲜；手动归属错误 `exchange positions do not match owned signal lots` 仍在 errors 中，未处理仓位或清除记录。未触发下单/确认/撤单或服务重启。
- 子代理实际模型档位无法核对后停止，主代理完成；共享 main 未整体提交混合改动。经验 `docs/learnings/visual-fidelity-requires-composition-not-only-tokens.md`。

## 最新进度：全站 Linear 工作区结构与交互重构（2026-10-17）

- Owner 选定第二张 Linear 深色概念，要求全部页面一起重构。`DESIGN.md` 已替换先前 NerdWallet 规范；来源固定为 VoltAgent commit `3b605329240ce735b510037cca322f1831fdd7f8`，参考图及源文档在 `docs/design/references/`。这是社区设计分析，不是官方 Linear 组件库。
- 27 个主要路由改为六个工作区的功能栏 + 上下文菜单，加入菜单过滤、页面索引、分组位置记忆、键盘导航和密度切换。市场改主图 + 右侧判断/价位 + 下方分区；信号改可展开列表；数据集改目录行；研究改主列表/侧流程；知识改列表/编辑双栏；阅读页、通知页、YOLO/VLM 与模拟交易台统一组件和各自布局。VLM Shadow DOM 显式接入共享 tokens。交易台保留图表节点与原表单/账本状态；没有下单、确认、改仓或改变巡检配置。
- 验收：394 项前端测试通过；14 个相关 JS 语法、共享 CSS 解析与定向 diff 检查通过。27 路由桌面及 390px 实际访问，手机 document width/scrollWidth 全为 390。实测搜索、工作区恢复、移动抽屉/选币、数据集展开/文件详情、知识编辑查看、研究详情/Escape、市场分区浏览器返回及深浅主题。必要截图与原始结果在 `output/linear-ui-20261017/`，最终首屏 `market-final.jpg`、`datasets-final.jpg`、`research-final.jpg`、`paper-desk-final.jpg`；没有另建报告。
- 外部 TradingView iframe 本次曾返回空白文档，并有第三方脚本 contentWindow 错误；内置区间图正常。增加可见的重载/切换提示，隐藏时延后初始化，重试入口已操作验证；不宣称第三方图表内容已恢复。VLM 原有余额不足 HTTP429 暂停、Arthur 采集心跳缺失、旧数据集索引缺失记录均如实显示，未自动修改这些服务状态。
- 收尾 Chrome 临时标签 977882588 已关闭，之前的 977882578 也不在列表，viewport 已恢复；使用现有 Chrome，未启动新的浏览器进程。两执行账户仍不同，信号 9 笔/手动 3 笔，确认请求 3；四巡检仍 enabled，周期 15/15/240/1440 分钟。手动 worker 心跳新鲜但报 `exchange positions do not match owned signal lots`，两次只读复查持续存在；没有作为 UI 问题清除或操作仓位。原进程及 Owner Chrome 保留，具体最终心跳见 `final.json`。
- 共享 main 未整体提交混合改动。子代理调用无法核对实际模型/档位后已停止，由主代理完成。经验：`docs/learnings/ui-rebuilds-need-layout-ownership-and-real-state.md`。

## 最新进度：信号中心小幅时钟领先误拒已修复上线（2026-10-17 16:16 北京）

- Owner问两笔自动实盘失败并继续要求直接市价开单。15:45 IMX15m空头、SAND15m多头反复`retry_pending/quote_stale`，交易所ticker领先本机约0.5–1.9秒，原自动`Engine.prepare`将负年龄统一拒绝；手动入口已有的等待修复未覆盖自动入口。CFX15:30空头同因31次重试后16:00到期。两笔都没有入场intent；SAND曾走到杠杆设置，不等于已经发入场单。
- 自动入口现对原preflight容差内（≤5秒）的领先有限等待一次，再按原0–5000ms报价年龄及30分钟信号期限重检；保留真实响应/交易所时间，新增校验时间、等待耗时、原始接收年龄。原`ordType=market`/无`px`保持，多空均只发一次；125U/2%、全仓、最高档位、冻结止损、两账户隔离与未知POST只对账保持。
- 44项定向测试、294项执行层/层间边界回归通过。真实只读IMX/SAND原年龄-1037/-1561ms，等待1043/1566ms后年龄6/5ms，成功计算1373张20倍/109张50倍；这只是当刻只读报价，没有直接发测试单。
- 16:14:56信号78774正常SIGINT退出并释放锁，原session启动97511，revision=`signal-clock-wait-20261017-v1`。账本未清空，原cutoff=1792158236572及配置逐值相同。第一轮处理时两条15:45信号已过16:15期限，均`skipped/signal_expired`且无intent；没有补开。修复后新自然信号成交尚未验收，不设新AI定时任务。
- 真实GET确认USO0.15多/STABLE20空入场均market/filled，原SL144.61/0.02753均live且方向/数量/归属匹配。16:16两执行心跳正常无错、UID不同；手动91058、网页91400、扫描91407、YOLO13522、跟单23135、OwnerChrome1277保留。4巡检仍enabled、周期15/15/240/1440分钟；扫描1387/1960零错仍进行，未宣称全轮完成。本轮无浏览器，无新增调度。
- 代码只改`engine.py`及对应测试/README，保留共享main其他改动，未整体提交。诊断、真实只读准备、重启与保护/健康证据在`output/okx-clock-diagnosis-20261017/`；经验`docs/learnings/clock-lead-fixes-must-cover-each-execution-entry-path.md`。子代理工具此前无法回显实际模型/档位已停止，本轮由主代理完成。

## 最新进度：均线密集工作台改为紧凑列表并接入成熟组件（2026-10-17）

- Owner 要求币种卡片缩小、周期单行、优化整体交互。本轮范围为均线密集工作台与手动预览。桌面币种行实测63px，四个检测周期同一行；统计/账户压为窄条，K线起点从约665px上移至399px，常用看图按钮保留，显示/导出收进设置菜单。重复说明折叠，手动票据减少留白，价格图优先。
- 实际引入 Bootstrap5.3.8 的按钮/表单/导航/dropdown 与 Tabler Icons3.49.0；`yoyo/monitor/ui/`含锁定依赖与可复现构建，输出本地静态资源，无CDN。CSS限定`.spike-ui`，未导入全站reboot、布局或modal控制器；只加载dropdown+Popper。现有主题token、原生dialog、事件/API与两个账户边界保留。
- 桌面/390px/深色真实浏览器通过：列表周期单行，宽度无溢出；菜单支持Esc/方向键/外部关闭，勾选不收起；手机选择周期关闭picker并将焦点回图表。修正打开picker自动弹搜索候选遮挡列表，手机输入16px、点击区域38–40px。真实AKE多头只读预览成功，六均线和盈亏框可见，未确认或下单。
- 70项相关前端测试、JS语法与定向diff检查通过；证据`output/compact-ui-20261017/`，最终桌面`07-final-desktop.png`、手机`03-mobile-picker.png`、深色`04-dark-menu.png`、票据`05-ticket-desktop.png`/`06-ticket-mobile.png`。无浏览器error/warn。后台无需重启；两个执行心跳正常、UID不同、手动账本仍2条旧记录/confirmed仍2，4巡检政策不变；扫描860/1960零错仍运行。未新增定时任务。
- IAB临时tab11已关闭并核对列表为空，viewport恢复、主题恢复跟随系统，无新Chrome；网页91400/扫描91407、执行78774/91058、YOLO13522、跟单23135和Owner Chrome1277保持。子代理实际模型档位无法核实后停止，主代理完成；共享main未整体提交混合修改。经验`component-adoption-must-preserve-workflow-and-density.md`。

## 最新进度：六均线止损、独立实盘账户与自动回执已上线（2026-10-17）

- Owner 最终确认默认止损为最新已收盘 5m 的 SMA/EMA 20/60/120：多头最低均线减 1 tick，空头最高均线加 1 tick，再向外取整。此前单根 K 线极值、波段 2L/2R 方案均被替代。`manual_ma.py` 复用 workbench close 指标与 1,000 根预热，公共参考、图表和账户报价共用闭合快照；图上显示六线、边界来源及多空盈亏框。确认后的点位不自动漂移，实际成交后仍按固定 SL 重算 3R/5R TP。
- 均线密集新增独立“实盘账户”页：总权益、可用保证金、浮盈、3% 风险，持仓、普通挂单/保护、最近 7 天最多 100 笔交易所历史、最近 50 条系统开仓记录；历史失败不抹掉有效持仓。复用信号中心展示和安全序列化，明确只读手动 runtime，无信号账户回退。全量非终态自有账本决定归属，原有外部仓位仍显示自行管理。账户自动刷新；弹窗确认后每 3 秒查询，按钮改“查看持仓与订单”，不再要求点刷新。
- A 与 DOT 两次旧请求均已 skipped，分别因 preview expiry / expiry before leverage；无 entry_price。A 确认后约 95 秒才被处理，旧手动 worker 的空闲串行交易所读取阻塞了队列。现空闲先检查队列，零自有仓且无新确认时不发 GET；有请求仍执行完整新鲜检查。独立 ManualClient 仅对 GET 连接复用、每阶段并行/去重，POST 不重试；信号执行路径不改。真实只读账户+归属读取 3.584 / 2.344 秒，不是完整成交时延承诺。开仓已有 `ordType=market`、无 limit px，增加明确断言。
- 最终 285 项执行层/边界测试、63 项相关前端测试通过。浏览器真实 CT 空头预览：15:40 收盘 SMA120=0.3757291667 为最高线，tick=0.0001，SL=0.3759；参考价0.3437、3R TP0.2471、16张/20倍/保证金2.7496U。截图 `output/manual-ui-20261017/ma-live-ct.png`；已保存报价来源 `ma-live-preview-last.json`。未确认、无真实交易写请求。模拟长/短、3R/5R、390px及自动状态推进通过，模拟成交不能冒充真实成交。
- 真实独立账户页与历史/保护已验收，截图 `account-live-final.png`、`account-live-history.png`。自动更新时间从15:45:59推进至15:46:19；期间交易所账户 SAND 在15:45:42出现市价卖出成交，随后只剩 FIL，此为外部账户变化，不是 QA 发单。系统账本仍仅两条旧 skipped；不宣称新手动成交与保护已真实跑通。
- 手动 worker 80262 正常退出后重启为91058，revision=`responsive-manual-20261017`；主站88237→91093→91400，扫描91407沿用checkpoint。信号78774、YOLO13522、跟单23135、Owner Chrome1277保留。最终两账户UID不同、心跳正常无错，4巡检策略/频率/enabled不变，无新AI定时任务。收尾扫描653/1960、零错，未宣称本轮已完成；证据 `account-ma-final-health.json`。
- 8769模拟服务90768已精确退出且端口关闭，旧web/scan/手动worker均退出。IAB最终验收tab10已关闭，viewport已恢复，未创建Chrome；重载瞬间tab9成为data错误页，关闭被浏览器URL策略拒绝，未标记保留，待回合自动清理，不能宣称所有临时页已核实关闭。共享main未整体提交混合改动。子代理实际模型/档位无法核实后停止，由主代理完成。
- 经验：`manual-queue-checks-must-precede-idle-network-reconciliation.md`、`order-ma-boundaries-must-share-the-chart-snapshot.md`、`reuse-account-presentation-without-sharing-account-state.md`。保留每笔Owner确认、远程入口授权及原账户隔离，不接管外部仓位。

## 最新进度：核对5m止损来源并修正时间标注（2026-10-17）

- Owner质疑高低点。当前规则仍是Owner先前选择的“最新一根已收盘5m K线”，不是5m图上的波段高低点。已发异步问题确认其语义，尚未收到答复，不自行改变止损/仓位参数。
- 对保存的PARTI原报价时间直接读取OKX原始历史数组，15:00–15:05高0.03345、低0.03330，与stop_candle及图表逐值一致；未发现该例OHLC取错。发现提示原只写收盘15:05、横轴用开盘15:00，容易被理解为另一根K线。按钮改“上一根5m K线”，显示完整开闭区间；图上箭头绑定open_ms，只有实际OHLC与止损来源两端一致才标记，缺失或不一致明确提示。
- 22项针对性前端检查、JS语法通过；真实浏览器PARTI15:05–15:10高0.03352/低0.03337的来源箭头验证通过，未确认订单。静态代码已生效，无服务重启。截图`output/manual-ui-20261017/stop-source-live-parti.png`；原始核对`stop-candle-audit.json`；健康`stop-source-final-health.json`。
- IAB临时tab8已关闭、未修改viewport或开启Chrome；账户仍隔离，两个执行worker、YOLO、跟单、网页与Owner Chrome保留，四巡检策略/周期不变，无新增AI定时任务。子代理调用未回显实际模型/档位，已停止，由主代理完成。经验`docs/learnings/candle-stop-labels-must-use-the-chart-time-anchor.md`；共享main未整体提交混合修改。

## 最新进度：先显示点位 K 线，资金检查失败也保留图表（2026-10-17）

- Owner反馈PARTI做空的保证金拒绝仍只显示错误，要求先看到K线、止盈止损。新增只读公共`/api/manual-live/reference`，独立于账户/worker资格查询；图表立即开始读取，即使无价格也显示真实K线。公共参考只含展示字段，不创建确认身份或队列记录。失败回执附带该次准确三价，优先于独立公共报价，避免错配。市场越过止损时保留真实市价/止损，不伪造止盈框。
- 页面在核对资金期间显示参考框，失败后保留图、三价与原因；3R/5R更新、编辑/切币/关闭时废弃旧结果，参考过期明确标记。手机首屏优先图表，390px无横向溢出。成功订单仍需Owner确认，未改风险/杠杆/止损规则或账户隔离。
- 275项执行层/边界检查、62项相关前端检查通过。浏览器真实PARTI先出现行情图，再返回47,990张超过13,000单笔市价上限（该次参考0.03344、SL0.03345、3R TP0.03341）；截图原单则需388.43U/可用101.23U。先前本轮只读诊断也复现PARTI保证金不足，所需228.55286U、可用105.06694U，随行情不同不可混称原单。独立公共点位冷读13.724s、HTTP复查8.5s；K线暖读可先显示，并非固定秒数承诺。
- 真实截图`output/manual-ui-20261017/reference-live-parti.png`；图及拒绝详情`reference-live-parti-detail.png`；模拟长短/3R5R/资金延迟和手机证据`reference-*-fixture.png`。真实读取和健康在`reference-live-parti.json`、`reference-http-parti.json`、`reference-final-health.json`。模拟账户不会连接执行器，本轮从未调用确认或交易所写请求。
- 仅网页86612正常SIGINT重载为88237，扫描88244保留checkpoint；收尾1856/1960零错，不宣称本轮扫描已全部完成。两账户UID不同，心跳正常无错，四巡检策略/周期/enabled保持；78774、80262、13522、23135、Owner Chrome1277保持。IAB tab7关闭、viewport恢复，8769模拟服务88070已精确停止，旧web/scan退出。
- 注意：收尾账本已有1条并行确认的A做多记录，状态skipped、原因manual preview expired or policy invalid、无entry_price；不能继续沿用上轮“confirmed=0/trade_count=0”的结论。这不是本轮浏览器验收提交的订单；未调整执行期限或管理现有仓位。
- 经验见`docs/learnings/reference-charts-must-survive-order-eligibility-rejection.md`。共享main未整体提交混合改动，无新增AI定时任务。

## 最新进度：手动订单加入 K 线与多空盈亏框，保证金拒绝显示明细（2026-10-17）

- Owner截图A做多/最近5m提示保证金不足，后又提供CT成功预览截图并要求K线多空框。真实只读复查A：总权益159.6255U、可用90.4515U；买价0.11302、SL0.11247，870张、最高10倍，需98.3274U、差7.8759U。BEAT同轮需119.1272U、可用90.1906U。不是漏用最大杠杆或重复扣减；这些是复查时数字，不能冒充截图时行情。保留3%/原止损/最高档位，不自动缩仓或拆单。
- 新`InsufficientMarginError`保留原异常文本和拒绝门，结构化409显示预算、张数、最高杠杆、所需/可用保证金及缺口。API仍不发交易写请求。
- 新`manual_chart.py`和`manual-position-chart.js`：公共显示入口复用shared candle hub，100根已确认连续5m K线，缺口/缺最新失败；预览图默认聚焦最近一小时，可缩放/拖动/还原。盈亏框绑定本次服务端入场/SL/TP，多空自动镜像，3R/5R重预览；不画未来K线。图表与订单并行读取，20秒超时、重试、切币/关窗取消与晚响应隔离，图表失败不延长或改变订单有效期。
- 后端268项执行层/层间测试、前端50项通过，后续图表超时/视角调整后定向20项再次通过。模拟订单验收多头/空头、5R、错误提示、图表失败、390px深色无横向溢出；真实CT空头默认止损预览成功：398张、20倍、入场0.3499、SL0.3511、3R TP0.3463、保证金69.6301U。真实图表100根至14:50北京，浏览器无error/warn；60秒到期后确认按钮消失，只有更新报价。未确认，手动trade_count=0/confirmed=0。
- 只重载主站84249→86612，扫描86628，收尾1621/1960零错；信号78774、手动80262、YOLO13522、跟单23135、Owner Chrome1277保持。账户UID不同、心跳正常无错误，四巡检spec/enabled/周期不变，无新增AI定时任务。IAB唯一临时tab6已关闭、viewport已恢复，列表为空；8769隔离服务86428已精确停止、端口关闭，没有新Chrome。
- 真实截图`output/manual-ui-20261017/position-chart-live-ct.png`，模拟手机截图`position-chart-mobile-fixture.png`，原始诊断`margin-diagnostic-a-beat-cfx.json`，健康`position-chart-final-health.json`。经验`margin-rejections-need-both-risk-and-collateral.md`、`order-charts-must-not-become-execution-inputs.md`。共享main未整体提交；子代理工具未回显实际模型后停止，由主代理完成。

## 最新进度：预览再次提速，BEAT 市价上限提示可核算（2026-10-17）

- Owner反馈5秒仍慢，并报告BEAT做多、最近已收盘5m低点止损预览超单笔市价上限。只读核对合约`ctVal=10 BEAT`、`maxMktSz=7000张`、`lotSz=0.1`，单位没有读错；失败当刻报价未留存，不能声称完整重现原单。实查权益约157.4722U；明确假设买价0.07697、SL0.07691时，3%预算4.724166U需7873.6张，确实超过上限。真实复查时市价已低于默认止损，返回另一正确拒绝。
- `MarketOrderLimitError`保留原异常文本/拒绝条件，额外返回预算、两价、距离、合约面值、所需张数与上限；API结构化409，页面显示具体计算明细。没有自动降风险、拆单、加宽止损或修改杠杆，两个账户及3%/3R5R规则保持。
- 预览改为接口级限速（同接口类型/分页共桶），ticker与账户查询重叠；使用时仍检查原0–5000ms年龄，过期重新取价，失败不退回旧报价。连接保活120秒不缓存业务响应，每次仍检查余额/持仓/全量挂单/档位。只读同进程实测冷5.905秒、暖2.068/2.410秒；上线后真实HTTP为6.229/3.442/2.541秒。首次连接与网络尾延迟仍存在，不承诺每次2秒。
- 261项Python执行层/层间回归、42项前端检查通过，JS语法与定向diff检查通过。模拟BEAT结构化错误的桌面及390px页面验收可读、无横向溢出；截图`output/manual-ui-20261017/beat-limit-explanation-fixture.jpg`明确为模拟数据。真实HTTP只读预览成功，未确认、未发送交易写请求，手动trade_count=0/confirmed=0。
- 仅主站82208正常SIGINT重载为84249，扫描84257保留checkpoint，收尾1690/1960、零错，未宣称新轮全部完成。信号78774、手动80262、YOLO13522、跟单23135、Owner Chrome1277保留；两账户UID不同、心跳正常无错，四巡检spec/enabled/周期不变。没有新增AI定时任务。
- 临时IAB tab5已关闭、viewport已恢复，未创建Chrome；8769模拟服务84181已精确停止，端口与旧网页/扫描进程已核实退出。耗时与健康原始证据在`output/manual-ui-20261017/preview-latency-v2-*.json`、`preview-v2-final-health.json`。经验见`preview-concurrency-must-follow-endpoint-budgets.md`与`risk-budget-rejections-need-contract-arithmetic.md`。共享main未整体提交；子代理工具未回显实际模型后停止，由主代理完成核验。

## 最新进度：手动订单预览提速已上线（2026-10-17 14:20）

- Owner反馈“预览做空/做多计算订单太久”。根因是14次串行交易所GET、重复3次余额和反复HTTPS建连，旧耗时约49.4秒。新增`preview_reads.py`，4路并行、同一预览内余额去重、按合约依赖预取档位，ticker最后读取；每次预览重新获取账户/持仓/全量分页挂单，服务只复用连接而不复用业务响应或认证/cookie。
- `Service.preview`使用独立engine副本，预览缓存不能进入执行器。仍无交易所POST，手动确认、两个runtime/UID、3%/3R5R、闭合5m止损、原报价与确认期限、健康检查和执行前重检保持。连接池通过路由lifespan关闭，兼容主站自定义lifespan。
- 252项执行层及层间回归通过。真实只读3次耗时7.409/4.838/5.096秒，均12次GET；上线后HTTP为8.073/5.354秒。真实CFX15m页面预览成功（自定义SL仅验算、未确认）；另记录一次查询503后重试恢复，默认止损过近的保证金不足正确拒绝，不宣称网络永不超时。没有真实下单验收。
- 网页PID80271正常SIGINT重载为82208，扫描子进程82215，checkpoint保留，收尾1473/1960、零错；信号78774、手动80262、YOLO13522、跟单23135保持。两个账户UID不同、心跳正常无错误，手动trade_count=0/confirmed=0，四个巡检spec/enabled/周期不变。未新增AI定时任务。
- 原始耗时、复现脚本和真实截图在`output/manual-ui-20261017/preview-latency-*`、`measure_preview_latency.py`、`preview-speed-live.jpg`。仅创建IAB临时tab4，已关闭并核实列表为空；前轮遗留错误页也已由回合清理移除，未创建Chrome。共享main不整体提交混合改动。经验见`docs/learnings/preview-latency-is-a-network-dependency-problem.md`。

## 最新进度：信号中心与均线密集已隔离为两个账户，手动入口已重做（2026-10-17）

- Owner纠正：两个入口是两个不同账户，不是替换全站账户。**本节取代下方“新账户替换默认/旧账户只管理”的运行结论**。原信号账户已恢复到 `~/Library/Application Support/Fable/OKXExecution`，原凭据/账本/cutoff=1792158236572及125U/2%策略保持；默认CLI选择Engine，只消费自动信号。`com.fable.okx-execution.session` / PID78774继续开新信号及管理原USO、STABLE。
- 新凭据仅在 `~/Library/Application Support/Fable/OKXManualExecution`，独立execution.sqlite3/manual.sqlite3、锁、日志、配置与worker；`com.fable.okx-manual-execution.session` / PID80262 / `worker-manual.plist`，只消费逐笔确认。UID绑定 `manual_account_policy` version=1/mode=manual_only_shared；专用runtime缺政策禁止启动，默认信号runtime含手动政策也禁止启动。两账户UID只比较不输出，已确认不同。旧previous目录已恢复回默认，previous管理标签已卸载，不能重新启动遗留plist。
- Owner确认现有FIL/SAND多仓和BTC/ETH挂单继续自行管理。系统不接管；任意外部仓位/普通单/算法单占用该币种时拒绝新单（含反向仓），系统后续改保护/退出前精确核对自有币种数量和外部订单。分页覆盖全部挂单。并发Owner操作不具备交易所原子预留保证；不得把同币种仓位混合。
- API与UI独立：信号中心账户接口读原runtime；`/api/manual-live/account`及全部手动预览/确认/回执读新runtime。YOLO增加账户条和图表上方预览按钮。弹窗重做设置/确认/成交保护步骤，3R/5R卡片、风险预算/保证金/三价带、固定底部按钮；修改废旧报价，过期不得确认，未知确认只查询原ID。保留Owner允许远程确认的口径，不新增仅本机限制。
- 真实GET测得ticker领先本机571ms、public/time领先1.871秒。手动报价在原preflight的5秒时钟容差内有限等待，再按原0–5000ms报价门与原60秒preview截止重检；没有修改30分钟信号门或伪造时间戳。真实只读CFX自定义SL0.0600诊断成功：88张、20倍、参考0.05476、3R目标0.03904、保证金2.40944U、预算4.63389U、报价年龄6ms；仅验证计算，不是交易建议、没有排队/确认/交易写请求。真实UI默认SL当时保证金不足正确拒绝；旧1秒等待版本另一次UI仍误判，最终5秒版本已重载，最后的UI重载遇到临时浏览器错误页，未冒称最终真实UI成功。
- 回归260项Python通过；最终增加角色防退化与5秒边界后，手动/隔离56项通过；前端51项通过，JS语法/定向diff检查通过。模拟账户桌面及390px浅深色验收覆盖3R/5R、非法/自填SL、报价过期、回执丢失仅一次确认、保护回执、历史返回与焦点恢复。无真实手动成交/止盈止损触发验收；新账户trade_count=0、confirmed=0。
- 主站最终PID80271，扫描80283。前两次重载等1960/1960零错；最后为加载时钟修正，在新轮128/1960零错时正常SIGINT重载，持久checkpoint保留，收尾新轮已到193/1960零错，尚未完成全轮。原信号执行PID78774、YOLO13522、跟单23135、Owner Chrome1277保留；两个执行心跳正常无错误。四巡检仍5m/15m每15分钟、1H每240分钟、日线每1440分钟，没有新AI定时任务。
- 必要原始证据在 `output/manual-ui-20261017/`，无密钥/完整UID。8769隔离服务PID79154已精确停止并确认端口关闭；临时IAB tab3已关。临时tab2重载期间成为data错误页，其导航、关闭和viewport reset均被浏览器URL策略拒绝；未标记保留，回合自动清理之前无法确认关闭/恢复，不能声称全部清理成功。未操作Owner日常浏览器。共享main混合改动未整体提交；子代理实际模型无法确认后停止，由主代理完成。
- 经验：`entry-sources-must-own-distinct-account-runtimes.md`、`manual-tickets-need-visible-commit-and-receipt-states.md`、`subsecond-exchange-clock-lead-can-wait-without-aging-quotes.md`。历史账户替换笔记记录旧误解，不要拿它覆盖本节的Owner纠正。


## 最新进度：OKX 新账户已连接，现有仓位管理范围待回复（2026-10-17）

- Owner 明确授权“用刚发的凭据替换当前账户”。新凭据已通过只读身份、账户模式和权限核验，并保存为默认私有 runtime 的 `credentials.json`（目录700、文件600）；无密钥进入仓库或诊断输出。新 UID 与旧账本不同，期货账户/双向持仓、read_only+trade 权限；网页按凭据文件身份自动刷新，无需重启主站。
- 新账户当时有 FIL、SAND 多仓及对应 OCO，BTC、ETH 买入限价挂单。均未被接管、撤销或更改。新默认账本与手动确认队列为空，未启动新账户执行器，也未伪造账户归属或设置 cutoff 绕过 preflight；不能称为已可开新单。已问 Owner：现有仓位/挂单继续由 Owner 管理、系统仅管理未来确认的新单（推荐），或接管现仓并另指定退出规则。收到回复后继续实现相应归属隔离，不重复询问是否更换凭据。
- 原默认执行 PID75024 优雅退出、锁释放且无未决执行状态后，完整运行目录保留为 `~/Library/Application Support/Fable/OKXExecution-previous-20261017`，meta/trades/journal 迁移前后逐行一致。原 USO 多0.15张/SL144.61、STABLE 空20张/SL0.02753，交易所保护均live。旧账户在 session LaunchAgent `com.fable.okx-execution.previous-20261017` / PID77446 / `worker-manage-only.plist` 下继续管理，`entries_enabled=false`、心跳正常、无管理错误；不再接收新信号。
- 新默认 `OKXExecution/worker-session.plist` 保留待启用；旧 `com.fable.okx-execution.session` 已卸载，不能把旧账本复制到新账户或盲目重启旧默认 plist。私有 `account-switch.json` 记录旧目录和标签。自动策略125U/2%、手动3%/3R或5R规则未改；无测试单、无转账、无新的AI巡检任务。经验：`docs/learnings/account-replacement-must-preserve-position-ledger-identity.md`。

## 最新进度：均线密集手动实盘已接入（2026-10-17）

- Owner规则：1R=当前交易账户总权益3%，交易所允许的最大杠杆；默认SL=最新一根已收盘5m K线低点（多）/高点（空），可手填；TP默认3R、可选5R。每笔必须在形态卡片“手动实盘”预览后点击确认。Owner明确取消仅本机限制，原免登录远程工作台也可确认；same-origin不是身份认证，不要误称已做Owner登录。
- 新增 `manual_source/manual/manual_queue/manual_engine/manual_api.py` 与 `manual-live.js/css`。检测只作为已保存上下文，不promote、不变自动信号资格。网页使用只读交易所客户端，60秒预览冻结账户/风险/数量/保证金/杠杆/SL/R倍数，确认写私有队列；现有单写执行器共用归属账本，附带OCO并按实际成交均价校准TP。未知POST只对账不盲重试；手动单不调用SPIKE反向/跟踪退出。API totalEq为USD，经同回执USDT eq/eqUsd折算，资金账户不纳入。
- 265项Python（含34项新实盘模拟测试）与48项前端检查通过；测试账户浏览器完成5R确认→成交→双保护核验，桌面/390px手机通过。真实HTTP只读RENDER预览：多752张、20倍、SL1.922、TP1.942、风险预算约3.76197U、保证金72.4552U；没有确认/实盘订单。后续真实UI报价越过默认SL时409阻止预览，符合当前价格核验；手填SL1.90后真实UI预览134张/20倍/风险预算约3.75777U/TP2.012成功，仍未点击确认。交易所曾短暂GET超时，拒绝预览后恢复；不承诺网络永不失败。
- 执行PID70880→75024，revision=`manual-yolo-20261017-v1`；原cutoff1792158236572、自动配置125U/2%保持。原扫描1960/1960、0错后重载主站26159→75209，扫描子进程75221新轮1960/1960、0错。YOLO13522、跟单23135、Owner Chrome1277保留，4个巡检spec/频率/enabled一致；没有新AI定时任务。原有USO与STABLE两仓继续原策略管理。
- Owner另贴了一组三段凭据；本节交付时尚未替换，后续已授权并切换，当前状态以顶部账户切换记录为准；禁止复述凭据值。
- 临时Playwright会话`fable-manual-live-20261017`及其8个进程已退出，8769临时模拟服务已关闭；收尾服务心跳正常、实盘确认0条。原始验收材料：`output/playwright/manual-live-20261017/`；经验：`docs/learnings/manual-confirmation-must-bind-risk-and-share-ownership.md`。没有真实新手动成交/TP或SL触发验收；不要为测试发真实单。共享main混合改动，未做整体提交。子代理模型不可核实，任务由主代理完成。

## 最新进度：MINA 漏开缓存观测时钟已修复上线（2026-10-17 12:22 北京）

- MINA 30m 多头 `9ad61fb8a81087b7e04ac3ec`：11:30收盘、11:33:31.512检测、12:08:46.518首次准备，晚于原12:00到期，被记为 `skipped / signal_expired`，没有订单intent。现有USO 4H持仓管理反复报checkpoint stale：扫描器只在闭合K线变化时写seed，执行器却把seed内容修改时间用于30分钟读健康门，正常4H间隔也会阻断所有新信号。旧日志无时间戳，不声称逐秒重建全部阻断区间。
- 修复把成功读取观测单独保存在 `candle_observation:{symbol}:{timeframe}`，绑定checkpoint原修改时间与末根收盘时间；内容不变无需重压缩/重放，失败读取不刷新凭证。执行器沿用原30分钟门和缺失已收盘K线检查；没有扩大时限、绕过管理故障或补开历史MINA。错误/恢复日志加时间。212项相关检查通过；1960次轻量观测写在临时库约7.86秒、0外部请求。新revision为 `checkpoint-observation-20261017-v2`。
- 等原扫描1960/1960、0错完成后，仅重载主站监督的扫描子进程26166→70913；新轮6.27秒完成1960/1960、0错，已保存1960条观测凭证，checkpoint含观测写耗时1.975秒。执行器26035优雅退出后由原session LaunchAgent启动70880，新revision、心跳与无管理错误确认。主站26159、YOLO13522、独立跟单23135保持，四巡检计划enabled/频率不变。无需新增浏览器。运行只读验收保存到 `output/okx-quote-fix-20261017/mina_activation_verification.json`。
- 12:18左右用只读OKX客户端重新确认 STABLE 30m空头20张（均价0.0262900000000001、止损0.02753）和USO 4H多头0.15张（均价160.866、止损144.61）：入场均filled，附带止损均live，方向、数量、trigger和client id逐项一致。未验证真实止损触发/改价/完整退出。原始回执在 `output/okx-quote-fix-20261017/verified_exchange_entries_and_stops.json`。
- Owner随后明确“不需要定时任务了，修好了就行，下次有问题我再叫你修”。刚建的heartbeat `automation`「核验下一条实盘信号和止损」已通过automation工具删除，不再自动跟进。该指令取消的是本次AI定时跟进，不影响既有行情/执行/YOLO巡检服务。
- 本轮服务诊断误输出launchd继承的xAI API key到工具记录，已告知Owner需作废重建；不复制该值，不再输出服务完整环境。OKX凭据未输出。经验：`docs/learnings/unchanged-candle-content-is-not-a-stale-observation.md`。

## 最新进度：OKX 报价误判与入场前重试已修复启用（2026-10-17 02:21 北京）

- Owner 发现新信号没有实盘开仓并要求修复。XLM 01:30/GRASS 02:00 两笔15m空头均被旧进程记为 `skipped / exchange quote is stale`。已修正请求前时钟误判：ticker最后读取、响应后按原5秒门核验；杠杆回读及账户归属复查后重取价并按原2%/冻结止损计算张数，所需杠杆档位变化时本次不发入场单。30分钟信号门与125U/全仓/最高档位等配置保持。
- 尚无入场intent的暂时GET失败持久化 `retry_pending`，2/4/8/16/30秒退避，在原信号到期后停止；旧skipped/rejected不重开，未知POST只对账不重发。账户表格增加身份回退、失败原因枚举与重试时间，不公开原始API错误。191项Python与10项前端检查通过；只读preflight通过，真实GRASS预览71张/20倍/计划风险2.4921U/报价年龄748ms，无该预览的交易写请求。
- 旧执行PID76972已退出，修复版PID26035在当前登录会话的 `com.fable.okx-execution.session` 运行，revision=`quote-retry-20261017-v1`、新信号入场已启用。私有runtime内 `worker-session.plist`，KeepAlive=false，没有安装登录/开机启动；日志在同runtime的 `logs/worker-session*.log`。原终端Ctrl-C不再控制新进程；Owner可用 `launchctl bootout gui/$(id -u)/com.fable.okx-execution.session` 停止，停止不会撤掉交易所仓位/保护，具体见模块README。不能再次启动第二执行器。
- 激活后原cutoff=1792158236572、配置逐值/文件内容、两笔交易行及journal逐行相同，无补单。真实账户125U、0持仓/挂单/保护单；心跳正常、无worker错误。网页PID20498→26159，在1960/1960、零错后优雅重载，新轮同样1960/1960零错；YOLO13522、跟单23135、用户Chrome1277启动时间保持，四巡检spec/频率/enabled相同。仅一临时IAB页，已关闭，浏览器无error/warn；未做实体手机验证。
- 原始核验 `output/okx-quote-fix-20261017/`（本机git排除）；经验 `docs/learnings/slow-entry-setup-needs-a-fresh-risk-plan-before-intent.md`。当前仍未以真实新入场/附带保护/平仓回执完成端到端验收，等待下一条自然新鲜信号；不要为验收补开旧单或造信号。共享main混合改动未整体提交。

## 最新进度：紧凑顶栏与模拟持仓快捷止损（2026-10-17）

- 顶栏桌面 53px、390px 手机 52px；收起按钮移至侧栏左下角，改线框面板图标；桌面展开时隐藏顶部重复按钮，收起后保留重新展开入口。删除原信号版本图标，手机抽屉关闭及焦点恢复已实测。
- 模拟持仓增加“止损到开仓价”。前端提交意图与新鲜 quote_id，服务端写事务内读取当前均价并保留 TP；既有更优止损不后移。方向/报价/持仓关闭校验、幂等回执与导出审计保持。明确不含手续费；不修改用户现有持仓。
- 131 项前端、98 项后端检查通过。隔离账本浏览器实点多空：SL 100，TP 130/70 保留，仅两笔原开仓成交、两条保护回执。手机动作高度 44px、无横向溢出；已修复早期 CSS 优先级导致 36px 的问题。主站嵌入 TradingView 支持链接报一次 403，本站功能验收未发现错误。
- 监控完成 1960/1960 零错后只重载公共网页进程（13512 → 20498，约 2 秒），新监控扫描 1960/1960 零错。YOLO 13522、执行器 76972、跟单 23135、用户 Chrome 1277 的 PID/启动时间前后不变；四计划 spec/enabled/频率及执行配置不变，用户 accounts/positions/fills/limit_orders 逐行相同。
- 证据 `output/paper-entry-stop-20261017/`；经验 `docs/learnings/entry-stop-shortcuts-must-resolve-inside-the-ledger-transaction.md`。唯一临时 IAB tab8 已关闭、viewport 恢复，18867 隔离服务和两份临时账本已清理，未开启 Chrome。未做实体手机测试。主代理完成；子代理实际模型无法核验后立即停止。


## 最新进度：币种周期标签可直接打开图表（2026-10-17）

- 币种行的周期改为独立按钮，一次选择精确币种/周期；有形态高亮并显示数量，当前周期边框强调。点击恢复完整形态筛选并定位最新框，迟到响应仍由 requestVersion 隔离。
- 手机周期按钮高 44px，390px/320px 下实测无横向溢出；选中后关闭弹窗、恢复滚动并将焦点交给图表。桌面轮询保留具体周期按钮焦点，Enter/Space 可操作。
- 34 项前端测试通过；真实 DOS 5m/15m 的识别框 ID 分别与后台结果一致，1H 零形态清除旧框，日线历史不足明确提示，无控制台错误。证据 `output/detection-period-tags-20261017/`；经验 `docs/learnings/period-shortcuts-need-independent-targets-and-focus.md`。
- 仅前端静态资源变更，无服务重启。巡检计划 spec/频率/enabled 与监控、YOLO、执行器、跟单和用户 Chrome 进程启动时间前后相同；监控 1960/1960、零错误。唯一测试 IAB tab7 已关闭、viewport 已恢复，用户 tab1/2 保留；未开启 Chrome。未验证实体手机。


## 最新进度：均线密集检测交互改进已启用（2026-10-17 01:24 北京）

- 按币种合并各周期最新结果。`latest.py` 在只读事务内按 results.rowid 增量投影，以检测截止时间而非完成先后选择最新，保留每个币种/周期的 job 来源。1144 组（286 币 × 4 周期）首次读取 1.43 秒，未变化复查 0.58 毫秒，HTTP 未变响应 65 字节；后续只传变化的组，不解压 K 线、不启动检测。
- 菜单进入默认最新汇总，显式历史链接仍看原任务；巡检发现直达精确识别框并保留跨周期币种列表。图表可切周期，30m/4H 无检测时复用 `/api/chart` 监控行情，明确标注“尚未检测”，不冒充无形态。
- 巡检卡片桌面四列、390px 手机两列，约 175 × 132px。共享搜索下拉支持方向键/回车/Escape、弹窗顶层显示、动态表单和多币种格式。检测使用加密币池，信号等使用全市场池；已有模拟开单选择器保留。主题/刷新等操作改图标，来源与版本文字保留在可聚焦的信息提示。
- 49 项前端、46 项 YOLO 后端测试通过；桌面与 390 × 844 浏览器实测周期切换、对应 K 线框、行情预览、键盘选币、菜单重进最新、发现直达，以及手机无横向溢出、弹窗关闭恢复滚动。未做实体手机键盘或公网长时测试。
- 主监控 1960/1960、零错后优雅重载网页服务，YOLO 在提交边界恢复；执行器 76972 及配置、独立跟单 23135 未改变。新 01:16 轮 5m/15m 各处理 286 币，failed/pending/remaining 均零，各 1 币历史不足，耗时 329/336 秒。四计划频率与启用状态保持。
- 原始证据在 `output/detection-ux-20261017/`；只用临时 IAB tab6，未开启 Chrome，收尾关闭并恢复 viewport。经验见 `docs/learnings/latest-result-identity-must-include-timeframe.md`。未提交混合工作区；子代理实际模型未能核验，已停止，由主代理完成。

## 最新进度：六项前端性能优化已启用（2026-10-17 00:58 北京）

- Owner要求先做市场页重复渲染、YOLO轮询、模拟盘历史统计，再完成其余三项；实测后清理浏览器且保留后台巡检与执行。本轮只改当前main共享工作区，未创建分支/worktree、未打包提交其他会话混合修改。
- 市场刷新等待行情/区间/首次历史一起完成，按稳定顶层区域比对标记；未变区域、焦点和展开状态保留，事件只绑定一次。同一行情窗口实际约38秒13区域全部保留、0替换/0长任务；跨新K线时允许更新图区，早期长样本节点变化不能当作错误或全页重建。
- YOLO任务列表SQL摘要投影，完整冻结配置仅在打开详情读取；列表/运行详情带revision，按可见页签轮询，已结束任务不重复取详情。实测20条282089→11644字节，未变列表146字节、详情56字节。运行详情未变化仍需计算摘要指纹，尚非推送协议。
- 模拟盘账户/成交/仓位与权益由SQLite触发器分开版本化，报价和笔记不使统计失效；同一读事务核对版本和数据。账本变更才重算精确Decimal统计，权益只读新增bucket并保留首尾/极值/缺失点，<=1200点；修订、删除、回填强制重建。最多8个账户投影。真实副本64成交/25仓位/5850权益点，统计逐值相同，旧读取3.2–6.5ms、暖读取0.55–0.61ms；额外5万权益样本暖读0.813ms，首次增量构建166ms高于旧全读62ms，不能宣传冷读提速。
- 模块先preload并行下载脚本，再等CSS并依次执行；浪浪5脚本实测开始时间跨度0.3ms。workspace-runtime提供GET去重、15秒超时、离页取消、失败退避，写入不因离页取消/不自动重试；同步传输异常也释放请求锁。阅读/市场离页60秒清理重型DOM，搜索Worker显式dispose，PDF仅缓存一本且释放Blob（32MiB内嵌上限，较大文件独立阅读器）。笔记、选项保留；真实搜索186结果回收后重建一致。三个隐藏页面DOM=0，Worker创建/终止1/1，Blob创建/释放1/1。仅本轮测试页挂起jobs GET，超时后解除拦截，自动恢复20条且错误提示清除，无须重载。
- 285项相关Python检查、93项前端检查通过，另做JS语法/定向diff检查。等待主监控1960/1960、0错误后重载，YOLO在提交边界短暂停/恢复，四计划spec/频率/enabled完全相同。主站现PID10427、YOLO10435；执行器PID76972及配置未变、独立跟单23135未动。新一轮主监控1960/1960、0错；00:46轮5m/15m各286处理，372.28/376.83秒，failed/pending/remaining均0，各1币上市历史不足。
- 无训练/promote、交易参数、下单/撤单、VPS或实盘账本写入。原始验收在`output/frontend-perf-20261017/`，其中bench只在临时副本写测试样本并自动删除；本机短时验证不等于公网弱网或长时内存保证。本轮仅一个临时IAB页（tab5），未启动Chrome；故障拦截已清除，结束关闭测试页，保留用户浏览器。
- 经验见`view-refreshes-need-stable-regions-and-poll-projections.md`、`analytics-cache-versions-must-follow-ledger-not-quotes.md`、`hidden-workspaces-need-owned-cancellation-and-release.md`。子代理实际模型/推理档位工具未回显，按Owner规则停止，由主代理完成。



## 最新进度：主站共享 K 线读层已启用（2026-10-17 00:23 北京）

- Owner要求全站同币种周期去重、统一限流、增量更新和同步状态，保留后台巡检/实盘。`yoyo/data/candle_hub.py`在本机运行目录`shared-candles/cache.sqlite3`提供独立公开读缓存；按源站/合约/原生周期/开盘时间唯一保存OKX九字段，确认与形成中K线分开，保留成交量单位和UTC日界。跨进程文件锁合并请求，WAL短事务；只补缺失区间，确认数据冲突失败，缺口不伪造。每组4096根，形成中2秒；缓存不是canonical/VPS写者。
- `monitor.OKX`及当前8766主站监控、YOLO/图表续接、市场共振/区间、趋势线与VIDYA读者接入；共享K线总额度8次/秒、后台合计3次/秒，监控使用优先额度。Mac Python3.9的monotonic原点不跨进程共享，已改用系统clock_gettime并做错开进程启动测试。`/api/candles/status`和“运行状态 → 全站K线共享”显示新鲜度、历史不足、复用、补数和失败；只读状态接口不触发行情下载。
- 6个真实并发客户端只发1次OKX请求；跨市场共振适配复用20根既有K线0次外部请求、5.39ms，base/quote量保持。核心115项回归通过，另有VLM/层间边界84项及前端12项通过（含重叠覆盖）。旧daily_enablement/30m_enablement共11项仍与现有周期/接口夹具冲突；没有为它们改变实盘周期或阈值。跟单完整旧套件因主venv缺pydantic_settings未收集，已用独立market适配测试验证其公开读接口；未安装/升级依赖。
- 等旧扫描1960/1960完成后，YOLO在原子提交边界停/恢复，主站优雅重载。旧扫描有109个网络失败，新进程首轮39.05秒补齐109项、1960/1960且0错。新版本00:16轮5m/15m各286币完成，分别382.58/387.84秒，failed/pending/remaining_endpoints均0，各1币历史不足；四计划及频率不变。状态接口20样本p95 129.4ms，缓存851组约95.3MiB，观测时内存空闲26%。这是本次实测，不代表任意网络/冷启动负载保证。
- 实盘执行PID76972及配置前后相同，未动实盘账本、风控、下单路径、ACTIVE或VPS；两份不同检测任务的实时续接成功，原检测快照逐值不变。VLM服务在自动审核关闭/无运行任务时重载，原关闭状态保留。TradingView外嵌仍由第三方供数；独立Arthur跟单进程PID23135含真实账户监听，未强制重启，其模拟1m共享适配已写好、待正常维护重启加载。旧8642/18766独立历史工具不计入当前主站接入覆盖。
- 原始验收在`output/shared-candles-20261016/`（名称沿用开始日期）；手机390px和桌面1280px验证无页面横向溢出，表格独立滚动，搜索/刷新正常。经验`docs/learnings/shared-candles-need-process-wide-identity-and-budget.md`及`cross-process-budgets-need-a-shared-clock-epoch.md`。本次变更留在共享main工作区，未把其他任务的未提交/未跟踪模块整体打包提交。

## 最新进度：YOLO 增量巡检实测并启用（2026-10-16 23:20 北京）

- Owner授权实测后直接启用：全市场5m/15m每15分钟、1H每4小时、UTC日线每天。四计划已启用，旧每小时15m计划移除；API、后台单写锁/心跳和真实页面确认，正常自动轮次已继续运行。日线北京时间08:01触发，各周期保留收盘后60秒到达余量。本机须保持运行，停机积压恢复后补齐。
- 结果和每币种游标原子提交，每次最多12个新端点，短周期优先轮转；单模型进程、小批次推理、2个后台输入预取线程、共享3次/秒公开请求限流与内存压力暂停。YOLO旧输入缓存仍合入较新的监控闭合K线尾部；32根OHLC块压缩共享，均线及历史检测图精确保留，旧格式可读。
- 冷启动短周期全286币耗1853秒，原串行暖轮超预算而拒绝启用，失败保留。优化后正常一轮380.711秒、API p95 0.275秒、内存实测1.7GiB、无内存暂停。同轮逻辑结果载荷33.9MiB降至7.0MiB，约79.22%（不含SQLite页/索引与可替换输入缓存）。长周期首次补历史890.995秒、p95 1.659秒；最后一小时积压补扫1032.072秒、p95 0.566秒。暖轮耗时不是冷启动或所有负载保证。
- 日线7币网络失败保留在原任务；同截止同计划补跑7/7完成21端点。原acceptance回执仍false，独立finalize核对持久化证据后activation=true：2638段结果连续、928个可用币种周期。5m/15m各285可用+1历史不足，1H 273+13，日线85+201；不足来自1240根模型输入要求，不冒充覆盖。原始证据及启用截图 `output/yolo-patrol-incremental-20261016/`。
- 115项相关Python、22项前端检查通过；实际新结果图/框、行情续接与移动弹窗核验通过，最终新页面无控制台错误。一次浏览器调试超时后新标签页恢复。源码至5773f434ae，无训练/promote/阈值/执行准入变更，未重启实盘执行器。先前监控重载前后6个paper状态相同（排除响应时钟）；最终恢复进度API已加载，无须再次重载。
- 全站K线目前只部分共享：监控/龙头/模拟信号读者复用monitor checkpoint，YOLO优先读后补缺；其他独立入口仍有各自缓存和限流。跨模块统一请求合并、总预算、版本通知与新鲜度监控尚未实现。经验见docs/learnings的incremental-patrol-cursors、saved-input-prefix及patrol-recovery三条笔记。共享HANDOFF其他修改保留，不整体打包提交。

## 最新进度：模拟账户行情连接池恢复（2026-10-16）

- Owner 报模拟持仓缺新鲜行情、权益/可用资金未知。实际受影响为ETH和BONK，均未被移出扫描池；模拟交易独立读取公开ticker，不用信号扫描名单。22:51日志先报TLS EOF，随后持续PoolTimeout；新建同源HTTPX/requests连接均能读到新鲜报价。
- 真实httpcore代理隧道的内存网络夹具复现：握手失败占满池，之后持续等待连接槽。`PublicHTTP`遇传输错误淘汰对应池，仅公开GET最多重试一次；旧池引用排空再关闭，迟到异常不影响新池。保留15秒报价有效期和原始时间戳，未恢复时前端列出确切缺失币种。无依赖升级、账本修补或旧价填补。
- 207项Python检查初次206通过；新代理TLS夹具签名修正后相关7项通过。134项前端检查通过。服务重载后两次真实API核验三个账户marks_complete/risk_complete均true、权益/可用非空，ETH/BONK报价恢复，自动检查error=null。accounts/positions/fills/limit_orders/receipts逐行前后一致。实盘进程和原cutoff保持，未发送交易所写请求。浏览器新页/CDP核验超时，未把API恢复当成实页视觉验收。
- 经验 `docs/learnings/public-market-pools-must-recover-after-transport-failure.md`；模拟持仓/成交/订单与资金参数保持，原有共享main修改未打包提交。子代理模型档位此前已无法核实，本轮由主代理完成。

## 最新进度：Owner 缩小信号扫描池（2026-10-16）

- Owner 明确移除 XMR、BTCDOM、ARPA、SFP、BIGTIME、HOT、ICP、GMT、EGLD、MAGIC，并追加移除15个非USDT线性永续。共享新增准入规则覆盖全部四周期；当前509个live OKX合约中5个命中指定币种，实际新信号池489个。其余5个仍在排除名单，历史负收益筛选只是Owner范围决策，不标记收益有效。
- 新增信号与存量管理分开：排除标的不新增事件/通知；已有参考和执行账本未结束标的保留K线。执行归属不可读时保守保留行情，不放开新增。当前GMT有旧参考持仓，行情采集490个/1960个周期单元，其中489个允许新信号。1084条原事件身份完整保留。
- 已重载监控服务，数据库/API/实际浏览器确认489及排除规则；首轮扫描仍进行中。6个paper条目整体前后一致。Owner此前启动的实盘执行器心跳正常，cutoff=1792158236572未变；本轮没有重启执行器或发送交易写请求。执行器磁盘代码另有准入拦截，但旧进程未加载；即时生效依赖已重载的监控源。生效前排除标的无新鲜待处理信号，已有两条V13历史事件均早于cutoff。
- 186项Python检查首次184通过、2个新测试夹具未合法登记；改用真实事件构造后相关15项通过，未削弱生产校验。7项前端检查及差异格式检查通过。125U/2%/全仓/最高档位杠杆/四周期/多空等其他配置不变。经验 `docs/learnings/universe-exclusions-must-preserve-position-management.md`；Notion #59已追加实际进度。共享main混合修改保留未整体提交，子代理实际模型档位无法核实后由主代理完成。

## 最新进度：周一高低点区间 v1.11 · 关键价位扩展（2026-10-16）

- Owner纠正目标为优化Pine指标本身，并选择昨日POC、VAH/VAL、nPOC、上周高低点。以v1.8为基线新增 `yoyo/evaluation/pine/monday_range_v1_11.pine`（v1.9/v1.10已占用），保留v1.8源码与私有脚本，不加入迷你图。v1.11已保存为新的TradingView私有指标并加载到WLDUSDT.P主图，恢复5分钟图；不包含策略、报警或订单。
- 新价位在固定15m请求上下文计算，80档均匀分配成交量、70%价值区，属于估算。自然日/自然周采用所选时区，日线沿用v1.8的交易所时区例外；上周包含周末。nPOC回看最多14个自然日，仅在后续已收盘15m K线触及后移除；缺口使未触及证据失效，覆盖不足会提示。原周一区间和双向Fib公式保持，绘图改用时间坐标并预留对象预算。
- 本地源码保护与网页既有计算测试共20项通过；原生Pine夹具调用实际函数，日志确认profile/midnight/prior-week/touch/gap/partial-day/DST通过，临时测试代码已移除。WLD同一时刻5m/15m/1h/4h的POC/VAH/VAL/上周高低五值一致；这不是全标的、全周期证明。源代码、夹具与原始核对记录见 `tests/evaluation/test_monday_range_v1_11.py`、`tests/evaluation/monday_range_v1_11_native_checks.pine`、`output/pine/monday_range_v1_11_validation.json`。
- 经验：`docs/learnings/pine-key-levels-need-closed-source-context.md`。共享main中的其他会话修改保留，本轮未整体打包提交。子代理运行模型档位无法核实后停止，由主代理完成。


## 最新进度：信号中心分区、摘要读取与缓存（2026-10-16）

- Owner 要求移除“正在蓄势”、优化存储缓存与卡片之外的界面。该计数属旧版观察状态，已移除信号中心计数与旧观察入口。当前默认信号卡片，信号记录 / 表现分析 / 实盘账户独立展示，页签记入URL；旧历史深链继续保留已结束列表。账户配置说明折叠，等待下一根收盘参考的卡片给出明确原因。
- 引入 Bootstrap 5.3.8 的局部表单、按钮、导航，源与锁文件在 `tools/signal-ui/`，本地编译CSS约22 KB（gzip约3.5 KB）；无全局reset、CDN或Bootstrap JS。ECharts只在首次进入分析页加载，记录首页省下约1.12 MB脚本（gzip约368 KB）。账户只在账户页轮询，账户页不拉信号账本；相同行不每15秒重建DOM，时间标签按分钟更新。
- 实测发现市场状态计数仍用SQLite JSON解析约269 MB完整图表页，五次约5.6–12.8秒。改读约5.6 MB事务一致摘要，完整方法（含完整性检查/键联结）复测约11–24毫秒，计数一致；不清理数据库或历史数据。新增HTTP专用信号精简投影缓存，上限8项/16 MB/30秒，SQLite data_version跨写连接失效；日期、未来排除、新鲜度每次重算。当前98条样本的历史账本计算暖缓存约3.0–3.8毫秒，未缓存约4.1–7.8毫秒。端到端HTTP仍有约百毫秒至亚秒波动，不能把查询加速倍数当作整体页面倍数。
- 127项Python与50项前端检查通过（后续缓存实现/状态联结又定向复测7项）。同步修正旧测试样本仍引用V1切换键/协议的过期fixture，没有放宽生产过滤。桌面1440、390/320px、深浅主题、空筛选/重置/方向周期组合、分析/回撤、账户与页签刷新通过；未发现整页横溢或浏览器错误。证据 `output/playwright/signal-cleanup/`。
- 本地监控服务重载，前后6个paper状态一致；Owner此前启动的OKX执行器心跳持续正常，无本轮交易写请求、配置变化或执行器启停。共享main的混合修改保留，未整体提交。经验 `docs/learnings/status-counts-must-read-the-compact-storage-projection.md`、`docs/learnings/workspace-tabs-must-own-their-loading-and-polling.md`。子代理实际档位无法核实后停止，由主代理完成。

## 最新进度：Owner 开放 OKX 远程只读展示（2026-10-16）

- Owner明确要求“okx实盘不需要限制只能本机才能看到”，覆盖下方历史本机限定与公网403口径。现有免登录公网入口开放余额、持仓、挂单和执行记录；已说明持有链接即可查看。移除账户接口的loopback/代理标记限制，保留同源浏览器检查、字段白名单、GET-only/no-store；不返回密钥/账户ID/私有路径，不添加交易控制。
- 20项后端展示/资产与5项前端检查通过。网关实际返回200、read_only=true、Cache-Control=no-store，真实公网浏览器也已显示账户。重载前后6个paper及OKX执行状态一致（当时未启动）。随后验证时已观察到执行器心跳正常、0持仓/0执行记录；本轮未启动该进程或发送交易写请求，不推断首次订单已验收。
- 证据 `output/playwright/okx-remote-display/`；经验 `docs/learnings/account-visibility-and-execution-permission-are-separate.md`。只修改当前main工作区相关文件，未打包提交其他会话的混合修改。

## 最新进度：Notion #59 · 全仓与信号中心工作区（2026-10-16）

- Owner 最新口径覆盖下方逐仓记录：125U、1R 2%、全仓、全标的四周期多空、交易所档位最高杠杆，无额外仓数/合计风险比例上限。全仓读取币种级 availEq/imr，杠杆按合约双向共享；设置后复查保证金，保护与退出核验同一模式。本机未启动配置已在单写锁内原子更新并保留私有备份，凭据未改。
- 完整只读 preflight 通过（Futures + long_short_mode）。worker未启动、执行记录0、无交易所POST；真实入场/保护/改单/退出仍未验证。自动实盘须由Owner本人运行 `.venv/bin/python -m yoyo.okx_execution run --ack EXECUTE-125-USDT-LIVE`，首次只接启动后的新信号。125U额度与2%计划风险不是全仓损失隔离。
- 信号中心现有页面重排为总览/信号记录/实盘账户：账户摘要、前置筛选、重置、完整筛选累计净R/回撤和周期贡献，本地Apache ECharts 6.1.0，保留深浅主题与手机。曲线按退出时间合并同刻记录，缺失R/时间不补零；与真实账户收益分开。只读账户接口仍仅本机，公网网关复核403。
- 150项Python（执行器/历史统计/资产/启动/层边界）与42项Node检查通过。真实浏览器核验1440/390/320px、搜索空态/重置/详情/卡片/15m多头筛选与曲线一致、深浅主题，无整页横向溢出或console错误。原始证据 `output/playwright/signal-redesign/`，已本机排除git；服务重载前后6个paper状态一致，未重启旧运行。
- Notion #59保持进行中，已更新实际进度。经验：`docs/learnings/cross-margin-changes-collateral-and-leverage-scope.md`、`docs/learnings/signal-curves-must-use-complete-filtered-exits.md`。共享main混合修改继续保留，不打包提交其他会话；子代理实际档位无法核实，主代理完成。

## 最新进度：形态图续接行情、排序与移动弹窗修复（2026-10-16）

- 当前盘口结果默认跟随最新已收盘K线，可手动更新或关闭；新增“看形态与后续”。只读chart API保留原检测快照/框，按原close/HL2来源续算SMA/EMA，先读既有OKX确认缓存，缺口再读同源公开行情。缺数据明确失败并保留旧图，不写入K线或检测结果、不触发新推理。历史模式默认不续接。
- 币种支持确认时间、模型分数、数量、名称正反序、关注优先，选择自动记忆；巡检发现支持最新/模型分数/币种排序，先排序再分页。请求过期或切币时不会混入旧回复，更新不抢走手动图表视角。
- 移动端表单固定关闭/提交，正文独立滚动并适配visualViewport/safe area；大图内币种列表改为嵌套原生弹窗。先锁定页面再移动图表，先恢复内容高度再恢复滚动，修复关闭大图回顶部；图表允许纵向页面触摸滚动。
- 98项后端/资产/层边界与30项前端检查通过。实际ALGO15m从17:45续至21:00并自动更新到21:15（14根）；320×568、390×700/400弹窗按钮均在可视区，滚动恢复566.5px，浏览器无error/warn。证据 `output/playwright/yolo-live-mobile-20261016/`。这是浏览器尺寸验证，尚非实体iPhone键盘验收。
- 本地服务已重载；6个paper状态和既有巡检计划前后完全相同，YOLO worker保持。未修改模型/阈值/交易参数、发起检测或改计划。经验见 `docs/learnings/chart-continuation-must-preserve-the-detection-snapshot.md` 与 `docs/learnings/modal-scroll-restoration-requires-stable-content-height.md`。子代理运行档位不可核实后停止，由主代理完成。

## 最新进度：Notion #59 · 信号中心展示 OKX 实盘（2026-10-16）

- 信号中心顶部新增只读实盘区域：账户权益/可用/浮盈、真实持仓、普通挂单及四类策略委托、本机执行记录、进程锁与心跳。配置125U/2%，全标的四周期、逐仓最高档位杠杆，无额外仓数/合计风险比例限制。参考统计仍在下方单独标注。
- 新 `GET /api/okx-execution/status` 按需独立缓存，只有交易所GET；不初始化执行账本、不启动worker、不加入信号脉冲。缺失是未知，失败保留明确过期的旧快照；收益只取齐全的交易所平仓/USDT手续费回执，不含资金费。公网免登录网关实测403，本机可读，不返回凭据/账户ID/原始错误。
- 本轮最新只读账户模式已为 `long_short_mode`，覆盖下方历史“待切换”状态；本轮未发模式修改请求。worker仍未启动、执行记录0，真实入场/保护/改单/退出回执尚未验证。不要把账户连通或模式满足当成实盘验收完成。
- 后端/资产/启动依赖/层边界134项与前端8项检查通过；真实桌面、390/320px和深浅主题检查见 `output/playwright/okx-account-ui/`。本地监控服务已重载，前后6个paper状态一致（原有2个error/4个stopped），未重置/重启这些运行。UI填充态验收仅为浏览器拦截样例，不是实际交易。
- 经验：`docs/learnings/account-dashboard-must-separate-connectivity-and-execution.md`。共享main已有混合修改，本轮集成保留在原工作区，不整体提交其他会话改动；子代理实际档位无法确认后由主代理完成。

## 最新进度：Notion #59 · Owner 将执行额度改为125U（2026-10-16）

- 本次明确指令覆盖下方历史50U口径：资金配置改为125U，1R仍按 `min(125, USDT权益) × 2%`；权益125U时计划风险2.5U，按合约步长向下取整，非实际亏损保证。全标的/四周期/多空、最高档位杠杆、无额外仓数及组合风险比例限制保持。
- 已同步代码默认/校验、统一启动确认 `EXECUTE-125-USDT-LIVE`、README与本机私有 `config.json`。更新时取得单写锁，核实无启动cutoff、无运行配置和交易记录，再原子替换配置，仍0600。未读取或修改凭据，未请求交易所API。
- 42项执行模块测试通过，覆盖125U定仓2.5U、权益下降与上涨封顶、保证金额度不足。Notion已同步；worker仍未启动，上一轮账户单向模式问题仍待Owner切换。此次只是明确参数调整，无新实验或报告。

## 最新进度：Notion #59 · 私有鉴权通过，持仓模式待切换（2026-10-16）

- Owner 提供凭据后，经隐藏终端输入保存到仓外 `~/Library/Application Support/Fable/OKXExecution/credentials.json`（0600，目录0700）。不得在日志、仓库、Notion或回复中复制凭据。当前 private GET 鉴权成功，读取+交易权限，无提现权限；执行配置仍50U/2%，无3仓/6%上限。
- 账户 `acctLv=2` 已满足 Futures 模式，但 `posMode=net_mode`，与执行器要求双向 `long_short_mode` 不符，完整 preflight 未通过。只读核对 USDT、空仓、普通挂单0，以及 conditional/oco/trigger/move_order_stop 四类挂单均0；账户可交易493个live线性USDT永续。余额细节仅在本机私有联调收据，不写仓库。
- 未发送任何交易所POST，未下单/撤单/划转/设杠杆/改账户模式，worker仍未启动。下一步Owner在OKX切换双向持仓后继续只读preflight；真实入场、保护、改单、退出回执尚未验收。已建议将聊天中披露的凭据在OKX轮换后本机录入；未擅自修改或撤销key。
- Notion #59已回写上述状态并保持进行中。仅配置与只读联调，不是实盘运行或收益验证；无需重复申请已授权的只读检查。

## 最新进度：自动巡检直接浏览最新形态（2026-10-16）

- 巡检页以“最新发现”为首页，按首次观察时间列出币种、周期、多空、模型分数、确认时间；同币/周期/方向/冻结契约内合并重叠核心，重复扫描不把旧形态顶成新发现。支持时间、币种、周期、方向筛选，每5秒更新，计划管理折叠在下方。
- 新只读 `/api/yolo-detect/discoveries` 从已有不可变结果增量投影，不创建任务或重跑推理。真实35条形态观察合并19处；首次读取约0.48秒、温缓存约1毫秒。点击携带job/symbol/box，打开准确K线与框，显式定位优先于旧视角；一键返回发现列表，保留当前筛选。
- 91项后端/资产/层边界与25项前端检查通过；实页验证BILL精确框、刷新深链、ALGO三轮合并、搜索/空筛选恢复、390/320px无横溢、深浅主题。证据 `output/playwright/yolo-patrol-20261016/`。服务重载前后6个paper条目与既有巡检计划逐项相同；未发起新检测、改计划或模型/交易参数。
- 经验 `docs/learnings/patrol-discoveries-need-event-clocks-and-exact-chart-links.md`。仅功能验收；列表保存历史发现，不代表形态仍在持续。子代理实际档位无法核实后停止，由主代理完成。

## 最新进度：Notion #59 · 独立 OKX API 执行模块（2026-10-16）

- Owner 选择直接 API、50U、按初始止损定仓 1R=2%、交易所档位最高杠杆，并明确否决最多3仓/合计6%限制。实现跟随 V13.1 全部标的、15m/30m/1H/4H、多空；不加币种白名单、仓数或组合风险比例限制。同币跨周期通过双向逐仓和逐信号 lot 记账，实际同向保证金/强平仍由交易所聚合。
- 新模块 `yoyo/okx_execution/`：只读监控、Decimal 张数/档位、私有本机凭据入口、签名 REST、独立 SQLite/单写锁、发单前 intent、未知订单不重发、附带止损核验、2R + 1H 2ATR/其他4ATR追踪、原始反向退出、owned-lot 对账。按实际市价成交定义管理R，不承诺复制参考次开价/收益；最高杠杆可能早于策略止损强平，2%不是损失保证。
- 130项新模块/现有V13.1/层边界检查通过；实际只读82条、73标的、四周期，管理样例16根闭合K线。OKX公开时间/DOGE合约/93个档位返回成功。私有鉴权与真实入场/保护/改单/退出仍未验证；无密钥、无私有请求、未启动worker或启用实盘。
- 已初始化 `~/Library/Application Support/Fable/OKXExecution/config.json`（50U/live但无执行进程）。下一步 Owner 在本机运行 `.venv/bin/python -m yoyo.okx_execution credentials` 隐藏输入三项凭据，然后做只读 `preflight`/`preview`。独立账户须Futures模式+双向持仓，不接管手动仓位。撤保护到平仓有无保护间隔；未知回执/部分退出/清算差异需对账，不能重置账本后重发。
- 代码提交 `5225b46e58`，共享main提交前已有5个未推送提交，因此未代推其他会话提交。经验 `docs/learnings/hedged-signal-lots-need-owned-order-reconciliation.md`；Notion #59保持进行中并记录Owner纠正。未重启监控、改生产资格/新鲜度/脉冲或重新pin旧paper；新增yoyo源码会改变paper manifest。子代理运行档位不可核实后停止，由主代理实现核验。

## 最新进度：YOLO 日常交互继续打磨（2026-10-16）

- 增加本浏览器关注币种与当前任务内关注筛选、当前币种换周期检测、形态摘要/多空颜色说明、密集根数/确认根数/模型分数。图表常用操作与显示/导出分开，六条均线提供图例；逐任务/币种保留最近40个图表视角，切币与刷新可恢复。
- 未提交草稿持续保存；支持 ETH/USDT 等常见输入，提交前检查币种池、历史边界/范围/已收盘时间；可显式一键对齐，不静默更改时间。快速提交曾绕过未加载币种池，保留那条 NOTREAL 失败记录；现等待列表并检查弹窗/草稿生命周期，取消后不继续提交。源不可用时明确提示重试。
- 实际 ALGO5m 任务 `2d9bc721d1484ed4a3c1080eee20df4e` 完成1/1、6窗口、0形态，仅功能验收。25项前端测试通过；真实页面验证关注/视角/草稿刷新、换周期、输入校验/时间修正、深浅主题与390/320px布局。PNG 从 data URL 改为 Blob 文件下载后浏览器返回下载回执；回执路径工具暂不可读，未声称落盘图片内容已核验。证据 `output/playwright/yolo-comfort-20261016/`。
- 经验 `docs/learnings/async-preflight-must-respect-the-draft-lifecycle.md`；仅前端与对应测试，既有巡检未更改，测试关注已恢复，不改训练/模型参数/交易准入。子代理无法核实实际档位，主代理完成。

## 最新进度：Notion #59 · OKX 接入可行性（2026-10-16）

- #59 标题为“能否把我们信号中心在跑的策略做到 okx”，已完成可行性核查并写回原页，状态 Done 仅代表问题核查完成；接入开发、账户联调与资金运行未完成。原截图为 OKX 策略页，可评估 Signal Bot；若优先保持现有 V13.1 动态保护，建议独立 API 模拟执行模块，不能承诺显示为 Signal Bot。
- 实读 `/api/v13/signals` 确认 V13.1、15m/30m/1H/4H、`orders_enabled=false` 与 `is_trade=false`。原生程序直推需按 OKX 官方 §3.4 确认固定 IP 接入；还需补完整退出、交易所保护、跨周期持仓归属、去重恢复与实际成交对账。旧 L4 TP5/SL2 和 Copier 跟单执行契约均不能直接套用。
- 17 项现有 V13.1 状态机测试通过；原始核查 `output/task59/feasibility_evidence.json`，经验 `docs/learnings/signal-bot-transport-does-not-preserve-execution-semantics.md`。任务 https://app.notion.com/p/3fb8856479af8092b673f3b74ed5d6f4 ，资料 https://app.notion.com/p/3fb8856479af81db85b7ebacf250b3a8 保持待验证。未发 webhook、下单、改参数、重启服务或启用实盘。

## 最新进度：YOLO 检测页使用流程优化（2026-10-16）

- Owner反馈 `/#yolo-detect?job=4f32c06a6a0c441b81c48354df8aff8d` 难用。本轮改为结果优先：有形态币种侧栏/窄屏下拉、上一币/下一币、自动定位密集区、全局/最近视图、大图与原始输入弹窗；检测记录、巡检和模型说明独立页签。
- 新建检测、巡检草稿与浏览旧结果分离；草稿记忆、单币快捷检测、历史时间预设、深链保留币种与页签。正在看旧快照时明确标识，较新结果仅匹配同模型/周期/范围；历史任务还须同时间段。浅深主题切换保持当前范围。
- 实际从新表单提交 ETH5m 任务 `0c82b8b149b04b50a2d3c47a83805172`，1/1完成、6窗口、0形态，只验证交互，不代表识别精度。用户原任务285/285、7币有形态作为浏览验收；既有每小时全市场15m巡检未改动。
- 19项前端、15项相关后端测试通过；浏览器核验切币/刷新、草稿隔离、搜索无命中恢复、记录返回、原图与大图、历史表单、巡检预览、深浅主题、1440/390/320px无横向溢出。证据 `output/playwright/yolo-ux-20261016/`，经验 `docs/learnings/detection-browsing-must-not-share-state-with-scan-drafts.md`。无新训练、模型切换、promote或实盘变更。

## 最新进度：日常 YOLO 检测工作台与菜单直拖（2026-10-16）

- Owner 要求直接拖拽菜单并自动保存、独立找龙头菜单、可视化 YOLO 手动/自动检测。入口 `/#yolo-detect`、`/#auto-leaders`；旧 `#langlang?tab=auto-leaders` 跳转到独立菜单。去掉排序模式按钮，鼠标直拖、触屏握柄与键盘移动，支持跨组持久化。
- 独立 `yoyo/yolo_workbench/` 队列/进程锁/SQLite，单币或全部加密 USDT 永续，5m/15m/30m/1H/4H/UTC日线，历史逐端点或最新三个收盘端点，定时巡检与取消、部分失败、重启恢复。复用只读已确认行情缓存，不向 forward 脉冲加任务。模型和渲染契约固定；默认既有 Grade-A Close1280，HL2 可选，但没有全周期最佳或生产资格裁决。
- Lightweight Charts 4.2 全局 K 线、六均线、量、真实框与缩放/点击、原始输入/标注图、结构/原始/方向筛选、币种搜索、PNG/JSON 导出。修复捆绑图表对小数逻辑坐标返回0导致框消失，采用相邻整数 K 线中心插值；保留核心时间、确认窗收盘、首次检出三个不同时间。
- 核心先提交 `76dc34d074` / `5d0c9d9f08` 再生成实际结果。一次定时全币种15m任务 `204148b553f0436f9ad3031082cf8928` 完成285/285、1710窗口、0失败、9币有结构、29原始框；这只验证功能，不是精度/收益验收。ETH5m、15m历史、日线与FARTCOIN历史有框样本实跑；后者来自已知事件，仅验收可视化。不存在币种的失败记录保留。
- 109项后端与18项前端检查通过；桌面、390/320px、深浅主题、直拖/跨组刷新、原图/导出、筛选和计划创建/停止实页通过。证据 `output/playwright/yolo-detect-20261016/`。本机服务已重载，原6个模拟运行ID/状态/心跳/错误逐项未变。当前保留账本内已有的每小时全币种15m计划；本轮专门创建的QA计划均删除，没有清空观察记录。
- 两条经验：`docs/learnings/chart-boxes-need-verified-fractional-coordinate-mapping.md`、`docs/learnings/research-detection-jobs-need-separate-books-and-clocks.md`。共享main，混合接线文件保留工作区；无新训练、promote或实盘变更。子代理返回信息无法确认实际Luna/max后停止，由主代理完成。


## 最新进度：BTC 关键位 5m 插针收回已测试（2026-10-16）

- Owner要求“看5min级别，到关键点位只做插针收回”。沿用上一轮首次触及和全部前端价位：同根完整5m开盘/收盘严格在原侧、影线严格穿位，多空镜像；下一5m开盘才计收益。未加入影线比例或TP/SL；未筛选对照也等同样5m收盘，固定15/60/240分钟、主60分钟，原0.2%成本与2024-10-13时间切分不变。
- 3163有效收回事件（3012不重复5m时刻），3066匹配随机；前后2025/1138。15/60/240m毛+1.153/-1.027/-2.428bp，净-18.847/-21.027/-22.428bp；随机净-19.322/-18.447/-15.762bp，配对超额+0.326/-2.692/-6.141bp，p=.714/.081/.0314。60m q=.4359，前后净-20.747/-21.524bp；15类全期60m净均负，无类别跨期过门。同钟全部触价60m净-20.649bp，筛选没有改善整体均值。
- Builder/config `eec12c2523`先提交后运行；91项测试通过，独立1m切片复核5946信号、17835结果和112782控制配对，最大误差1.14e-12bp。原始结果 `experiments/active/exp-btc-key-level-wick-reclaim-20261016-v2/results_v2/`；继承v1已审计价位/首次触及谱系和哈希。配对超额仅含已匹配样本，不能拿全样本均值直接减随机均值。
- 此固定规则的盈利假设未通过，registry保留拒绝结果；只测试首次触及同根收回与固定时间退出，不外推所有反复回踩/多根收回/长影线或TP/SL策略。训练/生产资格false，无前端或实盘改动。Notion研究 https://app.notion.com/p/3fb8856479af819b9facfcb02b6db533 ；新v0.2策略关联原v0.1，旧失败记录保留。经验 `docs/learnings/reclaim-confirmation-must-reset-the-entry-clock.md`。

## 最新进度：BTC 关键位首次触及反应已测试（2026-10-16）

- Owner要求测试「市场共振观察 → BTC 区间与关键价位」能否挂单吃短反应。沿用纽约日界、15m前端公式、原0.2%成本；OKX BTC 2021-10-13至2026-10-13既有1m归档，2024-10-13时间切分。每个日期来源价位只取首次触及，昨日POC/nPOC合并，从下一分钟开盘测量，15/60/240分钟，主比较60分钟。
- 5945事件、5610不重复触及分钟；5824事件匹配成功（97.96%），116058对照配对。15m/60m/240m平均净反应-17.841/-19.950/-21.238bp；匹配超额+1.602/-1.695/-5.419bp，按周聚类p=.0970/.2584/.0542。60m前后段净-18.819/-21.699bp；15类全期60m均负，无类别跨期通过门槛。周一低点全期相对较好但前段净+8.187bp、后段-22.683bp，不能挑全期结果宣布有效。
- 这是位置反应筛查，无实际挂单排队、止盈止损或组合收益；60m有利/逆向最大幅度中位29.377/30.071bp不能当成可兑现利润。全部负结果和理想价位参考保留；训练/生产资格false，不改前端或实盘。
- Builder/config `a6f99cecb0`先提交后运行；81项相关测试通过，独立verifier `665f1ff105`复算17835条反应、116058对照及8517价位通过。两处源缺口共3分钟，缺失形成窗/路径剔除。实验与原始结果 `experiments/active/exp-btc-key-level-reactions-20261016-v1/`，两个registry登记；Notion研究 https://app.notion.com/p/3fb8856479af81cdb42fd6610c0aab54 。两条learnings保留浮点剖面复现与触及反应/成交利润的区别。
- 共享main，只提交本轮归属内容；子代理实际档位无法确认后停止，由主代理完成。后续完整挂单策略需先确定退出规则，当前不自动调止盈止损或成本。

## 最新进度：自动找龙头观察 v1 已运行（2026-10-16）

- Owner 要求“实现自动找龙头”。新增独立 `altcoin_leaders.py`／`altcoin_leader_service.py`，只读复用现有 OKX 1H confirmed checkpoint，每小时取得公开合约分类，每分钟检查；不新增全币种行情请求、K线写者或 forward 任务。范围为明确分类的加密币 USDT 线性永续，排除基准／已知稳定币／股票商品与盘前。首轮 283／283 可比较，约4秒；当时 BTC／ETH 均等待启动，显示发现池，未硬凑龙头。
- 新研究规则显式展示：1H收盘突破此前24收盘启动；首次收低冻结首波；再过首波最高收盘确认恢复；回到起点、恢复后首次回落或120小时终止。本币首波为正、相对基准不弱且仍守起点才进入偏序候选；按本轮相对表现、回吐、收盘最大回撤、恢复耗时分层并保留并列，强弱与行动状态分开。近7天高点只是参考，量比是同币张数，不冒充阻力金标、成交额或主动方向。
- 工作台 `/#langlang?tab=auto-leaders`，API `/api/altcoin-leaders`、`/history`、`/snapshots/{id}`。独立 `altcoin-leaders-v1.sqlite3` 保存不可变派生快照、首次观察和候选池版本；新加入／缺失／已退出交易池分别保留。A→B→A追加新A，重复轮询不新增。历史不能作当前排名，故障或过期暂停判断。规则未验证收益；真实盘口、订单流、人工关键位、共振与效果检验仍待实现。Notion #50/#53 已回写当前范围，#53仍进行中。
- 14项后端／API／导入、73项资产交付与层边界、18项前端检查通过；实页验证基准切换、搜索分页、历史回看、283条完整导出对齐、HTTP503暂停与恢复、1440／390／320px和深浅主题。候选分层路径另用明确标注的合成夹具验证，未落入真实账本。初次QA因选择控件标签匹配与缓存旧资源失败，修正标签并刷新后通过；所有注入已撤销。证据 `output/playwright/auto-leaders-20261016/`，经验 `docs/learnings/automatic-leader-ranks-need-shared-clocks-and-observation-history.md`。
- 核心先提交 `5a054637ec` 再生成真实快照；本机服务已重载且后台运行。原模拟运行在本轮之前已因源码变化处于 error，重载前后运行ID／状态／心跳／错误逐项未变，不声称模拟已恢复。共享 main，新增模块独立提交，其他会话贡献的混合接线文件保留工作区；不改真实交易、通知、成本或风险门。子代理无法确认实际 Luna/max 档位后停止，由主代理完成。

## 最新进度：Notion #50／#53 找龙头流程 v1.1（2026-10-16）

- Owner 要求继续迭代并补找龙头逻辑。`trading-playbooks.js` 的 `leaders()` 扩为 12 节：候选池、共同事件窗口、首波参与、同窗回吐与结构、恢复顺序、并列／降级、合成反例与独立验证；领涨判断和当前行动状态分开。`weekly()` 输出逐字相同。同步更新 Notion #50 与 #53 并回读，原始资料及未知附件块保留；#50 方法完成，#53 实时监控仍进行中。
- 方法页入口 `http://127.0.0.1:8766/#langlang?tab=leaders`。这是人工方法页，候选数据库、自动排名及实时共振尚未实现。原话定位依据本地字幕与索引，本轮视频取回受限，未冒称重新看完视频；新增公式与名单比较为待验证研究建议。市场接口仍有 HTTP 451 与 stale／ready=false，来源选择沿用已有待决项，不静默换源。
- 15 项相关前端检查通过，12 节目录与合成例算术复核通过；实页 1440／390／320px 无文档横向溢出，手机表格内可横向滚动，跳转开单检查及返回通过，控制台无错误。修复目录跳转标题被顶部栏遮挡（`trading-playbooks.css` 两处偏移）；浅／深主题可读。证据 `output/playwright/task50-leaders-v11-20261016/`，经验 `docs/learnings/leader-strength-and-entry-readiness-need-separate-states.md`。共享 main，未提交包含其他会话贡献的混合工作区；无交易参数、通知、生产或下单变更。

## 最新进度：V13.1 15m 到达后前向模拟（2026-10-16）

- Owner 批准执行后，接入独立模拟 worker；实际观察后预定下一未来 15m 开盘，保留原止损并重算风险。原 30min 新鲜度、2R/4ATR、原始反向退出、20bp 不变；只启用 15m，不将 1H 的 2ATR 混用。记录原参考开盘、入场价差、观察/入场延迟；多空和资产类别分组，参考 performance 不参与成交。
- 北京时间 01:36:14 新建运行 `dc0c7e42add44beb902cafb33ea48424`（`#paper`）。OKX 全部 USDT 永续、多空都保留；分类快照 286 加密/194 股票类/9 商品类，后续新合约未分类。后台心跳正常，初次验收 0 条决策，无历史回填；旧 5 个运行记录逐字段不变。仍为 K 线模拟，未含资金费、盘口滑点和退出指令延迟，无账户组合净值或匹配随机对照，不宣称盈利，不启用实盘。
- 148 项 Python、7 项前端检查通过；10 条多空退出路径与 canonical V13 回放对照，现有 44 条信号同入场价格全部一致，净 R 最大误差 6.3e-15。这是适配核对，不是新的前向收益。实页 1440/390/320px 无横向溢出，V13.1 仅展示 15m 选项。源码包 1,148 个文件逐一校验，hash 与运行及当前源码一致。
- Builder `07bc93ab67` 先提交再启动；配置和登记收据在 `experiments/active/exp-spike-v131-observed-paper-20261016-v1/`，两个 registry 已登记；Notion 原观察 `3fa8856479af813db925f29bace95f79` 追加启动状态，原快照保留。经验 `docs/learnings/observed-paper-entry-must-freeze-intent-before-the-fill.md`；截图、原账本备份和验证证据 `output/playwright/paper-v131-20261016/`。共享 main，仅提交本轮归属文件/新增登记段；未改他人工作区，子代理档位无法核实后由主代理完成。

## 最新进度：近期 15m 信号收益诊断（2026-10-16）

- 北京时间 00:59 冻结 44 条信号：41 条已结束、27 胜 14 负、胜率 65.85%、净 +38.4697R、PF3.834；另 3 条未结束、浮动 -0.5891R。空头 32 条 +40.0215R，多头 9 条 -1.5517R。旧 39 条净 R 未变，新 AI/BREV 两条结束增加 +5.4620R。
- 更正旧观察的资产范围：OKX 扫描全部 live SWAP，按当前官方 instCategory，9 条股票类永续贡献 +24.0945R（62.6%）；32 条加密合约 +14.3752R、胜率 62.5%、PF2.260，不能直接与 Binance 加密合约长历史比较。10/12 入场 27 条贡献 +30.3406R，去掉当天仍有 14 条 +8.1291R。
- 41 条 API/只读数据库投影、净 R 公式、checkpoint 入场开盘与退出价逐笔核对通过，44 个 ID 唯一。25 条追踪退出 +51.3089R，9 条初始止损 -9.6765R，7 条反向退出 -3.1627R，分解调平。没有本批匹配随机对照，仍只作描述性观察；没有改标的范围、交易规则或生产资格。
- 原始证据在 `data/monitor_diagnostics/15m-recent-20261016/`；Notion 原观察 `3fa8856479af813db925f29bace95f79` 已追加更正，原快照保留。经验 `docs/learnings/recent-signal-performance-needs-asset-class-and-event-cohorts.md` 已提交。子代理运行档位无法核实，停止后主代理完成。

## 最新进度：15m 历史多空拆分与单向重放（2026-10-16）

- 同一 V13 普通回踩 15m 历史（Binance 638 币，2023-01-01 至 2026-09-23 04:00 UTC）重放双向/只做多/只做空；只改入场方向，原始反向退出、2R/4ATR、0.2% 成本不变。从预热开始重放，638 流源哈希及逐笔双向 parity 通过，单向新增/替换交易均为 0。
- 多头 5372 笔 +16.71R、PF 1.005；空头 5773 笔 +471.31R、PF 1.142。只做空保留 51.8% 交易与 96.6% 算术净 R，但 2023/2026 空头为负。空头匹配覆盖 13.6%，超额 -6.58bp；前后段均不显著，方向差异周聚类区间跨零，结论 inconclusive，不修改生产。
- Builder `0569714f1b` 先提交后运行；20 项相关测试及标准库独立复算通过。原始结果 `experiments/active/exp-spike-v13-15m-direction-20261016-v1/results_v1/`，配置、收据、逐笔与汇总完整；已登记两个 registry 及 Notion 研究实验 https://app.notion.com/p/3fa8856479af8152ae1bca1fb534c805 。经验 `docs/learnings/direction-filter-needs-admission-replay-and-side-matched-controls.md`。累计 R 不是账户收益率；当前 OKX 观察与 Binance 历史分开。


## 最新进度：Notion #52 磁盘整理与冷文件迁移（2026-10-16）

- **后续分析可继续复用**：原路径软链接与实验/产物登记保留；本轮复查第四批 103 个入口均正常，沿原路径读取行情 CSV.GZ、旧实验事件 CSV.GZ、权益 CSV 和训练 PNG 成功。`AGENTS.md` 已补充主动查登记、跟随选定软链接目录、外盘离线不重建及不做哈希的长期规则；未来分析无需 Owner 重复提供外盘路径。普通递归搜索会跳过软链接，旧脚本的仓内路径限制需按实际任务定向适配。

- **第四批已完成**：103 组（98 个冷数据目录与 5 个单独大文件），796,440 个文件、39,511,886,794 字节（36.80 GiB）已迁至 Elements SE，原路径保留软链接，本机副本已释放。包括研究行情、旧训练/审核图片、实验明细与归档。验收时本机可用 116.07 GiB；历次累计迁出 1,047,635 个源文件、152,705,881,185 字节（142.22 GiB）逻辑数据。硬链接与后台写入会影响实际空间差，不能把逻辑字节数当成物理释放量。
- **第四批未做哈希**：原生复制、路径集合、数量、大小、时间、后台真实读取均通过。54 个受影响数据集保留身份与统计，最终 126 次文件 HTTP 读取及公网 HTTPS 图片读取通过；全目录仍为 197 个数据集、1,279,110 条索引。原有 7,155 条失效索引未在本轮修复。页面显示部分外接目录，配置/受跟踪标注继续留本机；28 项存储检查、71 项层边界检查、7 项前端检查通过。服务存活；验收时扫描正在运行、market_ready=false、model_ready=false，不把文件可读当作生产就绪。
- **收据与恢复**：`data/_storage_migrations/20261016-task52-remaining/summary.json` 为最终验收；同目录 `restore_bulk.py 批次名` 支持目录/单文件恢复，已验证保留其他登记、并发元数据、外盘副本与覆盖保护。外盘 `storage-migration-20261016-task52/receipts-remaining/` 保存 1,534 份收据、1,160,449,417 字节，路径/大小/时间核对通过。103 个精确迁移入口的 Git 展开状态为空、试暂存输出为 0，HEAD 可读；共享 main 未提交混合工作区。
- **保留与经验**：有效 Git 对象、运行环境、当前行情写入目录、运行数据库、活动 Label Studio 图片和受跟踪资产继续留本机。部分目录外接、失败响应保留、身份查询有限重试与硬链接变化核对的经验分别见 `docs/learnings/partial-external-storage-needs-file-level-resolution.md`、`docs/learnings/migration-cutovers-must-retain-http-error-bodies.md`、`docs/learnings/external-volume-lookup-retries-must-preserve-identity-checks.md`、`docs/learnings/migration-hardlinks-need-receipt-backed-change-accounting.md`。读取迁移数据需连接 Elements SE。
- **追加迁移已完成**：再次盘点后新增迁出 36,722 个文件、12,629,362,094 字节（11.76 GiB）：10 组历史行情 5.60 GiB、6 组冷实验目录 4.53 GiB、5 份压缩明细 1.63 GiB。原路径保留软链接，本机副本已释放；验收时本机可用 103.21 GiB。历次累计迁出 251,195 个文件、113,193,994,391 字节（105.42 GiB）原始数据。
- **追加批次不做哈希**：10 个新增外盘数据集 ID、路径、文件计数及大小保留，切换时 20 次、最终 19 次 HTTP 文件读取通过；上一批 5 个数据集复查通过，现有 HTTPS 入口实际读取新迁行情成功。全目录仍为 197 个数据集、1,279,110 条索引。新收据及恢复工具在 `data/_storage_migrations/20261016-task52-more/`；外盘 `storage-migration-20261016-task52/receipts-more/` 已保存 293 份收据，路径、大小及时间核对通过。单文件恢复工具通过原路径、大小、权限、纳秒时间、外盘副本保留及覆盖保护小样本验证。追加 21 个精确本机排除项，迁移路径展开状态为空、试暂存为 0。混合结果目录只迁冷数据，跟踪摘要保留本机；经验见 `docs/learnings/cold-migration-boundaries-must-preserve-tracked-and-web-evidence.md`。
- **后续大数据迁移已完成**：9 组、214,230 个文件、68,577,553,694 字节（63.87 GiB），含三组历史行情 38.61 GiB、四组旧实验结果 22.50 GiB、两组训练样本 2.76 GiB。原路径保留软链接，本机副本均已释放，数据位于 `/Volumes/Elements SE/MacData/zhangzc/fable-trading/` 对应相对目录。加上首批，累计迁出 214,473 个文件、93.66 GiB 原始数据；最终验收时本机可用 93.82 GiB，空间差还受后台写入和 APFS 影响。
- **Owner 已明确取消哈希检验**。本批使用原生复制成功、路径集合、文件数量/大小/时间和实际后台/网页读取验收；不声称内容逐字节一致。原始记录与恢复工具在 `data/_storage_migrations/20261016-task52-bulk/`，以各批 `.complete.json` 为完成依据；外盘 `storage-migration-20261016-task52/receipts-bulk/` 已保存副本，185 个收据文件的路径集合和大小核对通过。Notion #52 已更新为 Done。
- 已按 Owner 明确授权给实际 Python 3.9 添加磁盘访问权限；监控 LaunchAgent 改为 Python 启动 `yoyo.monitor.launch_agent`，再以子进程运行 `caffeinate`，避免外层 `caffeinate` 改变 macOS 权限归属。三组行情网页及研究接口读取、两组训练样本网页展开与 PNG/TXT 实际读取通过；五个数据集的 ID、路径、文件计数和逻辑大小保留。两组图像目录仍为 64,003 / 59,242 条索引记录。读取已迁数据需要连接 Elements SE。
- 相关 21 项目录存储测试、71 项层边界检查、7 项前端检查通过。六个本机迁移软链接入口加入 `.git/info/exclude`，共享 `.gitignore` 未改。经验见 `docs/learnings/background-disk-access-follows-the-responsible-executable.md` 与 `docs/learnings/preflight-removable-storage-before-dataset-discovery.md`。

### 首批记录（Owner 取消哈希检验之前）

- 已迁移 243 个冷文件、31,987,078,603 字节（29.79 GiB）到 `/Volumes/Elements SE/MacData/zhangzc/storage-migration-20261016-task52/`。含 231 份旧临时路径清单、9 份一天前且未打开的 Git 临时打包文件、2 份历史 `upload.tar` 与 1 个视频。有效 Git pack、活跃行情/数据库/模型保留本机。
- 231 份清单按 SHA 去重为 58 份 gzip，23.19 GB 原字节占外盘 261.48 MB；其中 12 份末尾截断，原字节仍全部保存，不认证为完整可再生清单。外盘全部迁移内容合计 9,063,031,175 字节。两份上传包和视频保留原路径软链接，读取它们需要连接 Elements SE。
- 原文件身份、时间、属性、SHA、归档路径、脚本及恢复工具在 `data/_storage_migrations/20261016-task52/`，外盘 `receipts/` 有副本。所有内容校验通过，原路径状态逐个验收；恢复工具 5 项小样本检查通过。Git HEAD 可读；看板 HTTP 200、`stale=false`、`refresh_error=null`，`model_ready=false` 仍保留，不能声称模型生产就绪。
- 本机可用空间由首次约 887 MiB 恢复至约 35 GiB；前后差包含后台活动与 APFS 变化，不全部归因于本轮。Codex 临时 Git 清单持续生成的根因未修复，本轮没有改应用、Git 忽略规则或创建定时清理。经验：`docs/learnings/verified-cold-migration-can-deduplicate-identical-content.md`。


## 最新进度：模拟开单按截图精简（2026-10-16）

- Owner 截图要求已实现：方向按钮保留做多/做空与图标；持仓卡的持仓/可平 USDT 金额及保证金四舍五入显示整数，价格/费用/盈亏保留精度；移除加仓快捷按钮，保护按钮统一“止盈止损”。原始金额与交易计算不变。
- 移除开仓确认开关、旧偏好读取和订单确认弹窗，市价/限价均点击后获取新鲜报价并直接提交；有效期、请求参数固定、防重复、离页取消和幂等重试仍走原直接提交路径。账户与保护价弹窗继续工作。
- 91 项相关前端检查通过；实页核对显示、方向切换与保护弹窗打开/取消，未提交订单。两份服务静态资源及内容哈希与源码一致，刷新生效，无服务重启。初次测试的两项旧显示断言失败已更新，输出均保留在 `output/playwright/paper-desk-simplify-20261016/`；必要经验见 `docs/learnings/removed-preferences-must-not-control-direct-submission.md`。共享 main，未提交混合工作区；子代理实际档位无法核实，停止后主代理完成。


## 最新进度：前端性能、状态恢复与阅读交互（2026-10-16）

- Owner 批准此前列出的七项全部优化：内容哈希缓存与 gzip、按页面加载资源、研究请求合并及按需轮询、URL 子页与会话阅读位置恢复、笔记保存失败反馈和备份、手机折叠概览/目录与章节翻页、转写索引/Worker/分页检索均已实现。
- 首次公共壳层 JS/CSS 从 46 项、1,059,061 字节未压缩响应，降到 9 项、58,475 字节 gzip 响应（约 1.01 MiB → 57 KiB；不含 HTML/API，不代表页面总耗时）。真实浏览器重复访问 9 项均命中磁盘缓存。HTML/API 继续 no-store，资料重新验证，带匹配内容哈希的脚本/样式才可长期私有缓存。
- 研究页按当前视图取依赖，60 秒内复用；手动刷新强制重取；仅有排队/运行任务时 4 秒轮询，空闲降为 60 秒，离页/隐藏暂停。按页面代际隔离模块迟到加载，并为失败资源提供重试。逐页独立启动发现并修复市场表格 CSS 和个人系统跨页入口的隐式依赖。
- 浏览器验收 25 个入口的 320px/1440px 布局，手机文档无横向溢出；交易页等额外等到真实数据完成。章节 Back/Forward/刷新、搜索第 2 页恢复、切页恢复原 847px 阅读位置通过。隔离 localhost 来源实测笔记立即切页保存、模拟配额失败保留草稿、重试成功及刷新后保留；故障注入已还原、测试笔记已清空，未改 127.0.0.1 的用户笔记。390px 阅读另验。
- 转写 40,806 段、8 组查询与旧逻辑逐条对齐；实页“箱体”186 处，4 页，每页最多 50 个 DOM 结果。253 项前端、42 项相关 Python 检查通过。未做实体手机或公网延迟基准；第三方 TradingView 支持/遥测请求仍有 403/网络失败，图表本体加载，本轮未改外部服务。
- monitor 与已有公网网关已重载以启用传输策略，未改交易/通知设置或提交订单。原始对照、测试、截图与 HTTP 证据在 `output/playwright/frontend-performance-20261016/`；六条必要经验在 `docs/learnings/`。共享 main，保留并行书架更新，未提交混合工作区。子代理模型档位未获工具确认，停止后主代理完成。

## 最新进度：四本小册深化为图解研读（2026-10-16）

- Owner 指出原小册太浅并接受“深度研读＋图解教材＋案例/反例/练习”方案。安装 `deep-reader`（GitHub `tronghieu/agent-skills` 固定 `f91c8ed82c566c6b3c4a93eabce498ed7dce6021`），应用已有逐章笔记→主题综合→回原文查关键主张的方法，并目视核对图表。四本现在分别为 8/6/7/10 个单元，共 31 单元，对应原 92 章／节；解释正文约 3.5 万字符，65 幅图表/图解（含 8 张公版扫描页）、62 道练习。覆盖和字数不是质量证明，源文与方法验证仍分开。
- 新增 `trading-books-study.js`：流程、数值表、折线、K线、足迹和公版扫描图；每图标原书/复算/编者构造来源。逐步图的坐标范围、标签和数据表仅用当前可见数据。增加四书对照、术语、答案折叠和含数值的导出；手机宽图在图内滑动。`trading-books.json` 版本 `2026-10-16.1`，最终草稿同步在 `data/reading/task56/deep-study/`。
- 保留原章节与旧笔记；新单元用 `study-*` ID，chapter/unit/mode 路由分开，刷新和分享恢复单元，原文往返按映射。修复误挂原章节翻页器导致按钮不切单元的问题。订单流原件仍链接作者官网或浏览器本地打开，未公开重托管现代原文。
- 回原文纠正并保留边界：Graphophone现金差70美元无法调平；债券价差复利年化约12.31%与原口径分开；Valtos ratio/被套术语和策略例冲突保留。Tape横置表旋转后可核对股数、费用和11−7/8−3⅜−1/4=6½点，未当成现金收益率；Two Good Days原书未标年份，删除未经证实的1908。
- 23项相关前端测试通过；31单元桌面与320px逐一打开，另验390px及深浅主题、揭示/重置、答案、笔记刷新、旧XXIV笔记、导入导出和原文映射。13项公网静态资产逐字节一致；HTTP/2抓取偶发失败，HTTP/1.1复查全通过。证据 `output/playwright/task56/deep-study/`，复现 `node --test tests/monitor/frontend_trading_books.test.cjs tests/monitor/frontend_module_loader.test.cjs`。
- Notion #56及四本资料页新增新版入口/目录并回读核对，状态仍进行中/待验证。未新增完整来源；15本待全文、5项官方资料待逐节整理，不算完成。经验新增 `ocr-failure-is-not-source-absence.md` 和 `reading-units-need-separate-progress-and-route-identities.md`。两位Luna/max子代理实际运行档位已核对；共享main，未提交混合工作区、未重启服务或改交易参数。

## 最新进度：三本技术书原文与完整小册（2026-10-15）

- Owner 要求每本与首本一样、原文放前端并整理小册。本轮完成 Wyckoff《Studies in Tape Reading》12 章与附录、《How I Trade and Invest in Stocks and Bonds》1925 第五版 14 章、Valtos《Trading Order Flow》2015 版 42 节；现为 4/19 本、共 92 章／节完成。三本完整小册已写回原有 Notion 资料页，回读核对全部标题和末章答案；阅读状态已提炼，方法待验证。#56 仍进行中，15 本待全文，5 项官方资料未逐节整理。
- 两本公版完整扫描件与 OCR 已保存到前端，逐章标注印刷／PDF 页码、原文要点、编者解释、练习、答案和边界。Valtos 保留作者版权，直接加载官网当前 `/book/TradingOrderFlow768.pdf`，与旧 `/dl/` 文件 SHA 相同；旧地址在浏览器验收中失败，新地址已独立打开。备用按钮可只在当前浏览器读取已下载原件，不上传，哈希不符拒绝；扩展自动选文件受权限限制，改用系统文件选择器完成本地 PDF 打开、哈希验收和第 41 节（PDF 140）实际显示；原件不上传。官网独立打开成功，嵌入加载仍较慢，备用入口保留。
- 本地 PDF 直接 iframe 被站点防嵌入头挡住，改为按需加载、哈希校验后 Blob 显示，原安全设置保留。订单流教材 COT、trapped 命名、策略案例及 ratio 算术疑点在小册对应节保留。新增 Elder 官方配套资料、Market Wizards 借阅和 Markets in Profile 官方试读，未冒充完整正文。
- 30 项相关前端检查通过，实页确认威科夫末章扫描定位、同章切换、4 本完整状态及 390/320px 阅读无横向溢出。公网 9 项资源逐字节比对见 `output/playwright/task56/technical-booklets-public-check.json`；来源及图表核对留在 `data/reading/task56/technical-sources/`。经验 `docs/learnings/embedded-originals-need-source-and-reader-verification.md`。共享 main，未重启服务或操作交易。

## 最新进度：全站前端细节巡查与优化（2026-10-15）

- Owner 要求整体梳理优化。巡查 25 个菜单入口的实际加载后页面；修复手机导航背景滚动/焦点泄漏，增加抽屉关闭入口、Tab 闭环与焦点恢复（含选择当前页），手机顶栏保留当前页面名称。`/` 只聚焦当前页可见搜索框，编辑、输入法及弹窗不被抢占。
- 共享刷新按钮按页面显示 busy，旧请求不能解除新页面的加载状态；研究列表区分首次加载、失败和空结果，共用并发读取 Promise，离页后的读取/任务轮询失败不再污染其他页面。后台刷新不插入跳动提示；因子/实验/任务表重绘按记录身份恢复焦点，实验翻页定位新页首条并避开顶栏。
- 修复 Bit浪浪七个标签导致的 566px 整页溢出，以及体系流程表单完整选项加载后撑到约 2554px 的问题；收紧嵌套 grid/select 尺寸约束，保留完整选项和研究内容。Bit浪浪手机版统计两列，版本/日期分层，数值与单位保持完整。
- 最终 232 项前端检查通过（本轮专项 57 项）；并行书架更新过程中曾有 2 项内容断言失败，资料/测试更新后全量复跑通过，原失败输出保留。25 个入口在 320px 下文档宽度均为 320px，另实页检查 1440/390px、深浅主题、搜索空结果、菜单键盘闭环、复选框与翻页焦点、交易页全屏进出。未做实体手机测试；没有写交易/通知设置或重启服务。9 份实际服务静态资源与当前源码一致。
- 原始截图、宽度记录、测试输出、改动前源码在 `output/playwright/frontend-polish-20261015/`；无额外报告。按 extract-approach 保存导航焦点、请求归属、列表身份与加载后响应式验收四条经验。共享 main，未提交混合工作区；子代理无法核实实际模型档位，停止后由主代理完成。

## 最新进度：新建账户取消流程修复（2026-10-15）

- Owner 反馈表单仍不能取消。此前只修复入口可见性，本次改为原生账户弹窗；取消、右上角关闭、外侧空白与 Esc 均关闭并清空草稿，焦点返回新建账户。空账户不再强制展开表单，关闭后刷新不会重开。
- 编辑时阻止轮询及打开前发出的后台响应重绘弹窗；离开页面清理弹窗与滚动锁。提交中防止误认为已撤回请求；结果不明时关闭仍保留原幂等请求供核对重试。账户数据、交易参数和真实交易逻辑不变。
- 92 项账户／行情面板／资金分析前端测试通过；实页验证四种退出、重开空草稿、输入焦点和刷新保持关闭。经验 `docs/learnings/empty-state-must-not-force-a-dismissed-form-open.md`。共享 main，不覆盖其他会话改动。


## 最新进度：市场共振证据与行情状态补齐（2026-10-15）

- Owner 对市场次开盘／12h 逻辑要求“那这个要做好啊”。页面现可比较同一批完整事件的 1/4/12/24h，前后时间段和匹配随机对照并列；动态门槛、方向、品种及持有控制已实页核验，390/320px 两段结果都可读。
- 发现原 v5 退出研究只用 z2/v2/60%，不能直接支持默认 z3/v3/75%。新登记 `exp-market-sync-shock-20261015-v6`，配置先提交 `e54cffc97e`，复用已提交 v5 builder，原8退出、次开盘、0.2%成本、种子和2025切分不变。725个事件与v2严格样本逐条一致，724个完整48h路径，固定持有误差<1.5e-7。
- 主组1H上涨ETH为72+54=126次：12h净+50.475/+58.139bp，随机-5.226/-3.613bp，相对随机p0.0015/0.0735。其余7种退出在前段全部比12h差，无一种两段都改善；48h跟踪前段反而少16.592bp、后段多59.055bp（p0.5087/0.3648）。仍是看过的历史，不是独立验证。严格/宽松条件分别加载v6/v5；其它筛选明确显示研究不对应。新增记录 https://app.notion.com/p/3fa8856479af81c2b303ebd29e10680c ，原任务#14/#28已更新。新入场方式未测试，不改策略。
- Binance 451响应体明确为地区访问限制。保持原来源，重试降至30分钟；显示最后成功更新时间及数据源。ready同时检查BTC/ETH因果指标与原协议要求的山寨输入，未知不能显示为无信号；缓存仍停在北京时间10/10 21:00。OKX公共时间接口200已验证；已异步请Owner选择是否改用OKX单独积累，尚无回复，未换源或混用历史。
- 87项相关Python、7项前端检查通过；全库注册表另4项失败，修改前已有条目缺source_commit（基准e54cffc97e已核对），本次新实验与3个产物单独按契约校验通过，不补造旧来源。公网JS/CSS与本地一致，历史API实际返回v6。原始记录/截图在 `output/playwright/workbench-cleanup/market-*`。复现命令在新config，汇总及receipt留results_v5，未额外出报告。
- 磁盘再度耗尽，发现Codex后台`codex-workspace-diff`反复生成约100MB临时路径清单。仅清理62份超一天、未打开且格式已核实的可再生产物，释放6.28GB，清单/哈希已保存；没有改应用本体，持续生成问题尚未根治。行情、模型、交易账本未删。
- 三条经验已保存：退出证据需匹配事件集、数据齐备才算ready、重复差异清单耗尽磁盘。服务已重启载入后端改动，真实下单/通知规则/ACTIVE均未修改；其它会话共享main改动保留。


## 最新进度：交易技术书架扩展与原文补查（2026-10-15）

- Owner 要求继续找原文并补威科夫、SMC／ICT、订单流等技术类。书架从 6 项扩展到 19 本书＋5 项官方课程／文档／短篇，新增 18 项已查重登记 Notion；取消技术书千人评分硬门槛，未核对评分留空。增加类型与原文可用性筛选，完整原文、官方资料、试读、借阅与购买入口分开展示。
- 新增实际完整原文：Wyckoff《Studies in Tape Reading》（1910，204 扫描页）、《How I Trade and Invest in Stocks and Bonds》（原作 1922，实际下载为 1925 Fifth Edition，228 扫描页）、Michael Valtos《Trading Order Flow》（151 页）。作者官网另有 Tom Hougaard 17 页价格行为手册、25 页同名早期文章及 6 页 Part 2；早期文章不冒充 2022《Best Loser Wins》全书。原件、SHA 与 manifest 在 `data/reading/task56/technical-sources/`，现代免费 PDF 前端链接作者原件，不公开重托管。
- 当前 4 本书全文可读，但只有原有《股票作手回忆录》24 章小册完成。新 3 本尚未逐章研读；15 本书仍待全文；5 项官方资料未逐课／逐节整理。原有现代书补了出版社试读、购买和两项有资格限制的图书馆借阅入口。Notion #56 保持进行中，下一步优先研读已取得的 3 本技术书，新增观点全部待验证。
- 24 项相关前端检查通过，实页验证主题／类型／可用性筛选、SMC 官方课程、订单流 PDF 入口、未整理小册的真实状态、原有 24 章原文保留，390/320px 无横向溢出。公网静态资源比对留在 `output/playwright/task56/technical-expansion-public-check.json`。经验 `docs/learnings/book-editions-require-the-downloaded-copyright-page.md`：版权页图像纠正目录年份及 OCR 误读。继续共享 main，无服务重启或交易操作。

## 最新进度：工作台 11 项精简与核查（2026-10-15）

- 通知中心移除历史列表及其请求；信号卡片删掉“信号 K 线”起的下半段，信号中心不再跳 TradingView；移除全站页底 V13.1 参考成交提示。其他页面的图表功能不变。
- 个人原则查明来自 9 月 25 日 Manual-v4 流程草稿。按 Owner 要求删除本地 8 条原则的全部 25 个版本，关闭预置原则模板；备份 `/tmp/fable-workbench-cleanup-before/workspace.sqlite3`，其他 kind 记录数量逐项保持。历史 Notion 来源不删除。
- 新建账户表单移至页首并自动聚焦；此前被图表/交易区推到首屏下方。在隔离 8878 账本完整创建两个模拟账户，未提交订单，验收后服务已停止。
- 模型中心 35 项改为可展开目录行；YOLO 记录改为紧凑表格、阶段筛选、20 条分页，运行快照折叠。修正 `gold` 单词误把黄金交易实验归入 YOLO 的问题，现 71 项。实验登记 430 项配中文问题标题、30 条分页；中文标题绑定原 question 哈希，原文/结果/资格不改，详情可展开原始材料。
- 208 项前端、81 项相关 Python 检查通过；桌面及 390/320px、深浅主题，模型搜索/展开刷新、实验和 YOLO 搜索分页、原则空状态、通知无历史、信号无图表链接均实页检查。15 项公网静态资源与本机匹配。原始验证与截图 `output/playwright/workbench-cleanup/`，不额外生成报告。
- 市场共振的 ETH 次开盘/12h 是历史比较基线；v5 没有替代退出在前后段同时显著胜出。当前 Binance 返回 451，缓存 K 线停在北京时间 10 月 10 日 21:00，页面诚实显示停更。Arthur 服务在线但采集心跳为空，目标 Chrome Discord 标签页在登录页，最后入库北京时间 10 月 15 日 01:30；需 Owner 登录并保持频道/桥接可用，当前本地模拟账本无成交，不能称跟单已恢复。
- 15m 快照（20:21 北京）39 笔已结束参考段，25 胜14负、净 +33.0077R、PF3.432。空头30笔 +34.5595R，多头9笔 -1.5517R；北京时间 10/12 入场27笔贡献 +30.3406R。发现延迟均<30min，但同轮行情相关性高、没有匹配对照，非账户收益和稳定优势证明；已按唯一键保存 Notion 描述性观察 `3fa8856479af813db925f29bace95f79`。
- 验收发现磁盘只剩147MiB，服务持续 SQLite disk I/O error。核对并清理20份无占用、一天前的可再生临时 Git 路径清单，释放2,026,954,512字节后状态快照恢复，TG配置重新正常显示。仍有大量同类临时清单，生成源未查，空间需另行治理；未碰数据库、行情或模型。清理路径/哈希有原始清单。
- 经验已按 learning law 保存：账户表单邻近入口、中文展示绑定问题原文、工作流歧义词需上下文、SQLite I/O 与临时清单空间。共享 main，未提交其他会话改动；子代理因无法核实实际模型档位停止，由主代理完成。

## 最新进度：交易书架原文／小册标签（2026-10-15）

- Owner 要求 tab 加原文小册。顶部现为「书架／原文／小册／笔记与复习」，原文与小册共用当前章节；已接入《股票作手回忆录》24 章英文正文并保留完整电子版文件，原文按需加载，失败可重试。目录重绘保持当前章可见，原文浏览不标记小册已读。
- 22 项相关前端检查通过，24 章逐一去空白全文对比一致；实页验证 I、XXIV 原文与小册同章切换及 390px 无横向溢出。经验 `docs/learnings/original-and-booklet-tabs-share-chapter-identity.md`。仍为一本全文、五本待原文；未重启服务或操作交易。

## 最新进度：Notion #56 交易书架与首本完整小册（2026-10-15）

- 任务要求热门高评分书籍原文逐本研读、小册与前端集成。本批选择 6 本（Goodreads ≥4.1、评价 ≥1,000，保留版本、检索日期和来源），入 Spike 交易资料库；前端「学习复盘 → 交易书架」支持搜索、排序、逐章阅读、笔记、间隔复习、小册导出及进度导入导出。
- 《股票作手回忆录》1923 英文正文 I–XXIV 全部逐章研读；24 章中文分析小册含原文锚点、具体案例、边界、练习与自查，前端与 Notion 同步。原文和 SHA/逐章 manifest 在 `data/reading/task56/`（不入 git）。不复用现代译文，作者故事不当策略验证。
- 其余五本《以交易为生（新版）》《金融市场技术分析》《交易心理分析》《金融怪杰》《Best Loser Wins》只有书目导读，未取得完整原文；任务附件实页确认是空模板。#56 保留进行中，下一步须取得本人已购/授权全文后逐章补齐，不能把书评或下载状态冒充通读。编者覆盖、Owner 本地已读及知识库验证状态分开记录。
- 21 项相关前端检查通过；浏览器核验笔记刷新保留、导入冲突与错误保护、完整 24 章导出、待原文无已读按钮、失败重试，以及桌面/390/320px 深浅主题。截图和原始验收在 `output/playwright/task56/`。源码 `trading-books.js/css/json`；经验 `docs/learnings/book-source-coverage-is-not-reader-progress.md`。继续共享 main，未提交其他会话改动，未重启服务或操作交易。

## 最新进度：历史开单仅已结束，并增加全量统计（2026-10-15）

- Owner 纠正“历史开单”定义：只包含已结束记录，盈利、亏损及保本一起列出，无状态筛选。新增后端 `outcome=closed`，先筛选和聚合全部记录，再排序/分页；按退出记录日归属今天/本周，默认最近结束优先。卡片保留原筛选，切换视图更新查询代际并清空旧结果。
- 历史统计提供结束笔数/胜负保本、累计与平均/中位净 R、胜率、盈利因子、平均盈亏比、平均盈利/亏损、最佳/最差单笔、累计 R 最大回撤、平均持有时长、可判定的连胜/连亏，以及周期/方向/退出原因拆分。净 R 直接使用既有退出投影，不再扣成本；同刻退出合计计算回撤，异号同刻退出不虚构连续顺序。缺失数据、无亏损分母均保持未知；等风险 R 不冒充账户收益率。
- 16 项后端统计/API/V13.1 集成检查及 40 项前端检查通过，包含超过 2,000 条完整聚合、分页与排序不改统计、跨日退出、空值、保本、全胜全负、同时退出及切换迟到响应。此前旧 V1 分析夹具失败没有通过修改初始化门来绕过。
- 实页核验历史无状态控件、盈亏都保留、卡片盈利筛选不污染历史、分页统计不变、今天按退出日、详情展开刷新保持；1440/390/320px、深浅主题通过，手机无页面横向溢出。全量 61 笔结束记录逐笔核算与分组对账通过（33 胜/28 负；仅为当时快照）。
- 服务已重载，备份 `/tmp/fable-before-closed-signal-history-20261015-200746/`；原有 23 条模拟成交逐字段保留，未提交订单、改成本或信号执行。证据在 `output/playwright/signal-history-closed/`；经验 `docs/learnings/closed-history-is-a-cohort-not-a-layout.md`。继续共享 main，未提交他人工作区变更。

## 最新进度：信号中心历史开单列表（2026-10-15）

- 信号记录区默认显示“历史开单”，保留“信号卡片”切换；复用同一全量账本 API、方向/周期/合约/状态筛选、R 排序与每页 24 条分页。列表显示参考入场价/时间、初始止损、退出价、净 R/状态和持仓 K 线数；手机保留四列，完整字段在详情展开。
- 详情显示完整北京时间、保护价、退出原因、成本、更新时点及记录 ID，保留 TradingView 和个人观察入口。入场与退出只读现有 performance 字段，未知不补零、不用确认收盘价代替入场。按记录 ID 保留展开状态，筛选或翻页清除。
- 37 项相关前端检查通过。额外 Python 检查 7 通过、8 失败：`test_signal_analytics.py` 旧夹具仍写 `notification_policy:v1_bark_arm`，当前代码要求 V12.8 初始化键；本次未改 Python 文件或初始化门。当前 V13.1 的 5 项集成测试全部通过。
- 浏览器实页核验 95 条记录的分页、卡片切换、展开与刷新保留、方向/状态筛选、R 排序、空结果和键盘展开收起；1440/390/320px、深浅主题通过，手机无横向溢出。公网 index/app/history CSS 与本机字节一致，无需重启。未提交订单或更改信号/成交数据。
- 截图与原始验证在 `output/playwright/signal-history/`；经验 `docs/learnings/signal-history-must-share-the-ledger-query-and-projection.md`。共享 main 未提交其他会话改动；子代理因工具无法核实实际模型档位而停止，主代理完成。

## 最新进度：资金摘要移到顶部行情区（2026-10-15）

- 按 Owner 截图将六项账户资金摘要从持仓下方移到顶部行情右侧；桌面三列两行、手机两列三行，24h 行情数据置于下沿。复用原有资金节点与轮询更新，TradingView 容器保持原位。
- 74 项相关前端检查通过；1440/390/320px 实页核验，窄屏金额不换行且无横向溢出，资金摘要仅出现一次，切换账户即时同步。公网 JS/CSS 与本机一致；截图 `output/playwright/task54/top-balances-mobile.png`。仅前端，未重启服务或操作正式订单。

## 最新进度：移除布局下拉框（2026-10-15）

- 按 Owner 手机截图移除“图表在上 · 持仓在右”布局选择器；保留已保存的排列和图表展开/收起功能。7 项行情前端检查通过，390px 实页无选择器、无横向溢出，公网 JS/CSS 与本机一致。截图 `output/playwright/task54/layout-control-removed.png`；仅前端，未重启服务。

## 最新进度：账户交易统计与移除导出按钮（2026-10-15）

- 移除交易页导出按钮，底部新增每账户全部记录的交易统计：已结算净收益/收益率、完整交易数/胜负持平、胜率、平均盈亏比、盈利因子、总费用/成交额、平均盈利/亏损、最佳/最差单笔、最大余额回撤及平均持仓时长。紧凑桌面四列、手机两列，支持深浅主题。
- 后端从全部成交按完整仓位生命周期汇总（加仓和分批平仓只算一笔，全部平完后进入胜负统计），费用全扣。回撤按完整余额事件序列计算，不从压缩曲线推断、不冒充浮动权益回撤；统计为账户全生命周期，不随日历月份改变。没有已平仓交易时胜率显示缺失，无亏损不生成无穷比值。
- 194 项模拟交易 Python 测试和 100 项前端测试通过。额外 `tests/boundaries` 165 通过、8 失败：`test_authoritative_docs` 两项（AGENTS/CLAUDE 差异、文档里的 API 路径）、`test_no_cross_repository_bridges` 一项（`ma_morphology_reuse_recover.py` 旧路径）、teacher 隔离五项（现有产物登记缺 `source_commit`）。这些文件未在本次修改，未绕过门禁或修改研究状态。
- 三账户只读账本对账通过，浏览器验证切换账户、空统计、1440/390/320、深浅主题和口径说明；公网三个静态资源一致、统计接口成功、后台运行无错误。服务已重载，备份 `/tmp/fable-before-account-statistics-20261015-185506.sqlite3`，原 21 条成交逐字段保留；未提交正式交易。
- 原始截图/验证 `output/playwright/task54/statistics/`，经验 `docs/learnings/account-statistics-count-complete-position-lifecycles.md`。子代理工具无法核实实际模型档位，已停止，主代理完成；继续共享 main，未提交工作区变更。

## 最新进度：平仓直接提交（2026-10-15）

- Owner 要求去掉平仓确认弹窗。市价平仓、限价平仓均按选定百分比取新鲜报价后直接提交；开仓确认开关独立。复用原冻结数量、幂等回执、重复点击锁和离页取消逻辑。
- 98 项前端检查通过，本机与固定公网 `paper-desk.js` 与本地一致。仅改前端，未使用正式持仓测试平仓、未重启服务。经验 `docs/learnings/direct-close-uses-the-existing-quote-pipeline.md`；继续共享 main。

## 最新进度：委托展示单位与仓位倍数（2026-10-15）

- 按 Owner 标注固定持仓/委托为 USDT，删除单位切换及历史委托折叠区。沿用前轮紧凑卡片，无等待报价说明与常态检查 footer；真实后台故障提示仍保留。
- 持仓/委托显示当前名义金额除以当前账户权益的仓位倍数，缺失或非正权益显示“—”。旧账本未保存原始选定倍数，不用合约杠杆或当前表单默认值冒充；已平仓记录的旧杠杆明确标作“合约”。保证金计算和账本不变。
- 91 项前端检查通过；1440 与 390px 浏览器核验，手机无横向溢出、缩放 1，公网两份静态文件与本机一致。截图/原始验证 `output/playwright/task54/orders-cleanup/`，经验 `docs/learnings/displayed-exposure-needs-equity-not-margin-leverage.md`。未提交/撤销正式订单，未重启后端，继续共享 main。

## 最新进度：资金曲线视觉与交互重整（2026-10-15）

- 修复全局 SVG 图标描边继承到坐标文字的粗糊问题：图表独立重置 stroke，坐标改用稳定 CSS 字号的 HTML 标签。移除英文眉题、压缩数值与周期区，细线配淡色分段面积，收益变化带区间百分比；余额仍为真实阶梯，权益缺失不连线。
- 去掉原生粗滑杆，鼠标悬停/点选显示日期和金额，触屏点选，键盘左右/Home/End 可选数据点，Escape 恢复最新点。指针仅更新提示节点，缓存几何模型；历史刷新恢复图表键盘焦点。余额/权益、7天/30天/全部及日历功能保留。
- 90 项前端检查通过，覆盖阶梯/缺口、时间距离选点、区间余额继承、不补权益、零值单点、触控/键盘。浏览器桌面、390/320px、深浅主题与区间切换通过，手机无横向溢出、缩放1；三份公网静态资源与本机一致。仅前端，未提交正式订单、未改账户或保护价。
- 截图与原始核验 `output/playwright/task54/curve/`；经验 `docs/learnings/icon-svg-defaults-must-not-style-data-charts.md`。前轮止盈止损底部说明已去掉并收紧空白（59 项下单前端检查通过）。继续 main，未提交共享目录改动。

## 最新进度：三面板布局与订单区压缩（2026-10-15）

- 按 Owner 截图去掉委托“等待新鲜报价触价/时间”行和常态自动检查 footer，真实拒单原因及后台异常仍可见。USDT/币数量并入记录标签栏，压缩订单间距、撤单按钮及空状态；资金摘要紧跟记录区，减少下单卡片旁的空白。
- 行情、下单、持仓改为三个独立布局槽，图表上方“布局”可选四种排列，默认图表在上、下单在左、持仓在右；支持持仓左右换位、图表/下单左右换位，浏览器本地保存。手机单列按对应顺序堆叠，320px 资金卡片两列避免数字断行。
- TradingView 节点保持连接，切换仅调整 CSS grid-area。85 项前端检查通过；实测四种布局坐标、刷新记忆、1h 周期跨布局及表单重绘保留、单位切换、390/320px 无横向溢出和缩放 1。固定公网原生全屏进入/退出兼容；三份静态资源与本机相同。仅前端修改，未重启后端、未提交订单/撤单或修改保护；验证期间观察到正式订单数变化，本代理未执行订单写操作。
- 原始截图/检查 `output/playwright/task54/panel-layout/`；经验 `docs/learnings/panel-reordering-should-not-reconnect-live-embeds.md`。TradingView 自身 support 接口仍有 403，图表和周期操作正常；未做实体 iPhone Safari。继续 main，共享工作区未提交。

## 最新进度：交易页全屏、全站侧栏折叠与面板简化（2026-10-15）

- 按 Owner 标注，将账户/新建/刷新/导出工具并入行情卡片右上，去掉币种箭头块与开仓/平仓切换栏。平仓从持仓卡片进入，并有返回开仓；止盈止损默认折叠，输入/展开状态在表单重绘时保留，折叠后已填价格显示“已设置”。统一 select 箭头与右内边距，删除仓位公式说明，保留资金不足错误。
- 全站顶栏新增侧栏收起/展开按钮，桌面状态保存到本地并跨路由/刷新保留，手机继续临时抽屉。新 `workspace-display.js/css` 独立管理桌面偏好、移动抽屉和全屏；侧栏隐藏时不可键盘聚焦。
- 交易工具栏“全屏”覆盖原交易根节点（图表、下单、持仓、资金分析都在内，可滚动），支持退出按钮和 Esc。优先浏览器原生全屏，缺失/拒绝时仍可视口铺满；全屏外元素暂设 inert，退出恢复；异步迟到请求不能让已离开的页面重入。整个过程不移动 TradingView iframe。
- 81 项前端检查通过。浏览器 1440/390/320px 核验原生全屏与退出、Esc、侧栏刷新及切页记忆、手机抽屉、周期 1h 和止盈止损输入保留；320px 无溢出、缩放 1。公网原生全屏实测，8 个页面/静态资源与本机字节一致；自动规则后台正常。未做实体 iPhone Safari 测试，未提交正式订单，后端逻辑与账本结构未改。原始截图和检查 `output/playwright/task54/layout/`。
- 经验 `docs/learnings/fullscreen-and-sidebar-need-separate-lifecycles.md`。保持 main，未提交共享工作区改动。

## 最新进度：模拟交易行情、TradingView 与历史仓位（2026-10-15）

- 参考 Owner 两张 OKX 手机截图：顶部选择合约、实时 OKX 最新成交价与 24h 涨跌/高低/估算成交额；官方 TradingView Advanced Chart 使用精确 OKX USDT 永续符号，显示 K 线与成交量，保留原生周期/指标。手机先行情图再下单，桌面左右双栏；无订单簿。图表可收起、打开完整图表，跟随明暗主题。
- 新 `paper-desk-market.js` 保持 iframe 原位，表单/账户轮询不重载图表；切币取消旧行情请求，离页释放轮询。只读 `/market` 每 3 秒复用 OKX 公共缓存，不保存执行报价；超过 15 秒显示过期，不用中间价冒充最新成交。下单仍按独立买卖报价。
- 历史仓位改为紧凑三列卡片与可展开关联成交；均价来自全部开/平仓成交加权，收益率明确除以累计开仓保证金，避免把最后剩余仓位均价当历史均价。已平仓判定兼容 Decimal 的 `0.00`；不重写成交。
- 256 项 Python（含边界）与 73 项前端检查通过。真实浏览器核验 BTC/ETH 永续、1h/4h、桌面指标菜单、深浅主题、表单交互及轮询保留周期、关联成交展开保留、390/320px 无溢出与缩放 1。固定公网手机 ETH 图表及报价可见；未做实体 iPhone Safari。TradingView 自身 support incidents 接口 403，不影响本次 K 线和指标菜单加载；外部组件仍依赖网络和其币种覆盖。
- 正式 8766 已重启；后台自动规则正常，首页和三份新静态资源公网/本机字节一致。备份 `/tmp/fable-task54-before-chart-20261015-173333.sqlite3`；更新时原 3 账户、8 持仓记录、18 成交、62 报价、27 回执、1 委托原行逐条保留。代理未提交正式订单，隔离 8878 已停止；用户后续操作/自动规则继续正常变化。原始验证和截图 `output/playwright/task54/chart/`。
- 经验：`docs/learnings/market-charts-must-outlive-trading-form-renders.md`、`docs/learnings/closed-position-display-needs-all-fill-history.md`。未切分支或提交共享目录其他修改。

## 最新进度：模拟开单加仓、费率、强平与资金分析（2026-10-15）

- 同合约允许多笔开仓挂单与继续加仓。同方向、同合约杠杆合并，按剩余数量加权均价；不同方向或杠杆独立。持仓默认显示 USDT，可切币数量，新增加仓入口；已有止盈止损及百分比平仓保留。卡片收紧间距，手机输入至少 16px 避免聚焦缩放。
- 新成交采用 OKX 普通等级 Maker 0.02% / Taker 0.05%；限价立即穿过买卖价按 Taker 与可成交报价，真正挂单成交按 Maker。旧成交费用不回写。BTC/ETH 合约最高 100×、其他币最高 20×，另校验公开风险档位；权益仓位倍数默认 10×，与合约杠杆独立。币种按 24h 成交额估算排序并支持搜索。
- 显示基于 OKX 标记价、公开维持保证金档位的全仓条件爆仓价（假定其他币价不变）。账户权益触及维持保证金加平仓费用时，后台事务内撤销全部挂单、平掉该账户持仓并记录清算费用/证据；缺失或过期风险数据不伪造价格。当前是全仓一次性强平模拟，未实现 OKX 分档减仓、保险基金、ADL、资金费和深度滑点。
- 每账户底部新增余额/权益曲线、7/30天/全部范围、数据点查看和北京时间每日净盈亏日历。余额与日历从全部成交及费用重建；浮动权益每分钟采样，历史不存在的数据不补造。导出 schema v6 保存采样、费用角色及清算证据。
- 176 项账本/API/保护、71 项边界、67 项前端检查通过。隔离 8878 实测同仓加仓合并、持仓并存两笔同币挂单、搜索成交量排序、山寨币 20× 上限；最终 3 笔成交、1 个已平仓合并仓位、2 笔已撤挂单，余额 962.495659769716378 精确对账，仅为功能验收。390/320px 无横向溢出，确认弹窗缩放保持 1；未做真实 iPhone Safari 实机检查。隔离服务已停止，原始证据 `output/playwright/task54/exchange-*`。
- 正式 8766 已重启，固定公网新界面及三份静态资源一致、后台运行无错误。更新前备份 `/tmp/fable-task54-before-exchange-20261015-160841.sqlite3`；迁移验收时 3 账户、5 持仓记录、9 成交、1 委托、17 回执的原字段逐条未变。本代理未提交正式测试单；用户后续正常操作与自动规则执行会继续改变账本。Notion #54 同步本轮验收与范围。
- 经验笔记：`additions-must-weight-the-remaining-position.md`、`limit-order-fees-depend-on-liquidity-role.md`、`cross-liquidation-is-an-account-condition.md`、`equity-history-cannot-be-reconstructed-from-fills.md`（均在 `docs/learnings/`）。下方成本与无强平描述是旧版本历史。

## 最新进度：持仓止盈止损管理与百分比平仓（2026-10-15）

- 按 Owner 要求，持仓卡片新增设置/修改止盈止损弹窗，留空或清空后保存可取消；显示剩余仓位的预计净盈亏。修改以新鲜可平仓价格为基准，支持把止损移入盈利区间；部分平仓保留剩余保护。修改生效时间持久化，早于修改的缓存行情不得触发新设置。
- 平仓改为 1–100% 整数比例与 25/50/75/100% 快捷按钮，市价、限价均支持。后端按当前剩余持仓计算，100% 精确清尾；确认时固定数量并验资/验可平量。已有平仓挂单占用时明确拒绝超量，百分比基数不偷偷缩成可平量。
- 152 项账本/API/保护测试、71 项层间边界、61 项前端检查通过。隔离 8878 实测 0.01 BTC 设/改/取消保护 → 25% 市价平 0.0025 → 剩余 0.0075 的 50% 限价挂 0.00375 → 超量拒绝 → 撤单 → 100% 平尾仓；3 笔成交逐笔对账。1440 桌面、390/320 手机编辑和保存通过，控制台无警告或错误。原始证据 `output/playwright/task54/management-*`。
- 正式 8766 已重启且比例报价接口成功，独立后台运行。备份 `/tmp/fable-task54-before-management-20261015-140115.sqlite3`；原账户/持仓/成交/委托/回执的已有字段逐条保留，本代理未提交正式测试订单或改保护价。导出 schema v5 保存保护修改及引用报价；新增时间列兼容旧仓位。
- 经验 `docs/learnings/position-management-needs-current-price-and-remaining-exposure.md`。原成本、触价按实际观察报价、无深度滑点/资金费/强平等边界保持不变。

## 最新进度：仓位倍数与合约杠杆拆分（2026-10-15）

- Owner 纠正“全仓”的含义：默认合约杠杆 100×、实际仓位为当前账户权益的 10×。两个独立下拉均沿用九档；自动计算数量，保证金为名义金额除以合约杠杆，再加原开仓费验资，资金不足拒绝而非自动缩单。下方显示仓位金额、保证金加费用、下单后可用；止盈止损估算同步使用新的仓位数量。
- 134 项账本/接口/止盈止损测试、71 项层间边界与 55 项前端检查通过。隔离 8878 浏览器分别通过确认弹窗和直接提交挂两币：权益 1,000、每笔名义 10,000、合约 100×，可用依次 890、780；撤销后恢复 1,000，无成交。390/320px 无横向溢出，控制台无警告/错误。合约 100→50 保持盈亏不变、保证金翻倍；仓位 10→5 则数量和盈亏减半。
- 正式 8766 已重启，后台运行且新 `equity_multiple` 报价通过；固定公网与本机 JS 字节一致。更新前备份 `/tmp/fable-task54-before-exposure-20261015-134757.sqlite3`。已有订单/持仓/成交/回执原记录保留；Owner 在更新期间操作了正式账户，新增成交与资金变化逐笔一致，不能把整个账本声称为未变。本代理仅在隔离环境下单，正式仅 GET。
- 原来的 10× BTC 挂单已被撤销，当前账本有 100× 新仓位；以页面实时值为准。旧单不自动改杠杆或数量。原始证据 `output/playwright/task54/exposure-*`；经验 `docs/learnings/exposure-multiple-must-be-independent-of-margin-leverage.md`。下方全可用资金与旧止盈止损数字属于历史版本。

## 最新进度：止盈止损实时净盈亏估算（2026-10-15）

- 按 Owner 要求，止盈、止损价格下方即时显示预计净赚／净亏 USDT；全仓预算包含开仓费，净盈亏再扣平仓费。限价使用委托价，市价多头使用卖一／空头使用买一；不重复乘杠杆。费率沿用后台快照，未调整成本或下单规则。
- 输入、杠杆与账户资金变化联动；后台只更新估算结果与可用资金，不重建正在编辑的输入框。无资金、缺失费率、错向价格和过期行情明确提示，不编造金额；估算报价独立于提交报价，不会弹窗或下单。
- 51 项前端检查通过。隔离 8878 浏览器验证 390px 浅色／深色、市价多空与限价、杠杆调整、轮询时保留输入焦点，无横向溢出。截图参数 1,000 USDT、10×、委托 80,960、止盈 81,964、止损 80,678 显示 +102.86 / −54.25 USDT，按开平仓各 0.1% 计费。原始证据 `output/playwright/task54/pnl-*`。
- 正式服务已即时提供新静态文件，无需重启；本轮未提交测试订单。金额为设定价格下的估算，实际触价跳价会不同。经验：`docs/learnings/live-exit-pnl-must-use-sized-exposure-and-both-fees.md`。

## 最新进度：模拟开单改为固定公网入口（2026-10-15）

- Owner 要求手机收藏后不换链接。复用本机已有 ngrok 账号，固定入口 `https://strifeless-bluffly-channing.ngrok-free.dev/#paper-desk`；显式绑定域名的 `com.spike.public-ngrok` LaunchAgent 已启动，Mac 登录后自启动并自动重连。
- 仍经原 Caddy 网关保留同源检查、凭据清理和远程桌面动作阻断，沿用 Owner 的整个工作台免登录口径。新地址实测可用后停用旧 Quick Tunnel，旧临时链接已退役；`start/status/stop` 自动识别本机保存的固定入口。
- 39 项公网网关检查通过；真实公网首页/账户 API/JS 均 200，390px 浏览器显示模拟开单且无横向溢出，重启 ngrok 后同一域名仍返回工作台。未提交测试订单或改动用户挂单。证据 `output/playwright/task54/fixed-public-verification.json` 与 `fixed-public-mobile.jpg`。
- Mac 与服务必须在线；免费版有 Visit Site 提示（Cookie 隐藏 7 天）及每月 20,000 请求 / 1 GB 配额。固定地址不等于无限额度或常驻云端。凭据未复制入仓库；配置/入口说明保存在用户 Library 下。经验见 `docs/learnings/fixed-public-url-needs-persistent-domain-and-reconnect.md`。

## 最新进度：模拟开单公网手机入口已恢复（2026-10-15）

- Owner 询问手机公网访问。已有 Quick Tunnel 的本机网关正常，但旧连接持续报 `Unauthorized: Tunnel not found`；只重启 `com.spike.public-tunnel` 后重新注册，监控/账本/网关配置未改。
- 当前临时入口 `https://tabs-sources-orlando-pensions.trycloudflare.com/#paper-desk`；首页、模拟账户 API、最新版 JS 均 200，真实浏览器 390px 显示“区间练习”、开多按钮与确认开关，页面无横向溢出。未提交公网测试订单。
- 沿用此前 Owner 的整个工作台免登录配置，持有链接即可访问。Mac 与服务需在线；免费域名重建后改变，不保证长期可用。本机 `PublicTunnel/access.txt` 已更新。原始检查 `output/playwright/task54/public-verification.json`；经验 `docs/learnings/tunnel-process-status-is-not-public-reachability.md`。

## 最新进度：任务 54 可选直接开仓（2026-10-15）

- 按 Owner 要求移除界面“预览”字样，按钮为开多/开空（平仓为平多/平空）；新增“开仓前确认”开关，默认开启并在当前浏览器记住选择。关闭后市价/限价开仓取新鲜报价后直接提交，开启时仍走确认弹窗；平仓继续确认。
- 两种开仓方式共用报价快照与提交函数，保留止盈止损、杠杆、资金检查、请求幂等和失败重试。报价获取期间离开再返回页面会丢弃旧请求，防止迟到报价直接下单。
- 44 项前端检查通过；隔离 8878 真实行情走通限价挂单/撤销、直接市价开仓和确认平仓，2 笔成交、1 个已平仓仓位、1 笔已撤委托，余额精确对账。正式账本仍为 1 账户、0 持仓/成交/委托、1 回执。1440/390 界面通过，隔离服务已停止。
- 原始检查、导出、截图 `output/playwright/task54/direct-*`；经验 `docs/learnings/direct-entry-reuses-the-quote-submission-pipeline.md`。仅前端/测试改动；正式浏览器恢复默认开启确认，未提交正式测试订单。

## 最新进度：任务 54 成交确认弹窗与费率说明（2026-10-15）

- Owner 要求预览弹窗，覆盖下方“桌面右侧预览”旧设计。现为居中原生 dialog，价格、保证金、费用、止盈止损分组；返回修改保留表单，Esc/遮罩可关闭，Tab 循环，过期禁用，提交中不可重复操作，结果不确定时退出弹窗显示原请求重试。
- 36 项前端检查通过；真实浏览器核验 1440 桌面深浅主题、390×844 手机、320×568 小屏滚动及焦点/过期/取消，限价做多和市价做空预览正常，控制台无警告或错误。正式账户未提交测试订单；账本仍为 1 账户、0 持仓/成交/委托、1 回执。
- 费用展示读取后台 preview fee 与 snapshot fee_rate，明确标注“模拟费率 0.1% · 按名义金额计算”。未改成本：截图 9.901 USDT 来自统一 0.1%；OKX 官方合约示例 Maker 0.02% / Taker 0.05%，该成交额对应约 1.98 / 4.95 USDT，但实际账户等级未知，限价立即成交也可能是 Taker。来源 https://www.okx.com/help/trading-fee-rules-faq 。
- 原始证据 `output/playwright/task54/modal-*`；经验 `docs/learnings/trade-dialog-must-follow-the-request-lifecycle.md`。仅前端与对应测试调整，后台账本/费率未修改，仍为共享 main，未提交其他会话改动。

## 最新进度：任务 54 交易界面样式优化（2026-10-15）

- 默认开仓杠杆已按 Owner 改为 10 倍；本轮统一资金条、输入框、按钮、明暗主题与记录空状态，突出账户权益；全仓/杠杆/反转同排，止盈止损分组，成交预览移到桌面右侧记录上方，手机依次排列。
- 27 项前端回归通过；正式页面实测限价预览/取消、方向反转、币种搜索、成交标签、深浅主题，1440/390/320 宽无页面横向溢出；未提交测试订单。截图 `output/playwright/task54/polish-light.jpg`、`polish-dark.jpg`、`polish-mobile.jpg`。初次 `polish-before.jpg` 因 viewport 尚未稳定被裁剪，不用于前后定量对比。
- 仅 `paper-desk.js/css` 展示层调整，无后端或账本改动；经验见 `docs/learnings/trade-confirmation-belongs-beside-the-ticket.md`。共享 main 未切分支或代提交。

## 最新进度：任务 54 开仓止盈止损已上线（2026-10-15）

- 市价、限价开仓均可选填止盈价和止损价；校验多空方向，限价成交后才启用。后台约每 5 秒按成交后的新鲜买一（多头）/卖一（空头）触发，按实际观察报价平掉剩余仓位，跳价不保证触发价。
- 自动平仓与同仓位平仓挂单撤销共用 SQLite 事务；部分平仓继续保护剩余数量，手动/自动竞争不会重复平仓。成交记录标注止盈/止损原因，导出 schema v4 保存保护价与报价证据；旧仓位迁移默认空，不擅设保护价。
- 250 项 Python、27 项前端检查通过。隔离 8878 提交带保护限价单后关闭页面，后台完成入场及自动止损，2 笔成交精确对账；1440 桌面和 390 手机通过，控制台无错误。该跳价场景仅为功能验收。
- 正式 8766 已重启，自动检查运行；原有 1 个账户、1 个回执逐条未变，持仓/成交仍为 0。备份 `/tmp/fable-task54-before-stops.sqlite3`；原始检查、导出及截图 `output/playwright/task54/stops-*`；Notion #54 已同步。
- 经验：`docs/learnings/protective-exits-must-share-the-position-transaction.md`。服务停止/行情缺失时不执行、不补历史触价；资金费、深度滑点、强平仍未建模。共享 main 未切分支、未代提交其他会话改动。

## 最新进度：任务 54 紧凑面板、全仓与限价挂单已上线（2026-10-15）

- Owner 最新口径覆盖下方历史 UI：参考其 OKX 截图做简版，压缩头部留白；开仓使用全部可用资金、自动预留手续费计算数量，无数量/理由输入；杠杆仅 1/2/5/10/15/20/30/50/100，默认做多、箭头反转做空。搜索下拉保留，持仓/委托/成交使用标签切换。
- 持久化限价开仓/平仓与撤单；冻结开仓保证金及费用、锁定平仓数量，SQLite 事务处理并发。独立后台每约 5 秒使用提交后的新鲜 bid/ask 触价，按委托价保守全额模拟成交；关闭页面不影响，停机不补历史触价。仍无强平/资金费/深度滑点/自动止盈止损。导出 schema v3 包含订单与触价证据。
- 215 项 Python + 22 项前端检查通过；隔离 8878 浏览器实际走通 3 笔限价委托（1 撤销、2 成交）、5 笔成交、2 个已平仓仓位，余额与逐笔盈亏费用精确对账。1440 桌面、390 手机无溢出，明暗主题通过；自动刷新不抢占表单或覆盖预览。
- 正式 8766 已重启更新，挂单后台正常；原有 1 个账户、0 持仓、0 成交、1 个回执逐条未变。正式测试下单为 0。Notion #54 已同步。原始截图/检查/导出在 `output/playwright/task54/compact-*`；隔离账本 `/tmp/fable-task54-compact-qa`，更新前正式备份 `/tmp/fable-task54-before-compact.sqlite3`。
- 新增 `paper_desk_limits.py` 与相关测试；三条 learning 记录全仓费用预算、挂单原子冻结、后台刷新不占表单锁。共享 main 未切分支、未代提交其他会话改动。

## 最新进度：任务 54 币种改为搜索下拉（2026-10-15）

- Owner要求币种下拉选择并支持搜索。新增公开OKX instruments目录（live/linear/USDT永续，缓存5分钟），当前483个合约；前端支持缩写、完整合约名、不区分大小写与分隔符，精确匹配排第一，点击/方向键/Enter选择、Escape取消。选币后仍取15秒有效报价，切换币种清除旧确认。
- 140项Python、21项前端检查通过；桌面ETH搜索→ETH真实报价预览、手机SOL搜索、空结果/取消和390px无溢出已验。正式8766已更新，原账户与成交账本未改变。原始证据 `output/playwright/task54/symbol-search-verification.json`；Notion任务已同步。未提交共享工作区其他改动。

## 最新进度：任务 54 自选杠杆已补齐（2026-10-15）

- Owner补充要求“杠杆也要支持选”。手动开仓现可选1–125倍整数杠杆，默认1倍；保证金=入场名义金额÷杠杆，部分平仓按比例释放，数量/绝对盈亏/手续费不重复乘杠杆。持仓期间固定，旧仓位迁移为1倍，旧请求回执仍可幂等重试。
- 138项Python、17项前端检查通过；真实浏览器完成10倍开仓→部分平仓→刷新→全部平仓，3笔新增成交与原5笔1倍成交的导出余额精确对账。390px无横向溢出。刷新busy状态未及时禁用按钮导致点击丢失的问题也已修复。
- 正式8766已更新并实测10倍预览；更新前已存在1个账户，原账户/回执逐条保留，未写正式测试成交。旧段“正式账户为空”为首次上线时的历史状态。Notion任务已更新；原始证据 `output/playwright/task54/leverage-verification.json`，隔离账本 `/tmp/fable-task54-qa`。
- 仍是本地模拟；1–125不是交易所逐合约上限，不含资金费、深度滑点、强平或自动止盈止损。两条新learning见 `docs/learnings/leverage-changes-collateral-not-base-exposure.md`、`docs/learnings/refresh-busy-state-must-match-visible-controls.md`。没有切分支、训练、promote或合并其他会话改动。

## 最新进度：Notion 任务 54 手动模拟开单已上线（2026-10-15）

- Owner确认范围为现有工作台手动模拟开仓、平仓和查看盈亏。入口 `http://127.0.0.1:8766/#paper-desk`；正式账户为空，由Owner自填初始虚拟资金。
- 新增独立 `paper_desk.sqlite3`：多空、部分/全部平仓、余额/占用/浮盈、费用、逐笔成交和完整JSON导出。1倍名义占用、每边0.1%成本、确认15秒内OKX公开bid/ask报价；无交易所订单接口，不含资金费、深度滑点、强平或自动止盈止损。
- 124项Python与14项前端检查通过；隔离8878真实报价走通5笔成交/2仓位并逐笔对账，1440桌面明暗与390手机无页面横向溢出。8766已重启并验证账户及报价接口200；测试账本仅在 `/tmp/fable-task54-qa`，原始导出/截图/代码SHA见 `output/playwright/task54/verification.json`。
- Notion任务 `https://app.notion.com/p/3fa8856479af80419d7cdcdc01920a57` 已写验收。源码 `yoyo/research_workspace/paper_desk.py`、`paper_desk_api.py` 与前端 `paper-desk.js/css`；learning `docs/learnings/quote-cache-needs-explicit-empty-state.md`。共享main已有大量其他改动，本轮未合并代提交。

## 最新进度：直接识别测试完成，模型尚未验收（2026-09-26）

- Owner要求直接测最新模型，不再逐页追问盲审。40轮训练已经完成，本轮沿用同一best与conf0.25；没有重训或切线上模型。
- 新增386图推理：202旧人工正例仅17有空头框、8与继承参考框IoU≥0.5；172旧空头困难反例0告警。旧画法不同，不能称8/202为整体召回率；反例也不能代表全市场误报率。
- 3个明确认可参考以当前画法回放：FIL后1根匹配、后2根偏框；NEIRO后2根匹配、后1根无框；NMR后1根匹配、后2根无框。当前画法的HL2/裁剪/尺度一起变化，没有单因归因或逐图新金标授权。
- Owner前两次页级“无”按当时记录对应4图，其中R02 USELESS30m仍报0.969945做多，其余3无框；这是实际语义反证。没有继续要求R05/R06或推断隐藏重复。14币各自最强连续告警图已直接检查，原始框保留。
- 386图哈希/尺寸/预测ID与远端SHA核验通过，9当前参考图未来扰动一致。入口`early_owner_check_delivery_20260926.json`；`owner_check_scores_20260926_v1`含raw，`owner_check_gallery_20260926_v1`含手机PNG。Notion EXP-108已追加，资格仍false。
- 下一步应针对已见语义误报、输入兼容性及相邻帧稳定性制定修复；不能拿原规则集高分验收，也不能重复开训或强迫Owner继续27图盲审。下方“等待盲审”是历史状态。

## 最新进度：v6补边故障已复现，盲审图已准备（2026-09-26）

- 固定阈值0.25的逐图配对：159张test规则正图，pad0→pad0.5为110持续命中、40命中变漏检、4反向改善、5持续漏检；命中150→114。指标区别于上一轮AP；原始标签仍非逐张人工金标。
- KAIA预先规则选出的诊断案例：原图/加左右16px灰边/恢复分数0.984710/0.004088/0.984710，内容像素一致，恢复逐值一致。固定1312宽度、只改左偏移0/16/32px又为0.001148/0.004088/0.984432，确认真实位置敏感性，不是展示框坐标问题。单例不能证明训练固定位置是全部病因；未修改权重或线上输入。
- 盲审准备目录：`experiments/active/exp-ma-morphology-v6-threeview-20260925-v1/blind_review_20260926_v1/reviewer/`。23个不同观察窗、18合约、同币可见区间隔离，4隐藏重复，共27任务；仅显示因果原图和K线编号。抽样不读取模型输出，涨幅池选择偏差仍保留。目标24少1个合格1H窗，没有放宽隔离规则。
- Owner盲审已开始。实时进度和下一页必须读取`blind_review_20260926_v1/review_progress.json`，当前答案视图为`reviewer/answers.current.json`，每轮原话和页级范围解释保存在`adjudications/owner_round_*.json`；原空模板/准备回执保持冻结。每次只登记该轮实际回答的字段，不外推隐藏重复，不提前透露模型输出；按`next_page`手机内联展示后续图片。当前新增标注可先本机追加，阶段完成后统一提交，避免每两张图打断审核流程。
- 入口`early_followup_delivery_20260926.json`，raw配对表/两次张量实验/私有映射均已保存；11项专项检查通过，双机probe SHA一致。Notion EXP-108已追加。维持近右端低延迟目标，不追加未来K线凑位置，不重训、不promote。下一项生产资格缺口是实际盲审标签与稳健性方案，不重复开训。

## 最新进度：v6低延迟离线回放完成，尚未通过生产验收（2026-09-26）

- 新/旧v6A各16,958张同输入推理完成：333个规则正事件、799静态图、2,625连续闭合观察时点。固定14/16/17根因果窗、conf0.25、IoU0.5；333原P9像素/未来扰动一致。83项相关检查及修订后9项检查通过；原始预测、计数和SHA复算一致。
- test159事件，新模型156在最早规则启动时点匹配，旧模型43；核心后8根内156/155。新模型1全程漏检、2仅提前出现但启动时消失。双方命中的val/test324事件中，新242更早、82持平，中位提前1根。不是人工金标召回，也不是旧Grade-A生产性能对比。
- 连续冻结行情18合约/53周期流，新134/2625时点有框、旧351/2625；没有穷尽人工标签，不能称为误报率。新val普通负例1/175误触发，困难0/27；test挑战仅2张。test mAP50仅因补边由96.62%降至87.63%，脆弱性仍在。
- 入口 `experiments/active/exp-ma-morphology-v6-threeview-20260925-v1/early_evaluation_delivery_20260926.json`；逐事件、原始框及padding诊断均已保存。手机审核PNG在同实验`early_evaluation_review_20260926_v1/`。Notion EXP-108：`https://app.notion.com/p/3e78856479af81e6b9cae10361da78da`，状态不确定、生产否。
- 状态 `offline_diagnostics_complete_acceptance_not_granted`。下一步先做独立真实连续行情盲审与困难负例/补边稳健性，核对提前核心与启动确认语义；保持当前权重和阈值冻结。本轮未重训、未promote、未切换线上模型或通知。下方“回放待进行”为历史状态，不要重复开训。

## 最新进度：v6研究训练40轮完成，权重已回传（2026-09-26）

- 3060于北京时间05:15完成40/40轮，总耗时254.50分钟，无运行报错。best/last、完整日志、results.csv及实际参数均已回传Mac并与远端SHA逐项一致。
- 本地权重：`datasets/ma_launch_owner1500_morph_v6_early_20260926_v1/training_runs/exp-ma-morphology-v6-threeview-20260925-v1/weights/{best,last}.pt`；完成回执：`experiments/active/exp-ma-morphology-v6-threeview-20260925-v1/early_training_completion_20260926.json`。
- best SHA `df6a49684ddcaed373ca4d8365f9d3ae4a2e0d4278aad01edbb762f46829231c`；best按验证mAP50-95选自epoch10。训练结束自动验证349图/174框：P0.937、R0.824、mAP50 0.935、mAP50-95 0.729（日志四舍五入），不是连续行情误报率或延迟验收。
- 当前状态 `training_completed_evaluation_pending`。下一步验证首次命中延迟、已走价格、漏检/误报、困难负例和pad/渲染敏感性；尚未回放验收，未切换ACTIVE或监控。下方训练中状态为历史记录，不要重复启动。

## 最新进度：v6低延迟研究训练已在3060启动（2026-09-26）

- Owner本次明确授权“差不多了吧，去训练吧”。只授权这一次研究训练；生产资格仍false，未切换ACTIVE/监控模型。
- 当前冻结计划 `experiments/active/exp-ma-morphology-v6-threeview-20260925-v1/early_training_plan_20260926.json`。新数据 `datasets/ma_launch_owner1500_morph_v6_early_20260926_v1`，14,550图/4,850事件；1,837正事件/5,511正图，3,013负事件/9,039负图，排除144事件（4正/140负）。每事件按自身首次闭合启动证据确定后1至5根，三视图只变左侧7/9/11根；原核心坐标投影，完整右缘蜡烛。规则候选不冒充逐张人工金标。
- 两机完整预检一致；manifest SHA `c863aa0f76a86220c4e8ba0d543814cbcecd3cf0b0f36e19acb68d72490400f6`，remote preflight SHA `18ea0deb0df9c01204bef67796824fae43fd850dae8eca6f4e2621fa8465fc18`。训练12,153图，主验证349、主测试319；困难挑战另存（val129/test2），禁止将稀疏挑战集当稳健结论。
- Windows `Administrator@192.168.1.2` / `C:/fable`，实际训练PID20156（venv启动进程12468）；已在SSH断开后确认存活，epoch1/40推进至246/1520批次，GPU79%，零损坏图。YOLO11s原始base、1280、batch8、40epochs、全部增强关闭；实际args.yaml逐字段与原冻结配方一致。97项相关测试和2项作业生命周期测试通过。
- 远端数据目录内 `training_job.json` 持久记录运行/失败/完成；`training.log`为日志；结果目录 `training_runs/exp-ma-morphology-v6-threeview-20260925-v1`。入口 `scripts/windows/run_v6_early_training.py` 拒绝重复启动，禁止看到SSH断开就重开。核验回执在实验目录 `early_training_launch_20260926.json`；当前仍在训练，未取得最终权重/评估。
- 下一步读取上述运行状态；完成后拉回best/last与results并核验，检查实际因果连续回放首次命中延迟、已走价格、漏检和误报，保留pad0/default验证差异。不能只凭mAP放行。下方“训练集尚未完成”等均为已被本条更新的历史进度。

## 最新进度：低延迟逐根对照已生成，训练集尚未完成（2026-09-25）

- 同一批10正+10负已生成120张诊断观察图（核心后0至5根）；本机 `http://127.0.0.1:8879/`，产物 `exp-ma-morphology-v6-threeview-20260925-v1/early_review20_v1/`。原框、每个观察时钟和后续审核图分开显示。六个诊断时点不等于训练六倍扩图，也没有选定固定框后根数。
- 20个旧后5根原图均逐字节重绘一致；逐截止扰动后续价格不影响当前图/指标。17项相关测试通过，实际浏览器逐根切换与正负筛选通过。原图/标签未改，生成训练标签0，未开训。
- 所有10个原正例在后0根时原框越过旧画布右缘；没有截框蒙混，图上和manifest均明确标出。早期正负类别均未裁决，旧困难反例“后5根未启动”不能自动决定早期类别。下一步先修复能容纳完整最右K线/核心的因果渲染，再逐案例确定早期标签及最多三视图方案；不能把本次诊断图当训练集。

## 当前目标更正：优先解决 YOLO 发现太晚（2026-09-25）

- Owner明确目标是低延迟识别当前均线密集启动，真实形态应尽量靠近观察窗右端。R5/R8/R11为了分散位置增加等待，方向错误，已从当前训练入口撤下；不能再把右侧集中本身当失败，也不能换一组全体固定框后根数。
- 新入口 `exp-ma-morphology-v6-threeview-20260925-v1/early_detection_contract_v1.json`：暂无选定训练数据；最多三视图/事件，围绕逐案例最早可识别时点裁剪，原核心几何随真实裁图投影。早期图不能自动继承事后正标签，未来审核图继续分开。旧计划/资产/回执只作历史证据，不删除或改写。
- 下一步以已有案例的早期真实窗口核对可见形态，验首次发现相对形态/启动的延迟（根数及分钟）、已走价格、漏检和误报，再定新构建方案。此前FIL“主升走完才提案”和确认区依赖教训必须作为设计依据。当前没有重新造图或开训，未改运行模型。
- 训练入口要求显式 `--dataset-root`；当前计划即使误设资格为true仍拒绝训练，历史preflight只给资产审计结果，不给ready状态和训练命令。下方“准备完成”及“扩大位置分布”的段落是已被本条更正的历史记录。

## 最新裁决：v6训练设计审计未通过（2026-09-25）

- Owner追问“还有其他问题吗”。撤回将文件校验通过表述为学习设计准备完成。当前训练资格仍false，未启动新训练，未改运行模型。
- 当前三位置数据的正框只有六组横向中心(三位置×两核心长度)、两档框宽；仅用train的裁剪元数据模板就能复现480张test正图的横向框，横向IoU1.0，打乱视图编号对照均值约0.404。这不是检测mAP，也没有证明任何类别/纵向/误报性能。
- 训练困难负例267/6408张(89事件)，修正版test困难负例0；1841正事件全部仍是3R后验精选规则候选，逐样本人审未确认。通知适配器为Grade-A/CLOSE/绿红/W18-W19；新候选为HL2/蓝紫/W20-W21，不能直接替换。
- 旧v6A权重17aee567…在已有5250窗原始回放的773框中766框(99.1%)落在post5。原生/对齐padding的val mAP50为0.08650/0.99465，敏感性未解决；不是新模型结果，也不能据此给出无金标的误报率。A/B训练日志40轮分别5908.98/7829.46秒。
- 原始审计：`exp-ma-morphology-v6-threeview-20260925-v1/learning_design_audit_v1.json`，复现：`.venv/bin/python -m yoyo.datasets.ma_morphology_readiness_audit --output <新JSON路径>`。现有PNG/TXT、计划与旧回执保持不变。精确哈希/事件ID未跨split，视觉近重复与连续行情金标验收仍未完成。下一步先明确目标语义与真实推理输入，设计跨事件位置分布、困难反例和未见位置验收，再决定重建；不能沿用下方历史“准备完成”来直接开训。

## 最新：SPIKE V13.0 回踩再突破已交付（2026-09-25）

- Owner要求解释回踩逻辑、跨周期详细对照并写V13.0。新源码 `yoyo/evaluation/pine/spike_burst_v13_0.pine`，TradingView另存私有“SPIKE V13.0 · 回踩再突破”修订2，原V12.8源码与私有脚本保留。默认普通多空回踩，可选联合多头或原版参考；保留历史趋势区域。
- 原候选后依次不同收盘K突破原高低点、触位守住、再破首段极值；24根等待，确认后次开模型参考，保留原绝对止损并重算R。102项本地检查、20项原生核心断言、6种生命周期情景通过；15m/1h图表运行正常，保存源码逐字核对。次开参考在该根收盘核验路径，确认提醒在此前收盘产生；没有创建提醒或下单。
- 更正“回踩无用”的过度归纳：两月与2023起长历史中，15m普通/联合的胜率与平均净收益均改善；1h非全面改善，≥5R比例下降。2025起长后段15m仍正，2026-08-23起短后段亏损，不能混用窗口。知识库原实验已追加更正，资格仍false。
- 交付证据 `experiments/active/exp-spike-v130-retest-delivery-20260925-v1/delivery_receipt.json`；首次74plot运行失败与修复留档，原生合成核验不等于Binance档案与TradingView行情逐笔parity。未运行新经济实验、训练、promote或通知替换。

## 最新：真实三位置修正完成，训练尚未启动（2026-09-25）

- 新默认数据集 `datasets/ma_launch_owner1500_morph_v6_positions_ready_20260925_v2/`：13,948图，其中1,841正例事件×3=5,523正图，7,605负图，另保留820张不可变P9评估图。训练10,926图=4,518正+6,408负；618个旧负例事件因扩窗后的形态/保护区冲突被剔除，没有降低阈值凑数。
- R5=前11/后5、R8=前8/后8、R11=前5/后11，同事件画面等长。原P9实际TXT框经bar/price投影，各图使用自己的真实右端收盘时间；这是延迟形态确认，不是当根启动/tip信号。位置、时间和框绑定已进入强制preflight。
- Mac与RTX3060全量资产审计相同，manifest SHA `8f29a6bc74a4ce9e3b260503b43a819d8c0c4483fa5366dff9c87fb6dcd28f40`；环境契约一致、远端CUDA可用。107项检查通过，4份pilot清单逐字节相同。原P9 reference350/320及challenge148/2成员/字节/时钟不变。
- `exp-ma-morphology-v6-threeview-20260925-v1/position_preparation_receipt.json`绑定原始证据。同一批10正例的三位置和未来审核图见`position_review_v1/index.html`（本机8878），图框已与全量数据核对。312份归档恢复1,770份1m有界片段；两次中断的临时PNG/TXT和双机传输ZIP已清理，日志、原数据与恢复片段保留。
- 完整构建使用已提交源`c64bccb6495b26c08d41bb55580bc0e5046efcbf`；后续等价并行版本经pilot及107测试验证。未开训/未激活；训练资格仍false。固定位置旧数据包是历史版本，不再作为默认训练入口。

## 最新更正：三窗口仍固定后5根，位置增强待修订（2026-09-25）

- Owner发现正例框总在相同右端距离。全量5,523正图的pre/post只有7/5、9/5、11/5，各1,841张；原实现只改变左侧长度，未实现参考Grade-A的右端距离变化。核心末根后固定5根（严格计数为倒数第6根），审核额外40根不属于训练输入。
- 已确认裁剪设计限制，不能把哈希/环境preflight通过解释为位置增强已验收；先修订窗口方案及逐视图可见时钟，再准备重建与验收。当前数据和标签保持不变，仍未开训。见`docs/learnings/left-context-variation-does-not-vary-right-edge-distance.md`。

## 最新：20 张带未来走势的训练样本审核已生成（2026-09-25）

- Owner要求抽看10正+10负及实际正例框。`exp-ma-morphology-v6-threeview-20260925-v1/review_future20_v2/index.html`提供20图、正负筛选、全尺寸图和原训练图对照；正5多5空，负5困难5背景，均取固定train事件，不按未来收益挑图。红框从真实训练TXT投影，蓝虚线是训练截止，之后额外40根只供审核。
- 冻结选择`review20_selection_v2.json`，每个原训练PNG重绘SHA均匹配，原PNG/TXT哈希未改。两份缺失1m源只从3060哈希验证归档恢复审核片段。`review_manifest.json`留存几何、时间与来源证据；没有改标签或开训。

## 最新：v6 三窗口完整训练包已备好，尚未开训（2026-09-25）

- Owner要求“整理一下，准备去训练模型了”。完整目录 `datasets/ma_launch_owner1500_morph_v6_threeview_ready_20260925_v1/`：4,994事件/14,312图，正1,841事件/5,523图，负8,789图；训练12,522图=4,518正+8,004负，其中975张Grade-A困难负图。共享跨周期模型，pre7/9/11、post5与原核心不变，每事件最多三图。
- Grade-A 3,129候选中保留1,312事件，新增3,936图；排除1,817事件，逐项原因在`excluded_candidates.jsonl`。按当前完整源SHA冻结新源版本，重算旧指标、CLOSE/HL2规则与全可见完整候选窗；保护原候选/金标，不拿收益判断负例。10,376旧图校验后硬链接复用，新增PNG205,877,617字节；临时805MB传输包两端已清理，原始数据未删。
- Mac与RTX3060 `C:/fable/datasets/ma_launch_owner1500_morph_v6_threeview_ready_20260925_v1/` 的全资产审计相同，manifest SHA `b50fbda5384071bcde5a9f10632c36f9041ff4314ed026479ecac1bec78903d2`。训练清单/data.yaml已在两端生成；torch2.8.0/torchvision0.23.0/ultralytics8.4.89/numpy2.0.2/pandas2.3.3匹配，远端CUDA可用。90项专项及层间检查通过。
- 固定原P9 val350/test320；新增Grade-A challenge另列148/2事件。P7/P11 reference只有正例，是同事件召回/框稳定性检查，不能与平衡P9的mAP直接比较。最新段新困难负例只有1事件，需后续真实连续回放验误报；此次无模型性能或收益结论。
- `training_preparation_receipt.json`记录完整证据；训练包代码`yoyo/datasets/ma_morphology_training_package.py`。当前只完成准备，training gate仍false且未启动训练。收到明确开训指令后单独登记本研究训练授权，保留冻结数据/原计划，更新相应计划引用与registry，在新目录重做preflight，再运行显式train；仍用YOLO11s初始权重/1280/40epochs/batch8/seed0/全增强关闭。不能把研究开训变成ACTIVE/promote或通知替换。

## 最新更正：跨周期共用视觉形态模型，不按周期排除负例（2026-09-25）

- Owner明确大小周期可以出现同类均线密集图。之前将Grade-A负例全为15m当作不能复用的理由过强，已撤回：一个共享YOLO可混合周期，周期是溯源及分组验收字段，不是类别，也不强制每个正例必须配同周期负例。统计分布差异需要实测，不能仅凭周期计数断言模型有偏差。
- 当前以`exp-ma-morphology-v6-threeview-20260925-v1/evaluation_contract_v2.json`与`negative_reuse_audit_v2.json`为准。此前15/58、2/58只是旧采样器约束下的覆盖，不代表视觉负例全部可复用容量。已有5,523张正图及原记录保持不变；后续按形态标签、画面/均线一致性、事件保护与时间切分筛选Grade-A负例，周期不同本身不拦截。没有开训或更换运行模型。

## 最新：v6 三窗口正例已准备，Grade-A 负例不能整包复用（2026-09-25）

- Owner 要求1,841事件最多三倍扩图，并询问沿用Grade-A负例。`datasets/ma_launch_owner1500_morph_v6_threeview_20260925_v1/` 已生成5,523正图：train4,518/val525/test480；pre7/9/11，post固定5，原核心/事件切分/确认时钟不变。4,853张通过哈希核对后硬链接复用，只新增670图；原P9仍是固定评估集，另外两种视图只做同事件稳健性对照。
- 176个缺失1m源事件从3060现存140份gzip恢复短片段；gzip与完整CSV哈希全部核对，再验证本机片段哈希及重绘旧图/标签一致。仅取回16,942,631字节CSV；新增PNG32,329,733字节。首轮缺源pilot和中止的串行恢复保留；最终pilot6事件18图、全量5,523图检查通过，专项20项及层间边界71项通过。
- 旧Grade-A负例全为15m，而v6正事件只有58个15m。2,664个旧训练负事件的旧窗口有41个触及当前保护区；422源中48个能对照v6历史SHA并匹配，374个尚无历史预期SHA证据。同源/周期/±7天仅覆盖15/58个15m正例；旧事件只可当候选，须按新图可见信息重新审查与渲染。不能以未来亏损判形态负例，也不能把旧验证事件塞入新训练。
- 实验`exp-ma-morphology-v6-threeview-20260925-v1`已登记；`delivery_receipt.json`及两个负例审计保留证据。**训练尚未启动，完整训练集未就绪**。下一步确认密集启动的困难负例语义并补同周期负例，再冻结训练清单；沿用原YOLO11s/1280/40epochs配方，保留原填充脆弱性与连续回放验证。未改变运行模型、通知或资格。

## 最新：学习、知识与内容输出加入统一工作台（2026-09-24）

- Owner要求建立学习与知识体系、支持全平台输出。8766新增 `#learning`、`#knowledge`、`#content`，菜单置于“学习与输出”；体系总览增加对应业务线。六个知识领域连接学习问题、复述、复习日期、知识证据、实验引用和内容母稿。
- 沿用运行目录 `research_workspace/workspace.sqlite3` 的追加版本记录。保存冲突保留草稿；内容保存指定知识版本快照，平台版本独立编辑，支持复制、中文 Markdown 导出、按平台/状态筛选及手动作品链接/时间登记。覆盖知乎、公众号、小红书、X、B站、抖音、YouTube、Telegram和自定义平台；没有外部发布器或付费AI生成调用。
- 正式库已创建六项待学习计划及一条待验证 DuckDB 候选知识（`knowledge-d3778bcd77054a3b`）；未创建已发布内容。Notion既有中枢增加知识地图与输出流程，仍为正式知识入口；本机草稿通过来源和Notion页面链接关联，尚未自动双向同步。
- 204项研究工作区/层间边界检查、79项监控前端检查通过；隔离8876实际走通学习创建→知识提炼（自动关联原计划）→知乎版本→保存→导出，390px明暗主题无横向溢出。正式8766已重启并核对API。隔离测试未写生产学习记录、通知或交易；无训练/策略/账户配置变化。
- 维护原则见 `docs/learnings/content-must-freeze-knowledge-evidence-versions.md`。本轮修改保留在现有main工作目录；共享文件还有先前通知、个人交易等未提交改动，未合并代为提交。

## 最新：ISRG 再次证明原生与后台元数据口径不一致（2026-09-24）

- Owner再次指出ISRG30m16:30收盘395.56有Bark、图上无信号。事件`5300ec9405ab95353af46f15`，Bark/TG各sent一次；后台确有空头，不是重发。原生同品种30m的V12.8面板为`crypto / 未知`，极简/仅联合均false、确认及空头样式开启。
- 固定1364根闭合K线及tick.01，仅后台base从ISRG改None，同根空头由passed变为base_asset_unknown，证明OKX元数据补值与Pine未知拒绝的语义差异足以致错。原生面板BB未过只是最近原始确认，未对应本次当根，不可当成该bar证据；其余原生数值仍未逐根核对。
- 只完成定位，没有修复算法、改门槛、暂停/重启服务或改通知订阅。四个运行监控模块启动SHA吻合磁盘，桌面恢复BTC1H。证据`data/research/spike_v128_isrg_parity_20260924/`、learning `docs/learnings/cross-platform-parity-includes-metadata-authority.md`；Notion `3e58856479af814b8b60d697134cad37`。下一步以原生为准统一身份输入和未知语义，再做OHLC历史/IMACD/BB/最终事件双边对账；不可声称图表已一致。

## 最新：VWAP / TWAP 距离筛选完整回测，拒绝作为默认条件（2026-09-24）

- Owner 要求研究并回测。复用冻结 Binance638 合约5m数据聚合15m/1H，2023–2024原始候选特征中位数定门、2025-01至2026-09-23后段固定检验；仅改入场准入。原下一开盘、反向退出、止损/追踪、风险及20bp不变，各组重新回放占仓，切点继承共同原版仓位。
- 后段普通15m原/VWAP/TWAP均净−7.70/−12.67/−10.54bp；1H为−5.37/+18.27/+11.56bp，胜率均未提高。四项同门随机净超额为−5.37/−1.23/−48.56/−38.56bp，Holm p均1；1H VWAP改善区间跨零、仍落后随机，不是验证成功。VWAP原10R赢家保留15m51.7%、1H69.3%；q10诊断四组净收益皆负。联合信号也无对照优势。
- 1276流无错误、原版逐笔与源账本一致，20项专项测试通过，独立36组统计复算通过。BTC/ETH首次试跑因1H早期98候选不足预设100停止，失败保留；扩至4币后通过，门槛未降低。实验 `exp-spike-v128-vwap-20260924-v1` 的 `delivery_receipt.json` / `run_v1` / `summary_v1` / `audit_v1.json` 已保存；Notion EXP-105 `3e58856479af81f8b856ea75c1399049` 记拒绝。
- 拒绝范围仅本次UTC日锚定绝对距离门；事件锚定/穿越/回踩与TWAP拆单未测。固定池、既有研究历史与Python/Pine未逐事件对齐限制保留，不能外推OKX通知或实盘收益；训练/生产资格false。Pine、监控及默认策略未改变。

## GEV 通知与原生图不一致：已确认差异，计算尚未对齐（2026-09-24）

- Owner 的 GEV 30m / 15:30 / 942.49 空头 Bark 可追溯到 raw `d1408acce0c386057fedeb00` 和 YOLO `8ecedbee762f61b25343363b`，等待0根；后台闭合1362根重算复现。原生同一15:00开盘K线OHLC完全相同，但V12.8事件码0、确认价空、MD/SB为0/0；后台为−0.07911/−0.00879。原生底层资产字段未知、后台OKX元数据为GEV，Pine未知门明确拒绝。已检查桌面实例非极简/非仅联合，不能归咎于隐藏箭头。
- 原生面板“最近原始确认”还提示BB未过，但不能当成所选15:00当根的BB证据。具体历史序列/递归起点差异尚未定位，不宣称Python/Pine一致，也不改门槛或猜测底层资产补值。TG/Bark新文案改“后台信号 / 原始信号”，注明“未核验 TradingView 图上信号”；图片与状态说明同步去掉原箭头措辞。
- 验证7个通知/图片测试文件129通过、20失败；撤去本次展示修改的内存基线复现同样20失败，保留旧测试，未冒充全绿。细节及证据：`docs/learnings/backend-signal-is-not-a-tradingview-arrow.md`。下一步应对齐原生与后台历史输入、元数据和逐根中间值，不能仅修文案后宣布信号一致。
- 通知文案已随8766服务重启生效（started_at_ms `1790236056484`），Bark/TG/snapshot运行源码哈希与磁盘一致；原GEV两事件两通道仍各sent/attempts=1，没有补发测试或历史提醒。桌面图表已恢复检查前的BTC一小时视图。

## 本机存储：常用行情留本机，原始下载包外移（2026-09-24）

- Owner 授权迁移，并要求直接选择后续方案。最终采用本机保存行情表/元数据、Elements SE 保存原始 ZIP：34,372 个下载包、7,228,496,357 字节（约6.73 GiB）已外移，原 `downloads/` 路径保留指向外接盘的链接。两份数据集为 `data/crypto/market/kline_preholdout_binance_um5m` 与 `...um15m`；根目录已恢复为本机普通目录，`data/kline_*` 兼容入口不变。日常行情读取不依赖外接盘，仅原始压缩包访问需要该盘。
- 此前整份迁移已逐文件校验，但真实8766 LaunchAgent被macOS拒绝访问移动盘；命令行成功不能代表后台可用。2,562 个常用文件（15,332,415,418 字节）已迁回并逐文件核对原始 SHA-256、权限、原有扩展属性，恢复原始纳秒修改时间；外接盘暂保留这些文件的完整校验副本作备份。没有增加 Python 磁盘权限。已撤回本轮临时外接目录访问代码与登记配置，目录继续沿用原有仓内访问边界。
- 两数据集 ID 与常用文件逻辑路径保持不变；只更新它们的目录记录，ZIP 不计入网页可用文件，不重建其他数据集。真实后台两组详情/文件接口均 HTTP 200，响应 SHA-256 与原始清单相同，`read_market_data` 两组样本通过；96 项 Python、7 项前端检查通过。最后实测本机空闲约71.5 GB（66.6 GiB），空间变动受系统及其他活动影响，不能全部归因于本次迁移。
- 操作、完整哈希与原始时间、旧索引备份及运行验收收据在 `data/_storage_migrations/20260924-binance-history/`；最终状态见 `summary.json` 与 `hot-runtime-smoke.json`。先前整份迁移与权限失败保留为历史证据，不能沿用21 GiB净外移的旧结论。外接目标为 `/Volumes/Elements SE/MacData/zhangzc/fable-trading/data/crypto/market/`，分区 UUID `9C313B72-8A2D-4E45-8CEF-51A3E1117FB3`。

## 最新：VLM 超时截图核查与失败状态展示（2026-09-24 13:42）

- Owner 提交 ACU 1H 超时截图。运行 API 与私有配置元数据均确认当前为智谱 `glm-5.3-flash`，配置文件最近保存于 12:37；先前“已切千问”的记录不代表现在仍选中千问。千问北京配置保留，但 Key 仍为空。没有供应商自动回退证据，也无法凭文件时间确定是谁保存；本次未改供应商、密钥或重发请求。
- ACU run `b44a47e913e74545b70aeca043605363` 在 13:02 发起，13:07 读超时；exchange `cb4ffa60fc5a4f1cb2a25d6dfbc50be4` 为 6 图、约 255 KB、`stream=false`、32,768 token 上限及 max 思考，未捕获完整 HTTP 响应。不能据此断言视觉能力差，远端处理与传输原因仍未分清；超时本身尚未修复。
- 前端结果卡显式显示该次供应商、模型、时间及人类可读耗时，超时标为“请求超时”；未完成记录不展示方向和空证据/风险块，也不从当前配置补填历史模型。失败候选时间改为观察/结束时间；自动开关开启但无 Key 时显示等待配置并提供设置入口。历史记录与自动调用策略保持原样。
- 33 项配置/自动队列/原始记录 Python 检查、54 项 VLM 前端 Node 检查通过；统一入口浏览器实测 ACU 显示智谱、13:02、5 分 00 秒和无判断说明。未发起真实模型调用。已询问 Owner 恢复千问等待 Key 或继续排查智谱，尚待回复；不要自行覆盖当前已保存的选择。

## 最新：统一工作台免费公网入口已按 Owner 要求去掉登录（2026-09-24）

- Owner 要求先试免费地址，无域名或账号，之后明确要求“去掉登录”。现采用 Cloudflare Quick Tunnel + Caddy 免登录代理：公网 → 127.0.0.1:8780 → 8766；8771 沿用内部代理。已移除配置生成器中的 Basic Auth、凭据生成与401就绪要求，仅重启 Caddy 生效，监控/扫描/通知服务未重启。
- 原 URL 保持为 `https://pasta-okay-leading-alleged.trycloudflare.com`；最新地址用 `.venv/bin/python -m yoyo.monitor.public_tunnel status` 查询。管理命令 start/status/stop；停止仅关闭公网两项 LaunchAgent。`~/Library/Application Support/Fable/PublicTunnel/access.txt`（0600）现只记录免登录入口说明，旧 credentials.json 不再读取或使用。持有链接即可访问完整工作台。
- 所有页面/API免登录；写请求仍先验证公网 HTTPS Origin，之后才重写内部 Host/Origin 并清除 Authorization、Cookie、转发头。公网桌面打开接口封闭，前端远程访问打开当前设备的 TradingView 网页；本机保留原桌面行为。
- 本次实际 Caddy 集成测试15 passed；匿名GET/同源POST可用，异源写、HTTP、无效Host和远程桌面调用被拒绝。真实公网匿名首页、status、VLM status、JS全部200且无认证挑战，未携带凭据/Cookie。重复start验证两个服务PID均未变、仍免登录，地址未变。未调用付费AI/发通知/写交易记录。此前首次开通的密码模式验证（401后认证200）仅为历史，不代表当前状态；内置浏览器曾被client blocked，本次通过HTTP验证公网链路。
- Cloudflared 2026.9.1 / Caddy 2.11.4 由 Homebrew 安装；独立 launchd KeepAlive，不改变 Python 依赖契约。Quick Tunnel 无 SLA、地址非永久、Mac 必须在线；当前 VLM 对浏览器返回 JSON，没用 Quick Tunnel 不支持的浏览器 SSE。流程说明见监控 README 与本次 learning。

## 最新：个人交易系统改为原则 → 开单 → 记录（2026-09-24）

- Owner 明确个人交易首先沉淀原则，然后开单，最后才是记录。`#manual` 默认打开交易原则，主导航为三步；具体打法与盘前计划收为辅助入口。体系总览同步口径，监控版本 1.19.0。
- Principle 独立版本化，待沉淀 / 正在遵守 / 归档；参考文案不自动落库或启用。开单页需核对全部当前启用原则，事务内验证集合与版本并保存不可变快照到原 watching Ticket。只提供 OKX 合约页导航，没有订单调用；准备不记成交。成交、管理、平仓、复盘继续追加事件，旧记录无迁移删除。
- 复盘心得可带来源转成未保存的原则草稿；原则冲突保留原草稿版本。信号中心可带入合约、方向、周期与来源，不覆盖正在编辑的草稿。原则修改不会追改旧交易快照。
- 研究工作区 127 项 Python、监控前端 72 项 Node 通过；在独立临时数据库内浏览器走通原则创建、开单核对、成交、平仓、复盘、回流草稿，390px 无横向溢出。验证数值仅在隔离环境，不是交易证据；无真实下单或账户修改。设计边界见 `docs/learnings/principles-should-precede-execution-and-journaling.md`。
- 扩展 Python 检查 264 passed / 2 failed；失败仅为既有 `test_desktop_client.py` 把 `1D` 和小写合约当非法输入，与 HEAD 中原有规范化行为矛盾。相关实现及测试均无 diff；未改其语义来过关。本机 8766 已重启至 1.19.0，API 与浏览器已核实；生产个人记录仍为 0，测试数据未混入。

## 最新：VLM 已保存千问旗舰最高质量配置，等待 Key（2026-09-24）

- Owner 要求直接用千问最强视觉模型、最高配置，API Key 稍后提供。当前本机 VLM 0.13.0 已保存 `qwen3.8-max` / 北京；`api_key_configured=false`，自动队列等待配置，不回退调用智谱。统一入口 `http://127.0.0.1:8766/#vision` → 模型设置，浏览器已核实已保存模型、质量参数与待配置密钥。
- 旗舰请求使用 `reasoning_effort=xhigh`、`max_completion_tokens=131072`（含思考）、单图 `max_pixels=16777216`、SSE+usage；不同时传 thinking_budget，不放大原图，不自动重试。保留原始 SSE，对话展示分别拼接思考与最终回答；缺终止标记、length、格式/语义不合格不采纳。固定快照 `qwen3.8-max-0902` 也可选，历史模型和记录保留。
- 视觉模块 275 项 Python 全套通过；随后新增的两项 SSE API 落库用例随 Qwen 29 项专项通过；前端最终 50 项通过，层间 import 边界 71 项通过。均为离线或本机 UI 验证，没有调用千问真实付费接口、未证明识图效果提升；下一步是提供同地域 DashScope Key 后验证连接与真实图表。详情见实验 README 与 `docs/learnings/maximum-vlm-quality-is-a-model-specific-contract.md`。

## 最新：信号中心已切换 SPIKE V12.8（2026-09-23）

- Owner 要求前端使用最新逻辑。此前主扫描器仍为 V9、突破/联合为 V11.2；现在服务 1.17.0 的普通生产者为 `yoyo/monitor/v128_signals.py`，联合为 `v128_lines.py`，实际 API 已返回各自 v128 协议，启动源码哈希已核验。
- 15m 使用信号 K 线开盘时已完成的连续 60 根原生 H1，严格 SMA60 方向门；NEIRO 09/23 20:00、SSV 19:45 空头被拒绝，LDO 20:15、ETH-USDT 19:45 保留。联合接 V12.6 有序配对父引擎及 V12.8 原价格退出，不再沿用旧监控 RSI 扩展；趋势线原生 pivot 同值边界仍未完成 TV 逐事件对账。
- 新旧协议、事件 ID、通知切点分离；普通 V9 归档、联合 V11.2 归档保留，预热历史按新规则重算且不补发。升级前 1,304 条普通/YOLO、1,801 条趋势线/联合 payload 逐条核对零删除、零改写。私有 SQLite 备份在运行目录 `backups/before-v128-20260923T125530Z/`，切点持久且重启不重置。
- 普通全市场 491 合约 × 4 周期共 1,964 cells、联合/突破 2,455 cells 均完成零错误；首次暴露的单根 4H 历史压缩核边界在适配层处理为 loading，未改冻结研究内核。135 项 Python 专项/层边界与 36 项前端检查通过；真实浏览器确认新版与旧版入口隔离。
- 只升级本机通知监控；Pine 文件、阈值、新鲜度、成本、训练/ACTIVE 与账户未改。普通 Bark、联合 Bark + TG 延续原设置。详见监控 README 和本次两条时序/短历史 learning，未生成任务报告。

## 最新：SPIKE + 智谱视觉研究工作台（2026-09-23）

- 0.12.0 修复回放主路径：图表上方原信号时间/相对根数/AI状态、原策略箭头、一键信号时点冻结并识别、返回信号与刷新恢复结果；价格轴保留低价有效小数。播放不调用AI，已有run复用且不自动重试；已看未来的blind session须显式回顾学习，保留future_seen并禁止补独立标签。新增模型请求验收均用隔离模拟provider，无新增真实模型结论。
- 本次视觉相关测试通过；扩展运行 `pytest tests/vision_research tests/boundaries tests/causality tests/parity -q` 为699通过/9失败：AGENTS/CLAUDE不同步、ma_morphology_reuse_recover跨仓路径字串、V8 TOTAL2 artifact缺source_commit导致5项、L1 candidates/render迁移SHA不符2项。失败文件均不在本次回放修改范围，未修改门禁或重录哈希来通过测试。
- 0.7.0 新增独立「验证回放」菜单（`http://127.0.0.1:8771/#replay`）：本地原生历史、连续区间与预热校验、120 根闭合因果前缀、单步/播放/暂停/自由定位。冻结1440×800原图及规则/模型/参考版本，先人工后AI模式、暴露时序锁、单次持久调用、追加回访与JSON导出均已实现；训练/生产资格false。任务清单 `docs/protocol/vision_validation_task_list.md` 已同步7张Notion任务卡，下一步逐张参考确认。
- 回放验收：241项Python＋21项Node通过；桌面/390px/明暗、播放暂停、刷新恢复、时间越界、图片404门通过。真实ETH15m首调run `6da66b1834f54f158eb16adfc6a25613` 90秒超时；同像素/5参考/规则/模型显式复测 `f61442ae934e4c479b5a352f8ea8484c` 87.37秒完成（13,363 tokens，extended/no_match），原失败保留。实际API六图逐项SHA与冻结输入一致，回访原图未变；仅工程验收，准确率和收益未验证，时延继续列入质量任务。现有自动队列恢复原开启状态，未修改8766监控。
- 0.4.0 按Owner“检测盘口”纠正为图中最右端当前形态：prompt v3 + schema2 区分密集未启动/正在启动/已经远离/无形态/不清楚；只有当前启动可match，已远离不能画旧核心框。截图须完整包含最新K线，前端拦截历史视口、后端核对声明视口与快照；仍不声称像素已验证。12根复查期限、90秒TTL不变，旧记录标为旧版整图且不改写。同一RAY旧图和原参考哈希一致的重测run `94ed016f2a2642dbb08e6bcb163818c2` 返回extended/no_match/无框，约11.11秒；仅单例回归，不代表准确率。194项Python+5项Node测试通过，浏览器拖离最新K线时在调用前被拦截。
- 0.3.0 全面重构前端：固定侧栏导航、以 TradingView 图表为主的工作区、图像缩略历史、分栏证据/折叠详情和人工复核、模型设置左右配置与原始请求/响应。统一 SPIKE 明暗主题、字号层级和 Lucide 图标，390px 窄屏改为顶部导航/折叠候选；无变化的结果不重建 DOM，切页保留复核草稿。185项既有相关/边界测试通过，真实桌面/窄屏/明暗与历史/API入口已检查；没有触发付费推理，也没有改后端调用参数、参考图库或凭据。
- 0.2.1 修复 JSON 输出兼容：当前 `glm-5.3-flash` 显式请求 `json_object`，只拆完整单一 JSON/无语言代码围栏，仍严格校验结束状态、字段与框；不修补截断、不自动重试。NEIRO 20:31旧失败原文缺失，确切语法原因不可追溯；同一待判图+原3张参考+原规则在部署后run `d107e463615e41da9dc16f74d6f4d148` 实测HTTP200/完整JSON，约13.98秒，原图/参考哈希一致，旧记录未改。185项相关/边界测试通过；模型与low档不变，不能将格式修复当识别准确率提升。
- 0.2.0 新增“模型设置 → API 原始记录”：在解析前保存实际序列化请求正文及完整 HTTP 响应，支持图片预览、完整 Base64 原文、下载、错误和历史跳转；Key 不落账，旧记录不补造原文。实测 EGLD 15m run `6f4a440f5ea94b93a8cfacbc07321dbf` 为待判图+5张参考图，HTTP200、8,817 tokens、cached_tokens=0；请求图片哈希逐项对应运行快照，导出与原文一致。173项相关/边界测试通过，浏览器明暗主题及记录切换通过。当前每次仍是独立 Chat Completions 请求并重发全部参考图，未接入 Responses 会话续聊；只验证工程链路。
- 入口 `experiments/active/exp-spike-gemini-vision-20260923-v1/`，应用 `yoyo/vision_research/`；本机 `http://127.0.0.1:8771`，由 `.venv/bin/python -m yoyo.vision_research.manage start` 管理用户 LaunchAgent。只读现有 SPIKE 8766及同源OKX公共行情；实际子代理 GPT-6 Luna/max 已核实。
- TradingView Lightweight Charts 4.2.0 为主画布，可平移缩放；同一套 SPIKE 明暗配色与字体。工作台每10秒刷新当前120根K线（包含明确标注的未收盘bar），每30秒刷新候选；默认信号后12根内可手动识别，页面可调6/12/24/48根。不可变snapshot绑定当次OHLC/MA、观察时间与截图，90秒TTL，服务端同时检查窗口到期；返回实时图表再刷新。历史signal_close API与旧识别记录保持原语义，上传图片时间边界不作验证声明。
- 实时快照端到端已在 HPE 30m 验证：run `9cef1192dba4430eadac969603117e0e`，20:00信号、20:48:41实际观察、末根未收盘，glm-5.3-flash约13.38秒返回uncertain。使用当时运行库3张参考图，保留用户现有配置；识别后返回实时图表可继续更新。160项相关/边界测试通过，桌面平移保持、过期窗口扩大后恢复识别、390px无横向溢出已验证。
- 全局参考图库持久保存在独立 SQLite，识别保存引用版本；取消原4张上限，按合计12MiB、单张小于5MB和智谱50张总图数上限校验。Owner明确红框只含启动前密集区：V3重新选AUCTION/CHZ多头、JELLYJELLY替换MEME空头，FIL/TRUST左界各收紧一根、右界保留在启动前；当前共5张，运行库revision4。`default_references_v3/manifest.json`记录逐事件核心/启动边界、OHLC前缀哈希及来源，生成器先提交de56ed2b78后运行；旧8张包保留，新框待Owner逐张确认，训练/生产资格false。166项相关/边界测试通过，页面刷新/大图与API图片逐像素核对通过，旧识别记录与引用图片未变。默认仅初始化未编辑的库，后续删改不会被覆盖。
- Owner 已授权 Key 本机持久保存：`runtime/private/settings.json`（目录0700、文件0600、Git忽略）原子写入，启动自动读取；输入留空保留旧值，账本/导出/状态接口不返回 Key。Owner 已要求换为智谱并提供 Key；已在本机固定保存，默认 `glm-5.3-flash`，调用官方 `open.bigmodel.cn/api/paas/v4/chat/completions`，不自动重试。配置带 provider，旧 Gemini 凭据不会发给智谱。
- 智谱已完成真实前端9图调用：LDO 15m TradingView截图 + 8张全局参考，运行 `577fd09c249d40a5b43f3036213906d9`，约14.08秒返回 `uncertain`，12,410 tokens；截图1200×732、参考版本2、provider=zhipu，输出及输入已保存。77项专项测试通过。只验证工程链路，不代表识别准确率或交易效果；训练/生产资格false。
- Gemini旧两条失败没有HTTP码；之后运行 `a5bda16b25164ed29f6c828cc74fa417` 已明确返回HTTP402余额不足。旧记录保留，不改写成智谱结果。服务重启就绪等待从5秒延至20秒，覆盖launchd调度，避免命令假报失败。

## 交付偏好已更新（Owner，2026-09-23）

Owner明确要求彻底删除“每次都弄报告”的原则。默认直接在对话中汇报，只有明确要求报告或指定文档产物时才生成；不自动写Markdown、转HTML/PDF、发布网站或因技能默认流程调用build-report，也不每轮询问。必要原始结果、复现信息和实验登记按工作需要保留。仓库规则、章程、README与全局指令已同步。

## 最新目标：SPIKE降低频率，提高净胜率与兑现高R（Owner，2026-09-23）

- Owner原话：“我们的目标是尽可能的降低交易频率，提高胜率和高 R”。联合衡量实际频率、扣费胜率、兑现3R/5R/10R占比和大赢家保留率；不以少交易或高胜率单项判定成功。
- 研究优先级改为入场筛选→保留大赢家的退出→真实加仓。此前2R费用保本仅保留为候选，尚未执行，优先级后移。高R以实际净收益/入场原始风险计，浮盈另列。
- 每轮固定原退出/成本、只改一个因果筛选变量，必须带前后段及匹配随机净bp对照。具体每天笔数、高R和胜率门槛尚未指定，不自动设置实盘限制或调参。
- 完整口径：`docs/protocol/spike_selective_entry_objective.md`；Notion沿用V12.8研究记录。目标明确不等于已有通过方案；此前硬叠形态、时限、早保本的失败结果继续保留。

## 最新：SPIKE北京时间入场时段分析完成（2026-09-23）

- Owner明确查“北京时间几点入场更好”。固定近两月源逐笔，按实际entry_time分6个4h桶/24小时；原版/回踩、普通/联合、15m/1h共八组，12,131记录、11,877闭合。不是加时段门后的新占仓回放。
- 原版1h最佳4h为00–04；普通00–01有113笔、50.44%、均净+274.46bp/+0.848R，联合02–03有43笔、51.16%、+446.26bp/+1.189R。但两小时冠军后段均净收益转负，两个00–04桶去最好周也转负。原版15m收益最高4h为20–24，后段同样转负。
- 观察候选为15m普通回踩20–24：159笔、43.40%、+140.09bp/+0.416R；前段78笔43.59%，后段80笔42.50%、+13.89bp/+0.077R，另1笔跨切点。23–24有48笔50%，但属全期探索，前段胜率冠军其实是17点且后段失败。
- 所有可算48槽主检验Holm p均1；20–24候选随机仅8对，净收益区间跨零。没有已验证最佳小时；若继续，优先冻结20–24准入语义后完整重放候选占仓。禁止直接删旧交易当新策略。
- 报告`analysis/p1_spike_v128_entry_clock_20260923.md`，实验`exp-spike-v128-entry-clock-20260923-v1`最终`summary_v2`。12测试通过，240桶独立复算一致；v1仅receipt类型写入失败，修复后3CSV逐字节相同。GPT6 Luna Max只读时间口径复核已验证；无Pine/退出/成本/生产改动，资格false。 Notion研究EXP-102：`3e48856479af81dbbf2edf1d08822903`，报告提交`7e1e351f46`已推送。

## 最新：SPIKE回踩再突破入场已完成，暂不默认接入（2026-09-23）

- Owner选择“突破后回踩守住，再次突破入场”。冻结原信号高低点、三个不同bar、24根到期、确认后次开；保留原stop/2R启动4ATR/20bp，按真实延后价重算R。不叠扩张门。
- 638币×15m/1h，1276流零错且父版成交/状态/原随机对照完全复现。普通15m/联合15m/普通1h/联合1h成交5913→795、2789→336、1322→199、689→88；全期净胜26.87→32.60%、27.38→35.17%、34.43→38.25%、37.96→39.47%。
- 平均净R依次−.0865→+.0468、+.0242→+.1623、+.2638→+.1437、+.5893→+.5059；≥5R153→14、89→8、47→3、39→3，比例四格下降。后段四格均净R负，1h联合30.83%→22.22%、+.0244→−.1255，仅27笔已平。
- 中位等6–7根、追价0.60–0.68个旧R，风险距离约1.60–1.68倍；按旧风险重算仍缩水。主要同确认随机匹配只覆盖15%–25%，四超额负、Holm p1；补充无成交记0请求对照也未确认优势。
- 最终run_v1/summary_v1；报告analysis/p1_spike_v128_retest_entry_20260923.md；大账本archive/consolidated/spike_v128_retest_entry_20260923/replay_and_ledgers_v1.tar。25专门测试+71边界检查通过；Luna实际gpt-6-luna/max。首轮policy覆盖和控制无成交分母、父status键错误均修复，失败smoke_v1/v2/v3保留，smoke_v4与全量16CSV哈希相同。
- 拒绝此冻结定义的统一默认；下一步优先因果价格位置/确认代价诊断，不能用未来成功回选早入价。无Pine/生产/训练/账户/退出/成本变更，资格false。Notion记录3e48856479af81d8a1eece7071453496。

## 最新：SPIKE扩张high20入场门已研究，拒绝默认接入（2026-09-23）

- Owner“开始研究”后冻结单变量：信号ATR%/过去含当前96根均值，严格高于此前30天80%分位；原退出/20bp/原风险R固定，15m/1h普通与联合均测。两个月旧638池，1276流零错、父版逐笔及原随机对照全部复现。
- 成交降66.6%–74.9%。15m普通均净R−0.0865→−0.0940，联合+0.0242→−0.1484；1h全窗普通+0.2638→+0.3948、联合+0.5893→+1.0192，但后段恶化。1h联合后段48笔，胜率30.83%→22.92%、均净R+0.0244→−0.4089、≥5R为0；不能用全期好看宣布成功。
- 同扩张条件随机入场为主要单特征对照：四主检验Holm p均1。1h联合全期超额+86.53bp、区间[−189.02,+351.03]，后段−275.35bp。全期原39笔≥5R留17、原11笔≥10R留5。拒绝统一默认门，不推断所有扩张定义无效。
- 实验exp-spike-v128-expansion-entry-20260923-v1；报告analysis/p1_spike_v128_expansion_entry_20260923.md。最终run_v2/summary_v1；身份链修复前后5104 CSV哈希全相同，33流继承原起始仓、未知候选0。9项行为测试，已核实实际GPT-6 Luna/max独立复核；原始run_v1和smoke保留。全量本机archive/consolidated/spike_v128_expansion_entry_20260923/replay_and_ledgers_v1_v2.tar，delivery记录SHA。
- 后续优先诊断入场失效与追价程度，先查旧实验并预定单变量。没有改Pine默认、退出、成本、训练、ACTIVE或账户；资格false。 Notion研究记录`3e48856479af810ebf2de66e673bb066`已同步并关联父研究；报告/紧凑产物提交`bbfe8ab3fc`已推送main。

## 最新：SPIKE V12.8 近两月 15m/1h 全量回测完成，未验证随机优势（2026-09-23）

- Owner要求两周期、多子代理详细回测。固定2026-07-23 00:00Z至09-23 04:00Z，638旧合约池、1276流零错误；583合约完整ready，55合约近期平价零成交量，不冒充活跃市场。20bp成本与原退出不变。
- 普通SPIKE：15m候选7060、闭合5842、净胜率26.87%、均净−23.54bp；1h候选1615、闭合1281、净胜率34.43%、均净+43.48bp。联合多头：15m候选2790/闭合2736/27.38%/−4.98bp；1h689/656/37.96%/+133.27bp。两个信号族重叠，不能加总。
- 确认曾浮盈后净亏止损：普通15m/1h为3486/711，联合1653/361；达到1R后净亏止损分别908/188和447/112。止损柱顺序不明另列，不夸大可确认浮盈。
- 四组配对净bp超额均负（−20.70/−10.05/−48.69/−51.90），Holm p均1；8月23日起四组平均净bp均负。全窗1h PF高不能称稳定优势。
- 报告`analysis/p1_spike_v128_recent_20260923.md`；实验`exp-spike-v128-recent-20260923-v1`含表格、逐笔与delivery；大文件本机归档`archive/consolidated/spike_v128_recent_20260923/frozen_replay_and_ledgers_v1.tar`。数据/回放/统计/诊断builder均先提交后运行；20项新增行为测试通过，独立复算交易/成本/MFE/随机均值及主p值、极端路径。
- Python研究移植，尚未原生Pine逐事件市场对账。V12.8加仓仅显示候选（15m第1/2次407/164，1h163/115），没有追加成交收益；参数、账户、训练、ACTIVE均未改，资格false。研究未验证正超额，注册为rejected；本次描述性任务已经完成。 Notion已同步`3e48856479af81b7a051d9d3cefb096e`（完成/非生产），最终报告独立核对PASS。

## 最新：训练选图规则叠加SPIKE原始多空，未通过（2026-09-23）

- Owner要求解释当前训练选图并适配试验；两名Luna Max分别追溯规则、审查SPIKE路径与统计，主线程完成全638币15m重放。实验`exp-spike-ma-launch-gate-20260923-v1`，报告`analysis/p1_spike_ma_launch_gate_20260923.md`。
- 只变入场门：原版、密集单特征、全部硬规则、完整参考；各自串行，保持原始反向退出/成本20bp/2R启动4ATR追踪。输入2024-09-10至2026-05-01，2025-09-10切段。未来3R标签、未来最高分NMS不进入特征。
- 51370候选→15135密集/59硬规则/6完整参考；后段原版20600闭合、净−10.15bp/笔，密集7147/−5.64，硬37/−49.06，完整3/−226.73。两主臂Holm p均1且样本不足100，无通过。原后段1325个≥3R硬门只保留2，134个>10R全部丢失。
- 全期硬门+37.82R，但同一时间的FIL/MINA两笔+44.11R，不据全期精选结果改默认值。Python原始双向重算，不宣称原生Pine/历史V12.6 raw父账本parity；当前同时相交接法不能代表全部集成方式。
- v6正例1841个是3R精选规则形态，非逐样本Owner金标；数值选图close均线，图像渲染HL2；负例全窗非密集而不是未来亏损。core+5在15m需晚75min，不能回画启动前。
- 原始builder先提交`5dc60ec7f1`，统计先提交`52231c21db`；14专项通过，扩展468通过/8既有失败。结果、receipt、输入SHA保留。没有改Pine默认/监控/训练/ACTIVE/账户，资格仍false。

## 最新：SPIKE V12.8 两次加仓候选提示（2026-09-23）

- Owner 选择先加图表提示、保留 V12.6 原退出。新源 `yoyo/evaluation/pine/spike_burst_v12_8.pine` 从 V12.6 派生，没有合入下方 V12.7 时限实验；旧源/旧私有脚本均保留。
- 每个原 V9 多头参考框最多两次候选：完整已收盘 H1 回踩突破、原始 2R、结构参考提高、候选价递增。结构参考触及时暂停本框提示，原 ATR/反向/提前退出完全不变。两次后仍更新参考；候选不是成交，没有仓位/保证金/利润保留会计。
- 连续整点 H1；60m 原生、60 可整除的分钟图用 `[1] + lookahead_on`，低周期在实际可知的图表收盘标记；4h/日线不提示。极简模式隐藏新增显示。
- 10 项源码门通过，去新增块和身份替换可逐字节恢复 V12.6。TV 合成状态探针 10,961 根反复场景 bar 无断言错误；不等于独立市场样本。新私有“SPIKE V12.8 · 两次加仓提示”已保存 revision 2，原生编译通过（保留父版 `second` 命名警告）。
- 代码提交 `0ca64dc9dc` 在探针生成之前；只改状态表位置的后续提交 `34b8b6ca08`。报告 `analysis/p0_spike_v128_roll_hints_20260923.md`；实验 `exp-spike-v128-roll-hints-20260923-v1`。没有收益回测、实盘准入、账户或监控变更。

## 最新：V12.7候选「先突破后V9加时限」已回测，默认关闭（2026-09-23）

- Owner「先回测第1条时限，再做V12.7」。唯一变量：持线证据寿命600→8根15m，其余（V9准入、几何、先框后突破、止损、退出、20bp）不变。638/638币零错误。
- 延迟解决：联合相对首次收在线上方的滞后中位13→2根，先突破后V9型57→4根，入场追高中位0.564R→0.229R，追高>1R比例28.5%→2.1%；先框后突破占比27.5%→70.0%。
- 收益未改善：全期18,934→13,669笔，胜率28.68%→29.10%，每笔净R−0.0907→−0.0641，总净R−1716→−876；但后段胜率不变24.38%、每笔净R−0.2325→−0.2354、净bp−40.2→−47.9、随机超额−0.019→−0.024R，四项Holm p全为1。净>5R 482→382、>10R 133→107（占比反升）。
- 全期改善全部来自少做9,630笔陈旧配对（−1011R），共同9,304笔逐笔相同、质量差0.00。基线臂逐字段复现V12.6 run_v2 baseline与控制组。
- 运行前写死的默认规则因后段每笔净R略差而不通过：V12.7 Pine 交付开关「先突破后V9：限制最长间隔」，默认关、上限8根。Owner可另行决定默认开，但那是取舍不是验证。
- 报告`analysis/p1_spike_v127_held_age_20260923.md`；实验`exp-spike-v127-held-age-20260923-v1`与产物已注册；专项50通过，扩展468通过/8既有范围外失败。学习两条：证据寿命即延迟、少做不等于每笔更好。
- **未完成**：`yoyo/evaluation/pine/spike_burst_v12_7.pine` 还没在TradingView编译或建私有脚本（Owner本轮中止浏览器操作），因此不能称原生交付；Notion无连接器未同步；画线"不准"仍等Owner给具体例子。

## 最新：均线密集启动规则已移植为 Pine V1/V2（2026-09-23）

- Owner“写”“把相似度和评分也移植进去”。V1 只含两组硬性条件（13+26）；V2 追加第一道最近邻相似度（50 样例，阈值0.5）与第二道分段lock-step/DTW/DDTW相似度＋六轴质量分（门槛0.3611898959，距离尺度1.1995844783）。渲染box_height_norm门未移植（扫描器本身写0）。
- 一致性：90个冻结候选全部通过硬门；6个候选按Pine嵌入精度复现账本quality_score，最大差5e-5，A级判定一致；生成文件＝模板＋数据包重渲染逐字节相同；分段距离/最近邻与冻结研究函数一致1e-12。12项测试通过。
- V1仅硬门的信号约为研究候选10倍（AAVE 15m 12:1、APT 15m 20:2）；V2开“只显示A级”回到研究口径。本轮无收益结论。
- 交付`yoyo/evaluation/pine/ma_dense_launch_v1.pine`、`..._v2.template.pine`、`..._v2.pine`（114KB）、`ma_dense_launch_references.py`、`ma_dense_launch_v1_reference.py`、reference_pack.json。报告`analysis/p1_ma_dense_launch_pine_20260923.md`；实验已注册。
- **未做**：TradingView原生编译与逐事件对账（需Owner同意用浏览器登录其账号保存私有脚本）。未改SPIKE/监控/账户；Notion未同步（无连接器）。

## 最新：YOLO形态v6 A组高分前10%正收益复核为不稳健；V10.4前瞻检验被叫停（2026-09-22）

- Owner“先测试yolo模型”。v6 A组原经济评估（RESULTS.md：val top10 +12.0bp、test +15.2bp）完全复算；合并230笔+13.4bp，但日块95%区间[−34.8,+60.5]、中位−93.4bp、去最好3笔−14.0bp、相对随机超额+32.9bp周块p0.108，Spearman约0.05。预注册判定not_robust。报告`analysis/p1_ma_morph_v6_econ_audit_20260922.md`；代码4fd2dc5cfd先于结果。形态模型可作识别/可视化，不作交易信号。
- V10.4联合1h前瞻（止盈0.3/0.5/1R＋原退出，胜率≥77%判定）代码71a8e2a705已提交；Owner要求停掉后台，下载停在229/638，可续跑。121币预览不达标且不能当正式结论，见实验目录`STATUS.md`。
- 对话中曾误称v6未做收益检验，已在报告更正。下一步Owner待定：资金费率套利历史测算、资金费率极端反向；Notion未同步（无连接器）。

## 最新：SPIKE“跑赢随机”结论改用bp复核，无一通过（2026-09-22）

- Owner同意“先做第1步”。只读复核V10.4六周期联合、V9全量、V12.6 15m共357,863个已平仓匹配对，把超额单位由R换成bp；32个全期单元bp确认0、仅R显著1（V8 30m）、不显著31。
- 15m以上成本R差只贡献0.010–0.035R，R结论基本不是成本假象；但bp区间因尾部加权变宽。V10.4联合1h：R +0.244（p0.011）、bp +69.9bp [−20.7,182.8]（p0.105），前后段两口径均正，是唯一稳定方向的候选（30m次之）。V9 30m、V10.4 15m、V12.6 15m后段bp转负；V9 4H无超额。
- 代码7a15319281先于结果，4项测试通过。报告`analysis/p1_spike_bp_random_audit_20260922.md`；实验已注册；Notion未同步（无连接器）。下一步待Owner：仅对V10.4联合1h补2026-05后数据做一次预注册检验；确认前不做滚仓/实盘。

## 最新：SPIKE V1与V12.6小周期1m/3m/5m均不可交易（2026-09-22）

- Owner要求分别测V1、V12.6的1m/3m/5m，并看V1近两个月截至今天。OKX官方1m归档＋已确认API尾段（ETH/BTC永续，2024-02-01至2026-09-22 14:00Z，0缺分钟），3m/5m由同一1m聚合；V1原版/V1同退出/V12.6三臂，20bp，匹配随机。
- 全期、前段、后段全部净亏（唯一例外V1原版5m前段+4.76R，后段−34.91R）。全期均净R：V12.6 1m −1.253/3m −0.745/5m −0.452；V1原版−0.833/−0.617/−0.277。V12.6毛bp +0.7/−0.5/+3.6，成本平均0.33–1.32R/笔。
- 近两个月V1原版1m 18笔−12.18R、3m 14笔−4.63R、5m 8笔−6.73R；V12.6同期−265.14/−69.86/−5.65R。
- 预注册R超额在1m显著但是假象：随机止损更窄、成本R更大；bp超额全部跨0（V12.6 1m −2.3bp p0.92）。学习`docs/learnings/r-excess-over-random-is-biased-when-control-stops-are-narrower.md`。
- Builder 1a178d154e、统计ddcea13f01先于运行；6项测试通过（含V1原版与09-11账本规则逐行一致）。报告`analysis/p1_spike_lowtf_v1_v126_20260922.md`；实验`exp-spike-lowtf-v1-v126-20260922-v1`已注册。V12.6为Python移植、未原生对账；本会话无Notion连接器，Notion未同步。无Pine/监控/账户/训练变更。

## 最新：V12.6联合重检H1未改善扣费胜率（2026-09-22）

- Owner同意优先扣费普通盈利胜率并保留大盈利。638/638币15m默认Python port完成、18,956候选，原20bp与退出不变；新增联合当根H1方向只拒22个（0.1161%）。全期28.6786%→28.6696%，均净R−0.090655→−0.091099；后段亦变差，不采用默认。
- 被删22笔8赢14亏、净+6.3989R，无新增成交；净>5R 482→480，>10R仍133。43专项通过；扩展505过、8范围外失败；audit_v4核对18,927共同成交、32源码Git blob与全部收据/3828叶哈希通过，30独立指标恒等式通过。
- 结果analysis/p1_spike_v126_htf_recheck_20260922.md；run_v2、最终summary_v3与audit_v4.json，实验/产物注册；Notion3e38856479af81bdacf6d31de35a1d6e状态不确定、非生产。原BB20 smoke及审计dtype失败保留。原生Pine pivot未对账，不能把研究数当实盘胜率；无Pine/训练/账户改动。

## 最新：SPIKE+突破的BTC全局SMA门未通过（2026-09-22）

- Owner要求验证BTC永续同周期或固定1h SMA120及邻域。638币、15m/1h、9301候选、原20bp，SMA60/120/240，历史price与当前RSI7退出分别对照。Builder36c2d439先提交，638/638零错误，旧基线逐笔/状态/随机对照完全复现。
- 当前RSI7 15m无门-0.0946R/笔，同周期120降为-0.1422，固定1h120为-0.1182；少做34%但原46个>10R只保留34。1h120全窗改善但后段退化；两种退出前段均选H1 SMA240，后段仍负、24条件0通过，不改默认门。
- 独立BTC决策核对65107行无差异。23专项通过；扩展420通过、2个本轮范围外L1迁移哈希失败。报告`analysis/p1_spike_joint_btc_gate_20260922.md`；实验/产物已注册；Notion3e38856479af81519395d61310f7c9ba状态拒绝。未改Pine、监控、真实账户或训练/生产资格。

## 最新：突破+spike 已接入 Bark 与 TG（2026-09-22）

- Owner 要求联合信号双通道通知。旧版独立 lines 账本未接发送器；现 1.16.0 已重启，联合15m/30m/1H/4H启用后新收盘30分钟内发送，历史/普通break/重复不入队。V9原始及YOLO仍原Bark范围，TG原始流未开启。
- 独立原子队列、每通道切点、不可变通知payload及发送前复检；429限次、未知不自动重发。页面逐条显示双通道回执，原2条Bark历史未知改为数量提示。
- 127定向Python＋24前端检查通过；Bark与既有TG频道“Yolo均线密集交易系统”明确标注测试均获有效回执（TG2094），测试不计行情信号。启用切点09-22 12:55:49.816北京，未补发110条旧joint。
- 报告 `analysis/p1_spike_joint_notifications_20260922.md`；证据 `output/qa/joint_notifications_20260922/`；学习 `docs/learnings/display-events-need-their-own-notification-contract.md`。旧V1 service测试9失败已在HEAD复现，不宣称全仓通过；无Pine/模型/生产/真实订单变更。

## 最新：高周期线实体确认与固定2R已回测（2026-09-22）

- Owner提出开盘/收盘都越高周期线才止损，并调整2R。8个预列组合完成：原/均线触价/单收盘/实体确认，分别原追踪与毛2R全平。29币15m前轮归档、H1 SMA60门、20bp，非V12完整bk，TV仍V12.6。
- 后段多头均线触价501笔−32.03R；实体确认526笔−64.03R，14笔净亏超2参考R、最差−4.43R。原固定2R全期多头胜率31.42→34.28%，但净R−9.61→−90.78；多空合计245.01→−91.60R。均不支持直接默认替换。
- 源a7f558ef71先于回放；83检查通过，29流双父账本parity、21,243共同fixed/serial一致。月置换8月/30校正分辨率不足，inconclusive不冒充证伪；pandas FutureWarning保留。报告analysis/p1_spike_ma_confirm_20260922.md；新实验exp-spike-ma-confirm-20260922-v1。
- 独立硬止损兜底与2R部分止盈仅下一轮假设，未运行/未设默认；2R本轮按对话明示毛2R全平，不等于旧V9净2R保本。无训练/实盘改动。
- Notion研究记录：https://app.notion.com/p/3e38856479af8163afc1c03769c2eb6c；状态完成、非生产，保留失败与适用边界。

## 最新：均线外侧止损已回测，多头未过默认替换门（2026-09-22）

- Owner明确“先加上回测”，四臂原版/SMA120/H1 SMA60/both已实现。29币15m同H1方向门，2024-09-10至2026-05-01前Binance归档；初始锚点单变量、原成本/反向/2R启动4ATR不变，入场风险与仓位倍率重算。
- 全29流完成，原2701笔逐币一致，2700闭合/1删失，47项检查通过。多头全期both胜率31.42%→32.07%、净R−9.61→+10.95；后段仍亏且均净bp−9.40→−16.12，三方案预列门均失败。救回40原亏单同时伤28原赢家；保留空头后段改善但未验证的线索。
- 这是V9风险框机制，不是新版V12完整bk几何或无时间戳的OKX PEPE截图复现；Python次开与Pine收盘参考差异明确。新研究exp-spike-ma-stop-20260922-v1；报告analysis/p1_spike_ma_stop_20260922.md；原TV/Pine V12.6未覆盖。Notion首次传输失败后已精确查重重试成功，链接见notion_sync.json。

## 最新：SPIKE V12.6 自适应视觉已交付（2026-09-22）

- 新私有“SPIKE V12.6 · 自适应视觉”revision1，保留V12.5源文件及私有脚本。自动黑白趋势线、确认突破起默认3根K着色、精简六均线与主趋势线、独立Pin Bar颜色；历史局部整理与淡化保留。
- 源码18722beb0f先于TV保存；编辑器全文148787字符一致，26项检查通过。原生黑白背景、精简、自定义橙色与恢复默认验证；最终OKX PEPE15m白底，All changes saved。桌面端实例同步未核实。
- 止损仍为最近5根和ATR，不引用SMA120/高周期均线。截图缺入场时间，不能唯一定位PEPE事件；未改止损或声称收益改善。报告analysis/p1_spike_v12_6_visual_20260922.md；Notion3e28856479af81b686e9fbad52560b34。

## 最新：5R V5已回放，降低目标未带来合格的新筛选（2026-09-21）

- Owner“或者大于5r”将同批数据、同两模型离线研究的标签改为最终扣费净R严格>5，原风险分母/退出/20bp成本不变。49,070闭合中1,703个>5；未知不作负类。源码7482247先于训练，旧10R源码与产物保留。
- 2025-10-01至2026-09-10共同期，原串行662/24,568=2.69%；新模型最高逻辑回归top10为84/2,517=3.34%，保留72原赢家、漏590、新增12，均净-4.23bp。旧风险127/3,138=4.05%、均净+20.08bp；旧10R模型top5按5R计54/1,201=4.50%，仅保留51/662。新8档中7档低于旧模型同5R计分，唯一上升档仍低于原版。8新模型研究门全失败。
- 四季训练、3,531流14路径完成，原路径及候选经济parity通过。25专项、108相关门通过；新模型独立数值概率最大差2.22e-16。实际matmul警告保留，未断言底层原因。
- 审查补齐比较器上游预测/评价receipt链与源流集合校验，另存target_comparison_v2，与v1两张CSV逐字节相同；未重训或重放价格。初稿报告/manifest留存，最终报告analysis/p1_spike_5r_model_v5_20260921_final.md、delivery_manifest_v2.json。Notion3e28856479af81eea861d838b90d45a7为拒绝准入。
- 无Pine/TV/生产/账户修改。现有20特征未证明足以事先挑出5R赢家；若扩展大盘/板块/高周期等新信息轴，另立明确单变量与训练范围；不能把更低标签门槛或盘中MFE当兑现胜率。

## 最新：10R V4离线分类器已完成，仍未超过旧风险基线（2026-09-21）

- Owner“继续”批准仅本数据20特征+周期、两个固定分类器的离线训练例外；全局P0/P1、YOLO、父产物资格和生产准入未变。9月成熟标签训练/3月无标签分数门槛，2025Q4起四季前推，源码d2aa13403d先于训练。
- 共同目标期2025-10-01至2026-09-10：原串行206/24,568=0.8385%；逻辑回归5%档21/1,201=1.7485%，保留19旧赢家/漏187/新增2；LightGBM10%档45/2,782=1.6175%，保留38/漏168/新增7，均净-20.25bp；旧风险十分位57/3,138=1.8164%。8模型门槛全部未通过综合研究门，不promote。
- 独立全候选ROC-AUC0.5948/0.6039，低于负风险分数0.6255。高预测概率段过度自信；WIF14.3299R被所有门槛拒绝。不能通过抬高分数门槛承诺高胜率，也不能换算100U滚仓。
- 3,531流14路径完成，原版及共同候选经济parity、原cache哈希通过。15专项、108相关门通过；全仓注册表另4项因两条既有source_commit缺失失败，父HEAD复现记录保留。实际matmul警告仍在，独立数值核验概率最大差1.53e-16、梯度8.47e-05，支持冻结分数可用，未锁定库层原因。
- 报告`analysis/p1_spike_10r_model_v4_20260921.md`、目录`exp-spike-10r-model-20260921-v4`、Notion `3e28856479af810fa172fcb435e084ae`均已归档为拒绝准入。两固定模型授权范围已执行完；上级周期/大盘/板块新轴只列后续候选，未自动拓展训练或改变退出/成本/Pine/账户。

## 最新：从全部原始信号提高10R命中率，V3/V3.1未验证出新改善（2026-09-21）

- Owner再次纠正：主目标是最终扣费净R>10的命中率，普通赚钱胜率只是辅助。全49,207原V9做多候选已补齐；49,070闭合/448个>10R，137删失，原41,814交易逐笔覆盖。不是只研究旧赢家或实际开过的交易。
- V3对20因果轴、6,960规则只用较早段选择；V3.1另立计划用每月此前6完整月成熟标签选规则。后段原串行210/25,567=0.8214%；风险低20%93/6,161=1.5095%，保留90旧赢家/漏120/新增3；逐月选择13/1,657=0.7846%。全部新方案率差区间跨0、随机校正p=1，拒绝准入；旧风险10%后段1.721%也仍未验证。
- 3,531流全部完成、原经济路径一致、原缓存未改。25专项和108相关门通过；独立Luna确认所有标签、旧赢家覆盖、9,000历史阈值、五个固定策略保留数。原串行recall混入新增赢家，已新建serial_stats_v2按event_key交集纠正，原产物保留。V3.1父receipt加固后重跑，决策逐字节一致。
- 联合报告`analysis/p1_spike_10r_discovery_v3_20260921.md`；Notion V3 `3e28856479af81f5a96fcb782658bd18`、V3.1 `3e28856479af81d78963d4016a0f28aa`均拒绝准入。WIF14.3299R被所有筛选拒绝，不能为已知截图调参。
- 下一步具体离线分类提案在V3目录`NEXT_MODEL_PROPOSAL.md`。已申请仅本研究离线训练例外，因为AGENTS规定P0/P1前禁止新训练；尚无Owner答复，禁止启动训练或改变training_eligible。未改生产、Pine、账户或100U滚仓执行。

## 最新：High-R Entry V2/V2.1已回放，比例改善仍未过验收（2026-09-21）

- Owner改为优先普通胜率、只抓10R机会。V2固定20根突破单变量失败：全期胜率29.33%→30.21%，净>10R比例0.8801%→0.8186%；原367高R保留145/丢222/新增35。报告`analysis/p1_spike_high_r_entry_v2_20260921.md`。
- 预列风险分数诊断提供探索线索，另立V2.1：原V9信号参考风险<=同周期过去3完整月第10分位，至少100候选，阈值先提交后回放。无标签校准49,207候选、75阈值61已知；不改原止损、退出或成本，不组合突破/高点追踪。
- V2.1全期5,286闭合，胜率33.33%、净>10R96笔/1.8161%；后段3,312闭合，28.08%/1.7210%、均净+9.27bp。但两个率差95%区间含0、匹配随机p0.22144，仍未过验收。原367高R保留85、丢282、新增11，WIF截图14.3299R被筛掉（参考风险2.8531%>月门1.8911%）。不称高胜率或只进10R赢家。
- 两版均3,531流0失败、原V9逐笔一致。V2 builder b2e5a24dbd、20专项和108门通过；V2.1源码f91e769032、阈值f69d1e52e6先于运行，22专项通过，相关107首过+1目录重命名竞态后重跑通过。V2独立审计、V2.1主任务75阈值/全部receipt及17,655文件哈希核对留存。
- V2.1报告`analysis/p1_spike_high_r_entry_v21_20260921.md`。研究目录`exp-spike-high-r-entry-20260921-v2`及`...-v21`；Notion3e28856479af8133ad1ad809d0eae4c7与3e28856479af81cb84b5de79f75793ca均拒绝准入。保留候选与负面证据，不再本轮追调参数；无训练/Pine/TV/生产/账户变更，不把R换算100U滚仓。

## 最新：High-R V1高点追踪未提高大趋势捕获（2026-09-21）

- Owner要求优化主要抓高R的新版本。旧V9已闭合多单41,700、盈利12,230、净>10R367；记录毛MFE>10有1,164，797笔未以净>10兑现，但不是全部可挽救。旧MFE通常不计退出bar。
- 只将多头4ATR追踪锚点close改为入场后已知最高high，原2R收盘启动、初始止损、20bp和空头不变。3,531流串行全完成、0失败，原V9全部逐笔一致；完整亏单/空流/删失保留，随机也用新版退出。
- 净>10R367→259：保留194、丢173、新增65；净>20R100→57。胜率29.33%→30.63%但全期净R1697.59→1084.40、单笔净bp2.82→−4.97。后段210→152笔高R，仍−2158.20R；随机超额−0.03057R、p0.78027。**拒绝替换。** WIF单例14.33→15.38R不能覆盖RAVE134.65→1.37R的提前退出损失。
- 正式builder bdaec68c7a先于run_v2；21专项、108相关门通过。报告`analysis/p1_spike_high_r_20260921.md`；实验`exp-spike-high-r-20260921-v1`保留Python源/逐笔/控制/receipt及独立复核。Notion3e28856479af81e0bf7ed272ebb818a0拒绝。未改Pine/TV、监控、训练或生产；没有把无杠杆R当100U滚仓收益。

## 最新：RSI7已纠正为从每笔实际开仓开始计数（2026-09-20）

- Owner指出XTZ应从开仓起算；上一条“入场不重置”是助手错误推断，不能归为Owner确认。连续性和计数起点必须分开。下方全局V1仅保留为历史记录，不再用于评价Owner规则。
- 源码1245ff2776：RSI/SAR预热保留，每笔入场自己的计数从0开始，入场bar收盘计入、信号及此前不计；同周期，仅大菱形连续同色，异色重置1；恰好第7个空头收盘确认后次开全平，无盈亏条件，原保护先触发先退出。
- XTZ15m开仓后第6个为09-19 11:30，第7个为23:45，次开0.3671净52.8178R；错误V1是29.5905R，原保护39.3633R。最高0.3968不等于可成交退出价。LIGHT30m本仓仅3个，仍按原V9反向净0.2666R退出。
- 128项Python＋22项前端通过；既有服务482币2410格136.4秒0错误，83条全部V2，68闭合/15活跃。闭合合计原价格97.3057R、错误V1 87.5693R、修正V2 110.7603R；相对原价格只有XTZ变化，不当收益验证。
- 原价格19:52:49快照83条完整保留；20:24:45切换前V1另存previous快照83条，与备份逐条相等；事件ID、首次发现时间不变。原生浏览器三版本已核对，最终当前规则/XTZ筛选显示52.82R。
- 新报告`analysis/p1_spike_joint_rsi_entry_count_fix_20260920.md`，证据`output/qa/joint_rsi_entry_count_20260920/`，学习`docs/learnings/streak-continuity-does-not-define-its-counting-origin.md`。Notion V2 `3e18856479af81189c6dcfce78b327a0`关联旧V1，仍研究中/想法/非实盘；V1标拒绝。只改模拟退出，无Pine/TV或实盘操作。

## 历史：RSI7全局计数V1（已被上方入场起点V2纠正，2026-09-20）

- Owner最终明确连续同色大菱形计数、异色重置、同周期，不加盈亏分支；当前多头联合遇第7个空头强菱形收盘后次开全平。指标计数不因入场重置，小菱形/圆点不参与；原止损、4ATR保护、V9反向先触发先退出。
- 源码fd298c8885，前端切换加载修复99e2dc83c5；124项Python（含层间门）＋20项前端通过。已通过既有manage restart加载；首次482币2410格138.4秒0错误，83条投影全部新版。当前15笔活跃计数均已知，窗口首色/缺口不明时仍必须unknown。
- 切换前83条旧performance和首次发现时间与SQLite备份逐条相等。界面“退出规则”可选固定19:52:49旧快照，不能把它当持续旧规则对照；不清日志，不改事件发现时间。实际浏览器新旧切换、加载时清空与XTZ全平原因已验收。
- 2笔历史RSI提前退出：XTZ15m原39.3633R→29.5905R；LIGHT30m原0.2666R→0.3029R。已闭合合计97.3057→87.5693R，当前样本减少9.7364R，不能宣称收益改善。两笔事件链均为多头强菱形后连续7个空头强菱形、下根open成交。
- 报告`analysis/p1_spike_joint_rsi_exit_release_20260920.md`，运行与浏览器证据`output/qa/joint_rsi_exit_20260920/`；Notion3e18856479af819eb65ed970222e0521仍研究中/想法/非实盘。只改前端模拟账本，Pine/TV仍是下条V12.5交付；没有新收益实验或生产准入。

## 最新：V12.5默认显示15m对应1h SMA60（2026-09-20）

- Owner要求15m也默认画出上级线。新私有“SPIKE V12.5 · 上级方向线”revision3：15m默认紫色H1 SMA60阶梯线，5m原15m EMA120保留；独立显示开关不改变过滤/退出/风险参数。
- 源码e793ce26c009；剔除显示/版本文字差异后与V12.4逐字一致，网页编辑器全文复制与本地相等。BTC15m/5m实图及默认开关验证，最终5m布局“All changes saved”。旧源/私有脚本保留。
- 桌面端长文本输入超时，交付在同账号Chrome网页版完成；未确认桌面端实例同步。报告`analysis/p1_spike_v12_5_h1_line_20260920.md`；本地截图与AX在`output/qa/spike_v12_5_h1_line_20260920/`。这是显示修复，无收益或生产变更声明。

## 最新：首4小时区间回归，BTC/ETH三年固定版均净亏（2026-09-20）

- Owner要求代码实现并回测BTCUSDT.P、ETHUSDT.P。固定OKX5m，纽约00–04墙钟区间，close越界后回归、次开、事件极值SL、2R、NY日末退出，20bp。纽约2023-09-20至2026-09-19午夜，各315360正式bar与31天预热。
- BTC2335笔毛−38.59R净−1694.91R，ETH2246笔毛+135.05R净−851.82R；后一年−584.73/−237.10R；前后与多空均净亏。后段对随机Holm p0.6020/0.09110，拒绝当前版准入。BTC/ETH有40/11笔2R赢家仍因窄止损成本而净亏；R不是账户百分比。
- builder36d27908a4先提交再run_v1；18专项通过、全部4581笔独立成交/账本复核、12原生API价格样本匹配。守门468通过8既有失败，失败细目见AUDIT.md；没有隐瞒或顺手改无关文件。
- Python与独立Pine v1源码已交付，TV新私有revision1编译运行。原生所绘9月17日参考H76648.7与API76655.2不同，完整parity未建立；TV原生CSV导出需Premium未完成，报告财务数字只用Python。
- 报告`analysis/p1_four_hour_range_btc_eth_20260920.md`；实验`exp-four-hour-range-20260920-v1`含中文逐笔、控制、图、SHA manifest；Notion3e18856479af8131a2b9d56908ea9397状态拒绝。无成本/障碍调参、训练、ACTIVE或真实交易。

## 最新：固定趋势基线没有稳定胜过Spike（2026-09-20）

- Owner“去做吧”授权统一风险比较。29币Binance、15m/1h/4h、V9/V9.1/Donchian20/55/SMA20/60共435流，20bp原风险；20/55为图表bar适配，不是完整海龟，不是V12.3全量回测。
- 后段15m简单三臂净−1598.34/−579.29/−1440.68R；1h三臂皆负；4hDC55+8.20/SMA+25.96但随机超额负/近零。九项Holm全1，拒绝当前简单替换主张。V9.1 15m+103.50R但空+132.92/多−29.41，回撤104.58R，随机p0.046875未达0.01。
- 回放d4e5898eea先提交，run_v2与v1所有逐流产物SHA相同；统计ba86ffea32先提交，statistics_v2仅纠正200条删失控制展示，收益/推断不变。18专项通过、539扩展通过8既有失败，Terra独立全汇总/配对/p/曲线核算通过。
- 结论报告`analysis/p1_trend_baselines_20260920_findings.md`，实验`exp-trend-baselines-20260920-v1`与final_delivery_manifest；Notion3e18856479af81bbae71e8c929d9805a拒绝直接替换，V9.1优势仍不确定。无Pine/训练/生产或成本止损变更。

## 最新：V12.4已在TV交付5m→15m EMA120方向过滤（2026-09-20）

- Owner授权优化5m并加过滤保存TV。新私有“SPIKE V12.4 · 5m方向过滤”revision1：仅5m默认开启，确认收盘严格高于已完成15m EMA120才多、低于才空，相等/未知拒绝；15m H1 SMA60、原始反向退出及原风控/线/框逻辑保留。
- 48检查及Luna Max只读复核通过；原生保存编译成功，仅保留旧second遮蔽警告。旧V12.3源文件及私有脚本保留。
- 原案OKX ETH 2026-08-19 14:00北京5m收1908.14，高于15m EMA120 1907.87：同根关过滤入场许可1、开过滤0；原空头参考被拦截。最终恢复5m开启过滤，12:47:27布局已保存。
- 源码c66fce874f53；报告`analysis/p1_spike_v12_4_5m_filter_20260920.md`；实验`exp-spike-v12-4-5m-filter-20260920-v1`；Notion3e18856479af81998e51e11e956e48c5。功能完成，收益未新验证，前次六线未通过结论保留。无报警/订单/生产变化。

## 最新：5m开多空配15m六均线，未验证出可采用的最优线（2026-09-20）

- Owner要求验证15m哪条线适合过滤5m多空。固定29币Binance历史，六条SMA/EMA20/60/120＋无过滤，仅改入场方向许可；已完成15m、完整双向串行、原止损/退出及20bp成本。11943候选，ETH及方向分组保留。
- 前段选SMA120；后段4263→3339闭合，净−923.96→−785.21R，改善138.76R但月块95%区间[−173.47,361.13]、p0.203125；单笔净bp由−17.63变−18.72，10R赢家保留23/29。EMA120后段描述性最好−740.85R，不能追选为已验证最优。20周期几乎无额外过滤。
- 20专项检查、29原V9基线逐笔对账及独立Luna Max复核完成；交付再次逐币核对冻结输入、metadata和完整29覆盖。全仓468通过、8项既有失败已记录。复用builder时必须补跑final_audit中的输入/完整池检查；当前结果不受这些防护缺口影响。
- 报告`analysis/p1_spike_v9_5m_15m_ma_20260920.md`；实验`exp-spike-v9-5m-15m-ma-20260920-v1`，源码先提交27e23560f2，Notion3e18856479af8172a889c5d8a2dd0f0c拒绝当前规则准入。8月OKX截图未用于选线；未改Pine、成本、止损、训练或生产。

## 最新：V12.3 / V9.1 已新增15m专用1h SMA60过滤（2026-09-20）

- Owner明确选择SMA60，仅15m默认开启：确认收盘严格高于上一根已完成1h SMA60开多，低于开空；相等/未知不新入场。其他周期跳过，原始反向确认仍退出。既有框后来配突破不重审SMA。
- 独立源文件和TV私有脚本已交付：V12.3 revision2、V9.1 revision1，原版本保留；38检查及Luna Max复核通过。原生15m默认开关与数值、1h跳过均已核对；当前BTC OKX15m图为V12.3，V9.1保存在我的脚本。
- 源码提交1e1f1bab5d；报告`analysis/p1_spike_h1_sma60_release_20260920.md`；实验`exp-spike-h1-sma60-release-20260920-v1`；Notion3e08856479af81808071e4ea7014f6ed。功能完成，无新收益/全量TV parity或样本外最优声明。无HTML、报警创建、生产部署或下单。

## 最新：RSI第5个同色大菱形＋四次25%退出，1h/4h均未盈利（2026-09-20）

- Owner明确只数大菱形，连续第5个同色入场；不止损，每个反向大菱形平初始数量25%，四次清仓。小菱形/圆点忽略，单仓不加仓，亏损也减仓；两周期独立，20bp往返，无R单位。
- 1h33候选28入场全闭合18赢，均值净−0.6937%、合计−19.4233个百分点；4h8入场7闭合4赢、1笔剩75%，含末端均值−1.4163%、合计−11.3301个百分点。4h多头仅3笔全赢。相对随机p0.00950/0.003906但绝对损益仍负；不判通过。
- 持仓中位14.15/50.25天，考虑减仓后单笔过程最差−31.84%/−24.49%原名义；没有资金费、杠杆清算或账户复利。1h/4h每笔100控制，4h存在20–50唯一时间重复抽样，不能当800独立样本。
- source341568根5m含90天预热，原324288根不变；8native4h抽样完全相等，30/90事件0差异。builder先提交2b3b5b1f1c再跑run_v1；94检查与独立源/账本复核通过。
- 报告`analysis/p1_btc_rsi_fifth_diamond_quarter_exit_20260920.md`；实验`exp-btc-rsi-fifth-color-20260920-v1`含中文逐仓/逐次减仓CSV、PNG、控制与manifest；Notion3e08856479af81169602db2278ae57d7拒绝当前固定版本。未调参/Pine/训练/生产变更。

## 最新：历史YOLO/L2六线形态复用审计，221例来源已接回（2026-09-20）

- Owner询问旧判断层与历史人工形态能否做特征工程。221审核图逐张SHA与原清单相同；79正142负，全15m、126币；全部事件按原时间接回同OKX行情，0重叠窗口冲突，51旧行号有位移。原OHLC哈希缺失，不能称历史数值逐字节复现。
- 优先复用`global_shape.py`的33项precore提取器和分组消融方法；旧28项L2收益目标不同，旧自动标签AUC不能当Owner形态准确率。79正框边界是规则派生、原图可见核心后3–5根，旧val仅8正；需先核对因果视角/标签，再做特征分布、简单分类和正反模板检索。无训练、调参或准确率承诺。
- 报告`analysis/p1_ma_morphology_reuse_20260920.md`；实验`experiments/active/exp-ma-morphology-reuse-20260920-v1/`保留原路径inventory_v1和显式恢复recovery_v2。源码先提交472f189e02/e06b10d6c0，Luna Max审计完成；Notion3e08856479af8145a211de5fd0e7eac4记录核查已完成、方法待验证。TV仍V12.2，无HTML或生产变更。

## 最新：BTC RSI＋六线独立只做空，仍未盈利（2026-09-20）

- Owner追问后仅关多头入场，保留多头菱形撤销等待；同源三年、原1h高止损、3R、20bp、单仓均不变。194闭合50盈144亏，净-53.4293R；原189空单全部成交一致，新增5笔+1.2666R，无移除。
- 前两年-40.9061R，后一年-12.5232R；19400随机控制超额p0.1626、CI跨0。1终端空单+0.2199R单列；独立语义/合成/占仓复核通过，无生产变更。
- 报告 `analysis/p1_btc_rsi1h_sixma5m_short_only_20260920.md`；实验 `exp-btc-rsi1h-sixma5m-short-only-20260920-v1`；Notion `3e08856479af8160bdf2e3396a583699` 状态拒绝。只做空不是双向账本的简单子集。


## 最新：SPIKE 六均线密集拆分完成，不能直接把3ATR收紧到1ATR（2026-09-20）

- Owner要求详细拆分“20/60/120 SMA/EMA密集与交叉”；固定29币Binance历史、15m/1h/4h，1974280根共同有效bar，按2025-09-10切分。旧规则覆盖49.05%/48.51%/48.23%，其中三组交叉不连通58.98%/57.79%/57.37%。不同线对>=2仍保留97%以上。
- 宽度、最大值、核心前ATR、交叉组、覆盖率、自身P20、收拢与记忆均独立测试，未叠加优化。旧Owner语义9例的提案末端：1ATR会挡FIL/NEIRO/NMR参考，却保留MMT/MASK否决；提案坐标未确认，不能算分类准确率。body位置只做17例描述。
- 6因果/反例测试、87流旧特征一致、29收据核验；8张past-only机制图及对照图已视觉检查。替代规则未验证，应区分收拢/密集区/离束启动/失效时点；不擅改TV。报告 `analysis/p1_spike_ma_density_20260920.md`；实验 `experiments/active/exp-spike-ma-density-20260920-v1/`。
- 扫描源码67411473aa，图表a9c6c011e4，锚点da19593578；Notion3e08856479af81cf879ffdbad0a82d24标不确定。TV继续V12.2，无HTML/训练/收益声明/生产变更。

## 最新：BTC 1h RSI 大菱形＋5m 六线确认，三年固定3R未通过（2026-09-20）

- Owner确认OKX BTC-USDT-SWAP、原菱形1h低/高止损、3R；1h收盘后完整5m实体严格越过close SMA/EMA20/60/120，下一开盘、单仓、每菱形一单。固定20bp成本，无优化。
- 2023-09-19 21Z至2026-09-19 21Z，315648根5m+30天预热；564菱形，356闭合91盈265亏，毛+8R净-97.05R，前两年-61.39R、后一年-35.66R；多空均亏。随机超额+0.0916R/笔、CI跨0、p0.1907。direct520闭合净-289.36R，但六线平均净bp更差，不能只报R改善。
- 84检查、独立来源/因果/账本复核通过；19原生1h精确OHLC匹配，30/90天预热事件全同。run_v2仅修统计BLAS警告，两版交易逐字节一致、87600控制表一致。1末端空单标记+0.2199R单列；资金费/原生TV完整parity未验证。
- 报告 `analysis/p1_btc_rsi1h_sixma5m_20260920.md`；实验 `experiments/active/exp-btc-rsi1h-sixma5m-20260920-v1/` 含中文逐笔/分组表/PNG/来源收据。Notion研究 `3e08856479af81d8b089c8371b34ea0e` 状态拒绝，原假设保留。无训练、Pine变更或生产准入。


## 最新：V9 上级SMA过滤完成29币回测，暂不替换默认（2026-09-20）

- Owner授权均线方向过滤和长度搜索；纯V9双向、固定29币、15m→1h/1h→4h/4h→日线，20/30/50/60/90/120/150/200及基线。前段选15m SMA50、1h SMA20、4h不加，原风控和0.2%成本不变。
- 后段1195→1157笔、51.77→69.04R；325→321笔、47.16→51.41R；4h99笔−4.48R不变。15m改善CI[-3.36,42.22]跨0，1h只剔除4笔亏单；多头后段仍−56.30/−21.37R。全部历史15m网格最大SMA60只是描述，不反向改选。
- 全部87流与原V9 adapter逐笔一致、58项相关测试通过、Luna Max复核。8月精确符号检验三项Holm最低0.01171875高于原门0.01，已诚实记录此设计缺陷，不能把未过门说成无效。共同预热不翻转改善方向。
- 原statistics的BLAS警告经24项p标量穷举核对一致；statistics_v2逐元素求和并补跨界控制、全事件退出/删失一致性与逐币方向，收益/选参/p不变。回放提交d26088fde2，最终统计b842c5560e；实验`experiments/active/exp-spike-v9-htf-sma-20260920-v1/`；报告`analysis/p1_spike_v9_htf_sma_20260920.md`。
- Notion3e08856479af813984a1fbbe2be6c194标不确定。TV保留V12.2，未新建Pine或改监控/实盘/默认；后续若改版仍新文件和新TV私有脚本。无HTML。

## 最新：V12.2 历史淡线，撤销全图3条的错误产品假设（2026-09-20）

- Owner明确历史要保留，只能整理同一区域的重叠线。V12.2独立源文件及TV私有脚本“SPIKE V12.2 · 历史淡线”revision1已编译，旧V12/V12.1保留。
- 冻结A时间/价格邻域归组，局部默认3组可调；普通历史透明度74、联合40，历史资源上限40组，默认事件处截断。局部先淘汰，避免错误挤掉无关历史；检测/配对/风险/成本不变。
- 55项检查、Luna Max复核通过；BTC OKX15m宽图局部整理开关往返可见历史多区域，最终恢复开启并保存布局。已有second遮蔽警告保留；无完整事件导出或收益结论。
- 源提交4ab18ee2e0；报告 `analysis/p1_spike_v12_2_history_regions_20260920.md`；产物 `experiments/active/exp-spike-v12-2-history-regions-20260920-v1/`；Notion `3e08856479af8137a727db68846a5eae`。后续每改一版仍新建文件和TV脚本；无HTML或生产变更。

## 最新：V12 bk 紧凑样式，ONE三次普通突破合并显示（2026-09-20）

- TV私有修订12已编译；bk / bk+spike / C·RT，小字无背景。普通同本图/上级来源、同B时间价格、首次8根内显示聚合bk · N，保留首条代表线和首次时点，提示列所有成员；联合不合并，事件/报警/风险不变，选项可关。
- ONE原生05:00截点一个bk · 3，#218/#221/#220的02:30/03:30/04:30三次；03:00截点仅#218的bk，无未来成员。中间原生代表A/C与辅助reference不同，报告明确没有完整parity声明。51相关检查通过。源提交4becc98a0e，SHA aca54ec5f292fefdcbb45b496746224c619ef31411428a592ffecc358d8238ad。
- 报告 `analysis/p1_spike_v12_compact_bk_20260920.md`；产物 `experiments/active/exp-spike-v12-compact-bk-20260920-v1/`；Notion `3e08856479af813dab66c21468108a32`。回放已退出，当前ONE OKX15m。无HTML、收益结论或生产变更。

## 最新：V12 持线后 V9 配对与七项显示修订已原生验收（2026-09-20）

- Pine 私有修订11（03:26:09）编译，最终SHA `6b1d0af141eb0da92e9eaa42ee89e55ad00ba03f564fb7e4c953b38bdee8ab06`。简洁 `break+spike`、线/标签共同保留30组、默认BB压缩、退出虚线毛R及净R提示、15m/30m/1h/4h独立输入；默认数值未优化。
- 新增突破后每根收盘站在线上→后来V9；触线/跌回/断档/过期永久撤销。HTF建线用HTF参数，持线用本图收盘/存活根数。HYPE OKX08-19 19:00突破→23:00 V9，16根站线，最终TV已联合。旧空框19:15退出58.656、毛-1R净-1.23R提示已核。
- Bybit目标A08-18 22:30/B08-19 07:45/C16:30，原AB反弹仅1.416ATR被A冲击ATR压制；局部族新增B点ATR默认开关，2ATR门不变。最终原生已画目标线/break，结构17:15确认；无同期V9不宣称联合。OKX BTC08-24三条辅助候选中途回线下，不能按新持线路径配19:15 V9。
- 51检查通过，ONE原目标与前缀保持；辅助参考BTC14→15/HYPE12→13，不是交易数。收益/完整Pine parity/非默认分周期原生回放未验。V11.2、生产和风险成本未改。报告 `analysis/p1_spike_v12_held_break_display_20260920.md`；产物 `experiments/active/exp-spike-v12-held-break-display-20260920-v1/`；Notion `3e08856479af81049d35f914484a7cd7`。回放已退出，恢复OKX BTC15m，用户手绘保留，无HTML。

## 最新：SPIKE V12 已识别 ONE 局部回踩线，原生回放通过（2026-09-20）

- 新文件 `yoyo/evaluation/pine/spike_burst_v12.pine` 保留原主要三点族，新增A/B主点＋left2/right2局部C；本图与上级同步接入。V11.2源文件、V9/风险/成本及生产监控未改。
- ONE目标A09-15 12:45/B09-16 17:45/C19:30，Python与TV原生均20:15建线、09-17 02:30收盘突破；当时无V9多头框，所以只有单独突破。TV正式私有脚本版本7已保存、编译运行；诊断实例移除，回放已退出。
- 原生验收发现条件嵌套校验内派生body历史与同offset原始O/C不一致。V12修为原始OHLC实体＋每bar ATR快照，阈值不变；另把time历史缓冲扩大到3100覆盖极限输入。不能再称关闭辅助族后与旧Pine执行逐信号完全一致。
- 37测试通过、真实ONE8前缀一致；辅助参考1499根17线12突破不是合并Pine交易数。Luna Max实现参考及审查，主代理完成原生验证。报告 `analysis/p1_spike_v12_local_touch_20260920.md`；产物 `experiments/active/exp-spike-v12-local-touch-20260920-v1/`；Notion `3e08856479af817fb953ea3b3f8d2531`。仅单案例功能通过，跨币误报/收益与完整parity未验证，production/training=false，无HTML。

## 最新：ONE15m截图未识别的两层门已定位（2026-09-20）

- 以截图明显A9/15 12:45 .0007039、B9/16 17:45 .0006518推定白线，C19:30 .0006486距线仅.0143ATR；但B-C仅7根<24，C左12根包含更高B，raw/soft都不认pivot。卡在建线，不是贴线误差大。单改min_gap仍会被pivot定义挡住。
- 原OKX15m1499根/1h931根已冻结，截图游标9/17 03:30四价完全匹配；三日窗无15m突破/V9。1h9/17 08/09已有突破，但对应15m多头框long_open=false，默认框内规则无法联合；不能误套旧6根配对窗口解释。
- Luna Max核对具体索引/双轨和框状态。报告`analysis/p1_spike_v112_one_line_audit_20260920.md`；证据`experiments/active/exp-spike-v112-one-line-audit-20260920-v1/`。仅诊断，无参数/生产变化；未导出TV完整K线、画线对象或实际设置，保留parity边界。

## 最新：29币因果入场推进门单变量失败（2026-09-20）

- Owner「去做吧」后冻结E<=1：联合突破收盘相对父V9收盘的推进，以父收盘到已知原止损距离归一化。只试1.0，不搜阈值；原退出/成本不变。实现/计划/19项相关测试先提交a744bb3e72再回放。
- 29币、571候选状态和570原入场完全复现；新450入场/448平仓，共享450字段/随机配对一致，0新增成交，120笔直接拒绝。15m总净R−7.97→+11.21，1h−1.47→+8.08，4h+53.55→+53.50。
- 三周期全期改善CI均跨0。1h后段仍−13.23R，4h后段−4.78→−5.47R；预注册三周期均拒绝。15m净R增加19.18但总bp减少568.27，不能只报获利改善。PENDLE亏单被拒，也误伤PUMP1h+5.68R/ENA15m+7.16R。
- 产物`experiments/active/exp-spike-v112-entry-extension-20260920-v1/`；报告`analysis/p1_spike_v112_entry_extension_20260920.md`；Notion记录3e08856479af818bb35dfb97ca51f890标拒绝。Q6仅描述、未加第二门；未测试利润保护。无HTML/默认策略/生产改动。

## 最新：29币570笔已逐笔复盘，8张机制图（2026-09-20）

- 原入场、价格、退出、时间、MFE和保护轨迹570/570对账。568平仓（187盈/381亏）、2边界；无规则/成本/行情源变化。每笔有中文解释、信号时事实、后续路径与待验证不足，非人工看570张图。
- 381亏单中234未到1R便价格止损、78到1R不足2R回吐、16盘中2R但收盘未激活、11追踪激活后仍净亏（2笔仅成本拖负）、42反向退出。按浮盈轴127到1R、33到2R。1h82亏中29到1R、6到2R；该1h池没有激活追踪后仍亏。
- 512个止损根均有完整5m，原MFE只漏了2笔退出根内小幅推进（UNI0→0.00855R、BTC4h0→0.50688R），不改变1R/2R分类。Luna Max审查提出的开盘下界问题已修正，6个针对性检查通过。
- 产物`experiments/active/exp-spike-v112-trade-review-20260920-v1/README.md`含570行中文CSV、29逐币MD、8图索引；综合`analysis/p1_spike_v112_trade_review_20260920.md`。Notion已记为不确定，保留原随机对照和旧失败实验；无HTML/生产变更。

## 最新：固定29币收益已拆解（2026-09-19）

- 同一570入场/568已平仓：15m−7.97R但等名义每笔+0.3840%；1h−1.47R/每笔−0.2334%；4h+53.55R/每笔+6.9482%。R与价格金额权重不同，不能当账户百分比。
- 4h37笔中DASH32.49R和BTC12.46R贡献净R的83.9%；Luna Max核对极端交易算式和局部原始OHLC，暂未发现计算bug。4h后段14笔−4.78R/−3.9943%每笔；1h后段52笔−21.55R，15m后段160笔+11.64R。1h另3笔跨界+8.04R单列。
- 三周期相对原匹配随机的区间均跨0，保留不确定研究结论，不接受策略。详情`analysis/p1_spike_v112_selected_returns_20260919.md`，全部逐币/分段/尾部表与控制账本见`experiments/active/exp-spike-v112-selected-returns-20260919-v1/`。Notion已同步，无HTML/新回放/生产变更。

## 最新：Owner选定29币，三个周期共570笔入场（2026-09-19）

- Owner列出30币后明确先排除ARY；保留BTC ETH SOL PEPE WIF ZEC HYPE USELESS MUBARAK UNI PUMP ARB AR BOME ENA ONE PIEVERSE AAVE NEAR ZEN DASH PENDLE XTZ JUP AERO OP FIL APT API3。PEPE对应1000PEPEUSDT。
- 相同V11.2框内首破与原2024-09-10至2026-05-01窗：15m 409笔、1h 122笔、4h 39笔；568笔已平仓，PEPE/XTZ的4h各1笔数据边界未平仓。4h是本轮补算，29币全部完成；BTC1h适配器逐笔4单复现。
- 逐币数量/完整交易/覆盖见`experiments/active/exp-spike-v112-selected-counts-20260919-v1/`；解释见`analysis/p1_spike_v112_selected_counts_20260919.md`。新币覆盖较短，日线上级预热实际根数逐币披露。仅数量盘点，无盈利或策略接受结论。

## 最新：V11.2 1h完整账本诊断（2026-09-19）

- Owner要求1h详细分析并用Luna Max子代理；已独立复核。原run_v2按SHA读取，2,199已闭合、2条边界未闭合；556币，全期胜率27.83%，−0.0921R/−62.56bp每笔。
- 亏单68.4%记录浮盈不足1R，只有8.5%达到2R后仍亏；原退出根不更新MFE，不能推断同根顺序或止损后路径。
- 严格早段1,074笔−0.0540R，后段1,077笔−0.2278R，跨切点48笔+100.73R。旧早段把跨界赢家归入才为+0.0381R；全部数量和损益守恒。
- 等待不是单调关系：>48根组弱；13–24根正值对赢家和单位敏感，随机超额区间跨零，不是最优参数。三项前轮修法仍未通过，未新增策略回放。
- 交付：`analysis/p1_spike_v112_1h_diagnostics_20260919.md`；完整2,199笔中文CSV、PNG与所有分组表见`experiments/active/exp-spike-v112-1h-diagnostics-20260919-v1/delivery_manifest.json`。Notion已存为不确定；无HTML、Pine/监控/实盘变更。

## 最新：V11.2 三项执行逻辑研究均未通过（2026-09-19）

- Owner「去研究吧」后，分别检验 V9 后0–6根首破、沿用父V9止损、最高价2R激活原追踪；不组合、不搜索参数。638币、15m/1h独立串行，原9287条记录复现。
- 15m时效过滤净bp/笔 −16.29→−4.35，后段仍−35.82；改善95%月块区间跨0。1h全期−62.56→−73.35，后段更差。仅15m时效保留为待验证线索。
- 父V9止损提高胜率但不等于改善价格收益；1h全期净bp−74.26。50笔救回EIGEN/ETC/ORDI，也损失BOME/TRUMP/LINK三笔赢家。高点激活追踪全期两周期均变差。
- run_v1有一笔随机控制的浮点近零风险，保留失败证据；run_v2修复后实际交易全部不变，只使该控制无效。33项相关测试通过。全部6个候选/周期未过门。
- 报告：`analysis/p1_spike_v112_execution_20260919.md`；完整证据：`experiments/active/exp-spike-v112-execution-20260919-v1/delivery_manifest.json`。研究库记录已同步，状态拒绝、不可用于生产。未改Pine/监控/实盘。
- Owner最新偏好：不需要每次HTML；后续默认对话说明+必要MD。先前段落的HTML是历史产物，不代表当前要求。原TV一致性仍仅GAS得到具体解释，未完成全量认证。

## 最新：V11.2 六均线支撑过滤完整回放失败（2026-09-19）

- Owner「试试」后，只检验框内首次突破当根 `close > ropeHigh`；保持一次性消费、原退出、0.2%成本，两臂独立串行。未改 Pine、监控或实盘。
- 638 币全量完成、0 失败；9,287 笔原基线的14个成交字段及额外收益/对照字段全部复现，状态集合一致。
- 原候选98%已在六均线上方。15m全期 -0.0950→-0.0909R/笔，后段 -0.2921→-0.2921，改善区间跨0。
- 1h全期 -0.0921→-0.1090R，后段 -0.2278→-0.2359；少亏23.50R、漏赚55.01R（含1笔10.79R），不采用此硬门。
- 原始数据全部允许研究；本轮沿用旧2024-09-10至2026-05-01窗口只是控制对照，旧段落中的holdout限制已由Owner取消。
- HTML：`analysis/html/p1_spike_v112_support_20260919.html`；实验：`exp-spike-v112-support-20260919-v1`。122项相关测试通过；注册表另有4项旧缺提交号错误，本轮记录单独有效。无参数搜索，无生产晋级。

## 最新：V9 多头用突破管理持仓（加仓 / 没突破就走）+ V11.2 Pine（2026-09-19）

- 背景：「V9 单子框里后来出了突破」事后看 +0.49R（15m）/ +0.53R（1h），但等突破再进场是亏的。
- 加仓（首个突破后加一份，跟原单出场）：加的那份 15m −0.052R、1h −0.064R，整体更差。建议不用。
- 没突破就走（6 根）：后段 15m −0.234→−0.149R（区间不含 0），前段变差，全期不变；约七成单子被提前平。
- Pine `spike_burst_v11_2.pine`：默认「多头框内」规则、加仓提示、N 根没突破就走（默认关）；
  「仅显示突破+spike」现在会显示出了突破+spike 的 V9 框；新增「显示全部候选线」诊断。未在 TV 编译。
- Owner 问 15m SOL 明显的趋势线没画出：可能原因见报告对话（三点+2ATR 回落+实体不穿线+只画一条主线）；
  该例在 holdout 期，本地无法复现，已给 TV 上的诊断开关。
HTML：analysis/html/p1_spike_v112_manage_20260919.html。holdout 0，未 push。

## 最新：V11.1「多头框内突破就算」单独回测——更差，1h 明显差于 V10.4（2026-09-19）

Owner 规则：V9 多头框开着时出现突破（上级或本周期）就算突破+spike，不限根数、不检查，每框一次。
15m 每笔 −0.095R（V10.4 −0.056），1h −0.092R（V10.4 +0.088）；1h 与 V10.4 差 −0.180 [−0.325, −0.024]。
上级突破类后段最差（15m −0.42R）；离 V9 越久越差。建议不用「框内就算」，Pine 未改成此规则，等 Owner 决定。
HTML：analysis/html/p1_spike_v11_box_joint_20260918.html。holdout 0，未 push。

## 最新：SPIKE V11 回测（15m←1h、1h←4h 上级突破）——未通过，未优于 V10.4（2026-09-18）

- V11 每笔：15m −0.048R（V10.4 −0.056）、1h +0.062R（V10.4 +0.088）；后段 15m −0.254、1h −0.126。V11−V10.4 区间都跨 0。
- 新增的「突破+spike（上级突破）」占 13–14%，前段 +0.27/+0.21R，**后段最差** −0.29/−0.42R，不是最强信号。
- V10.4 与 V9 两臂逐笔复现原账本（0 差异）。holdout 0；Owner 的 HYPE 9-17 例子在 holdout 内未核对。
- Pine 显示已按 Owner 口径改为「突破+spike（上级突破）」（逻辑未变），仍未在 TV 编译。
- 待 Owner 确认：「15m 多头框还开着 + 上级突破就算」是否是另一条规则（不限 6 根）。
HTML：analysis/html/p1_spike_v11_mtf_joint_20260918.html。未 push。

## 最新：SPIKE V11 Pine（多周期突破+spike）——已写，未在 TV 编译，未回测（2026-09-18）

Owner：「15min级别可以看1h级别的趋势线突破信号……1h级别可以看4h级别突破信号；默认突破了要显示a b c；趋势线不要突破了右边就没了」。
`yoyo/evaluation/pine/spike_burst_v11.pine` 由 Owner 的 V10.4 原文逐处改出（原文件不动）：
- 上级周期（自动 15m→1h、1h→4h，可手动）在 `request.security(..., f[1], lookahead_on)` 里跑同一套找线/突破；
  上级K收盘后的第一根本图K线才看得到突破，不回填。SPIKE 只来自本图；配对窗口、第二事件当根门、一次 SPIKE 只消费一次都同 V10.4。
- 来源可选：本周期+上级 / 仅上级 / 仅本周期。突破后的线继续向右延伸（默认 48 根），A/B/C、A 点与突破点圆圈默认显示；历史联合组只给最近 8 组保留 A/B/C。
- 上级引擎里已突破的线直接离池（不保留「已破待配对」），这是与本周期引擎唯一的规则差异。
**未编译、未回测**；Owner 示例（HYPE 2026-09-17）落在 holdout 期内，本地没有也不读取。

## 最新：SPIKE V10.4 · 1h 复现与趋势线增量验证——原结果逐笔复现；联合门的改善主要是「少做」（2026-09-18）

Owner 要求只做 1h、只做多，回答「加入下降线突破+SPIKE 后，筛选收益、等待代价、持仓机会变化分别是什么，提升是否受实现或口径影响」。
三臂同数据同出场：A 原 V9 多头 / B 只要线突破（消融参照）/ C 原联合。**holdout 消耗 0；未 push。**

- **复现**：原 1h 8,041 笔、1,608 联合、8,041 随机对照，用原提交只读导出与当前 runner 重跑，14 个字段 0 差异。TV 一致性**未验证**。
- **C−A 每笔 +0.093R，月块 95% 区间 [−0.046, +0.223] 含 0**；频次只有 A 的 22.5%。
  后段 C −0.077 vs A −0.159（少亏，没转正）；后段 +410R 总差里约 350R 是「少做四分之三」，约 60R 是每笔效应；前段 C 总量少赚 246R。
- 筛选（影子，事后）：没配上的 V9 事件避开 −4,263R、也放弃 +4,061R；V9 的 60 笔 ≥10R 大单漏掉 48 笔。
  80% 联合在 V9 当根成立（零等待）；「先 SPIKE 后突破」305 笔平均等 2.1 根、入场贵 83bp。
- 数据断档差异影响 1h 事件 0 个；相等高点两口径结论相同；19.6% 联合的线当时不是屏幕主线。
- 勘误：1h 拆解报告「去掉最好 1%（15 笔）」实为 14 笔；配对超额分母为 1,473 对。
- **结论：不支持作硬过滤；最多作 V9 之上的提示标签。** 账户级（含浮动盈亏）验证未完成。

HTML：analysis/html/p1_spike_v10_4_1h_increment_20260918.html；输出 experiments/active/exp-spike-v10-4-1h-increment-20260918-v1/runs/v104-1h-incr-20260918-01/。
前向验证只给了固定配置与字段（报告 §16），**未启动**。未训练、未 promote、未改仓、未动真金。

## 最新：SPIKE V10.4「突破+spike」只做多 · 6 个周期——全部未通过，1h 最接近（2026-09-18）

Owner 贴 Pine「SPIKE V10.4 · 突破+spike」，要求「只做多 只做突破➕spike信号的 5min 15min 30min 1h 4h 1d 周期都跑一下
币种数据你都有的 不用新拉数据」。V10.4 ≠ 上面那条「V10（突破后 ≤7 根）」：它用双轨三点下降线，SPIKE 与突破两种顺序都算、间隔 ≤6 根。
联合信号不开自己的盈亏框，所以出场借用 V9（次根开盘入场、5 根低点/2ATR 止损、2R 后 4ATR 追踪、V9 空头确认平仓，0.2% 往返）。

**holdout 消耗 0**；数据 = 本地 Binance USD-M 5m 官方归档 638 个永续（止于 2026-04-30），六个周期都由同一份 5m 聚合；窗口同 V9/V10。

| 周期 | 笔数 | 全期每笔R | 配对超额 | p | 后段每笔R |
|---|---:|---:|---:|---:|---:|
| 5m | 13,165 | −0.2401 | +0.0922 | .033 | −0.2596 |
| 15m | 4,708 | −0.0560 | +0.1203 | .045 | −0.2508 |
| 30m | 2,703 | +0.1242 | +0.2241 | .053 | −0.0425 |
| **1h** | 1,476 | **+0.0878** | **+0.2436** | **.009** | **−0.0773** |
| 4h | 330 | +0.0233 | −0.1958 | .847 | −0.5549 |
| 1d | 8 | +6.4563 | −4.9755 | .883 | −1.0068 |

- 1h 是唯一稳定强于随机入场的（后段超额也 +0.17R，p=.033），但后段绝对为负；p=.009 未做 6 周期校正（Bonferroni .054）。
- 30m 的 +336R 有 +135R 来自一笔（RAVEUSDT 2026-04）；去掉最好的 1% 两个周期都变负。
- 联合门相对「所有 V9 多头」在 5m–1h 都让每笔和超额变好、笔数剩 ~20%，但没把后段转正。
- 拐点相等高点规则（TV 未公开）的事前敏感性复跑：信号 5m +24%，**所有判定不变**。
- 事后分组「1h 先突破后 SPIKE」+0.17R/超额 +0.43（p=.004）只是线索，不是结论。

HTML：analysis/html/p1_spike_v10_4_joint_multitf_20260918.html；实验：experiments/active/exp-spike-v10-4-joint-multitf-20260918-v1/；
Pine 原文：yoyo/evaluation/pine/spike_burst_v10_4_owner.pine；移植：yoyo/evaluation/spike_v10_4.py（逐行对照 Pine 循环的测试钉住）。
**建议留档不动；不建议为 V10.4 烧 holdout。** Notion 本轮未记录（本会话无 Notion 连接器）。未训练、未 promote、未改仓、未动真金。

## 最新：SPIKE V10 重做（只做多 + 突破后 ≤7 根）——未通过（2026-09-18）

Owner「v10去掉 重新做 … 支持先突破 然后最多7根k出现v9信号 我们v10才做多 不需要做空信号」。
**旧 V10（多空、≤0/12/48/200 四臂）撤回**，其报告/账本/代码原样留作历史（下一节），不再叫 V10。
新 V10 = V9 多头确认落在 Owner 趋势线向上突破的当根或其后 ≤7 根；不开空；V9 空头确认仍结束已有多单
（那是 V9 的出场，没改）。事前登记 3 臂 v9 / v9_long / v10，把「不做空」和「加突破门」拆开归因。

**holdout 消耗 0**，同一截断 + 逐笔对账已发布 V9（61,530 笔全一致）。

| 臂 | 全期每笔R | 全期超额 | p | 后段每笔R | 后段超额 | p |
|---|---:|---:|---:|---:|---:|---:|
| v9（多空） | +0.0553 | +0.0699 | .0285 | −0.0487 | +0.0033 | .445 |
| v9_long | +0.0299 | +0.0695 | .151 | −0.2102 | −0.0549 | .981 |
| **v10** | +0.0752 | +0.0957 | .154 | **−0.2448** | **−0.1204** | .987 |

- 突破门在多头里总净 R 净差 **−0.3R**（避亏 13,760R ≈ 错过 14,050R），只把笔数砍到 40%，每笔数字才好看。
- 「不做空」删掉 V9 空头 +2,475R，其中**后段 +1,654R**——后段唯一赚钱的一半。
- 后段每个突破年龄桶都 −0.16~−0.26R/笔，0–7 根反而最差；换窗口救不了。三个周期后段全负。
- 不是盲测（撤回版已按方向切过这段数据），事前计划写明「预期不通过」。

HTML：analysis/html/p1_spike_v10_long_break7_20260918.html；
实验：experiments/active/exp-spike-v10-long-break7-20260918-v1/；回放源码 f8b4fa078e。
Pine：yoyo/evaluation/pine/spike_burst_v10.pine 已整份替换为新定义（**未在 TV 上编译过**）；旧版在 5be18a4757。
**建议留档不动；不建议为 V10 烧 holdout。** Notion 本轮未记录（本会话无 Notion 连接器）。
未训练、未 promote、未改仓、未动真金。

## 最新：趋势线突破 V2 策略 · 止盈止损网格——三个周期全部未过（2026-09-18）

Owner 贴 Pine「主下降趋势线 · 关键高点 V1」，要求「做个 v2 版本 做成策略 回测一下
自己找到止盈止损的最优参数 多个级别都试试」，本人睡觉、由我自行跑完。
**这是和上面 SPIKE V10 不同的一件事**：V10 拿这条线给 V9 做入场门；这里是把这条线的突破
单独当成做多策略，只搜出场。两份移植（`trendline_break.py` / `trendline_v2_signals.py`）
由 `tests/evaluation/test_trendline_break.py` 逐 bar 钉在一起。

**holdout 消耗 0**，数据止于 2026-05-04（冻结的 `read_prefix` 逐字节拦截）。
54 个 OKX 永续，15m 为底合成 1h/4h；止损 10 档 × 止盈 9 档 = 90 格 × 3 周期 = 270 个配置。
开发段 2022-01→2025-01 选参（邻域中位数，事前写死），`selection.json` 提交后才读复查段
2025-01→2026-05。

| 周期 | 入选 | 开发段每笔 R | 复查段每笔 R | 配对超额 R | 月块 p | 判定 |
|---|---|---|---|---|---|---|
| 15m | SL6 / TP0.5 | −0.0847 | −0.0799 | −0.0048 | 0.81 | 未过 |
| 1h | SL6 / TP10 | −0.0073 | −0.1831 | −0.0328 | 0.80 | 未过 |
| 4h | SL2.5 / TP10 | +0.0368 | −0.0053 | +0.0933 | 0.21 | 未过 |

- 15m 与 1h 的开发段 **180 格没有一格为正**（R 与 bp 两个口径都是）。成本吃掉一切：
  1 ATR 止损的每笔成本 15m 0.42R、1h 0.20R、4h 0.09R。
- **搜出来的格子输给没搜的参照格**：4h 参照 SL2/TP4 复查 +0.0298R、PF1.045，入选格 −0.0053R。
  本仓第三次同形态（ETH/BTC BB×Stoch 各一次）。
- 唯一正面信息：4h 入场比匹配随机好（复查三格超额 +0.070~+0.093R、毛 R 为正），
  但量级小于成本、bp 口径全负、p>0.1。**不是换止盈止损能修的。**
- 指标的"突破"包含"线自己降到横盘价格上"，是穿越不是上涨（有测试钉住）。

HTML：analysis/html/p1_trendline_v2_tbsl_20260918.html；
实验：experiments/active/exp-trendline-v2-tbsl-20260918-v1/；
Pine 策略：yoyo/evaluation/pine/trendline_break_strategy_v2.pine（**未在 TV 上编译过**；
默认参数是参照格 SL1/TP2，不是"最优值"）；owner 原文逐字存 trendline_key_high_v1_owner.pine。
笔记：docs/learnings/net-r-grid-search-drifts-to-wide-stops-because-fees-shrink-in-r.md、
a-descending-trendline-break-is-a-crossing-not-a-breakout.md。
不申请 holdout。未训练、未 promote、未改仓、未动真金。

## 已撤回：SPIKE V10 第一版趋势线突破门——每笔更好、总量减半、后一段依然为负（2026-09-18）

Owner 给出 Pine「主下降趋势线 · 关键高点 V1」，要求「加入 spike v9 写一个 v10」并回测，
门的口径是「V9 出信号那根 **本身就是突破** 或 **突破在最近 N 根之内**」。
V10 = V9 + 这道入场门，只删入场、不改出场、不动反向退出。空头镜像成上升支撑线向下破。
TradingView 脚本 `yoyo/evaluation/pine/spike_burst_v10.pine`（**未在 TV 上编译过**，
首次粘贴若报错把错误发回）。

**holdout 消耗 0**：3,531 条流全部截断在 2026-05-04 之前，开盘 ≥ 该时刻的 K 线一根不读；
2026-05-04→09-10 这 4.2 个月仍是完整盲样本。截断被逐笔核对：每条流的 v9 臂与已发布 V9 账本
在「出场落在保留 K 线上」的交易上完全一致（3,531/3,531 通过），这道检查抓到两个真 bug。

事前登记 5 个臂（v9 / 突破当根 / ≤12 / ≤48 / ≤200 根），全部上表不挑。
**全期 2024-09-10→2026-05-04**：a12 每笔净 R +0.0553→**+0.1028**、PF 1.083→1.155、
配对超额 +0.0699→**+0.1018**（月块 p .0285→.0105）、事件回撤 4,023R→1,381R，
但准入只剩 42%，总净 R +3,400R→+2,685R。
**分段才是结论**：前段 a12 超额 +0.2049（p=.0040），**后段 2025-09→2026-05 所有臂仍为负，
且 a12/a48/a200 的配对超额由 +0.0033 转为 −0.0028/−0.0097/−0.0109**。
没有任何全期或后段格子达到 p<0.01。后段亏损全在多头（V9 −0.2102→a12 −0.2395/笔），
空头后段 a12 +0.1534/笔、超额 +0.1262、p=.0265 是最亮一格但属**事后方向切分**。
4H 每笔最高却配对超额 −0.13，是 beta 不是信号。
**未采纳、未 promote、未训练、未改仓。** 这是「前段有效后段消失」在本项目第三次出现。

反直觉附带结果：多头 47.7%~55.7% 的「突破」其实是下降线自己走到价格上，价格没涨；
**这一类每笔 +0.0822R，反而高于价格真冲上去的 +0.0399R**——「再加一道只要真突破」的直觉被推翻。

HTML：analysis/html/p1_spike_v10_trendline_gate_20260918.html；
实验：experiments/active/exp-spike-v10-trendline-gate-20260918-v1/；源码 5be18a4757。
**下一步要 Owner 点头**：①烧 holdout 做一次性验收（臂必须事前冻结成一个，我会选 a12，
但事前预期是不通过）；②什么都不做留档。

## 最新：BTC 5m 最终验收未通过，本配置已否决（2026-09-17）

Owner「拿btcusdt.p 5min 跑最终验收」。**holdout 第 2 次记录在案的读取、该配置第 1 次消耗**，
账本 docs/HOLDOUT_LEDGER.md 第 2 条在读任何字节前登记并提交（含事前预期"未通过"）。
计分 2026-05-04→2026-08-31（3.9个月），读入 holdout K 线 34,464 根，计分信号 157 个。
参评只有原参数 BB200/2.0/3% 与 Owner 已批的保本含成本；**网格候选 BB400/3.0/4%、BB300/2.5/3%
未参评**（复查段已否决，拿被否决的候选烧 holdout 是浪费）。
**结果：未通过。** 原参数 85 笔、净 −6.0619R、每笔 −0.0713R、毛 −0.3990R、PF0.712；
保本含成本 85 笔、净 −5.5797R、每笔 −0.0656R、毛 +0.0830R。
三条事前标准：①净R为正 ❌；②配对随机超额为正 ✅（+0.0298/+0.0367 R/笔）；③月块 p<0.01 ❌（0.50/0.44）。
**一条满足两条不满足 = 未通过，本配置即被否决**；后续任何 BTC 5m BB×Stoch 实验需重新取得 holdout 授权。
②持续为正与前段"BTC 有微弱入场优势"一致，但扣每边10bp后仍是负的——优势量级不够，不是成本口径问题。
HTML：analysis/html/p1_btc_bb_stoch_holdout_acceptance_20260917.html；
实验：experiments/active/exp-btc-bb-stoch-holdout-acceptance-20260917-v1/；源码 c8c72e3f5e。
新增 yoyo/data/authorized_holdout_window.py：跨边界读取必须先有编号账本条目且点名本实验；
冻结的 read_prefix 那道 `end>2026-05-01` 硬门原样保留。程序在 results.json 已存在时拒绝重跑。
5项聚焦测试通过。未训练、未promote、未改仓、未动真金。

## 最新：BTC 5m 315组参数搜索——开发段转正、复查段全崩（2026-09-17）

Owner批准的多变量搜索。开发段2024-01→2025-09从315组按事前邻域中位数规则选出
稳健BB400/倍数3.0/止损4%（131笔、每笔+0.0512R、PF1.133、较差路径净清算+25,184.86U），
诊断峰值BB300/2.5/3%（270笔、+0.0431R、+32,412.51U），原参数BB200/2.0/3%为−67,924.14U。
selection.json在读复查段价格前提交于67af64a458，复查程序核对字节一致才运行（代码强制）。
**复查段2025-09→2026-04：稳健−0.4080R/笔、PF0.275、49笔；峰值−0.2843R/笔、PF0.451、97笔；
未调参原参数−0.0666R/笔。两个入选者都输给不调参，且复查毛R分别−17.55/−21.13——不是成本问题。**
开发段315组中仅10组（3.2%）较差路径为正，且全部挤在BB300-400×倍数2.5-3.0×止损3-5%角落，
笔数从523骤降到131。这是ETH那轮之后**第二次**同形态崩塌。
HTML：analysis/html/p1_btc_bb_stoch_optimization_20260916.html；
实验：experiments/active/exp-btc-bb-stoch-optimization-20260916-v1/。
71MB逐组账本与稠密曲线仅本地保留，哈希在local_artifacts.json。24项聚焦测试通过。
开发段按事前约定不抽随机对照（不参与排序），表中如实写"未抽对照"而非占位p值。
**holdout消耗0**；这28个月此前已被整体看过，复查段是已观察数据内的时间留出，不是盲验。
bb_stoch_optimization新增--exp与grid_controls_per_trade，默认值保持ETH那轮逐字节不变。
未训练、未promote、未改仓、无实盘资格；开发段正收益不是可交易结论。

**四轮累计结论：ETH 5m毛为负；BTC 5m/1m毛微正但f*仅+0.5bp（OKX最低maker 2bp，拿不到）；
XAU 1m约零；四个入场门全部无信息；参数搜索两次复查崩塌。建议换入场形态，不要再调这条规则。**

## 最新：换市场换周期——BTC 毛微正、ETH 毛为负，净额全亏（2026-09-16）

Owner要求测BTC 5m/1m与XAU 1m，并要求同时进行。规则一字未改（BB200/Stoch5·3·3/3%止损/每边10bp
全是ETH值），唯一变量是市场与周期，**未为新市场调参**。数据全部OKX官方月度档案：
BTC 5m 245,088根、BTC 1m 1,225,440根（均2023-12-31→2026-04-30），XAU 1m 151,674根
（OKX的XAU-USDT-SWAP 2026-01-15才上市，仅3.5个月；XAUT-USDT-SWAP无档案数据）。holdout消耗0。
三组×三市场全部净亏：参照每笔BTC5m −0.0625R/702笔、BTC1m −0.0638R/3192笔、XAU1m −0.0722R/405笔。
**但毛收益符号不同**：ETH 5m毛−21.16R（免费也亏），**BTC 5m毛+4.98R/697笔、BTC 1m毛+11.74R/3159笔**
（优势微正、被费用吃光），XAU约0。BTC最好一组f*=+0.51bp/边，而OKX最低maker 2bp，**拿不到**。
BTC 5m原版配对随机超额+0.0384R/笔 p=0.042——按本项目p<0.01门槛不显著，且属3×3多重比较。
RSI门在三个市场一致为负贡献（BTC5m毛+4.98→−1.76）。
HTML：analysis/html/p1_btc_xau_bb_stoch_timeframes_20260916.html；实验：
experiments/active/exp-btc-xau-bb-stoch-timeframes-20260916-v1/（50文件manifest）；源码97ad1ba3a8。
37项聚焦测试通过，全库468/7既有失败。引擎改动：1m序列必须自带_data_gap列，
match_context/enrich新增minutes参数（默认5，既有调用逐字节不变）；archive与prefix reader接受1m。

## 进行中：BTC 5m 315组参数搜索（2026-09-16）

Owner明确批准的多变量搜索。experiments/active/exp-btc-bb-stoch-optimization-20260916-v1/，
复用bb_stoch_optimization（新增--exp指向任意实验目录、开发段可关闭随机对照）。
开发段2024-01→2025-09，复查段2025-09→2026-04，**选完提交selection.json后才准读复查段**；
选稳健邻域中位数而非峰值。约37秒/组，开发段≈3小时。config冻结于b71a36efad。
**诚实边界：这28个月今天已被整体看过，复查段是已观察数据内的时间留出，不是新鲜盲验；
先验很差（f*仅+0.51bp），要翻盘需把毛优势提高一个数量级。**

## 最新：BB×Stoch 28个月长周期诊断——七组全负，规则本身是负期望（2026-09-16）

Owner连批三件事：保本价含0.2%往返成本（**只批这一条**，"止盈不许挂在亏损侧"未批准）、
"三条方向都试"、窗口拉长。数据往前接OKX官方月度1m档案聚合5m：245,088根、
2023-12-31→2026-04-30、28个月、**holdout消耗0**；与既有CSV在37,800根重叠上OHLC逐值一致。
2024-01→2025-12这23个月此前未被本策略族用过，**本轮看过即已花掉**，故全程零参数搜索。
七个事前冻结组（v2原版／保本含成本参照／RSI线门／◈同根／◈20根窗／SAR方向／取消平半）
**全部亏，且全部在扣费之前就亏**：参照652笔净−64.85R、每笔−0.0995R、PF0.78、毛−21.37R；
原v2 650笔−64.51R。**盈亏平衡费率f*=−7.87bp/边（全组−7.7至−22.7bp）＝免手续费也亏**，
成本不是病因。四个门全部让每笔更差（−0.1176/−0.2521/−0.1219/−0.1485），
事件级1085笔标签置换p=0.49/0.74/0.65/0.56，无一携带信息。短窗那点"RSI门略好"在长窗反号。
保本含成本≈中性（−64.51→−64.85R），其副作用"平半时新保护价已被越过"46笔−7.45R已单列。
HTML：analysis/html/p1_eth_bb_stoch_longrun_20260916.html；实验：
experiments/active/exp-eth-bb-stoch-longrun-diagnosis-20260916-v1/（52文件manifest）；源码7bbf45b073。
43项聚焦测试通过，全库468通过/7既有失败（未绕过）。引擎新增partial_fraction/be_cost_fraction，
默认值与冻结v2逐字段一致（parity测试在）。未训练、未promote、未改仓、无实盘资格。
**结论建议：这条线的问题在入场规则，继续加过滤或压成本都没有出路。**

## 最新：holdout首次记账读取（2026-09-16）

`docs/HOLDOUT_LEDGER.md` 建账，第1条=Owner授权的单根诊断：2026-08-19 19:15那一单为何开仓。
答案：信号根是19:10（北京）不是19:15，且是**空单**——RSI14=70.25已超买，v3门放行；
当天真正会被拦掉的是08-20 01:55那单（RSI65.30）。范围限2026-08单月、不计收益、
数据未进data/、冻结读取器的`end>2026-05-01`硬门未放宽。产物：
experiments/active/exp-bb-stoch-bar-diagnosis-20260916-v1/。

## 最新：Pine v3（BB×Stoch＋Parabolic RSI）已写好但未存进TradingView（2026-09-16）

yoyo/evaluation/pine/eth_bb_stoch_strategy_v3.pine：仅比v2多RSI区域门与显示，
退出仍用未过滤信号，带useRSI开关。**未保存到TV**——本机无已登录TV会话
（Chrome扩展未连接、内置浏览器无cookie）。Owner图上RSI实参仍未核实。

## 最新：BB×Stoch 加 Parabolic RSI 区域过滤，未证明改善（2026-09-16）

Owner要求在已保存的「ETH BB × Stoch · 策略 v2」上加 ChartPrime Parabolic RSI：超卖才做多、
超买才做空；先写Pine保存TV再回测。Pine v3已写好（yoyo/evaluation/pine/eth_bb_stoch_strategy_v3.pine，
仅比v2多RSI门与显示，退出仍用未过滤信号，带useRSI开关），**尚未保存到TradingView**——
本机无已登录TV会话（Chrome扩展未连接、内置浏览器无cookie），需Owner登录或连扩展后再保存与编译。
Owner图上RSI实参未核实；本轮按公开默认14/30/70。
回测沿用v2冻结前缀（OKX ETH-USDT-SWAP 5m，读到2026-05-01之前，holdout消耗0），三组事前冻结：
unfiltered逐笔复现上一轮账本；rsi_entry（主）58笔净−8.4118R、PF0.7277、胜率43.10%；
rsi_both（敏感性）−8.8614R。原版75笔净−11.0176R。**总亏少2.61R但每笔几乎没变**
（−0.1469→−0.1450R），最大连亏6→12笔变差；配对随机超额+0.0082R/笔、p0.4688。
事件级独立回放：放行73笔−0.1113R/笔 vs 拦截37笔−0.2116R/笔，9999次标签置换p0.313，两组都亏。
候选111只拦37，做多候选RSI中位28.42、做空75.37，指标与原信号高度共线。
HTML：analysis/html/p1_eth_bb_stoch_rsi_filter_20260916.html；源：analysis/p1_eth_bb_stoch_rsi_filter_20260916.md。
实验：experiments/active/exp-eth-bb-stoch-rsi-filter-20260916-v1/（56文件manifest）；源码3a44171f93。
24项聚焦测试通过，全库468通过/7既有失败（未绕过）。两条成交路径逐笔相同，敏感性无分辨力。
未训练、未promote、未改仓、无实盘资格。Notion本轮未记录（无可用登录会话）。

## 最新：Owner澄清为人工开单系统，手册v2已交付（2026-09-16）

Owner要本人看图、人工判断和下单；均线密集为主练形态，YOLO辅助识别，V9收盘确认，BB反转另卡。
HTML：analysis/html/p1_owner_manual_trading_system_20260916.html；开单卡：analysis/html/p1_owner_manual_order_card_20260916.html。
覆盖盘前、画结构、信号有效期、SL/仓位、部分成交、真实保护、趋势管理、值守/预定离屏及复盘。
默认1H和风险0.25%等是提案，用户本金/盯盘时间尚待补充；没有新增盈利证据或账户操作。
人工版采用可见V9反向确认与独立ATR14/RMA读数，明确不能继承原V6反向退出回测。
源码4257eb4c51；来源/算例/链接/静态HTML结构核查通过，未浏览器像素QA；市场样本和holdout0。
新版本Notion：https://app.notion.com/p/3dd8856479af81fc8db1e26ee985359e，Inbox/想法，关联旧版。
实验目录experiments/active/exp-owner-manual-trading-system-20260916-v2/绑定手册与源证据。旧账本报告保留。

## 最新：个人欧易账本复盘与指标系统重组（2026-09-16）

Owner要求结合近期V9、V1、Stoch、BB反转分析旧交易并重整系统。附件4899条，
4898 USDT重构净−14828.48U，另1条BTC净+0.0004480031BTC分列。最大BTC赢家
+29002.99U，其余−43831.47U；177条强平净−9983.44U。缺权益/现金流/初始SL，
不能算账户回报或历史R，不把持仓时长、做空分组直接当因果优势。
复用已有报告重组：原V9趋势A、最新BB×Stoch v2反转B、待实现V1四周期门禁、统一账户预算。
A/B均无新增实盘资格；下一项经济研究只变V1门禁，未新回测、未新读市场holdout。
个人2026记录为附件明确授权。本轮未训练/promote/改仓。原始V9与YOLO追加通知去重，
Pine收盘参考和Python下一开盘成交时序区分，V7/V8影子不冒称V9。
HTML：analysis/html/p1_owner_okx_history_20260916.html；源：analysis/p1_owner_okx_history_20260916.md。
实验目录：experiments/active/exp-owner-okx-history-20260916-v1/，含规则、日志CSV、
3图SQL、artifact.json、核验notebook和delivery_manifest。逐条私人数据留本地data；未推远程。
统计源68dd0775e9，报告源ebe08f3230；独立CSV/Decimal核心金额复核、只读指标映射完成。
HTML规范/结构校验通过；无兼容headless-shell，交互图表/来源菜单及窄屏未验收，保留语义数据表。
Notion审计：https://app.notion.com/p/3dd8856479af81eb9f0ac76bd5aaf1e6；
系统Inbox：https://app.notion.com/p/3dd8856479af81deb0ffe4ad7d8ae08c；不是已验证或实盘。

## 最新：MA/Stoch退出v2找到相对改善，后段仍亏（2026-09-15）

Owner继续找更好止盈止损；固定原入场、1x权益名义、20bp成本。第二轮31组含3旧锚点，
从28新组开发预选wide_tp2_no_arrow：6倍5m入场ATR止损、2R止盈、取消反向箭头退出。
1—2月35平＋1末仓，1000→1078.81U（+7.88%），原规则−11.25%；最差单笔−10.22%，
最大回撤29.24%（原26.82%），最长持仓11.43天。选择6aecb66342提交后才跑3—4月复查。
复查60平＋1末仓，1000→739.02U（−26.10%），原规则−34.91%；回撤28.89% vs37.12%，
最差单笔−3.67%，最多9连止损。笔数156→60，每笔净均值反而−26.21→−45.86bp。
随机配对净超额开发−26.90/复查−22.93bp，p0.6370/0.7811；只承认相对改善，拒绝稳定盈利主张。
两段均已在v1观察，全轮探索，后段不是新验证；holdout0，近月未重测。130不同相关测试通过，
主代理5690笔平仓/11512条成交/5687对控制核验通过。元数据搜索未找到更早同源5m，
旧cache软链已断；未换Binance。辅助角色运行模型未可核验，不声称独立轻量模型审计。
HTML：analysis/html/p1_ma_stoch_exit_optimization_v2_20260915.html；4表1图/本地链接已检，
权益图已目视，无HTML浏览器像素QA。实验：experiments/active/exp-ma-stoch-exit-optimization-20260915-v2/；
191文件manifest含34本地稠密曲线；源码c139acc181。Notion：
https://app.notion.com/p/3dc8856479af81be8b5ede00792232aa；状态rejected，不接生产。

## 最新：MA Shift × Stoch退出优化已完成，未找到盈利改善（2026-09-15）

保持15m已收盘颜色＋5m原Stoch箭头、1x权益名义、20bp成本。1—2月16组开发全部亏，
baseline净−11.25%最少亏，选择bcbd8fdd3c提交后才评分3—4月验证；仅原规则和预设2ATR锚点。
验证baseline156平＋1末仓，1000→650.89U（−34.91%），PF0.559，回撤37.12%；stop_2
191平＋1末仓，636.56U（−36.34%），PF0.457，回撤36.46%。最差单笔净亏6.59%→1.30%，
连亏9→12；风险尾部缩小不等于收益改善。随机净超额−7.37/−8.19bp，p0.7375/0.9061。
原近月−17.04%结论仍保留；本轮不读近期价格调参，timestamp-first仅pre-May，holdout0。
120相关测试通过；主代理核对3601笔平仓、7288条成交和3599对控制通过，无独立代理复核声明。
全库上一轮468通过/7既有失败未重跑，未绕过。HTML4表1图及链接已检、权益图已目视，未做HTML浏览器像素QA。
HTML：analysis/html/p1_ma_stoch_exit_optimization_20260915.html；实验：
experiments/active/exp-ma-stoch-exit-optimization-20260915-v1/；源引擎d949e02946。
110文件manifest含18份仅本地保存、git忽略的稠密曲线；逐笔、控制和摘要入库。
Notion：https://app.notion.com/p/3dc8856479af81078409ce4bbebaa155；状态rejected，不接生产。

## 最新：ETH近一月 MA Shift颜色 × Stoch反向退出已完成（2026-09-15）

Owner当前TV为OKX ETHUSDT.P；15m SMA40/hl2染色限制5m翻身Stoch5/3/3超买超卖箭头准入。
北京时间8/15 00:00—9/15 18:15，收盘确认下一开盘、单仓、反向箭头全平、成本0.2%固定。
组合116平仓+1末仓，1000U按1x权益名义复投剩829.64U（−17.04%），净胜率37.07%，PF0.585、
逐5m收盘MTM回撤17.72%；单Stoch剩698.88U（−30.11%）。每笔毛4.62bp低于20bp成本，
匹配随机115对超额−10.11bp、日块单侧p0.8396，不支持该月盈利主张。分批规则尚未指定，未择优。
本精确配置holdout第1次、已暴露历史；9867完整5m含720预热，缺根0。18定向测试及306笔独立
账本核对通过；全库468通过/7既有失败（TOTAL2资产缺source_commit×5、迁移哈希×2），未绕过。
HTML结构及权益图已检，内置浏览器拒绝file地址，未做HTML浏览器像素验收。无生产变更。
HTML：analysis/html/p1_ma_shift_stoch_eth_month_20260915.html；逐笔、控制与27文件manifest：
experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/；源码6864e83489。
Notion：https://app.notion.com/p/3dc8856479af81828e6cc91ec50f88d0；状态rejected，交付完成。


## 最新：前端与 Bark 已切换 V9，旧信号已清空（2026-09-15）

Owner 明确“前端信号，和 bark 信号通知改成 v9 的”“之前的信号全部删掉”。
本机 v1.15.0 / f8e240e2af 已启用 V9 完整多空准入，沿用 15m/30m/1H/4H、
两阶段 Bark 与原 YOLO 权重；Telegram 关闭。切点为北京时间 2026-09-15 14:56:44.580。
已删除主监控库 6,603 个旧事件（6,398 原多头、195 原空头、10 YOLO）、34 候选、
42 Bark 记录及展示缓存，保留 1,916 个纯行情预热缓存；旧事件与队列实查均为 0。
扫描和入库双重门拒绝旧协议/回放/切点前原始信号，首次重置幂等；独占锁与私有备份已核验。

前端已实看 SPIKE V9、Bark 已启用和信号 0；卡片仅确认收盘风险参考，暂不计算 V9 持仓路径 R。
启动源码 SHA 与运行文件全部一致；验收扫描 565/1,916，错误 0，仍在首次预热，不声称全市场已完成。
73 项后端、19 项前端检查通过；全库守门 468 passed/7 failed，失败为已有 artifact 缺 source_commit（5）
及 candidates/render 迁移哈希漂移（2），未绕过。未发测试通知，不代表手机实收已验证。

运行命令 `.venv/bin/python -m yoyo.monitor.v9_migration --apply`（停止服务后首次执行）；
证据 `output/qa/spike_v9_switch_20260915/`，实际备份与回执在 ImpulseMonitor 运行目录。
另修复失效 Xcode Python 链接，改用现有 CommandLineTools Python 3.9.6，未改包版本。
仅监控信号切换；独立 V7/V8 影子研究、forward_log、ACTIVE、订单和研究资格未变。


## 最新：ETH V9 全周期开仓时 YOLO 检测已完成（2026-09-15）

北京时间7/15 00:00—9/15 12:00，OKX ETH-USDT-SWAP；3m/5m/15m/30m/1H/4H/UTC日线及各自小级别。当前close1280权重、原V9退出与0.2%成本固定。205准入信号、162实际开仓；同级检出6、小级别10、均检出2。1H三笔只有亏损−0.76R的单检出，+18.16R赢家未检出；完整1H从+16.19R降至−0.76R。14项Holm无显著，稀疏样本不支持直接硬过滤，不能据此反向选择未检出。
本精确配置holdout第1次；旧V9/权重消费保留，非盲OOS。814输入数组SHA与开仓时钟、646份RGB预览、全部逐笔固定退出核验通过；27专项测试通过，旧监控fixture20失败如实记录。无训练、阈值修改、monitor/ACTIVE或真金变更。
HTML：analysis/html/p1_spike_eth_v9_yolo_entry_20260915.html；逐笔图册：experiments/active/exp-spike-eth-v9-yolo-entry-20260915-v1/results/gallery.html；manifest：experiments/active/exp-spike-eth-v9-yolo-entry-20260915-v1/delivery_manifest.json；Notion：https://app.notion.com/p/3dc8856479af819b9b56d592c23a0105。状态inconclusive（关系样本不足），交付complete，production_eligible=false。

## 最新：V9 三条币种成绩排除，跨期检查全部恶化（2026-09-15）

Owner指定胜率≤10%、平均净R<−0.36、PF(R)<0.5，分别对已有全量30m/1H/4H账本做统计。V9事后排除26/118/107币，净+2758.17R→+2908.45/+4637.89/+4391.25R；前一年定名单后，后一年却由−2089.19R→−2228.28/−2314.03/−2254.94R，错过9/39/42笔原净10R。ETH早期会被后两门排除，后期净+116.23R，说明不能用同窗输家名单冒充前瞻效果。
仅已暴露结果统计，新OHLC0/新回放0；每精确条件/模式第1次账本消费，原回放记录保留。8合成检查、原6组基线、32组代数与控制分母核验，Luna Max独立六组名单/数值及ETH反例复核通过；未实施名单，V9/TV/监控均未改。
HTML：analysis/html/p1_spike_v9_asset_exclusions_20260915.html；全部名单/逐币指标：experiments/active/exp-spike-v9-asset-exclusions-20260915-v1/results/readout_v1/；源码0ec3a7de5e；Notion：https://app.notion.com/p/3dc8856479af81989d09c1919b1bb238。状态rejected指跨期策略证据失败，统计交付complete，production_eligible=false。

## 最新：ETH V9 3m/5m 补跑完成，扣费后仍亏损（2026-09-15）

Owner要求“3min 5min 也跑一下”。原低周期完整历史及分段：OKX3m V8−935.30R→V9−773.89R、PF0.4966；Binance5m−624.48R→−476.81R、PF0.6910。前后段都负，净改善90.60%/95.68%来自成本R减少；原净10R保留11/11和15/16。拒绝这两组样本的盈利主张，不把不同交易所/日期的差异归因为周期。
原3525笔V8平仓与候选逐笔一致，全部6600笔V8/V9平仓与控制引擎一致；45合成+116项目边界/因果/数值检查通过，两位Luna Max只读复核。3m含已暴露后段；低周期专项第1次holdout、不变V9历史累计第2次。未调参、未再改TV、未动监控或实盘。
HTML：analysis/html/p1_spike_v9_eth_lowtf_20260915.html；证据：experiments/active/exp-spike-v9-eth-lowtf-20260915-v1/；源码37215d29c0；Notion：https://app.notion.com/p/3dc8856479af8154b70dea07b6952ff4。完整结果、过滤身份、随机对照与36文件manifest已归档，production_eligible=false。

## 最新：SPIKE V9 已保存 TV，原全量对比完成（2026-09-15）

Owner后续明确要求子代理、TV保存与全量对照，覆盖前一实现阶段不做对照的范围。3531原冻结流完整串行回放，V8逐笔parity通过；V8净−2021.78R→V9+2758.17R、PF0.970→1.050，但后一年仍−2089.19R。原532净10R保留458、丢74、新4；改善69.12%来自成本R减少。后一年匹配随机入场超额p=0.1924，仍为研究不确定。
V9私有脚本「SPIKE V9 · 标的量能时段过滤」第1版已保存；未加图表/提醒，未切换监控或实盘，不声称TV成交parity。49针对性测试、完整回执/源集合核验及两位Luna Max子代理复核完成；HTML6表、8本地链接和窄屏预览通过。此次V9配置holdout消耗第1次，重用暴露历史，非盲OOS。
交付：analysis/html/p1_spike_v9_full_backtest_20260915.html；逐笔及来源：experiments/active/exp-spike-v9-full-backtest-20260915-v1/；Notion：https://app.notion.com/p/3db8856479af819c8be7f793b2e35002。回放源码c26b0ebfa6、统计7f9da6fd21，配置未改；production_eligible=false。

## 最新：SPIKE V9 三条过滤已实现（2026-09-15）

Owner 明确“不需要对照啊 直接做”“升级为v9”，故合并底层USDC、确认RV>50、UTC周日计划开仓三条排除；继承V8结构与退出，增加确认价风险/成本R说明。134项合成与边界检查通过，源码ef4de009d0；未读真实行情、holdout0次，未运行新收益对照。
交付：analysis/html/p1_spike_v9_implementation_20260915.html；Pine：yoyo/evaluation/pine/spike_burst_v9.pine；Python：yoyo/evaluation/spike_v9.py。TradingView尚未编译/加载，旧V7/V8 shadow未改名或切换，版本实现不代表盈利或实盘验收。Notion新版本：https://app.notion.com/p/3db8856479af81e1b257dbd9ae09aca3。

## 最新：ETH3m ICT开仓时段研究已完成（2026-09-14）

按owner当前图表，夏令时北京时间London14–17、午间17–20、纽约20–23；IANA纽约冬季同钟面为未原生验证模型。六组时段、四种既有退出，仅用2023-08至2026-04；本配置holdout消耗0次。
开发期预选New York，后期不重选；三段合并与候选均未得到稳定正净收益。纽约盘中两路径完整335/334笔最长连续净亏均5；固定1U账户1000→848.58/851.64U，亏后翻倍486.45/422.45U，2025-03-07因下一档512U价格风险超过现金停止。保本能打断连亏，不能清除上一轮亏损。
13时段检查、96自然序列、192现金账本、72匹配组及744旧V8基线逐笔对齐通过；HTML11表与本地链接检查，未做浏览器视觉验收。Pine图18笔与Python20笔仍未逐项映射，不宣称图表成交parity。
交付：analysis/html/p1_spike_eth3m_ict_sessions_20260914.html；证据：experiments/active/exp-spike-eth3m-ict-sessions-20260914-v1/results/；Notion：https://app.notion.com/p/3db8856479af8122afc9fd6a57768240。拒绝本冻结配置用于倍投，不promote、不动实盘。研究冻结69c9c7636b；原receipt记录的是结束时HEAD，实际参数见validation_receipt。

## 最新：ETH3m 9月逐笔核对已交付（2026-09-14）

Owner要求北京时间9月1日至9月14日21:19全部交易，确认OKX。固定原始V8为25信号、20实际交易（19已平仓＋1截止持仓），无carry-in、无资金容量过滤；费用保本净1R三时序各22笔，单独列出，未优化。
源补齐至21:18完整收盘，22124根含1重叠一致、无尾部缺口；每配置第1次专项holdout消耗，读取前回执已落地。86交易价格/R核对、原始逐bar止损及独立回放一致，112相关测试通过；HTML86图最小DOM检查通过，未做浏览器像素验收。
交付：analysis/html/p1_spike_eth3m_september_trade_audit_20260914.html；中文CSV：experiments/active/exp-spike-eth3m-september-trade-audit-20260914-v1/results/original_trades_zh.csv；Notion：https://app.notion.com/p/3db8856479af81a8b169e8c429ab98b6。等待owner按序号拆解；不据此接受策略、不改实盘。

## 最新：ETH3m净1R＋费用保本验证已完成（2026-09-14）

- Owner要求验证“保本提高0.2%”。冻结净1R全平、成本BE、一tick后触发、亏后翻倍、累计净回本才复位；无六层认亏重启。nextbar/O-H-L-C/O-L-H-C时序及固定/翻倍容量对照共40账户均未盈利；本配置holdout0次。
- 新规则没有9次连续完整初始止损，全部已成交记录最高7。连续10倍容量两条盘中路径中最长完整止损仅2，但保本没有消除旧债，未回收净亏累计9次后下一风险512U无法承担，余额463.25/399.23U。不是实际tick证明或交易所强平模拟。
- 64U风险那单已兑现净64.0024U，但此前净债务115.2252U，故仍欠51.2228U。nextbar组580.78U并非持续交易安全：仅成交11笔、拒单1797次，最后实际开仓2025-03-04。20bp仍为名义固定研究成本，1U仍为价格风险，非含费亏损上限。
- 132项定向检查通过，独立2769机会与6168账本核对通过；以前全库旧注册/迁移哈希失败保留。报告：analysis/html/p1_spike_eth3m_net_recovery_20260914.html；证据：experiments/active/exp-spike-eth3m-net-recovery-20260914-v3/results/；Notion：<https://app.notion.com/p/3db8856479af81eba17ac7308df03aeb>。非生产，不promote。

## 最新：ETH3m分批止盈与跟踪止损研究已拒绝（2026-09-14）

- 13组预登记退出、4组冻结参考、89个窗口×资金账户均净亏；1000U固定1U连续2023-08至2026-04，全仓3R期末131.80U、1R平50%并保护残仓76.10U、平70%71.85U。16个连续加码账户只剩4.17—12.52U。
- 分批确实提高部分净胜率，但缩小大赢家收益且改变单仓准入笔数。0.5R平半开发完整初始止损最长6，净亏最长仍36，不能承诺未来最多6次。原V8无固定3R，表内全仓基线明确添加3R目标。
- 127项定向测试通过；独立核对15543行份额成交和37753行资金，均一致。成本固定20bp、未用holdout、非生产；完整门测试收据见validation.json。
- HTML：analysis/html/p1_spike_eth3m_partial_tp_20260914.html；实验：experiments/active/exp-spike-eth3m-partial-tp-20260914-v1；Notion：<https://app.notion.com/p/3db8856479af81e19fead530a0bec2c7>。再研究需先验证扣费入场优势或整轮净债务假设，不能用已看的holdout继续调参。

## 最新：ETH3m首笔1U回本倍投v2已拒绝（2026-09-14）

- Owner明确1000U、首笔价格止损风险1U并授权优化退出/资金。855开发组全部亏损且最长真实连亏均>6（最短9）；不以六层重置伪造市场连亏上限。
- 加码开发第一：上一笔×1.5、0.5R触及后下一bar移开仓价、2R毛止盈、价格保本或净赢重置，最多6层。开发249.48U；冻结后2025年635.86U、2026前4月822.85U；2023-08至2026-04连续1000U只剩10.31U。588笔开仓价退出全为净亏。
- 原始V8准入/初始止损/20bp成本不变；拒单不占影子仓位；744笔原开发基线10字段一致，36合成测试和36账户核验通过。守门114通过4个既有注册字段缺失失败，未绕过。
- 本配置holdout消耗0次，不继续用holdout找参数；拒绝、未启用、非生产。HTML：`analysis/html/p1_spike_eth3m_recovery_20260914.html`；Notion：<https://app.notion.com/p/3db8856479af81abaee7f9ce7a8b6cb6>。以后若研究动态整轮回本保护或净费用预算，另立假设，不能承诺最长连亏6次。


> 文档地图：`docs/DOC_MAP.md` · 本周计划：`analysis/week_plan_20260720.md` · 纪律：`CLAUDE.md`

## Owner纠正：先看V8 ETH原始交易统计（2026-09-14）

- 已回到完整冻结自然平仓账本，未加入倍投/资金拒单：3m OKX1571笔，净胜率24.89%；5m Binance1954笔，26.00%。最长连续净亏27/25次；连续净亏止损13/15次；连续初始止损12/13次。
- 记录MFE≥3R为328/420笔，实际净赚≥3R为75/119笔。原V8无固定3R止盈，收盘2R启用4ATR跟踪；MFE忽略退出那根的新极值，不能称固定3R止盈胜率。原分段边界中断连续次数。
- 仅重用已暴露的SHA固定账本，无OHLCV/新策略回放。HTML：`analysis/html/p1_spike_v8_eth_baseline_stats_20260914.html`；全部连续段/R分布：`experiments/active/exp-spike-v8-eth-baseline-stats-20260914-v1/results/`。4项单测和独立连续段核验通过。

## ETH V8 止损后倍投研究已收尾（2026-09-14）

- Owner授权1000U、可全损的离线研究及冻结后一次holdout。40次开发比较（34个周期×参数组合、25组参数）全部亏损；开发冠军均只成交2笔，其余大多因资金约束拒单。
- 冻结后2025验证3m752.72U、5m833.91U；连续账户3m724.82U、5m792.28U。3m最终holdout876.96U，固定风险974.92U。5m无holdout源；其2026年前4个月盈利窗未执行翻倍，不能证明倍投有效。
- 本版rejected，不启用；不是交易所强平模拟。13项专项测试通过，18行账户与逐笔对账；全套113通过、4项因其他注册记录缺source_commit失败，原日志保留。报告：`analysis/html/p1_spike_eth_martingale_20260914.html`，实验：`experiments/active/exp-spike-eth-martingale-20260914-v1/`。
- 选择f4aa4530bc先于最终评估，holdout第1次收据保留。继续新资金规则需另立版本，不能在已评估holdout上追调。Notion：<https://app.notion.com/p/3db8856479af81bf9c94f5e9f483e621>。

## SPIKE V7／V8 五条降噪路线已执行（2026-09-13）

- Owner 要求的市场状态、跨所确认／事件折叠、早期失败退出、组合容量／相关性风险和前向影子均已落地并逐项裁决。历史覆盖 Binance／OKX／Gate 3,531 条 30m／1H／4H 数据流；复用验证历史已非盲，四个新历史配置各记录授权 holdout-era 使用 #1。
- 可保留的是事件语义：同资产×周期×方向在一根周期内折叠，V7／V8 分别减少 24.55%／23.76% 跨所重复确认；它用于通知去重与风险计数，不代表等待 follower 后入场更赚钱。
- 拒绝上线的硬门：`joint_delta_60m > 0` 大量误删 10R；2／3 根早退均损伤净R或尾部；最多五仓几乎清空候选且 PF<1；30日相关性 `>=0.80` 在 V7／V8、开发／复用验证间不稳定。线上入场、退出、Bark、YOLO 与订单路径均未改变。
- 独立 `spike-v7-v8-forward-shadow-v2` 已从北京时间 2026-09-13 17:07:26.988 启用，只记录此后已收盘的 OKX 30m／1H／4H。首次 1,437 单元 0 error、0 历史事件、0 启用前快照；前端“前向影子”展示 V7/V8、市场标签及 TradingView 整卡入口，不推送、不下单。
- 聚焦验证 70 passed／4 warnings，Python 编译和前端 JavaScript 检查通过；相关性 metadata 与六项输出均已哈希绑定并有篡改 fail-closed 测试。交付报告：`analysis/html/p1_spike_noise_reduction_execution_20260913.html`；源码提交 `6b32aa210030aa7f83c22aedf25f53a026a3138e`。
- 下一步只积累前向盲样本，按事件 ID 分层市场状态与跨所确认；样本成熟前不得继续在相同历史上微调 3ATR、广度或相关阈值。

## 独立Donchian/EWMAC山寨强势研究完成（2026-09-10）

- 固定54来源/52山寨分账户，49币截止前够256日预热；开发2023–24、验证2025，均是已被研究过的历史，不是全新盲测。holdout消耗0次。
- 三政策全部rejected：D开发+19.20%、验证-2.87%；E+14.75%/-6.15%；D_E+10.12%/-3.71%。匹配随机D+23.55%/+3.01%。D强势时段贡献+10.37pp/-2.22pp；不能借上涨片段宣布选时优势。
- 8082e988先提交builder再跑一次；167检查、38120事件/24105实际接受的模拟交易、25564匹配及资金/统计独立复核通过。固定0.2%入场名义往返成本未含完整funding；没有新训练、实盘或部署。
- 交付：`analysis/html/p1_altseason_donchian_ewmac_20260910.html`；实验`experiments/active/exp-altseason-donchian-ewmac-20260910-v1`。完整大CSV本地保留并逐项注册哈希；报告排版修订没有重跑策略。下一步若研究新规则需另行预注册；不得将本配置改写成已验证盈利系统。

## Spike 短周期仅前端、Bark 只保留长周期（2026-09-09 22:00:54）

Owner 连续明确要求：5m 前端显示，5m/15m/30m 不通知 Bark。v1.12.0 / dd81924 已部署，
当前六周期5m/15m/30m/1H/4H/1Dutc都扫描并记录指标启动/YOLO确认；Bark仅1H/4H/日线两阶段，
Telegram继续全部关闭。独立BARK_TIMEFRAMES不能替换DIRECT_TIMEFRAMES，后者控制API展示。
原始入队/模型入队/发送出口共用禁推校验；启动仅清退短周期pending，sending先变unknown，
保留终态回执、模型工作和全部启用时间。过去10个disabled5m候选不复活。
实机重启后短周期pending/sending及新sent均0；266份TG、384份旧Bark终态、22项启用元数据逐条保持，
运行源码SHA完全一致。旧Bark unknown=1仍保留，不把它冒充本次发送失败或已送达。
前端已实点5m/30m筛选、30m两个阶段静音说明、5m观察卡片及0G5m真实120根K线预览。
Windows3060同步5m白名单并重启显示客户端，网关读取相同新规则；BTC5m保存布局URL及文件SHA已核，
本轮没有实际打开WindowsTV图表或发送测试通知。1H/4H/日线信号规则/权重/新鲜度/订单路径不变。
1021项监控/边界测试、66项前端/主题测试通过，源代码已推main。证据output/qa/spike_short_period_display_20260909/。
验收时475×6=2850窗口正在重新加载，进度持续增加、错误0，未声称全市场首轮已完成。
8行情流冷启动完整分页预算约24分钟，结合completed/errors判断，不要重复重启。

## Spike 3060 Windows 入口已接通（2026-09-09 21:05）

Owner要求3060也能访问前端并打开3060自己的TV。已在WIN-ZZC/Administrator部署
`C:\fable\spike-client`，桌面Spike快捷方式指向本机`http://127.0.0.1:8766/#signals`。
Mac仍是扫描/Bark唯一进程，未重启监控（started_at_ms=1788954302965）；Windows无模型或推送。
Mac独立LaunchAgent `com.spike.3060-tunnel` 将Mac8766经SSH反向转发至Windows loopback8767；
Windows登录任务`SpikeDesktopClient`在Session2监听loopback8766，白名单GET读Mac，开图POST本机处理。
Windows真实API→TV正文已验BTC30m、ETH4H、SOL日线，具体布局综合过滤/AlGc61US，TV3.4.1.8194。
WindowsGUI子进程曾把Initializing LoggerService写入回执，已用ShellExecute隔离标准流；不放宽requested校验。
283项Python定向、56项前端回归通过；客户端安装器等待自身ready，登录启动，隧道KeepAlive。
3060当前IP是192.168.1.2（旧.3拒绝，已核对主机密钥和WIN-ZZC）；DHCP若改需重核对隧道地址。
源文件/重装/故障排查见docs/ops/SPIKE_WINDOWS_CLIENT.md；证据output/qa/spike_3060_20260909/。
卡片提示改为本机TradingView，Mac仍用原AppleScript；没有改信号/阈值/Pine/YOLO/通知/订单。

## Spike 已增加日线监控（2026-09-09 19:45，覆盖下方四周期口径）

Owner要求日线也出信号。v1.11.0 / c787e90已部署；当前15m/30m/1H/4H/日线
收盘启动先发Bark，YOLO通过追加，TG继续关闭。日线使用已有1Dutc流，每日08:00北京时间
收盘；新增1Wutc作背景，周一UTC开盘，校验/缓存/_higher_at均修正周一偏移，旧四周期不变。
日线direct/model首次启用19:45:04.769 / 19:45:04.736；旧20项启用元数据、347份Bark终态、
266份TG队列逐条保持，新增日线队列0，无测试或历史补发，运行源码SHA匹配。
前端信号/观察页可筛日线，BTC真实120根日线预览及interval=1D已验。日线需341根历史，
周背景需341周；后者不足只显示未知，不能挡可见启动。日线YOLO未做收益/泛化验收。
1077项监控/边界/因果/parity通过、2项旧migration哈希失败（candidates.py是本轮前已有
未提交改动，render.py的HEAD自身已与ledger不符），未绕过或重写ledger；56前端测试通过。
上线时475合约×5周期2375窗口正在预热、已推进且错误0，不能说已全市场就绪。
七数据流冷启动完整分页请求预算约21分钟，短周历史会少些，勿反复重启。
证据output/qa/spike_daily_20260909/；学习笔记daily-rollouts-need-calendar-alignment-and-independent-cutovers.md。

## IMACD V2.7 结构止损与趋势框（2026-09-09，覆盖下方旧Pine止损默认值）

Owner反馈NEAR30m等止损太紧、3R框误导。源码`yoyo/evaluation/pine/imacd_dense_mtf_v2_7.pine`，
代码提交`b9b2e33`，保留云端原名“IMACD 零轴密集启动 · 多周期趋势 V1”，最终保存第15版17:11。
默认近期5根极值（含释放根）±0.2ATR缓冲，初始风险至少1ATR；释放时冻结结构与ATR，
入场时冻结止损/R，下一根跳空越过冻结结构时不生成参考。保留旧“信号K线”模式。
默认收益框仅显示已确认最大有利幅度，不再以3R撑高；3/5/10R只在实际达到后标记。
止损标签显示风险%和ATR，价格文字向框内排版。触损/结构结束/新段接管后冻结，触损后不复活R。
原信号/五项alerts/六MA/副图与通知均未改；SMA20仍是均线参考，不是实盘移动止损。
8个静态契约测试和TradingView48项原生Pine检查通过；NEAR30m实图SL2.312、5.36%/3.6ATR，
OPN1H空头实图SL0.05663、1.18%/1.01ATR。11例只做工程宽度核对，范围0.423%–34.557%，
未验证放宽后收益，5/.2/1不是最优参数；不要说结构止损让任意晚信号都可交易。
报告`analysis/html/p0_imacd_v27_risk_reference_20260909.html`；证据`output/qa/imacd_v27_risk_20260909/`。

## Spike 今日涨幅名单已核对（2026-09-09，截止16:29:59）

截图11币×当前四周期44窗：今天有2个可见多头释放，GRASS15m13:15已获Bark13:16:14接受，
YOLO14:45确认于14:46:27接受；ZEC30m08:30早于15:24启用，不补发。其余9币今天无可见新释放。
关键覆盖缺口：IOST/MINA/GPS/ENA今天15m已有内部dense entry，但双线近零分别0/6/9/0根，
不足focus12，showMarks=false下不是当前通知事件；GPS主线零轴13根不等于双线近零13根。
USELESS/TRUTH/GPS昨天已有Bark成功回执，今天部分周期md持续为正；TRUTH1H23:00同根YOLO的
Bark仍unknown，不能冒称手机送达。IOST9月4日高周期箭头属后来的历史重建，不是当时实时抓到。
只读快照output/qa/spike_gainers_audit_20260909/，复核器yoyo/evaluation/spike_gainers_audit.py；
10,396根focus状态独立重放一致，人工破坏sb的负对照检出12处差异。未改Pine/阈值/模型/服务/通知。
下一步应在完整同期样本单独验证内部密集启动及持续趋势观察，不凭事后赢家榜直接放宽通知或宣称收益。

## Spike 已关闭5m、开启30m（2026-09-09 15:24，覆盖下方旧5m口径）

Owner最新要求5min通知关闭、30min打开。v1.10.0 / 177bc89已部署；当前15m/30m/1H/4H
收盘启动先发Bark、YOLO通过追加；全部TG继续关闭。30m高周期按Pine为2H，仅背景；不改Pine或止损。
5m不再扫描，10个未完成模型候选已标记disabled，5m待发Bark与pending/error模型候选均0；
发送端和模型入口另有周期检查。旧事件、终态回执与5m启用历史保留，不再授权发送。
30m direct/model 首次启用北京时间15:24:16.702 / 15:24:16.685；旧18项元数据未变，
TG266/Bark250旧队列中的终态回执保留，TG无新行，运行源码SHA一致，无测试/启动通知。
BTC30m已读到240根真实已收盘K，间隔1800000ms；前端信号和观察筛选改为30m。
752监控/边界与53前端测试通过。上线验收时475合约×4周期1900窗口仍在正常预热，尚未全覆盖就绪；
新增2H后共6条数据流，冷启动请求下限约18分钟，勿因预热反复重启。证据output/qa/spike_30m_switch_20260909/。

## Spike 5m 两阶段 Bark 已上线；初始止损已诊断（2026-09-09）

Owner 要求5min也通知，并分析止损偏小。v1.9.0 / d22f5ed 已在本机部署；5m/15m/1H/4H
收盘启动先发Bark、YOLO通过追加，全部TG继续关闭。5m高周期背景为1H，网页链接interval=5；
前端信号与观察页均可筛5m，实际SNDK 5m的120根K线预览已验。未改Pine、模型参数、新鲜度或执行器。
5m direct/model 两个首次cutover分别为北京时间14:51:29.544 / 14:51:29.533；
原16项启用元数据、TG266/Bark231份既有回执保留，TG无新行，运行源码SHA匹配。

475合约×4周期首轮1900窗口于15:04:40完成，794.39秒；跨收盘边界有31项
awaiting_latest_confirmed_bar，不能把完成计数当成全市场实时就绪。
第二轮15:09:12完成1900窗口、错误0、151.81秒，15:09:59健康检查ok=true；5m已从旧14:50收盘推进到15:05。
实际5m更新/通知仍可能跨一根K，30分钟门限保持。模型ready，未发测试或启动消息。
随后核对真实5m Bark回执：NOK/OPN多头、ROK空头三条原始启动，以及NOK一条YOLO追加均sent；
只代表Bark服务接受，不代表手机实收。这四条收盘至接受约3.75–8.70分钟，包含预热追赶，不冒充稳态延迟。
证据 `output/qa/spike_5m_20260909/`；工程回归748后端/边界+53前端通过，最后提示文案修正另41定向通过。

14:46冻结的194个今日原箭头均找回精确K线；176个正风险、18个零风险按Pine不画框。
当前默认按信号收盘至该K线极值作R，无ATR最小宽度；15m/1H/4H正风险样本141/30/5，
止损中位0.453/0.436/0.437 ATR；既有0.2%研究成本中位占0.741/0.430/0.176R，非账户实测费率。
旧研究的下一根开盘、2ATR止损属于另一合同。未测试放宽后的收益，未选择最优倍数，未改TV止损。
报告 `output/qa/spike_stop_review_20260909/index.html`；浏览器本地文件预览被安全策略拒绝，未绕过，
算术/尺度不变性及本地链接独立检查通过。后续建议固定入场与账户风险，逐项对照ATR下限和结构缓冲。

## Spike Bark 优先打开 iPhone TV（2026-09-09 12:22）

v1.8.1 / 0f809a7 已上线；新 Bark 的 url 改为官方声明支持的 https://www.tradingview.com/chart/。
正文保留原带 OKX 币种及周期的“网页备用”；长按系统通知的复制按钮提供 OKX:币种.P，不开启 autoCopy。
TradingView AASA 显式排除带非空 symbol 的 chart Universal Link；当前只请求打开 App，不保证切币种、周期或指标。
手机实际点击仍待 Owner 验证；历史旧消息保持旧链接。没有发测试/启动通知，也没有猜测未文档化 scheme。
536监控测试通过；模型ready，1422窗口重启预热中，已扫0错误；源码SHA一致、全部启用边界和原TG266/Bark199终态回执保留，TG无新行。
只改Bark消息展示/点击字段与版本，没有改信号、YOLO、队列身份或TG关闭口径；证据 output/qa/spike_bark_ios_20260909/。
详见 docs/ops/SPIKE_MODEL_CONFIRMATION.md 的手机打开方式及对应 learning。

## Spike 全周期直接 Bark、Telegram 已关闭（2026-09-09 12:02）

Owner最新要求15m也不等YOLO，且移除全部TG；此节覆盖下方09:30的15m model-only与双渠道口径。
运行v1.8.0/源码9c55366：15m/1H/4H指标收盘启动先发Bark，YOLO通过后追加；runtime.notification_channels=['bark']。
15m direct首次cutover北京时间12:02:35，旧1H4与Bark cutover保持；不补发启用前15m箭头。前端默认指标启动，三周期可筛，TG控件/回执入口已移除。
TG worker/model分支默认关闭、不读凭据、不入队/配图、无TG线程，startup probe也关闭。旧pending退休，历史sent/unknown保留；没有删除共享凭据或修改其他服务。
530监控测试+51前端测试通过；实际页面15m筛选已验，运行源码SHA全部一致、TG无新队列/无pending、旧TG266/Bark196份终态回执及旧cutover保留。
上线时模型ready、Bark enabled，474合约×3周期正在正常预热，已扫描部分无报错；此次无真实启动/测试通知，无Pine/模型/新鲜度/执行更改。
说明docs/ops/SPIKE_MODEL_CONFIRMATION.md；本机证据output/qa/spike_bark_only_20260909/{before,after}.json。Bark历史未知回执原样保留，不自动补发。

## Spike 1H/4H 两阶段通知已上线（2026-09-09 09:30）

Owner要求1H/4H原收盘启动不等YOLO，TG/Bark先发“指标启动 · 未经 YOLO 确认”，通过后独立追加“YOLO 确认”；15m仍model only。
源码5e67b5d，运行v1.7.0；新direct政策imacd-direct-start-notifications-v1于北京时间09:30:04启用，原TV/model事件协议、权重、阈值、wait9与30min新鲜度未变。
旧raw启用时间不能授权新通知，旧箭头不补发；双阶段单独去重，重启不互吞pending，首阶段限流先等待、终态或过期不阻断新的确认。
前端新增“指标启动 1H/4H”，原模型列表保留；卡片、主题、周期、Mac TV入口保持。模型故障不阻断1H/4H direct路径。
监控回归、边界测试和48项前端测试通过；实际页面已点验；运行源码SHA全部一致，旧TG249/Bark179份回执及旧启用时间保持。
本次无启动/测试通知、无订单；上线检查正在正常全市场预热（1419窗口，暂未完成一轮），模型ready、已扫描部分0报错。勿因正常预热反复重启。
运行协议详见docs/ops/SPIKE_MODEL_CONFIRMATION.md；本机只读上线证据output/qa/spike_two_stage_20260909/{before,after}.json。

## Spike 高波动山寨趋势研究已交付（2026-09-09凌晨）

Owner授权全时段目标研究；271不同币=54原池+215外部+SOPH/USELESS，原池15m/1H/4H、外部1H/4H。
34/9为继承默认，focus12/.10为显示默认；没有找到跨期跨币稳定的参数冠军。旧年1H主提名Chandelier、4H signal5近期原池/外部均负；15m无旧年入围项，近期四退出均负。
真实长尾与兑现需分开：USELESS1H同风险md35.33R、SMA6038.56R，固定3R2.97R；SOPH4H13.21R、ZEC4H37.14R属截止盯市。高波动上周前25%会漏静转动，XRP1H48.05R自然退出未被保留；不能因此直接宣称取消过滤有稳定收益。
报告 analysis/html/p1_imacd_altcoin_trends_20260909.html；14全景 experiments/active/exp-imacd-altcoin-trends-20260909-v1/results_clock_v2/gallery_final/index.html。
280定向测试通过；图/附件SHA与结果锁复核。修正周一名单按确认收盘切换；clock-v1保留但被v2取代，1H4H正式holdout本配置第2次、15m第1次、外部第1次，未重选参数。
原生1H OI/taker112流161036行留存；原2终端分页错误保留，近期覆盖完整，collector已修等值inclusive边界且32测试通过；未评收益或接入线上。
无Pine/YOLO/通知/ACTIVE/执行/凭据变更；大事件账本/曲线/PNG与数据留本机，关键清单和HTML入库。两研究注册为rejected指未通过稳定性，不代表所有趋势系统都不可能盈利。

## YOLO 已提交72题的框义与未来150复核（2026-09-09凌晨）

冻结9月8日晚导出：新HL2已提交72题（37277–37348），51保留框、15无目标继承原框、6无目标+改框冲突。
57个坐标改动全部保留宽高，56右移；51保留框中49至少截到一根实体，这是几何检查，不是形态错标率。
原弱框也混有趋势段，不能批量移回默认。建议先明确平台首末根、启动K留外，再修预框；未来150终点不能替代形态判断。
72主图+72未来150均从有界源重放一致，144源图SHA不变；2项定向测试及只读查看器实测通过，无LS/答案/训练/Gold写入。
报告analysis/html/p1_yolo_owner_annotation_audit_20260908.html含8例详解及72题台账；只读查看器output/offline_tasks/owner_annotation_audit_20260908/gallery.html。
仍待Owner说明实际是整框拖动还是拉过边角；未排除交互问题，不能断言用户刻意追启动。6冲突逐图保留，未经Owner核对不自动清除。

## IMACD V2.6 副图启动点已修正（2026-09-09 00:08）

针对Owner所指零轴菱形/尖角/光晕重叠：改tiny圆点、透明标签+方向空行、光晕6/4/2。
仅6显示语句变化，余下源代码逐字相同；六MA/主图/R/信号条件/输入/队列预算保持。
源码 yoyo/evaluation/pine/imacd_dense_mtf_v2_6.pine，先提交662922b，原云脚本保存版本12（09-08 23:56）。
编辑器全文SHA一致；V2.5保护测试4通过；USELESS1H用户16根段、XAUUSDT.P1H与KAITO1H多空在浅色/黑色已看图。
临时黑色背景已撤回白色，最终USELESS1H Aug27–Sep4。无本地新PNG，工具原生截图作观察证据。
报告 analysis/html/p0_imacd_pane_readability_20260908.html；此次无通知、模型、前端或执行改动。

## IMACD V2.5 已保存到 TradingView（2026-09-08 23:40）

Owner 授权后实现最新释放持续阶段、冻结初始 R、3/5/10R 已达标记与首次箱沿/固定均线回踩。
源码 yoyo/evaluation/pine/imacd_dense_mtf_v2_5.pine，最终提交190a47a；云端原脚本保存版本11（23:36）。
原系统/focus/MTF/五警报/六MA/副图零轴逐段一致。触初始止损后R永久冻结，但结构可继续；保护线不是执行退出。
面板显式标注最新释放日期，不随历史窗口或十字光标改归属；最新only里程碑/参考避免标签累积。
4个保护测试、24个实际Pine纯函数探针通过；原生ETH15m/1H/4H与最终PEPE30m已看图。
临时nextopen参数已恢复信号收盘，图表所有更改已保存。未改监控、YOLO、TG/Bark或执行器。
注册表另有既存13项holdout白名单遗漏，合计19pass/1fail；本轮配置已按Owner任意日期授权补白名单，不冒称全绿。
工程报告analysis/html/p0_imacd_pine_lifecycle_20260908.html；自动file浏览器打开被安全策略拦截，文件保留，无绕过。
没有收益验证或新训练/部署；实时首tick全过程、深色主题本轮未专项重验。原V2.4及云端v9可回退。

## YOLO 后续走势参考已扩到150根（2026-09-08晚）

Owner要求100–150根，当前1043题默认150：1041完整、STORJ一题132因行情缺口、USELESS一题13因保留集边界。
2bee44e先冻结builder/prereg再构建，1043主图pixel replay、8000正图label对、29181原文件前后SHA通过。
独立包datasets/owner_review_future150_20260908_v1映射新1043图+旧2513原40图软链；任务data/预框/答案均不改。
7369d7b发布器核对来源、XML语义与全部任务/预测后，只改future Image绑定和项目标题为“YOLO 本周审核 · 1043题 · 未来150根”。
Owner提交当前题后刷新；默认筛选/右键和上移布局保留。Chrome37322实际150/150可见，图底610<提交栏624，无测试提交。
原40说明在折叠区；75聚焦检查通过，发布前后413人工答案保留，无holdout OHLCV读取/训练/金标资格变化。
报告analysis/html/p1_yolo_review_future150_20260908.html；父实验results/future150_*三个JSON。此前future40长度口径由本节覆盖。

## YOLO 当前入口已修正（2026-09-08晚：1043题明确命名，默认按钮保留筛选）

源码94fdc657先提交后安装。项目77现名“YOLO 本周审核 · 1043题 · 未来40根”，
tab44“本周审核 · 1043题”；总3556保留旧2513。此段覆盖下方仅能用AsDisplayed的旧操作口径。
Owner保留未提交修改后回列表Ctrl+F5一次；此标签直接点蓝色Label All按钮，补丁转交原生filtered入口。
入口仍为http://192.168.1.4:8081/projects/77/data?tab=44；不改变其他标签或已打开的编辑页。
首次真实验收因浏览器缓存旧包失败；ignoreCache重新加载后默认按钮进入HL2题37277，预框和future40可见。
32 Node+28 Python聚焦测试通过；原3556预测与368人工答案保留，本轮答案/草稿/任务/预测写入0。
没有新训练/holdout读取或金标放行。右键补丁仍在；恢复必须先队列入口、再右键，不能直接重跑旧右键安装器。
报告analysis/html/p1_yolo_review_entry_fix_20260908.html；父实验results/review_entry_fix.json。
Windows浏览器本轮未远程点击验收，需Owner强刷加载；旧有未提交修改的浏览器页未刷新。

## YOLO 旧2513处置清单已冻结（2026-09-08晚，Owner同意旧池先筛、新1043优先）

aa7a0ec初版库存，dfce4bb修复一致多答案拼成重复框并完成跨池元数据审计。
最新库存 output/offline_tasks/historical_inventory_20260908_v2/：2513全部保留，
323有人工框、33明确无目标、12矛盾、2145未审待形态筛选；来自UTC13:21:41—13:21:50快照。
原答案/任务/图片/预测/split和当前1043入口均未修改；没有完成2145逐图形态筛选。
元数据关系37对，核心重叠25对（同venue5、跨venue20），同源+核心完全一致0；
同OKX核心相交ENS36586→37642、PNUT35623→37708、FIL36002→37354、RVN36732→37775、ICP37104→37629。
后两对旧train/新val，合并前需统一时间依赖/分组；不能直接继承标签或按两个旧split相加。
旧17组精确审核图重复=17组alias（34身份），原框身份保留；两组pHash跨币不合并。
最新HL2全部24000背景变体/3129事件与close逐20字段相同；NIGHT/QTUM旧2背景冲突仍存在，
只记下一版成员复核hold，不改历史负例。旧覆盖只审447个OKX背景，其余2682Binance不能称干净。
147聚焦测试通过，无媒体/OHLCV/holdout读取或新训练；均线初始化前缀未纳入此次可见区间统计。
报告 analysis/html/p1_yolo_historical_inventory_20260908.html；父实验results下historical_inventory_summary、
historical_hl2_overlap、historical_background_parity三个JSON。源码/产物逐项注册，raw库存不进git。
下一步继续基于Owner提交标准做旧形态预筛，核对5跨池对、12答案矛盾和2背景冲突；
Owner当前只继续1043 AsDisplayed预框审核，不要求重审整个旧2513。

## YOLO 最新训练数据已可审核（2026-09-08晚：1043事件带实际HL2标签）

Owner要求把最新8000正图对应的1000多事件放LS调整，并在本周彻底整理数据集。
本节覆盖下方旧tab44完整历史列表的操作口径；原历史报告与计划不改写。
最新HL2是1043正事件/8000变体（699×8+344×7），另3129背景事件/24000图；
正事件888train/155val、488LONG/555SHORT，仅旧split来源。本轮没有新训练或金标放行。
656f318源码/prereg冻结、fa61a5a注册后构建：主图复制实际HL2训练PNG，预框直接来自
实际YOLO文本；8000图标签SHA前后通过，1043主图像素重放、3129独立图解码/SHA通过。
1042题有future40，task37821到holdout边界只有13根；future独立HL2目录且无labels。

bb7c01d交付到原项目77，新增1043、重入0；旧2513任务、预测、368答案保留，总3556题。
同一Windows桌面入口http://192.168.1.4:8081/projects/77/data?tab=44，tab44现名“本周审核”，
只显示1043个新protocol；从按钮右侧下拉“Label Tasks As Displayed”进入，右键提交不变。
Label All清筛选，不能用；project.model_version明确选grade_a_hl2_training_label_review_v1。
LS1.13.1空版本无法连审混用新旧预框；旧未审预框仍存，但恢复旧队列前须切回历史版本。
旧76是close版4172空白任务，原样保留；没有第四项目或新的必审50阶段。
真实Mac Chrome AsDisplayed进入37276，框及调整手柄、future和右键提示可见；未改框/提交。
WIN-ZZC严格SSH身份下认证GET新主图与future图SHA一致；没有调整Windows浏览器模式或快捷方式。

061eed5先冻结再运行human-only导出，采集3556任务/3556原预测/368真实答案/0返回草稿：
323 owner_boxes、33无目标且仍带未改原预框、12无目标却带修改框冲突；没有自动当金标。
冲突task34866/34879/34882/34889/34892/34933/34938/35018/35020/35024/35121/35125。
raw/预测/逐条答案分开保存于output/offline_tasks/owner_review_exports/20260908T130556806730Z/；
只读分页采集，非原子快照；原API答案未改写。下次用python -m yoyo.datasets.owner_review_export export
重新导出，不能把预测当人答，也不能直接从有框推断正例。含12冲突的原工作全部保留。

报告analysis/html/p1_yolo_dataset_consolidation_20260908.html；实验exp-yolo-dataset-consolidation-20260908-v1，
PLAN补充本周日程，results含导入/重入/物理/浏览器/Windows/人工导出摘要，pack为datasets/grade_a_hl2_review_20260908_v1。
142聚焦tests通过，非全仓绿。未完成1043人工审核、全部历史重筛、跨池消重、几何回算与新训练。
下一步Owner审核新1043，Codex按原始答案处理12矛盾、NIGHT/QTUM冲突/别名/背景保护及时间隔离，
用各variant坐标变换生成独立新版本。P0/P1过门才可训练；周日20:00交付有效子集与未确认库存。
当前代表训练图core后997题9根、46题8根，不得直接当符合新短延迟合同的训练图。
没有新holdout行情读取、模型推理、训练、promote或实盘动作；新资格继续false。

## YOLO 审核路线更正（2026-09-08：先筛形态，不再要求审完整2513）

Owner反馈“这2513张图是怎么来的，感觉很多图都不是我想要的标准的图”。
本节覆盖下方旧的“完整列表逐张审完”下一步；历史计划与报告保留为当时记录。
已直接复核CSV与冻结manifest：2525方向复核行，来自2391原图；去12skip后保留
1361short+1152long=2513框级任务；104为精确匹配的旧⭐，其余2409也全量保留。
源表SHA bb7081e7e1821c5f791486fae0f29caf18307b104bbb07156c35883781071c9a；
manifest SHA 7588f9c62f26a66986f747f9dd27dff8f6c216a6b26f307e705cba2880c3a641。
plan_rows没有当前形态质量准入；central_core只取旧框中央一半并限制4–7根，
没有逐图定位平台/启动。旧方向确认和旧⭐不能代替当前形态及派生核心确认。

本周下一步调整：Codex先按已有Owner参考与local_signal_v2语义检查候选形态，
再为拟入选项细化可编辑预框。先分清原形态不合适与派生裁图/框不合适；
保存原ID、原几何、候选判断及理由，不能只按统一根数、未来涨跌或机器分数决定正例。
2513保留为历史候选档案，已提交答案保留；未选中不自动改背景，旧⭐不自动全入选。
人工仍用原项目、预框和future40，不新增项目/必审50阶段，也不要求Owner重审整池。
原周计划300独立事件是待吞吐和形态合格率验证的目标，不能为了数量放宽标准。
下一版队列与训练数量须据真实筛选证据更新；P0/P1及训练/生产资格不因期限放宽。

本轮只完成来源复核与路线更正，尚未完成2513逐图重筛；没有改Label Studio任务、
当前Default44列表、用户草稿/答案或右键提交功能，没有读取行情/holdout或启动训练。
教训：docs/learnings/historical-direction-review-does-not-select-current-shape-exemplars.md。

## YOLO 改在 3060 电脑审核（2026-09-08）

Owner追加“右键就是提交”：171b833源码冻结后，已向native LS的React main.js尾部安装限定项目77
审核页的右键handler；原包备份与事务收据在output/offline_tasks/label_studio_right_click_20260908/。
不重启、不改标注；原Submit/Update按钮负责验证保存，900ms跨题防连点，Shift+右键保留菜单。
输入框/菜单/弹窗/列表/其他项目不触发。7安装测试+12浏览器模拟场景通过，未提交真实答案。
实际任务34777刷新后原按钮title显示右键提示、console error0；3060实取新bundle SHA一致。
Owner先保存当前未提交改动，再Ctrl+F5强刷；后续图片/审核区右键即原提交/更新。
复现和恢复命令见docs/ops/YOLO_REVIEW_FROM_3060.md；未来LS升级需重新核对补丁。

Owner要求用3060电脑操作审核。已严格SSH验证WIN-ZZC/RTX3060，目前192.168.1.2；
Windows桌面已创建并读回核对“YOLO 人工审核.url”，指向Mac192.168.1.4:8081/projects/77/data?tab=44。
从Windows用原账号实际认证读取项目77：2513任务/2513预框/20答案；安全样本主图与future40
实际GET200且SHA与原图一致。首次浏览器登录仍由Owner完成，没有代填/提交人工答案。
同一Mac native服务和数据库继续保存进度，没有安装新LS、迁移或创建项目；目前Default44不变。
Mac须开机、两机同LAN；不要重跑旧start_label_studio_review.sh（含旧包准备和Docker重启）。
操作与证据见docs/ops/YOLO_REVIEW_FROM_3060.md；收据output/offline_tasks/yolo_3060_review_access_20260908.json。
只确认审核链路和机器身份；训练空闲时段/版本契约/P0P1资格仍待周计划检查，未训练、未读holdout。

## YOLO 本周计划（2026-09-08：周日 9/13 20:00 交付研究候选与固定对照）

Owner要求“这周必须把yolo模型优化一版，做计划”。计划已落盘：
analysis/html/week_plan_yolo_20260908.html；源文analysis/week_plan_yolo_20260908.md。
实验exp-yolo-owner-label-optimization-20260908-v1，当前只到planning，未开始本轮训练。
只读实测项目77仍2513任务/2513预框，20份真实提交（多9/空11）；不是2513新金标。
目前Default44完整列表不变；计划同项目统一待审队列由Codex准备并验连续出题，As Displayed进入，
不能用会清筛选的Label All。Owner只调整预框，future40只供审核，不要求先50分组或跨项目。
暂按每日1h，周四冻结前约3h排300独立事件目标（160train/40dev/100accept），另30盲重复；
数量待完整会话耗时校准，第二人复标资源和当前3060状态尚待落实，不能冒称已就绪。
下一步：20真实答案的human-only导出→保留人工几何并回算→受限训练窗→时间/重复隔离。
当前审核主图1859/2513在建议core后>5根，不能直接训练；A/B须共用合法重建图。
周四18:00 P0/P1证据卡，过门后周五固定YOLO11s960×40两臂；周日一次pre-holdout验收。
主目标背景误报相对-20%、召回最多-3pp、方向错不增加；小样本必须报告区间。
不继承8月训练豁免，不读全项目holdout、不promote；training/production资格仍false。

## YOLO 当前进度（2026-09-07：2513个建议框已审计，统一入口逐张调整）

最新操作口径：Owner质疑先50分组增加人工负担。人工默认回到完整2513张的Default44：
http://127.0.0.1:8081/projects/77/data?tab=44 ，直接Label All Tasks连续审核。
已只读核实44无过滤、2513唯一任务；浏览器切回该入口，预框和future40继续沿用。
“先50”是后台校准建议，不是人工必须完成的阶段门；Owner不必切分组或管理审计标签。
既有重点/重复/冲突/星标视图留作后台排查，原数据和人工答案不变。下述为上一轮审计记录。

已装工具本轮真正接入：source301e1fe冻结后，Datumaro检查2513框，0 error；
28 warnings对应14张几何统计异常，不当错标。CleanVision确认17组完全重复/34身份，
另2组pHash相同/4身份（跨币候选，不自动合并）；异常主图尺寸/比例0。全部2513保留。
FiftyOne后台fable_owner_box_curation_20260907_v1持久化收录，仅主图+proposal_boxes，
无ground_truth/predictions；重入新增0。Cleanlab已安装，但当前合格样本外检测预测
覆盖0/2513，诚实未评分。人工仍只用项目77，不增加第四个Label Studio项目。
项目77重点50入口：http://127.0.0.1:8081/projects/77/data?tab=45 ；
重复38/负图冲突3/星标104分别tab46/47/48。页内按task ID，FO/JSON按风险排序。
点按钮右侧下拉选择Label Tasks As Displayed，不能用Label All（会删掉筛选）。
左右箭头是项目历史；只查看时回列表点具体行。实际Chrome已进正确34777，建议框/
future40均加载；未伪造提交。初版in_list后端可查但前端卡加载，c18a61f冻结后仅修复
45–48过滤编码为or/equal；旧收据保留，重入新增0，59联合tests通过。
本轮实验experiments/active/exp-owner-box-curation-20260907-v1/，报告
analysis/html/p1_yolo_owner_box_curation_20260907.html。源图、旧标签和原4个人工答案保留。

Owner确认复用历史标签，并要求细化框。先冻结f2d3acb再构建：2513原人工框
（short1361/long1152），104精确⭐排前；12个skip另记，17组同范围别名保留。
Label Studio项目77：http://127.0.0.1:8081/projects/77/data 。用As Displayed进入当前筛选，
建议框已带入，可直接提交或拖动；右图完整未来40根，原人工框和编号对照可展开。
本版只按既有中央4–7根规则细化蜡烛+六均线包络，未逐图重判启动首根。
原人工确认/方向/⭐保留；新派生框sample_owner_geometry_confirmed=false，未写回训练。
主画布、原参考、编号对照、future_only分开。2510原长图实际时钟通过；3张越holdout
在打开前拦截，用安全画布替代。2513全部future40，10052图独立解码+SHA核对。
121聚焦测试通过；真实Chrome页面选框出现调整手柄，4图加载；未伪造人工提交。
重复导入新增0/复用2513，原项目76（4172事件）/74（276补充题）和历史答案保留。

覆盖审计源码d21980b先冻结，只读metadata：当前4172中OKX596/Binance3576，不跨venue
硬联结；旧框与当前核心5同向正、0反向、1负例交集。全可见负图另发现2候选冲突事件：
NIGHT的8张空标签图完整含旧7根核心；QTUM的7张与旧宽框交集，其中2张完整含旧4根核心。
共3旧框/2负事件/15变体，QTUM两旧框不可当两独立事件。时间相交不等于语义同一，
未自动翻标签。后续合并训练集前必须处理这些冲突，不能只查负例pseudo-core。

Pack：datasets/owner_box_refinement_20260907_v1/；实验与收据：
experiments/active/exp-owner-box-refinement-20260907-v1/（results含覆盖明细、物理/UI检查与导入收据）。
交付报告：analysis/html/p1_yolo_owner_box_refinement_20260907.html。
历史12565人工图、1345旧short正例、500方向批次/176图⭐口径与证据，仍见
analysis/html/p1_yolo_historical_label_reuse_20260907.html，不与当前1043自动正事件混称。
下一步Owner在项目77完整列表逐张调整；后台保留重点50与14张几何警告供诊断，
不再要求Owner切组或先完成50张。读取真实Owner新答案，再逐图回写独立新数据版本。
旧train/val只作来源标记，未来上下文与新旧合并需要重新审时间依赖。无新训练、模型推理、
holdout OHLCV、promote；训练和生产资格仍false。

## K1/K2 当前会话进度（2026-09-07 — V39 等确认减少交易，但仍亏损）

本节仅属于当前 K1/K2 研究会话，与上面的 YOLO 工作分开。目标工具当前 paused；
本轮按 Owner“继续当前会话任务”完成研究，不冒称目标已恢复或盈利达成。
V38 入场时钟审计通过，171 前缀/946 trace；HTML 已交付并注册，非经济盈利证据。
V39 源码1682629先冻结，再回放已批准 BinanceUSD-M BTCUSDT2023–2024 原63病例108控。
只改为原 E..E+55m 等native5同色入场，原止损、20bp成本、真翻转退出与原E+72h不动。
原63请求均净−12.8093→−5.7649bp；38实际成交均净−9.5575bp，PF.6843，7胜31负。
25不成交=15等待触损+10过期，包含遗漏3个原赢家。22延迟fills退出时点/价格全未变，
19追价变差、3入价改善；这组均净+19.5647→−.7654bp。整体改善7.0445bp=毛−.8920+省费7.9365。
原36三控主增量+2.3301bp，CI[-8.2927,13.7565]、p=.3796；绝对超额+.8945bp。
仅2023H2病例均净正，配对增量两半年正两半年负。拒绝，不降门、不设TV默认。
E39 summary/pre_outcome及19CSV保存于实验目录和data/owner_k1k2_delayed_entry_v39。
650聚焦tests+31报告tests已过，1548独立保存断言通过；FAILURE_ANALYSIS已完成。
最终HTML analysis/html/p1_btcusdtp_owner_k1k2_delayed_entry_v39_20260907.html，
artifact_final由cdf8a3d生成，追加26交付tests，canonical通过；仅structural_only，未验手机交互。
早期纯MD包被图表契约拒绝，原包/收据保留，未重跑回测；8行半年图由真实SQLite复算。
下一步优先隔离确认后市价追入与等回踩的成交机制，先审触价/同bar止损等时钟，
不同时改退出/环境门，不搜索最佳等待期限；新holdout/成本/止损/ACTIVE/TV/实盘均未动。

## ⚡ 历史结果（2026-09-07 — V37真翻转退出仍亏，改善来自未匹配少数赢家）

目标active，未盈利。457dc62冻结后仅改colour→transition_colour，共振两臂关闭；
同BinanceUSD-M BTCUSDT2023–2024、原63病例/108控，非当前完整15m Pine策略。
病例平均净−22.54→−12.81bp，中位−21.97→−29.94bp；13好25坏25不变；硬止损5→20。
17笔从未激活同向后止损。44个不同K1不等于独立样本。四半年仅2023H2平均净正。
原匹配36病例自身−1.08bp变化，27未匹配贡献超过全部净改善；最大两赢家都未匹配。
配对增量+2.06bp，CI[−7.83,10.61]、p=.3697，绝对超额−1.44bp；拒绝，不降门。
初态同向16笔两臂不变且均净−21.65bp；初态反向47包含最大两赢家，不能一刀切禁止。

E37 experiments/active/exp-btcusdtp-owner-k1k2-transition-exit-20260907-v37/；13数据输出
在data/owner_k1k2_transition_exit_v37，单仓新59笔净−13.29bp、阻塞4。summary/report_data/
REVIEW/QA/收据齐全。报告analysis/html/p1_btcusdtp_owner_k1k2_transition_exit_v37_20260907.html。
canonical最终artifact_final.json由0791e19构建，原生验证通过、HTML structural_only；
未验手机/主题/来源弹窗。两早期报告包及失败保留；没有再跑经济回测或覆盖原始证据。
398聚焦测试通过，新增报告SQL最后3过；非全仓绿，V36旧4失败未绕过。无新holdout读取，
V36两次旧文本暴露仍按原QA保留；不改成本/TP-SL/依赖/TV/ACTIVE/训练/实盘。

下一步NEXT_ACTION.md：先审K2后待确认再入场的因果时钟、原K2止损是否仍有效、未确认/
失效/未知请求如何保留，所有原控同规则；然后冻结单一新假设。原72h持仓期限不能偷当
新入场有效期。不要把未来曾激活、首5m早退、MFE或赢家身份当入场过滤，勿扩大行情范围。

## ⚡ 历史结果（2026-09-07 — V36真实量门失败，已定位反色延续早退）

目标active，盈利目标未达。4e57139冻结后回测BinanceUSD-M BTCUSDT2023–2024；
完整1h源码纯形态63K2/44K1，非当前15m完整Pine接受逻辑。真实量同向保留23笔，
平均净−21.53bp（成本20bp），四半年均未给出正收益证据；36三对照覆盖57.14%。
主配对改善+3.195bp＝毛−.508bp＋省费3.704bp，p=.1768、CI跨零，拒绝，不调正值门。
退出时钟d8920c3核实：47/63入场前5m已反色；38首5m颜色退出只是反色延续，
不是新翻转。这是原colour状态规则的行为，不是程序实现错误；未证明延后就盈利。

E36 experiments/active/exp-btcusdtp-owner-k1k2-genuine-flow-20260907-v36/；原始交易
data/owner_k1k2_genuine_flow_v36，summary/pre_outcome/clock/SQL/QA/REVIEW完整。
交付analysis/html/p1_btcusdtp_owner_k1k2_genuine_flow_v36_20260907.html；最终canonical
artifact_reviewed2.json由3f1ddd7构建，早期两artifact保留；structural_only，无手机UI实测。
相关297tests通过；扩展852过4败（2旧迁移hash、2holdout名单，含本轮意外暴露记录）。
本轮两次意外旧holdout文本展示，详见QA；未用于任何经济计算，不能声称全调查零暴露。
不改守门授权名单使其变绿，未改成本/障碍/TV/ACTIVE/训练/实盘。

下一步已冻结但未跑：NEXT_EXPERIMENT.md，同63病例/108原控，共振关闭，仅colour→
transition_colour，先审实际arming语义、保留K2硬止损/72h/.002费用。比较全部母群、
38早退组与其他25组、原三对照及各自单仓占用；不只挑赢家。63样本/匹配不足，
即使改善仍只能机制研究，不能部署。禁止调用会读2025+的旧宽加载器；不重跑覆盖V36。

## ⚡ 当前真相（2026-09-07 — V35因果对齐完成，K2请求/对照供给不足）

目标模式active继续；372ed6a先冻结后只读V4纯身份/时钟/终态投影和V34真实quote flow。
251固定1h K1母→55首K2请求（100超时、95错误侧收盘、1HL2侧烛色），非全市场K1K2；
462原控制只覆盖154母，97母无控；55请求仅31有三控（93原控制，其中25发出K2）。
四半年K2 13/19/12/11，paired4/12/10/5，不能55case对89control K2直接比较。
1,001窗口全部complete：case251/55/55，control462/89/89（K1/K2/K1close-throughK2close）。
八季度prefix逐字段一致；全年38零量不落事件窗，零/缺根/未知到达已做合成反例。
historical complete≠observed delivery：无日志，所以后者1001不可用，不等于历史都迟到。

E35 experiments/active/exp-btcusdtp-k1k2-genuine-flow-alignment-20260907-v35/，data独立
data/k1k2_genuine_flow_alignment_v35；summary/config/报告数据/REVIEW/QA/三cell笔记齐。
14174d4先冻结报告control parent身份复算，2484b83报告及HL2术语修正；419tests过13旧警告。
报告analysis/html/p1_btcusdtp_k1k2_genuine_flow_alignment_v35_20260907.html；canonical通过，
structural_only无Chromium、无UI验收；notebook为stdlib顺序执行，非Jupyterkernel。
新增收益/2025+行情/holdout读取0；原V4/V34/TV/成本/TP-SL/阈值/ACTIVE/实盘未改。
下一步不在55小子群反复旋参：先审查更早完整K1K2形态定义，在2023–2024安全前缀
重建因果候选母群和对照支持，再冻结单一真实flow假设；不能调用旧load_featured读到2026，
不能从成交/赢家名单重建候选。样本、对照、净优势各自验收，不把本轮输入通过当盈利。

## ⚡ 当前真相（2026-09-07 — V34真实主动成交量覆盖完成，盈利未验证）

Owner“那开始下一步啊，开始目标模式”：49c0d8c先冻结后只读Binance BTCUSDT5m
2023–2024共24个月官方CHECKSUM与本机原ZIP。210528/210528完整、17544小时桶齐全；
0缺根/未知/重复/null，38原始零成交。209923根普通烛身/quote delta非零者中40489相反
（19.2875%），不是MA Shift颜色，不是胜率/未来预测，不据此挑参数。
复核补强8aaaa00后发现delta浮点相消容差误报警，93b556c修正并保留原summary/manifest，
verification逐月全字段/CSV字节一致。c90c6da原文本Decimal全量单位审计：买42/卖49
共91侧flags、84独立bars，全为浮点边界；精确源越界0、删改0。

E34：experiments/active/exp-btcusdtp-genuine-flow-coverage-20260907-v34/；
source_manifest/summary/verification/unit_audit/report_data/artifact/verification.ipynb/QA齐全。
独立研究数据data/genuine_flow_coverage_v34，不改原缓存，不替代OKX执行源。
报告analysis/html/p1_btcusdtp_genuine_flow_coverage_v34_20260907.html；canonical验证/打包过，
structural_only（无Chromium，未做手机/主题/source-dialog实际UI验收）。3notebook codecells
stdlib顺序执行，非Jupyterkernel；284联合tests过，13旧警告。报告准备的括号语法错误
在产出前修复54efb7c并补SQL测试，失败过程见QA。源码/产物按版本分别留链。

真实收益/标签读取0，2025+bar与holdout未读，成本/阈值/TP-SL/TV/ACTIVE/训练/下单未改。
下一步先冻结真实flow与K1/K2决策时刻的因果对齐、零量状态处理，再定单一共振假设；
回踩逆向主动成交减弱仅候选，窗口/阈值尚未选，不把输入通过当盈利通过。
大目标仍未达。初始goal为usageLimited；交付前get_goal返回null，随后按本轮Owner请求
重新create_goal成功，08:09UTC返回active，goal_activation.json已记录；未消费usage reset。

## ⚡ 当前真相（2026-09-07 — V33改换研究路线，真实量输入模块完成，未跑新收益）

Owner追问为什么一直不行、能否多换思路。本轮源码/历史报告查重：既有动态退出、
真实分批、首次收缩突破、K2等确认、4h背景/广度均已测，不能包装成未试过。
V24/V30/V32固定4h诊断不等价于动态路径，但这些失败和早期路径负结果全保留。
三条候选独立记录：结构位突破后K2回踩（不硬绑MA）、真实taker流量、原生4h事件15m执行。
仅第二条输入接口已实现，另两条是草案，不是新回测或获准阈值/障碍改动。

384053c先提交plan/config/两条learning，247039c提交独立flow parser与合成测试。
原Binance规范化仅输出OHLCV；新yoyo/data/binance_um_flow_archives.py显式保留
taker买币量/金额、quote量/count，派生sell/delta，不冒充OI/盘口/资金净流入。
原七列逐字段dtype不变，旧parser/缓存不改；保留source_exchange=binance_usdm，
不是OKX执行源替代。earliest_available_at仅完整bar理论边界，不是实际网络到达。
70新合成tests，最终258联合tests过（13旧警告）。首次发现旧test取non_15m_gaps，
单独不加载新模块也失败；仅修为既有non_bar_gaps并补5m/15m缺口反例，QA保留过程。

计划HTML analysis/html/v33_research_reset/PROJECT_PLAN.html，标准转换/结构检查，
无browser/mobile视觉验证。E33有PROJECT_PLAN/config/QA。真实行情/新收益/holdout读取0，
TV/成本/TP-SL/阈值/ACTIVE/训练/生产均未改；goal仍usageLimited，不能称后台持续执行。
下一步另冻结2023–2024真实成交方向数据的来源/覆盖检查，先验证可用再立经济假设；
不得直接又把新量字段叠到旧门并无限旋参数，不得把数据接口通过当盈利通过。

## ⚡ 当前真相（2026-09-07 — V32前置量波经济失败，改善完全由少扣成本）

Owner“继续下一步”：4cbcc93先冻结 V31门的4h/20bp经济检验，再读取保存V24标签。
原251母/744控制不变；通过100弃权148未知3，对照全300（158通过142弃权）不裁剪。
100入口毛+1.554979bp、净−18.445021bp，CI[−44.1894,+6.5951]；
55毛方向错、7小毛利不足成本、38净正。配对超额+2.332327bp，CI[−26.6268,+31.6545]。
同248机会改善+11.469797bp=省成本+11.935484bp−毛贡献0.465687bp，
成本贡献104.06%，不是方向过滤盈利。四半年25/27/23/25，净均值全负；10/24月份正。
四主Holm p .9163/.8926/.6681/.1088，not_supported，不进入执行/TV/动态退出优化。
L49均值+1.397648bp、S51−37.509546bp只是描述，不事后转成只做多。

Root独立算术audit核对3980标签/1004母H/四推断，另作者完整政策/半年终门补核通过。
99新合成测试+245边界/父支持/report测试通过（13既有警告）；report查询builder3ea0bb2。
V31来源summary没有sources字段：按真实started/frozen/audit链修复，收益读取前发现且有回归tests。
HTML analysis/html/p1_btcusdtp_hourly_volume_wave_economics_v32_20260907.html，14sections/15blocks/1native24月双系列图。
canonical validate/package通过，structural_only，无Chromium，无browser/mobile/source-dialog验证。
E32有完整三账本/summary/audit/report_data251行/REVIEW/QA/4已执行notebookcodecells；
notebook用stdlib顺序回放，非Jupyterkernel验证；不安装依赖。

V31仅支持通过的旧结论不回写。新收益仅这一次配置；无raw/2025+/holdout（0次）、
TV/成本/障碍/参数/ACTIVE/训练/下单改动。总盈利目标未达，goal usageLimited，不宣称后台继续。
下一步先与历史实验去重，再设计入场前结构诊断（首次突破vs区间反复穿越），
不要继续堆相似颜色/量波门，也不要从微正多头倒推新最佳参数。新阈值/障碍/数据需要独立方案。

## ⚡ 当前真相（2026-09-07 — V31前置方向量改善覆盖通过，未读该门收益）

Owner“下一步，继续”：f8e450b先冻结再只读V20保存小时OHLCV、V24原251/744身份。
ChartPrime DeltaPulse源码20/5数值，派生门d*(wave[T-1]-wave[T-2])>0，
较早T-2需100连续小时；非原生背离信号、非首次启动/真实订单流、不叠V29。
100accepted/148abstain/3unknown；controls401/343/0。四半年25/27/23/25，
24月全有、每月2–8，四支持门80/12/12月/3月全过，不是功效或盈利验证。
100入口全有300原控制（158通过142不通过），未来条件比较不可只留158。
3unknown仍是2023-06-25 UTC03/11/14，older count51/59/62预热不足。

Root独立stdlib审计f8e450b重算18222小时主要EMA/lagwave/clock、995门、
62counts、251groups和支持门通过；非所有debug字段、非raw5/Pine/经济审计。
77新合成/guard tests；含父契约/边界联合299过11.48s，report另5过。
报告11markdown sections/12blocks/1native24月两系列支持率图，非胜率或净值。
初次缺查询来源验证失败保留；f57f835实际SQLite投影且与原Python投影相等后过。
最终HTML structural_only，无Chromium，未验browser/mobile/source-dialog，无安装。
交付analysis/html/p1_btcusdtp_hourly_volume_wave_support_v31_20260907.html，
E31保存summary/audit/contexts/counts/源收据/QA/NEXT_EXPERIMENT及验证失败旧产物。

V31仅support accepted、未读新分组收益/2025+/holdout、未改TV/退出/成本/ACTIVE。
下一步是另行冻结固定4h/20bp经济诊断与省成本分解，再读保存V24labels；
不是调EMA/阈值或换控制。2023–2024反复使用，必须标探索而非独立验收。
V30经济拒绝不变，盈利目标未达，get_goal usageLimited，不consume reset，
不承诺后台执行。保留其它会话dirty，仍main单仓。

## ⚡ 当前真相（2026-09-07 — V30默认分类器经济诊断未通过，改善主要是少扣成本）

Owner批准另做V30探索，明确不回写V29的78<80支持不足。917f333先提交再读取保存
V24标签/V29上下文：原SMA40的251母、744原三控，default10/100、4h主、20bp冻结。
78accepted/170abstain/3unknown；无新参数/抽样/原始价格/2025+/holdout读取。
78个保留入口毛+5.2284bp、净−14.7716bp，35正/40毛方向错/3被成本抹去小毛利；
条件对照原234控制净−19.4240bp，超额+4.6524bp，CI[−16.1421,+25.1684]跨零。
政策均值在相同248已知门机会为−4.6459bp，背景−7.9000bp，超额+3.2541bp。
政策改善+14.2614bp中13.7097bp即96.13%来自省170次20bp，毛贡献仅+0.5517bp。
四主Holm p .9285/.6678/.4329/.0488均未达.01，四半年保留均值皆负，9/24月净正。
1/12/24h仅描述且也负；not_supported，未进入执行/动态退出验证，不声称永远无效。

初次root独立算术审计917f333通过3980保存label/1004母H/4主推断/24月；补审增加
SD极值唯一网格，首次字段名错误失败收据保留，896db05修复提交后只读重算过。
不导入策略模块，NumPy仅复现PCG64；非原始行情真实性/盘中路径/Pine或实盘审计。
核心作者二次检查和可复现命令在E30 REVIEW.md，不冒充第三方独立审核。
系统Python核心数值版本合规，但全仓测试239过3版本漂移失败2跳过；未改系统环境。
项目.venv联合245测试通过13既有警告；真实诊断在系统Python跑（.venv缺seaborn）。

交付analysis/html/p1_btcusdtp_hourly_classifier_economics_v30_20260907.html；14sections/
15blocks/1native24月双系列均值图，非净值；表内完整组、分母、成本分解和风险。
官方validation/package过，structural_only，browser/mobile/source-dialog未验。
最终tooltip明确K1正标签计数非控制组；旧artifact/receipt可恢复保留，未重跑经济。
E30存完整ledgers/audits/report_data/REVIEW/QA/portable_final收据，8artifacts登记。

盈利总目标未达成，get_goal当前usageLimited；未consume reset，不承诺后台继续。
本轮批准的经济诊断已完成，不自动继续相同门调参、不降成本、不上TV/ACTIVE/真金。
下一步建议先审既有实验、拟定互补的入场前状态变化/启动事件及未污染验证方案；
不是在已见赢家上找参数，新方案需owner决策。保留其他会话dirty改动。

## ⚡ 当前真相（2026-09-07 — V29源码默认分类器支持不足，未读该门收益）

按 ChartPrime Trend Classifier 原源码 default10/100 加一个已完成 K1 的同向状态门，
原 SMA251 及 V24 已冻结744自身时钟controls不变，不采用VWMA288。源码d305c2c
先提交再计算：78accepted/170abstain/3unknown；control136/608/0。四半年20/17/19/22，
24活跃月，每半年6月；78低于预注册80，另三门通过。80不是统计功效或盈利裁决。
状态已知248/251；78通过入口均完整三控，234controls中51通过183不通过。

原因互斥拆解：67仅价格距离不符，33仅斜率不符，70两者不符。170中168是分类器
中性，仅2明确反向；不可说删掉170亏损或盘整交易。3unknown为2023-06-25 UTC
03/11/14时K1，断点后连续53/61/64根未完成100预热。源码offset=-1仅绘图，不倒移
决策时钟；短分支允许flat slope，不能自行对称化。默认参数不等于最优参数。

独立stdlib审计f549dd4先提交再跑：18222小时/995上下文/62计数/251母组重算通过，
13inputs/5outputs/10sources+18/12父sources核验，初末SHA一致。非raw5聚合、抽样重放、
原始来源真实性、Pine原生/实盘一致性或经济审计。报告SQL首次重名错误保留失败收据，
2合成tests复现后限定c.上下文、92a6e57先提交再成功查询；未动上游结果。
451联合tests过、13既有Pyparsing警告；139为V29三测试文件数量，不重复累加旧子集。

报告analysis/html/p1_btcusdtp_hourly_classifier_support_v29_20260907.html；E29保存
完整结果/audit/report_data/QA/NEXT/portable收据。13sections/14blocks/1native24月图；
官方validation/package通过，structural_only，browser/mobile/source-dialog未验。
新增源码backplot时钟、真实schema联表测试、中性不等于盘整三条learning。

V29登记inconclusive仅支持不足，不读78子集收益、不降80、不改2025+/holdout、TV、
ACTIVE、训练、部署或真金。V28负面经济结论不变，盈利目标未达成。
下一步V30尚未登记/运行：先审查与原启动互补且不重复旧实验的状态变化定义及因果钟；
不得从168中性或V28赢家倒推参数。见E29 NEXT_EXPERIMENT；不继续堆同义MA票。
get_goal为usageLimited，未消费reset，不承诺后台运行。保留其他会话dirty改动。

## ⚡ 当前真相（2026-09-07 — V27支持通过，V28量权入口经济检验拒绝）

V27 builder36a315e先提交再重建新VWMA288母的背景资格。284完整组三控=852，
98.6111%覆盖，通过260门；15493合法边/9282候选时钟/179组件。4个未知母全保留。
独立审计ebbdb21逐小时重算18222保存小时、全部边和容量上界284=可行284；
18inputs/10outputs/12sources及父链验过。第一次审计ID时间解析失败记录保留，
修复先提交后通过；不是收益或随机样本结论，V27仅support accepted。

V28经济源码2c39bac先于抽样与OPEN读取。一颗PCG64 seed20260907另抽852控制，
不继承容量见证。288自身4h标签全known；283完整配对，4缺支持+1控制缺内部5m行，
不补抽、不删母。主4h全母毛−3.0715bp，减20bp后−23.0715bp，
95%月块CI[−34.0168,−12.2284]，p.9999。有效配对母−21.7227/背景−18.8433/
超额−2.8794bp，CI[−13.8960,8.8144]，p.6868。四半年自身成本门皆负。
169毛方向错误、18小毛利被费翻负、101超过成本；1/12/24h自身均值也负。
固定时钟方向变化不是K1止损/分批TP可执行PnL。V28 rejected，不是待优化。

独立审计f308a82重建4560标签/1152配对/852随机控制及月推断；补审逐笔端点报价、
诊断哈希/顺序和裁决通过。初审收据保留，未改原经济源码/结果。源补审364f60c
前后哈希包围比较219551pre2025 OPEN，逐值一致；仅事后源一致，不倒称原run
已证明原子快照。817联合测试通过，13既有Pyparsing警告；未装新依赖。

主报告analysis/html/p1_btcusdtp_hourly_vwma_fixed_clock_v28_20260907.html，
支持报告analysis/html/p1_btcusdtp_hourly_vwma_background_v27_20260907.html。
两份官方portable validation/package通过，structural_only；浏览器/手机/来源弹窗
未验。E27/E28保存完整结果、audit、QA、REPORT_NOTES与delivery收据；4条learning。
无2025+价格、holdout、TV、ACTIVE、训练、部署或真金改动。盈利目标未达成。
get_goal当前usageLimited，不消费reset，不声明后台继续。保留其他会话dirty改动。

下一步尚未注册或评估V29。应先审ChartPrime源码清单及已测趋势状态，挑真正不同的
入场前因果定义，不能重复MA长度/普通放量/prior突破或从101盈利标签倒推过滤器。
先冻结定义做无收益支持审计，再考虑单变量经济验证；详见E28 NEXT_EXPERIMENT。
停止将VWMA40当改善版、停止只在同入口群换TP。旧V24/V25/V26历史段落不回写。

## ⚡ 当前真相（2026-09-07 — V26量权参考支持通过，尚未计算新收益）

374f003先提交SMA40→VWMA40(HL2)纯参考/全机会/runner/配置/计划/102新测试。
固定V20保存小时OHLCV，旧251所有字段和全部共同特征先复现，写baseline检查点后
才算VWMA；新入口288，交集173、删除78、新增115。四半年68/74/66/80，
24活跃月、每半年6月，80/12/12/3支持四门通过。34512完整时钟×方向，
shape合格2827，unknown32（缺根2、形态预热26、参考预热4），两臂分母一致。

a3318dd独立stdlib审计先提交再跑：18222小时×两臂、全部入口字段、全机会与124计数
独立重算通过；10inputs/7outputs/12sources+18/11父sources核验并计算后二次SHA。
新增115的旧侧拒绝组合：58仅实体未贯穿、54仅HL2side、3贯穿+side+extension；
删除78的新侧拒绝46/27/5。不能把颜色修正当作趋势质量或盈利证据。
102新tests、429联合tests通过；16独立自检。reportbuilder37ade25。
报告analysis/html/p1_btcusdtp_hourly_vwma_reference_support_v26_20260907.html，
13sections14blocks1native月差图，official结构验证过；browser/mobile/source-dialog未验。
E26目录保存全部结果/audit.json/QA/REPORT_NOTES/NEXT_EXPERIMENT；2条learning。

V26 accepted仅指support，不是策略盈利验收。无收益/原始5m/2025+/holdout/TV/ACTIVE/
训练/实盘修改。盈利目标未达成（本轮get_goal返回usageLimited，未使用reset）。
下一步V27尚未登记运行：以全部288为母单另建三键背景，至少260完整三控组，
不可继承旧248分配；旧V10表的SMA穿线和旧母排除要随新参考/288母重建。
支持通过再另冻结随机分配与主4h/20bp标签，退出保持冻结；详见E26 NEXT_EXPERIMENT。
V24入口负面结论和V25结构事件支持不足结论不改。保留其他会话ARB等dirty改动。

## ⚡ 当前真相（2026-09-07 — V25同根结构事件支持不足，未算该门收益）

b1cca1e先提交V25事件公式/runner/config/plan/130新测试再真实运行。
V20冻结18222保存小时trace，V24原251case/744随机control与248组三控/3无控保持。
只将持久同向结构改为K1本根首次建立/反转方向事件；10/10与unknown不变。
实际case9accepted/241abstain/1unknown，control3/741/0。四半年1/2/4/2，
8活跃月、各半年活跃月1/2/3/2；80/12perhalf/12months/3monthsperhalf四门全部失败。
250caseknown，原248三控全known，9accepted全matched；其27controls为1accepted/26abstain。
旧同向137中128不是本根方向事件，9均为反向state翻转；唯一unknown是2023-06-25
03:00UTC K1的no_confirmed_break（原unmatched）。不改unknown为abstain或删分母。

48511b4先提交独立stdlib auditor再真实执行：18222小时结构、995上下文、62计数、
251母配对全重建通过；8本轮+18/12历史sources、13inputs/4outputs核过。
V20历史失败在featurefreeze之后，首次/恢复标记同一个SHA；只验特征链，不读旧收益。
runner初末双验SHA；447联合tests通过。独立保存审计不等于raw5/Pine/live或收益验证。
项目.venv下182registry/boundary和130新测试通过；system Python扩展检查3项非数值
依赖版本不匹配已记QA，未改依赖；两环境numpy2.0.2/pandas2.3.3一致。
HTML analysis/html/p1_btcusdtp_hourly_structure_event_support_v25_20260907.html，
13sections14blocks1native月图；官方validation/package通过，structural_only，
browser/mobile/source-dialog未验证。首打包缺实际SQL失败已记录QA，修为真实SQLite
投影后打包，未动策略或结果。3条learning和audit/QA收据保存。

V25登记inconclusive仅指支持不足，不能说已证明亏损；不再读9笔收益或放松该门。
V24固定入口持续性not_supported、V19纯退出微调停止结论均保持。
下一步候选V26（未登记/未计算）：只把入口SMA40(HL2)改为VWMA40(HL2)，先重建
全机会与原SMA251 parity再审新支持；退出SMA/形态/20bp均冻结。详见V25目录
NEXT_EXPERIMENT.md；新增入口不能继承旧248三控。不是再扫MA20/30、EMA、
普通放量或prior20突破（V1等已测过）。未读取新配置/2025+价格/holdout，
无TV/训练/ACTIVE/实盘改动。goal active盈利未达成；保留并行ARB等未提交改动。

## ⚡ 当前真相（2026-09-07 — V24固定入口持续性已检验，拒绝）

6088258先提交label/随机抽样/统计/诊断/config/plan，再唯一真实运行两CLI。
V23支持通过后，单PCG64 seed20260907在完整二部组件内另抽744控制，先于raw读取。
251原母全部保留，219551行pre2025 OPEN前缀，3980labels/1004pairs。
1/4/12/24h原母各251known；配对248/248/247/247，3无支持保留自身标签。
唯一control缺2023-06-22 22:15UTC使12/24h unknown，不补抽、不两控均值。

主4h全母毛+1.0056bp，成本阈值−18.9944bp，95%CI[−30.7010,−7.1072]，p.9984。
248配对case−18.9073/control−23.6476/超额+4.7403bp，CI[−12.3611,23.8876]，p.314。
四半年均−12.7528/−24.8499/−16.3283/−20.3738，全部负；24monthsum/n推断not_supported。
1/12/24h全母成本阈值也负，不选24h当新主结果。固定时钟不是stop-managed可执行PnL。
4h140毛亏18费翻负93成本正；4h→24h52负转正但34正转负，未来组不能作入场门。

独立保存OPEN Decimal3980labels最大差0、1004pairs、744随机allocation/RNGstates验过；
10outputs6frozen12source16inputs哈希与时序过。另独立24月CI/p/2976control聚合过，
非原始源真实性或执行验证。408联合测试过；全部IQR尾部保留。
报告analysis/html/p1_btcusdtp_hourly_fixed_clock_v24_20260907.html；14章节+2原生图，
官方validation/package过，structural_only，浏览器/手机/来源弹窗未验。
report builder e0e0724先提交后构建；QA/NEXT_EXPERIMENT和learnings已记录。

下一步V25尚未登记或运行：只做“K1本根首次建立/翻转已确认结构”的保存特征支持审计，
不是V20持久同向状态；10/10定义和旧known/unknown不变。V24 NEXT_EXPERIMENT.md有
输入SHA与支持合同：先>=80accepted、每半年>=12、>=12months及每半年>=3months；
支持不足不读该门结果、不放松门。V20全hourly_trace范围能覆盖新744controls外边界，
仅查schema/time未评分事件门；要审V20历史resume来源链并逐请求因果字段再用。
V19纯退出微调停止不变；无TV/2025+价格/holdout/训练/ACTIVE/实盘变动。
goal active，盈利未达成，不得将V24负结果标成待优化或已稳定赚钱。

## ⚡ 当前真相（2026-09-07 — V23背景支持98.80%通过，未算新收益）

b696015先提交三键背景支持合同再运行，0d7283a随后提交纯固定时钟标签与报告builder。
仅读V10保存匹配特征和原V4母；251全保留，248完整组三控制=744不复用时钟，
3未知均2023-06-25因当前小时段此前ATR比例仅39/47/50<168，缺波动桶。
13192edges/8521候选/168组件，165非空均完整二部图；最小供给剩余8。
独立保存审计未调用原builder/allocator/MILP，逐边资格与风险/止损一致，
独立组件上界248=可行分配248。10CSV/6本轮源/5输入/14旧提交源已核。
四段52/55,66/66,55/55,75/75。旧V10六键154/251不追认通过，新问题是
整体K1相对同月/UTC6h/causalvol背景，不是独立形态因果效应。

联合支持/容量/标签277tests过，registry/boundaries182过；纯标签另55独立合成检查。
HTML analysis/html/p1_btcusdtp_hourly_background_support_v23_20260907.html已生成，
12blocks1nativechart，官方validation/package过、structural_only，未做手机/浏览器QA。
V23 accepted仅支持，不是随机经济样本或盈利。V19拒绝结论和纯退出微调停止不变。

下一步V24 fixed-clock研究正在实现：另注册seed20260907的组件内不放回随机配对，
必须在raw价格/标签前冻结；共享四时钟同一分配。全部251母自身label照算（含3未配对），
1/4/12/24h仅4h主，任何control缺失导致该三组比较未知，不重抽。
固定OPEN markout非K1stop可执行PnL；仅pre2025四fold，仍需独立时间/前向验证。
未读新markout/2025+价格/holdout，无TV/训练/ACTIVE/实盘变动，goal active盈利未达成。

## ⚡ 当前真相（2026-09-07 — V19风险减半已拒绝，停止该队列纯退出微调）

a776b5b先提交V19原单变量合同/engine/runner/verifier再真实回放。251原K1、
462controls/154triples/97unmatched不变；713fast1426slow上下文先冻结再读V18锚。
确认失败全平100%改同点减半50%，余仓走原slow15/K1stop/72h，20bp不变。
不是TP、不是V16/V18整笔收益平均；V16仅8项最终结构锚，实算两腿。

V19均净−16.3411bp（基准−16.7690），PF.6272（.5918），71赢180亏，四半年全负。
138riskhalf：38改善100恶化，16亏转赢；113不变。D251+.4279bp,p.3921,
CI[−2.5680,3.3036]；I154+2.9824bp,p.1307,CI[−1.8871,7.6416]，97未知保留。
138中99slow退出38改善61恶化；39hardstop全部恶化。26风险半仓取代V16晚盈利半仓
是未来事后标签，严禁作门。2case单仓各250且mask相同，均净−16.5407仍负。

独立账本verifier实际passed：33sources56outputs，原身份/两腿/费用/154triples/
2case单仓/48月会计/V16结构。非rawMA、独立pCI或live证明。另只读审计逐笔/极值/
26替代组；联合1064tests过2跳，系统统计工具27tests过。诊断全尾保留。
报告analysis/html/p1_btcusdtp_hourly_failed_reduce_v19_20260906.html已生成，18块1图，
官方validation/package过，structural_only；浏览器/手机/来源弹窗未验。
1e7019b只修reportbuilder收据路径，验证收据在E根不在frozen results内。

V19原停止条件已触发：不再3/4根确认或更多半仓比例。下一步单独注册V23背景支持
审计，详见V19/NEXT_EXPERIMENT.md。旧V10六键完整三配对容量最大154已证明，不是
种子/solver故障。新对照测整体K1相对month/UTC6h/causalvol背景，颜色/斜率只描述；
其余有效性/穿线排除/风险合法/72h禁入/3control/no reuse不改，先支持>=226才准
另注册固定时钟标签。不是修复旧合同，不得追认V19的90%匹配门通过。
V23目前实现中未读新收益；无2025+价格/holdout/TV/训练/ACTIVE/实盘/依赖改动。
goal active，盈利未达成；保留所有并行ARB等工作树改动。

### V22跨币变化门背景（已完成并拒绝，以下为当时记录）

f5a0e50先提交V22公式/config/plan，再唯一读取SHA锁定的V21保存小时trace，
没有新raw5/2025+价格。四币rank50相邻完整小时变化，仅direction*raw整数差>0
允许，不叠V21。自有K1OPEN T取外部OPEN T−1h/T−2h，52连续小时并集；
713自身上下文/62支持行先冻结才复用原V18退出。251/462/154/97与20bp全不变。

122accepted129abstain0unknown；23/37/29/33perhalf、23月支持通过。每笔净
−16.7690→−23.0341bp，PF.5918→.4662，四半年度毛净全负。122中23赢99亏，
99亏=80毛亏+19费翻负；75fastfailed/12hardstop/35slowexit。避97losers但漏32winners；
省2580eventbp费，却丢1181.1392eventbp毛，只净改善1398.8608。D251+5.5732bp,
CI[−2.2801,12.6209],p.0905；matched154D+7.2528/controlD+7.0770,
I+.1758bp,CI[−10.8010,10.5092],p.4899，97未知不删。单仓122均−23.0341；
matching61.35%仍低于90%。盈利半仓28笔23赢是持仓后标签，严禁用作入场门。

c84f12e诊断/报告builder、8752fea独立verifier先提交后实际执行。530联合测试过。
独立保存小时审计70168rows/713context/62support/154triples/24source/23CSVhash通过；
非raw5/Pine/live/经济独立回放。另只读账本审计旧字段、四单仓mask、40×17指标、
费用/退出/双腿已验；V18CSV往返仅净字段<1e−16差，守1e−12，不夸逐bit。
HTML：analysis/html/p1_btcusdtp_hourly_breadth_change_v22_20260907.html，
16块2native图，官方validation/package过、structural_only；浏览器/手机/来源弹窗未验。

V22已完成拒绝，勿重跑成待优化。下一步见V22/NEXT_EXPERIMENT.md：回到已准备未跑
的V19确认失败后50%风险减仓，先审prepared代码归属/契约、登记提交，再真实双腿回放；
不是按V16/V18全笔收益取平均。V19原停止条件不变：若净不正或jointD/I失败，结束
这批队列纯退出微调，改做独立观察时钟的入场持续性审计与足够对照。不继续扫描rank
阈值/周期/币池。本轮无TV/训练/ACTIVE/订单/依赖改动，所有并行ARB及V19 dirty保留。
当前goal工具active；盈利未达成，未标complete或blocked，旧V21交付状态记录仅历史。

### V21静态外部位置共振背景（已完成并拒绝，以下为当时记录）

ChartPrime 148项审查已完成；V20结构门已拒绝（背景如下）。V21固定ETH/SOL/BNB/XRP
四币native1h HL2 rank50等权均值方向门，不含BTC，不是多数投票或资金流。自有K1
开盘T仅取外部[T−1h,T)完整小时，51连续小时，缺任一币未知；入场仍T+1h。
6547a04先提交builder/config/plan再读取固定Binance5m pre2025价格前缀；四币各
210504行，70168小时状态，713自身上下文/62支持行先冻结，再读取原V18缓存退出。
原251case/462control/154triples/97unmatched、20bp/K1stop/72h/退出全部不变。

支持96accepted155abstain0unknown，4半年度21/26/18/31、23活跃月通过。每笔净
−16.7690→−21.3286bp、PF.5918→.5126、胜率21.91%→13.54%；96中13赢83亏。
漏42/55赢家，仍留83/196亏单，72亏单毛已亏、11费用翻负。57fastfailed、11hardstop、
28slowexit；拒绝组也亏，不可反转规则称盈利。唯一正2024H2均+7.7365bp，最大赢家
超过该段净总；其余30均−6.5720bp，仅集中度诊断，正式结果未删任何交易。
D251+8.6114bp，95%CI[.6625,16.2224]、p.0279；I154+1.1819bp，CI[-11.8664,13.5247]、
p.4287；97未知I不删。全251机会均−8.1576来自少交易减亏，不是保留质量改善。
单仓候选95笔均−21.9064bp；匹配61.35%仍不达90%，不降低门。

90b7070独立stdlib审计器先提交再执行，重建70168保存小时rank/713自身门/62支持行/
154triples及20source/24CSV哈希通过；不是raw5聚合、Pine、实盘延迟或经济独立回放。
另只读会计核对旧退出字段、四单仓mask、1240指标值、D/I及失败原因通过，不独立p/CI。
347项联合测试通过。9d9ba75诊断/报告builder先提交后运行；所有极端值保留。
HTML：analysis/html/p1_btcusdtp_hourly_breadth_v21_20260906.html，15块2原生图，官方
validation/package通过、structural_only；无浏览器/手机/来源弹窗验证。实验：
exp-btcusdtp-1h-external-breadth-preholdout-20260906-v21，已完成且拒绝，不是待重跑。

盈利目标未达成；交付时状态工具返回blocked，本轮未改变状态，不声称会自动继续。
下一步候选见V21/NEXT_EXPERIMENT.md：先查重旧日线广度变化，再冻结
同一外部均值上一完整小时的变化方向，独立对原V18、不叠V21、不并改退出。未评估，
不宣称有效；下一配置须先登记并提交builder/plan再读价格。仍需解决独立时间验证和
匹配覆盖不足，不能把重复开发期转正视为最终达标。本轮无2025+价格/holdout/TV/
训练/ACTIVE/实盘改动。V19仍prepared未跑；并行ARB/依赖/ML和V19 dirty引擎全部保留。

### V20确认结构共振背景（已完成并拒绝，以下为当时记录）

ChartPrime全148项源码审查已完成（背景如下），随后真正检验固定10/10持久小时
结构门。a70fb35提交builder/config/plan后才读pre2025价格前缀，713自身上下文及
18222小时状态先冻结；同一原251case/462control/154triples/97unmatched，V18
退出/K1stop/72h/20bp不变。137accepted/113abstain/1unknown，4半年度26/30/33/48，
24个月支持通过。oldK1K2多币结构家族早已失败，不称新因子或Pine逐点parity。

初次会计读取被known_5m_available的CSV字符串/UTC容器差异拦住，保留failure和
首次访问；ab77a68仅修精确时间解析并从原四张SHA冻结表恢复，无重读行情/特征。
cached退出会计：每笔净−16.7690→−17.2178bp，PF.5918→.6145，四半年度全部负。
全251保留，D250known=+7.3661bp,p.0207；I154=−4.0900bp,p.8354，97仍未知。
同154配对caseD+3.6724，而controlD+7.7624，不能将减仓少交易误称alpha。
避92losers4183.91eventbp，但漏21winners2342.38eventbp；接受137中34赢103亏，
91笔毛已亏（不全怪费用）；66fastfailed、17hardstop、54slowexit。结构龄中位59h，
128沿用旧方向、仅9当根新break，不是已证实的盘整禁入器。未按年龄再选门。

e2a3f2c独立stdlib verifier重建18222小时状态/713own门/62支持行/154triples，通过；
不独立验证raw5聚合/Pine/经济。另只读审阅独立复核137/309接受旧字段、四单仓mask、
40metrics/1240scalar与22CSV SHA；不独立p/CI或成交回放。相关300tests通过。
HTML交付：analysis/html/p1_btcusdtp_hourly_structure_v20_20260906.html，13块1原生图；
官方portable validation/package通过、structural_only，浏览器/手机/来源弹窗未验证。
实验：exp-btcusdtp-1h-confirmed-structure-preholdout-20260906-v20；结果原目录含失败。

整体goal仍active而非完成/blocked。下一步候选是跨资产真实新增信息，但先去重旧
BTC15m/ETH15m/山寨日线共振历史，不能包装成新发现；已有Binance5m五币在2023–2024
时间戳完整，OKX普通5m缓存并无该期；不同源只能作为显式外部输入，不能暗换BTC
原执行源。后续先冻结字段/池/完成时钟/缺失未知规则，再支持和固定对照检验。
本轮不读2025+价格/holdout，不更新TV/训练/ACTIVE/实盘，不改原匹配90%门。
V19仍仅prepared未跑；保留它的dirty引擎与其他并行ARB/依赖/ML工作，勿顺手提交。

### ChartPrime源码审查背景（已完成，以下为当时记录）

Owner 最新请求转为逐项研究 ChartPrime 主页全部指标，优先于 V19 的价格回放。
exp-chartprime-public-confluence-audit-20260906-v1 已冻结完整148发布ID：134公开源码
逐项审读、14受限只审官方说明，无遗漏/重复/采集错误。三份reviews按45/50/53分区，
134原始字节SHA、发布权限/脚本ID/版本/URL与独立目录联结、引用行界全部通过。
6ebad6b采集builder先提交后采集；fee0dec报告builder先提交后构建，30本轮tests通过。
静态语义审查不是Pine编译/实时完整运行验证，部分外部库未递归展开，条目显式注明。

交付：analysis/html/p1_chartprime_public_confluence_audit_20260906.html，165原生块、
148逐项说明、1分类图/1完整索引。官方portable验证/打包通过，structural_only；
未装headless浏览器，CUA本地file预览被安全策略阻止，未绕过；手机/theme/来源弹窗未验。
主要结论：优先验证已确认结构、跨资产广度、量权成本区/相对量字段的增量，不凑MA票。
原MA Shift是HL2对SMA40侧色≠斜率/osc色；offset=-1显示≠真实可用时间；未偏移
HTF lookahead_on仅按受影响字段判泄漏；delta/liquidation多数是OHLCV代理。
Multi Asset Histogram确有10外部资产输入，但需固定事前币池、数据源/完成时钟、
去本币和缺历史处理，不能称10独立因子或已获超额。默认参数均非“最优”。

本轮未读价格/holdout、未回测、未保存TV/改样式、未训练/ACTIVE/实盘操作。
盈利goal仍active且未达成。下一步若继续实现，先冻结一个共振字段，核对与已测
入口家族重复性与支持度，再按时间/全机会/成本/匹配对照单变量验证；不并改退出。
V19 failed-reduce的引擎/runner/verifier/合成tests与plan/config已准备在工作树，
但未提交实验、未登记、未跑任何真实价格；勿称已盈利或按V18线性拼收益。
不要覆盖并行ARB/其它未提交工作；未动生产/依赖/分支。

### 第十八轮背景（已完成并拒绝，以下为当时记录）

盈利goal仍active/未达成、不是blocked。cbb25d3先提交builder/config/plan/tests后
唯一raw回放pre2025前缀219551行含pre2023warmup9024；交易评估2023–2024旧开发集。
全251/462/154triples/97unknown保留，713fast/1426slow先冻结再读取旧结果。
OFF V17六表全旧字段复现，候选99case/140control非旧full路径不变，逐臂重算单仓。

仅亏损fast full延后到相邻第二根完整native5仍反色且新actualopen未赚够20bp，
慢15m仍同向才确认全平；盈利真edge50%和slow余仓/K1stop/20bp/72h不变。
均净−17.0782→−16.7690bp，PF.5716→.5918，四半年全负，D+.3092bp,p.414；
I154+.6462bp,p.3935，却来自匹配case−.2950而control−.9412，不能称病例改善。
152旧full建180pending=138确认+33取消+9优先终止。70改善81更差100同收益，
其中99同路径。4亏转盈+425.649eventbp抵196继续亏−348.049，51赢家不变。
138仍full增量−231.186、3等待stop−48.865、11slow+357.651。原V16被切22赢家
只救4，18仍亏；31被切partial只恢复5。V18仍比V16均净低1.5751bp。
实际serial250均净−16.9703bp，全251机会未选置零均净−16.9027，不混分母。

82a3651saved verifier/report/notebook已提交后执行；55output/30source/6anchor/
2245trueedge/1110lifecycle/48monthly/费用-D-I-serial审计通过，非raw/SMA/漏记edge/p
独立回放。07ab093PATH_QUERIES提交后saved-only归因。最终351tests过2utilityskip；
system27诊断tests全过、实际工具执行，未装包。直接pytest路径失败后改python-m
正确本仓import；未改代码救测试。扩展3754过2既有L1hash失败未动。Notebook3格
plainPython，pin三verifier；HTML19块1native图structural_only、未手机/theme浏览器QA。

交付：analysis/html/p1_btcusdtp_hourly_failed_confirm_v18_20260906.html。
V18已完成拒绝，勿重跑成待完成。下一项见V18/NEXT_EXPERIMENT.md：保持两根确认
只把确认后的100%全平改50%风险减仓，余仓原slow15/K1stop/72h，任何半仓后快腿
仅一次；第二open恢复>20bp仍只取消，不造无edge半仓。不是V1slow状态半仓、V2
固定R或V16盈利edge半仓，不能线性拼旧收益。尚未实现/登记/评估下一配置；先冻结
新builder/计划/测试并提交再真实全体回放。覆盖61.35%不足90%仍不降门。
本轮无2025+价格/holdout/TV/训练/ACTIVE/实盘改动，不能承诺盈利。

### 第十七轮背景（已完成并拒绝，以下为当时记录）

盈利goal仍active/未达成、不是blocked。V17 e5139c5先提交engine/config/plan/tests；
attempt1 OFF case表被CSV两source ID类型parity拦住，ON未跑，9文件原样保存attempts。
只修读入时文本转换不松parity/不改策略，10af2c7提交修复及verifier/report/notebook，
attempt2同pre2025原价格前缀完成、candidate首次执行；交易评估2023–2024，另含2022年
9024预热行（timestamp-only末审确认始于2022-11-30 16:00UTC）。251cases/462owncontrols/
154triples/97unknown不变，713fast和1426slow初态先冻结，OFF V16六表全旧字段parity。

唯一变化：V16尚未partial，完整fast5m真翻反、latest完整slow15m仍同向且实际open
毛利<=20bp时全平；>20bp仍原50%兑现和slow余仓。没有移动K1stop或改变20bp/72h。
均净−15.1939→−17.0782bp，PF.6796→.5716，四半年全负，D−1.8843bp,p.6922；
I154−7.1906bp,p.8612，97未知仍在。152全平106改善46恶化，22旧赢家转亏，全属于
31条原稍后partial路径；31条−3085.2460eventbp抵掉其他121全平+2612.2985。原90partial
剩59；32新全平前已见>=1R，不能说都没启动。每臂单仓重算后恰好仍250笔，候选均净
−17.2807bp。不要只数hardstop63→21；新200亏/51赢，少止损不等于赚钱。

Saved独立验证54output/27committedsource/6anchor/2945recordededge/48monthly/全部
D-I-serial通过；不是raw/SMA/遗漏edge/p独立重跑。V17联合319tests过；扩展3600过
2既有L1migration hash失败未动。66f0ac8统计诊断builder提交后system python3真实工具
运行：SD59.5442bp、56IQR极端值全留，不改推断。system27tests全过，.venv25过2可选
tool集成skip，依赖没改。Notebook3格plainPython、pin两verifier；HTML16块1native图
structural_only，没有mobile/theme/source-dialog浏览器QA。无holdout/TV/训练/实盘改动。

交付：analysis/html/p1_btcusdtp_hourly_failed_launch_v17_20260906.html。
V17已完成并拒绝，不是待跑。下一假说见V17/NEXT_EXPERIMENT.md：仅将亏损fast全平
改两根连续原生5m反色确认；原盈利partial不延迟。第二根重新核slow与actualopen，
不合格取消pending、不latch/不造无edge半仓。V1已有slow15两根确认（不能说从未试过），
V9抽样/V12冻结小时MA不是此臂；暂无等价V1–V17组合。仍需新冻结/注册/提交后真实回放
全251/462，不能日志拼收益。下一项尚未实现或跑收益。61.35%覆盖仍不足90%，不降门。

### 第十六轮背景（已完成，以下为当时记录，勿重复）

盈利goal仍active/未达成、不是blocked。c75aae5先提交builder/config/plan/engine/tests，
随后唯一V16原2023–2024价格回放；同251cases/462owncontrols/154triples/97unmatched。
基准V15native15真实翻色不变；只加完整5m真翻色、latest完整15m同向且实际open毛利
严格>20bp时兑现50%，余仓原规则/K1stop/72h，未移动止损。快713和慢两臂1426初态
先冻结再读旧收益；V15六表全旧字段复现，713最终路径和单仓占用完全不变。

均净−16.6624→−15.1939bp，PF.6658→.6796，四半年全部负；D+1.4684bp,p.1156；
I154+2.4747bp,p.2339，97未知仍留。单仓250笔均净−15.3889bp。90半仓57改善33少赚，
净救回9旧亏；161未半仓160亏1赢、均净−72.0025bp。128有快事件却均无>20bp合格open，
其中106所有事件open毛利≤0、22曾正≤20bp；另33未评估快edge（高优先级全平可截断）。
不得说106从未浮盈，也不得把90条件子集+86.43bp当入口能力。31最终半仓者此前已有
不合格edge，下一失败早退必须统计错杀恢复赢家。全部251快流reset0，非缺数据漏兑现。

ecce7d5独立saved验证54output/24策略committed来源、6anchor、快慢上下文、1895记录
edge/加权20bp/全部D-I-单仓通过，非第二raw/SMA/完整edge/p验证。V16联合270 tests过；
扩展快照3365过2既有L1迁移hash失败，未修改他人文件/门。Notebook3格plainPython，
单独pin自足verifier；不是Jupyter/fullschema。官方HTML15块/1native图structural_only，
未mobile/theme/source-dialog浏览器QA；无新依赖/价格holdout/TV/训练/实盘改动。

交付：analysis/html/p1_btcusdtp_hourly_dual_partial_v16_20260906.html。
V16已完成并拒绝升级，不是待运行。下一项见V16/NEXT_EXPERIMENT.md：只加半仓前
完整5m真反转且slow仍同向、open毛利未超过既有20bp时全平失败启动的分支；其余不变。
必须先bounded排重/冻结/提交再回放全251/462，不只挑事后128亏单；重算变化后的单仓。
下一实验尚未实现/登记/运行。61.35%覆盖失败仍需独立证据方案，不能删97来提高比例。

### 第十五轮背景（已完成，勿重复）

盈利goal仍active/未达成、不是blocked。V15先提交2acc280再唯一一次raw回放原2023–2024；
保持原251cases/462owncontrols/154triples/97unmatched，无入场门。原生5m→15m SMA40
真实翻色同时改变聚合/seed/记忆，不是纯晚退。1426自身初态先冻结才读收益，V5六表
全部旧字段基准parity通过。均净−14.3066→−16.6624bp，四半年皆负；D−2.3557bp,p.7302；
I154+.5946bp,p.4696，97unknown不可删。15m实际单仓250笔均净−16.9934bp，不接受升级。

失败分解：19亏转盈、17盈转亏、170持续亏平均更深；50旧colour→新硬止损抵消多数
colour路径改善，硬止损14→63。187新亏中53heldMFE≥1R、134不足；3个≥1R最大毛浮盈
仍未覆盖20bp，不能MFE当成交。15m初态反向32贡献多数增量恶化，但同向219仍亏；
48/53回吐已同向，不能宣称过滤初态就赚钱。当前源码旧state15语义232/251同净、19差，
非完整V1历史账本复现。逐笔原因/反例/所有失败已进HTML，不只呈现赢家。

45c1d23独立saved验证54output/21committed来源、时钟/账本/D/I/单仓/上下文通过；非第二
raw/SMA/firstedge/p重算。最终V15新增233tests过；扩展3194过2迁移hash失败（非本轮
L1 candidates/render），不修他人改动/不改门。Notebook3格plainPython、非Jupyter/fullschema，
当前pinV15→V12→V11，复用同verifier。HTML16块/1nativeSQL分箱，structural_only未mobileQA。

交付：analysis/html/p1_btcusdtp_hourly_native_exit_v15_20260906.html。
下一项详见V15/NEXT_EXPERIMENT.md：在原native15完整251上，仅增加完整5m真翻色且
完整15m仍同向、可成交open毛利覆盖成本时先兑现50%；余仓原规则，不移动止损/加门。
先查重/冻结/提交再回放，不能旧MFE拼收益。下一实验尚未登记/实现/运行，继续goal。
无2025+价格评估/holdout0/训练/TV/实盘改动。V15不得重复包装成尚未测试。

### 第十四轮背景（已完成，勿重复）

盈利goal仍active、未达成、不是blocked。本轮先提交5fc542b的builder/config/plan/tests，
再09:43UTC真实加载原2023–2024前缀219551行。V14只测原251case/462owncontrols上的
此前20完整连续小时严格突破，K1自身排除，不叠4h颜色或小时斜率。60case通过、191弃、
0未知；controls53/409/0。病例四半年17/15/11/17，总60<80且最少11<12，23月/min5月
通过月份门。严格停止于支持阶段，没有读旧收益表、没有新收益回放/排名，不能说60笔亏。
原154triples/97unmatched不变，所有154来源已知不等于251匹配覆盖达到90%。

冻结713门+14973自身小时来源。bbc1c63独立保存验证器重算prior20 extrema、K1close、
窗口时钟/严格门/4输出hash/11committed源码/62分维度计数/154triples，全通过；不冒充
raw5聚合重跑。3057相关tests通过44.02s。Notebook3格plainPython，复用同验证器，
非第二独立验证、非Jupyter/fullschema。HTML12块/1真实SQL半年计数图，官方structural_only，
没有手机/来源弹窗浏览器QA。无2025+价格物化/holdout0/训练/TV/实盘改动。

交付：analysis/html/p1_btcusdtp_hourly_prior_breakout_v14_20260906.html。
实验：experiments/active/exp-btcusdtp-1h-prior20-breakout-preholdout-20260906-v14/。
下一项先排重后提案：同原251/462/154，唯一native5m→native15m trueflip规格。V1已有
原251的15m状态退出，V8已有不同286cases的native15m trueflip；不得称从未试过。先冻
两周期全部713初始状态，若15mtrueflip与旧状态路径恒等就报告语义复核，不换名创新。
Native改变聚合/时钟/MA记忆，不是纯退出频率效应；heldMFE低不证明旧退出后没有趋势。
详见NEXT_EXPERIMENT，尚未实现/登记/运行下一实验。继续盈利goal，不降低门救结果。

### 第十三轮背景（已完成，勿重复）

盈利goal仍active、未完成。本轮有实际进展：d3366d3先提交builder/config/plan/tests，
再首跑V13原2023–2024价格前缀219551行，无2025+价格物化/holdout0。唯一变化是
K1 OPEN前最近完整4h HL2/SMA40颜色同向准入；40连续4h足够，不夹带43根斜率预热。
保留原251病例/462控制/154triples、K1stop/20bp/72h和V5native5m trueflip，不叠V12出口。
先冻结713 owncontextgate，再全旧六表全字段parity，候选实际回放accepted且旧字段全同。

病例121accepted/127abstain/3unknown，控制311/151/0。Actual121净−18.9541bp，PF.5476，
30胜91亏，四半年全负；并未提升交易质量。已知248含127弃单0均−9.2478bp，不能和原
全251均−14.3066直接相减。同248before−14.1018，D+4.8540，CI[−2.309,11.427]p=.0927。
95避亏抵4405.14事件bp，但32漏赢损3201.34；127被弃本也净−9.4787，不可反转门称盈利。
I154−1.6531bp，CI[−11.453,7.333]p=.6295，原97仍未知；控制改善更多。3未知全warmup，
原也未匹配。单仓firstunknown占72h，其余2unknown被阻塞；独立episode仍NaN，serial
只1unknown/250known，249被处理不等于249交易，实际121无变。91亏里75heldMFE<1R、
83毛非正、8成本翻负；不是大多数只差止盈。原14硬止损仍留11，却漏32/62原赢家。

独立保存验证器7c56ef3核51输出/20源码hash、三态费用、D/I/串行/48月表；不重放raw
或独立重算p。最终2879相关tests通过。notebook5格plainPython，1145交易成本/432保留
旧字段/713门/D通过；非Jupyter/fullschema。HTML16块/1真实SQL全251分布，官方
structural_only，无Chromium，手机/来源弹窗视觉未验证。详见V13 VERIFICATION。

交付：analysis/html/p1_btcusdtp_hourly_prior_colour_v13_20260906.html。
实验：experiments/active/exp-btcusdtp-1h-prior4h-colour-preholdout-20260906-v13/。
下一步已只读排重：require_breakout20在V1随已选1h斜率+旧colour测试过，111→16净−40.557，
失败保留；max_cross_count/min_efficiency也做过。只可单独复核原251/V5trueflip上prior20
结构突破，不叠本次4h门、不扫N；先因果支持数，不够80/min12则不靠看收益救参数。
NEXT_EXPERIMENT仅提案，尚未登记/实现/运行。新鲜独立验证与90%覆盖未解决，不自动上线。

### 第十二轮背景（已完成，勿重复）

盈利goal仍active、未完成。本轮是实际进展：c5ce2f2先提交builder/config/plan/tests再回放；
V12仅在原V5 native5m trueflip上加frozen_ma_exit，K1自身1hSMA40冻结边界，完整持仓5m
收盘严格错侧后下一真实open退出。251原病例/462控制/154triples、entry/K1stop/20bp/72h
不变，无V11限时规则。只有原2023–2024价格前缀219551行，无2025+价格物化，holdout0。
先核713自身小时MA/close和保存几何，再全六张旧表所有旧列parity，之后才算候选。

新净−13.9493bp（旧−14.3066）、PF.6306、62赢家/189亏不变。新增20退出均原亏单，
10改善10恶化、231不变；D251+.3573bp，CI[−.340,+1.073]、p=.1846。17原变色/3原硬
止损被提前，平均提前23min；31结构触发含11同刻原颜色优先。未改231仍净−8.625bp。
154匹配病例仅改善.1441bp，控制恶化1.1995bp，I154+1.3436bp，CI[−.400,+3.750]、
p=.1324，97未知；不能把控制变差说成病例赚钱。四半年全负，90%覆盖/净/PF等仍拒绝。
全部251病例g在(0,1)，控制10g<0、190inside、262超过止损；不改变对照，不外推纯形态。

独立保存验证器cafc14d已过52输出/18源码hash、1426交易公式、713几何、48月表及原配对；
不重放raw/不重算p。最终2534相关测试通过。189亏里161终局毛非正、28成本翻负；重叠
35曾到1R亏单≠互斥14giveback。facts在实际生成前修paired分母和正负例子标记，未改收益。
HTML15块/1实际SQL计数图，官方structural_only；notebook13格/5代码格plainPython过，
非Jupyter/fullschema。完整来源/范围：V12 VERIFICATION；没有TV/训练/部署/真金改动。

交付：analysis/html/p1_btcusdtp_hourly_frozen_ma_v12_20260906.html。
实验：experiments/active/exp-btcusdtp-1h-frozen-ma-exit-preholdout-20260906-v12/。
下一步已只读排重：1h斜率V1/V3做过；V3的4h门是颜色AND斜率、248共同支持、旧colour
退出，不是纯颜色门。下一提案只测K1 OPEN前完整4h颜色与方向一致，保留原251机会、
各控制独立门、未知非0，不夹带43根斜率热身；详见NEXT_EXPERIMENT。尚未登记/实现/运行。
不继续围绕V12边界调阈值；新鲜验证和原匹配支持仍未解决，不承诺盈利或自动上线。

### 第十一轮背景（已完成，勿重复）

盈利goal仍active，未完成。V11 builder/config/plan先提交33f31f0，再首跑原2023–2024
开发价格219551行，最后2024-12-31 23:55 UTC，无2025+价格物化，holdout0。固定原V5
direct K1全部251病例/462控制/154组三对照，原六表全部旧字段parity后才计算候选。
唯一新增：前60min完整5m CLOSE从未达到0.5初始R则退出；达到过永久取消期限。
entry/K1stop/原生5mSMA40 trueflip/20bp/72h不变，不改入口、不重配控制。

新净−13.4534bp（旧−14.3066），PF.6007（旧.6247），赢51（旧62），14硬止损不变。
127超时承担全部变化，80改善47恶化124不变；15盈转亏、4亏转盈、185皆亏、47皆赢。
D251=+.8533bp，CI[−4.056,+5.088]、p=.3640。匹配154病例反而恶化1.3325bp，
97未匹配改善4.3234bp；不能用全251D减154组控制变化。正确I154=−.5983bp，
CI[−7.549,+5.560]、p=.5682，97仍未知。新超额+8.5483bp不等于净盈利。
四半年新净−14.99/−24.84/−13.01/−2.63bp，全负；两版单仓251意图/251成交、0跳过。
全部财务门拒绝，61.35%覆盖门也未通过。没有TradingView/训练/部署/真金变更。

独立保存账本验证器2c332ed核49输出hash、15首次commit源码、1426成交公式/时钟、
251/154配对及48月表；不是第二引擎raw重放，也未独立重算p。首验证器的asymmetric
merge列误报已修，不改结果、不重跑价格；120验证器测试，最终2068相关测试通过。
200亏损最终163毛非正、37成本翻负，但终局不说明途中浮盈；互斥10回吐不是所有≥1R。

交付：analysis/html/p1_btcusdtp_hourly_launch_v11_20260906.html。
实验：experiments/active/exp-btcusdtp-1h-launch-deadline-preholdout-20260906-v11/。
报告16块/1实际SQL分布图；官方HTML structural_only，无浏览器视觉/手机交互验证。
附属notebook普通Python4代码格通过，非Jupyter/fullschema；完整范围见VERIFICATION。

下一步不沿60min阈值追赢家：先排重“完成的小周期收盘破坏K1冻结MA边界”这一结构
失效假设，与已有5mtrueflip、等待同向、源区规则比较；若确有独立含义再冻结单变量。
这个下一实验尚未登记/实现/运行，不可口头算完成。开发样本重复使用、原匹配支持和
新鲜独立验证缺口仍在；不得降20bp/90%门、把旧后续价格当新鲜样本或承诺盈利。

### 第十轮背景（已完成，勿重复）

V10 builder/config/plan/test先提交b6e9056再首跑；仅原2023–2024价格前缀219551行，
没有2025+价格物化/holdout0。全部251原1h穿MA母、462旧控制、分配表所有字段和
四份receipt哈希先parity，然后保存完整图再求解。无新收益/训练/TV/实盘变更。

最大154/251=61.35%，等于旧贪心154；原要求90%需226，差72且不可通过重排增加。
251母/1666候选时刻/1829合法边/231组件；独立组件上界154=旧可行下界154，MILP
同证实154。12输出哈希+14源码commit哈希由2129bca标准库验证器独立核验，不读raw。
97缺配=3母缺ATR桶+19六键后不足+1期末边界+71穿线排除后不足+3共享供给冲突。
3冲突都是2母共用3/4/4候选，换人不增总量；最大解仅换入1母，不用于重算收益。
同向小时斜率111母103匹配，反向140母51匹配；不能把条件超额外推全251或据此当盈利门。
排除条件重叠：2640母×候选机会−811穿线=1829边，294实际母排除包含在811里，不再减。

报告：analysis/html/p1_btcusdtp_hourly_support_v10_20260906.html。
入口：experiments/active/exp-btcusdtp-1h-matching-support-preholdout-20260906-v10/。
HTML官方工具结构通过，无兼容Chromium，手机/交互未验证；notebook验证范围见VERIFICATION。
修正旧V8/V9三条artifact_type为合法report，未修改旧报告/结果/费用/门；不放宽schema。

下一步停止调配对器。回到启动持续性/早期结构失效的单变量机制；先排除与V5反色、
V6等待同向、V2分批、V8/V9慢管理重复。全251同机会策略变化与154条件对照变化分开，
不得绕过90%覆盖门宣称全系统有效；新鲜独立验证仍未获得。该交易实验尚未登记/运行。
下一候选已排重为：固定V5原251入口，前60min完整5m收盘从未达到+0.5初始R则超时退出；
达到过则永久停用此早退、回到原trueflip。仅1组先验不做网格，详见V10 NEXT_EXPERIMENT。
盈利goal仍active且未达到，V9财务失败及旧动机完整保留在下，勿重复本次支持审计。

### 第九轮背景（已完成，勿重复）

Owner盈利goal仍active。V9 prereg/config/builder/tests先在12df0a0提交，再首次运行；
旧2023–2024开发期含事前预热共219551价格行，截止2024-12-31 23:55 UTC，holdout0。
原生5mSMA40完全不变，仅决策检查5m→15m；raw5风险和质量仍每5m。固定V7全部
286case/849control/959zone，入场时价/方向/K1stop/ATR/risk/20bp/72h与控制分配全不变。
旧V7六表全旧字段parity通过；两臂context逐字段及解压CSV字节一致，所有2270结果闭合。

新净−16.4331bp（旧−17.7878），PF.6380（旧.6087），胜75/286（旧72），SL26（旧23）。
D286=+1.3547bp，CI[−1.596,+5.043]，p=.2269；I283=+.8283bp，CI[−2.413,+4.375]，
p=.3229，3未匹配仍NaN。新配对case−16.1045/control−21.9751，超额+5.8706bp、p=.1160。
四半年新病例−21.24/−23.96/+3.61/−26.17bp，只有一段正；完整财务与改善门拒绝。
207两版皆亏、7亏转盈、4盈转亏、68皆赢。211新亏单只有27笔在实际持仓曾≥1R
（重叠标签14giveback+12costflip+1hardstop）；不能将所有亏损归因于止盈没拿住。
两版单仓都959意图/286交易、0跳过，意图均值−5.3048→−4.9008bp，不是删单改善。

独立CSV验证器046b92a核验2270收益/成本/时钟、286/283/959配对和独立单仓循环。
最终1342测试通过；三路只读代码/保存账本/报告复核无阻断。不是独立原始行情第二重放。
报告builder78f7243；HTML结构验证通过，但无兼容浏览器，手机视觉和交互未验证。
交付：analysis/html/p1_btcusdtp_hourly_cadence_v9_20260906.html；旧V1–V8原样保留。
实验：experiments/active/exp-btcusdtp-1h-decision-cadence-preholdout-20260906-v9/。

下一步仍未登记/运行：回到owner原“1h大实体/真实吞没穿MA直接入场”的入口家族，
先诊断原251case只有154匹配（61.35%）的事前支持不足，再设计入场持续性实验；不得
偷偷放松核心匹配、筛未来赢家/MFE，或重命名旧共振/分批失败规则再跑。V7–V9是源区
突破机制支线，不是原入口全系统的盈利验收。已有后续价格不是干净独立验证样本。
保存事前账本已复核：154匹配/94不足/3因果ATR桶缺失；同向小时斜率111中103匹配，
反向或平140中仅51匹配，故不能把154条件超额外推到251。下一轮先做无收益支持审计：
固定原匹配键/三控制/不复用，保存完整候选供给与排除边，求最大可覆盖母数；90%需226。
完整matching_frame未留存，94不足暂不能拆成原始稀疏、穿线排除或分配耗尽；不得猜因果。
笔记：docs/learnings/matching-coverage-can-select-a-different-entry-population.md。
无TradingView、ACTIVE/frozen、VPS/forward、训练、部署或真金变更。盈利目标未完成。

### 第八轮背景（已完成，勿重复）

Owner盈利goal仍active。V8 prereg+builder在e460539先提交，成功首跑仅旧开发2023–2024，
无2025+价格／holdout0。固定V7全部286请求、849控制、959源区，原生5mSMA40 trueflip
换原生15mSMA40 trueflip；硬止损仍逐5m，原入场时价/方向/K1stop/ATR/risk/20bp/72h全固定。
5m全部旧trade/episode/pair/serial字段parity通过后才算15m。所有2270结果闭合。

新净−19.9065bp（旧−17.7878），PF.6812（旧.6087），胜67/286（旧72），硬止损86（旧23）。
D相同286均值−2.1187bp，CI[−15.113,+10.221]，p=.6196；73改善/188恶化/25不变。
I相同283三控制组均值−3.0774bp，CI[−18.145,+11.156]，p=.6498；3未配对保留NaN。
新283配对case−19.2993/control−21.2642，超额+1.9649bp、p=.4206。四半年仅2024H1正。
旧赢→新亏29、旧亏→新赢24；63旧翻色退出改为硬止损，平均额外亏45.90bp。
慢管理有部分大赢家，但190持续亏单平均更亏22.36bp，不能只报PF及右尾。

新单仓908/959源区、262交易净−15.0081bp；51被占位挡住的区含24请求（20亏4赢）。
原959固定意图净−5.3048→−4.1002bp、变化+1.2046bp，CI仍跨0；不能换分母宣称D改善。
病例15m初态282同/4反，控制640同/209反，旧5m全同；保持原5m匹配未重配。
这是聚合/颜色时钟/MA记忆3h20m→10h共同变化，不是孤立检查频率因果。

下一项尚未登记／运行：保持原5mSMA40记忆，只把其决策采样到完整15m收盘，分离
检查节奏与均线滞后。需写清phase、初态、缺口重置、独立raw5风险时钟，不能只改参数字符串；
维持286/849/959同源配对及完整旧版parity。若仍无净优势，回到启动持续性/入口质量，
不得筛未来200翻色退出／24亏转盈／58曾1R回吐单作为好样本，也不重做旧分批规则冒充新发现。
既有2025后续段不是干净独立样本，任何正开发都仅允许冻结后新鲜前向验证。

实验 `experiments/active/exp-btcusdtp-1h-management-spec-preholdout-20260906-v8/`。
单独报告 `analysis/html/p1_btcusdtp_hourly_management_v8_20260906.html`；前七轮报告原样保留。
无TradingView、ACTIVE/frozen、VPS/forward、训练、部署或真金变更。最终验证见V8 VERIFICATION。

### 第七轮背景（已完成，勿重复）

Owner 盈利 goal 仍active。V7完整builder/config/plan/tests先提交ad8f5c8，再首次读取
2023–2024开发前缀219551行；2025+价格未打开、holdout0。四段生成959个冻结源区：
187到期、486首突破不合格、286合格请求全闭合。StageA支持门全通过才计算收益。
病例286笔净−17.7878bp/PF.6087/胜率25.17%，四半年−26.28/−25.19/+5.18/−27.29bp。
匹配283/286=98.95%，849控制全闭合；配对case−17.4129/control−22.4553，超额+5.0424bp，
CI[−3.871,+14.019]、月块p=.1514。全286笔均可单仓执行，净收益不变，不可上线。
入场全部5min同向，无5min立即退出；214亏损中23硬止损、23费用翻负，重叠28笔曾1R。
旧反色故障已排除，不能再用它解释本版。毛均值仅+2.2122bp，不覆盖20bp预设成本。

源区规则：4+4小时内含冻结后4边界，首8h收盘出界即消费，形态不符也消费；新源仅在
因果终态后积8新小时。入口家族替换去掉SMAcross/hourlycolour，不是孤立数值因果对比。
下一项待登记：保持所有286请求/止损/真实open/对照分配/成本，仅5min truecolour管理
换原生15min truecolour管理；重放相同849控制并检查单仓占位，不能筛未来MFE或仅28回吐单。
这是管理规格替换（40根记忆3h20m→10h、聚合/确认时点同变），不是纯检查频率因果。
保留既有5m匹配，另报15m初始同/反/未知状态，不事后挑对照；持仓外后续趋势仍未知。
该项尚未运行，不能声称15min盈利。新旧机会不同，勿将V5的251笔与V7做逐笔配对。

实验 `experiments/active/exp-btcusdtp-1h-frozen-source-preholdout-20260906-v7/`。
完整报告 `analysis/html/p1_btcusdtp_hourly_impulse_ltf_exit_20260906.html`。
运行前733测试PASS；交付测试/复核见实验DELIVERY.md/VERIFICATION.md。
没有TradingView、ACTIVE/frozen、VPS/forward、训练、部署或真金变更。

### 第六轮背景（已完成，勿重复）

Owner 盈利 goal 保持 active，尚未完成。V6 builder/config 先在5e70584提交，首跑因旧CSV
与Timestamp类型parity停止；保留九文件，仅修比较类型后提交824d2d1才成功运行。
独立复核两次八个派生输入逐字节一致，所有旧结果列恒等，原251案例母／462控制母保留。

等待首个完成5m同向后下一open入场，继承K1止损、母+8h等待／+72h总期限、20bp及V5翻色退出：
55旧成交→47次请求／46成交／1风险拒单，另8到期；净均值−22.45→−30.42bp，四半年均负。
同46共同成交旧−24.23→新−30.42bp；24笔真实延迟全部亏，等候中位50min、平均追价13.01bp。
其中19笔退出时价完全相同，晚入场额外损296.11事件bp；记录MFE毛bp中位15.54→8.15，20/24
不足20bp成本。不是只由R分母变化造成。少7个硬止损却少5个赢家，不能宣称改善。
全母配对−.657bp，95%月簇CI[−1.946,+.454]，p=.8377；匹配154/251=61.35%仍不足90%门。
新单仓45笔−30.09bp。14 source/config与10父输入哈希、230完成交易时钟／成本独立复核通过。
测试589项通过；本轮两次均只物化219551行至2024末，未读2025+价格／holdout0。

下一步只优先预注册**事前冻结源区、首次释放一次消费的入口生成器**，不再筛原55个K2。
泛化压缩／放量／n20突破／三票共振在V1与ETH/XAU和旧研究中已测过，不能重命名为新发现。
待验证起点：前后4+4完整1h包络内含时冻结后4上下界，最多8h首根收盘出界就消费；只有
该第一根满足原大实体／吞没且实体跨冻结界才发K1，形态不符也消费，不准后移选好看的K。
退出／止损／成本先固定；源区重新武装只用消费后的历史。先设计支持与匹配，再看收益。
新旧母池不同，不能冒充原251逐笔配对。已有2025后续段不再当干净独立样本；正开发只许可
继续冻结规则的前向验证。不要重复V5/V6、不要用未来MFE或未来会恢复同向挑入场。

实验 `experiments/active/exp-btcusdtp-1h-flat-realignment-preholdout-20260906-v6/`。
完整报告 `analysis/html/p1_btcusdtp_hourly_impulse_ltf_exit_20260906.html`。
没有TradingView、ACTIVE/frozen、VPS/forward、训练、部署或真金变更。

### 第五轮背景（已完成，勿重复）

Owner“直接去做，要把这套系统做到赚钱”的 goal 保持 active。V5 在 `af9d4d8` 提交后才运行，
固定 V4 母请求/控制，单改5m反色状态→相邻同向到反向退出；K1止损、20bp、母72h不变。
直接251笔：247入场已同向恒等；另4笔1改善3恶化，净−14.02→−14.31bp。
K2 55笔：22同向/33已反向，原27笔5min离场全部来自已反向组且正好是27笔改动；
12改善15恶化、净−22.92→−22.45bp，胜率4/55→8/55但硬止损2→11，四半年仍负。
新增9个硬止损均在小周期首次恢复同向前发生。两组匹配覆盖仍154/251=61.35%；
新K2对照超额−1.31bp/母，p=.6897。已同向组毛收益direct+6.12/K2+.55bp也不足20bp。

下一步：**单独预注册空仓等小周期恢复才入场**，不是持仓等；继承母K1+8h等待界、
母+72h总时限、K1止损，确认后真实下一open重算入场价/风险。不能直接抽取未来会武装的24笔，
不能仅删原本反色来宣称盈利。所有原母、过期/失效零值、未知值及错过赢家必须保留。
若只机制比较仍沿用61.35%匹配，明示不能通过90%门；正式候选需提前冻结充分匹配设计。

V5 receipt仅219551行到2024末；未读2025+价格、holdout0。独立核查857旧请求全部列恒等，
1672闭合case/control的成本/时限/回报公式通过，42无效风险请求为同21控制重复两模式。
443 tests PASS（104新增），结果与完整HTML已保存；浏览器/手机视口QA未通过（仅结构通过）。
没有TradingView、ACTIVE/frozen/VPS/forward/训练/部署/订单变更。
实验 `experiments/active/exp-btcusdtp-1h-colour-transition-preholdout-20260906-v5/`；
报告 `analysis/html/p1_btcusdtp_hourly_impulse_ltf_exit_20260906.html`。

### 前四轮背景（均已完成，勿重复当新实验）

Owner 最新要求“直接去做，要把这套系统做到赚钱”，目标保持 active，未宣称完成。
V1 已实现 1h 大实体／真实吞没穿 SMA40、下一 5m open 入场、跨周期反色退出，48 项开发
比较后候选历史迁移 74 笔 −12.14bp/PF0.627；V2 六种分批兑现、V3 四组事前环境也全部失败。
V4 builder/config 在 a169fa6 提交后才运行：固定原始 251 母 K1，对比直接入场、等首个 K2
但仍 K1 止损、相同 K2 改为 K2 止损。5m native40 退出、20bp、共同母时限 72h 不变。
三个方案实际成交 251/55/55 笔，净均值 −14.02/−22.92/−22.16bp；四个半年度均负。
等待每原始机会净收益 −5.02/−4.86bp，不能冒充每成交收益。相同 55 个机会原直接入场
为 −43.15bp，等待时机确有改善；但漏掉原来 62 个赢家中的 55 个。母对照覆盖仅61.35%。

关键新线索：27/55 笔 K2 入场仅 5min 离场，43/55 在30min 内；只有3根 K2 自身小时异色，
不能把快速退出归咎于它们。当前退出检查“反向状态”，不是要求从同向切换到反向。
**下一步先注册因果时钟诊断**：入场前最近已完成小周期是否已反向、入场后何时真正翻色；
再分别验证重新同向后首次翻色／小周期再启动入场，保留硬止损、母时限、全部未入场机会。
不可把未来55个K2母子群当入场特征，不可无脑扩大止损或无限延迟退出。

V4仅物化2023–2024前缀219551行，未打开2025以后价格，holdout0；980条闭合case/control
均核对20bp与时限，339项测试通过。没有Pine/TradingView、ACTIVE/frozen、VPS、forward、
部署或真金操作。完整HTML：`analysis/html/p1_btcusdtp_hourly_impulse_ltf_exit_20260906.html`。
V4注册与证据：`experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/`。

## ⚡ 当前真相（2026-09-05 — ETH/XAU 15m 品种专属 K1→K2 均确认失败）

Owner 要求分别为 `ETHUSDT.P` 与 `XAUUSDT.P` 研发 15m K1→K2 趋势策略。本轮已按品种独立完成
MA 架构、压缩→释放→承接票数、慢线 runner 缓冲和 10% 微止盈的顺序单变量选择。冻结结果为：
ETH `EMA30/SMA60 + 2票 + 1.25ATR runner`；XAU `EMA20/SMA50 + 0票 + 1ATR runner`。

两者都不能上线。ETH 2026-03/04 确认 19 笔为 **-77.26bp/笔、PF0.086、0/2 月为正**，比
匹配随机差 68.04bp（p=0.9929）；XAU 15 笔为 **-50.41bp/笔、PF0.135、0/2 月为正**，
比匹配随机差 9.35bp（p=0.7086）。确认期 runner 激活率只有 21.05%/13.33%，硬止损占
73.68%/86.67%。XAU selection 的 20bp 成本还是中位 2ATR 风险的 114%。

关键结构病因是 12 根强趋势状态没有把 K1 锚成启动点：信号距 regime 起点中位仍达 ETH 98 根、
XAU 46 根。趋势年龄、效率、翻色数、fee/risk 和 90 组固定 TP/SL 事后压力测试均没有跨期稳定解。
下一版只能将 episode 时钟改为“中性/压缩后的首个 K1 arm，2–8 根内首个 K2 consume”，并用新
前向数据验证；K2 影线成交还需同 venue 1m 数据或把所有失败触线纳入探针账本。仓库 holdout 读取
0 行；未改 TradingView、ACTIVE/frozen/forward、部署、仓位或订单。完整报告：
`analysis/html/p1_eth_xau_15m_asset_specific_k1k2_20260905.html`。

## ⚡ 当前真相（2026-09-05 — ETHUSDT.P 15m 共振 V17/V18 全部拒绝）

Owner 要求加入多因子/多周期共振并减少盘整误开。本轮冻结 V16 K1→K2、渐进 TP、SMA60 runner、
2ATR 止损、96 根与 20bp 成本，先测 ETH 完整 1h/4h、BTC 15m+1h、量能、波动扩张、路径结构
六轴及 12 个门。方向类共振多数重复 V16 已有趋势状态；12 门没有一个通过全部样本、跨期、经济、
右尾与多重检验门。V17 因此 `rejected_before_audit`。

唯一诊断候选扩张轴被单独冻结为 V18：`min(ATR ratio96, BB-width ratio96) >= 0.85`。开发期
54/182 笔由 -22.32 改善到 +4.59bp/PF1.077，但阈值来自已看过的 14 组合，FWER p=0.2756。
固定后搬到 2025–2026-02 transport audit，37/114 笔为 **-42.97bp/PF0.475**，而 V16 全体
+5.19bp/PF1.065；比同月×时段×ATR桶×方向匹配随机差 -31.73bp，p=0.8929。最强 12 笔只留
1 笔、仅留 6.34% 正收益，说明绝对扩张门删掉了从压缩状态起步的大趋势，不是退出参数问题。

全部候选 REJECT，未改 Pine/TradingView、ACTIVE/frozen/forward、部署、仓位或订单；repository
holdout 返回 0 行。下一条有效假设只能是因果“压缩→K1 有向释放→K2 承接”的状态变化分数，且需
新前向数据，不能再用本次 audit 调阈值。完整报告：
`analysis/html/p1_ethusdtp_15m_causal_confluence_20260905.html`。

## ⚡ 当前真相（2026-09-05 — ETHUSDT.P 15m 渐进止盈 V16 已保存，未证明优于纯 runner）

Owner 明确要求“慢慢 TP”，不能用抬高整仓止损冒充止盈。本轮冻结 V5 稀疏趋势 K1→K2 入场、
2ATR 初始止损、完成 K 线 +2ATR 激活、SMA60(HL2)±1ATR runner、96 根和 20bp 成本，
连续排除大比例固定减仓、颜色/弱势触发、未来强趋势标签和因果路径分类后，留下 V16：
`+2/+4/+8/+12` signal ATR 各真实平原仓 2.5%，总计只平 10%，90% 继续跟原 runner；
部分止盈代码块绝不写 stop。

开发 182 笔中 V16 是 10/15/20% 候选里唯一保留至少 95% P95 的方案。非 pristine 审计
114 笔中纯 runner +7.36bp/PF1.091，V16 +5.19bp/PF1.065，配对差 -2.17bp、
p=0.914；P95 保留 93.58%。它给 runner 非正交易合计补 +150bp，却从前 10% 大赢家拿走
-445bp，净 -248bp。这证明真实落袋可减少 giveback，但仍是对趋势右尾征税，不能宣称提高收益。

Pine v6 官方编译 0 错，已私有保存为 `Fable ETH 15m Trend · Gradual TP V16` 并挂图；只作
研究显示，不 promote。完整报告：
`analysis/html/p1_ethusdtp_15m_gradual_take_profit_20260905.html`。所有行情读取止于
2026-03-01 前，仓库 holdout 行数 0；没有改 ACTIVE/frozen/forward/部署/仓位/API key/订单。

## ⚡ 当前真相（2026-09-04 — BTCUSDT.P 15m 震荡重复信号已做状态机修复）

Owner 指出 15m 图在同一盘整区间出现四组 K1/K2，不符合趋势策略。根因不是单一形态阈值：
V3 把平仓错误地当成趋势重置；V4 拆开持仓与趋势账本后，又发现历史 regime 记忆不能证明
K2 当下趋势仍活着。最终 V5 保持严格趋势创建门，增加当前零轴活性门，并规定每个 regime
只接受一次信号；止损/超时不重新武装，只有反向强趋势或 8 根中性状态才重置。

开发期密度从 36.61 降至 7.18 次/月，审计期从 36.89 降至 8.71 次/月；审计期 24h 内重复
从 67.37% 降至 9.76%，中位间隔 16.75h→73.88h。Owner 指定的 2026-08-05 图是该配置
获批 holdout 使用第 1 次，只作冻结后视觉核验：原四组重复信号已消失，未据此调参或算收益。

经济结论仍是拒绝：审计 123 笔，扣 20bp 后 -10.41bp/笔、PF 0.806；匹配随机超额
+5.54bp、p=0.353。形态 score 审计 AUC 0.536，top-decile -53.08bp，不能靠继续收紧
K 线阈值救活。V5 已编译并保存到 TradingView 私有脚本版本 5，但只作研究显示；没有改
ACTIVE/frozen/forward/部署/仓位/订单。完整报告：
`analysis/html/p1_btcusdtp_15m_trend_regime_live_entry_20260904.html`。

## ⚡ 当前真相（2026-09-04 — BTCUSDT.P 15m 多因子共振全数拒绝）

Owner 要求把既有多因子、特征工程和其他共振都试一遍。本轮固定 EMA30 高召回候选、下一根开盘、
2ATR 初止损、收盘 +2ATR 激活、SMA60±1ATR runner、96 根 horizon 与 20bp 成本，构建
**103 个模型输入 + 1 个共振票数**，预注册 14 个因子包：K1→K2、趋势年龄、完整 1h、波动、
量价参与、结构动量、ETH 共振、32 根序列形态和时段。

14 个候选在 2023 expanding OOF 全部 0/3 正折；规则提名的 `legacy_plus_participation`
为 -17.15bp/PF 0.654/FWER p=0.799。冻结后 2024 为 314 笔 -7.14bp/PF 0.881，
2025–2026P1 为 375 笔 -13.96bp/PF 0.733；score p=0.0427/0.4002，匹配随机超额
+3.70bp(p=0.359)/+0.81bp(p=0.463)，全部经济门失败。q99 汇总转正是看过结果后的 25/21
笔稀疏尾部，拆半年后 2024H2 与 2026H1 已反转，禁止改默认阈值。

失败状态机已修正：旧表把盘中 MFE≥2ATR 混作“收盘已激活”。真实账本中 205/375 从未激活，
合计 -17,027bp；170 笔激活仓合计 +11,792bp，104 个已激活赢家贡献 +14,396bp。
退出仍能保留右尾，下一优先级是严格 K1→K2 episode、多空分模和“是否收盘激活”的两阶段任务，
不是再堆指标或收紧止盈。完整报告：
`analysis/html/p1_btcusdtp_15m_multifactor_confluence_20260904.html`。

本轮经济数据最晚至 2026-02-27，仓库 holdout 价格读取/评分 0 行；但继承父级截图影响的显示候选，
所以 lineage 仍标记 holdout-contaminated。未修改 TradingView、ACTIVE/frozen/forward、部署、
仓位或真实订单，`training_eligible=false`、`production_eligible=false`。

## ⚡ 当前真相（2026-09-04 — OKX 全市场 1h 模型先检、代码后检 Top-10 已交付）

Owner 明确取消“只看已经冻结的非 holdout 历史候选”限制。本轮重新冻结当时全部合资格 OKX
USDT 永续 1h 原始 K 线：277 个合约中 274 个具备 396 根完整 K 线，CASHCAT、CP、DGAI
因上市过短显式排除。流水线保持 **YOLO 先提案 → 当前 bar 六均线站位代码门 → 同币同方向
5-bar 去重 → 模型置信度 Top-10**；不读取 `t-1`，不要求首次站上/站下。

完整漏斗：**65,760 个 W18/W19 输入 → 2,294 raw boxes → 2,008 个合法结构框 →
1,928 个当前站位通过 → 248 个事件 → 10 张全局图**。Top-10 恰好全是 SHORT，没有人为
补多空比例。未来 96 根在模型任务构建前物理移除，Top-10 身份冻结后才接回审核；每张图含
180 根历史、信号点、96 根未来及 exact model input inset。

这十个不是都赚钱：SHORT 方向收盘变化在 24h 仅 2/10 为正、48h 仅 1/10、96h 为 8/10；
FLOKI 与 IRYS 到 96h 仍为负，CAP 期间 MAE 为 -11.72%。没有入场、成本、止盈止损口径，
这些不是交易收益。逐张画廊：
`experiments/active/exp-1h-okx-model-first-standing-top10-20260904-v1/results/review/gallery.html`；
Owner 报告：`analysis/html/p1_1h_okx_model_first_standing_top10_20260904.html`。

本轮登记为 checkpoint holdout 使用 **#20**。独立 verifier 复验 274 个源哈希、2,008 个代码门、
248-event 排序、10 个像素、10 次 Future Mutation、10 个结果与 10 张图，全部 PASS。该权重
原生为 15m，1h 属于 OOD 图审；未训练、调参、promote、部署、改 ACTIVE/frozen/forward、
发消息或下单，`training_eligible=false`、`production_eligible=false`。

## ⚡ 当前真相（2026-09-04 — 已按 Owner 要求去掉前一根首次穿越条件）

Owner 选中“本根首次站上六线”并要求去掉。本轮保留模型先、代码后的顺序，只把第二层改为
一根 K 线当前站位门：LONG 在模型提案端点 `t` 收盘高于全部 SMA/EMA 20/60/120 即通过，
SHORT 镜像；不读取 `t-1`，不再要求首次穿越。

同一冻结账本从 v1 的 **8 raw 提案 → 0 通过**变为 v2 的 **8 raw 提案 → 8 通过**，方向
翻转 0/8；重合核心去重后是 **1 个 LONG 事件**。Future Mutation 8/8、独立六线复算和
图像验收全部 PASS。说明前一根限制确实造成了误拒，Owner 的删除要求已经生效。

最早完整可用时间仍是 **2026-09-02 03:00 CST**：首个 YOLO proposal 与首个代码通过在
同一根 02:00 开盘 K 线上。后级门现在不再额外延迟或误拒，但不能早于上游模型首次提案。
完整报告：`analysis/html/p1_1h_filusdt_model_first_standing_gate_20260904.html`。

本轮登记为 checkpoint holdout 使用 **#19**；没有联网、重新推理、训练、调参、promote、
部署、改 ACTIVE/frozen/forward、发消息或下单。该 1h OOD 已知盈利单诊断不证明精度或收益，
`training_eligible=false`、`production_eligible=false`。

## ⚡ 当前真相（2026-09-04 — 模型先、代码后已实测，瓶颈仍是 YOLO 晚到）

Owner 纠正流水线顺序应为“模型先检测密集，再由代码检测站上线”，并要求实测。本轮复用
FILUSDT.P 1h 五日扫描的 240 张冻结输入与完整 raw-box 账本，不联网、不重新推理、不降
`conf=0.25`；把第二层单变量替换为无阈值的两 bar 规则：当前收盘首次越过六条
SMA/EMA 20/60/120 的目标侧边缘。

结果是 **8 个原始 LONG 模型提案 → 0 个代码通过 → 0 个事件**。第一个 raw YOLO 提案仍是
09-02 02:00 bar、**03:00 CST 才完整可用**；当前 `close=0.7621 > 六线上沿 0.709675`，
但前一根已经 `0.7721 > 0.704157`，所以不是“本根首次站上”。其余 7 个也都晚于首次穿越。
结论不是模型先的架构错，而是后级代码不可能早于前级首个提案；现有 completed-history YOLO
才是延迟下界。

同时确认 Owner 的“密集后刚启动”不能简化为第一次穿越完整六线：模型核心开始前，价格已经
在六线上方。正确目标是模型在当时右端先输出 `dense_active`，代码锁存状态后再判断离开密集
核心并站稳，禁止把后来框回填到核心时间。完整报告：
`analysis/html/p1_1h_filusdt_model_first_breakout_gate_20260904.html`。

本轮登记为 checkpoint holdout 使用 **#18**；方向翻转 0/8，Future Mutation 8/8 PASS，
独立六线复算通过。没有训练、调参、promote、部署、改 ACTIVE/frozen/forward、发消息或下单；
该 exact gate 登记 rejected，`training_eligible=false`、`production_eligible=false`。

## ⚡ 当前真相（2026-09-04 — FILUSDT.P 1h 五日回放命中同一上涨，但信号偏晚）

Owner 要求用现有模型回放 FILUSDT.P 1h 最近五天，检查手工盈利单能否被识别。本轮按冻结
Grade-A full40 native-1280 checkpoint、W18/W19、`conf=0.25`、NMS `0.70`、core4/5、
post2–9 与因果语义门运行：120 个完整小时端点、240 张输入，经 8 原框 → 8 结构框 →
8 语义框，去重后是 **1 个 LONG episode**。

模型核心在 **09-01 19:00～23:00 CST**；01:00～02:00 的主升小时已经上涨 `+6.69%`，
首次检测标签才落在 02:00 bar，需等它收完，到 **09-02 03:00 CST** 才因果可用（收盘
`0.7621`，置信度 `0.2966`）。因此它命中了截图里的同一段上涨，但识别的是局部均线密集启动，
不是手画下降趋势线本身，也不能说在突破当刻提前入场。截图没有成交时间/价格，严格逐笔身份
仍未解。全局图与报告：
`analysis/html/p1_1h_filusdt_grade_a_recent5d_probe_20260903.html`。

这是该 checkpoint 的 holdout 使用 **#17**。离线复验通过 8/8 输入像素、8/8 实际方向与
8/8 反向语义重算，零网络读取；CPU/MPS 置信度差 `2.18e-6`。1h 仍是 15m checkpoint 的
OOD completed-history 研究回放；没有训练、调参、promote、部署、改 ACTIVE/frozen/forward、
发消息或下单，`training_eligible=false`、`production_eligible=false`。

## ⚡ 当前真相（2026-09-03 — 每日涨跌 Top5+Top5 已交付 5,083 张检测图）

Owner 要至少找 5,000 个用于继续优化数据集。本轮按预注册从 2025-10 向前扫描完整月，在
2021-07 首次过线：52 月、1,584 日、15,522 个 Binance USD-M 榜单币种日，经 1,608,553
个可见 W18 端点 → 407,748 个因果预筛/CUDA 输入 → 30,398 原框 → 25,932 结构框 →
8,600 语义框 → **5,083 个全局去重事件和 5,083 张检测图**。前一完整月边界 2021-08
只有 4,969 个新事件；最终为 **5,053 个新审核事件**，停止边界正确。

5,053 必须拆成 **1,652 个新语义正候选 + 3,401 个新 hard-negative 候选**，不是 5,053
个 Gold 正例；另有 15 个精确训练输入和 15 个训练正例近邻，已隔离。独立 verifier 重验
12,691 份源 ZIP、407,748 个任务、25,932 个语义/重合决定及 5,083 张完整复合图像素，
全部 PASS；网络、模型推理、holdout OHLCV 均为 0。

检测图：`experiments/active/exp-15m-ma-launch-grade-a-daily-movers-5000-v1/results/model_inputs/`；
可筛图库：`experiments/active/exp-15m-ma-launch-grade-a-daily-movers-5000-v1/results/gallery.html`；
Owner HTML：`analysis/html/p1_15m_ma_launch_grade_a_daily_movers_5000_20260903.html`。同日榜单是
收盘后才知道的事后挖掘层，不能当实时选币、验证、回测或收益证据。未自动改 label/dataset、
训练、调门、promote、部署、改 ACTIVE/frozen/forward、发消息或下单；两项 eligibility
仍为 false，生产仍 `detector=none`。

## ⚡ 当前真相（2026-09-03 — Crypto 15m / 1h / 4h / 日线最新扫描完成）

Owner 要求用现有模型扫描最新四周期行情，并允许 4h / 日线回看最近 15 天。本轮冻结 OKX
USDT 永续 277 币宇宙、Grade-A full40 native-1280 checkpoint、W18/W19、结构门、因果
语义门与审核排序后执行：57,770 个模型窗口经 2,122 原框 → 1,837 结构框 → 294
语义框 → **41 个去重研究候选**，分周期为 **15m 0、1h 2、4h 31、日线 8**；其中
**5 个**仍在各自周期的最新已收盘端点。ZEC 是唯一同方向跨周期币种（4h + 日线），
但两个事件并不同步，不能解释成实时共振确认。

离线复验通过 1,075 份冻结 K 线、1,837 个模型输入像素、1,837 个语义决定、41 个
事件/排序与 41 张图，网络读取和模型推理均为 0。首次执行在读完行情后因语义 frame
缺 ATR 列失败；修复只补回父实现既有的 Pine-RMA ATR14，并复用原冻结字节，没有二次
读取市场。正式报告：`analysis/html/p1_crypto_grade_a_yolo_mtf_latest_20260903.html`；
可筛图库：`experiments/active/exp-crypto-grade-a-yolo-mtf-latest-20260903-v1/results/scan/gallery.html`。

这是该 checkpoint 的 holdout 使用 **15m #13、1h #14、4h #15、1d #16**。此前 Owner 对
4h 批次“都不太行”的裁决继续有效；更高周期是 OOD completed-history 审核候选，不是
允许交易的信号。未训练、调门、promote、部署、改 ACTIVE/frozen/forward、发 Telegram
或下单，`production_eligible=false`。

## ⚡ 当前真相（2026-09-02 — 新增因果特征有改善迹象，但未过升级门）

Owner 问 28 个 L2 特征能否继续增加并授权实际试验。本轮冻结真实 Grade-A L1 候选、
side-aligned 语义、TP5/SL2/72、0.2% 成本、dependency representatives、时间切分、模型参数
和匹配对照，只增加因果特征列。旧 rich builder 的 116 个候选先剔除 6 个语义重复列，最终从
28 列扩为 110 列（新增 82）；3,779 行复算的旧 28 列最大误差 3.553e-15，未来特征、非有限值、
时间错位和 holdout 行均为 0。

March tune 独立选择 LONG/SHORT：LONG 选 full_110，SHORT 只选 baseline_28 + 26 个 MA family
列。selection receipt 提交后才打开 April final。冻结入选组合的 exact top-decile 净收益由旧
28 列的 +89.79bp 升到 +110.78bp，q90 数量由 20 增到 31，匹配对照 8/8 为正；但固定 q90
单笔质量由 +123.11bp 降到 +98.70bp，且置换 p=0.046395 未达到预注册 p<0.01。因此实验登记
为 rejected：这是值得保留的正向迹象，不是可 promote 的升级。

Owner 追问“31 个是否都赚钱、有没有图”后，逐事件联结 outcome ledger 纠正了一个命名陷阱：
receipt 的 `win_rate=41.94%` 是 **13/31 个 TP 标签率**，不是净盈利率。真实为 13 TP、
14 SL、4 timeout；其中 3 个 timeout 扣 0.2% 成本后仍为正，因此是 **16/31 净盈利、
15/31 净亏损**。已逐张生成并复算 31 张 1920×1320 图，全部保留，没有只挑盈利样本：
`analysis/output/ma_launch_l2_feature_addition_v1/selected_q90_signal_gallery/gallery.html`。
图中蓝虚线左侧是因果输入，浅色右侧是审计用未来结果，31/31 原 L1 输入像素与框坐标复现。

完整报告：analysis/html/p3_15m_ma_launch_l2_feature_addition_20260902.html。正确下一步是增加
同一冻结 L1 目标域的独立经济事件后做一次预注册确认，不在本轮 final 上继续调组，也不提前
消耗 holdout。未 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。

## ⚡ 当前真相（2026-09-02 — 28 特征分组消融已否决）

Owner 问为什么 L2 是 28 个特征并授权实际试验。本轮确认这 28 个是 2026-07-07 为旧
strict-rule 候选人工设计的基线，不是当前 YOLO 候选上自动选出的最优集合。实验冻结真实
Grade-A L1 候选、side-aligned 语义、TP5/SL2/72、0.2% 成本、dependency representatives、
时间切分、LightGBM 参数和 tune-q90，只在七个预注册特征子集之间变化，LONG/SHORT 分开选。

3 月 tune 的七个方案扣成本 top-decile 全部为负；仍按预注册规则冻结的“最不差”组合是
LONG 24 特征（去动量）与 SHORT 16 特征（MA 结构 + 价格趋势）。打开未参与选择的 April
final 后，两者合并 top-decile 净值 -69.05bp，冻结 q90 18 个事件净值 -76.27bp，
置换 p=0.972603，LONG/SHORT 均为负且匹配对照失败，实验登记为 rejected。

旧 28 特征基线的 1,021 个 final 分数、side percentile、阈值和 KEEP 决策全部复现，最大差
1.11e-16；其 q90 仍为 20 个、+123.11bp，但 p=0.072093、n<30、SHORT n=7，历史
拒绝不变。正确结论不是“删到 16/24 个”，而是当前 417 train +229 tune 独立事件不足以
稳定选组；后续要增加同一冻结 L1 的目标域候选，并把 early-stop 与 feature-selection
拆成两个时间窗。报告：
analysis/html/p3_15m_ma_launch_l2_feature_group_ablation_20260902.html。
未读 holdout，未 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。

## ⚡ 当前真相（2026-09-02 — 上万参考图已接入 L2，但目标域错配导致否决）

Owner 追问“不是有上万个训练图、还有很多 Gold 吗”，并授权实际接入验证。本轮没有把形态正负
冒充盈亏：旧 10,000 正图 + 10,000 配对负图逐事件联回原 K 线，在各自 `window_end_i` 只取
已收盘特征，从下一根开盘重算固定 TP5/SL2/72 收益。18,364 个训练截止前窗口中 18,069 个
成功生成经济标签；按同币完整 168 根输入 +72 根标签暴露合并后，L2 独立训练块由 417 增到
13,867。真实 L1 的 229 个 tune 与 242 个 final 独立事件完全不变，原多空模型、阈值、分数和
KEEP 决策均精确复现，`holdout_rows_opened=0`。

数量增加没有带来目标域提升：参考事件占扩充训练代表 97.18%；LONG 形态正/负 TP 率仅
25.32%/22.90%，SHORT 为 25.20%/26.65%，说明 L1 形态标签不是 L2 收益标签。扩充 28 特征
模型在固定真实 L1 final 上 AUC 0.4864、top-decile 扣 0.2% 成本后 -16.54bp、置换
`p=0.668933`；SHORT tune-q90 在 final 放过 95.29%，校准明显塌缩。单特征对照 top-decile
+90.73bp，但 `p=0.072293` 且匹配对照门仍失败。实验登记为 `rejected`，模型不得 promote。

正确下一条数据路径不是继续堆挑选出来的漂亮图片，而是用同一个冻结 L1 在更长 pre-holdout
历史、更多币种上真实扫描提案，再逐事件生成 L2 经济标签、依赖去重、按时间切分和多空分训。
Owner Gold / 正图 / hard negative 继续服务 L1 形态检测；它们不能按图片数替代 L2 目标域样本。
交付报告：`analysis/html/p3_15m_ma_launch_l2_reference_augmentation_20260902.html`；24 张实际
final 输入高清页：`analysis/html/p3_15m_ma_launch_l2_reference_augmentation_diagnostic_gallery_20260902.html`。
没有读取 holdout、promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。

## ⚡ 当前真相（2026-09-01 — L1.5 已从默认研究链物理旁路）

Owner 决定去掉未通过的 L1.5。新入口只执行 `冻结 L1 候选 → dependency episode 合并 →
LONG/SHORT 独立 L2`，从原始 3,779 行 L1 ledger 用明确 `usecols` 读取 27 个字段，读取的
`l15_*` 字段为 **0**，且 runner 不导入 global-shape 或 L1.5 实验模块。历史 L1.5 代码、
prereg、失败模型和报告保留为不可改写的失败证据。

独立重训已证明旁路不是文字开关：新旧 LONG / SHORT 模型 SHA-256 完全相同，两个阈值差均为
0，逐事件最大分数差均为 0，入选 ID 完全一致，仍为 34 个。经济裁决没有因此变好：整体
q90 净均值 +38.88bp，但置换 `p=0.192081`；LONG 17 个净均值 -50.94bp，SHORT 17 个
+128.69bp，所以 L2 仍 `rejected`、`production_eligible=false`。没有读取 holdout，没有
promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。交付报告：
`analysis/html/p3_15m_ma_launch_l1_l2_bypass_l15_20260901.html`；架构决定：
`docs/decisions/0003-bypass-l15-until-shape-supervision-is-valid.md`。

## ⚡ 当前真相（2026-09-01 — 因果 L1.5 + 多空 L2 全链路已跑完并否决）

Owner 授权把 L1.5 与 L2 正确路径全部落地。第一版 L1.5 虽只看检测时已经收盘的 K 线，
但弱标签与特征都包含同一段 post-core 确认进度，LONG / SHORT AUC 及单特征 AUC 都为
1.0，属于标签重建捷径，已在进入候选筛选和 L2 前停止。第二版把 L1.5 输入物理截断到
每个事件的 `core_end`：固定 128 根、0 根 post-core，3,129 个独立事件按时间切分并分别训练
LONG / SHORT。LONG 最终 AUC 0.9505、召回 82.61%、FPR 7.25% 通过；SHORT AUC 0.8906、
FPR 15.70% 超过预注册 12% 上限，因此 L1.5 总门失败。

同一冻结最终段的 242 个独立 L1 候选做四臂对照：L1 池净均值 +7.35bp；L1.5-only
筛到 146 个后反降至 -7.43bp；L2-only q90 为 34 个、+38.88bp，但置换 p=0.1921 且
LONG -50.94bp；完整 L1.5+L2 仅 18 个、+15.66bp，p=0.2304，SHORT -12.03bp。
所以正确结构已经实现并验证，但当前弱形态标签和收益 L2 不能组成可用生产门，实验登记为
`rejected`。38 张实际 128 根输入像素校验失败为 0；没有读取 `>=2026-05-04` holdout，
没有 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。交付报告：
`analysis/html/p3_15m_ma_launch_l15_precore_l2_pipeline_20260901.html`；本地高清图库：
`analysis/html/p3_15m_ma_launch_l15_precore_l2_pipeline_gallery_20260901.html`。

## ⚡ 当前真相（2026-09-01 — 与 L1 完全同窗的 L2 多空回归已否决）

Owner 纠正上一版 168 根全局上下文 L2：判断层必须只针对 L1 实际看到的 18/19 根
1280×742 原图和当前原框，并继续使用未来实际收益回归，多空分开训练。本轮在冻结的
25,911 个 L1 原始框上按 `symbol + side` 重聚为 3,827 个 episode；3,827/3,827 原图逐像素
校验通过，3,798 个完整标签样本产生 673 个最终独立事件。模型只读图中可见 OHLC、
SMA/EMA 20/60/120、当前框和当前 confidence，没有旧 48/96/168 根原始上下文、volume、symbol、
后续 episode 最高分或 holdout。

预注册经济门 **FAIL**：总体 top-decile 扣 0.2% 成本后 **-4.7bp**，置换
`p=0.377162`，AUC `0.4519`，Spearman `-0.1201`。tune-q90 总体虽为 +4.7bp，但仅
32/61 个入选事件有完整 8/8 随机对照；覆盖组为 -49.0bp，缺配组为 +63.9bp。LONG
best iteration=1、最终仅 10 个不同分数且 q90 为 -16.9bp；SHORT q90 +70.7bp 是已经看过
final 后的探索性结果，不能事后删 LONG 再冒充独立成功。15 项 lineage/parity/safety 校验全过，
所以结论是经济预测弱，不是图、框或数据链坏掉。

实验已登记为 `rejected`；没有读取 `>=2026-05-04` holdout，没有 promote、部署、改
ACTIVE/frozen/forward、发 Telegram 或下单。交付报告：
`analysis/html/p3_15m_ma_launch_l2_short_window_side_split_20260901.html`；40 张模型实际输入：
`analysis/html/p3_15m_ma_launch_l2_short_window_side_split_gallery_20260901.html`。
下一条若做经济 L2，必须新预注册、单变量降维并用新的未见时间段；若要判断“全局形态好坏”，
那是另一个需要 Owner 全局好/坏 Gold 的 L1.5 分类任务，不能用未来收益代替形态真值。

## ⚡ 当前真相（2026-09-01 — L2 多空拆分回归有提升但预注册拒绝）

Owner 要求说明 L1.5，并把既有 15m L2 训练集按 LONG / SHORT 分开重训。实验严格复用冻结的
3,779 条数据、28 个因果特征、TP5/SL2/72、0.2% 往返成本、时间切分和 dependency blocks；
唯一变量是“混合回归器”改为“多空两个回归器”。混合模型在 242 个最终独立事件上逐分数复现，
最大差 `9.90e-17`，41 个 KEEP 决策完全一致。

多空模型的 tune-q90 在最终时间段合计保留 20 个独立事件，净均值 **+123.11bp**，高于混合模型
的 **+3.39bp**，并跑赢 8/8 组同币 × 同月 × 同 UTC 时段 × 同 ATR 桶 × 同方向随机对照；但
合并置换 `p=0.072093` 未过 0.01，合计 n=20 未达 30，SHORT n=7 未达每侧 10，因此预注册总门
**FAIL**。两个权重只保留为 rejected research artifacts，不启用、不 promote、不部署，也未读取
`>=2026-05-04` holdout。

L1.5 的正确职责是“局部 YOLO 之后、经济 L2 之前”的因果全局形态分类：输入固定 168 根已收盘
K 线，LONG / SHORT 分开建 Owner-confirmed `global_shape_good` Gold 和局部像但全局错的 hard
negatives；不得拿未来收益自动生成形态标签。报告：
`analysis/html/p3_15m_ma_launch_l2_side_split_20260901.html`。

## ⚡ 当前真相（2026-09-01 — ARBUSDT 用户截图已由 Grade-A 1280 模型检出）

Owner 给出 2026-09-01 09:05 CST 的 ARBUSDT 15m 大涨截图，要求用现有模型实测并标出
点位和时间。冻结使用 Grade-A 8,000 正例 + 24,000 负例 full40 native-1280
`best.pt`，`conf=0.25` / NMS `0.70` / W18–19 / core4–5 / post2–9 全部未改。Binance
截图匹配报价与 OKX 原生数据源交叉扫描均只得到 **1 个 LONG episode**，两者
核心均为 **09-01 00:00..00:45 CST**，首次检出窗口右端均为 **01:15 bar**。

Binance 首次置信度 0.6713，01:15 bar 收盘 `0.08962`；因 K 线按开盘时间命名，无前视
完整可用时间是 **01:30 CST**，下根开盘 `0.08963`。截图时仍在形成的 09:00 bar
明确排除；后续到截图 `0.11694` 的 +30.48% 是结果条件选图，不是精度或收益
证据。标注图：`analysis/output/arb_screenshot_probe_20260901_v1/ARB_15m_model_detection_annotated.png`；
交付报告：`analysis/html/p1_15m_arbusdt_screenshot_model_probe_20260901.html`。

这是 Grade-A full40 native-1280 checkpoint 的 holdout 使用 **#3**。没有训练、调参、promote、部署、
forward 写入或下单；该模型仍需 2 根 post-core 确认 K，只是 completed-history / delayed
detector，`production_eligible=false`，生产仍 `detector=none`。

## ⚡ 当前真相（2026-08-29 — A级 8k + 匹配负例 24k 已在 RTX3060 训练）

Owner 问“准备去训练，负样本应该用啥”。结论是**不用旧 30,000 张 OKX-only 负样本**，改用新建
`ma_launch_owner_grade_a8000_yolo_neg24000_v1`：8,000 张 A 级正图逐字节复用，每个独立正事件
同币、同源、同半年块、同 split、同核心根数与同 7–8 个位置配 2 个 dense-no-launch hard 事件和
1 个 easy 事件，最终为 16,000 hard + 8,000 easy，train/val 均精确 1:3。

全量 builder 与独立 verifier 均通过：32,000/32,000 实际 PNG 哈希唯一，8,000/8,000 正图与
标签 parity，24,000/24,000 负标签为空，负事件重叠/活动度/split/holdout 失败均为 0；随机错配
1,000 次的 exact-pairing 零假设 `p=0.000999`。第一轮发现 4 个停牌后冻结报价事件造成 27 个重复
像素副本，已保留失败 receipt，并通过源窗口最低活动度门从事件选择阶段完整重建，未事后删样本。
报告：`analysis/html/p1_15m_ma_launch_owner_grade_a8000_neg24000_20260829.html`；50 组实际输入：
`experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-v1/results/actual_model_inputs_matched_sample50.html`。

Owner 已两次明确覆盖阶段门：先授权这一批 completed-history 数据开训，后于当前对话扩展为
**可读 holdout、可 promote、可部署**。授权回执：
`experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-train960-v1/owner_authorization.json`。
这是权限放行，不是已经消耗 holdout 或已经通过生产指标；记录时 holdout / promote / deploy
均仍为 0。真金下单、撤单、改仓位不在本次授权内。

`ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960` 于 03:16:48 +08 启动，冻结配置为
YOLO11s / 960 / batch 8 / 40 epochs / patience 10，双机 64,000 个 image/label 文件的 SHA、尺寸、
类别绑定与 split 隔离全量验证通过后才启动。当前仍保持
`training_eligible=false / production_eligible=false`：它们记录客观质量状态，不用来否定 Owner 授权。
训练完成后先执行已冻结 chronological val，再预注册并执行一次 holdout 验收。若最终进行
promote / deploy，必须保持其 2–9 根 post-core K 线的 completed-history 输入契约，不冒充
tip / tip-1 / tip-2 因果即时信号。

本轮相关数据集与 registry 测试 51/51 通过。全仓为 1,769 passed、7 skipped、5 failed；失败来自
当前工作区另外存在的依赖锁改动与本机环境（FastAPI/OpenCV/PyYAML 版本不符，且缺
`ultralytics`/`torchvision`），不是本轮文件。未替 Owner 修改或提交这些外部脏改动。

## ⚡ 当前真相（2026-08-28 — ETH 30 日 41 个 episode 已完成置信度审计）

Owner 追问“按照置信度分析”。本轮只读取 holdout 消费 #5 已交付 ZIP 内的 41 个 episode / 1,057
个候选，不联网、不重推理、不读未来收益或 Owner 标签。TG 图上的**首次可见框**置信度中位 0.429，
23/41 低于 0.50，≥0.75 为 8，≥0.90 仅 4（#40、#14、#01、#37）。同 episode 后续最高分
中位却达 0.790，18/41 最终 ≥0.90；23/41 的最高分出现在首次检测之后，最晚再等 12 根 15m K。

最高分与 episode 候选数的 Spearman `rho=0.859`，10,000 次秩置换 `p=0.00010`；首次分数与
候选数只有 `rho=0.281, p=0.0761`。结论是 episode 最大 confidence 被重复窗口次数显著抬高，
不能冒充早期质量。冻结候选敏感度显示 conf 0.50/0.75/0.90 分别仍有 31/22/18 个 episode；
0.90 的中位/P90 额外延迟为 22.5/94.5 分钟。**本轮不选择、不修改阈值**，因为没有逐 episode
真值，无法证明高分更标准。完整报告：
`analysis/html/p1_15m_ma_launch_owner_yolo_eth30d_confidence_20260828.html`。

这是同一已消费输出的描述性切片，`new_holdout_consumption=false`；训练、权重、标签、ACTIVE、
promote、forward、部署和订单变化均为 0；最终主项目回归 1755 passed、4 skipped。**下一条允许动作**：若要真正选阈值，必须在独立
pre-holdout 或新鲜因果 tip Gold 上按首次可见分数报告 precision、漏检率与延迟，不能用本月
41 张继续调门后声称未见验证。

## ⚡ 当前真相（2026-08-28 — ETHUSDT.P 近 30 日原模型扫描已逐图交付）

Owner 要求“用模型再跑一次 ETHUSDT.P 近一个月并发到 TG”。本轮按预注册范围扫描
`ETH-USDT-SWAP` 2026-07-29..08-27 的 30 个完整 UTC 日，原 Owner 10,000 正例 + 30,000
负例 YOLO、`conf=0.25`、NMS 0.7、W18–25、core4–5、confirmation4–6 全部未改。
24,480 个实际输入产生 1,318 个原始框，1,057 个通过冻结结构合同；5-bar 去重后 53 个事件，
再按跨滑窗连续重叠合为 **41 个 episode：27 LONG / 14 SHORT，覆盖 24/30 日**。

原始候选与四维框全部保留；TG 交付以用户决策单位去重，每个 episode 只画最早可见的一个原始框。
41/41 张 1920×1400 图均含 128 根整体行情与模型实际输入 inset，模型输入像素、完整重渲染、PNG
SHA、唯一性均 41/41 通过，循环错配零假设为 0/41。15:24–15:28 CST 已按 Telegram document
无压缩发送 44/44：总览、41 张逐图、无损 ZIP、HTML；完成回执合同 SHA
`7c355bf4b8d1e8037bcc5ef10a16a51a4339d84c11478ddb69273c9f9bac3460`，
`manual_owner_review_required=false`。报告：
`analysis/html/p1_15m_ma_launch_owner_yolo_eth30d_20260828.html`。最终主项目回归为
1751 passed、4 skipped；工作区外部实验 `external/Kronos` 因未安装 qlib 不在本仓依赖合同内，
按正式测试范围排除且未改动。

这是同配置经 Owner 授权的 holdout 消费 #5。41 个 episode 是模型真实提案，不是 Owner Gold；
历史 14 项样本挖掘门没有被拿来自动冒充视觉真相。没有训练、调参、改标签/权重、ACTIVE/frozen、
promote、forward、部署或订单变更，仍 `training_eligible=false / production_eligible=false`。
**下一条允许动作**：Owner 直接看 TG 图；若要改检测规则或减少信号，必须换 pre-holdout 或新鲜
tip Gold 另行预注册单变量实验，不能继续利用这 30 日调参后声称未见验证。

## ⚡ 当前真相（2026-08-28 — 08-27 检测框与实际训练图语义不一致已证实）

Owner 指出“还有很多框识别的不对，和拿去训练的图对比”。本轮冻结复用 08-27 的 43 个原始
预测和既有快照，不联网、不重推理、不改框；按 10,000 个实际训练正例当时使用的同一套 14 项
形态门与 Owner-50 距离门逐项复算。**只有 #27 TAO LONG 与 #30 LIT LONG 通过，41/43 不符合
训练正例标准。** 对 41 个失败输入穷举全部 core4/5 × confirmation4/5/6 后，替代合格核心仍为 0，
所以主要不是统一左右平移问题，而是整张输入属于训练标准外形态。

根因两层：扫描只验预测框横向几何，没有重跑训练语义门；训练标签本身也不是纯均线窄带，而是
包住核心完整影线 + 六均线再加 4% 留白。9,578/10,000 个训练正例的 K 线跨度大于均线跨度。
43/43 当前输入和 29 个不重复实际训练配对均逐像素重渲染一致；43 张 2560×946 对照图、画廊、
CSV、receipt 与独立 verifier 全通过。完整入口：
`experiments/active/exp-15m-ma-launch-owner-yolo-20260827-training-parity-audit-v1/results/comparison_gallery.html`；
报告：`analysis/html/p1_15m_ma_launch_owner_yolo_prediction_training_parity_20260828.html`。
发送回执登记后的全仓回归为 1747 passed、4 skipped。

Owner 随后明确要求“发到 tg”。14:23–14:24 CST 已按 Telegram document 无压缩发送 4/4：
分布总览、代表性并排图、含 43 张高清对照/画廊/CSV/summary 的无损 ZIP、自包含 HTML 报告；
完成回执 SHA 已登记，`manual_owner_review_required=false`。

本轮**没有 Owner 人工审核任务**：自动结论就是 2 个保留、41 个淘汰，不要求 Owner 逐图打勾、
改框或填表。这是同配置经授权的 holdout 消费 #4；禁止在 08-23..27 上据此调门。训练、标签、
权重、ACTIVE/frozen、promote、forward、部署和订单变化均为 0，仍
`training_eligible=false / production_eligible=false`。**下一条允许动作**：若继续修复，在
pre-holdout 或新鲜 tip 数据上预注册“推理后自动执行冻结形态门”的单变量方案；自动淘汰与 QA，
不把审核任务转嫁给 Owner。

## ⚡ 当前真相（2026-08-28 — 08-27 的 43 个事件已按高清全景逐张交付）

Owner 先要求详细分析，随后明确要求“先把昨天的所有信号发我看一下，高清一点，不要只给检测框，
要看整体”。`exp-15m-ma-launch-owner-yolo-20260827-fullcontext-v3` 因此冻结复用 rawbox-v2 的 43 个
08-27 事件，不重推理、不筛选：37 LONG / 6 SHORT、19 个币。每个 1920×1400 PNG 上方为冻结
快照共同覆盖的 110 根 15m 全景（08-26 22:00..08-28 01:15 UTC），右下为逐像素相同的
1280×742 W18–25 模型输入；每张只放同一个原始 YOLO 框，虚线标真实 `window_end_time`。

43/43 事件身份、模型输入、全图逐像素重渲染、原框逆投影闭环和 PNG SHA 全通过；43 张均作为
Telegram document 逐张发送并有断点回执。详细审计显示 43 个 5-bar 事件只有 34 个重叠 episode，
其中 9 个是已有 episode 的二次触发；Top20 19/20 有信号，选择性仍不足。框中位宽 3.73 根，
只占 110 根全景 3.42%，与 10k 训练标签中位宽仅 +4.2%；预测中位高度却 +43.5%。检测要在核心
后再等 4–6 根，即 60–90 分钟。全仓回归 1738 passed、4 skipped。报告：
`analysis/html/p1_15m_ma_launch_owner_yolo_20260827_fullcontext_analysis_20260828.html`。

这是同一配置 Owner 授权的 holdout 消费 #3；分析为事后探索性展示审计，禁止据此在 08-23..27
调阈值、episode 预算或框高后继续声称未见。网络、新推理、训练、ACTIVE/frozen、promote、
forward、部署和订单变化均为 0，`training_eligible=false / production_eligible=false`。
**下一条允许动作**：等 Owner 看完 43 张后给出逐样本语义判断；若要减少重复或覆盖率，另在
pre-holdout / 新鲜 tip Gold 上预注册单变量方案。

## ⚡ 当前真相（2026-08-28 — 最近五日 Top20 已按原始 YOLO 四维框重做）

Owner 指出上一版五日图一张有多个框，而且框位与训练/模型图不一致，随后明确要求“那你重新弄”。
根因已确认：v1 虽用正确的 1280×742 W18–25 输入推理，却只保留预测 `cx/w`，丢掉 `cy/h` 后
用核心 K 的 high/low 重造纵向框；又把多个滑窗事件叠到压缩的 96 根日图上。v1 因此只可继续
引用扫描数量和事件身份，**不得再作为模型原框位置证据**。

修正版 `exp-15m-ma-launch-owner-yolo-recent5d-rawbox-v2` 完全冻结原权重、conf=0.25、NMS=0.7、
W18–25、核心 4–5 和确认 4–6。4,528 个结构候选保留完整 `cx/cy/w/h`，按重叠决策区间聚成
192 个 episode；每币日复核只取最早 episode，100 张为 **97×单框 + 3×无框**。离线 verifier
重渲染 100/100 实际模型输入、重画 100/100 overlay 均逐像素一致；旧口径 239/239 事件身份
完全不变，最大置信度差 `1.11e-16`。最终全仓测试 1734 passed、4 skipped。

总览、五张每日高清图、100 输入 + 100 overlay 无损 ZIP 和 HTML 已作为 8 个 Telegram document
发完并写入回执。报告：
`analysis/html/p1_15m_ma_launch_owner_yolo_recent5d_rawbox_repair_20260828.html`；总览：
`experiments/active/exp-15m-ma-launch-owner-yolo-recent5d-rawbox-v2/results/overview_rawbox.png`。
这是该配置经 Owner 授权的 holdout 消费 #2；没有调参、训练、ACTIVE/frozen、promote、forward、
部署或订单变化，仍 `training_eligible=false / production_eligible=false`。**下一条允许动作**：停在
诚实的 97/100 过度检出结论；若要减少信号，另开 pre-holdout 或新鲜前向单变量实验，不得继续
在 2026-08-23..27 上调阈值、挑 episode 或手改框后声称未见验证。

## ⚡ 当前真相（2026-08-28 — 新 Owner YOLO 最近五个完整 UTC 日已扫描）

Owner 要求“跑一下最近 5 天的数据”。已按上一轮口径扫描 2026-08-23..27 每日事后绝对涨跌幅
Top20，共 100 个币种日、75 个唯一币、81,600 个 W18–25 因果窗。新 10k 正 + 30k 负 Owner
YOLO 在 `conf=0.25`、5-bar 去重后得到 **239 个事件：LONG 168 / SHORT 71**；分日
57 / 38 / 47 / 54 / 43。97/100 个币种日有框，平均 2.39 个；前三天完全相同榜单比旧模型
96→142（+47.9%），说明新模型在高波动完成路径上明显更积极，不能包装成更准。

RTX 3060 exit=0，远端/本机 9 个 artifact SHA 一致；独立 verifier 检查五日榜单、100/100
连续 96 根、训练支持几何、239 个坐标、去重与 6 张 PNG 均通过。跨平台回执反斜杠问题已修为
writer 统一 POSIX 路径、verifier 安全兼容旧 Windows 路径，没有重跑或改预测。报告：
`analysis/html/p1_15m_ma_launch_owner_yolo_recent5d_20260828.html`；总览：
`experiments/active/exp-15m-ma-launch-owner-yolo-recent5d-v1/results/overview.png`。本配置 holdout
消费 #1，榜单是收盘后事后信息；没有调参、训练、ACTIVE/frozen、promote、forward、部署或交易
变更，仍 `training_eligible=false / production_eligible=false`。**下一条允许动作**：停在这组真实
负面选择性结果；若要减少多框，另开 pre-holdout 或新鲜 tip Gold 实验，不能在这五天上调阈值
后再冒充未见验收。

## ⚡ 当前真相（2026-08-28 — 10,000 正 + 30,000 负的 960 YOLO 已训练完成）

Owner 明确要求“去训练”。RTX 3060 作业已经完成并正常退出：请求 40 轮，实际第 29 轮
`patience=10` 早停，交付第 19 轮 `best.pt`。冻结时间验证集回载结果为 P 0.8852、R 0.9262、
mAP50 0.9462、mAP50-95 0.7923；LONG / SHORT mAP50-95 为 0.780 / 0.805。固定
`conf=0.25` 对全部 5,445 张空标签负例推理，46 张出框，误报图率 0.845%；hard 1.148%，
easy 0.267%。权重与 3060 现场 SHA 一致。

高分追加了 split 身份与完整依赖区间审计：train/val 图像 SHA、sample ID、source sample ID
交集均为 0；191 个共用源文件的依赖区间重叠 0，间隔 76 小时。没有发现直接复制或时间交叉，
但这是 completed-history 的 Owner 批量授权弱标签静态 val，不是 tip 实盘证据；且没有同数据、
同 split、同配方的 10,000 负例训练 arm，因此不能把提升因果归给“多了 20,000 负例”。

报告：`analysis/html/p1_15m_ma_launch_owner_yolo_neg30000_train960_20260828.html`；实际预测渲染：
`experiments/active/exp-15m-ma-launch-owner-yolo-neg30000-train960-v1/results/validation_preview.png`；
权重：`analysis/output/ma_launch_owner_yolo_neg30000_v2/ma_launch_owner_yolo_neg30000_v2_y11s_ft960/weights/best.pt`。
holdout 读取 0，ACTIVE/frozen、promote、部署、forward 与交易状态变更均为 0，
`training_eligible=false / production_eligible=false`。**下一条允许动作**：停在研究结果；若要继续，
应单独预注册同数据 10k-negative 对照或 Owner 明确批准新的 tip 金标/新鲜前向验收，不能直接上线。

## ⚡ 当前真相（2026-08-27 — 负样本已从 10,000 扩为 30,000，旧数据逐字节保留）

Owner 纠正“负样本为什么只有 10,000”并明确“应该搞 3w 张”。新 v2 已在本地完成：
`datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2/` 包含 10,000 正例 + 30,000 负例，
全部 1280×742 无框 PNG；hard 19,922 / easy 10,078。每个正例有 slot 1/2/3 三个不同负窗，
旧 v1 的 10,000 正例与 10,000 负例全部保留，图片 SHA 和标签 SHA 20,000/20,000 一致；新增
20,000 负例 hard 14,923 / easy 5,077。

全量 QA 解码 40,000 张图片并核验 80,000 个文件：图片 SHA 40,000/40,000 唯一，正标签
10,000/10,000 可解析，负标签 30,000/30,000 字节为空，底图精确红框像素 0。30,000/30,000
同币、同源、同半年、同 split、同窗口几何；378/378 个源文件内负依赖区间互斥，全部 14,117
个严格正候选继续受保护。train 为正 8,161 + 负 24,483，val 为正 1,815 + 负 5,445；purge
内正 24 + 负 72 仅留 `excluded/`。holdout OHLCV 读取 0。

实际负输入抽样：
`experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2/results/actual_negative_inputs_seed50_added50.html`；
报告：`analysis/html/p1_15m_ma_launch_owner_yolo_neg30000_20260827.html`。78 个局部 hard 配额在
相同安全匹配块内回退 easy，未放松阈值、禁入区或时间隔离。数据仍是 completed-history 的整批
授权 weak labels，`training_eligible=false / production_eligible=false`；训练、3060、权重、
ACTIVE/frozen、forward、部署与交易变更均为 0。**下一条允许动作**：Owner 可查看抽样 HTML；
如明确要求训练，另开训练实验，不读 holdout、不自动 promote。

## ⚡ 当前真相（2026-08-27 — Owner 认可的 10,000 张已转成 10k+10k YOLO 数据集）

Owner 明确回复“刚刚的10000张不错，弄成训练数据集，同时弄好负样本”。本轮已经物化本地
`datasets/ma_launch_owner_autofill10000_yolo_v1/`：10,000 正例（LONG/SHORT 各 5,000）+
10,000 一一配对负例（hard 4,999 / easy 5,001），全部是 1280×742 无框 PNG；正例每图一个
独立 YOLO label，负例 label 字节为空。`data.yaml` 暴露 train 8,161+8,161、val 1,815+1,815；
切点 purge 内正负各 24 张只放 `excluded/`，训练看不到。

正例不是从红框图擦框：从锁定 OHLCV 重渲染干净底图，再把旧框临时叠回做 SHA 对照，
10,000/10,000 与 Owner 刚认可的审核 PNG 逐字节一致。全量复核解码 20,000/20,000 图片、
40,000/40,000 图片/标签文件；20,000 个图 SHA 全唯一，模型输入精确红框像素 0，正标签
10,000/10,000 可解析，负标签 10,000/10,000 为空。错框零假设 0/1,000 匹配。负样本保护了
同源全部 14,117 个严格候选，同币、同源、同半年、同 split、同窗口几何，不复用；train hard
4,079，val hard 908。holdout OHLCV 读取 0。

模型实际输入抽样：
`experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-v1/results/actual_model_inputs_sample100.html`；
报告：`analysis/html/p1_15m_ma_launch_owner_yolo_dataset10000_20260827.html`。预注册的手写时间标签
晚于真实墙钟，冻结字节未被事后重写；权威时间线见同目录 `provenance_time_correction.json`。

这仍是 completed-history、Owner 整批授权的 weak labels，不是逐样本 Gold，也不是 tip 检测器。
`training_eligible=false / production_eligible=false`；训练、3060、权重、ACTIVE/frozen、forward、
部署与交易变更均为 0。**下一条允许动作**：Owner 可先看实际输入 HTML；若另行要求训练，必须
单独登记训练实验，且先决定是否审查 136 个 `h_norm>0.5` 的纵向大框，不得自动 promote 或读 holdout。

## ⚡ 当前真相（2026-08-27 — 严格 15m 自动样例已扩到 10,000 张，仍非训练集）

Owner 接受 autofill v7 五十张后要求按同样标准扩到 10,000。正式 v1 已完成：5,000 LONG +
5,000 SHORT，5,153 个 4 根框 + 4,847 个 5 根框；10,000 个 1280×742 PNG 每张恰好一个
红框，事件和图片 SHA 全唯一，覆盖 229 个币。完整画廊 100 页、每页 100 张，另有 100 张等距
总览。入口：`experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/results/public/index.html`；
报告：`analysis/html/p0_15m_ma_launch_owner_autofill10000_20260827.html`。

17,186,076 根 pre-holdout 15m K 扫出 14,117 个严格唯一核心；同币同方向一小时事件 NMS 后
LONG 5,146 / SHORT 6,235，最终各取 5,000。最大距离 0.4954（冻结门 0.5），核心时间严格止于
2026-05-03 18:15Z。全量独立复核已逐张重算 SHA/尺寸/红框像素，100 个 HTML 页的 10,000 条
图片链接也全部解析；锁定 `.venv` 等距重渲染 100/100 PNG 字节一致。holdout OHLCV、label、训练、3060、ACTIVE/frozen、forward、部署和交易
变更均为 0。

这仍是 completed-history P0 shape retrieval：检索用到核心后 +1/+2/+3/+5，且 Owner 只接受了
参考族，不是逐样本确认新增 10,000 个 Gold 边界。`training_eligible=false / production_eligible=false`。
**下一条允许动作**：允许停在本地 10,000 张和 HTML；不得直接生成 labels/负样本或开训。若 Owner
以后另行要求训练，必须先走逐样本 Gold 类别/边界、负样本排除、时间 group split 和训练授权门。

## ⚡ 当前真相（2026-08-27 — 自动补齐 v7 已逐张发 TG，无 Owner 审核任务）

Owner 明确指出此前“20 张有框 + 30 张无框，再请人工复审”不是其要求。strict Review50 v5
因此改判为**任务类型错误并作废**：检索固定数量合格样例时，淘汰必须留在内部并从大池补位，
不能把无框候选和 KEEP / ADJUST / REJECT 工作转嫁给 Owner。

正式 v7 从冻结的 10,000 个 pre-holdout 候选自动补齐 50 张：25 LONG + 25 SHORT，22 个 4 根框、
28 个 5 根框，50/50 每张恰好一个红框，覆盖 47 个币、每 UTC 小时最多 1 张、每天最多 2 张。
核心/释放硬门通过 3,217，连同 Owner #42/#44 方向归一化距离门通过 106，再按时间与币种约束
求解 50 张。2026-08-27 18:42 CST，50 个 1280×742 原始 PNG 已作为 Telegram document 逐张发送，
并于 18:45 CST 发送 HTML；SHA 逐张入回执。开场消息已明确旧 v5 人工审核口径作废。

入口：`experiments/active/exp-15m-ma-launch-owner-autofill50-v7/results/public/index.html`；报告：
`analysis/html/p0_15m_ma_launch_owner_autofill50_20260827.html`。holdout OHLCV 读取 0，YOLO label、
训练、3060 作业均为 0，ACTIVE/frozen、forward、部署与交易状态未改。v6 作为“框内仍含过多已走
行情”的未发送内部失败保留，不得混用。

**下一条允许动作**：本轮允许停在“50 张示例已交付”，Owner 不需要填审核表。若以后要升级为
训练集，必须另走 P0/P1 Gold 类别与逐样本核心边界门，并取得单独训练授权；禁止直接把 v7 PNG
转成 labels、开 3060、promote 或生产切换。

## ⚡ 当前真相（2026-08-27 — 原图 imgsz=1280 重训完成，但不替换 960）

Owner 在抽查同一批 1280×742 训练 PNG 后明确回复“ok 重新去训练吧”，授权单变量重训。RTX 3060
直接读取原来的 36,812 张 PNG 和标签；manifest、train/val、框、正负样本、seed、优化器与全部
增强配方未变，唯一变量是训练 `imgsz 960 → 1280`。源 PNG 未离线缩图或重编码；训练仍保留与
基线相同的内存 `translate=0.02 / scale=0.1`。运行在第 31 轮 patience 早停，最佳第 21 轮，
exit_code=0，远端/本地文件 SHA 一致，best.pt SHA 为
`a9fae2ef64489e24f39bd51714d96b43cf680bf80d5c536537f03a4402e9f9c0`。

固定 pre-holdout 2,940 张时间 val 的同设备 2×2 网格给出：960/960 mAP50-95 0.3313，
960/1280 0.2672，1280/960 0.2187，1280/1280 0.3110。原生对原生的 P/R/mAP50/mAP50-95
分别下降 0.0414/0.0049/0.0337/0.0202；easy 负样本从 3.401 升到 4.082 框/千图，hard 维持
2.723。因此 **拒绝用 1280 替代 960**。1280 权重只保留为可复现负结果，仍是完成态弱标签、
`training_eligible=false / production_eligible=false`；holdout 消费 0，ACTIVE、frozen、forward、
部署和交易状态均未改。报告：
`analysis/html/p1_15m_ma_launch_t3_yolo10000_imgsz1280_20260827.html`。

**下一条允许动作**：回到 Owner 逐样本 Gold 类别与核心边界审核；不要继续按源图像素猜训练尺寸，
也不要 promote 两个弱标签权重。若以后再改分辨率，必须先冻结单变量并跑 train×eval 2×2 网格。

## ⚡ 当前真相（2026-08-26 — 15m t-3 审核/训练视图已纠偏，模型未变）

Owner 指出的两个视觉矛盾已修复为独立 v2 产物：首批 1,000 张审核图从原 OHLCV 重新渲染，
现在蓝线全部位于 `t-3`、橙色虚线保留原选择 `t`；与原本已经正确的后续 9,000 张合并后，
**10,000/10,000** 审核行精确满足 `review_marker_source_i = source_anchor_i - 3` 和 45 分钟差。
旧候选图不覆盖。

新的 40 页审核画廊不再把 48 根完成走势与 14--22 根模型输入当成同一缩放：左栏直接引用
canonical 训练 PNG，并用同名 YOLO `.txt` 做 CSS 叠框；右栏物理分离显示 `t-30..t+17`
未来辅助图。9,938 个正例图片/标签 SHA 逐项接回，62 个 purge 候选明确没有训练图；10,000
卡片、19,938 图片引用、9,938 框、缺失路径 0。holdout 读取 0，训练数据、旧权重、ACTIVE、
forward 和生产状态全部未改。报告：
`analysis/html/p1_15m_ma_launch_review_parity_v2_20260826.html`；完整画廊：
`experiments/active/exp-15m-ma-launch-t3-review-parity-v2/results/index.html`。

**下一条允许动作**：Owner 先看 v2 左栏“模型实际输入+框”是否表达目标。该修复不把统一 t-3
弱标签升级成 Gold，也不授权重新训练；若框语义仍不对，应逐样本修核心边界并走 P0/P1 数据门。

## ⚡ 当前真相（2026-08-24 — 做空优先；Owner-long v2 已冻结暂停）

**Owner 最新选择已覆盖执行优先级：先继续做空，做多暂停但不删除。** 做多 v2 的 1,144 个
唯一 event、SHA 与预切分继续冻结，仍全部 PENDING / `training_eligible=false`；本轮不得继续
生成 long 图片、label、negative 或训练。

当前唯一人工入口切回旧 short 模型实际训练过的 1,345 张正例：
`datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/public/index.html`。先审默认 A 档
385 张（`K/1` 保留、`X/2` 去掉、`?/3` 待定），不是当前 `owner_2525.html` 的 long 扩数据页。
2026-08-25 起该入口不再缩放整张 900×521 长图，而是按每张原始 Owner 绿色框坐标直接显示
局部放大 canvas；没有“完整原图”切换，不改原图、框、manifest、答案格式或本地进度 key。
1,345 行已复核为 1,345 个唯一 sample、train 1,143 / val 202、跨 split dependency 0；训练图片
与标签 2,690 个文件全部存在且 SHA 通过。未找到已落盘的 `ma_rope_prefilter_v1_answers.json`，
所以不能假装已有人工结果。

排序版审核包现已补齐 fail-closed `summarize` 接回门：必须同时锁定 public manifest、short
positive manifest、review-only truth 与 rope score ledger 的 SHA，并核对答案的 review/sample
双身份；未审行保持 PENDING，未来辅助审核图永不进入模型输入，接回后仍不会修改
`training_eligible`。下一条允许动作是 Owner 审核并导出 JSON；随后只生成回执和新版 manifest
预览，不训练、不读 holdout，等待 Owner 单独批准是否物化与开训。

Owner 当前选择继续整理数据集。做多方向没有复用 short R1 中 916 个空标签，而是回到
`analysis/output/owner_side_review/review_sheet.csv` 的 1,152 个 Owner-long 原框。8 个重复 alias
合并后得到 1,144 个稳定唯一 event；每个 alias 的 Owner 原行、原始 bar/YOLO 几何和原图均已
逐项锁 SHA。唯一 event 的审核顺序 A/B/C=339/480/325（页面去重前 long A=343）。正式 v2：
`datasets/owner_long_gold_center_candidate_v2/candidate_manifest.jsonl`，SHA256
`0b342a75e55d66a99d84e3d6a5be2be90c4e2f3e9de436aef37939cc1d31e929`。

依赖块预切分为 train 963 / val 171 / drop 10，跨 split dependency/event 都是 0；train→val 当前
只证明相隔 158 个名义 15m 时间格，`actual_ohlc_gap_bars=null`，必须在 bounded pre-holdout
物化后再验。构建器没有打开 OHLC、生成图片/label 或读取 holdout；1,144/1,144 都是 PENDING，
`training_eligible=false / production_eligible=false`。v1 保留为历史但已 superseded，禁止复用。
报告：`analysis/html/p1_owner_long_candidate_manifest_v2_20260824.html`。

**下一条允许的动作**：Owner 在 `owner_2525.html` 默认 `A + long + 未审核` 完成 343 个原始框并
导出 JSON（`K/1` 保留、`X/2` 去掉、`?/3` 待定）；先 join 成 339 个唯一 target 的结果。同一
target 的 alias 必须全部有一致答案，否则保持 PENDING 或 fail closed。没有回执前不得生成 long
训练图、YOLO label、negative 或启动训练；旧 short 1,345 refilter 也不能被新 long ledger 覆盖。

## ⚡ 当前真相（2026-08-21 — 六均线绳结预筛已落地，但只能排序、不能自动删图）

Owner 已明确纠正任务：不是审核 fixed-W10 的 2,649 行混合 Gold，而是把**旧模型实际训练过的
Owner 人工正例原图重新过滤一遍**，只留下现在仍认可的最佳形态，再构建新版本训练集。此前
`original_source_triage_v1` 混合六种来源；`canonical_ohlc_triage_v2` 又把当前 OHLC W200 与历史
长图并排，两张本来就不是同一裁剪窗口。二者均已停用为当前审核入口，只保留作谱系证据。

正确母池是 `datasets/owner_short_gold_center_v1/positive_manifest.jsonl`：Owner 当年亲自判为
`short` 的 1,361 个框，15 个重复别名合并、1 个时间切分 purge 后，形成旧训练实际使用的
**1,345 张正例（train 1,143 / val 202）**。它确实来自原来一万多张人工工作：15 份 canonical
Label Studio export 合计 12,565 张唯一图 / 12,684 个 completed annotation / 6,291 个框；随后
有效 OHLC 对齐、方向复核和 short-only 才收窄到 1,345。Owner 新参考图是向上启动，所以本轮
同时评分完整 2,525 个方向框（long 1,152 / short 1,361 / skip 12），不能只处理 short。

Luna Max 可见本机任务直接实现了因果六均线绳结分数：窄带 0.35、交叉换序 0.30、K 线实体
触碰/穿束 0.25、持续 0.05、收紧 0.05；斜率一致性权重 0。1,345/1,345 与 2,525/2,525
全部评分。旧 manifest 有 1,339 条行数后缀路径过期，均经 symbol + decision index/time 精确
闭合找回；不得按旧文件名判数据丢失。

104 个 exact ⭐ 标杆只用于固定分档：旧 1,345 为 A=385 / B=575 / C=385；完整 2,525 为
A=744 / B=1,067 / C=714。独立 390 条 Owner keep/drop 反证未通过：AUC 0.489、置换 p=0.635、
A+B precision 18.21% 等于 base rate。因此分数**只允许改变人工审核顺序**，不允许自动
keep/remove。两个正式入口：

- 旧训练 1,345：`datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/public/index.html`
- 完整方向池 2,525：`datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/public/owner_2525.html`

报告：`analysis/html/p1_ma_rope_prefilter_20260821.html`。页面每次只显示一张 Owner 原图，快捷键
`K/X/?/J/L/U/Z`，默认 A 档；不存在左右两张不同 K 线，也没有 `R`。

**下一条允许的动作**：Owner 先审 1,345 页面默认 A 档的 385 张并导出 JSON，再决定是否审 B。
不覆盖旧数据地生成新 manifest 预览后，仍需 Owner 明确批准 `training_eligible=true`；此前禁止
3060 训练。Owner 要求查看的 2026-08-16～21 BTC/ETH 已登记为设计示例 view #1，不能再冒充
未见 holdout 或最终验收。

仍需 Owner 独立裁决：两个 ATR 实现的 warmup 分歧，三选项见
`docs/consolidation/DUPLICATE_SEMANTICS.md` §4。

---

## 历史真相（2026-08-19 — 单仓收敛完成，ACTIVE 未变）

**四个卫星仓已回迁并可归档；本仓成为唯一 ACTIVE 交易研究仓。** 分支
`claude/fable-trading-consolidation-758d61`，8 个 `consolidation(c0..c7)` 提交，
验收 `accepted`：七项检查全过。**ACTIVE 指针、forward log、成本合同、部署脚本、
systemd 单元共 12 个运行安全对象与 C0 逐字节相同**；未训练、未 promote、未部署、
未下单、**holdout 本次消耗 0**。测试 701 → **1185 passing**，新增失败 **0**
（11 个先存失败全是本 worktree 缺 gitignore 掉的 `data/` 产物，集合与 C0 完全一致）。

最要紧的一条：**本仓此前跑不起来，除非 `~/yoyo-trading` 在磁盘上**——63 个文件
import `yoyo.*`，靠 editable 安装指向仓外。整包 55 个 `.py` 已字节一致迁回 `yoyo/`。
另发现 35 个脚本的跨仓 `sys.path` 桥把 yoyo-trading 插在**第一位**，即它们一直在
import 另一个仓的 `yoyo`（含 `render.py`，检测器绑死其像素）；已全部删除并加两道
AST 防回归。

**需要 owner 裁决的一件事**：两个 ATR 实现不一致（warmup 播种差异，bar 14 差 0.109，
200 根后耗尽）。ATR 定义 TP/SL 障碍距离，且差异方向取决于取数起点。已量化并钉住，
**未修**——改任一边都会移动已发布数字，障碍参数是 owner 保留项。三个选项见
`docs/consolidation/DUPLICATE_SEMANTICS.md` §4。

验收报告：[`reports/consolidation/FINAL_ACCEPTANCE.md`](reports/consolidation/FINAL_ACCEPTANCE.md)
· 交接：[`docs/consolidation/HANDOFF_AFTER_CONSOLIDATION.md`](docs/consolidation/HANDOFF_AFTER_CONSOLIDATION.md)
· 四仓历史结论：`experiments/historical/`

**收敛已闭环（2026-08-20）**：PR #1 已合并（`31d6b2a`），随后 4 个提交也已进 main；
四个来源仓的 README 顶部已加 ARCHIVED 只读声明并推送
（`e6e5164` / `be1c7bb` / `a16f495` / `06b97d5`）。**一个仓都没删。**

**下一条允许的动作：只有 P0（owner 形态定义与重复标注稳定性）→ P1（Gold Dataset）。**
在 P0/P1 通过前禁止新训练、多周期扩展、promote 与实盘替换。

旧交接曾把 Cleanlab 28 张写成可直接转成 DIRECT 错误率证据；该口径已被上面的 2026-08-20
审计纠正。28 张是模型选择队列，只能用于优先修错，不能估计无偏错误率。

---

## ⚡ 当前真相（2026-08-13 — ETH全年冻结形态门命中2次；1参考+1待Owner确认）

**最新进展（2026-08-13 17:44 CST）：Owner要求统计今年ETH有多少次8月10日参考形态，冻结门
v1已一次性扫完。** 覆盖2026-01-01至08-13 17:15 CST、21,542根连续ETH永续15m bar，0缺口；
本地规范文件止于08-05，缺失后缀用同一OKX history-candles公共接口只读内存补齐900行，未写
`data/kline_fetched`。核心门只用结束bar及以前OHLC、8根前文、ATR14和六均线；后3/5根只作
事后确认标签。扫描前冻结门、扫描后未调参，11个宽度组合→4个端点→12-bar去重为**2个事件**：
①Owner参考本身，2026-08-10 19:30–20:15 CST，4根；②唯一新候选，2026-02-22
19:00–20:15 CST，6根。Codex视觉复核第二张相似，但不得冒充Owner金标；当前口径是机器候选2、
Owner语义确认1、待确认1，且两张核心几何均未获Owner逐bar确认。该冻结门读取9,734根holdout，
登记为配置第1次且由本轮“找今年有多少”的请求明确授权；所有产物`training_eligible=false /
production_eligible=false`，不训练、不promote、不部署。报告：
`analysis/html/p2_eth_yearly_morphology_count_20260813.html`；对照图与manifest：
`analysis/output/eth_yearly_morphology_gate_v1/`；builder先由commit `afad164`入库。下一步只等Owner
对2月22日候选回复YES/NO。正确项目环境全测712 passed、2 skipped；系统Python曾因缺少yoyo/
ultralytics在收集期失败，未当代码失败或通过隐去。

**上一进展（2026-08-12 — 拿到119个早期前沿YES；主动检索机制被证伪，块效应主导）：**

**最新进展（2026-08-12 13:47 CST）：Owner已完成300张早期前沿审核，解盲诊断完成；
检索分层零区分度，时间块解释几乎全部方差；等待Owner在A–E选项中点头，不得自动开R3。**
300/300裁决与manifest精确一一对应，119 YES / 181 NO / 0 SKIP / 0改判，总YES率39.7%
（Wilson 34.3–45.3%），覆盖89个币。解盲后内部`yes_like` 61/150=40.7%、
`similar_no_boundary` 58/150=38.7%，差2.0pp、置换p=0.81；affinity AUC 0.534(p=0.33)、
最近邻距离AUC 0.509(p=0.78)、model_confidence AUC 0.548(p=0.17)——用11个Canary YES做的
5-NN检索在Owner语义上等于随机，且方向在块间翻转（B04 boundary 0.56>yes_like 0.29，
C03 反过来0.74>0.32）。唯一显著的轴是候选块：B03_20251115 4.0% → C05_20260215 73.5%，
极差69.5pp、置换p=1e-4，且各块两层样本数均衡（24–26/7–8），不是抽样混淆。几何切片
（核心6–7根44–45% vs 4–5根34%）与低波幅1–2%仅19.1% YES均未做块内控制，不能当结论。
本轮YES是**未来辅助语义裁决**，不是precision、不是基率、不是独立时间块；框坐标仍是R1提议，
只算逐样本类别确认，不算框几何金标。审核节奏中位0.87s/张（上一轮0.98s），标注噪声未量化。
300个事件仍全部`training_eligible=false`、`production_eligible=false`，未训练、未转标签、
未改conf/NMS/窗口/ACTIVE、未deploy、未读holdout。报告：
`analysis/html/p2_local_signal_v2_early_frontier_review300_owner_result_20260812.html`；
产物：同目录`owner_review_summary.json` / `owner_review_joined.jsonl`；
汇总器`scripts/summarize_local_signal_v2_early_frontier_review.py`（先入库commit e7ba4b1后运行）。
测试`pytest tests`：709 passed、2 skipped。下一步选项（需Owner决策）：
A 批准119 YES/181 NO转为**仅类别**训练标签；B 做块内随机抽样150–200张拿真实基率（推荐）；
C 用130个YES重建检索但先离线验证；D 盲重复审核10–15%估一致率；E 直接开R3（不推荐）。

**上一进展（2026-08-12 13:29 CST）：Owner批准的“早期启动前沿 YES / 相似NO”300张语义
发现包已完成，当前必须停止并等待Owner审核。** 两个冻结R1候选池合计1,484事件；精确剔除
此前四轮700个已审唯一event_id后剩784，再用上一轮Canary 11 YES / 89 NO的decision前因果
OHLC+SMA/EMA20/60/120+框几何做5-NN主动检索，内部选150 `yes_like` + 150
`similar_no_boundary`。UI已盲化抽样层/置信度/距离/来源，无推荐答案。最终300唯一事件、154币，
与旧700重叠0；300模型输入+300 auto-Y因果图+300独立未来48根图全部存在且SHA唯一，300/300
`visible_end_bar == decision_bar`、检索未来0、默认裁决0、training-eligible 0、holdout读取0，
最晚未来图仅到2026-02-16。真实浏览器已验证Y/N/S、左右键、append-only改判、刷新恢复；正式
目录未写测试裁决。全仓701 passed、2 skipped。审核入口命令：
`scripts/serve_local_signal_v2_semantic_review.py --out analysis/output/local_signal_v2_early_frontier_review300_v1 --port 8766`，
浏览器开`http://127.0.0.1:8766/`。PRE-REVIEW报告：
`analysis/html/p2_local_signal_v2_early_frontier_review300_prereview_20260812.html`。

证据等级必须保持诚实：这300张来自旧train-time块的**未审余量主动发现集**，不是新post-train
时间块或独立precision验收；最初8个D块因3060不可达、Mac MPS过慢而放弃，其96MB可重建快照
已移动到`~/.Trash/codex-local-signal-v2-cleanup-20260812/`。当前包审计的是冻结R1 W12–19候选；
293/300框为4–7根，7/300为8根，不能宣称Owner目标20–30根检测窗口已经实现。Owner审核结果
写入`analysis/output/local_signal_v2_early_frontier_review300_v1/owner_verdicts.jsonl`。审核完成后
只做ID联结、总YES率与内部150/150解盲诊断；**不自动训练R3/R4、不改positive标签/conf/NMS/
窗口/ACTIVE，不deploy、不下单、不清forward log、不读取holdout。**

**最新进展（2026-08-12 12:40 CST）：Owner批准的只读边界诊断已完成，根因进一步收敛为
“尺度分布偏移 + 启动释放语义未学稳 + 正例成熟度偏后”，禁止据此直接开R3。** 200条审核样本
逐条联结原始causal OHLC并复算：200/200 source lineage与model-input SHA通过，最大物化时间
2026-05-03 19:45 UTC，0 future图片、0 future OHLC、0 holdout。Canary模型纵轴占用/单根K高度
中位仅18.0%/29.0px，Positive为51.0%/55.2px；97/100 Canary触发6% floor，说明尺度风险明确，
但floor-off仅3条且真实跨度≥4%的8条仍0 YES，不能宣布“改auto-Y即可解决”。Canary YES相对NO
的核心差异是核心跌幅-45.4/-13.7bp、decision收盘相对六线-73.0/-9.8bp、decision六线跨度
75.4/35.0bp、20线框后斜率-4.68/-1.24bp/bar；核心横向中心57.1%/56.3%，位置不是主因。
Positive YES又明显比Canary YES成熟：框后跌幅-60.1bp vs -13.1bp，说明85% purity没有覆盖
“启动前沿”。R2 new仍0/25且结构最弱，R1 suppressed 5/25。下一步推荐先扩新的pre-holdout
早期YES/相似NO语义对照，再固定数据/split/框/配方只改renderer表示做单变量臂；当前不训练、
不调conf/NMS、不转标签。仓库主测试697 passed、2 skipped。报告：
`analysis/html/p2_local_signal_v2_semantic_boundary_diagnosis_20260812.html`。

**最新进展（2026-08-12 11:55 CST）：Owner已完成v2全部200张YES/NO审核，机器诊断明确为情况B；
禁止自动开R3/R4。** 裁决日志200行/200唯一ID，与manifest精确对应，96 YES、104 NO、0 SKIP，
0 holdout。旧Positive Pool为85 YES/15 NO（85%），当前Canary为11 YES/89 NO（11%）；内部
common retained=6/50、R2 new=0/25、R1 suppressed=5/25。R2既产生纯NO的新候选，又抑制部分
Owner认可的R1信号；R1/R2均继续blocked。Canary high-confidence也仅4/27 YES，调conf无证据
解决。真实波幅<1%的Canary为2/39 YES，2%–4%为4/15，说明模型6%最小纵轴可能损害低波幅
分辨率，但≥4%仍0/8，不能直接归因或立刻换renderer。下一步推荐先做**只读边界诊断**：比较
尺度占用、均线结构、框位置、decision延迟和R1/R2差异；不训练、不读holdout。结果报告：
`analysis/html/p2_local_signal_v2_positive_semantic_audit_owner_result_20260812.html`。85个旧Positive
YES、11个Canary YES和104个NO当前仍`training_eligible=false`，转换训练数据需Owner另行批准。

**最新进展（2026-08-12 11:36 CST）：Owner指出v1无走势对照且K线被压平，正式审核入口已
切换为v2，v1禁止继续裁决。** 根因是模型输入renderer的`MIN_REL_SPAN=0.06`：真实波幅不足6%
时蜡烛会被训练纵轴压成水平带；train/live虽一致，但低波幅形态分辨率可能因此受损，尚未验收。
v2物理分开200张原始模型输入、200张人眼auto-Y因果图和
200张独立auto-Y未来对照；左图仍为200/200止于decision，右图只作Owner参考且全部早于
holdout。Positive保持v1原100个不换；Canary保留69个并替换31个未来不足16根的事件，内部配额
仍为共同50/R2新25/R1抑制25。Positive未来48根，Canary安全未来16–46根，0 holdout读取、
0预选答案、0训练资格。正式入口`http://127.0.0.1:8766/`，裁决写入
`analysis/output/local_signal_v2_positive_semantic_review200_v2/owner_verdicts.jsonl`；报告：
`analysis/html/p2_local_signal_v2_positive_semantic_audit_prereview_v2_20260812.html`。最终YES率必须
称为“未来走势辅助的Owner语义裁决”，不得冒充纯causal precision。继续停止R3/R4与hard-negative。

**最新进展（2026-08-12 10:18 CST）：Local Signal V2 Positive语义纯度审计PRE-REVIEW已完成，
当前必须停止实验并等待Owner完成200张YES/NO/SKIP。** 审核包包含当前R2使用的1,345个SHORT
positive分层抽样100张，以及最新独立Canary分层抽样100张（内部为共同保留50、R2新生25、
R1抑制25，UI已盲化）。全部为独立PNG；`visible_end_bar == decision_bar`和`future_bars == 0`
均为200/200，0预选答案、0训练资格、0 holdout读取。界面只提供YES/NO/SKIP及Y/N/S、左右键，
裁决append-only保存，可中断继续和修改。正式入口需先运行
`scripts/serve_local_signal_v2_semantic_review.py --port 8766`，再打开`http://127.0.0.1:8766/`；
PRE-REVIEW报告：`analysis/html/p2_local_signal_v2_positive_semantic_audit_prereview_20260812.html`。
**在Owner审完前禁止训练R3/R4、继续加hard negative或提前判断模型好坏。**

**最新进展（2026-08-12 00:50 CST）：第三训练臂R2已完整训练并在新连续块终审，结论失败。**
run=`owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft`在RTX 3060跑满40轮，best epoch35，
best SHA=`52cd38fd…32afe`；3060最终best复验P/R/mAP50/mAP50-95=
0.8365/0.7596/0.8780/0.7432，Mac固定val独立复验=0.8475/0.7525/0.8774/0.7413，
与R1的0.8626/0.7770/0.8980/0.7405相比没有实质val提升。新非重叠pre-holdout块为
2026-05-03 12:15–23:45 UTC，215币、10,105 endpoints、80,840个W12–19暴露窗，conf0.25、
NMS0.70、同币核心中点±5根去重全部冻结，holdout读取0。R1→R2 raw 3,964→3,538
（-10.75%），事件223→195（-12.56%），折算455.5→398.3 events/day，触发币98→93；仍远超
“少而准”。跨模型一对一配对为共同163、仅R1 60、仅R2 32，R2自身旧问题保留率83.59%。
R2不得promote、部署或调高conf美化。完整报告：
`analysis/html/p2_owner_short_gold_center_hardneg_r2_canary_20260812.html`。下一步先分层审计共同保留/
R2新生/R1被抑制事件，不自动开第四臂、不读holdout。

**最新进展（2026-08-11 23:14 CST）：Owner明确回复“允许”，第三训练臂已在RTX 3060启动。**
run=`owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft`，远端`zzc@192.168.1.4`，WMI cmd
PID 42396、python PID 37808/worker 40452。上传端核对train 4,572、val 402，0 corrupt；首轮已
运行到约28%，约7 batch/s、显存2.7G。日志逐项确认Stage A SHA `c0e94f47…bf1a`、YOLO11s、
imgsz960、batch8、epochs40、patience10、seed0、AdamW lr0=1e-4、warmup0.5、rect=true，
flip/mosaic/mixup/HSV等禁用增强全0；三库版本Mac/3060一致。授权只覆盖本次训练启动，**不含
holdout、promote、部署、TG或交易操作**。启动回执：
`analysis/output/owner_short_gold_center_hardneg_r2_train_launch_20260811.json`。训练尚未完成。

**最新进展（2026-08-11 23:08 CST）：第三训练臂数据集已完成，等待Owner单独授权训练。**
新时间块200张裁决为26对、2框偏、172不对；四张train-time页累计114 target、2 rebox、584
Owner确认难负例。584个全部唯一、0 Owner框重叠、0 holdout、训练因果图0未来。为保持第二臂
W12–19分布和2,286 hard总量不变，本轮按桶内当前模型触发置信度纳入531个；W18/W19溢出的
53个暂存，未改W、复制或挤占别桶。第三臂仍为train 1,143正+1,143 easy负+2,286 hard、val
202正+200 easy负；hard组成531确认误报+852 Owner-long+903旧模型背景。base 5,376文件逐SHA
一致，0联合SHA重复、0语义区间重复、0 train/val交叉；Ultralytics数据检查通过；全测674 passed、
2 skipped。训练输入200张审计：
`analysis/html/p2_owner_short_gold_center_hardneg_r2_audit200_20260811.html`；数据报告：
`analysis/html/p2_owner_short_gold_center_hardneg_r2_dataset_audit_20260811.html`。Stage A初始化、40轮、
batch8、AdamW 1e-4和增强合同均已预注册但**尚未启动训练，未promote、未部署、未读holdout**。

**最新进展（2026-08-11 22:42 CST）：第二张 train-time 难负例扩充页已由 Owner 全部裁决为
25对、0框偏、175不对，协议/源SHA/200 ID/计数/因果与时间门全部通过。** 三张 train-time
审核页累计为88正例+412难负例；412只占第二臂2,286 hard槽位18.02%，因此没有直接训练。
Owner裁决报告：`analysis/html/p2_owner_short_train_hardneg_expansion200_v2_owner_review_20260811.html`。

已转向五个未使用冻结train 12小时块（2025-06/08/10/12、2026-02）：881 symbol-block、42,288
endpoints、338,304个W12–19因果窗，固定权重/conf/NMS/去重合同得到10,533 raw→589事件；剔除
22个Owner框±12 bars重叠后安全池567。C02真实只有12事件，未降阈值或复制，缺额确定性均分给
其余块；新审核页最终47/12/47/47/47，共200事件、131币，与此前500个train已审事件零重复。
选择使用累计1,308正例/666负例参考且不看未来；未来48根只在选定后渲染。600图存在、全部未来
48根完整、0 holdout、0 labels、0 training-eligible。审核入口：
`analysis/html/p2_owner_short_train_hardneg_newblocks200_v3_20260811.html`；报告：
`analysis/html/p2_owner_short_train_hardneg_newblocks200_v3_report_20260811.html`。下一步仍是Owner完成
1/2/3裁决，随后审计累计覆盖；**尚未授权训练、promote或部署。**

**最新进展（2026-08-11 21:25 CST）：正例检索100张已由Owner裁决为45对、0框偏、55不对。**
源SHA、100 ID、声明计数、0 pending、0 Owner框重叠、0前视均通过。与负例偏置页18/200=9%
target相比，正例检索页45/100=45%，同一权重下富集提高5倍，证明两种审核页必须分开；两者均为
偏置主动学习集，不能冒充总体precision。累计train-time Owner参考为63正例+237难负例；237只占
第二臂2,286 hard槽位10.37%，仍不能开第三臂训练。

已用累计1,283正例参考（1,143 train金标+77 post-val语义正例+63 train新正例）与491确认误报
参考（254 post-val+237 train）重评原池剩余617事件，生成第二张**明确标注预期多数按3**的
难负例扩充页200张：B01/B02/B03/B04=21/60/60/59，B05原59事件已全部在前两页审完而不伪造；
200事件、126币，与前300零重复，600图存在、未来48根全满、0 holdout、0 labels、0
training-eligible。入口：`analysis/html/p2_owner_short_train_hardneg_expansion200_v2_20260811.html`；
报告：`analysis/html/p2_owner_short_train_hardneg_expansion200_v2_report_20260811.html`。下一步等Owner
审核新200；随后转向新的未使用冻结train时间块扩挖，不在原五块无限循环。**尚未授权训练。**

**最新进展（2026-08-11 21:06 CST）：文档路线没有跳步。** 第一臂 1:1 easy-negative
baseline 后，第二臂已按文档完成 `1,143 positive + 1,143 easy + 2,286 hard`（负:正=3:1，
hard占负例2/3），best SHA=`029f80a5…f537`；连续行情密度仍失败，未promote。train-time
负例偏置 review200 已由 Owner 全部裁决为18对、0框偏、182不对；协议、源SHA、200 ID一一
对应、0 pending、0 Owner框重叠、0前视全部通过。182是第三臂的高质量新hard种子，但只占
2,286槽位7.96%，不能直接开训或复制凑数。

为单独回答“模型能否找到接近早上ETH的形态”，已从剩余717个未审train事件新建正例检索页，
与负例页目的相反：参考为1,143个冻结train Owner金标 + 77个post-val已审语义正例 + 18个
train新正例，对照254+182个确认误报；只用decision前OHLC/六均线/框几何排序，未直接读取
2026-08-10 ETH holdout行情。选出100个全新事件、75币、五块20/20/20/21/19，与已审200零
重复；300张图存在、未来48根全满、0 holdout、0 labels、0 training-eligible。审核入口：
`analysis/html/p2_owner_short_train_positive_retrieval100_20260811.html`；报告：
`analysis/html/p2_owner_short_train_positive_retrieval100_report_20260811.html`。下一步等Owner完成
100张1/2/3裁决，再扩hard种子并设计第三臂；**尚未授权下一次训练。**

**最新进展（2026-08-11 晚）：第三臂训练前的 train-time 难负例审核集已经就绪，下一动作是
Owner 审核 200 张，不是直接训练。** 使用当前 hard-negative R1 best 在冻结 train 末端之前的
5 个独立 12h 时间块扫描：916 symbol-block、43,968 endpoints、351,744 个 W12–19 因果窗，
20,711 条 raw 检测去重为 953 事件；剔除 36 个触碰任一 Owner 框 ±12 bars 的事件后，安全池
917。按每块 40 个固定抽出 200 事件、123 symbols。排序只用 decision 前 OHLC/六均线/预测框，
此前 Owner 已审的 254 负例 + 77 语义正例仅作形态距离参考；未来 48 根在选样完成后才单独
渲染。600/600 图片存在，200/200 未来图完整，0 holdout、0 labels、0 training-eligible。
审核页：`analysis/html/p2_owner_short_train_hardneg_review200_20260811.html`；报告：
`analysis/html/p2_owner_short_train_hardneg_review200_report_20260811.html`。Owner 完成 1/2/3 裁决
并导出 JSON 后，先统计真负例数量与覆盖；不足则继续扫未使用 train 块，足够才构建保持正例、
easy negative、冻结 val 和训练配方不变的第三臂。**尚未授权下一次训练。**

**Owner最新纠正覆盖本节后文“Codex逐图寻找启动边界并重画61个橙框”的下一步：不得在已有
Owner原始金标坐标时重新凭感觉画框。新血缘必须是`原始⭐手框 ∩ Owner亲自确认short`，外层只
重裁十几根短窗，内层橙框从原手框正中心机械截取。61张Codex橙框保留为失败对照，不再作为待
批准训练标签。**

**最新裁决覆盖下面同日所有“固定20–30根窗口 / 0–10根后文”的表述：Local Signal V2 检测
“完美平台/启动形态”语义，不是盘口信号，也不是固定裁剪模板。ETH参考中红框只落在Owner
标出的两条竖线之间，核心约4–7根，不能包入右侧快速下跌。输入从最短充分上下文开始动态变化；
首轮只试约14–22根并继续向更短收缩。核心结束后3–5根内确认，3根优先、5根硬封顶。**

- 今天 Owner 提供的 ETHUSDT 15m 图是终极形态语义参考，不替代昨晚的数据集和训练成果。
- 昨晚 3060 真正完成训练的是 `local_signal_v2_stagea_randomcrop_v1`：2,378 正例 + 2,378
  easy negatives，train 4,040 / val 716，0 holdout。模型最佳 epoch 38、53 轮 early stop，
  最终复验 P/R/mAP50/mAP50-95=0.2376/0.4330/0.2332/0.1266。
- Stage A 继续作为宽位置表征底座，不从零推倒；但它机械使用 `anchor-2..decision`，实际框宽
  只有 4/5 根。旧 `dense_owner_w20_midbox` 保存了原始 Owner 5/7 根框；按 Stage A 修复后的
  event/time split 重新联结，2,378/2,378 事件完整对应。
- 旧W20–30候选中只有 **316** 个Stage-A train事件满足框后3–5根：delay3/4/5分别94/107/115，
  旧框宽5/7根分别171/145。位置为middle/right/far-right=36/265/15，**83.86%落在right带**；
  这批只能复核语义与边界，不能直接成为新短窗训练集。
- 模型已基本打掉固定右侧 shortcut：四位置桶 recall spread 14.72pp，位置-分数
  Spearman=-0.134；但精确度不合格。conf=0.10 event precision 22.66% / recall 52.79%，
  conf=0.20 precision 30.67% / recall 13.97%，阈值无法同时修复精确度与召回。
- 最大数据缺口：这316个旧框尚未按ETH两条边界线重新裁决；hard negative=0。V2审查页抽取
  200张：delay3/4/5分别80/65/55，按钮区分“形态和框都准 / 形态像但框要改 / 不是目标”。
  不看后续收益、不看模型置信度、不读取val/holdout。Owner裁决前全部
  `semantic_status=unreviewed / geometry_status=unreviewed / training_eligible=false`。
  诚实路径说明：316张母池中45张、200张审查中30张源文件物理路径位于旧
  `dense_owner_w20_midbox/images/val/`；这是历史按币种错split留下的目录名，按修复后的Stage-A
  时间split它们全部属于train，未使用358个Stage-A val事件。
- 已新增30张**动态短窗校准集**，不是训练集：post 3/4/5各10张，pre 6–10各6张，旧核心
  5/7根各15张；W实际14–22，框中心自然分布53%–71%，30个事件/币种全不重复。三张大图证明
  “固定最右/固定正中”的几何偏差已消除，但也直接暴露旧核心框仍会把部分明显启动大K包进去。
  因而全部保持`semantic_status=unreviewed`、`geometry_status=unreviewed_legacy_core_proposal`、
  `training_eligible=false`；动态重裁剪不能替代Owner语义/边界裁决。
- Codex已按唯一确认的ETH空头参考完成保守一审：`short_keep=5`、`short_rebox=4`、
  `short_hard_negative=4`、`mirror_unconfirmed=17`。4张rebox已把明显启动侧K从核心proposal
  移到3–5根确认区；代表板用绿=保留、橙=新框（红虚线=旧框）、红叉=难负例候选、紫=多头
  镜像。该一审不是Owner金标，全部`owner_confirmed=false / training_eligible=false`。
  当前必须先由Owner确认空头一审方向，并决定多头镜像是排除、独立类别还是方向归一化同类；
  决策前17张镜像既不是正例也不是负例。
- Owner随后明确回复“确认”，冻结为**只做空**，认可绿/橙/红代表板方向，并授权把冻结train事件
  扩到200张动态短窗；多头镜像排除且不得当负例。确认回执明确：只授权扩200，不授权训练、
  holdout、生产，也不自动确认200张逐样本标签。
- 200张已按相同事件重渲染完成：post3/4/5=80/65/55、core5/7=100/100、pre6–10各40、
  W14–22、112 symbols、0重复、0 val图/标签、0 holdout行。Codex逐板一审为：
  `short_keep=40`、`short_rebox_pending=61`、`short_hard_negative=25`、`mirror_excluded=74`。
  61张橙桶已完成逐图编号、启动边界判定和重画：没有统一左移，形成14种起止位移；新核心
  4/5/6/7根=2/56/2/1，post3/4/5=25/20/16，新完整窗W14–20，框中心自然为53.3%–71.9%。
  每个新核心都早于旧核心结束，橙色新框与红色旧框已在7张对照板重渲染；最大读取时间
  2026-03-18 12:15 UTC，0 holdout。几何已完成但仍是Codex proposal，当前200张全部
  `sample_owner_confirmed=false / training_eligible=false`，未开训。**该61张手工proposal现已被
  Owner否决为标签来源**；不得再请求批量认可或写入正式数据集。
- 已恢复真正Owner金标血缘：独立Owner框2,525个中，Owner亲自确认short为1,361框/1,317图；
  再与原始`⭐标杆`坐标逐框IoU=1.000联结得到71框，69框仍有未加工原PNG、2框原图缺失而诚实
  跳过。69张新橙框全部取原红框正中心，4/5/6/7根=24/16/12/17；外层W12–17，后文3/4根=
  56/13。无Codex重画、无模型预测、几何不看未来；审核未来48根独立。当前仍
  `training_eligible=false`，Owner只需确认“原框中心裁切”合同，确认后按同法扩1,361个short框。
- Owner已回复“可以”，中心裁切合同通过并已扩全量：1,361行short标注全部定位；独立审计发现
  15组为同一市场窗+同一核心框的历史别名，已在split前去重为1,346个目标，并把全部原Owner id
  保留在canonical manifest。重叠输入仍为1,287个依赖块；按块时间切分+150 bars purge后，
  train/val/drop正例=1,143/202/1，实际间隔162 bars。配套同币、同split、同W真实空背景
  train/val=1,143/200；2个val无安全背景而诚实缺失。0联合SHA重复、0跨split、0负例碰全部
  Owner框±12根；同路径二次重建正/负manifest SHA完全一致。当前为交接规范的1:1 easy-negative
  首臂，只用于挖hard negatives；第二臂必须是1:2/1:3且hard占大头，不能直接堆三倍easy。
- Owner于2026-08-11 13:29 CST明确“直接去训练吧”。3060 run
  `owner_lsv2_short_gold_center_v1_ft`已完成：Stage A best初始化、YOLO11s、imgsz960、batch8、
  seed0、40 epochs、patience10、显式finetune AdamW lr0=1e-4；flip/mosaic/mixup/HSV全0。
  40/40耗时1,833.54秒，best epoch=30；epoch记录P/R/mAP50/mAP50-95=
  0.8619/0.9010/0.9244/0.7427，3060最终best复验=0.8508/0.9035/0.9224/0.7302，
  Mac MPS独立复验=0.8467/0.9024/0.9206/0.7294。远端/本地best SHA均为
  `da278820…fc65b4`。曲线早期曾从mAP50 0.6468跌至0.0799再恢复，且当前val只是平衡Owner正例
  与easy背景，**不得用高mAP宣布成功**。下一步冻结best扫训练时间块连续窗口，挖hard negatives并
  建1:2/1:3（hard为主）第二臂；仍`production_eligible=false / auto_promote=false`，不读holdout、
  不改ACTIVE、不部署。
- 第二训练臂的数据已按交接规范完成并审计：train正例1,143、easy negative 1,143、hard
  negative 2,286，总负例:正例=3:1，hard占负例2/3；val仍冻结为202正例+200 easy背景，没有拿
  holdout或未来收益挑负例。hard来源为916个Owner-long语义负例和1,370个仅在原train时间块由
  当前模型排序挖出的背景；200张独立审计页已生成。Owner于2026-08-11 16:12 CST明确回复
  “允许 开始吧”，逐次授权run `owner_lsv2_short_gold_center_hardneg_r1_ft`。该run已完整跑满40轮，
  总耗时3423.21秒，best SHA=`029f80a5…f537`；Stage A初始化、epochs40、patience10、batch8、
  seed0、AdamW lr0=1e-4、warmup0.5及全禁用增强均与1:1 baseline一致。Mac独立固定val
  P/R/mAP50/mAP50-95=0.8626/0.7770/0.8980/0.7405，模型更保守但不能仅凭mAP晋升。
- 新旧模型已在固定post-val、pre-holdout连续12小时canary同场扫描：215币、10,320 endpoints、
  82,560 W12–19 exposures/模型，最大物理读取时间2026-05-03 12:00 UTC，holdout读取0。原始命中
  22,037→8,268（-62.48%），去重事件732→331（-54.78%），全市场折算1,464→662 events/day，
  触发币177→140。方向有效但密度仍失败，当前权重禁止promote；先人工复核331事件，再决定是否
  建第三臂。报告：`analysis/html/p2_owner_short_gold_center_hardneg_canary_20260811.html`。
- 上述331个事件已全部做成逐张Owner审核页，不是抽样或订单：140币，首次conf median/p90=
  0.3627/0.6553，事件peak conf median/p90=0.6344/0.8699。每个事件物理隔离为原始因果输入、
  橙框审核副本、最多未来48根对照，共993张PNG；328张有完整48根、3张47根，最大读取时间
  2026-05-03 23:45 UTC，0 holdout、0 labels、0 training-eligible、0预选。审核V2已按Owner反馈
  缩成三项：`1=对 / 2=框偏 / 3=不对`，按键后自动下一张，`Z`撤销；延续重复改由相邻事件关系
  另行诊断，不再占Owner主按钮。选择只存在本机并可导出完整JSON。331只够做错误分类种子，
  不够直接承担第三臂全部hard negatives；Owner裁决后还要从多个未使用pre-holdout块补挖。审核入口：
  `analysis/html/p2_owner_short_gold_center_hardneg_canary_review331_20260811.html`；报告：
  `analysis/html/p2_owner_short_gold_center_hardneg_canary_review331_report_20260811.html`。
- Owner随后发现未来对照K线普遍视觉过平；根因不是行情或数据，而是V2复用了训练renderer的6%
  最小纵轴跨度。291/331张（87.92%）实际图幅不足6%，实际波幅p10/median/p90=
  1.50%/2.99%/6.75%。审核V3已改为每图按真实OHLC+六均线极值自动缩放，图头和卡片显示真实
  波幅；331张未来图已重渲染。V2→V3的331张原始因果图和331张带框因果图SHA逐行不变，只有
  未来审核图变化；仍0 holdout、0 labels、0 training-eligible，审核入口路径不变。
- Owner已完成V3全部331个事件裁决：66对、11框偏、254不对、0 pending；协议、源SHA、ID集合和
  声明计数全部通过一对一质量门。该12小时块精确event precision=19.94%，把框偏也算形态命中时
  semantic precision=23.26%，明确负例率76.74%。peak conf有排序但不能替代重训：≥0.8语义
  precision=50.63%且只保留40/77语义正例；≥0.9虽为81.25%，仅保留13/77。254个Owner负例覆盖
  125币，但事件时间2026-05-03 00:15–12:00 UTC严格晚于冻结val末端2026-05-02 23:45，故只能
  作错误参考尺，直接回流训练会破坏时间切分。下一步必须在冻结train末端2026-03-13 18:30之前
  挖出同类负例，再建保持总量/W桶/val/配方不变的第三臂。报告：
  `analysis/html/p2_owner_short_hardneg_canary_owner_review_20260811.html`。
- Owner随后明确要求用刚训练权重回放最近2天并发TG，登记为该配置第1次消耗holdout。一次性OKX
  快照覆盖214/215个训练分布币种，W12–19逐bar扫描328,704窗，71,204条原始命中去重为2,500
  个事件，即60.845 events/1000 bar endpoints、1,250 events/day、5.84 events/币/天；211/214
  币触发，密度明确失败。2,091个已了结事件净@taker均值-0.203%；严格同event_id成对的1,498组
  为事件-0.231%、同币×同日×ATR桶随机+0.170%、差值-0.401%。不得在本次holdout上调阈值，
  当前权重禁止promote。
- 几何/时序并非全错：94.92%核心为4–7根，98.12%首次确认延迟为3–5根；模型精确命中Owner的
  ETH参考核心（2026-08-10 19:30–20:15 CST，4根，21:00决策，延迟3根，conf_max=0.905，
  纸面TP净@taker+1.020%）。但相邻20:45–21:45又产生一个延续事件，说明“能命中终极样例”与
  “全市场precision合格”必须分开裁决，后者当前失败。
- TG交付已核对：摘要、25/25张信号图（ETH目标为第1张）、HTML和全量事件CSV全部发送成功；
  回执在`analysis/output/owner_short_gold_center_recent2d_v1/telegram_receipt.json`。详细报告：
  `analysis/html/p1_owner_short_gold_center_recent2d_holdout_20260811.html`。
- 数量口径：本机原始缓存有602个CSV（1.2GB），其中456个15m文件，237个至少覆盖365天，
  单文件最长约430天。Stage-A可追溯正事件池为2,378个（train 2,020 / val 358），不是200个；
  当前200只是从2020个train中满足旧框后3–5根条件的316个候选里抽出的语义校准包。
  “3–5”是3–5根15m K（45–75分钟），不是3–5天。正式训练数据必须在Owner确认语义后回到完整
  历史池扩正例与难负例，禁止拿200张校准包直接开训。
- Owner审核页已改为双图：左图是冻结训练短窗，右图额外显示未来48根/12小时。未来图只存在
  `review_future_only/`及独立manifest，紫线标记训练截止；61张训练图生成前后SHA逐字节不变，
  未来目录无labels、0 holdout。HTML可逐张选择“认可/还要改/剔除”，也可浏览后全部认可并复制JSON。
- 所有旧数据、权重、日志、候选 ledger 和失败对照臂均保留。固定右侧/causal blank 臂只停用，
  不删除。延迟形态检测器仍 `production_eligible=false`，不得冒充新鲜信号进入 forward/ACTIVE/
  部署；若未来用于执行，必须另批完整窗口右端时间戳和延迟预算。

V2审查页：`analysis/output/owner_eth_target_review_v2_shortdelay/index.html`。30张动态校准图：
`analysis/output/owner_eth_shortdelay_calibration30_v1/`。Owner已回到当前会话，当前无需TG；
优先直接在对话中展示PNG。Codex空头一审与代表板：
`analysis/output/owner_eth_shortdelay_codex_firstpass_v1/`。动态200与一审：
`analysis/output/owner_eth_shortdelay_dynamic_review200_v1/`、
`analysis/output/owner_eth_shortdelay_review200_codex_firstpass_v1/`。61张逐图改框与7张对照板：
`analysis/output/owner_eth_shortdelay_review200_rebox_v1/`。
Owner交互确认页：`analysis/html/p1_owner_eth_shortdelay_review61_owner_gate_20260811.html`；未来审核图：
`analysis/output/owner_eth_shortdelay_review200_rebox_v1/review_future_only/`。
上述61页只保留失败对照。当前Owner审核入口：
`analysis/html/p1_owner_gold_center_crop_owner_gate_20260811.html`；源/短窗/未来三联图：
`analysis/output/owner_gold_center_crop_review_v1/`。
全量数据：`datasets/owner_short_gold_center_v1/`；64张正/背景配对审计：
`analysis/html/p1_owner_short_gold_center_dataset_audit_20260811.html`；报告：
`analysis/html/p1_owner_short_gold_center_dataset_20260811.html`。

## ⚡ 历史阶段（2026-08-11 — Owner 授权恢复真正的 Stage A 随机裁剪）

**直接裁决：`causal_blank_w30_v3` 只改变画布 X，框相对真实 K 线内容仍贴在最后几根，
Owner 已目视否决。该数据即使九道旧 P0 为绿也不得训练。Owner 随后明确授权恢复交接文档
Stage A 离线预训练：必须改变原始连续 K 线的 `crop_start_bar`，让框在真实 K 线序列中落入
左/中/右不同位置。**

- 授权范围：历史 pre-holdout Stage A 形态表征预训练；允许窗口包含 decision 后真实 K。
- 禁止范围：不得把 Stage A 指标当实盘结论，不得直接进入 tip-smoke/forward/ACTIVE/部署。
- 最终裁决：仍只认严格因果 Stage B + 真 tip；Stage A 权重只能作为 Stage B 初始化。
- 旧 `dense_owner_w20_midbox` 虽是真随机裁剪，但按币种哈希切分、含 246 条 holdout、
  2,300 个 unmanifested 图，故只能作为失败证据，不能恢复训练。
- 新版冻结目标：W20–30、Mode C delay 1/2、box=`anchor-2..decision`、anchor X 四桶目标
  20%/35%/30%/15%，一 event 一 crop、时间切分 + 150 bars purge、完整窗口早于 holdout。

新版 `local_signal_v2_stagea_randomcrop_v1` 已完成：2,378 正例 + 2,378 easy negatives；
train 4,040、val 716。24 张独立预览按四个真实 K 线位置桶各 6 张，anchor X 为
20%–85%，每个正框右侧仍有 1–22 根真实 K；不是右边加白。正例实际位置占比
20.14% / 35.79% / 29.31% / 14.76%，最大偏差 0.79pp。时间切分 + 150 bars purge、
0 holdout、0 跨 split、4,756 image/label/manifest 守恒，十道 Stage A P0 门全绿。
同 seed 二次重建正/负 manifest SHA 分别稳定为 `ae4675a6…e89` / `0fdced5c…3f0`。

Owner 于 2026-08-11 06:33 CST 明确授权上传并开训；3060 `zzc@192.168.1.4` 上的
`owner_lsv2_stagea_randomcrop_v1_cold` 已正常完成。配置为 YOLO11s、imgsz 960、batch 8、
seed 0、epochs 60、patience 15；flip/mosaic/mixup/HSV 全为 0。early stopping 于 53 轮结束，
最佳 epoch=38，总耗时 4,207.2s；最终复验 P/R/mAP50/mAP50-95 =
0.2376 / 0.4330 / 0.2332 / 0.1266。远端与本地 `best.pt` SHA-256 均为
`c0e94f47…bf1a`，训练目录和日志已取回独立 Stage A 路径。

按推理前冻结的真实 K 线位置门，conf=0.05 四桶 recall 为
84.72% / 74.29% / 75.47% / 70.00%，最大差 14.72pp（门 20pp）；anchor X 与 IoU-matched
score 的 Spearman=-0.134（门 |rho|≤0.20），三门全绿，确认不再只认真实内容最右端。但同一
阈值 easy-negative fire=26.54%、event precision=15.23%，**安静度未解决**；conf=0.35 虽近乎
静默，recall 也只剩 0.28%，禁止靠沿用/抬高旧阈值宣布成功。

当前停止点：Stage A 仅通过“位置表征”诊断，可作为严格因果 Stage B 的初始化；仍标记
`production_eligible=false`，不得 promote/forward/ACTIVE/部署。下一步固定 Stage A 权重，
只做因果 Stage B 微调；随后才从新模型收集 hard negatives 并验证连续窗口密度。

## ⚡ 当前真相（2026-08-11 — Owner 发现 B2/P2 固定最右位置 shortcut；P2 训练已停）

**直接裁决：三张 200 样本审计图不是显示问题。B2 固定 30 根因果窗的正框中心只落在
0.931034 / 0.948276 两个 X 比例，100% 集中于最右带，未满足交接规范 Stage-B 65%–95%
且不得固定 95% 的要求。现有 P2 hard-negative 路线建立在错误几何上，已作废，不能继续训练。**

- Owner 在 600 样本 montage 中直接指出“信号框怎么全是在最右边”；代码审计确认
  `visible_end=decision`、固定 `W=30`、`confirm_delay=1/2` 必然只产生上述两个位置。
- Windows 3060 的 P2 训练已精确停止；远端 `best.pt`、`last.pt`、`results.csv` 均不存在，
  因而没有可误用权重。远端数据保留，未删除其他任务或文件。
- 不能把位置修复塞进冻结 P2：那会同时改变布局与 hard negatives，违反单变量纪律。
- 已预注册独立位置臂：固定 B2 的 30 根可见 K、事件、split、seed、标签和 easy negatives，
  唯一变量为右侧 0–12 个纯空白画布槽位；不追加未来 K，目标框中心覆盖 65%–95%。
- 旧 renderer 像素合同不改；新增 opt-in Local-Signal V2 renderer，0 空白时必须逐像素等同旧版。
- 未读 holdout、未改阈值/成本/障碍/ACTIVE，未 promote、未部署、未下单。

独立 `local_signal_v2_p1_causal_blank_w30_v3` 已在 builder 提交后全量重建：2,388 正例 +
2,388 easy negatives；九道 P0 门全绿。框中心 0.6585–0.9483，四桶 732/625/572/459，
正负均覆盖 0–12 全部空白槽；0 future、0 holdout、0 跨 split、0 越界、4,776 文件守恒。
同 seed 二次重建后正/负 manifest SHA 分别稳定为 `f82a4910…43a1` / `83575284…94c8`。

下一步：该数据臂已具备训练前提，但尚未启动训练。需保持旧阈值、seed、训练配方和事件尺，
只评估位置布局这一个变量；通过后再从其冻结权重重新做 P2 hard-negative mining。

## ⚡ 当前真相（2026-08-11 — B2 候选密度失败；3,880 不是订单）

**直接裁决：上一版把 3,880 写成“交易/开单”是口径错误；它们是 B2 在 v10 预筛
proposal ledger 上的 L1 fire rows，不是订单。但密度复核也证实当前 B2 确实放得过宽，
因此停在 P2 hard-negative mining，P3 判断层不得提前启动。**

- P1 统一尺是 715 个平衡抽样 endpoints（358 正例 + 357 easy negatives），不是连续市场暴露。
  conf=0.35 在 easy negatives 上命中 56/357 = **15.69%**。
- v10 short-L2 pool 是已经预筛且同币至少间隔 18 bars 的 proposal ledger，不是订单流。
  B2 命中 3,880/7,795 = **49.78%**，按 ledger 跨度为 **88.27 L1 fires/日**。
- 3,880 个 fire rows 只去重成 3,715 个 outcome event groups，减少 4.25%；candidate_id 唯一，
  同币最小间隔 18 bars，edge2=edge3，数组/PNG 8 样本推理完全一致。高计数不是重复、edge
  或图像传输 bug。
- 不能靠抬 conf 修：0.45 时密度降到 8.35 fires/日，但验证召回从 73.46% 塌到 6.98%。
- 连续市场逐币×逐盘口 endpoint 尚未扫描，真实 L1 fires/日与可执行订单数均未知；禁止把
  88.27 或 3,880 外推为生产订单。
- 把每个 fire row 强行当 short 的反事实收益仍为负：10bp 后 -9.19bp、PF 0.893；匹配
  超额 +2.18bp 但 p=0.890625。该结果不是订单回测。
- 未读 holdout、未改阈值/成本/障碍/新鲜度，未 promote、未部署、未下单。

交付物：

- `analysis/html/p1_b2_short_l2_backtest_20260811.html`
- `analysis/p1_b2_short_l2_backtest_20260811.md`
- `analysis/output/p1_b2_short_l2_backtest_20260811.json`
- `analysis/output/p1_b2_density_diagnostic_20260811.json`
- `analysis/output/p1_b2_short_l2_backtest_20260811_{rows,selected,matched}.csv`
- `analysis/output/p1_b2_short_l2_backtest_report_20260811/{daily,symbol}.csv`

**下一方向：按交接规范做 P2 hard-negative mining + 连续因果 tip 密度回放。** 固定 B2
30 根窗口、事件尺和训练配方，只增加难负例；先冻结并验证 L1 密度门、event 匹配和去重规则。
只有 P2 密度与事件门通过后才进入 P3 LightGBM/规则判断层。禁止用提高 conf 代替重训。

## ⚡ 当前真相（2026-08-11 — Local Signal V2 P1 历史发现级通过，B2 胜出）

**直接裁决：30 根固定因果窗 B2 通过冻结事件门并成为 P1 候选；只接受历史发现，不具备生产资格。**

- 统一尺 715 endpoints（358 正事件 + 357 easy negatives），最大时间 2026-05-03 10:45 UTC；
  holdout 消耗 0。
- 冻结绝对门：Event Precision≥0.50、Recall≥0.50、FP/1000≤250。A 旧模型最大 Recall
  仅 0.0754，无法建立同 Recall 相对门；绝对门在候选结果前冻结。
- B2 fixed-30 @ conf=0.35：P=0.8193、R=0.7346、F1=0.7747、FP/1000=81.12、
  duplicates/event=0.0076，PASS / selected。
- C3 range-20–30 @ conf=0.45：P=0.7471、R=0.7095、F1=0.7278、FP/1000=120.28，PASS。
- B1 fixed-24 没有合格工作点：best-F1 @ 0.10 时 P=0.3543、R=0.9916、FP/1000=904.90，FAIL。
- dataset seed=20260807；三臂实际 training seed=0。字段歧义已勘误，trainer/3060 wrapper
  现在显式传递 seed；没有 seed sweep。
- 568 tests passed / 2 skipped。未读 holdout、未改 ACTIVE、未 promote、未部署、未下单。

交付物：

- `analysis/html/p1_local_signal_v2_report_20260811.html`
- `analysis/p1_local_signal_v2_report_20260811.md`
- `reports/P1_EXPERIMENT_REPORT.md`
- `reports/ACCEPTANCE_DECISION.json`
- `analysis/output/p1_local_signal_v2/comparison.json`
- `analysis/output/p1_local_signal_v2/training/B2/weights/best.pt`

**停止点：P1。** 下一步由 owner 决定是否以 B2 30 根窗为固定基线，只增加 hard-negative
mining 这一变量进入 P2；禁止自动 promote、读 holdout、部署或下单。

## ⚡ 当前真相（2026-08-10 — Local Signal V2 P0 修复通过，停在 owner gate）

**直接裁决：旧 Stage-B V1 的 P0 全绿是误报；strict-negative V2 已修复并通过 P0。**

- V1 positives 按时间切分，但 negatives 只继承 split 名称、候选来自全段历史：317 条 train
  negatives 晚于 train 截止，296 条 val negatives 早于 val 起点。
- 原 auditor 只审 positives，现已改为检查每个正/负窗口完整 `[start, end]`；V1 命令返回 1，
  strict-negative V2 八道门全绿。
- 新数据集 2,388 positive + 2,388 easy negative；train 4,060、val 716；0 holdout、0 event
  跨 split、0 label 越界、4,776 image/label/manifest 守恒、100% market-bar 可追溯。
- 同 seed 原地全量重跑两次，positive manifest SHA `6814b86c…b047`、negative manifest SHA
  `2cdcf889…13ba` 均逐字节不变；24-event preview 覆盖 24 个不同 symbol。
- Builder/auditor 已先提交为 `471f854`，数据随后从该 HEAD 全量重建，满足“builder 先入 Git”纪律。
- 旧 `owner_lsv2_stageb_cold` 绑定 V1 且训练时 HSV 非零，已 invalidated；不得作为新 V2 候选。
- 未来 3060 训练入口会下发仓库内 `src/detection/train.py`，不再调用远端未跟踪 trainer；
  flip/mosaic/mixup/HSV 全关。
- 未训练、未读 holdout、未改 ACTIVE、未部署、未下单。

交付物：

- `analysis/html/p0_local_signal_v2_stageb_strictneg_v2_report.html`
- `analysis/output/p0_local_signal_v2_stageb_strictneg_v2_audit.json`
- `datasets/local_signal_v2_stageb_strictneg_v2/manifest.jsonl`
- `reports/ACCEPTANCE_DECISION.json`

**停止点：P0。** 按交接规范 §14 等 owner 决定是否启动 P1 A/B/C 对照；不自动训练。

## ⚡ 当前真相（2026-08-03 — P2-M 只读机制审计已完成，必须停止）

**直接裁决：raw return IC 大部分含 ATR/barrier 尺度成分，但有小幅 scale-robust 残余；
P2 仍为 REJECTED，禁止据此选 feature 或训练。** P2-M 唯一数据源是 P1 immutable dataset。

- TP / SL gross return 在 ATR 单位上精确为 **+5 / -2**，确认 raw return 同时编码 outcome
  probability 与 ATR-scaled payout magnitude。
- P2-R frozen stable 20 features 中，14/20（70%）在 TP label、ATR-normalized gross、折内
  ATR quintile net IC 三条控制线上都衰减到 raw IC 的 50% 内；但未达到预注册 75% 全局门，
  所以 `global_mechanical_dominance=false`。
- 8/20 在三条控制线上仍满足 4/5 折同号且 abs median rho≥0.03，
  `global_scale_robust_signal=true`；其中 3 个同时 mechanical+robust。scale-robust 只是残余关联，
  不等于因果、经济 edge 或 feature shortlist。
- 五折 rows 2,937 / 2,918 / 2,996 / 2,944 / 3,000；P1 18,103 rows / 230 symbols；
  max signal 2026-05-03 05:15 UTC，max label end 2026-05-03 22:45 UTC，0 holdout。
- P2-M 专项 7 passed；完整 tests 513 passed / 2 skipped / 14 warnings / 0 failed。
- 未训练、未拟合、未选 feature、未调 threshold、未读 holdout、未改 ACTIVE、未部署、未下单；
  ACTIVE / forward log / ledger hash 不变，active bundle 不存在。

交付物：

- `analysis/html/p2m_readonly_mechanism_audit_20260803.html`
- `analysis/p2m_readonly_mechanism_audit_20260803.md`
- `analysis/output/p2m_mechanism_prereg_20260803.json`
- `analysis/output/p2m_mechanism_audit_20260803.json`
- `analysis/output/p2m_feature_mechanism_20260803.csv`
- `analysis/output/p2m_fold_target_mechanism_20260803.csv`
- `analysis/output/p2m_test_results_20260803.json`
- `analysis/output/p2m_hashes_20260803.sha256`

**停止点：P2-M。** `training_allowed=false`、`threshold_change_supported=false`。未来若 Owner
另行授权，只能先选一个单变量问题（target mechanism / one feature family / fresh-forward），
不得把相同 P1 的自适应结果包装成独立 confirmation。

## ⚡ 当前真相（2026-08-03 — P2-R 只读根因审计已完成，必须停止）

**直接裁决：P2 仍为 REJECTED；失败不是只改 q90 可以修复。** P2-R 只读取 P1 immutable
dataset 与 hash 冻结的 P2 产物，未训练、未读 holdout、未改 ACTIVE、未部署、未下单。

- 独立重建五个 test folds，rows 精确复现 2,937 / 2,918 / 2,996 / 2,944 / 3,000；P1
  18,103 行，max signal 2026-05-03 05:15 UTC，max label end 2026-05-03 22:45 UTC，0 holdout。
- fold-local exact-top 4/5 折 pressure-net≤0；加权 **-15.91bp**。同期整池 **-15.33bp**，
  exact-top 相对整池 **-0.59bp**，所以 ranking 没有证明增量，调 fixed threshold 不能救。
- matched control 从冻结 CSV 独立复算：1,051 pairs、12 UTC-week blocks、lift +0.74bp、
  exact sign-flip `p=0.4836`；pair ID / delta 完整性全过。
- outcome regime 明显漂移：TP-before-SL 五折 range 19.52pp，整池 pressure range 84.79bp；
  fold 2 / 4 都 collapse 到 best_iteration=1 / 15 distinct scores，fixed pass 有 4/5 折脱离
  8%–12%。这些是 contributor，不单独构成因果证明。
- 28 features 无 missing / inf；20 个满足预注册的跨折 Spearman 稳定规则。但 P2-R 已查看
  全部 feature × outcome；今后从中挑 feature 在相同 P1 重跑只能标 exploratory，不能重新
  作为独立 P2 acceptance。
- P2-R 专项 7 passed；完整 tests 506 passed / 2 skipped / 14 warnings / 0 failed。
- ACTIVE / forward log / ledger SHA 不变；active bundle 不存在；holdout 消耗 0。

交付物：

- `analysis/html/p2r_readonly_root_cause_audit_20260803.html`
- `analysis/p2r_readonly_root_cause_audit_20260803.md`
- `analysis/output/p2r_root_cause_prereg_20260803.json`
- `analysis/output/p2r_root_cause_audit_20260803.json`
- `analysis/output/p2r_feature_ic_20260803.csv`
- `analysis/output/p2r_fold_diagnostics_20260803.csv`
- `analysis/output/p2r_test_results_20260803.json`
- `analysis/output/p2r_hashes_20260803.sha256`

**停止点：P2-R。** 不调 threshold、不继续训练、不读 holdout、不创建/修改 ACTIVE bundle、
不部署、不下单。未来若 Owner 另行授权，只能先立新的单变量 exploratory 预注册；相同 P1
不能再提供独立确认，确认需要预注册后未参与选择的新鲜前向样本。

## ⚡ 当前真相（2026-08-03 — P2-L2 已完成且 REJECTED，必须停止）

**直接裁决：P2-L2 训练/验证流程完成，策略门失败。** 只使用 P1 immutable dataset；
artifact integrity audit accepted，但 strategy verdict 是 **rejected**。

- 主模型 best_iteration=1、1 tree、15 distinct scores；calibration q90 `>=` 实际 pass
  85.51%，threshold equality 81.23%，模型/selector health 全失败。
- 5-fold fixed runtime gate 只有 1/5 折 pressure-net>0；聚合 4,723 selected、pass 31.92%、
  pressure-net **-39.33bp**、PF 0.641。单特征 baseline 为 -22.67bp，反而少亏 16.66bp。
- 逐折 exact-top 也只有 1/5 为正；按 fold top-n 正确加权后为 **-15.91bp**。
- matched candidate control 1,051 pairs / coverage 22.25%；lift +0.74bp，UTC-week exact
  block permutation `p=0.4836`，未过 0.01。
- 初版曾错误 pooling 不同 fold 模型 raw scores 得到 +9.04bp；独立审计发现后未重训，改为
  foldwise aggregation，结论 -15.91bp；该纠错不影响 fixed gate 或最终 rejected。
- full tests 499 passed / 2 skipped；独立产物审计 17/17 true。
- ACTIVE / forward log / ledger SHA 不变；active bundle 不存在；未读 holdout、未部署、未访问
  trading client、未下单。

交付物：

- `analysis/html/p2_l2_preholdout_validation_20260803.html`
- `analysis/p2_l2_preholdout_validation_20260803.md`
- `analysis/output/p2_l2_results_20260803.json`
- `analysis/output/p2_l2_independent_audit_20260803.json`
- `analysis/output/p2_l2_selector_manifest_20260803.json`（research-only / execution=false）
- `analysis/output/p2_l2_dataset_binding_20260803.json`
- `analysis/output/p2_l2_hashes_20260803.sha256`
- `analysis/output/p2_l2_test_results_20260803.json`

**停止点：P2.7。** 不进入 P3，不改模型/threshold/cost，不读 holdout，不做 ACTIVE/bundle、
deploy 或 order。后续任何动作需要 Owner 新指令与新预注册。

## ⚡ 当前真相（2026-08-03 — P2.0 审计通过，P2.1 Owner 门已批准）

**P2 尚未训练。** 只读审计重新加载了唯一 P1 immutable dataset，并完成时间三段、完整
label interval / event-group purge。Owner 已以“批准”确认成本压力线和 fixed gate，机器
预注册当前为 `status=accepted`、`p2_training_allowed=true`。

- dataset SHA `aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a`；
  18,103 行 / 230 币 / 15,604 event groups；holdout signal / interval 均为 0。
- 固定三段在完整 event-group purge 后为 train 10,940、early-stop 3,498、calibration
  3,623；42 行 / 32 组被 purge，跨段 event group=0。
- fixture 发现并修复“穿越边界行删除后，同组邻居仍留在下一段”的依赖泄漏；现在触边会
  清除整个连接分量。
- 预注册推荐：LightGBM regression、target=`net_ret_swap_taker`、28 frozen features、无参数
  扫描、5-fold expanding walkforward、matched candidate control、UTC-week economic block
  permutation；AUC 不作成功裁判。
- Owner 已批准：①实际成本总 RT 0.15%，即 P1 taker-net 再减 5bp，P1-only 范围不含
  funding；②固定 gate 为 calibration q90、`>=`、可分边界取中点、并列整块通过、pass
  8%–12%、equal≤2%、不切 ties。
- 未训练、未在真实分数上校准 threshold、未读 holdout、未改 ACTIVE、未建 active bundle、
  未部署、未访问交易 client、未下单。

交付物：

- `analysis/p2_l2_audit_and_prereg_20260803.md`
- `analysis/html/p2_l2_audit_and_prereg_20260803.html`
- `analysis/output/p2_l2_audit_20260803.json`
- `analysis/output/p2_l2_prereg_20260803.json`
- `analysis/output/p2_prereg_test_results_20260803.json`（492 passed / 2 skipped）

**下一动作**：先跑 fixture 与小样本 dry-run，二者通过后才执行 full P2 训练验证；仍禁止
holdout、ACTIVE、active bundle、部署与订单。

## ⚡ 当前真相（2026-08-03 — P1-DATA 已完成，必须停止）

**直接裁决：P1 pre-holdout immutable short L2 dataset 重建通过；不得自动进入 P2。**
前置 `p0_independent_acceptance=accepted`；P1.0–P1.7 的 input snapshot、schema、canonical
路径、fixture、真实 dry-run、proposal-led full build、机器审计、fail-closed loader、报告均完成。

- canonical dataset：`data/p1/p1_short_l2_preholdout_aade2a334448d644.csv`
  - SHA256 `aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a`
  - 18,103 行 / 230 币 / 2026-02-01 01:00 → 2026-05-03 05:15 UTC
- manifest：`analysis/output/p1_dataset_manifest_20260803.json`
  - SHA256 `53b8a07612dae667a184da38bf8e0a694aaae15a5fd240d5b13238da3e13d682`
  - `training_eligible=true` 只表示 P1 数据门通过，不是训练授权。
- 冻结 source proposal 18,379 条全部数量守恒：18,103 dataset rows + 274 无 selected
  candidate + 2 canonical outcome reject；0 holdout signal、0 post-cutoff OHLC materialized。
- full replay 只消费冻结 L1 proposal 的 exact causal windows；344 current live universe 全记账，
  112 个零 proposal 币不读 K 线，不做历史负窗 L1 mining。
- fixture/dry-run accepted；full machine verdict accepted；fail-closed loader 复读 18,103 行；
  完整 `tests/` 为 488 passed、2 skipped、0 failed、0 deselected。
- P1.0 与 full 后 `models/ACTIVE`、`data/forward_log.csv`、
  `data/executor_ledger.jsonl` SHA 均不变；`models/active_bundle.json` 不存在。
- 未训练、未调 threshold、未读 holdout OHLC、未建 active bundle、未改 ACTIVE、未部署、未下单。

交付物：

- `analysis/html/p1_preholdout_dataset_rebuild_20260803.html`
- `analysis/p1_preholdout_dataset_rebuild_20260803.md`
- `analysis/output/p1_preholdout_dataset_rebuild_20260803.json`
- `analysis/output/p1_dataset_manifest_20260803.json`
- `analysis/output/p1_dataset_hashes_20260803.sha256`
- `analysis/output/p1_test_results_20260803.json`

**停止点：P1.7。** 下一步无论是训练、threshold/selector、P2、active bundle、ACTIVE、部署或
下单，都需要 owner 新指令；当前不得继续。

## ⚡ 当前真相（2026-08-03 — P0-SAFETY 已完成，必须停在 Owner gate）

**直接裁决：P0 本地安全验收通过，但当前策略不可执行。** `models/active_bundle.json`
不存在；example bundle 只描述 v10 的 `legacy_unaligned + abnormal tie mass + paper_only +
execution_eligible=false`，所以 production 会 fail-closed。v10 是 **legacy / audit-only**，
不是 paper/live active bundle。

- H1–H7 均在完整仓库中确认并完成 P0 隔离/修复：short→buy、ACTIVE/研究配置错认、q90
  大并列、return/cost 双扣、latest fallback、decision/fill 倒置、global tip-3。
- runtime parity **REJECTED**：ACTIVE 是 28 特征、1 棵树、固定门 pass 91.13%；历史研究参考
  是 47 特征、每折 250 轮、折内十分位。研究 `+23.49bp` 不得归给 ACTIVE。
- canonical outcome 已统一；TP5/SL2/72 只做显式化，没有改经济参数。无 fill 时 actual PnL
  为空；paper 只取 decision 后第一根 future open；broker fill 只认 ledger。
- 最终 global tip age `<=2`，局部 edge/global age reject 分开计数。
- 全量安全测试：472 passed、2 skipped、1 deselected、0 failed；deselect 原因是本机没有可选
  `torchvision`。原始全量结果保留为 472 passed、2 skipped、1 dependency failure。
- P0 前后 `models/ACTIVE`、`data/forward_log.csv`、`data/executor_ledger.jsonl` SHA 均未变。
- 未训练、未碰 holdout、未 deploy、未 promote、未清账、未下任何真实或 demo 订单。

交付物：

- `analysis/html/p0_safety_protocol_repair_20260803.html`
- `analysis/html/p0_runtime_parity_audit_20260803.html`
- `analysis/output/p0_runtime_parity_audit_20260803.json`
- `analysis/output/p0_safety_baseline_20260803/`

**下一步必须由 Owner 明确授权。** 优先决策：short return convention → P1 pre-holdout
immutable dataset rebuild → P2 成本/selector gate → active bundle cutover。不得自动进入 P1、
激活 bundle、归档 forward log、实现/启用 short executor 或恢复部署。

## ⚡ 当前真相（2026-07-30 — 认知颠倒：判断层是唯一有效环节，检测层在拖后腿）

> **完整交接文档:`analysis/STATE_20260730.md`** —— 三天工作、误判记录、下一步优先级都在那里。
> 本节只放最关键的。

### 一句话
**三天前以为「检测层做好了、判断层是短板」,实际相反。** v10 检测器的候选比随机做空
还差 6bp,九种出场规则全部无效,而判断层挑单在两个不重合的候选池上都稳定给出 +17.8bp。

### v10 候选池(18,379 笔 / 232 币,`data/judgment_v10_wide.csv`)
```
候选池均值         -6.41bp   (已扣 10bp 成本)
匹配随机做空       -0.39bp
→ 检测器因果贡献   -6.02bp   ← 开火本身是负价值
顶十分位          +11.35bp   ← 唯一为正
判断层顶档提升    +17.76bp,15/15 折全为正
```

### 唯一在两个独立池上都站住的结论
```
老池(tip_v1b,25,602 笔)  +17.82bp
v10 池(18,379 笔)        +17.76bp   (回归目标下 +23.49bp)
两池 Jaccard 重合度只有 8.6%
ATR 匹配对照 -19.24bp → 顶档超对照 +42.73bp   ← 不是「挑了高波动」
```

### 已证伪的(不要再试)
- **九种出场规则**:v10 上没有一种因果超额覆盖 10bp;且排名与老池完全反转
- **分类改回归**:v10 上只值 -0.53bp(老池上的 +21.46bp 未复现)
- **+245bp 顶档提升**:孤证,复现不了,按 bug 处理
- **holdout #10**:功效算过,n=1739 只能分辨 ≥39.4bp,要证的是 4~18bp
- **Kronos 基础模型**:配对贡献 +2.42bp(t=1.13),置换 p=0.0333 未过 0.01;
  「仅 Kronos」0/15 折为正 —— 它的价格预测对这批候选的盈亏没有可用信息。
  产物留在 `data/kronos_feats_v10.csv`,换池可复用

### 下一步(优先级)
1. **剖开顶十分位** —— 唯一稳的信号却从未被解释;顶档 vs 其余 90% 的特征差异,
   带匹配对照。若差异清晰,那可能才是真正的信号定义(比 owner 手画金标更值得当目标)
2. **标 `datasets/label_live_tip_1000/`** —— 1000 张盘口图、标签全空,owner 20 分钟,
   回答「你的形态只看盘口时你自己认不认得出」
3. **滑点实测** —— ledger 缺 `avg_fill_px`,所有成本数字不含滑点,而边和摩擦已同量级

### 必须回滚的一处（已完成 2026-07-30）
`scripts/live_signal_tg.py` 的 `USE_STOP` 已改回 **True**（TP5/SL2）。
v10 上「只止盈无止损」是 -4.64bp；纸面路径与生产障碍一致。

### 仍禁止
promote / 改 ACTIVE / 清 forward_log / 动 holdout(已耗 9 次)/ 真下单 / 改新鲜度三门。

---

## ⚡ 当前真相（2026-07-30 — ETH 3m short pilot v2 诊断训练完成；静态门失败）

### 一句话
**v1 因 99.74% 连续盘口恒开火而隔离；v2 改成“当前 tip 是/不是”的图像分类。
137 张数据完成一次 3060 诊断训练，但固定 0.50 门下 val 为 TP=0 / FP=0 / TN=34 / FN=8，
模型退化为全判 `no_start`，静态第一门即失败。后续语义审计又确认标签问题/来源不统一，且
锚点构造规则可 99.27% 推断类别；它不是 formal gold，禁止进入 smoke、promote 或 ACTIVE。**

### 2026-07-30 诊断训练结论
- Owner 明确授权“直接去3060跑吧”，并确认可与 PID 93656 的 v10 wide dump 并跑；原任务全程未停。
- 输入为 137 张 train/val 的 960×960 白底等比例补边副本；右端 T 完整保留；weak 150、smoke 7,089
  和 holdout 均未进训练。YOLO11n-cls、batch 4、seed 42、所有时序/颜色/裁剪增强关闭。
- RTX 3060 训练 21 epoch 早停，best=epoch 1，exit=0；远端/本地 best SHA256 均为
  `3ce89b668096e79eb00ae0ee8b4913024f91f46356626d22cbe11d3a98c30056`。
- 固定阈值 0.50：train 95 张 TP22/FP0/TN73/FN0；val 42 张 TP0/FP0/TN34/FN8。
  val top1 80.95% 恰等于多数类 34/42，balanced accuracy 50%，**FAIL** 预注册 TP≥6/8。
- 简单因果规则“当前 T 首次跌破六条 MA”在同一 val 为 TP5/FP0/TN34/FN3，明显胜过图像模型；
  因此本轮不是“多训几轮”问题，而是小样本/来源混杂/时间外泛化失败。
- 按 fail-fast 纪律，连续 smoke 与 30 事件 owner 复核未运行；阈值不下调、不扫描，不读取 holdout。

### 2026-07-30 失败根因复盘（数据结构 PASS ≠ 可学习性 PASS）
- 正例与负例不是同一个人工问题：30 个正例是 owner-yes 形态内另提橙色 T 后整批确认“来得及”；
  107 个负例是 Project 53 对原红框形态判“不是”。`label_provenance` 对 target 纯度为 100%。
- 正例 30/30 被重锚到六 MA 首次下破，负例 107/107 保留原 v10 tip；仅用构造元数据
  `anchor_time == first_below_time` 即 TP30/FP1/TN106/FN0（99.27%）。这是锚点/来源混杂，
  **不是未来泄漏，也不是可部署基线**。
- 原报告的 29 个正事件只按 box/未来标签区间归并。按模型完整暴露区间 `[T-199,T+60]`，
  137 张仅 32 个时间依赖块；30 正图仅 23 块，val 8 正图仅 5 块。跨 split 378-bar embargo
  仍然通过、无泄漏，但有效验证量远小于图片数。
- Ultralytics 用 `(top1+top5)/2` 选 best；二分类 top5 恒为 1，top1 又等于多数类基线，故 epoch 1
  被保存为“best”并不代表业务 TP/FP 最优。下一版必须逐 epoch 按固定门保存混淆矩阵。
- 详细 HTML：`analysis/output/eth3m_v2_problem_analysis_20260730/report.html`；机器审计：
  `analysis/output/eth3m_v2_problem_analysis_20260730/dataset_quality_audit.json`。

### 为什么不再画固定右缘检测框
- Project 53 的 107 张 owner-no 中，69 张历史窗口含已知 owner-yes 形态；它们是“当前 tip 不是”，
  不是“整张图没有对象”。继续写 YOLO 整图空标签会产生矛盾监督并强化右缘位置捷径。
- Owner 已明确只需要回答“是不是”；v2a 因此用 200 根 causal 图做 image-level
  `short_start / no_start`，不再把框宽当训练目标。

### 标签语义纠错（必须保留）
- v2 初稿错误地把生产扫描 `tip/tip-1/tip-2` 的**检测定位容差**解释成信号寿命，自动生成
  T/T+1/T+2 正、T+3 负，共 265 张。反方复核发现后，该版已隔离，**禁止训练**。
- 当前 v2a 只有 owner 实际确认过的时点进 train/val：固定 30 图的当前 T 正例，以及
  Label Studio Project 53 的 107 个 owner-no 当前 tip 负例。
- T-1/T+1/T+2/T+3/原 v10 共 150 条全部 target 为空，只进 `weak_or_review_manifest.csv`；
  只有逐时点复核或 owner 明确批准寿命规则后才可单变量加入。

### 数据与隔离
| 项目 | 结果 |
|---|---:|
| train/val 图片 | 137（30 是 / 107 不是） |
| 独立正事件 | 29（train 21 / val 8） |
| 完整暴露正依赖块 | 23（train 18 / val 5） |
| 全部完整暴露依赖块 | 32（train 25 / val 7） |
| train / val 图片 | 95 / 42 |
| 全局事件组 | 71 |
| 实际锚点 embargo | 378 bars（硬门 200+60=260） |
| 无标签待复核 | 150 |
| 连续 dev smoke | 7,089 bars（未标注，绝不自动转负例） |

- 30 张 timing 校准是 owner 在对话中的整批确认“看过了都来的急”，不冒充逐行 Label Studio
  金标；回执绑定固定 manifest、移动 HTML、30 张 review 图和 30 张 causal 图 SHA256。
- 30 张正图按重叠 3h 标签区间有 29 个事件，但按完整输入+标签区间只有 23 个依赖块；
  当前旧口径名称“独立正事件”不得再用于宣称统计独立性。
- 独立验证：标签白名单、图片/哈希、receipt、事件切分、因果窗、holdout 边界全通过；
  18 个相关测试通过。

### 产物
- 数据：`datasets/eth_3m_short_pilot_v2/`
- 构建器：`scripts/build_eth3m_short_pilot_dataset_v2.py`
- 独立验证：`scripts/validate_eth3m_short_pilot_dataset_v2.py`
- 验证回执：`analysis/output/eth3m_short_pilot_v2_dataset/validation.json`
- owner 回执：`datasets/eth_3m_short_pilot_v2/owner_confirmation_receipt.json`
- 审计报告：`analysis/p_eth_3m_short_pilot_v2_dataset.md`
- 可携带 HTML：`analysis/output/eth3m_short_pilot_v2_dataset/report.html`
- 训练预注册：`analysis/eth3m_short_pilot_v2_cls_prereg.json`
- 全图训练副本：`datasets/eth_3m_short_pilot_v2_cls_letterbox960/`
- 本地权重：`runs/classify/eth3m_short_pilot_v2_cls_diag_20260730/weights/best.pt`
- 远端日志：`C:/fable/logs/eth3m_short_pilot_v2_cls_diag_20260730.log`
- 本地原始远端证据：`analysis/output/eth3m_short_pilot_v2_cls_diag_20260730/remote_train.log`、
  `remote_exit_code.txt`、`remote_best.pt`（exit=0；日志 SHA256 `b8e6487b…`；远端/本地权重一致）
- 诊断训练报告：`analysis/p_eth3m_short_pilot_v2_cls_diag_20260730.md`
- 问题分析与重建方案：`analysis/output/eth3m_v2_problem_analysis_20260730/report.html`

### 状态与下一步
- `diagnostic_pilot_only=true`；`pilot_training_eligible=false`；`formal_gold_dataset=false`；
  `promotion_eligible=false`。
- 一次诊断训练已完成并失败；未调阈值、未跑 smoke、未 promote、未改 ACTIVE。
- 推荐下一步是先做统一 current-T 二选一 D0：240 个唯一 T（旧 yes 事件 earlier/original 成对
  120、旧 no 事件 original/near-miss 成对 80、非 v10 连续 tip 40）+10% 盲重复；一致率、
  source-only 基线和完整依赖块通过 Gate A 后，才扩 600 和 2,000。不能把下调阈值或挑
  checkpoint 当修复。
- 维护计划要求的结构拆分已完成；18 个数据测试、冻结 manifest/receipt 及 287 张图逐一哈希等价。

### Holdout 事故登记
- 并行审计助手误读了 `data/kline_fetched/ETH_USDT_SWAP_1m.part.csv` 的表头及 3 行
  2026-07-15 数据，发现后立即停止；未用于统计、选样、阈值或模型结果。
- 按“看一眼就是消耗”纪律，保守登记为全局 **holdout 第 12 次误耗**。v2a 构建本身只读严格
  pre-holdout 前缀；独立验证只读冻结产物。

### 仍禁止
- 265 张语义错误版；v1 权重进入 v2 / judgment / ACTIVE；自动 promote；清 forward_log；
  未经 owner 再读 holdout；真下单；改新鲜度三门。

## ⚡ 当前真相（2026-07-29 03:30 — 检测线的前提未被证实；v10 训练中；不 promote）

### 一句话
**v9 不可用(精度 0.4%),v10 正在重训修三处数据污染;但当晚一个更根本的测量显示:
在 owner 自己的标注密度下,因果特征一个金标都定位不到。**

### 当晚的决定性测量（`scripts/diag_tip_precision_at_owner_density.py`）
负样本 = 全部 440 万根 bar(不是挑出来的,所以结论不随采样口径变化):

```
基础率 0.0384%(1685 金标 / 4,392,738 bar)
密度 0.2~1.0 条/币·月(= owner 自己的标注密度)  → 命中 0,精度 0.00%
密度 10 条/币·月                               → 精度 0.26%,6.8x 基础率
密度 48.8(v9 的开火率)                        → 精度 0.23%,召回 11.8%
```

**因果窗口里有信号但极弱。**（2026-08-30 撤回:此处原引用的 499/2「画在盘口」统计已删除,
该指标算的是框落在 200 根固定渲染窗内的位置,不是标注者是否使用了未来信息,结论不成立。）

**边界**:这测的是表格特征(28 生产 + 19 alpha),YOLO 看像素,不完全等价;
且不证明金标是前视的,只证明**这些特征定位不到**。

### v9 为什么不可用
- **精度 0.4%**:owner 逐张审 277 个非金标开火,否掉 276 个(95%CI [0.1%, 2.0%])
- **开火密度 48.8 条/币·月**,是 owner 标注密度(0.18~0.36)的 **137~274 倍**
- **「召回 84%」是在 conf 0.05 下测的,生产跑 0.30**,该门槛下真实召回 **19.5%**
- 调门槛无解:压到 owner 密度需 conf≥0.50,那时召回 0%
→ `docs/learnings/recall-without-fire-rate-rewards-a-detector-that-fires-everywhere.md`

### v10 修了什么（训练中，3060）
| 污染 | 规模 | 修法 |
|---|---|---|
| 窗口未精确还原 | **19.3%** 的金标 | 要求 `resolve_win_start` 的 MAD<0.5(该返回值此前所有调用点都丢弃) |
| 方向按「先触发」判 | **9.8%** 的「空头」48 根内涨超 2% | 改为取窗口内**较大**位移 |
| 负样本教不会边界 | 采样器 `if passes(): continue` 排除了像的 | 加入 v9 自己的误开火 612 个 |

数据集:正 1322 / 负 1983(困难负 931),train/val 泄漏检查 **0 问题**。
参数与 v9 逐项对齐(单变量纪律)。训练中期 mAP 在 0.87 与 0.00 间震荡,
属改 GT 几何后的正常现象 → `docs/learnings/yolo-e21-train-instability.md`。

### 判断层
- `models/ACTIVE`(v11)在 25,602 行池上**顶档 -32.91bp**,底档最好
- 病因不是同源:**目标选错了维度**。ATR 五分档胜率 36.2~37.7% 持平,每笔净差 5 倍
  → 边在**幅度**不在**胜率**,而生产是二分类器,AUC 0.4962 是必然
- 换回归目标:配对 +21.46bp,t=3.21,13/15 折(p=0.0074);**但置换 p=0.32,仍打不过随机**
→ `docs/learnings/the-edge-is-in-magnitude-so-a-classifier-learns-nothing.md`

### 经济性（带匹配对照，此前所有数字都没有）
```
100×6m 池       +26.91bp
匹配随机做空    +17.15bp   ← 2025-11~2026-05 是山寨下行窗
检测器的贡献     +8.97bp   (t=5.71)
往返成本          10bp     ← 边与摩擦相等
```
→ `docs/learnings/pool-internal-metrics-cannot-see-beta.md`

### Holdout 记账补充（2026-07-29）
- Owner 在对话中明确要求用 v10 跑 2026-07-18～07-27 每日绝对涨跌幅 Top20，并继续要求整理规律；该窗口全部在 `2026-05-04` 之后，补记为全局 **holdout 第 10 次消耗**（此前 N=9）。
- 十日池按完整日结果事后选币，没有预注册匹配对照，只能作检测行为与标签语义审查，**不是正式验收，不能用于调参/promote**。本次二次汇总只读同一批已生成 CSV，不另记第 11 次。
- 结论与 HTML：`analysis/output/v10_daily_movers_10d_patterns/report.html`。
- Owner 随后又明确要求用 v10 在 ETH 3m 最近三个月预打标并生成 HTML，登记为全局 **holdout 第 11 次消耗**。本次只等距审查 2,000/43,621 个 causal-tip 锚点，v10 命中 47 个；未来 3 小时只出现在人工图，且 v10(15m)→3m 属 OOD。不得据此调阈值、验收、promote 或改 live。报告：`analysis/p_eth_3m_v10_prelabels_3m.md`；HTML：`analysis/output/eth_3m_v10_prelabels_3m/index.html`。
- ETH 微周期标注口径已由 Owner 明确冻结：人工 review 固定看未来 **3 小时**（3m=60 bars）；检测层的“形态是否成立/框坐标”与判断层的“后续结果/幅度”**分开保存**。下一步先做 ETH 3m 的 240 张开发期双视图校准包并过 Gate A；判断层 TP/SL/超时/成本暂未另批。
- ETH 3m 校准包预览已生成：**240 任务 = 216 独立事件 + 24 盲重复**；独立源配额 v10/numeric/downside/random = 65/54/43/54；33 个 v10 事件显示预框。HTML `analysis/output/eth_3m_calibration240_preview/index.html`，报告 `analysis/p_eth_3m_calibration240_preview.md`。全包及未来 3h 均 `< 2026-05-04`，**未耗 holdout、未导入 Label Studio**；等待 Owner 手机确认后再导入。
- 上述混合 240 HTML 被 Owner 明确否决为“不是要看的预标图”，仅留审计，**不得导入**。按修正口径已重做 `datasets/eth_3m_v10_prebox200/`：200/200 均为 v10 conf≥0.30 真框，全部显示红框，全部 exact-tip；白底单图、右侧 future+3h，无入场/TP/SL/成交量/背景填充。HTML `v10_prebox200_mobile.html`，报告 `analysis/p_eth_3m_v10_prebox200.md`；LS 任务已准备但仍未导入，待 Owner 确认。

### 下一步（需 Owner 决策）
1. **v10 验收**:已挂自动任务,训练结束即出「conf 0.30 下的召回 + 开火密度」。
   验收门是**密度接近 0.18~0.36**,不是 mAP,不是召回单项。
2. **决定性实验**:`datasets/label_live_tip_1000/` 1000 张盘口图(右缘=tip、无后文),
   1000 个标签**全空、从未开标**。标掉它才能回答「你的形态只看盘口时还认不认得出」。
3. 若 v10 密度仍压不下来 → 检测线的前提被证伪,应停止调检测器。

### 仍禁止
- promote / 改 ACTIVE / 清 forward_log / 动 holdout(已耗 11 次)/ 真下单 / 改新鲜度三门。

---

## ⚡ 当前真相（2026-07-25 00:20 — S3 shortish 过滤包 432/1000；待 Owner 目视；不 promote）

### 刚发生
- Owner 要求 1000 框包只留“看起来像空头启动”。
- 在 tip_v1b 原包上加启发式过滤：`NOT bull_stack AND ret12<=0 AND close<=ema60`。
- **保留 432/1000**（183 币）→ `analysis/output/owner_side_short_tip_v1b_detect1000_shortish/`。
- 报告：`analysis/p_short_tip_v1b_detect1000_shortish.md`。
- **未** promote / **未**动 holdout / **未**改 ACTIVE。

### 下一步（需 Owner）
1. 审 `detect1000_shortish/index.html` + 填 `review_sheet.csv`。  
2. 过严/过松再调规则阈值。  

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门。

---

## ⚡ 当前真相（2026-07-24 23:53 — S3 tip_v1b 1000 框包完成；待 Owner 目视；不 promote）

### 刚发生
- Owner 批 **S3-1**：用 `owner_side_short_tip_v1b` 在真实 K 线出 ~1000 框，排除 short 金标训练集。
- **完成**：`analysis/output/owner_side_short_tip_v1b_detect1000/` — labeled **1000** / tried 1176 / symbols **224** / train collisions **0** / right p50≈**0.997**。
- 脚本：`scripts/dump_short_tip_detect_sample.py`；报告 `analysis/p_short_tip_v1b_detect1000.md`。
- 前序 S2 仍有效：100×6m 回归 = 间歇弱边（净 +0.471%，ρ=0.016）→ **停扩币**。
- **未** promote / **未**动 holdout / **未**改 ACTIVE / **未**接执行器。

### 下一步（需 Owner）
1. 目视 `index.html` + 填 `review_sheet.csv`（owner_keep/note）。  
2. 根据 keep 率决定：仅辅证 / 建新金标 / 收摊。  
3. 障碍/holdout#8/promote **另批**。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---

## ⚡ 当前真相（2026-07-24 23:19 — short 100×6m 回归+walkforward 收口；S2=间歇弱边；不 promote）

### 刚发生
- **100×6m 扫池完成**：`data/judgment_yolo_owner_side_short_100_6m.csv` n=**25602** / 100 币 / pos≈0.284；complete 100/100（17:38）。
- **回归** `p2b_yolo_short_100_6m_reg`（无 holdout）：top-decile 净 **+0.471%**（n=510）/ Spearman **0.016** / val-q90=**0.00347** / 置换 p=**0.037**。报告 `analysis/p_short_judgment_100_6m_reg.md`。
- **walkforward** 5-fold：net_mean **+0.305%** / rho_mean **−0.010** / all_folds_net_positive=**false**。报告 `analysis/p_short_judgment_100_6m_reg_walkforward.md`。
- **S2 裁决**：扩样后单切仍正，但排序塌陷、稳健级未过 → **停止继续扩币叙事**；默认转 **S3 检测金标/信号定义**（Owner 1000 目视）。**未** promote / **未**动 holdout / **未**改 TP/SL。

### 下一步（需 Owner）
1. 是否开 tip_v1b **1000 目视框**（排除训练集）——S3。  
2. 是否换命题 / 收摊 short 判断层（默认：先 S3，不烧 holdout#8）。  
3. 障碍/holdout/promote **另批**；勿再开 binary top-K。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---

## ⚡ 当前真相（2026-07-24 16:40 — short 扩 30×6m；回归主线正；binary/top-K 支线关；不 promote）

### 刚发生
- **Owner 纠正（主线）**：short = **YOLO tip_v1b → 回归 LGBM（预测空头 realized_ret）→ 分位筛单**，对齐 v11。镜像=默认输入，不当「优化旋钮」。
- **30×6m 扫完成**：`data/judgment_yolo_owner_side_short_30_6m.csv` n=**7519**；墙钟 **≈16 min**（launchd）；主路径镜像。
- **回归** `p2b_yolo_short_30_6m_reg`：top-decile 净 **+0.371%**（n=150）/ Spearman **0.149** / val-q90=**0.00362**。报告 `analysis/p_short_judgment_reg_align_v11.md`。
- **binary 支线收口**（同池；本会话交付）：镜像基线 AUC **0.518** / 净 **−0.181%** / p=**0.125**；单变量 top-K10 更差（净 −0.237%）。报告 `analysis/p_short_judgment_refactor_v2.md`。**停止 binary 特征优化**。
- CLI：`--objective` + `--features-file`。**未** promote / **未**动 holdout / **未**改 TP/SL。

### 下一步（需 Owner）
- 同构**回归**下扩样本 / walkforward。障碍/holdout/promote 另批。勿再开 binary top-K。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---

## ⚡ 当前真相（2026-07-24 16:20 — short 判断层重构 + feat_mirror 单变量；不 promote）（历史；叙事已废）

### 刚发生（历史）
- **结构性**：short 主路径统一方向特征镜像（`align_short_feature_rows`）；`train --side` 拒混边。
- 曾把 feat_mirror 当单变量优化；**Owner 已纠正** → 见上节回归主链。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---

## ⚡ 当前真相（2026-07-24 16:05 — SHORT ONLY 首表：5 币 × 6m tip_v1b；不 promote）（历史）

### 刚发生
- Owner：「后台已停；**不管 HV**；最快回测」→ **5 流动性币** BTC/ETH/SOL/DOGE/XRP × 信号窗 `[2025-11-04, 2026-05-04)`。
- 扫池 `data/judgment_yolo_owner_side_short_5_6m.csv`（n=**1240**，pos≈0.296；墙钟≈**5.7 min**）→ train tag `p2b_yolo_owner_side_short_5_6m`（**无** holdout）。
- **SHORT ONLY 首表**：val AUC **0.599**；top-decile 净 **+0.062%**（n=24）；置换 **p=0.009**。报告 `analysis/p_short_only_backtest_tip_v1b_5_6m.md`。
- **诚实**：n 小；发现级刚过线；**未** promote / **未**动 holdout。tip_v1b tip-smoke 19/27 仍为检测辅证。

### 下一步（需 Owner）
- 同窗扩币 / 或停在本表转检测金标门——见报告「下一步」。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---

## ⚡ 当前真相（2026-07-24 14:45 — tip_v1b 训完；tip-smoke 19/27；不 promote）（历史）

### 刚发生
- **`owner_side_short_tip_v1b` 训练结束**（≈57 ep early-stop；进程已死）。权重：`runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt`。
- **tip-smoke**：**tip 19/27**、live **4/27**。报告 `analysis/p_owner_side_short_tip_v1b.md`。
- **未** promote / **未**动 holdout。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门。

---

## ⚡ 当前真相（2026-07-24 12:52 — tip 集完成；训练已按 Owner 批准重启）（历史）

### 刚发生
- **Tip 短集已完成**：`datasets/dense_owner_side_short_tip/`（train 1037 / val 324；holdout **0**；`box_right_frac` p50≈0.997；时间切分干净）。
- **Owner 早已批准开训**；`owner_side_short_tip_v1b` 经 launchd 开训（后已训完，见上节）。
- **未** promote / **未**动 holdout / 坏集 `dense_owner_side_short` 不覆盖。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。

---


## ⚡ 当前真相（2026-07-24 12:40 — Owner 叫停 short v1 train）（历史）

### 刚发生
- **`owner_side_short_v1` 训练已按 Owner 指令杀掉**：原 pid **26613** + wrapper **26607**；停于 epoch≈7。**未** promote。
- **叫停原因**：① 框非 tip（`box_right_frac` 中位 0.52；旧 pretip 窗）；② 非时间切分（val 99.4% 落在 train 窗内）。
- Owner 随后选 **选项 1** → 见上节（已重建 tip 短集）。

### 仍禁止
- promote / ACTIVE / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。

---

## ⚡ 当前真相（2026-07-24 午后 — Owner 选定 short-only 全链路）（历史；v1 已叫停）

### Owner 已批准 / 当时主线
- **只做空完整链路**：① short YOLO 检测 → ② short-only 判断层 → ③ 回测/优化。作战计划：`analysis/p_short_only_pipeline.md`。
- **检测**：本机 MPS 训 `owner_side_short_v1`（**已被 Owner 叫停**）；数据 `datasets/dense_owner_side_short/`（train 1004/1036，val 313/325）；日志 `analysis/output/owner_side_short_v1_train.log`；权重若有落盘 **未**晋升。
- **判断层骨架已铺**（不依赖 best.pt）：`build_dataset --side short` → `data/judgment_dataset_v2_{mode}_short.csv`；YOLO 池路径 `yolo_candidate_source.py --side short` → `data/judgment_yolo_owner_side_short.csv`。
- **原下一步闸门**（已废）：等 short train 结束 → tip-smoke… → **改为先重建 tip 对齐短金标**。
- **仍禁止**：promote / ACTIVE 切换 / 清 forward_log / holdout#8 / 真下单 / 改新鲜度三门 / 杀 §7-2 dump。Long YOLO **未**开。
- §7-2 3060 dump **并行不杀**。

---

## ⚡ 当前真相（2026-07-24 午间 — Owner 批双链路；先本机训 short YOLO）（历史）

### Owner 已批准
- **多空分模、双链路**；**先跑空**：本机训 `owner_side_short_v1`（不用 3060）。
- 数据：`datasets/dense_owner_side_short/`（short 1361 框 → train 1004 图/1036 框，val 313/325）。
- 开训：`python -m src.detection.train --data .../dense_owner_side_short/data.yaml --model models/yolo11n.pt --name owner_side_short_v1 --epochs 100 --patience 20 --device mps`（SAFE_AUG）。
- 日志：`analysis/output/owner_side_short_v1_train.log` → `runs/detect/owner_side_short_v1/`。
- **不** promote / 不 holdout#8 / 不改 ACTIVE。Long 模稍后。
- §7-2 3060 dump **并行不杀**。

---

## ⚡ 当前真相（2026-07-24 上午 — Owner 批 §7-2；3060 大样本 dump 进行中）（历史）

### Owner 已批准
- **§7-(2)**：用现有 `owner_v16_tipuni_cold.pt` 在 **3060** 扩宇宙重扫 v16 候选，复验方向墙是否小样本假象。
  **不是**双检测器训练；**不是** holdout#8；**不** promote。
- 3060 任务：WMI pid≈83452 · `logs/v16_dump_large.log` · 输出 `data/v16_candidates_large.csv`
  （`--n-symbols 999 --end 2026-05-03`）。本地评估脚本已备：`scripts/it16_large_sample_direction_wall.py`。
- dump 完成后：scp 回 Mac → 跑 IT-16 → 写 `analysis/p_it16_large_sample_direction_wall.md`。

### 仍禁止
- 像素双检测器训（IT-14 红灯）· holdout#8 · promote · 改 ACTIVE · 真下单

### 待 dump/IT-16 出结果后再决策
- 若方向墙仍在 → 回到 §7-(1) 告警-only / §7-(3) 换命题
- 若墙被打破 → 预注册卡后再谈是否申请 #8（须另批）

---

## ⚡ 当前真相（2026-07-24 通宵收口 — IT-14 红灯；tip 映射已审；未达实盘门）（历史）

### 待 Owner 醒来批准（已部分回应：选了 §7-2）

1. **§7 产品岔路**：`(1) 接受检测/告警-only` / `(2) 3060 用现有 v16 大样本重扫方向墙`
   / `(3) 换命题` / `(4) 批准「全市场密度谷 tip 扫描」单变量基线`？  
   **默认建议：1 为主；若继续判断层则先做 4；2 作最后一钉。** → **Owner 2026-07-24 选 2**
2. **不要**批「像素双检测器训」——IT-14 红灯（除非显式例外）。
3. **不要** holdout#8 / promote / 改 ACTIVE / 真下单——清单未过线。

### 通宵已完成

- **IT-14 红灯**：冻结 COCO tip 窗 embed → VIS AUC≤0.507 / top_dir_PF≤1.096；
  报告 `analysis/p_it14_visual_direction_precheck.md`。**未**上 3060 双模。
- **tip 映射审计**：`box_right_frac≈0.5` **冤枉意图**（裁图坐标）；机械上 cut 处
  dense 仅 1.55%、chg8>0 97.6%、偏谷底 ~10 bar。报告
  `analysis/p_tip_mapping_owner_intent.md`。
- **IT-15 tip remap**：Owner 子集上前移到谷底 raw PF 好看但**选择偏差不可部署**；
  报告 `analysis/p_it15_tip_remap.md`。
- **可上实盘清单**：`analysis/p_live_readiness_checklist.md` —— G0–G4/G6 仍红/黄；
  **未**到「只差 Owner 点头」。
- **learnings**：`box-right-frac-is-not-a-tip-intent-verdict` /
  `owner-subset-tip-remap-is-not-deployable-edge` /
  `frozen-visual-embed-red-means-no-dual-detector-train`。
- 活文档已更：`analysis/p_judgment_layer_lab.md` §2/§3/§7。

### 不变纪律

- **训练默认 3060**（`FABLE_3060_HOST`≈`zzc@192.168.1.3`；本机不开训）。
- **判断层 IT-00~15**：决策时刻**无可交易方向边**；判断层下一角色若继续 =
  过滤/是否交易/仓位，**不**再赌选边。
- **holdout N=7**；**未** promote / 开空 / 改 ACTIVE；detector=none；三门 30min；
  `forward_log` 0 业务行。
- **E1–E3 / 双检测器** 归档勿复活。

### 明早一键（仅当 Owner 选 §7-2；非双模）

```bash
# 连通（3060 通宵探测过：空闲、C:/fable 在）
FABLE_3060_HOST=zzc@192.168.1.3 bash scripts/sync_v16_to_windows.sh --check
# 大样本重扫规格需 Owner 点头后再写具体 scan 命令；权重在 Mac/3060:
#   models/owner_v16_tipuni_cold.pt （未晋升，仅研究用）
```

---

## ⚡ 当前真相（2026-07-24 凌晨 — 判断层实验室定论；勿烧 #8）（历史）

- **训练默认 3060**：YOLO 训练/微调/GPU 重训一律走局域网 Windows RTX 3060
  （`FABLE_3060_HOST` 默认 `zzc@192.168.1.3`，IP 会漂；WMI 开训；Mac 只建数据+sync+验收，
  **本机不开训**）。通道见 `scripts/sync_v16_to_windows.sh` + `v16_train_start.sh` /
  `train_on_3060.sh`；笔记 `docs/learnings/yolo-train-ships-over-ssh-to-3060-not-usb.md`。
- **判断层实验室 IT-00~IT-13 诚实定论**（未碰 holdout#8）：检测✓、动作真✓（oracle 2.68）、
  多空互补✓，但**决策时刻无可交易方向边**——选点 / 方向 / regime（5 角）/ 入场续势+fade
  全穷尽，落 ~1.0 或最近期塌。活文档 `analysis/p_judgment_layer_lab.md` §2/§7；
  learning `docs/learnings/dense-cluster-has-no-causally-tradeable-direction-edge.md`。
- **E1–E3 亦不解锁边**（归档，勿再申请 #8）：见下方历史节 / `p_entry_align_and_regime` /
  `p_e3_sparse_and_two_stage` / `p_chain_failure_attribution`。
- **holdout 记账 N=7**（#7 = A 空边趋势出证伪）。**未** promote / 开空 / 改 ACTIVE。
- **实盘**:detector=none 诚实空转（纪律 12）；三门 30min / ACTIVE=v11 frozen 文本指针 /
  阈值 / TP·SL **未改**；`forward_log` 仅表头（0 笔业务行）。
- **Owner 声明（须尊重）**：他框的是 **tip**，不是确认态。现有指标
  （`box_right_frac` 中位≈0.50）与之矛盾时，应**审计映射/阈值是否冤枉他**，勿否定意图。
- **IT-14 当时进行中** → 后续通宵节已收口红灯。
- **出路(需 owner 决策)**：见更新后的顶部通宵节 / `p_judgment_layer_lab.md` §7。

## ⚡ 当前真相（2026-07-23 深夜 — E1对齐抬召回边死；E2 atr门不修4月）（历史）

- **E1/E2 发现级（未碰 holdout#8）**：E1 重写入场对齐 owner short → 召回 25%→94%
  但 Jaccard 更差（0.045→0.018），因果 `no_tp` PF~**1.14**（相对 spread 1.415 倒退）；
  E2 `not_btc_up` 空转，`atr_q34` 抬至 1.607 仍救不过 2026-04（0.845）。**不申请
  holdout**。报告 `analysis/p_entry_align_and_regime.md`。
- **holdout 第 7 次（归档）**：A 因果空边趋势出证伪（PF@maker 0.997/0.969）。
  报告 `p_short_trend_holdout7.md`。**未** promote / 开空 / 改 ACTIVE。
- **实盘**:detector=none 诚实空转（纪律 12）；三门 30min / ACTIVE / 阈值 / TP·SL **未改**；
  **holdout 记账 N=7**。
- **出路(历史，已被判断层定论覆盖)**：E3 稀疏化等 — **勿**再为 E1/E2/E3 烧 #8。

## ⚡ 当前真相（2026-07-23 深夜 — holdout#7:A 因果空边趋势出证伪）（历史）

- **holdout 第 7 次消耗完成（owner 批只测 A）**：**证伪**。`spread_expand` short +
  `no_tp_sl2` / `trail4` 在 ≥05-04 窗 PF@maker **0.997 / 0.969**（train 1.415 / 1.359），
  净约 0 / −0.53；扣 0.2% 更差。报告 `analysis/p_short_trend_holdout7.md`。
  **未** promote / 开空 / 改 ACTIVE。
- **A/B train 背景**（已归档）：空边趋势出曾月度过线；B oracle≫规则但事后。见
  `p_short_trend_ab.md` / `p_trend_exit_base_rate.md`。
- **实盘**:detector=none 诚实空转（纪律 12）；三门 30min / ACTIVE / 阈值 / TP·SL **未改**；
  **holdout 记账 N=7**。
- **v16 tip-replay 终审已完成且证伪**（holdout#6）:1206 笔 · 胜率 29% · PF 0.78 ·
  净 −2.82；v11 判断反预测。报告 `analysis/p_v16_holdout_verdict.md`。
- **多空人工闸门（流式）已就绪**：http://127.0.0.1:8765/gallery.html ；旁路攒 tip。
- **出路(需 owner 决策)**:本挑战者收口；继续旁路攒真实 tip（v17）/ 换命题；
  勿再为同一 A 规则烧 holdout。

## ⚡ 当前真相（2026-07-23 深夜 — 空边趋势 A/B:月度过线；oracle≠规则）（历史）

- **A/B 已跑（未碰 holdout）**：空边 `spread_expand`+趋势出 **月度口径稳健过线**
  （no_tp **1.415** / trail4 **1.359** / trail3 **1.339** / ema55 **1.316**；月 top2
  净利≈51–58%）；但**季度集中 + 2026-04 翻车**。B：owner short oracle PF6–17 ≫
  规则，属事后确认态，**可部署仍认规则**。建议 holdout#7 只测 A 因果（不测 oracle）。
  报告 `analysis/p_short_trend_ab.md`。
- **Owner 已批趋势出场**（按趋势理解 / 改出场 / 目标=净收益·PF）。固定入场
  `spread_expand_chg8`+next_open；**空边** `no_tp_sl2` PF@maker **1.415**、trail3
  **1.339**、ema55 **1.316**（皆≥1.3）；多边全 <1.0。报告
  `analysis/p_trend_exit_base_rate.md`。**未**碰 holdout / ACTIVE / 三门 / 开空。
- **多空人工闸门（流式）已就绪**：打开 http://127.0.0.1:8765/gallery.html ，L/S/K 标；预览后台持续渲染（`stream_owner_side_pack.py`）。填完跑 `scripts/owner_side_feature_verdict.py`。
- **实盘**:detector=none 诚实空转（纪律 12）；三门 30min / ACTIVE / 阈值 / TP·SL **未改**；
  **holdout 记账 N=6**（本轮研究**未**再耗；**maker-on-holdout 未做**，仍属需 owner
    另批的第 7 次选项）。
- **v16 tip-replay 终审已完成且证伪**（holdout#6）:1206 笔 · 胜率 29% · PF 0.78 ·
  净 −2.82；v11 判断反预测（过线 157 笔 PF 0.60；top5% PF 0.48）。未 promote。
  报告 `analysis/p_v16_holdout_verdict.md`。
- **Owner 标框手法裁决（未碰 holdout）**:oracle 选点 train PF **1.183**（相对 emergence
  0.87 有增量），但可部署因果规则 PF **0.869≈emergence 无增量**——手感来自事后确认态，
  **不是**盘口 tip 因果 alpha；勿赌 v17 tip 金标继承 1.18。
  报告 `analysis/p_owner_label_feature_verdict.md`；
  learning `owner-label-oracle-alpha-is-not-causal-tip-alpha.md`。
- **启动入场分多空（未碰 holdout）**:上一轮混边 PF 是**测量呈现 bug**（已降权）；
  分边后多边全 ≤**0.94**，空边最好 spread-short **1.245**，**皆未过 1.3**。
  主报告 `analysis/p_launch_entry_long_short.md`（混池对照已降权链自
  `p_launch_entry_base_rate.md`）；learnings
  `long-short-must-be-split-in-base-rate-tables.md` /
  `mechanical-launch-entry-lifts-pf-but-not-past-1.3.md`。
- **因果择向结论（未碰 holdout）**:**择向未救出可交易边**——排列/突破/spread 最好仍
  spread-short **1.245**；排列跳过 43% tip 也抬不过 1.3。报告
  `analysis/p_direction_select_base_rate.md`；learning
  `causal-direction-select-does-not-rescue-pf-past-1.3.md`。
- **出路(需 owner 决策)**:holdout#7 测 A 因果空边趋势出（no_tp 或 trail4）？/
  影子纸面？继续攒 tip（旁路）/ 多边另开。默认见 `p_short_trend_ab.md`。

## ⚡ 当前真相（2026-07-23 深夜 — 研究收口:oracle≠tip,启动/择向皆未过1.3）（历史）

- **多空人工闸门（流式）已就绪**：打开 http://127.0.0.1:8765/gallery.html ，L/S/K 标；预览后台持续渲染（`stream_owner_side_pack.py`）。填完跑 `scripts/owner_side_feature_verdict.py`。
- **实盘**:detector=none 诚实空转（纪律 12）；三门 30min / ACTIVE / 阈值 / TP·SL **未改**；
  **holdout 记账 N=6**（本轮研究**未**再耗；**maker-on-holdout 未做**，仍属需 owner
    另批的第 7 次选项）。
- **v16 tip-replay 终审已完成且证伪**（holdout#6）:1206 笔 · 胜率 29% · PF 0.78 ·
  净 −2.82；v11 判断反预测（过线 157 笔 PF 0.60；top5% PF 0.48）。未 promote。
  报告 `analysis/p_v16_holdout_verdict.md`。
- **Owner 标框手法裁决（未碰 holdout）**:oracle 选点 train PF **1.183**（相对 emergence
  0.87 有增量），但可部署因果规则 PF **0.869≈emergence 无增量**——手感来自事后确认态，
  **不是**盘口 tip 因果 alpha；勿赌 v17 tip 金标继承 1.18。
  报告 `analysis/p_owner_label_feature_verdict.md`；
  learning `owner-label-oracle-alpha-is-not-causal-tip-alpha.md`。
- **启动入场分多空（未碰 holdout）**:上一轮混边 PF 是**测量呈现 bug**（已降权）；
  分边后多边全 ≤**0.94**，空边最好 spread-short **1.245**，**皆未过 1.3**。
  主报告 `analysis/p_launch_entry_long_short.md`（混池对照已降权链自
  `p_launch_entry_base_rate.md`）；learnings
  `long-short-must-be-split-in-base-rate-tables.md` /
  `mechanical-launch-entry-lifts-pf-but-not-past-1.3.md`。
- **因果择向结论（未碰 holdout）**:**择向未救出可交易边**——排列/突破/spread 最好仍
  spread-short **1.245**；排列跳过 43% tip 也抬不过 1.3。报告
  `analysis/p_direction_select_base_rate.md`；learning
  `causal-direction-select-does-not-rescue-pf-past-1.3.md`。默认建议收口，不值得开影子。
- **出路(需 owner 决策)**:继续攒真实 tip 分布（旁路，勿当救命主线）/
  或收摊换命题。默认建议见上述报告的「下一步」。

## 2026-07-23 夜 — v16 终审:证伪,不上线（历史）

- **holdout 第 6 次消耗完成(owner 预授权)。v16 tip-replay 终审 = 决定性负面,未 promote。**
  窗口 05-04~07-16 · 15 币 · **1206 笔 · 胜率 29% · PF 0.78 · 净 -2.82**(纯检测,亏损)。
- **判断层反预测(最关键发现)**:v16 fire 过 v11 判断层的 157 笔 PF **0.60**(更差);
  **判断分越高越亏**(top5% PF 0.48)。根因:v11 判断在"事后"候选上训练,拿到盘口
  "启动前"候选上是反向选择器。**整套 v16检测+v11判断 被证伪,不可交易。**
  报告 `analysis/p_v16_holdout_verdict.md`;learning `hindsight-trained-judgment-is-anti-predictive-at-the-tip.md`。
- **出路(需 owner 决策)**:两层都用真实盘口数据重训(v17 检测器 + tip 时刻→tip 后真实
  收益 重标定判断层),`collect_real_tips_pulse.py` 已每脉冲攒数据,owner 审 review_sheet 是闸门;
  **或**正视"实时盘口下该形态可能本就无扣成本 alpha"这一诚实可能。实盘维持空转。
- 回测搬 3060 GPU(~4h→~30min);loader 加编码容错(一个坏字节曾崩全run);前端回测页
  切 v16 tip-replay 数据源(旧 PF 6.61 折叠为"已废弃事后方法学")。

## 2026-07-23 傍晚 — 回测终审授权（历史；⑥已完成）

- **v16 判决反转(owner 目视 + 我逐图核实)**:金标"51.5% 误火"作废——那 33 张
  tip-empty-ok 是规则自动预标(非 owner 真值),v16 在 BONK/CAP/EDEN 右缘的框全在
  真实密集启动上,**是正确检出不是误火**。标签比模型错。教训:自动标签不得当裁判。
  画廊 `analysis/output/v16_empty_falsefire/`。
- **改用回测终审(owner 指令:让钱判,不让标签判)**。逐 bar 盘口 tip-replay
  回测器 `scripts/backtest_tip_replay.py`(检测器只见过去 / TP5·SL2 / maker 成本 /
  A′ 贴边门 / MIN_GAP)。小样(DOGE 单周)9 笔 PF 3.36 净 +5.6%,仅信号级不作数。
- **holdout 第 6 次**:当时预授权 + 条件闸门(pre-holdout ≥30 笔且 PF≥1.3…)后**已触发并完成**
  （终审负面，见上方「夜」节 / `p_v16_holdout_verdict.md`）。完整记账:
  ①07-08 2b ②07-15 回归 ③07-16 v8 ④07-17 v10 ⑤07-18 v11
  **⑥07-23 v16 tip-replay 终审（已完成·证伪）**。其后 **⑦07-23 A 因果空边趋势出
  证伪**（见顶部；**当前 N=7**）。注:v16 训练数据全在 05-04 前,故当时 pre-holdout 偏乐观。

## ⚡ 当前真相（2026-07-23 白天 — 实盘检测教义落地）

- **Owner 教义(纪律 12)**:实盘检测 = 最新盘口,任何"只能产出事后/延迟信号"的东西
  一律清除。已执行:①pre-v16 检测器权重**三机全删**(Mac/VPS/Windows,含现役 v12 与
  回滚备份;仅存 COCO yolo11 底座);②live 扫描删除回看窗,只扫 **tip/tip-1/tip-2**;
  ③无检测器期间 VPS 脉冲**诚实空转**(detector=none 日志,K 线照更、账本照结、TG/执行
  器静默)。看板状态条会显示权重不存在——这是事实,不是故障。
- **现役检测器:无**。**v16 已训完并验收:未过线**(应开火 3/9,空背景误火
  **17/33=51.5%** vs 要求 ≈0)——统一渲染管线没治好误火,窗末几何捷径或标注
  语义不可分仍在,见 `analysis/p_v16_tipuni_train.md`。**不 promote**,空转继续。
  **主建议已升级:训练分布必须以真实盘口 tip 窗为主体**(owner 审 48 张 +
  扩采 + `label_live_tip_1000` 盘口打标),v17 = 真实盘口分布首训,等数据。
- **v17 数据引擎已上线(2026-07-23)**:`scripts/collect_real_tips_pulse.py` 接入每轮
  VPS 脉冲(旁路,无 YOLO,120s 预算,只写 `data/real_tip_collect/`)——每脉冲采
  规则密集 tip 候选(owner 审:launch/hardneg,限 10/轮)+ 真实空背景负样本(免审,
  8/轮),MIN_GAP 去重。`scripts/build_real_tip_review_pack.py` 把 manifest 变审阅
  画廊 + review_sheet + LS 任务。**detector=none 期间照常采集**,为 v17 攒真实分布。
  Owner 动作:(a) 填 `v13_real_tip_preview/review_sheet.csv`(48 张,已有);
  (b) 数日后审 `real_tip_review/`(扩采批)。

## 当前真相（2026-07-23 凌晨）

- **数据集大清理（Owner 指令,07-23）**:旧式"非盘口分布"数据集全部隔离进
  `datasets/_deprecated_pretip/`（dense_15m_full / dense_2025h2 / dense_2026h1 /
  dense_owner_v11 / dense_owner_v12_htip / dense_swap_v1,共 11G,**任何训练禁用**;
  保留原因=golden_pool 12567 框的窗口消歧存档,见该目录 README）。错窗废品
  dense_owner_v13_pad200 已物理删除。存活:v14_pad200（v16 正样本源）、v15_tipval
  （v16 val 正样本源）、**v16_tipuni（现役）**、label_live_tip_1000（盘口打标包）、
  owner_eval_frozen（旧任务尺子,只作参考不作 tip 裁决）。
- **v16 val 修正（Owner 目检抓出）**:v14 的 val 从未 tip 对齐,v16 曾整拷 →
  1509 张中段 val 正样本已换成 v15 的 803 张 tip 对齐版;3060 已用正确 val 重启
  `owner_v16_tipuni_cold`（yolo11n 冷启动,v12 永不作底座——Owner 裁定）。

- **真实 tip 成败小样已开干（Owner 已点头）**：VPS 采集 →
  `analysis/output/v13_real_tip_preview/index.html`（tip+0 **48** 张预标：hit4 /
  miss-dense6 / noise5 / empty33）。报告 `analysis/p_real_tip_collect_started.md`。
  **下一步=Owner 目视填 `review_sheet.csv`**；审过才谈扩采/开训。**未**开训、**未** promote。
- **v15 败因定论（07-23）：正负样本两条渲染管线（风格捷径）**——训练集正样本
  100% `_pad200` 重渲、负样本 100% 旧式原图，模型学风格不学密集 → val mAP 0.72
  虚高 + 真 tip 空背景误火 58% + 真密集 0/6 全漏。**修复 = v16 一条管线渲染一切**
  （规格见 `analysis/p_v15_dataset_confound.md`，待 Owner 批）。
- **v15 已裁（07-23）：Hypothesis B 否决**——val 也 tip-align 后 tip_hit 仅 **0.017**、
  tip-smoke 仍 **0/27**，未向 v12 的 0.925 恢复。公平重验（full-MA + 真 tip 分母）
  仍否决：应开火 2/9、空背景误火 19/33，见 `analysis/p_v15_revalidate_fair.md`。
  **未 promote**，主线仍 v12。
- **tip 验收协议审计（07-23，Owner 质疑触发）**：tip_hit（val 重渲）与 tip-smoke
  （实盘同管线）测的不是同一件事；v12 的 0.925 属**过宽赦免**（slice-MA + 同分布 val），
  以后 tip 裁决以 **tip-smoke 为准**。见 `analysis/p_tip_eval_fairness.md`。
- **v14 tip 根因已写清（未过线）**：`analysis/p_v14_failure_rootcause.md`。
  主因 **C 语义≠盘口 tip**；**勿再同构 pad200**；主线仍 v12。
- **H-DET 状态**：H-DET-1 🔴（v13+v14+v15）；H-DET-7 🟢；议程
  `docs/RESEARCH_AGENDA_DETECT.md`。
- **v14 终局数字**：3060 ep26 / best=ep16；`models/owner_v14_pad200.pt`；报告
  `analysis/p_v14_pad200_train.md`。v13 错窗审计 `analysis/p_pad200_cut_audit.md`（已修仍挂）。
- **前端可视化真落地**（不抢 MPS）：前向 Tabulator + 状态条 train/fresh/tip + LWC 密集框/调试入口 —
  见 **`analysis/p_frontend_viz_opt.md`**（预览 `uvicorn …:8642`）。
- **夜间旁路（不抢 MPS）已落地**：LWC hardneg 批量 / 叠框画廊 / LS 小包 / Protections 规格 —
  见 **`analysis/p_overnight_20260722.md`** + `analysis/p_wuzao_topics_scan.md` A 档「已做」。
- **本机旁路工具集（发现级收尾）**：`.venv-tools` + `.venv-fo`；supervision 叠框 / FO 小批 /
  LS check / nvitop·mitm·marimo·profiling / ML4T+LEAN 只读对照 —
  见 **`analysis/p_side_tools_landed.md`** + `docs/LOCAL_DEBUG_TOOLS.md`（不杀 v13、不装 VPS）。
- 近期讨论过、现在不做的优化（检测 tip + 判断/执行/风控）统一记在
  **`analysis/backlog_future_optimizations.md`**——瓶颈仍在 tip；判断层多数要等 tip 通了再拧。
  判断层开源专搜（校准/熔断/一致性积木，无现成两层整机）见该文 **B4**。
- **议程与实盘**：不是「没按 `RESEARCH_AGENDA` 走」，而是旧 H9→H10→H1 发现级已结；
  07-20 起优先队列就是 H-TIP + 前向 100。实盘运维与 tip 迭代并行；H1/H3/H16 等确认级排队等 tip。
- **VPS 装机（Kuma/Grafana/exporter）**：仅清单 `docs/ops/VPS_OBSERVABILITY_PENDING.md`，**未装**。

## ⚡ 2026-07-21（A′ 贴边入账过滤上线）

**Owner 批准并已落地**：YOLO live/tip 入账只收扫描窗最后 **N=2** 根
（`bar_in_win ≥ 198`；按 bar 偏移而非像素%）。KORU 类 tip−3 / EDEN 中段框不再进账本；
脉冲日志 `tip_edge_rejected=`。**不过滤≠产生 tip**——模型 tip/tip−1 仍 0 框则
fresh 仍可为 0。见 `analysis/p_box_to_bar_lag.md` A′、`TIP_EDGE_BARS`。
三门 30min / 阈值 / TP·SL / tiered / forward_log **未改**。

## ⚡ 2026-07-21（tiered sizing 真仓上线 · 口径①）

**Owner 批准**：tiered 上 VPS 实盘；口径 **① 基础仓位减半**（不提杠杆、不充值）。

**已上线核验**（VPS live，equity≈**92.46U**，lev=3，max_concurrent=1，KILL 未置）：
| tier | size_mult | 名义 USDT | 保证金≈名义/3 | vs 权益 |
|------|-----------|-----------|---------------|--------|
| q90–q95 | 1.0 | ~138.7 | ~46.2 | 半仓 |
| q95–q99 | 1.5 | ~208.0 | ~69.3 | OK |
| q99+ | 2.0 | ~277.4 | ~92.46 | **=权益，≤可用** |

公式：`unit = (equity×lev) / 2`，`notional = unit × size_mult`（真乘仓位，`tier_headroom=True`）。
sidecar `sizing_tiers` q95≈0.02548 / q99≈0.04857；阈值仍 **0.02022**；三门 **30min**；
TP5/SL2；**未** clear forward_log。forward_log 已有 `tier`/`size_mult` 列（老行缺列=1x）。

**回滚**（止血 → 恢复 1x 满槽，去掉乘数）：
```bash
# 1) 立刻停新开仓
ssh root@206.237.14.112 'touch /opt/fable-trading/data/executor_KILL'
# 2) 回退 executor 头寸公式：把 unit_notional 段改回 notional=base*size_mult
#    或 git checkout <pre-headroom> -- src/execution/executor.py 后 rsync + restart
ssh root@206.237.14.112 'systemctl restart fable-executor'
# 3) 恢复开仓：rm data/executor_KILL
```
完整撤 tier：sidecar 删 `sizing_tiers` + forward 停打标（需另一次 owner 批准）。

**风险重申**：q99+ val 仅约 **41** 笔；2x 止损冲击 ≈ 名义×(2×atr)/权益，满档接近单笔打满保证金。
确认级仍靠前向新鲜 100 笔。

**五项其余进度**：滑点报告 ✅；tip 子集 / v12 池 / 晨报见并行会话。status-strip 新鲜度门已对齐。

## ⚡ 2026-07-20 夜（owner：检测主线 = v12）

**Owner 拍板「主线直接换 v12」**（检测层强制 promote，**未**耗 holdout）：
- `models/owner_best.pt` = H-TIP v12（tip_hit **0.925** / frozen-F1 **0.650**）
- 备份回滚：`models/owner_best_pre_v12.pt`（原 v11 chain F1 0.658）
- **判断层未改**：`ACTIVE` / `frozen_tp5_sl2_swap_yolo_v11_reg_20260718` / 池 v11  
- 报告：`analysis/p2a_v12_mainline_cutover.md` + `analysis/p_v12_htip_eval.md`
- 无 v12 历史组合回测；确认级仍靠前向 100 笔新鲜

**影子**：`FABLE_V12_SHADOW` 可关（主线已是 v12）；留作对照亦可。

## ⚡ 2026-07-20（实时 tip 路径上线）

**盘口 bar 当场入账**（commit 67d8733，已部署 VPS）：信号 bar = 最新收盘 bar 时
不再丢弃——当脉冲即写入账本（status=open，entry_time=下根开盘时刻，entry_price=
信号 bar 收盘价代理，maker_filled 留空作待回填哨兵），TG 立即通知、执行器立即可
开单；下一脉冲由 merge 回填真实下根开盘入场（detected_at 保留首见，延迟统计不失真）。
检出落账时点从信号后 31~37min 压到 **16~23min**。离线建数据集路径不变（仍要求入场 bar）。

**新鲜度三门统一 30min**（执行器 max_signal_age_min / TG 过滤 / 看板 FRESH_DETECT_MIN）：
30 = 15（bar 时长）+ 7（脉冲对齐+344 币扫描）+ 余量。**20 会结构性挡死一切**
（旧管道最快 31min 才能入账），55 会放进非 tip 迟到检出——阈值必须从管道时序推导，
见 `docs/learnings/freshness-gates-must-be-derived-from-pipeline-arithmetic.md`。
端到端保护：`tests/test_tip_realtime_path.py`。

**依赖**：实时 tip 依赖会在盘口开火的检测器——**现主线已是 v12**（原 v11 tip≈0.9%）。

**脉冲性能（2026-07-20 实测）**：update 76s + discover ~500s + phase2 1s ≈ 10min
< 15min 节拍，最坏落账龄 26min < 30 门。已做：14→6 窗、全帧→2000 根尾巴
（特征偏差 3e-07、渲染逐像素一致）、每币批量 predict（无增益——证明瓶颈是 YOLO
前向计算本体 ~0.24s/窗 × 2064 窗全局串行）。剩余可选杠杆（暂缓）：v12 上线后削减
回看窗 6→3-4；或每 worker 独立模型实例并行 predict（VPS ~2 核，预计 ~1.7x，
代价是内存与复杂度）。阶段耗时每轮打印（discover_wall / phase2_wall）。

## 2026-07-19 晚间（H-TIP / 事后检出）

> 注：本节「新鲜度 20min」已被 **07-20 顶部「三门 30min」** 覆盖；以顶部为准。

**定性**：打标/训练不是「全错」，是**分布错位**（框多在图中、右侧有启动后文；
实盘 tip 无后文）。对 tip 开单：检测层欠训；金标形态仍有用。见
`analysis/p_forward_hindsight_20260719.md`。

**前向（当时）**：10 行 **0 笔 lag≤20m**；EDEN `tip_fire=false`。  
**H-TIP 本机**：`dense_owner_v12_htip` → train `owner_v12_htip`。**不自动 promote**
（进度/通过线见 `analysis/week_plan_20260720.md`）。

## ⚡ 2026-07-18 主线快照（池仍 v11；细节历史）

**主线**：YOLO 检测（`owner_v11_chain`，frozen-F1 **0.658** → `models/owner_best.pt`）
→ 回归判断（`frozen_tp5_sl2_swap_yolo_v11_reg_20260718`，阈值 val-q90=**0.02022**，
池 `judgment_yolo_swap_v11.csv` · **26653** 候选 / 344 币）→ TP5/SL2 出场。
`models/ACTIVE` 与 `frozen.default_config()` 均已指向 v11 池。

**accept 回测（holdout 第 5 次消耗，owner 批准全量切流；完整记账：①07-08 2b ②07-15
回归切换 ③07-16 v8池 ④07-17 v10池 ⑤07-18 v11池）** @0.3% 成本：
**703 笔 · 净资金 +245.8% · PF 6.61 · 胜率 77.1% · maxDD 0.76%**（验收 4/4）。
对照 v8：428 笔 / +154.9% / PF 7.50。见 `analysis/p3_v11_pool_cutover.md`。

**执行层（VPS）**：`fable-executor` active · keys `environment=live`（~92U 权益）·
`fable-forward.timer` **每 15 分钟** YOLO live 脉冲 · `ENABLE_JOB_EXECUTOR=0`。
TG 通知只推 `status=open` 且 signal_age 新鲜（**现为 30min 三门**，见顶部 07-20；
本节写于 07-18 时曾用 20min）。无新鲜 open 时执行器安静空转——属正常。

**前向时钟重启（owner 2026-07-19）**：清空主线 `forward_log.csv` 重测 v11 闸门；
旧账本归档 `data/forward_log_pre_v11_retest_20260719.csv`；
`FORWARD_START=2026-07-18 16:15 UTC`（对齐最后收盘 bar，避免「start 在未收盘 bar 内」导致
candidates_seen=0）。裁决计数从 0 重计至 100。

**2026-07-19 链路优化**：tip 扫描在 start 超前数据时不再整表跳过；脉冲 `update_okx
--swap-only`；YOLO live 多线程发现 + predict 锁；时钟/设备日志。

**2026-07-19 实盘加固（overnight）**：
- forward timer 对齐 15m 收盘后 1 分钟（`:01/:16/:31/:46`）
- 脉冲结束立刻 `executor --once`（不等 30s 轮询）
- 括号 OCO 失败重试 2 次；ledger 计入 `order_partial` 防重复开仓
- 新鲜度 20min；轮询 30s；paused 不再每轮刷 ledger
- `scripts/live_health.py` + 30min timer TG 告警

**2026-07-16 快照（已被上方覆盖）**：v8 检测+判断；accept PF 7.50 / 428 笔。

**今天推翻的历史结论**（详见 `analysis/p2a_lr_bug_audit.md` + `p3_v8_pool_cutover.md`）：
- `optimizer='auto'` 的 lr=0.002 炸掉了**所有** chain 续训（epoch 3 精确崩溃，
  best.pt=epoch 1）——v7 及之前的 chain 模型等于没训过；已修（`FINETUNE_OPT` lr=1e-4）。
- "v6 0.595→v7 0.625 证明加标注有效"——撤回。干净的学习曲线（嵌套三臂，同机同val）
  给出真答案：**F1 ≈ 0.067·log2(train图数) − 0.265，未饱和**。
- "coco 血统连输两轮已弃"——补跑后反而证实（v8_coco 0.549 ≈ v6_coco 0.554）；
  但续训血统更强（0.650）。
- 旧判断池（101 币，脏检测器）→ 新池（267 币，17573 候选）：accept 窗口全指标胜，
  **holdout 第 3 次消耗，owner 明确批准**（第1次 07-08，第2次 07-15）。

**冻结尺子已物化**：`datasets/owner_eval_frozen/MANIFEST.json`（47 币/464 图）；
`is_eval` 查清单优先（两个拼写泄漏向量已封死：`_SWAP` 后缀 + `okx_` 前缀）。
**标杆基建**：`data/benchmark_exemplars.json`（176 张）；`scripts/benchmark_check.py`
体检门（训≥0.90/评≥0.60）已入 v9 流水线；**152 张标杆 ≈ 1600 张普通标注（10倍质量杠杆）**；
过采样×3 已证伪（0.636<0.650）。

**进行中**：owner 打标 round7（1000/3000，chunk3-6 已换 v8 预标）→ 标完跑
`bash scripts/train_owner_v9_from_round7.sh`（90% 闸门；曲线预测 v9_coco≈0.584 已登记）。
**训练一律走 3060**（`zzc@192.168.1.5`，7 倍速；WMI 启动防 SSH 杀进程；
`--cache false --workers 4` 防 16GB 内存爆；见 memory/training-on-3060.md）。

**最大未决疑点**：PF 7.5 属"好得反常"——检测层训练无时间切分（~2.5% 标注图落在
accept 窗口内）是结构性弱点；**前向 100 笔规则是唯一最终裁决**。v10 应登记
"检测层训练图截止 2026-05-04" 实验。

---

**写于 2026-07-08。** 读完本文件 + `CLAUDE.md` + `analysis/p2b_v2_report.md`，即可无损接手本项目。


## YOLO 主线（owner 2026-07-15 切流）
**候选源=YOLO（owner_best）+ 判断=冻结 `tp5_sl2_swap_yolo_20260715` + 出场仍 TP5/SL2。**
前向时钟从 2026-07-15 重启；规则时代 `forward_log` 已归档
`data/forward_log_rules_pre_yolo_20260715.csv`。说明见
`analysis/p2a_yolo_mainline_cutover.md`。A/B 报告：`analysis/p2a_yolo_critical_path_ab.md`。
round6 新标后只换检测权重再重扫；回滚规则：`CANDIDATE_SOURCE=rules` + 旧冻结。

## 当前状态一句话

**07-10 最新 owner 裁决（覆盖 07-09 均线决定）**：检测层、判断层及未来运行路径统一为
**SMA20/60/120 + EMA20/60/120**。新 ACTIVE 为
`models/frozen_tp5_sl2_swap_ma206_20260710.txt`，阈值 0.340933，数据 SHA256
`8df081a1...`；新前向账本从 `2026-07-10 10:30 UTC` 起独立累计。
迁移报告见 `analysis/p2b_ma206_mainline_migration.md`。全量 MA206 val AUC 0.5702/p=0.001；
0.3% 组合 PF 0.636；maker 0.06% PF 1.072，1h EMA120 过滤后 PF 1.154，
**尚未达到盈利验收线**。
看板迁移验收时发现并修复全量评分越界；**这是 MA206 配置第 1 次意外消耗 holdout**，
未经 owner 批准，结果隔离作废。当前 API/缓存只允许 `pre_holdout_only`，终审仍只认新前向。

**07-09 历史记录**：合约复制性检验通过，旧 `frozen_tp5_sl2_swap_20260709`
曾作为冻结工件；该工件和当时的 H1 PF 2.825 均属于 8-55 历史证据，现已由
`frozen_tp5_sl2_swap_ma206_20260710` 与独立 MA206 前向账本替代，不得再作为运行入口。

**2b 验收通过（holdout 已消耗）→ 阶段 3 第一轮未通过（PF 1.01@0.3%）→
owner 已委托"按推荐直接执行" → 出场结构扫描完成：TP5/SL2 为 v3 候选标签**
（val 净@0.3% +0.077%/笔 vs 基线 +0.001%，p=0.001，见 `analysis/p2b_v3_barrier_sweep.md`）。
**07-11 最新验收**：P2-11 E2.1b HSV0 自然完成，official mAP50 `0.8505`、固定
conf=0.30 一致率 `51.27%`，均未过门；固定 SAHI 全 val 使匹配 `665→625`、预测框
`1629→2753`、延迟 `11.27×`，拒绝接入。独立因果 long/short/no_trade YOLO 分类器
准确率 `34.78%`，固定 0.20% 成本后净 `-0.15236%/笔`、PF `0.7472`，同样拒绝接入。
q80 只诊断影子继续运行；截至 `19:45 UTC`，358 个 SWAP 同窗漏斗为
`67 候选 → q90 10 可执行 / q80 16 可执行`。这证明扫描和评分都压缩信号，但放宽到
q80 只增加 6 个可执行信号，不能替代前向盈利验证。
**07-10 追加（Grok）**：`codex/day1` 已合并进 `main`（`1c1344f`）并 push；owner 确认
P2-11 打标 findings + P2-12 黑名单写入 BLOCKED。  
**07-10 追加（Grok 接手）**：P2-12 数据审计完成（见 `analysis/p2_data_audit_report.md`）；
每日定时任务已含 `update_okx → forward_track → daily_digest`；正式窗口前向日志已有
**2 笔** closed 信号（冻结 TP5/SL2 SWAP）。`src/notify.py` + `scripts/daily_digest.py`
已同步进本 worktree。
**07-10 追加（多日无人值守）**：SWAP expand **完成**（399 个 15m 文件）；P2.5 Phase0–3 已合 main；
H1 shadow logger 已上线；**YOLO E2.1 正式重训已完成**：official val mAP50=**0.8503**（gate≥0.90 **FAIL**）；consistency match≈0.50；hardlist `fiftyone_hard_e21`；检测层仍非关键。
FO :5151 / Label Studio :8081 本机评审就绪；前向主线 + H1 双账本 digest。
章程：`output/offline_tasks/AUTONOMOUS_CHARTER.md`；状态：`MULTI_DAY_STATUS.md`。
**07-10 追加（P2.5）**：ops 鉴权 + 实验/议程 + **白名单 job runner**（默认 executor 关）+ **只读 data/model hub**。
公网/VPS 上 ops 前须设 `OPS_AUTH_MODE=token` + `OPS_API_TOKEN`；**禁止** VPS `ENABLE_JOB_EXECUTOR=1`。
纪律红线：holdout 与验收窗口均已消耗，v3 的确认性验证只能用前向新数据；
val 已被多次选型使用，其数字只用于排序不用于宣称绩效。
fable 拍板：主线 **SWAP** · **SMA/EMA 20/60/120** · 冻结 **TP5/SL2** · YOLO **非关键** · H1 **挑战者/影子**。

**07-20 追加（Grok，Claude 额度见底）**：主线前向诚实摘要见
`analysis/forward_mainline_status_20260720.md`——`data/forward_log.csv` 仅表头；
早期样本混 stockish；K 线约停在 07-16；**不改主线配置**。过夜规格 v2：
`docs/archive/grok_tasks/overnight_batch_v2.md`（task11–15：前向健康 / crypto-only /
H3 shadow / H16 / H1 续记）。

## 排序后的下一步（期望价值从高到低）

### ~~1. purged CV / embargo 泄漏修正~~（作废，2026-07-08 核实已实现）

原以为 train/val 边界存在标签窗口泄漏——**读代码核实后确认 purge 已在
`src/judgment/train.py` 实现**（`PURGE_WINDOW = 18.25h` = 73 根 outcome 窗口，
dev/holdout 与 train/val 两个边界均清除；与 `labeling.py` 的 entry=i+1、
HORIZON_BARS=72 精确对应）。v2 报告中的全部指标本来就是泄漏修正后的数字。
教训见 `docs/learnings/grep-before-planning-fixes.md`。

### ~~2. holdout 一次性评估~~（已完成，2026-07-08，owner 批准）

结果：AUC 0.602 / p=0.001 / top-decile 净 +0.083% —— **2b 验收通过**，明细在
`analysis/p2b_v2_report.md` 6.5 节。expanded × v2 的 holdout 已消耗，任何后续
迭代不得再评估 holdout（除非 owner 批准并注明"第 N 次消耗"）。

### 原第 2 步存档（执行方式备查）

- **为什么**：这是 2b 的正式验收。v1 已消耗过一次 holdout，v2 每个配置只许评一次。
- **怎么做**：
  `python3 -m src.judgment.train --data data/judgment_dataset_v2_expanded.csv --tag p2b_v2_expanded_final --eval-holdout`。
- **完成的样子**：holdout AUC / p / top-decile 净收益写入报告，明确判定
  "2b 验收通过/未通过"。通过 → 阶段 3；未通过 → 回 val 迭代，holdout 不许再碰。

### 3. 阶段 3：简单事件驱动回测（当前工作，2b 已验收）

- 按 `PROJECT_PLAN.md` 阶段 3 规范：自写 ~200 行事件驱动回测，taker 费 + 滑点 +
  资金费近似；检测（规则扫描）→ 判断（LightGBM 分数）→ 持仓 → 平仓全链路；
- 资金费率历史可用 CCXT 拉（唯一批准引入的新依赖，仅数据用途）；
- 验收标准在 PROJECT_PLAN 里，别改。Freqtrade 只作为回测结果的交叉验证，不做主框架。

### ~~4. 2a 全量训练与正式验收~~（2026-07-09 未达成，非关键路径暂停）

- 离线管道完成：yolo11s 官方评估 mAP50 0.8569 / mAP50-95 0.6643 /
  precision 0.8003 / recall 0.7112；
- 未达到 mAP50 ≥ 0.90，因此不写一致率脚本，不调 conf/IoU/增强凑数；
- 后续主线继续规则扫描 + 判断层 + 前向验证，YOLO 仅保留为已验证可学习的非关键路径组件。

## 停止做的三件事（含理由）

1. **停止给 strict 池单独调参**——2 898 个样本不够 LightGBM 学出超过单特征基线的
   结构（v2 实测模型 0.543 vs 基线 0.556）。扩池已验证成立，主线就是 expanded。
2. **停止在旧缓存数据上跑新实验**——新拉取的 400 天数据在时间覆盖上全面优于旧缓存
   （旧缓存仍参与 loader 合并，但不要再针对旧数据的特性做任何决策）。
3. **停止评估新框架**——2026-07-07 已做过完整评估（见会话记录/README）：
   阶段 3 自写回测，CCXT 仅拉数据，其余一概不引入。

## 未决队列（2026-07-08 深夜快照，两个后台任务当时仍在跑）

1. **YOLO 全量训练已完成**：yolo11s mAP50 0.8569，正式验收未达成，非关键路径暂停。
2. **合约数据**（okx_*_USDT_SWAP_15m_*.csv 落在 data/kline_fetched/）：
   拉完后跑冻结流水线复制性检验——expanded 池 + TP5/SL2 标签在 SWAP 序列上
   build+train（val only），合约成本：maker 0.02%/taker 0.05% + 资金费近似 0.01%/8h。
   owner 已确认实盘目标是合约。
3. **均线定义旧裁决（2026-07-09，已被 07-10 owner 推翻）**：P0-3 曾在合约数据上正面对比
   SMA/EMA 20/60/120 与现行 EMA 8/13/21/34/55+144/200。20/60/120 的 AUC 更高
   但 top-decile 净收益显著弱于 8-55；当时曾保留 8-55。当前及未来只用六线 MA206。
4. **冻结模型工件已完成**：当前生效工件为
   `models/frozen_tp5_sl2_swap_ma206_20260710.txt/.json`，阈值 val q90=0.3409333202，
   best_iteration=32，数据 SHA256=`8df081a1374c0edb1ef8a869cc4825830ecb2f07fd00209306c44dcc272040d1`。
5. 前向跟踪脚本已完成：`scripts/forward_track.py` 默认从
   `2026-07-10 10:30 UTC` 起扫描 OKX SWAP，加载 MA206 冻结模型打分，阈值以上写入
   `data/forward_log_ma206.csv`，并按 `(source, symbol, signal_time)` 幂等补记已知出场。
   **07-10 全量重建**：358 个 SWAP、19,666 个已标签候选；前向扫描见 21,086 个历史
   候选，正式窗口 `new_signals=0`、`total_rows=0`。
6. MA206 前向验证窗口从 2026-07-10 10:30 UTC 起积累；每日定时任务
   `~/.claude/scheduled-tasks/daily-okx-data-update` **已包含**
   `update_okx` + `forward_track` + `daily_digest`（2026-07-10 核实，无需再等点头）。
   ~3-4 周后用冻结 TP5/SL2+maker 配置做最终 PF 裁决。
7. 真实资金费接入已完成：`src/data/funding.py` 读取 `data/funding/*.csv` 的 OKX
   `realized_rate`，按持仓跨过的 funding settlement 累计长仓成本；`swap_replication`
   同时输出旧 maker0.06% 近似和真实资金费覆盖样本结果。当前 funding 数据只覆盖
   54 个 SWAP、约 2026-04-07→2026-07-08，val top-decile 覆盖约 73%~76%；
   TP5/SL2 在当前数据池复跑后净@maker+真实资金费（覆盖样本）约 +0.003%/笔，
   filled-only 为 -0.012%/笔，属于前向验证必须重点盯的风险信号。
8. 看板完善一批已完成：`/api/overview`、`/api/backtest`、`/api/trades`、
   `/api/symbols`、`/api/chart` 均支持 `universe=swap|spot`；分数缓存写入
   `data/scored_signals_<universe>.csv/.json`，spot 训练/打分前会过滤混入的
   `_SWAP` 行。新增 `/api/forward` 和前向验证 tab，当前 `data/forward_log_ma206.csv`
   只有表头，因此页面显示 0/100、PF/胜率为空。VPS 已同步部署。
9. H10 做空侧已完成：新增空头候选扫描、空头 barrier 标签和
   `scripts/short_replication.py`。SWAP short TP5/SL2 val AUC 0.6174、p=0.001、
   top 净@maker +0.205%；但 ma_spread 单特征 baseline 净@maker +0.343%，所以只记为
   发现级 alpha 线索，不改主线。
10. H1/H2 出场复合已完成：`scripts/exit_variants_sweep.py` 已升级为 SWAP-only
    口径，输出 `analysis/output/exit_variants_swap.json`。H1 scaled：
    AUC 0.6106、p=0.001、top 净@maker +0.326%、maker 组合 PF 2.825/maxDD 0.29%；
    H2 breakeven：p=0.1738，不显著。H1 只是发现级候选，冻结主线仍不变。
11. R4 多时间框架已完成：`scripts/mtf_sweep.py` 输出
    `analysis/output/mtf_sweep.json` 和 `analysis/p2b_mtf_report.md`。H7 5m 未带来
    机会数扩张（val 仅 0.63× 15m，filled-only 为负）；H8 30m h72 发现级通过
    （AUC 0.6297/p=0.001/净@maker +0.484%），但样本只有 0.24× 15m；1H 样本太小。
12. P2-9 冒烟测试 + CI 已完成：新增长仓 barrier 四路径、组合模拟同币种/并发不变量、
    loader 合并去重、update_okx 幂等测试；`.github/workflows/tests.yml` 在 push/PR
    运行 compileall + pytest，依赖安装限定在判断层/看板测试链路，不拉 YOLO 训练栈。
13. P2-10 非鉴权部分已完成：看板信号页新增合格未成交列表与 hover/focus tooltip
    （score、阈值差、ATR%、密集长度、标签收益、入场价）；回测页新增只读分数滑块，
    只过滤成交明细表，不重算净值/PF；移动端修复 chart grid 子项撑破 390px 视口。
    owner 2026-07-09 已拍板暂不加访问控制。
14. P2-11 Round 1 打标审计页已生成：seed=20260709，输出
    `src/webapp/static/label_audit.html`，样本清单见
    `analysis/p2a_label_audit_round1.md`。localhost:8643 真实浏览器验证桌面/390px
    手机均无横向溢出。**07-10 owner 确认** findings（PAXG 超宽、边缘残框等）；
    下一步单变量 E1 收 `x_pad_px`，改参前仍不重训。
15. P2-12 数据质量审计已完成（2026-07-10）：报告
    `analysis/p2_data_audit_report.md`；黑名单候选以股票/ETF 类薄流动性 SWAP 为主；
    **07-10 owner 确认** 22 个 base 已写入 `loader.BLOCKED_BASES`。
16. P2.5 Phase 0–3 已完成（2026-07-10）：ops Bearer/`X-Ops-Token` 鉴权、
    实验注册表、议程、**白名单 job runner**（默认 executor 关）、只读 data/model hub。
    VPS **禁止** `ENABLE_JOB_EXECUTOR=1`（`deploy_vps.sh` 强制写 0）。说明见
    `docs/P2_5_PHASE01_README.md` / `PHASE2` / `PHASE3`；设计见 `docs/P2_5_OPS_CONSOLE_DESIGN.md`。

## 明天开工的第一条消息（可直接粘贴）

> 读 CLAUDE.md、HANDOFF.md、analysis/p2b_v2_report.md。2b 已验收通过，
> 当前工作是 HANDOFF 第 3 步：阶段 3 事件驱动回测框架。按 PROJECT_PLAN 阶段 3
> 规范自写实现（taker 费 + 滑点 + 资金费近似），先给出模块划分和成本模型设计
> 让我确认，再写代码。阶段 3 的冻结 holdout 方案也需要先和我讨论
> （2b 的 holdout 窗口已消耗，回测的样本外窗口如何定义是一个待决策问题）。

## 本仓库的知识地图

| 想知道什么 | 看哪里 |
|---|---|
| 为什么做这个项目、旧项目怎么死的 | `README.md` |
| 三阶段路线图与验收标准 | `PROJECT_PLAN.md` |
| 人工标签有没有 alpha（P0） | `analysis/p0_alpha_report.md` |
| YOLO 检测层怎么训、效果如何 | `analysis/p2a_detection_report.md` |
| 判断层 v1 为什么"有信号没利润" | `analysis/p2b_judgment_report.md` |
| v2 双池实验结果与下一步选项 | `analysis/p2b_v2_report.md` |
| 踩坑记录（原子化笔记） | `docs/learnings/` |
| 工作纪律与质量标准 | `CLAUDE.md` |
