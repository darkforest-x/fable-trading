# Forward Sample Acceleration Options

> **2026-07-20 状态横幅（请先读）**  
> - 主线已切 **YOLO v11 池 + 实盘执行**；前向时钟曾于 **07-19 清空重启**（`FORWARD_START≈2026-07-18`）。  
> - **默认仍是 stay**：不靠降 q90 / 掺事后检出来「加速 N」。  
> - 真正阻塞 N 的是 **tip 检出率**（v11≈0.9%）→ 靠 **H-TIP v12**，不是本文件里的阈值魔术。  
> - 实时 tip 路径、新鲜度 30min、脉冲预算：见 `HANDOFF.md` 顶部与 `CLAUDE.md` 实盘纪律。  
> 下文保留 07-10 决策备忘，数字以当时为准。

**Status (historical)**: decision aid (2026-07-10 overnight). **Default recommendation: stay.**

**Context (historical)**: stage-3 confirmation gated on forward paper trades from
frozen TP5/SL2 SWAP (`FORWARD_START = 2026-07-08 UTC` at the time). What is scarce
is calendar time and **fresh tip rate**, not code.

As of 2026-07-10 packaging: formal-window log had ~2 closed rows. Path to ~100
maker-filled closed trades is measured in weeks (still true under v11 tip scarcity).

---

## 1. What “faster N” is allowed to mean

| Allowed | Not allowed |
|---|---|
| Log more **paper** rows for monitoring / shadow experiments | Re-open holdout or tune on ≥2026-05-04 for “more trades” |
| Explicit secondary logs with different rules | Silently change mainline threshold / start and keep calling it the same 100-trade gate |
| Honest risk labels on diluted samples | Claim accelerated N has the same statistical meaning as default N |
| Owner-approved redefinition of the gate | Agent unilateral promotion of a faster rule to HANDOFF |

---

## 2. Options (honest)

### Option A — Stay default (recommended)

| Knob | Value |
|---|---|
| Model | `frozen_tp5_sl2_swap_20260709` |
| Threshold | val q90 (~0.3747) |
| Start | 2026-07-08 00:00 UTC |
| Universe | OKX SWAP 15m expanded candidates |
| Exit | TP5 / SL2 |
| Log | `data/forward_log.csv` only |

**Pros**

- Single pre-registered gate; no post-hoc sample-size shopping.
- Matches freeze metadata and dashboard 0/100 semantics.
- Avoids confusing “more trades” with “better edge.”

**Cons**

- Slow: at ~1 signal/day order of magnitude, 100 closed trades is multi-week
  (horizon 72 bars ≈ 18h also delays closes).
- Funding / regime shifts during a long wait are real but unavoidable for
  true confirmation.

**Use when**: mainline adjudication (default forever unless owner rewrites the gate).

---

### Option B — Lower threshold, **logging only**

Lower the score cutoff for **extra** rows (e.g. log q80 or q85 in a side file)
while **keeping** q90 as the adjudication set.

| Variant | Behavior |
|---|---|
| B1 dual-threshold same log | Extra column `band` = `adjudication` \| `monitor`; only adjudication rows count for 100-trade PF |
| B2 dual log | Mainline log unchanged; `data/forward_log_monitor_q80.csv` for looser scores |

**Pros**

- More rows for ops (fill rates, symbol mix, data freshness) without changing
  the official gate definition if B1/B2 discipline holds.
- Can later study threshold sensitivity on forward data (still confirmation-
  grade only if threshold was fixed *before* looking at outcomes).

**Cons / risks**

- **Dilution**: lower scores are where val edge was weakest; monitor PF will look
  worse and invite “raise threshold” hacking.
- **Gate contamination**: one accidental filter flip on the dashboard and the
  100-trade PF is no longer the pre-registered experiment.
- **Work inflation**: exit resolution cost scales with open rows; more false
  opens increase merge noise.
- **Not free N**: q80 trades are a **different** population; you cannot pool
  them with q90 to claim “100 trades faster.”

**If owner wants B**: hard-code adjudication threshold in one place; UI must
default to adjudication-only; document the monitor threshold in freeze meta or
a config constant with a comment “not the 100-trade gate.”

---

### Option C — Multi-window / earlier start (backfill)

Run `forward_track --start 2026-07-01` (or earlier) to backfill threshold
signals on already-fetched bars.

**Pros**

- Quickly exercises merge/idempotency and fills closed outcomes for engineering
  smoke (already done once for `--start 2026-07-01` → ~19 closed in history).
- Useful for shadow books that owner labels as “unofficial practice log.”

**Cons / risks**

- **Not pure forward** relative to freeze date / formal window. Using backfill
  rows for the 100-trade PF reintroduces look-ahead of “we already lived those
  days” and blurs discovery vs confirmation.
- Overlap with data used for other research (even if not holdout) still burns
  narrative cleanliness.
- Multiple starts without naming → unreproducible “which N?” debates.

**If owner wants C**: keep formal adjudication start fixed; store backfill in a
**separate file** (`forward_log_backfill_20260701.csv`) never mixed into the
main counter.

