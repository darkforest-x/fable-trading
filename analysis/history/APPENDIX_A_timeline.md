# 附录 A:提交时间线

共 412 次提交,20 个活动日。由 `git log` 生成,非回忆。

## 按类型

| 类型 | 次数 |
|---|---|
| other () | 211 |
| docs (文档) | 42 |
| feat (新功能) | 26 |
| status () | 12 |
| fix (修复) | 12 |
| diag (诊断) | 8 |
| build (构建) | 8 |
| HANDOFF () | 5 |
| detection () | 5 |
| judgment () | 5 |
| labels () | 5 |
| research () | 5 |
| dashboard () | 4 |
| measure (测量) | 4 |
| ops () | 3 |
| execution () | 3 |
| v16 () | 3 |
| backtest () | 3 |
| train (训练) | 2 |
| housekeeping () | 2 |
| tools () | 2 |
| Erratum () | 1 |
| README () | 1 |
| Docs () | 1 |
| Architecture () | 1 |
| Status () | 1 |
| webapp () | 1 |
| E3 () | 1 |
| Queue12 () | 1 |
| Queue13 () | 1 |
| Housekeeping () | 1 |
| Queue14 () | 1 |
| Queue15 () | 1 |
| queue15 () | 1 |
| Queue16 () | 1 |
| Task10 () | 1 |
| Task9 () | 1 |
| ls_auto_import () | 1 |
| forward () | 1 |
| 3060 () | 1 |
| eth_micro () | 1 |
| scout () | 1 |
| handoff () | 1 |
| scout_mtf () | 1 |
| live (实盘) | 1 |
| deploy (部署) | 1 |
| plan () | 1 |
| test (测试) | 1 |
| analysis () | 1 |
| doctrine () | 1 |
| cleanup () | 1 |
| loader () | 1 |
| evidence () | 1 |
| verdict () | 1 |
| learning () | 1 |
| audit () | 1 |
| tooling () | 1 |
| hooks () | 1 |
| S3 () | 1 |
| factors () | 1 |
| prereg (预注册) | 1 |
| charts (图表) | 1 |
| scan () | 1 |

## 按日

### 2026-07-07 — 4 次提交

- `5c345be` **other** Add project skeleton and P0 alpha analysis of human launch labels
- `1ddc00e` **other** Add judgment layer (2b): strict-rule candidates, triple-barrier labels, LightGBM training
- `bbe78a5` **other** Add detection layer (2a): YOLO dense MA cluster pipeline.
- `a53d5cc` **other** 2b-v2: wider barriers (TP4/SL2 ATR + atr floor), expanded pool preset, OKX history fetcher

### 2026-07-08 — 19 次提交

- `d22eb57` **other** Parallelize OKX fetcher: browser UA for WAF, symbol thread pool + global throttle
- `e7a8389` **other** 2b-v2 results + handoff pack: expanded pool passes val acceptance
- `925e7b2` **Erratum** purge/embargo was already implemented; retire HANDOFF step 1
- `f0e9d6c` **other** 2b acceptance PASSED: one-shot holdout eval of expanded x v2 (owner-approved)
- `34cd017` **other** Install extract-approach skill (owner-executed), drop pending-install copy
- `fc82a00` **other** Stage 3 round 1: event-driven backtest -- acceptance NOT met (PF 1.01 @ 0.3%)
- `4dec21b` **other** Full-stack dashboard: FastAPI backend + vanilla JS / Lightweight Charts frontend
- `6a48226` **README** dashboard section
- `64c5fdc` **other** Dashboard v2: pro-grade analytics views
- `871e11e` **other** Add AGENTS.md mirror of CLAUDE.md for cross-tool agent compat
- `36464ca` **other** v3 exit-structure sweep: TP5/SL2 wins on val (net +0.077%/trade @0.3% cost)
- `2ced8a8` **other** Add one-command VPS deploy script for the dashboard
- `1bfbb6e` **other** Allow VPS deploy ssh/rsync without permission prompts (owner-requested)
- `41824bb` **other** v3 round 2: time hypothesis falsified, maker entries are the big lever
- `660fce4` **other** Incremental OKX updater: forward-validation data feed (route D)
- `1f9ecfa` **other** Signal browser: trade anatomy view + three portability bug fixes
- `6a1adc8` **HANDOFF** pending queue snapshot (YOLO finishing, swap refetch, MA decision)
- `bf496ef` **other** Offline relay pipeline: finish the pending queue without Claude
- `53404e4` **other** Work order for tomorrow's Codex session (owner out of Claude quota)

### 2026-07-09 — 39 次提交

