# H1 Scaled Exit — Forward Shadow Plan

**Status**: MA206 MVP shadow logger **implemented** (2026-07-10). Manual opt-in only —
not on daily cron or the mainline 0/100 dashboard counter. No holdout. Mainline TP5/SL2 untouched.

**Purpose**: log H1 scaled exit (half bank at 2.5×ATR + half trail 3×ATR) as a
**shadow** parallel to the frozen mainline TP5/SL2 forward log — without
replacing mainline scoring, threshold, dashboard adjudication, or scheduled
jobs until owner approves promotion.

---

## 1. Why shadow (not replace)

| Fact | Implication |
|---|---|
| Frozen mainline is `models/frozen_tp5_sl2_swap_ma206_20260710.*` | `scripts/forward_track.py` + dashboard forward tab + 100-trade PF gate all bind to TP5/SL2 |
| H1 is discovery-grade only | Under MA206, scaled maker PF is 1.096 and maker+H9 PF is 1.230; both remain below 1.3. The former PF 2.825 belongs to the archived 8-55 experiment. |
| Confirmation needs out-of-sample time | Only new forward rows (or a future owner-frozen window) can upgrade H1 |
| Replacing mainline early is a discipline break | Would contaminate the 100-trade TP5/SL2 verdict with a different exit economics |

Shadow = **second paper book** that shares the same candidate universe and
calendar, writes to a **different log file**, and is **never** mixed into the
mainline 0/100 counter unless owner explicitly redefines the gate.

Reference discovery report: `analysis/p15_h1_h2_exit_report.md`.

---

## 2. Non-goals (explicit)

- Do **not** change `DEFAULT_FROZEN_CONFIG` / `DEFAULT_CONFIG_NAME` (`tp5_sl2_swap_ma206`).
- Do **not** overwrite `data/forward_log_ma206.csv` or change its schema semantics.
- Do **not** retrain YOLO, edit `BLOCKED_BASES`, or touch `auto_label.py`.
- Do **not** call `train.py --eval-holdout` or otherwise score holdout.
- Do **not** auto-promote H1 into HANDOFF mainline or daily digest PF.
- Do **not** lower mainline threshold to "speed up" H1 sample size (see
  `docs/FORWARD_ACCELERATION_OPTIONS.md`).

---

## 3. Target architecture (when owner enables)

### 3.1 Artifacts

Mainline (unchanged):

- Model: `models/frozen_tp5_sl2_swap_ma206_20260710.txt`
- Meta: `models/frozen_tp5_sl2_swap_ma206_20260710.json`
- Log: `data/forward_log_ma206.csv`
- Exit resolver: fixed TP5 / SL2 (`src/judgment/forward_scan.resolve_forward_exit`)

Shadow (new, opt-in):

| Piece | Proposed path / contract |
|---|---|
| Config name | `scaled_25_t3_swap_ma206` (or `h1_scaled_swap_ma206`) — must not collide with mainline glob `frozen_tp5_sl2_swap_ma206_*` |
| Model + meta | `models/frozen_<config>_<YYYYMMDD>.txt/.json` via the same freeze pipeline as mainline (`src.judgment.frozen.train_frozen_artifact`) |
| Training labels | `label_candidate_scaled` / barrier registry `scaled` with `tp1=2.5`, `trail=3.0`, `sl=2.0`, `horizon=72` on SWAP expanded candidates |
| Dataset | Dedicated CSV under e.g. `data/ma206/swap_scaled_25_t3_ma206.csv` only after SHA + freeze identity are recorded |
| Log | **`data/forward_log_h1_scaled_ma206.csv`** (append/idempotent same rules as mainline) |
| Exit resolver | Port of `label_candidate_scaled` into a `resolve_forward_exit_scaled` (or exit-plugin dispatch) — half/half `realized_ret`, outcomes like `sl` / `scaled` / `scaled_timeout` / `timeout` |
| CLI | `scripts/forward_track_h1_shadow.py`, which only writes the MA206 shadow path |

### 3.2 What must match mainline (single-variable discipline)

Keep identical unless a separate experiment is registered:

