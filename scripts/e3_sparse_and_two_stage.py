#!/usr/bin/env python3
"""E3 sparse spread_expand + two-stage tip∩hard-confirm — train-only discovery.

Hard cut: signal open_time < 2026-05-04. Never touches holdout / ACTIVE / live.
E3 and two-stage are attributed SEPARATELY — never pack as one success claim.

1 · E3 sparsification
   Fixed entry family: spread_expand short (dense tip → Δfast_spread(8)≥thr,
   close < cluster_mid). ONLY raise thr / MIN_GAP (or joint) so full-market n
   falls near owner-short magnitude. Threshold pick is calibrated on **n only**
   (not PF) — PF is evaluated after the pick. Exits: baseline TP5/SL2 +
   no_tp_sl2_h144 (+ trail4). Compare vs unsparsed spread.

2 · Two-stage (sparse confirm)
   Stage-1: same dense-emergence tip window as direction_select / launch.
   Stage-2: inside tip..tip+WAIT, first bar satisfying pre-declared hard
   confirm (order_score≤0 ∧ already falling ∧ spread expand ∧ short side).
   NOT a full-market wide R3 clone. Force n ≤ TS_N_CAP via pre-declared
   bump grid on spread thr (count-only) if needed. Short only; same exits.
   Report n, overlap vs owner short, PF, 2026-04.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/e3_sparse_and_two_stage.py --n-symbols 20
  PYTHONPATH=. .venv/bin/python scripts/e3_sparse_and_two_stage.py \\
      --n-symbols 0 --tag e3_sparse_and_two_stage
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]

from scripts.chain_failure_attribution import (  # noqa: E402
    MATCH_WINDOWS,
    _month_key,
    _overlap_stats,
)
from scripts.direction_select_base_rate import (  # noqa: E402
    FAST_MAX,
    FULL_MAX,
    HOLDOUT_START,
    MAX_WAIT_BARS,
    MIN_DENSE_BARS,
    MIN_GAP_BARS,
    SPREAD_CHG8_THR,
    WARMUP,
    _dense_run,
)
from scripts.short_trend_ab import (  # noqa: E402
    EXIT_RESOLVERS,
    _enrich,
    _load_owner_shorts,
    _oracle_cuts_for_symbol,
    _period_blocks,
    _stats,
)
from src.costs import FORWARD_COST, LEGACY_P0_ROUND_TRIP  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402

DEFAULT_SHEET = PROJECT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"
SUCCESS_PF = 1.3
OVERLAP_WINDOW = 18
EXIT_KEYS = ("baseline_tp5_sl2_h72", "no_tp_sl2_h144", "trail4_atr_h144")
PRIMARY_EXIT = "no_tp_sl2_h144"

# ---- E3 pre-declared n band (owner short train ≈1361 boxes / ~1284 cuts) ----
N_TARGET_LO = 1000
N_TARGET_HI = 2500
N_TARGET_AIM = 1500
E3_THR_GRID = (0.00383, 0.0050, 0.0060, 0.0075, 0.0090, 0.0110, 0.0140, 0.0180)
E3_GAP_GRID = (18, 36, 48, 72, 96)

# ---- Two-stage pre-declared hard confirm (NOT full-market R3) ----
# Stage-1 tip = emergence: fast≤0.0028 & full≤0.0055, run first hits 5.
# Stage-2 inside tip+1 .. tip+WAIT: first bar matching ALL of:
TS_WAIT = MAX_WAIT_BARS  # 48
TS_ORDER_MAX = 0.0
TS_RET8_MAX = -0.015  # already falling ≥1.5% over 8 bars
TS_SPREAD_MIN = 0.0060  # harder than base 0.00383 and dead R3 0.004
TS_GAP = 36  # stronger sparse than MIN_GAP=18
TS_N_CAP = 2500
# Count-only bump if pre-declared still too dense (never pick on PF).
TS_SPREAD_BUMP_GRID = (0.0060, 0.0075, 0.0090, 0.0110, 0.0140, 0.0180)

BASE_GAP = MIN_GAP_BARS
BASE_THR = SPREAD_CHG8_THR


def _empty() -> dict[str, list]:
    return {"gross": [], "hold": [], "time": []}


def _append(bag: dict[str, list], *, g: float, h: int, t: pd.Timestamp) -> None:
    bag["gross"].append(g)
    bag["hold"].append(h)
    bag["time"].append(t)


def _pf_block(bag: dict[str, list]) -> dict:
    return {
        "maker": _stats(bag["gross"], bag["hold"], FORWARD_COST),
        "legacy": _stats(bag["gross"], bag["hold"], LEGACY_P0_ROUND_TRIP),
    }


def _month_pf(bag: dict[str, list], month: str = "2026-04") -> dict:
    g = [float(x) for x, t in zip(bag["gross"], bag["time"]) if _month_key(t) == month]
    h = [int(x) for x, t in zip(bag["hold"], bag["time"]) if _month_key(t) == month]
    return _stats(g, h, FORWARD_COST)


def _dedup_idx(idxs: list[int], gap: int) -> list[int]:
    out: list[int] = []
    last = -10**9
    for i in sorted(idxs):
        if i - last < gap:
            continue
        out.append(i)
        last = i
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    keys: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                keys.append(k)
                seen.add(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _iter_okx_train(n_symbols: int):
    n = 0
    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        if is_eval_symbol(symbol):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame.loc[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < WARMUP + 200:
            continue
        yield symbol, frame
        n += 1
        if n_symbols and n >= n_symbols:
            break


def _settle_short(
    enriched: pd.DataFrame, i: int, exit_name: str
) -> tuple[float, int] | None:
    out = EXIT_RESOLVERS[exit_name](enriched, i, -1)
    if out is None:
        return None
    return float(out.realized_ret), int(out.exit_offset)


def _collect_symbol_events(
    enriched: pd.DataFrame,
) -> dict:
    """Per-symbol fire indices; tip-arm semantics match ``collect_signals``.

    New dense tip (run==MIN_DENSE_BARS) re-arms and resets the wait window —
    same as direction_select ``spread_expand_chg8``. E3 = first expand≥thr with
    close<cluster_mid (short only). Two-stage = first hard-confirm bar in the
    same armed window (order≤0 ∧ ret8≤thr ∧ expand≥thr ∧ short).
    """
    n = len(enriched)
    close = enriched["close"].to_numpy(dtype=float)
    fast = pd.to_numeric(enriched["fast_spread"], errors="coerce").to_numpy()
    full = pd.to_numeric(enriched["full_spread"], errors="coerce").to_numpy()
    order = enriched["order_score"].to_numpy(dtype=float)
    cluster_mid = (
        enriched["cluster_max"].to_numpy(dtype=float)
        + enriched["cluster_min"].to_numpy(dtype=float)
    ) / 2.0
    run = _dense_run(fast, full)
    ret8 = np.full(n, np.nan)
    ret8[8:] = close[8:] / close[:-8] - 1.0

    tips: list[int] = []
    expand_by_thr: dict[float, list[int]] = {thr: [] for thr in E3_THR_GRID}
    twostage_by_thr: dict[float, list[int]] = {
        thr: [] for thr in TS_SPREAD_BUMP_GRID
    }

    armed_from: Optional[int] = None
    fired_e3: dict[float, bool] = {}
    fired_ts: dict[float, bool] = {}

    for i in range(WARMUP, n - 1):
        if run[i] == MIN_DENSE_BARS:
            tips.append(i)
            armed_from = i
            fired_e3 = {thr: False for thr in E3_THR_GRID}
            fired_ts = {thr: False for thr in TS_SPREAD_BUMP_GRID}

        if armed_from is None:
            continue
        if i - armed_from > TS_WAIT:
            armed_from = None
            continue
        if i <= armed_from or i < 8:
            continue

        chg8 = float(fast[i] - fast[i - 8])
        if not np.isfinite(chg8):
            continue
        mid = float(cluster_mid[i])
        if not (np.isfinite(mid) and mid > 0):
            continue
        is_short = bool(close[i] < mid)

        # E3 family: expand thr; only keep shorts (long expand still consumes
        # the arm's first-hit for that thr — matches collect_signals fired flag).
        for thr in E3_THR_GRID:
            if fired_e3[thr] or chg8 < thr:
                continue
            fired_e3[thr] = True
            if is_short:
                expand_by_thr[thr].append(i)

        # Two-stage hard confirm (short only; long does not consume — confirm
        # is directional by construction).
        if is_short:
            os_ = float(order[i])
            r8 = float(ret8[i])
            if (
                np.isfinite(os_)
                and os_ <= TS_ORDER_MAX
                and np.isfinite(r8)
                and r8 <= TS_RET8_MAX
            ):
                for thr in TS_SPREAD_BUMP_GRID:
                    if fired_ts[thr] or chg8 < thr:
                        continue
                    fired_ts[thr] = True
                    twostage_by_thr[thr].append(i)

        if all(fired_e3.values()) and all(fired_ts.values()):
            armed_from = None

    return {
        "tips": tips,
        "expand_by_thr": expand_by_thr,
        "twostage_by_thr": twostage_by_thr,
    }


def _pick_e3_config(count_rows: list[dict]) -> dict:
    """Pick thr/gap for n∈[LO,HI] closest to AIM; prefer thr-only then gap-only."""
    in_band = [
        r
        for r in count_rows
        if N_TARGET_LO <= r["n_fires"] <= N_TARGET_HI
    ]
    if not in_band:
        # fallback: closest to AIM regardless of band
        best = min(count_rows, key=lambda r: abs(r["n_fires"] - N_TARGET_AIM))
        best = dict(best)
        best["pick_reason"] = "fallback_closest_to_aim_outside_band"
        best["in_band"] = False
        return best

    thr_only = [
        r for r in in_band if r["gap"] == BASE_GAP and r["thr"] > BASE_THR + 1e-12
    ]
    gap_only = [
        r for r in in_band if abs(r["thr"] - BASE_THR) < 1e-12 and r["gap"] > BASE_GAP
    ]
    joint = [
        r
        for r in in_band
        if r["thr"] > BASE_THR + 1e-12 and r["gap"] > BASE_GAP
    ]

    def closest(rows: list[dict], reason: str) -> dict:
        best = min(rows, key=lambda r: abs(r["n_fires"] - N_TARGET_AIM))
        out = dict(best)
        out["pick_reason"] = reason
        out["in_band"] = True
        return out

    if thr_only:
        return closest(thr_only, "thr_only_in_band")
    if gap_only:
        return closest(gap_only, "gap_only_in_band")
    if joint:
        return closest(joint, "joint_thr_gap_in_band")
    # unsparsed somehow in band (shouldn't) — take closest
    return closest(in_band, "any_in_band")


def _pick_twostage_thr(count_by_thr: dict[float, int]) -> dict:
    """Pre-declared TS_SPREAD_MIN; bump on count-only if n > cap."""
    n0 = int(count_by_thr.get(TS_SPREAD_MIN, 0))
    if n0 <= TS_N_CAP:
        # Prefer staying at pre-declared if also ≥ LO; else bump down not allowed —
        # if too sparse (<LO) keep pre-declared and report under-band.
        return {
            "thr": TS_SPREAD_MIN,
            "n_fires": n0,
            "bumped": False,
            "in_band": N_TARGET_LO <= n0 <= N_TARGET_HI,
            "pick_reason": (
                "predeclared_under_cap"
                if n0 <= TS_N_CAP
                else "predeclared"
            ),
        }
    # Raise thr until n ≤ cap; among those ≤cap prefer in-band closest to AIM
    candidates = []
    for thr in TS_SPREAD_BUMP_GRID:
        n = int(count_by_thr.get(thr, 0))
        if n <= TS_N_CAP:
            candidates.append({"thr": thr, "n_fires": n})
    if not candidates:
        # even hardest still over cap — take max thr
        thr = TS_SPREAD_BUMP_GRID[-1]
        return {
            "thr": thr,
            "n_fires": int(count_by_thr.get(thr, 0)),
            "bumped": True,
            "in_band": False,
            "pick_reason": "bump_exhausted_still_over_cap",
        }
    in_band = [
        c for c in candidates if N_TARGET_LO <= c["n_fires"] <= N_TARGET_HI
    ]
    pool = in_band or candidates
    best = min(pool, key=lambda r: abs(r["n_fires"] - N_TARGET_AIM))
    return {
        "thr": best["thr"],
        "n_fires": best["n_fires"],
        "bumped": abs(best["thr"] - TS_SPREAD_MIN) > 1e-12,
        "in_band": N_TARGET_LO <= best["n_fires"] <= N_TARGET_HI,
        "pick_reason": "count_only_bump_to_cap",
    }


def _pooled_overlap(
    owner: list[tuple[str, int]], rule: list[tuple[str, int]], window: int
) -> dict:
    ow: dict[str, list[int]] = defaultdict(list)
    ru: dict[str, list[int]] = defaultdict(list)
    for s, i in owner:
        ow[s].append(i)
    for s, i in rule:
        ru[s].append(i)
    tot_o = tot_r = hit_o = hit_r = 0
    for s in set(ow) | set(ru):
        st = _overlap_stats(ow.get(s, []), ru.get(s, []), window)
        tot_o += st["n_owner"]
        tot_r += st["n_rule"]
        hit_o += st["owner_hit"]
        hit_r += st["rule_hit"]
    inter = min(hit_o, hit_r)
    union = tot_o + tot_r - inter
    return {
        "window": window,
        "n_owner": tot_o,
        "n_rule": tot_r,
        "owner_hit": hit_o,
        "rule_hit": hit_r,
        "owner_recall": round(hit_o / tot_o, 4) if tot_o else None,
        "rule_precision_vs_owner": round(hit_r / tot_r, 4) if tot_r else None,
        "jaccard_approx": round(inter / union, 4) if union else None,
    }


def run(*, n_symbols: int, tag: str, sheet: Path) -> int:
    t0 = time.time()
    out_dir = PROJECT / "analysis" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    owner_all = _load_owner_shorts(sheet)
    owner_by_sym: dict[str, list[dict]] = defaultdict(list)
    for rec in owner_all.to_dict(orient="records"):
        owner_by_sym[str(rec["symbol"])].append(rec)

    print(
        f"E3+two-stage  n_owner_short={len(owner_all)}  "
        f"n_symbols_cap={n_symbols or 'all'}  holdout=FORBIDDEN",
        flush=True,
    )
    print(
        f"  E3 n-band=[{N_TARGET_LO},{N_TARGET_HI}] aim={N_TARGET_AIM}  "
        f"base thr={BASE_THR} gap={BASE_GAP}",
        flush=True,
    )
    print(
        f"  TS confirm: order≤{TS_ORDER_MAX} ret8≤{TS_RET8_MAX} "
        f"spread≥{TS_SPREAD_MIN} gap={TS_GAP} n_cap={TS_N_CAP}",
        flush=True,
    )

    # Pass 1: collect fire indices per config (no settlement yet)
    # e3_raw[(thr,gap)] -> list[(sym,i)]
    e3_raw: dict[tuple[float, int], list[tuple[str, int]]] = {
        (thr, gap): [] for thr in E3_THR_GRID for gap in E3_GAP_GRID
    }
    ts_raw: dict[float, list[tuple[str, int]]] = {
        thr: [] for thr in TS_SPREAD_BUMP_GRID
    }
    pooled_owner: list[tuple[str, int]] = []
    # Keep enriched frames? Too heavy. Re-enrich on settle pass for selected only.
    # Store per-symbol events for settle without full re-scan of tip logic:
    # symbol -> {expand_by_thr, twostage_by_thr, enriched needed for settle}
    # Memory: 233 frames * ~30k bars — too big. Better: two-pass scan.
    # Pass1 count+indices only; Pass2 re-scan and settle selected configs.

    n_sym = 0
    t_min = t_max = None
    n_tips = 0
    print("=== pass1: count fires (n-calibration) ===", flush=True)
    for symbol, frame in _iter_okx_train(n_symbols):
        enriched = _enrich(frame)
        t = pd.to_datetime(enriched["open_time"], utc=True)
        owner_cuts = _oracle_cuts_for_symbol(enriched, owner_by_sym.get(symbol, []))
        for oi in owner_cuts:
            pooled_owner.append((symbol, oi))

        ev = _collect_symbol_events(enriched)
        n_tips += len(ev["tips"])

        for thr, idxs in ev["expand_by_thr"].items():
            for gap in E3_GAP_GRID:
                ded = _dedup_idx(idxs, gap)
                for i in ded:
                    e3_raw[(thr, gap)].append((symbol, i))
                    ti = t.iloc[i]
                    if t_min is None or ti < t_min:
                        t_min = ti
                    if t_max is None or ti > t_max:
                        t_max = ti

        for thr, idxs in ev["twostage_by_thr"].items():
            ded = _dedup_idx(idxs, TS_GAP)
            for i in ded:
                ts_raw[thr].append((symbol, i))

        n_sym += 1
        if n_sym % 20 == 0:
            print(f"  pass1 scanned {n_sym} …", flush=True)

    # Count tables
    e3_count_rows = []
    for thr in E3_THR_GRID:
        for gap in E3_GAP_GRID:
            n = len(e3_raw[(thr, gap)])
            e3_count_rows.append(
                {
                    "panel": "E3_count",
                    "thr": thr,
                    "gap": gap,
                    "n_fires": n,
                    "is_base": abs(thr - BASE_THR) < 1e-12 and gap == BASE_GAP,
                    "lever": (
                        "base"
                        if abs(thr - BASE_THR) < 1e-12 and gap == BASE_GAP
                        else "thr_only"
                        if gap == BASE_GAP and thr > BASE_THR + 1e-12
                        else "gap_only"
                        if abs(thr - BASE_THR) < 1e-12 and gap > BASE_GAP
                        else "joint"
                    ),
                    "in_band": N_TARGET_LO <= n <= N_TARGET_HI,
                }
            )

    e3_pick = _pick_e3_config(e3_count_rows)
    ts_count = {thr: len(ts_raw[thr]) for thr in TS_SPREAD_BUMP_GRID}
    ts_pick = _pick_twostage_thr(ts_count)

    print(
        f"  E3 pick: thr={e3_pick['thr']} gap={e3_pick['gap']} "
        f"n={e3_pick['n_fires']} reason={e3_pick['pick_reason']}",
        flush=True,
    )
    print(
        f"  TS pick: thr={ts_pick['thr']} n={ts_pick['n_fires']} "
        f"bumped={ts_pick['bumped']} reason={ts_pick['pick_reason']}",
        flush=True,
    )

    # Configs to settle
    settle_e3: list[tuple[str, float, int]] = [
        ("spread_base", BASE_THR, BASE_GAP),
        ("spread_sparse", float(e3_pick["thr"]), int(e3_pick["gap"])),
    ]
    # Sensitivity: 1–2 nearby in-band thr-only if different from primary
    extras = [
        r
        for r in e3_count_rows
        if r["in_band"]
        and r["lever"] == "thr_only"
        and not (
            abs(r["thr"] - e3_pick["thr"]) < 1e-12 and r["gap"] == e3_pick["gap"]
        )
    ]
    extras = sorted(extras, key=lambda r: abs(r["n_fires"] - N_TARGET_AIM))[:2]
    for j, r in enumerate(extras):
        settle_e3.append((f"spread_sparse_sens{j+1}", float(r["thr"]), int(r["gap"])))

    settle_ts_thr = float(ts_pick["thr"])
    settle_keys = [c[0] for c in settle_e3] + [
        "two_stage",
        "two_stage_predeclared",
    ]

    bags: dict[str, dict[str, dict[str, list]]] = {
        k: {x: _empty() for x in EXIT_KEYS} for k in settle_keys
    }
    pooled_rule: dict[str, list[tuple[str, int]]] = {k: [] for k in settle_keys}

    for name, thr, gap in settle_e3:
        pooled_rule[name] = list(e3_raw[(thr, gap)])
    pooled_rule["two_stage"] = list(ts_raw[settle_ts_thr])
    pooled_rule["two_stage_predeclared"] = list(ts_raw[TS_SPREAD_MIN])

    print("=== pass2: settle selected configs ===", flush=True)
    n_sym2 = 0
    for symbol, frame in _iter_okx_train(n_symbols):
        enriched = _enrich(frame)
        t = pd.to_datetime(enriched["open_time"], utc=True)
        # Gather indices needed for this symbol
        need: set[int] = set()
        name_by_idx: dict[int, list[str]] = defaultdict(list)
        for name, thr, gap in settle_e3:
            for s, i in e3_raw[(thr, gap)]:
                if s == symbol:
                    need.add(i)
                    name_by_idx[i].append(name)
        for s, i in ts_raw[settle_ts_thr]:
            if s == symbol:
                need.add(i)
                name_by_idx[i].append("two_stage")
        if abs(settle_ts_thr - TS_SPREAD_MIN) > 1e-12:
            for s, i in ts_raw[TS_SPREAD_MIN]:
                if s == symbol:
                    need.add(i)
                    name_by_idx[i].append("two_stage_predeclared")
        else:
            # same fires — mirror name
            for i in list(need):
                if "two_stage" in name_by_idx[i]:
                    name_by_idx[i].append("two_stage_predeclared")

        for i in sorted(need):
            ti = t.iloc[i]
            for ename in EXIT_KEYS:
                settled = _settle_short(enriched, i, ename)
                if settled is None:
                    continue
                g, h = settled
                for cfg in name_by_idx[i]:
                    _append(bags[cfg][ename], g=g, h=h, t=ti)

        n_sym2 += 1
        if n_sym2 % 20 == 0:
            print(f"  pass2 scanned {n_sym2} …", flush=True)

    # ---- Overlap ----
    overlap_rows = []
    for name in settle_keys:
        for w in MATCH_WINDOWS:
            row = _pooled_overlap(pooled_owner, pooled_rule[name], w)
            row["entry"] = name
            overlap_rows.append(row)
    overlap_h = {
        name: next(
            r for r in overlap_rows if r["entry"] == name and r["window"] == OVERLAP_WINDOW
        )
        for name in settle_keys
    }

    # ---- Main rows ----
    e3_main = []
    for name, thr, gap in settle_e3:
        ov = overlap_h[name]
        for ename in EXIT_KEYS:
            blk = _pf_block(bags[name][ename])
            m04 = _month_pf(bags[name][ename], "2026-04")
            months = _period_blocks(
                bags[name][ename]["time"],
                bags[name][ename]["gross"],
                bags[name][ename]["hold"],
                FORWARD_COST,
                freq="M",
            )
            e3_main.append(
                {
                    "panel": "E3",
                    "entry": name,
                    "thr": thr,
                    "gap": gap,
                    "exit": ename,
                    "n": blk["maker"]["n"],
                    "pf_maker": blk["maker"]["profit_factor"],
                    "sum_net_maker": blk["maker"]["sum_net"],
                    "win_rate": blk["maker"]["win_rate"],
                    "pf_legacy": blk["legacy"]["profit_factor"],
                    "pf_2026_04": m04.get("profit_factor"),
                    "n_2026_04": m04.get("n"),
                    "sum_net_2026_04": m04.get("sum_net"),
                    "owner_recall_w18": ov["owner_recall"],
                    "rule_precision_w18": ov["rule_precision_vs_owner"],
                    "jaccard_w18": ov["jaccard_approx"],
                    "ge_1_3": (
                        blk["maker"]["profit_factor"] is not None
                        and blk["maker"]["profit_factor"] >= SUCCESS_PF
                    ),
                    "apr_not_collapse": (
                        m04.get("profit_factor") is not None
                        and m04["profit_factor"] >= 1.0
                    ),
                    "n_months": len(months),
                }
            )

    ts_main = []
    for ts_name, ts_thr_used in (
        ("two_stage", settle_ts_thr),
        ("two_stage_predeclared", TS_SPREAD_MIN),
    ):
        ov = overlap_h[ts_name]
        for ename in EXIT_KEYS:
            blk = _pf_block(bags[ts_name][ename])
            m04 = _month_pf(bags[ts_name][ename], "2026-04")
            ts_main.append(
                {
                    "panel": "two_stage",
                    "entry": ts_name,
                    "thr_spread": ts_thr_used,
                    "gap": TS_GAP,
                    "order_max": TS_ORDER_MAX,
                    "ret8_max": TS_RET8_MAX,
                    "exit": ename,
                    "n": blk["maker"]["n"],
                    "pf_maker": blk["maker"]["profit_factor"],
                    "sum_net_maker": blk["maker"]["sum_net"],
                    "win_rate": blk["maker"]["win_rate"],
                    "pf_legacy": blk["legacy"]["profit_factor"],
                    "pf_2026_04": m04.get("profit_factor"),
                    "n_2026_04": m04.get("n"),
                    "sum_net_2026_04": m04.get("sum_net"),
                    "owner_recall_w18": ov["owner_recall"],
                    "rule_precision_w18": ov["rule_precision_vs_owner"],
                    "jaccard_w18": ov["jaccard_approx"],
                    "ge_1_3": (
                        blk["maker"]["profit_factor"] is not None
                        and blk["maker"]["profit_factor"] >= SUCCESS_PF
                    ),
                    "apr_not_collapse": (
                        m04.get("profit_factor") is not None
                        and m04["profit_factor"] >= 1.0
                    ),
                    "is_primary": ts_name == "two_stage",
                }
            )

    # Monthly detail for primary E3 sparse + base + two_stage under no_tp
    period_rows = []
    for name in ("spread_base", "spread_sparse", "two_stage", "two_stage_predeclared"):
        bag = bags[name][PRIMARY_EXIT]
        months = _period_blocks(
            bag["time"], bag["gross"], bag["hold"], FORWARD_COST, freq="M"
        )
        for mk, st in months.items():
            period_rows.append(
                {
                    "entry": name,
                    "exit": PRIMARY_EXIT,
                    "period": mk,
                    "n": st["n"],
                    "pf_maker": st["profit_factor"],
                    "sum_net_maker": st["sum_net"],
                    "win_rate": st["win_rate"],
                }
            )

    # Delta vs base for sparse
    def _ntp_row(rows: list[dict], entry: str) -> dict | None:
        for r in rows:
            if r["entry"] == entry and r["exit"] == PRIMARY_EXIT:
                return r
        return None

    base_ntp = _ntp_row(e3_main, "spread_base")
    sparse_ntp = _ntp_row(e3_main, "spread_sparse")
    ts_ntp = _ntp_row(ts_main, "two_stage")
    ts_pre_ntp = _ntp_row(ts_main, "two_stage_predeclared")
    ov = overlap_h["two_stage"]
    ov_pre = overlap_h["two_stage_predeclared"]

    # Verdicts (discovery-level; separate)
    e3_ok = bool(
        sparse_ntp
        and sparse_ntp.get("ge_1_3")
        and sparse_ntp.get("apr_not_collapse")
        and e3_pick.get("in_band")
    )
    e3_apr = sparse_ntp.get("pf_2026_04") if sparse_ntp else None
    e3_apr_collapse = e3_apr is not None and e3_apr < 0.85

    # Two-stage success: n controllable + (PF≥1.3 OR clear Δ vs spread) AND
    # Jaccard/precision clearly better than E1 wide R3 (J≈0.018, prec≈0.022)
    E1_R3_JACCARD = 0.018
    E1_R3_PREC = 0.022
    ts_j = ov.get("jaccard_approx")
    ts_p = ov.get("rule_precision_vs_owner")
    ts_overlap_better = (
        (ts_j is not None and ts_j > E1_R3_JACCARD * 1.5)
        or (ts_p is not None and ts_p > E1_R3_PREC * 1.5)
    )
    base_pf = base_ntp.get("pf_maker") if base_ntp else None
    ts_pf = ts_ntp.get("pf_maker") if ts_ntp else None
    sparse_pf = sparse_ntp.get("pf_maker") if sparse_ntp else None
    ts_pf_lift = (
        ts_pf is not None
        and base_pf is not None
        and (ts_pf >= SUCCESS_PF or (ts_pf - base_pf) >= 0.05)
    )
    # Incremental vs E3 sparse (same n band): must not be ~identical
    ts_vs_e3_delta = None
    if ts_pf is not None and sparse_pf is not None:
        ts_vs_e3_delta = round(ts_pf - sparse_pf, 4)
    ts_n_ok = bool(
        ts_ntp is not None and ts_ntp["n"] <= TS_N_CAP and ts_ntp["n"] >= N_TARGET_LO * 0.5
    )
    ts_ok = bool(ts_n_ok and ts_pf_lift and ts_overlap_better)

    payload = {
        "tag": tag,
        "holdout_touched": False,
        "n_symbols": n_sym,
        "n_owner_short_train": int(len(owner_all)),
        "n_owner_cuts_matched": len(pooled_owner),
        "n_tips_raw": n_tips,
        "time_min": str(t_min) if t_min is not None else None,
        "time_max": str(t_max) if t_max is not None else None,
        "elapsed_sec": round(time.time() - t0, 1),
        "predeclared": {
            "e3_n_band": [N_TARGET_LO, N_TARGET_HI],
            "e3_n_aim": N_TARGET_AIM,
            "e3_thr_grid": list(E3_THR_GRID),
            "e3_gap_grid": list(E3_GAP_GRID),
            "base_thr": BASE_THR,
            "base_gap": BASE_GAP,
            "two_stage": {
                "stage1": (
                    f"emergence tip: fast≤{FAST_MAX} & full≤{FULL_MAX}, "
                    f"run first hits {MIN_DENSE_BARS}"
                ),
                "stage2": (
                    f"within tip+1..+{TS_WAIT}: order_score≤{TS_ORDER_MAX} "
                    f"∧ ret_8≤{TS_RET8_MAX} ∧ Δfast_spread(8)≥{TS_SPREAD_MIN} "
                    f"∧ close<cluster_mid; MIN_GAP={TS_GAP}; n_cap={TS_N_CAP}"
                ),
                "spread_bump_grid": list(TS_SPREAD_BUMP_GRID),
                "not_full_market_R3": True,
            },
            "exits": list(EXIT_KEYS),
            "costs": {"maker": FORWARD_COST, "legacy": LEGACY_P0_ROUND_TRIP},
            "success_lines": {
                "e3": "PF@maker≥1.3 AND 2026-04 not ≪1; n in band; thr pick on n only",
                "two_stage": (
                    "n controllable + (PF≥1.3 or clear Δ vs spread) + "
                    "Jaccard/precision clearly > E1 wide R3"
                ),
            },
        },
        "e3": {
            "pick": e3_pick,
            "count_rows": e3_count_rows,
            "main": e3_main,
            "verdict_pass": e3_ok,
            "apr_pf": e3_apr,
            "apr_collapse_flag": e3_apr_collapse,
            "threshold_overfit_note": (
                "thr/gap chosen to hit n-band (count-only), NOT maximized on PF. "
                "Still a train-set sparsity choice → report as discovery, not deploy."
            ),
        },
        "two_stage": {
            "pick": ts_pick,
            "count_by_thr": {str(k): v for k, v in ts_count.items()},
            "main": ts_main,
            "overlap_w18": ov,
            "overlap_w18_predeclared": ov_pre,
            "predeclared_ntp": ts_pre_ntp,
            "delta_pf_vs_e3_sparse": ts_vs_e3_delta,
            "vs_e1_r3": {
                "e1_r3_jaccard": E1_R3_JACCARD,
                "e1_r3_precision": E1_R3_PREC,
                "overlap_clearly_better": ts_overlap_better,
            },
            "verdict_pass": ts_ok,
            "near_duplicate_of_e3_note": (
                "If |ΔPF vs E3 sparse|≪0.05 and n≈E3, hard-confirm adds little "
                "beyond raising expand thr — do not pack as independent win."
            ),
        },
        "do_not_pack": True,
        "holdout8_recommendation": "no — discovery only; do not apply for #8 on these alone",
    }

    json_path = out_dir / f"{tag}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str))
    _write_csv(out_dir / f"{tag}_E3_count.csv", e3_count_rows)
    _write_csv(out_dir / f"{tag}_E3_main.csv", e3_main)
    _write_csv(out_dir / f"{tag}_two_stage_main.csv", ts_main)
    _write_csv(out_dir / f"{tag}_overlap.csv", overlap_rows)
    _write_csv(out_dir / f"{tag}_periods.csv", period_rows)

    # Console summary
    print("\n=== E3 verdict (separate) ===", flush=True)
    if base_ntp and sparse_ntp:
        print(
            f"  base  n={base_ntp['n']} PF@m={base_ntp['pf_maker']} "
            f"apr={base_ntp['pf_2026_04']}",
            flush=True,
        )
        print(
            f"  sparse thr={e3_pick['thr']} gap={e3_pick['gap']} "
            f"n={sparse_ntp['n']} PF@m={sparse_ntp['pf_maker']} "
            f"apr={sparse_ntp['pf_2026_04']}  PASS={e3_ok}",
            flush=True,
        )
    print("=== two-stage verdict (separate) ===", flush=True)
    if ts_ntp:
        print(
            f"  primary n={ts_ntp['n']} PF@m={ts_ntp['pf_maker']} "
            f"apr={ts_ntp['pf_2026_04']} J={ts_j} prec={ts_p}  "
            f"ΔvsE3={ts_vs_e3_delta} PASS={ts_ok}",
            flush=True,
        )
    if ts_pre_ntp:
        print(
            f"  predeclared(thr={TS_SPREAD_MIN}) n={ts_pre_ntp['n']} "
            f"PF@m={ts_pre_ntp['pf_maker']} apr={ts_pre_ntp['pf_2026_04']}",
            flush=True,
        )
    print(f"wrote {json_path}  elapsed={payload['elapsed_sec']}s", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=20)
    ap.add_argument("--tag", default="e3_sparse_and_two_stage")
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    args = ap.parse_args()
    return run(n_symbols=args.n_symbols, tag=args.tag, sheet=args.sheet)


if __name__ == "__main__":
    raise SystemExit(main())