- `7bdcf13` **other** Expand work order into a full prioritized engineering plan (P0-P3)
- `ee19eb7` **other** Project management pack: frontend bug tickets, architecture doc, status/schedule/risks
- `297f0b7` **other** Evolution engine: research agenda (15 hypotheses), MTF plan, frontend console roadmap
- `10c19d4` **other** H9 discovery-tier PASS: 1h EMA144 alignment filter (+0.051%/trade maker-net)
- `6337c44` **other** Offline queue #2: funding rates, multi-TF data, H1/H2 exit variants
- `25e594e` **other** Agenda H16-H18: volume-price family (owner's breakout-volume insight formalized)
- `7d05bea` **other** Label-audit sample pack + YOLO iteration protocol (owner review loop)
- `4fa6bbb` **Docs** standalone SVG diagrams (LightGBM 10-step pipeline, triple-barrier anatomy)
- `020dbc7` **Architecture** document the known MA-set mismatch between 2a and 2b layers
- `7cb0070` **other** Overnight results: H1 scaled exit is a discovery-tier standout; pipeline PYTHONPATH fix
- `caad4b3` **other** Swap replication PASSES: mainline universe is now SWAP
- `72f00cd` **other** Work order: Codex worktree workflow + morning results marked done
- `6162672` **other** Session chronicle: the 07-07..09 sprint, distilled
- `9ad27ab` **other** Offline queue #3: H1xH9 stack on SWAP, multi-TF first pass, data audit
- `815de39` **other** Offline queue #4: v3 stack portfolio backtest on SWAP + auto-deploy to VPS
- `246ea14` **other** Queue3 results: 30m timeframe is the richest cell yet; scaled exit holds on SWAP
- `40f02c0` **other** Offline queue #5: tp5 reconciliation, frozen model artifacts, forward tracker
- `eceb4d0` **other** Offline queue5: frozen artifacts, forward-log first pass, v3 sim outputs
- `1fca9be` **other** Document swap replication in v3 report
- `612cae8` **other** Compare MA 20-60-120 judgment pool
- `3f5e647` **other** Record MA mainline decision
- `6b70b7d` **other** Fix signal chart autoscale on focus
- `de543f3` **other** Fix signal band autoscale
- `a14dfff` **other** Deduplicate signal exit barrier lines
- `141d305` **other** Record offline YOLO acceptance result
- `42ca210` **other** Freeze tp5 sl2 swap model
- `9adb6ab` **other** Add forward signal tracker
- `8925f2f` **other** Add realized funding cost replay
- `dc90d1d` **other** Add dashboard forward and universe views
- `57f8fc2` **other** Generalize sweep bench for multi-timeframe research
- `bf17651` **other** Evaluate H9 trend filter on maker simulations
- `2ee3ab6` **other** Evaluate mirrored short-side candidates on swaps
- `d663115` **other** Evaluate H1 H2 exit variants on swaps
- `c22861b` **other** Evaluate multi-timeframe swap pools
- `c21f6a9` **other** Add smoke tests and CI
- `b6c662c` **other** Improve dashboard non-auth controls
- `d5408ad` **other** Record dashboard auth decision
- `f567166` **other** Generate YOLO label audit round one
- `1b26e6d` **other** Record offline watcher marker lesson

### 2026-07-10 — 58 次提交

- `5a1cea1` **other** Record SAHI baseline lesson
- `f605cb1` **other** Record offline pipeline YOLO eval and swap replication results
- `10afbd5` **other** Add Telegram daily digest and notifier plumbing
- `9abc848` **other** Complete P2-12 data audit and wire forward daily chain notes
- `3a37c68` **other** Record YOLO label audit findings and offline task status
- `ab12d70` **other** Apply owner confirmations for blacklist and label audit
- `1c1344f` **other** Merge branch 'codex/day1' into main
- `9cf6369` **other** Note successful codex/day1 merge on main
- `3d197b7` **other** Implement P2-11 E1 tighter x_pad and relabel dataset
- `d2786d7` **other** Add E1 pad12-vs-pad6 overlay audit page
- `5340fe6` **other** Implement P2-11 E2 max dense core-trim for long boxes
- `0e57a77` **other** Add P2.5 ops console auth-first design draft
- `803daba` **other** Record long dense run core-trim lesson
- `b35a03b` **other** Implement P2.5 Phase 0+1 ops auth, experiment registry, and agenda
- `7c935d7` **other** Tighten E2 core-trim MAX_DENSE_BARS from 24 to 12
- `990fa67` **other** Add FiftyOne and Label Studio review tooling
- `65cd94d` **other** Add overnight morning brief template and plan doc
- `775d6fd` **feat** P2.5 Phase 2 hard-coded job runner
- `6374c73` **docs** H1 forward shadow plan + acceleration options
- `2c38273` **other** Merge P2.5 Phase 2 job runner into main
- `5d9bee1` **other** Wire ready-to-run FiftyOne and Label Studio starters
- `245ff1a` **other** Document multi-day autonomous charter and Phase2 completion notes
- `8684836` **feat** P2.5 Phase 3 read-only data and model hubs
- `d7ac593` **other** Add YOLO consistency_check and E2.1-vs-old-best baseline
- `1bd871f` **feat** H1 scaled exit shadow forward logger
- `cd326aa` **feat** P2.5 Phase 3 read-only data + model hubs
- `a754c29` **other** Update multi-day status after Phase3 merge and consistency baseline
- `76259a7` **Status** H1 shadow live, YOLO epoch2, multi-day pulse
- `b87c4cd` **other** Merge Phase3 hub test hardening and README
- `07081f8` **other** Record FINAL SWAP expansion results and post-expand data audit
- `5825ebf` **other** Finalize SWAP universe expansion and offline summary reports
- `015ddcf` **other** List post-expand SWAP blacklist candidates without applying BLOCKED
- `52afca3` **other** Add interim YOLO E2.1 training curve while retrain continues
- `f2ddc6f` **other** Add ACTIVE freeze pointer and YOLO E2.1 instability learning
- `63f2a72` **other** Improve daily digest for dual forward books and E2.1 mAP
- `1d8510c` **other** Arm post-train finalize watchdog and refresh E2.1 interim curve
- `146f566` **docs** multi-day status, PROJECT/HANDOFF sync, FO hard note
- `2ec48b5` **docs** NEXT_STEPS multi-day charter pointer and morning brief
- `1c870e3` **ops** live part-file scan in data hub; force VPS executor off
- `7b89132` **status** multi-day pulse after VPS executor pin and part resume
- `0c3d0cb` **webapp** show live incomplete .part.csv on data hub tab
- `88be09d` **status** ANIME/MANA fetch complete; E2.1 still training
- `147dc67` **ops** multi_day_pulse heartbeat for unattended days
- `61639a7` **docs** refresh E2.1 interim after ep18 val window
- `a1476ae` **ops** finalize FO hardlist after E2.1; digest reports train alive
- `c1f5be9` **status** 2h durable pulse — E2.1 ep19 train, forward dual-book OK
- `e2c45d1` **status** hourly pulse — E2.1 ~ep22, dual forward stable
- `a4f2b1e` **status** 2h durable — E2.1 new best ep25 mAP50=0.844, FO up
- `83b1697` **status** hourly — E2.1 best ep29 mAP50=0.851, dual forward stable
- `820a4df` **status** hourly — E2.1 best ep30 mAP50=0.855, train ~ep33
- `c187c69` **status** 2h durable — E2.1 best ep30 mAP50=0.855, 33 epochs, pytest green
- `376e8db` **status** hourly — E2.1 best ep30 mAP50=0.855, train ~ep37/40
- `35eab75` **status** YOLO E2.1 train finished 40/40 best ep30 mAP50=0.855
- `ce3c8e3` **other** Finalize YOLO E2.1 retrain report, consistency, FO hardlist
- `82b783d` **status** E2.1 formal finalize complete — mAP50=0.85 FAIL gate 0.90
- `0954f3c` **docs** E2.1 formal FAIL 0.90 recorded; 2h durable pulse
- `45037e6` **fix** Label Studio login email must be valid (example.com)
- `a5891aa` **status** hourly — E2.1 formal locked, dual forward stable, e21b side train

### 2026-07-12 — 4 次提交

- `a0e3049` **other** Pre-register forward verdict counting rule: crypto swaps only
- `b37518c` **other** Deep-history accelerator: fetch 2021+ swaps, pre-registered one-shot frozen test
- `13fd911` **E3** the boundary-contradiction experiment for the YOLO mainline (owner mandate)
- `3171934` **other** Label Studio round-2 packs: 200 val + 120 train tasks (deduped vs round 1)

### 2026-07-13 — 23 次提交

- `ddc5af1` **other** E3 margin diagnosis result (gap 50.4pp)
- `7b9e66c` **other** Golden set round 1: owner rejects 70% of rule boxes; rule family ceiling F1~0.45
- `b86f9fc` **other** Round-2 golden: owner self-consistency F1 0.88; owner-taste detector v1 launched
- `94e8907` **other** Owner-taste detector v1 result
- `16e787c` **other** Round-3 ammo: 2973 labeling tasks in 6 chunks + base-recipe comparison queue
- `09d87e5` **other** Deep-history one-shot test result (pre-registered)
- `c2aa7ae` **other** Owner-detector base comparison (scratch vs pretrained vs E2.1)
- `d0643f6` **other** Deep-history PASS logged; remote-labeling tunnel; v3 dual-base + swap round-4 queues
- `b23629c` **other** Tunnel also on port 80 (firewall-friendly, no port number needed)
- `574a309` **other** Round-4 swap packs rendered and auto-imported into Label Studio
- `e43c1bf` **other** Round-4 swap packs rendered and auto-imported into Label Studio
- `a378ead` **other** Auto-import pipeline: session+CSRF auth, cache-dir fix, idempotent projects
- `fec793f` **other** Round-4 swap packs rendered and auto-imported into Label Studio
- `ab14d65` **Queue12** v4 training on the full 2268-image golden pool (winner base auto-picked)
- `a56cc8a` **other** Round-4 swap packs live in Label Studio (1313 tasks, images verified)
- `5cced9f` **Queue13** 30m tp x horizon deep grid on CPU while GPU trains
- `90d7527` **other** 30m deep grid (tp x horizon, val only)
- `9bbf44d` **Housekeeping** commit three days of multi-agent work products
- `79de68d` **other** 30m deep grid rerun: tp dimension actually varied this time
- `4d804de` **other** Owner detector v3: dual-base comparison on 1268 golden images
- `3ad774b` **other** Bug sweep fixes: stockish side-channel, config clobbering, stale dashboard, shared eval
- `1c28a1a` **other** Fix dashboard 500: frozen-artifact loader was greedy and fail-hard
- `b4bb2a7` **other** Owner detector v4 on 2268-image golden pool

### 2026-07-14 — 16 次提交

- `60fb648` **Queue14** v5 dual-base on the mixed-universe golden pool
- `c6936e6` **other** Visual scout live loop + active-learning pack generator
- `c64561b` **other** Enable fable-advisor plugin (architect-as-orchestrator, security-inspected)
- `a1f46a3` **other** Automation chain snapshot before quota exhaustion
- `0474c3f` **other** Scout fixes: sync gallery to VPS each scan; HIMS & co join the stockish list
- `70008f6` **other** Scout v2: leaderboard universe, 1-hour freshness gate, 10-min cadence
- `85e071a` **other** Commit round-4 owner label exports; scout runtime output gitignored
- `f3fc910` **other** Owner detector v5: 3581-image mixed-universe pool, dual-base
- `47c48bf` **other** v5 winner promoted to owner_best; round-5 model-prelabeled packs live
- `99d20ab` **other** Frozen eval set fixes the shifting-val confound; promote v4 (真最强)
- `ebec296` **other** v5_from_v4 promoted: frozen-F1 0.663 (P0.758 R0.590) -- curve is climbing, not plateaued
- `ba9af45` **Queue15** v6 dual-base training on 4501-image pool, eval-symbols excluded
- `9b88f8a` **queue15** auto-restart scout after v6 training completes
- `b1033d0` **Queue16** H19 factor IC screen (CPU-only, niced)
- `0d78575` **other** H19 alpha factor library: 14 causal crypto-safe factors + IC screen
- `d191578` **other** Dashboard shell rework (Codex/Grok) + round-5 label exports

### 2026-07-15 — 39 次提交

- `9145416` **other** H19 factor IC screen: 14 causal crypto-safe alpha factors vs dense-launch forward return
- `62f34ab` **other** YOLO critical-path A/B: candidate-source converter + head-to-head runner, queued after v6
- `1909f9b` **other** Automate judgment-layer feature-gain evidence (H19 -> single-var screen)
- `eb79236` **other** H19 factor IC screen done (2/14 alive, weak); Grok overnight task batch spec
- `b42f1ee` **other** Grok runner: --prompt-file (fixes long-prompt shell mangling) + acceptEdits
- `1bb323b` **other** Grok isolation: run in a separate git worktree (~/fable-trading-grok)
- `fe50068` **other** Grok runner: per-task loop (single-turn mode completes one focused task each)
- `21b9320` **other** Grok runner: --continue loop per task (single-turn budget was too small for multi-step tasks)
- `9c52fff` **other** Harden queue17 gate: wait for v6 promotion, not just training end
- `fa0d927` **other** Task4 H3: MA-exit (close<EMA21) beats TP5 on val maker net
- `1fb2293` **Task10** factor causality + scaled/breakeven path tests; RESULTS
- `117f745` **Task9** dedupe tunnel scripts and owner F1 eval loops
- `6188e46` **other** Task1 H14/H17/H18: volume factor trio IC screen (all dead)
- `f546e5a` **other** Task2 H15: dense-quality second-order factors IC (all dead)
- `0fda2af` **other** Owner detector v6: 4501-image pool (round5 labeled), frozen-eval promotion
- `6d82b75` **other** Fix queue17 deadlock: v5 won promotion, not v6, so 'wait for owner_v6' hung forever
- `e2ed66d` **other** Correct promotion: v5's 0.663 was leakage (43/47 eval symbols in its train)
- `8425cd2` **train** speed knobs without hurting accuracy (workers/cache/plots/patience)
- `fcc8fb5` **docs** note cold-start patience recipe in offline_pipeline
- `f6c2361` **other** docs/learnings: YOLO speed knobs without accuracy hit
- `a4e422f` **other** Round-6 LS pack: 50% SWAP hard + 50% scout/model-uncertain
- `59cf652` **other** Round-6 expand: +1500 tasks (chunks 3-5), total 2000
- `62f228c` **other** round6 pack: NMS prelabels so one dense region → one box
- `bf9c4b0` **ls_auto_import** register all local-files roots in a pack
- `c22d099` **other** A/B complete: YOLO vs rule candidate sources on SWAP val
- `5f4b23d` **other** Mainline cutover: YOLO candidates + frozen_tp5_sl2_swap_yolo
- `6a97581` **other** forward YOLO: skip series with no bars after FORWARD_START
- `dc6a4e6` **other** forward YOLO: live mode only scans right-edge windows
- `3a67373` **other** yolo live predict on CPU to avoid MPS hang in multi-series forward
- `a126bde` **other** yolo scan: per-process temp PNG path
- `79af5bc` **forward** load YOLO before LightGBM; pin OMP threads (fix MPS hang)
- `4b249d8` **docs** YOLO mainline forward ops notes (OMP, venv)
- `30b3c57` **other** Backtest YOLO mainline; refresh score cache path for deploy
- `828bd62` **other** A/B verdict retracted: 88/101 val symbols were in the detector's training set
- `641a4eb` **other** Task5 H4: time-decay SL (flat vs TP5, discovery fail)
- `f572619` **other** Task6 H5: vol-adaptive barriers slight edge vs fixed TP5
- `c44b7d8` **other** Task7 H11: liquidity-tier models vs pooled (marginal stack edge)
- `d0b90c3` **other** Task8 H8: 30m grid TP{4,5,6}x h{48,60,72} (h60 not stable best)
- `019eaca` **other** docs/learnings: overnight batch insights (IC collapse, MA-exit, skew)

### 2026-07-16 — 20 次提交

- `417f8a4` **other** Decisive A/B: v7-holdout detector on 106 never-trained symbols, top-30% bucket
- `574199f` **detection** fix optimizer='auto' destroying every chain fine-tune
- `604c059` **detection** learning curve to test whether more labels buy generalisation
- `4c050dd` **3060** pull args.yaml/results.csv home and check the curve before the score
- `5262a6b` **detection** one definition of the eval/val split, and a dataset builder that uses it
- `50c8ea8` **detection** exemplar gate + materialized eval ruler (closes a real straddle leak)
- `ab9bce7` **eth_micro** ETH-only 1/2/3/5m channel backtest + monitor + signal notify
- `9c3aef4` **judgment** regression target (realized_ret) replaces binary as ACTIVE
- `6a2b130` **other** forward + dashboard: YOLO mainline wiring, low-TF backtest, scout tweaks
- `9ba1107` **labels** round6/7 exports, v8_chain prelabels for unstarted chunks, owner_best -> v8_chain
- `3d212e3` **judgment** refit regression on the clean v8_chain candidate pool (val-side win)
- `301e543` **scout** signal-style photo alerts with judgment score (owner format request)
- `d03090d` **judgment** cut ACTIVE over to the clean v8-pool regression (owner-approved)
- `f23b552` **handoff** 2026-07-16 current-truth section; deploy ships the v8 pool csv
- `0b124a4` **labels** dedup v8 prelabels for round7 chunk5-6 (142 duplicate boxes removed)
- `1a2fddf` **detection** v9 partial-round results (Grok --force at 83%) -- the curve holds
- `bcd7164` **dashboard** status strip shows judgment ACTIVE; vendored lightweight-charts (Grok)
- `2a32ce0` **housekeeping** cost route table, explicit backtest artifact selection, one truth doc
- `323d2cf` **labels** round8 generator -- fresh 2026-H1 windows, non-overlapping by construction
- `4b7aec4` **housekeeping** root down to five canonical docs; README rewritten for reality

### 2026-07-17 — 5 次提交

- `a53f278` **feat** scout_mtf, live executor sizing, dashboard UX, round8 v2 packs
- `87c2876` **scout_mtf** radar rank/UI refinements, v10 promotion record, volume-quote learning note
- `3e80052` **labels** round9 generator -- 2025-H2 windows, triple-dedup, v10 prelabels
- `861378b` **other** judgment audit + H-TS + v10 pool backtest: the gap lives between replay and live
- `83a0787` **execution** NaN atr_pct produced tp=sl=0 brackets; refuse entry when barriers unusable

### 2026-07-18 — 6 次提交

- `9821aec` **other** mine grok/overnight branch: H13 BTC-regime experiment + batch RESULTS digest
- `aff89b1` **execution** signal freshness gate, TG trade alerts, systemd single instance
- `6fd34b6` **dashboard** nav slimmed to the core loop; v11 local training pipeline
- `6dde333` **other** forward gate: single writer, 15-min pulse, YOLO source, snapshot ring
- `9e487dc` **other** execution docs match reality; drop the VPS override that starved the live loop
- `38212a2` **judgment** v11 pool config made real; default stays v8 until the artifact exists

### 2026-07-19 — 9 次提交

- `f10ee36` **other** Cut judgment mainline to v11 pool and harden forward live ops.
- `33c65bb` **other** Fix live forward blank scan when FORWARD_START leads last bar.
- `0b45ee8` **other** Simplify multi-TF radar UI to a clear, single-purpose console.
- `5cf9a5f` **other** Harden live trading path for overnight unattended runs.
- `8da871b` **labels** round10 packs (4x300, zero-repeat verified) + system diagrams
- `0714095` **other** live ops: daily funnel digest, walk-forward in every freeze, version pin
- `f38b985` **execution** enforce the validated 72-bar timeout exit on live positions
- `e15bbb4` **other** forward gate: hindsight detections cannot buy the verdict; enforce per-symbol gap
- `93972f2` **other** forward verdict: raise FRESH_DETECT_MIN 20->55 to match executor gate

### 2026-07-20 — 20 次提交

- `48d94db` **feat** H-TIP ops, short-tf side channel, and dashboard UI polish
- `67d8733` **live** real-time tip detection — record signals on the pulse their bar closes
- `1e483b5` **docs** HANDOFF tip-path truth block + two learnings (gate arithmetic, record-first backfill)
- `6e78337` **other** live scan: 6-window schedule (tip+2 pinned + stride walk) — undo 14-window tip-dense
- `e0c2ce8` **other** Rename project to darkforest-trading and revise sections
- `19b45b2` **deploy** stop pushing Mac klines to VPS — VPS is the live single writer
- `5a4f85f` **other** forward scan: phase wall-clock telemetry (discover vs phase2)
- `45b9443` **other** live scan: slice each series to a 2000-bar tail — full-history pandas was the growth term
- `f1adcfe` **other** live scan: one batched predict per series instead of 6 single-image calls
- `2073d01` **HANDOFF** pulse performance numbers and shelved optimization levers
- `626e88e` **plan** one-week execution plan for Grok (v12 eval -> shadow -> owner cutover decision)
- `31d702e` **dashboard** chart UX iteration — MA display modes, trade focus card, symbol combo
- `c292f55` **docs** refresh living docs for live + H-TIP phase (2026-07-20)
- `2ad7261` **feat** v12 H-TIP eval pass + tip-only shadow path (no promote)
- `1fad130` **fix** ship scripts to VPS for v12 shadow + source env file
- `e197271` **docs** v12 shadow VPS start record
- `770225e` **feat** owner-forced detector mainline cutover to v12 H-TIP
- `1279c4e` **docs** keep owner_best pre-v12 JSON sidecar for rollback
- `4544535` **feat** show v11 mainline backtest; replace stale v8 compare table
- `672a430` **fix** v12 shadow summary payload is a JSON string not dict

### 2026-07-21 — 28 次提交

- `75a37a1` **feat** v12 pool frozen config + cutover script (no promote)
- `ee07349` **test** exit parity suite for backtest vs executor semantics
- `79a665e` **feat** weight-centric sizing backtest on val window
- `b29b5be` **feat** v12 val-window rescan probe + score-shift compare tooling
- `a455db2` **docs** learnings — libomp segfault, exit parity args, isotonic step collapse
- `e290c70` **fix** exclude holdout rows from v11 baseline in valwin score comparison; add v12 score shift report
- `f4cfbd1` **feat** one-shot single-symbol live probe CLI (check_symbol.py)
- `20e2fb3` **feat** one-click symbol probe tab backed by check_symbol subprocess
- `5059016` **fix** probe timeout 150s + UI wording from measured VPS runtime (~41s)
- `df23352` **fix** tolerate pre-tiered-sizing frozen artifacts in check_symbol (VPS skew)
- `6f52b49` **feat** tiered sizing ready locally; tip-subset offline probes
- `542e62a` **feat** deploy tiered sizing with half-slot headroom
- `d0b5cfb` **feat** live-truth panel, freshness lag badge, richer probe cards
- `076babc` **docs** night report 2026-07-21 and execution slippage notes
- `6e925fe` **feat** tip-subset rerender resume/checkpoint and progress CSV
- `5a63bec` **feat** tip-subset val backtest with 0.0465 live discount
- `d5f6416` **docs** diagnose box→bar lag as semantic, not geometric bug
- `88d1f55` **feat** tip-edge gate keeps last N bars only (A′)
- `b9a1c77` **feat** tip-only env switches + smoke diag (default stays live)
- `9f81bd9` **docs** capture pad200 crop, stem-index, and mid-gold tip lessons
- `44aca7c` **feat** pad200 crop-after-box build and v13 train pipeline
- `bab7ea6` **feat** live tip auto-label and Label Studio 1000 pack
- `6d02ccb` **docs** pad200 and tip-only crop preview artifacts
- `d2b2286` **feat** pad200 build --resume and optional --mad-gate
- `10f17fe` **feat** add v13 pad200 watchdog for resume loops
- `2f687f7` **docs** map within-bar YOLO realtime path vs GitHub candidates
- `294cbce` **docs** review ChartScanAI for tip-detection lessons
- `3897ebe` **docs** shortlist GitHub repos by real fable pain points

### 2026-07-22 — 30 次提交

- `def0932` **docs** backlog future opts for detect + judgment layers
- `90eca18` **docs** detail judgment backlog and open-source shortlist
- `60ab8db` **docs** append judgment-layer GitHub building blocks to backlog
- `a9f5d40` **docs** open H-DET tip hypothesis cluster and register known results
- `707468d` **docs** add external YOLO sources and H-DET-EXT hypotheses
- `132b6c4` **docs** inventory H-DET-2 hard-negs and harden tip-smoke eval while v13 trains
- `5949cb6` **docs** scan wuzao topics and register H-FE/H-TOOL side hypotheses
- `d885e00` **docs** widen wuzao scan from tip-only to whole-repo useful
- `3c89a42` **other** Land wuzao A-tier debug tools overnight without touching v13/MPS.
- `304e536` **docs** refresh HANDOFF with v13 pad200 mid-run snapshot.
- `4b0c403` **feat** land visible dashboard viz (Tabulator, LWC boxes, status lamps)
- `22125a3` **fix** keep local :8642 alive via user launchd
- `d875438` **fix** quiet viz chrome after owner style feedback
- `1005175` **docs** deepen wuzao topics beyond frontend by subsystem use
- `7a35a33` **other** Land local side-tool venvs and discovery artifacts without touching v13/MPS.
- `86c699e` **docs** record v13 pad200 finale and H-DET-1 tip miss
- `c8b53d4` **docs** diagnose v13 trainset — labels OK, tip collapse real
- `bdde170` **fix** pad200 MAD default — okx_ stems were cut on wrong window
- `8227c82` **docs** record v13 pad200 wrong-window root cause in HANDOFF
- `5de1797` **docs** explain why pad200 stem fix regressed on v13 bulk
- `5494ab3` **docs** v14 pad200 MAD rebuild done + Windows train handoff
- `872c1d4` **fix** v14 Windows train ships over SSH to 3060, not USB
- `3d68767` **fix** point v14 .bat at SSH sync instead of manual copy
- `aa192d8` **docs** v14 sample30 + okx wrong-window audit clears sync
- `17cc277` **fix** point v14 3060 sync at live LAN IP and train_dense.py
- `4a9c0f0` **docs** v14 MAD-on tip eval fails discovery bar
- `f48aa10` **docs** v14 tip failure rootcause (C>B>A)
- `d3c0309` **other** Add v14 pad200 launchd train wrappers so local MPS training survives agent shell death.
- `5d888a4` **other** Add v15 tipval pipeline and OOM learning so tip-aligned val can train on 16GB without ap_per_class blowups.
- `28fc844` **docs** add 2026-07-22 owner project overview

### 2026-07-23 — 41 次提交

- `14e4912` **other** Start real tip success/fail prelabel pack for Owner review.
- `86fd4b7` **other** Revalidate v15 tip discovery with fair full-MA and real-tip denominators.
- `9a47d0a` **docs** land v15 verdict + tip-eval fairness audit into HANDOFF/agenda/week-plan
- `6cb0de0` **analysis** v15 failure root cause — pos/neg from two render pipelines (style shortcut)
- `947c168` **other** v16 builder: fresh-render every image through one pipeline (owner-approved)
- `d90a3b5` **v16** build complete (6963 train / 2987 val) + two-arm train runbook for Grok
- `8b54d9e` **v16** owner ruling — cold start only, v12 never a training base
- `bc10fb7` **v16** Windows 3060 sync + cold-start train launcher (owner: v12 never a base)
- `034b821` **other** v16 fix: val positives must be tip-aligned — source from v15 tipval, not v14
- `2049bb1` **HANDOFF** dataset quarantine (pre-tip style banned from training) + v16 val fix record
- `3cd3d3d` **doctrine** live detection means the newest bars ONLY — purge hindsight paths
- `e28c57a` **docs** live-tip doctrine as iron rule 12 (CLAUDE/AGENTS synced) + HANDOFF truth
- `66fea9c` **other** purge v12 shadow path + guard mainline preload for detector=none idle
- `363385e` **cleanup** archive pre-v16 scripts (35) + superseded docs; purge temp junk
- `ba010d7` **other** honest tip-replay backtest harness + strip stale v12/v13 tiles
- `c009360` **other** status strip: v16 epochs target 60 + test updated to new sidecar name
- `d3f70f8` **other** v16 verdict: FAIL the golden gate — unified pipeline did not cure false fire
- `0d79783` **other** v17 data engine: per-pulse real-tip collector + owner review pack builder
- `e78ae20` **HANDOFF** v17 data engine online (per-pulse real-tip collection + review pack)
- `832102b` **HANDOFF** v16 verdict reversed (auto-labels wrong, not the model); holdout #6 pre-authorized under discovery gate
- `27232c3` **dashboard** lead backtest tab with v16 tip-replay verdict; fold old hindsight numbers into a deprecated collapsible
- `47b0b4e` **loader** tolerate a stray non-UTF8 byte in a kline csv (encoding_errors=replace)
- `c862202` **evidence** v16 empty-tip 'false fire' gallery (verdict-reversing) + real-tip review pack output
- `e6f9e02` **other** v16 VERDICT: falsified on clean holdout — detector-only loses, judgment is anti-predictive
- `2b1e4d3` **other** base-rate test: dense geometry alpha with NO detector (pure rule) vs random baseline
- `de3d40a` **other** base-rate verdict: dense geometry is real but marginal; cost is the killer, not detection
- `3e7fda2` **docs** code review of recent tip/v16/v17 changes (2026-07-23)
- `094e210` **other** same-source judgment: dump v16 candidates + train aligned LightGBM
- `0e4863d` **other** same-source judgment (a+b): add BTC-regime/rel-strength/time features, scale to 100 sym
- `7962fcf` **verdict** walk-forward falsifies the same-source-judgment 'edge' as regime luck
- `ed0b8b4` **other** owner-box alpha test: manual eye is consistent (AUC 0.8-0.9) but not causally predictive
- `cfe92eb` **other** wrong-exit + two-layer tests: whisper of regime-dependent edge, never robustly tradeable
- `711a9f5` **other** directional analysis: the move is real (oracle PF 2.68) but direction is a coin flip
- `1afcafb` **research** owner-label oracle ≠ tip; launch entry fails 1.3 even when split by side
- `cd78e79` **docs** absorb tonight's research into HANDOFF truth; holdout N=6
- `d1c87ac` **tools** stream owner long/short review pack without shipping preview JPGs
- `88c0f81` **research** causal direction-select fails to rescue PF past 1.3
- `39d4414` **research** holdout#7 falsifies A short trend-exit (PF≈1.0)
- `20c5e4e` **tools** fix gallery Prev under unlabeled filter via visit trail
- `4c1703b` **research** archive side/exit/entry-timing findings before E1–E3
- `7b6b4bb` **research** attribute holdout collapse; E1–E3 do not unlock edge

### 2026-07-24 — 11 次提交

- `ea10311` **other** judgment-layer lab: v16+short-below-all-MA+short-quality judgment rescues period3 to 1.12
- `3e6997c` **other** judgment lab IT-07/08: vol-regime gate and rolling retrain both fail to stabilize
- `25819bf` **other** judgment lab IT-09: long and short are COMPLEMENTARY across regimes (owner was right)
- `ba172da` **other** judgment lab IT-10/11: regime side-selection exhausted (5 angles, same wall)
- `977ed48` **other** judgment lab IT-12/13: entry-timing lever also fails -> honest verdict
- `d7d3c34` **learning** dense-cluster launch has no causally-tradeable direction edge
- `92bf084` **other** judgment lab IT-14: visual-gestalt direction pre-check fails (4th evidence line)
- `f3d9dd3` **other** Commit tip-aligned short-only pipeline: builders, --side short judgment, docs.
- `bd03992` **other** Align short judgment to v11 regression path (objective + feature mirror).
- `6b0e871` **other** Record short 30x6m reg walkforward and 100-coin scan harness.
- `147d042` **other** Add short-only project management plan with S0-S6 gates.

### 2026-07-26 — 7 次提交

- `d533f87` **audit** verify short detector v1b pool is causal; full-mode safety is behavioral
- `db062e8` **other** IT-17/18: the short judgment's edge is a fixed-cost artifact, not selection
- `a5a4d2a` **other** IT-19: price the short chain at the executor's real route; correct my IT-18 claim
- `0ae6f5b` **tooling** repo-wide index guard and an analysis report index
- `90e95a1` **hooks** enforce CLAUDE.md / AGENTS.md sync at commit time
- `4a35e09` **S3** keyboard review server for the short tip_v1b 1000-box gold pack
- `3005917` **other** S3 review: add a tip zoom, because the pack PNG is not judgeable

### 2026-07-27 — 23 次提交

- `e06b408` **other** S3 review: lead with a context render showing what came AFTER the box
- `bb271f2` **diag** translate=0.02 does NOT cost tip placement — my hypothesis was wrong
- `0d7e030` **diag** short tip dataset's target is both 10 bars late and gated too strictly
- `1a4b76f` **build** short tip dataset v2 with a corrected target (owner-approved)
- `5e1a9c3` **train** launch short_tip_v2 on the 3060, and fix two things that blocked it
- `c5e45b2` **other** diag+build: v1/v2 had zero negatives, so the detector only learned "box the edge"
- `5f9a830` **diag** the owner's eye is ANTI-correlated with our mechanical dense definition
- `5e5e521` **build** v5 short tip set from the owner's stated pattern and their ⭐标杆 tags
- `8607029` **build** v6 fixes a stem-prefix bug and stops the filter vetoing the owner's stars
- `1b1d1ad` **backtest** honest tip-replay for the short side, priced at the executor's route
- `6b075b6` **backtest** v6 detects the owner's own pattern well and still trades at a coin flip
- `6fc883b` **judgment** the second layer on v6 selects WORSE than taking every candidate
- `2828d57` **diag** v6's confidence does separate — and my "it collapsed" read was unsound
- `b49bba6` **diag** CV density fails as a third normalisation, but volume confirmation works
- `a492812` **factors** fractional differencing works as advertised and separates nothing here
- `4b64028` **backtest** CPCV overturns my "the judgment layer inverts" call
- `b54fe18` **diag** the edge is not decaying — it lives in a mid-volatility band that is stable
- `a403907` **prereg** holdout #9 — mid-volatility band x high confidence, frozen before running
- `36e2250` **other** holdout #9: the frozen mid-volatility configuration does not pass
- `2143ab2` **charts** per-trade renders, and three hypotheses they produced that all died
- `7bc5999` **diag** half of stopped-out trades were once in profit, but cutting early is worse
- `d2fbb65` **build** v7 attacks the three gaps the gold comparison actually measured
- `d5e14e2` **build** v8 fixes box width to 10 bars, after measuring instead of guessing

### 2026-07-28 — 10 次提交

- `061c356` **other** diag+build: v8 learned one exact shape; v9 restores the owner's own box widths
- `2917611` **diag** of three untested ideas only the body-quality filter helps; open questions doc
- `c4a0a79` **build** v9 is the best detector yet — the width detour cost two runs, the rest held
- `fc18ddd` **measure** the barriers are the cost, not the exit tuning
- `88b678b` **measure** the no-barrier edge is directionally stable but decaying
- `8d17b00` **build** rebuild the judgment pool on v9, labelled for both exits at once
- `f13b9c6` **measure** no cheap prefilter keeps v9's candidates, so the rebuild scans every bar
- `fdfe036` **scan** v9 over the last 20h of BTC/ETH at 15m, 5m and 3m, with trade charts
- `a7e8e78` **measure** the exit isn't the problem, and the holdout cannot resolve what is
- `83963b0` **build** append per symbol, so a run that dies leaves a usable pool behind