- Universe: OKX `*_USDT_SWAP`, 15m, expanded candidate mask
- Features: current `FEATURE_COLUMNS` (no new features in the first shadow freeze)
- Score quantile for threshold: val **q90** (same selection rule as mainline freeze)
- Forward start: same `FORWARD_START` (default `2026-07-10 10:30 UTC`) unless owner
  opens a **named** shadow-only backfill window (must not redefine mainline start)
- Signal key: `(source, symbol, signal_time)` — see
  `docs/learnings/forward-logs-need-stable-signal-keys.md`
- Maker fill proxy: same as mainline (`entry bar low < open` for longs)
- No holdout in freeze threshold selection

### 3.3 What intentionally differs (the single variable)

| Dimension | Mainline | H1 shadow |
|---|---|---|
| Label / exit | Fixed TP5 / SL2 | Scaled 2.5 bank + 3 trail, hard SL2 until TP1 |
| Booster | Trained on TP5/SL2 labels | Current MVP reuses the same frozen entry booster; a scaled-label booster is only a future owner-approved experiment |
| Threshold value | Mainline val q90 (`0.340933`) | Current MVP uses the same `0.340933` entry threshold |
| Log path | `data/forward_log_ma206.csv` | `data/forward_log_h1_scaled_ma206.csv` |
| Adjudication gate | 100 maker-filled closed → PF | Separate counter; no auto merge |

### 3.4 Incomplete legacy artifact (do not wire as-is)

`models/frozen_scaled_25_t3_2026-07-09.json` is a **lightweight stub** (short
`pool_sha256`, `features` key instead of freeze `feature_columns`, no
`dataset_path` / full SHA). `src.judgment.frozen.load_artifact` will not accept
it. Before shadow runs:

1. Build a proper labeled SWAP dataset for scaled exits (or pin the existing
   sweep CSV with full file SHA).
2. Run freeze through `train_frozen_artifact` so meta matches mainline schema
   (`artifact_version`, `dataset_sha256`, `feature_columns`, splits, holdout policy string).
3. Commit model+json under `models/` only after owner review if treating as a
   promotion candidate; research-only freezes may stay local until then.

---

## 4. Implementation sketch (owner-enabled, ordered)

1. **Freeze H1 properly**  
   - Single variable: exit = scaled.  
   - Train on train only; threshold = val q90; holdout untouched.  
   - Record val fingerprint (n_train / n_val / SHA) in meta.

2. **Exit resolution for open rows**  
   - Mirror `resolve_forward_exit` structure but call scaled barrier math.  
   - While horizon incomplete → `status=open`.  
   - On close → write `outcome`, `label`, `exit_offset`, `exit_time`, `realized_ret`.  
   - Intra-bar ordering must stay conservative (stop before target), matching
     `label_candidate_scaled` docstring.

3. **Parallel log merge**  
   - Reuse `merge_forward_log` / open-key update rules.  
   - New keys append once; existing open keys only get outcome columns on close;
     never rewrite `detected_at` / `model_path` / `dataset_sha256`.

4. **Scheduler (optional, separate from mainline)**  
   - Mainline job stays: `update_okx → forward_track → daily_digest`.  
   - Shadow: either second step writing only to shadow CSV, or a weekly manual
     `PYTHONPATH=. python3 scripts/forward_track.py --config … --out …`.  
   - Digest must **not** silently fold shadow rows into mainline PF.

5. **Dashboard (optional, read-only)**  
   - Extra panel or toggle: “H1 shadow” reading `forward_log_h1_scaled_ma206.csv`.
   - Progress bar labeled **shadow** (e.g. n/100 shadow), not the mainline
     0/100 chip.  
   - Clear copy: “discovery confirmation log — not mainline adjudication.”

6. **Promotion criteria (owner decision later)**  
   Suggested, not automatic:
   - Shadow has ≥100 maker-filled closed rows in the same formal window, **or**
     a pre-registered smaller N with lower confidence.
   - Shadow PF / net@maker+funding beats mainline on the **same calendar** with
     honest cost routing.
   - Only then: freeze becomes default, HANDOFF mainline updates, mainline log
     may start a **new** series (do not retcon old TP5/SL2 rows as scaled).

---

## 5. Logging fields (proposed parity)

Reuse `FORWARD_COLUMNS` for merge compatibility. Optional shadow-only columns
(if added later, keep mainline CSV free of them):