---

### Option D — Multi-window in the sense of **parallel books**

Run several pre-registered streams in parallel (not sequential threshold shopping):

| Stream | Role |
|---|---|
| Mainline TP5/SL2 q90 from 2026-07-08 | Sole 100-trade gate |
| H1 scaled shadow | Exit challenger (see `docs/H1_SCALED_FORWARD_SHADOW_PLAN.md`) |
| Optional H9-filtered mainline | Filter challenger |
| Optional short side | Direction expansion (H10) |

**Pros**

- Calendar time is shared; several hypotheses mature together without
  reopening val.
- Matches research agenda “confirmation only on forward.”

**Cons**

- Implementation and UI complexity; risk of owner/dashboard confusion.
- Multiple freezes must each be honest (no holdout, fixed threshold).

**Use when**: owner wants challengers without accelerating the mainline N
definition.

---

### Option E — Broader universe / more symbols

After SWAP expansion + audit, include more liquid SWAP bases in candidate scan
(still respecting `BLOCKED_BASES`).

**Pros**

- More natural threshold crossings without lowering score bar.
- Aligns with “opportunity surface” growth.

**Cons / risks**

- Distribution shift vs freeze training universe → model miscalibration.
- Thin books inflate maker-fill fiction and funding gaps.
- Changing universe mid-forward splits the sample (must restart or tag eras).

**Use when**: freeze is rebuilt on the expanded universe (owner-approved single
variable), then a **new** forward series starts — do not silently widen the
old series.

---

### Option F — Shorter horizon / different exit to close faster

Reduce `HORIZON_BARS` or use faster-exit labels so open rows close sooner.

**Pros**

- Higher closed-trade velocity per calendar day.

**Cons / risks**

- **Changes the economic object** under test. Mainline edge was selected under
  h72 TP5/SL2; faster exits are a new hypothesis (H1 already covers one).
- Cannot accelerate the *same* gate by changing the outcome definition.

**Verdict**: not an acceleration of mainline N; it is a different experiment
(use shadow, Option D).

---

## 3. Quantitative intuition (order-of-magnitude)

Rough, not a forecast:

| Lever | Effect on time-to-100 (qualitative) | Effect on validity of “mainline PF” |
|---|---|---|
| A default | Baseline (weeks) | Full (pre-registered) |
| B lower threshold into gate | Faster | **Broken** unless gate redefined |
| B lower threshold monitor-only | Same for gate | Gate intact; monitor is soft |
| C earlier start into gate | Faster | **Diluted forward purity** |
| C backfill side file | Same for gate | Gate intact |
| D parallel shadows | Same for gate; more info | Gate intact |
| E more symbols without re-freeze | Maybe faster | **Shift risk** |
| F change exit/horizon | Different experiment | N/A for mainline |

---

## 4. Recommendation

**Default: stay on Option A.**

Reasons:

1. The project already paid for a clean freeze + formal start; the scarce
   resource is patience, not engineering.
2. Edge after real funding is thin; diluting the score bar mostly buys noise
   that will look like failure and tempt further tuning.
3. H1 and other discovery winners should use **parallel shadows** (Option D),
   not a hijack of mainline N.
4. Engineering needs (idempotency, digest, dashboard) are already satisfiable
   with rare formal-window rows plus optional side-file backfills that never
   touch the 0/100 chip.

**Acceptable owner overrides** (document in HANDOFF if chosen):

- **A + D**: enable H1 shadow log; mainline gate unchanged.
- **A + B2**: monitor log at a fixed lower quantile; adjudication stays q90.
- **A + C side file**: engineering backfill only.

**Discouraged without rewriting the experimental contract**:

- Lowering mainline threshold in place.
- Moving `FORWARD_START` earlier and keeping the same PF story.
- Pooling any accelerated rows into the 100-trade mainline count.

---

## 5. One-page decision for morning review

| Question | Answer if default |
|---|---|
| What counts toward 100? | Maker-filled **closed** rows in `data/forward_log.csv`, model = frozen TP5/SL2 SWAP, score ≥ val q90, signal_time ≥ 2026-07-08 UTC |
| Can we get 100 by next week by logging harder? | Only by changing the experiment (not recommended) |
| What should agents do overnight? | Keep running `forward_track`; draft shadow/acceleration **docs**; no threshold edits |
| Next code change that is still “default” | Optional H1 shadow **after** owner enable (separate file) |

---

## 6. Related

- Mainline forward: `scripts/forward_track.py`, `src/judgment/forward*.py`
- Freeze contract: `src/judgment/frozen.py`, `models/frozen_tp5_sl2_swap_20260709.json`
- H1 discovery: `analysis/p15_h1_h2_exit_report.md`, `docs/RESEARCH_AGENDA.md` H1
- Shadow packaging: `docs/H1_SCALED_FORWARD_SHADOW_PLAN.md`
- Stable keys learning: `docs/learnings/forward-logs-need-stable-signal-keys.md`