| Extra column | Why |
|---|---|
| `exit_family` | `"tp5_sl2"` vs `"scaled_25_t3"` for joined analytics |
| `tp1_filled` | Whether half bank occurred |
| `ret_half_bank` / `ret_trail` | Decompose scaled `realized_ret` for execution QA |

First ship can omit extras; `outcome` + blended `realized_ret` is enough to
compare PF.

---

## 6. Risks & honesty

- **Different model, different scores**: shadow signals are **not** a subset of
  mainline threshold crossings. Overlap analysis is optional research, not a
  gate.
- **Val optimism**: H1 PF 2.8 on val is selection-biased; forward may collapse
  toward mainline thin edge (+few bp after real funding).
- **Execution fiction**: scaled needs real half-size reduce + trail; OHLC
  paper log overstates fill quality the same way mainline does.
- **Sample starvation**: shadow starts empty; accelerating via lower threshold
  or earlier start is a **separate** policy choice (see acceleration options
  doc) and must be labeled if used.
- **Double counting**: never sum mainline + shadow PnL as “portfolio alpha”
  without an explicit portfolio sim (capacity / same-symbol lock).

---

## 7. Enable checklist (owner)

- [ ] Approve freeze of H1 on SWAP expanded with scaled labels (no holdout).
- [ ] Approve shadow log path and that mainline 100-trade gate stays TP5/SL2.
- [ ] Approve whether shadow joins daily cron or stays manual.
- [ ] Approve any dashboard surface (or CLI-only for first weeks).
- [ ] After N closed shadow trades: owner-only promotion discussion.

Until then: mainline forward continues as today; this document is the package
for a later implementer.

---

## 8. MVP implemented — how to run (shadow only)

### What shipped

| Piece | Path / behavior |
|---|---|
| CLI | `scripts/forward_track_h1_shadow.py` |
| Library | `run_forward_tracking_h1_shadow` / `resolve_forward_exit_scaled` in `src/judgment/forward*.py` |
| Log | **`data/forward_log_h1_scaled_ma206.csv`** only (refuses mainline `data/forward_log_ma206.csv`) |
| Entries | Same as mainline: `frozen_tp5_sl2_swap_ma206_*` scores + val-q90 threshold + expanded SWAP candidates |
| Exits | Scaled math (half @ 2.5×ATR + trail 3×ATR, hard SL2 until TP1) — outcomes `sl` / `scaled` / `scaled_timeout` / `timeout` |
| Tests | `tests/test_h1_scaled_shadow.py` (synthetic OHLC; no live data required) |

### Scaled freeze stub (honest)

`models/frozen_scaled_25_t3_2026-07-09.json` is a **lightweight stub** (`features` instead of
`feature_columns`, short `pool_sha256`, no `dataset_path`). `load_artifact` will not accept it.

**MVP choice**: do **not** load that stub. Score entries with the **mainline freeze** and label
exits with `resolve_forward_exit_scaled` (parity with `label_candidate_scaled`). This is a
**single-variable exit** shadow relative to mainline signals — not a separately trained
scaled-label booster. A proper `train_frozen_artifact` freeze on scaled labels remains an
owner-approved step before promotion discussion.

### Commands

```bash
# from repo root; needs OKX 15m SWAP under data/kline_fetched/ for a real scan
PYTHONPATH=. python3 scripts/forward_track_h1_shadow.py

# optional: custom start (does not change mainline FORWARD_START default when omitted)
PYTHONPATH=. python3 scripts/forward_track_h1_shadow.py --start "2026-07-10 10:30:00+00:00"

# optional: non-default side log (still cannot be data/forward_log_ma206.csv)
PYTHONPATH=. python3 scripts/forward_track_h1_shadow.py --out data/forward_log_h1_scaled_ma206.csv

# unit tests (no network)
PYTHONPATH=. python3 -m pytest tests/test_h1_scaled_shadow.py tests/test_forward_tracking.py -q
```

Mainline remains:

```bash
PYTHONPATH=. python3 scripts/forward_track.py   # → data/forward_log_ma206.csv only
```

### Non-goals still in force

- No cron wiring for shadow.
- No digest / dashboard merge into the mainline 0/100 PF gate.
- No holdout evaluation.
- No auto-promotion of H1.
