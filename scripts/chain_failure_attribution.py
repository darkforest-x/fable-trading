#!/usr/bin/env python3
"""Train-only chain failure attribution (A/B/C/D) — never touches holdout.

Owner asked which layer failed after holdout#7 killed pre-registered A
(spread_expand short + trend exits). This script adds leverage diagnostics
that existing reports only partially cover:

  A — Entry vs exit lift: fixed best entry × exit variants vs fixed best
      exit (no_tp) × entry variants (train PF@maker).
  B — Regime slices on spread_expand short + no_tp: month, atr quartile,
      BTC trend bucket (BTC ret96 / close vs SMA96).
  C — Feature filter on the same short fires: published rich-short AND +
      a simple score gate; walk-forward check whether 2026-04 is avoidable.
  D — Owner short boxes vs spread_expand short fires: Jaccard / hit rates
      within ±{0,8,18,48} bars; median |Δbars|.

Hard cut: signal open_time < 2026-05-04. No promote / no holdout / no ACTIVE.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/chain_failure_attribution.py \\
      --n-symbols 0 --tag chain_failure_attribution
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]

from scripts.direction_select_base_rate import (  # noqa: E402
    HOLDOUT_START,
    MIN_GAP_BARS,
    SL_MULT,
    TP_MULT,
    WARMUP,
    collect_signals,
)
from scripts.short_trend_ab import (  # noqa: E402
    EXIT_RESOLVERS,
    _enrich,
    _load_owner_shorts,
    _oracle_cuts_for_symbol,
    _stats,
)
from src.costs import FORWARD_COST, LEGACY_P0_ROUND_TRIP  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.features import add_features  # noqa: E402
from src.judgment.labeling import HORIZON_BARS, label_candidate_sl_only  # noqa: E402

DEFAULT_SHEET = PROJECT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"
SUCCESS_PF = 1.3
TREND_H = 144

# Published short causal AND from p_owner_side_rich_features_verdict (disclosure).
RICH_SHORT_AND = {
    "close_vs_sma20": ("<=", -0.0102),
    "order_score": ("<=", 0.0),
    "spread_chg8": (">=", 0.0040),
}

# Entry variants whose short fires we settle under fixed no_tp exit.
ENTRY_SHORT_KEYS = (
    "ctrl_fixed_short",
    "arrange_order_score",
    "range_break_n20",
    "spread_expand_chg8",
)

# Exit variants under fixed spread_expand short (A panel).
EXIT_KEYS = (
    "baseline_tp5_sl2_h72",
    "no_tp_sl2_h144",
    "trail4_atr_h144",
    "ma_ema55_h144",
)

MATCH_WINDOWS = (0, 8, 18, 48)


def _pf_block(gross: list[float], holds: list[int] | None = None) -> dict:
    h = holds if holds is not None else [0] * len(gross)
    return {
        "maker": _stats(gross, h, FORWARD_COST),
        "legacy": _stats(gross, h, LEGACY_P0_ROUND_TRIP),
    }


def _append(bag: dict[str, list], *, g: float, h: int, t: pd.Timestamp, **extra) -> None:
    bag["gross"].append(g)
    bag["hold"].append(h)
    bag["time"].append(t)
    for k, v in extra.items():
        bag.setdefault(k, []).append(v)


def _empty_bag(*extra_keys: str) -> dict[str, list]:
    bag: dict[str, list] = {"gross": [], "hold": [], "time": []}
    for k in extra_keys:
        bag[k] = []
    return bag


def _load_btc_ret96() -> pd.Series:
    """BTC close ret over 96 bars + close/sma96 - 1, indexed by open_time UTC."""
    btc_frame = None
    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if source == "okx" and symbol == "BTC_USDT_SWAP":
            btc_frame = frame
            break
    if btc_frame is None:
        raise SystemExit("ERROR: BTC_USDT_SWAP 15m series not found")
    en = _enrich(btc_frame)
    t = pd.to_datetime(en["open_time"], utc=True)
    close = en["close"].astype(float)
    sma96 = close.rolling(96, min_periods=48).mean()
    ret96 = close / close.shift(96) - 1.0
    vs_sma = close / sma96 - 1.0
    out = pd.DataFrame({"btc_ret96": ret96.to_numpy(), "btc_vs_sma96": vs_sma.to_numpy()}, index=t)
    return out


def _btc_bucket(ret96: float) -> str:
    if not np.isfinite(ret96):
        return "unknown"
    if ret96 >= 0.03:
        return "btc_up"
    if ret96 <= -0.03:
        return "btc_down"
    return "btc_flat"


def _atr_quartile(vals: np.ndarray, v: float) -> str:
    if not np.isfinite(v) or len(vals) < 50:
        return "unknown"
    q = np.nanpercentile(vals, [25, 50, 75])
    if v <= q[0]:
        return "atr_q1"
    if v <= q[1]:
        return "atr_q2"
    if v <= q[2]:
        return "atr_q3"
    return "atr_q4"


def _rich_and_pass(row: pd.Series) -> bool:
    for col, (op, thr) in RICH_SHORT_AND.items():
        v = float(row.get(col, np.nan))
        if not np.isfinite(v):
            return False
        if op == "<=" and not (v <= thr):
            return False
        if op == ">=" and not (v >= thr):
            return False
    return True


def _month_key(t: pd.Timestamp) -> str:
    ts = pd.Timestamp(t)
    return f"{ts.year:04d}-{ts.month:02d}"


def _overlap_stats(
    owner_idx: list[int], rule_idx: list[int], window: int
) -> dict:
    """Symmetric nearest-neighbor hit within ±window bars."""
    if not owner_idx and not rule_idx:
        return {
            "window": window,
            "n_owner": 0,
            "n_rule": 0,
            "owner_hit": 0,
            "rule_hit": 0,
            "jaccard": None,
            "owner_recall": None,
            "rule_precision_vs_owner": None,
            "median_abs_delta_owner_hits": None,
        }
    o = np.asarray(sorted(owner_idx), dtype=int)
    r = np.asarray(sorted(rule_idx), dtype=int)
    deltas_hit: list[int] = []
    owner_hit = 0
    if len(r):
        for oi in o:
            j = int(np.searchsorted(r, oi))
            best = 10**9
            for cand in (j - 1, j):
                if 0 <= cand < len(r):
                    best = min(best, abs(int(r[cand]) - oi))
            if best <= window:
                owner_hit += 1
                deltas_hit.append(best)
    rule_hit = 0
    if len(o):
        for ri in r:
            j = int(np.searchsorted(o, ri))
            best = 10**9
            for cand in (j - 1, j):
                if 0 <= cand < len(o):
                    best = min(best, abs(int(o[cand]) - ri))
            if best <= window:
                rule_hit += 1
    # Approximate Jaccard via matched pairs count / union.
    # Use min(owner_hit, rule_hit) as intersection proxy (conservative).
    inter = min(owner_hit, rule_hit)
    union = len(o) + len(r) - inter
    return {
        "window": window,
        "n_owner": int(len(o)),
        "n_rule": int(len(r)),
        "owner_hit": int(owner_hit),
        "rule_hit": int(rule_hit),
        "jaccard": round(inter / union, 4) if union else None,
        "owner_recall": round(owner_hit / len(o), 4) if len(o) else None,
        "rule_precision_vs_owner": round(rule_hit / len(r), 4) if len(r) else None,
        "median_abs_delta_owner_hits": (
            float(np.median(deltas_hit)) if deltas_hit else None
        ),
    }


def run(*, n_symbols: int, tag: str, sheet: Path) -> int:
    btc = _load_btc_ret96()
    owner_all = _load_owner_shorts(sheet)
    owner_by_sym: dict[str, list[dict]] = defaultdict(list)
    for rec in owner_all.to_dict(orient="records"):
        owner_by_sym[str(rec["symbol"])].append(rec)

    # A panels
    exit_bags = {e: _empty_bag() for e in EXIT_KEYS}
    entry_bags = {e: _empty_bag() for e in ENTRY_SHORT_KEYS}

    # B/C: spread short + no_tp with regime tags + features
    regime_bag = _empty_bag(
        "month", "atr_q", "btc_bucket", "atr_pct",
        "order_score", "spread_chg8", "ret_12", "close_vs_sma20",
        "rich_and", "score",
    )

    # D overlap accumulators
    overlap_owner_all: list[int] = []  # dummy; we aggregate per-symbol then pool
    per_sym_overlap: list[dict] = []
    pooled_owner: list[tuple[str, int]] = []
    pooled_rule: list[tuple[str, int]] = []

    n_sym = 0
    t_min = t_max = None
    atr_pool: list[float] = []

    # First pass collect atr for global quartiles? Better: per-trade atr then
    # assign quartile after collecting all atr values.
    raw_regime: list[dict] = []

    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        if is_eval_symbol(symbol):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        train_mask = times < HOLDOUT_START
        if int(train_mask.sum()) < WARMUP + 200:
            continue
        # Keep only train bars for settlement universe (warmup already inside).
        frame = frame.loc[train_mask].reset_index(drop=True)
        if len(frame) < WARMUP + TREND_H + 50:
            continue

        enriched = add_features(_enrich(frame))
        # close_vs_sma20 for rich AND
        if "sma20" not in enriched.columns:
            enriched["sma20"] = enriched["close"].rolling(20, min_periods=10).mean()
        enriched["close_vs_sma20"] = enriched["close"] / enriched["sma20"].replace(0, np.nan) - 1.0

        t = pd.to_datetime(enriched["open_time"], utc=True)
        sigs, _ = collect_signals(enriched)

        # --- D: overlap ---
        owner_cuts = _oracle_cuts_for_symbol(enriched, owner_by_sym.get(symbol, []))
        rule_shorts = [i for i, d in sigs.get("spread_expand_chg8", []) if d < 0]
        if owner_cuts or rule_shorts:
            for w in MATCH_WINDOWS:
                st = _overlap_stats(owner_cuts, rule_shorts, w)
                st["symbol"] = symbol
                per_sym_overlap.append(st)
            for oi in owner_cuts:
                pooled_owner.append((symbol, oi))
            for ri in rule_shorts:
                pooled_rule.append((symbol, ri))

        # --- A: fixed entry spread short × exits ---
        for i, d in sigs.get("spread_expand_chg8", []):
            if d >= 0:
                continue
            ti = t.iloc[i]
            if t_min is None or ti < t_min:
                t_min = ti
            if t_max is None or ti > t_max:
                t_max = ti
            for ename in EXIT_KEYS:
                out = EXIT_RESOLVERS[ename](enriched, i, -1)
                if out is None:
                    continue
                _append(exit_bags[ename], g=float(out.realized_ret), h=int(out.exit_offset), t=ti)

            # B/C features on no_tp settlement
            out_ntp = EXIT_RESOLVERS["no_tp_sl2_h144"](enriched, i, -1)
            if out_ntp is not None:
                atr = float(enriched["atr_pct"].iloc[i])
                atr_pool.append(atr)
                # BTC align
                if ti in btc.index:
                    btc_ret = float(btc.loc[ti, "btc_ret96"])
                else:
                    # nearest prior
                    pos = btc.index.searchsorted(ti, side="right") - 1
                    btc_ret = float(btc.iloc[pos]["btc_ret96"]) if pos >= 0 else float("nan")
                row = enriched.iloc[i]
                os_ = float(row.get("order_score", np.nan))
                sc8 = float(row.get("spread_chg8", np.nan))
                r12 = float(row.get("ret_12", np.nan))
                cvs = float(row.get("close_vs_sma20", np.nan))
                # Simple bearish score: lower order + more negative ret + larger expand
                score = 0.0
                if np.isfinite(os_):
                    score += (2.0 - os_)  # order_score 0 best for short
                if np.isfinite(r12):
                    score += max(0.0, -r12) * 50.0
                if np.isfinite(sc8):
                    score += sc8 * 100.0
                raw_regime.append(
                    {
                        "gross": float(out_ntp.realized_ret),
                        "hold": int(out_ntp.exit_offset),
                        "time": ti,
                        "month": _month_key(ti),
                        "atr_pct": atr,
                        "btc_ret96": btc_ret,
                        "btc_bucket": _btc_bucket(btc_ret),
                        "order_score": os_,
                        "spread_chg8": sc8,
                        "ret_12": r12,
                        "close_vs_sma20": cvs,
                        "rich_and": _rich_and_pass(row),
                        "score": score,
                    }
                )

        # --- A: fixed no_tp × entry variants (short only) ---
        for ek in ENTRY_SHORT_KEYS:
            for i, d in sigs.get(ek, []):
                if d >= 0:
                    continue
                out = label_candidate_sl_only(
                    enriched, i, direction=-1, sl_mult=SL_MULT, horizon=TREND_H, entry="next_open"
                )
                if out is None:
                    continue
                _append(
                    entry_bags[ek],
                    g=float(out.realized_ret),
                    h=int(out.exit_offset),
                    t=t.iloc[i],
                )

        n_sym += 1
        if n_sym % 40 == 0:
            print(f"  scanned {n_sym} symbols …", flush=True)
        if n_symbols and n_sym >= n_symbols:
            break

    atr_arr = np.asarray(atr_pool, dtype=float)
    for rec in raw_regime:
        rec["atr_q"] = _atr_quartile(atr_arr, rec["atr_pct"])
        _append(
            regime_bag,
            g=rec["gross"],
            h=rec["hold"],
            t=rec["time"],
            month=rec["month"],
            atr_q=rec["atr_q"],
            btc_bucket=rec["btc_bucket"],
            atr_pct=rec["atr_pct"],
            order_score=rec["order_score"],
            spread_chg8=rec["spread_chg8"],
            ret_12=rec["ret_12"],
            close_vs_sma20=rec["close_vs_sma20"],
            rich_and=rec["rich_and"],
            score=rec["score"],
        )

    # ---- summarize A ----
    a_exit_rows = []
    for e in EXIT_KEYS:
        blk = _pf_block(exit_bags[e]["gross"], exit_bags[e]["hold"])
        a_exit_rows.append(
            {
                "panel": "A_fixed_entry_vary_exit",
                "entry": "spread_expand_chg8_short",
                "exit": e,
                "n": blk["maker"]["n"],
                "pf_maker": blk["maker"]["profit_factor"],
                "sum_net_maker": blk["maker"]["sum_net"],
                "win_rate": blk["maker"]["win_rate"],
                "pf_legacy": blk["legacy"]["profit_factor"],
            }
        )
    a_entry_rows = []
    for e in ENTRY_SHORT_KEYS:
        blk = _pf_block(entry_bags[e]["gross"], entry_bags[e]["hold"])
        a_entry_rows.append(
            {
                "panel": "A_fixed_exit_vary_entry",
                "entry": e,
                "exit": "no_tp_sl2_h144",
                "n": blk["maker"]["n"],
                "pf_maker": blk["maker"]["profit_factor"],
                "sum_net_maker": blk["maker"]["sum_net"],
                "win_rate": blk["maker"]["win_rate"],
                "pf_legacy": blk["legacy"]["profit_factor"],
            }
        )

    # Lift attribution (spread + baseline → spread + no_tp vs fixed_short + no_tp → spread + no_tp)
    base_exit_pf = next(r["pf_maker"] for r in a_exit_rows if r["exit"] == "baseline_tp5_sl2_h72")
    best_exit_pf = next(r["pf_maker"] for r in a_exit_rows if r["exit"] == "no_tp_sl2_h144")
    fixed_entry_pf = next(r["pf_maker"] for r in a_entry_rows if r["entry"] == "ctrl_fixed_short")
    best_entry_pf = next(r["pf_maker"] for r in a_entry_rows if r["entry"] == "spread_expand_chg8")
    a_lift = {
        "exit_lift_on_spread_entry": round((best_exit_pf or 0) - (base_exit_pf or 0), 3),
        "entry_lift_on_no_tp_exit": round((best_entry_pf or 0) - (fixed_entry_pf or 0), 3),
        "baseline_spread_tp5sl2_pf": base_exit_pf,
        "spread_no_tp_pf": best_exit_pf,
        "fixed_short_no_tp_pf": fixed_entry_pf,
        "note": (
            "exit_lift = no_tp − baseline under fixed spread short; "
            "entry_lift = spread − fixed_short under fixed no_tp"
        ),
    }

    # ---- B regime ----
    def _slice_pf(keys: list, values: list) -> list[dict]:
        bags: dict[str, list[float]] = defaultdict(list)
        holds: dict[str, list[int]] = defaultdict(list)
        for k, g, h in zip(keys, regime_bag["gross"], regime_bag["hold"]):
            bags[str(k)].append(g)
            holds[str(k)].append(h)
        rows = []
        for k in sorted(bags.keys()):
            blk = _pf_block(bags[k], holds[k])
            rows.append(
                {
                    "slice": k,
                    "n": blk["maker"]["n"],
                    "pf_maker": blk["maker"]["profit_factor"],
                    "sum_net_maker": blk["maker"]["sum_net"],
                    "win_rate": blk["maker"]["win_rate"],
                }
            )
        return rows

    b_month = _slice_pf(regime_bag["month"], regime_bag["gross"])
    b_atr = _slice_pf(regime_bag["atr_q"], regime_bag["gross"])
    b_btc = _slice_pf(regime_bag["btc_bucket"], regime_bag["gross"])

    # ---- C feature filters ----
    g_all = regime_bag["gross"]
    h_all = regime_bag["hold"]
    rich_mask = [bool(x) for x in regime_bag["rich_and"]]
    g_rich = [g for g, m in zip(g_all, rich_mask) if m]
    h_rich = [h for h, m in zip(h_all, rich_mask) if m]
    times = regime_bag["time"]
    scores = regime_bag["score"]

    # Walk-forward score gate: train on past months, keep top 50% of next month
    by_month: dict[str, list[tuple[float, float, int]]] = defaultdict(list)
    for t, g, h, s in zip(times, g_all, h_all, scores):
        by_month[_month_key(t)].append((float(s), float(g), int(h)))
    months_sorted = sorted(by_month.keys())
    wf_kept_g: list[float] = []
    wf_kept_h: list[int] = []
    wf_month_rows: list[dict] = []
    for mi, m in enumerate(months_sorted):
        if mi == 0:
            continue  # need prior history for threshold
        prior = []
        for pm in months_sorted[:mi]:
            prior.extend(by_month[pm])
        if len(prior) < 80:
            continue
        thr = float(np.median([p[0] for p in prior]))
        kept = [(g, h) for s, g, h in by_month[m] if s >= thr]
        if not kept:
            continue
        gg = [x[0] for x in kept]
        hh = [x[1] for x in kept]
        blk = _pf_block(gg, hh)
        all_blk = _pf_block([x[1] for x in by_month[m]], [x[2] for x in by_month[m]])
        wf_month_rows.append(
            {
                "month": m,
                "n_all": all_blk["maker"]["n"],
                "pf_all": all_blk["maker"]["profit_factor"],
                "n_kept": blk["maker"]["n"],
                "pf_kept": blk["maker"]["profit_factor"],
                "sum_net_kept": blk["maker"]["sum_net"],
                "score_thr_median_prior": round(thr, 4),
            }
        )
        wf_kept_g.extend(gg)
        wf_kept_h.extend(hh)

    c_summary = {
        "all_spread_short_no_tp": _pf_block(g_all, h_all)["maker"],
        "rich_and_filter": _pf_block(g_rich, h_rich)["maker"],
        "rich_and_n_pass": len(g_rich),
        "rich_and_pass_rate": round(len(g_rich) / len(g_all), 4) if g_all else None,
        "wf_score_top_half_pooled": _pf_block(wf_kept_g, wf_kept_h)["maker"],
        "wf_monthly": wf_month_rows,
        "apr2026_row": next((r for r in wf_month_rows if r["month"] == "2026-04"), None),
        "rich_and_definition": RICH_SHORT_AND,
    }

    # ---- D pooled overlap (symbol-aware: match only within same symbol) ----
    # Rebuild as dict symbol -> lists
    ow_map: dict[str, list[int]] = defaultdict(list)
    ru_map: dict[str, list[int]] = defaultdict(list)
    for sym, idx in pooled_owner:
        ow_map[sym].append(idx)
    for sym, idx in pooled_rule:
        ru_map[sym].append(idx)

    d_pooled = []
    for w in MATCH_WINDOWS:
        o_hit = r_hit = n_o = n_r = 0
        deltas: list[int] = []
        for sym in set(ow_map) | set(ru_map):
            st = _overlap_stats(ow_map.get(sym, []), ru_map.get(sym, []), w)
            o_hit += st["owner_hit"]
            r_hit += st["rule_hit"]
            n_o += st["n_owner"]
            n_r += st["n_rule"]
            if st["median_abs_delta_owner_hits"] is not None and st["owner_hit"]:
                # recompute deltas for pooling median — approximate via recount
                o = np.asarray(sorted(ow_map.get(sym, [])), dtype=int)
                r = np.asarray(sorted(ru_map.get(sym, [])), dtype=int)
                if len(r) and len(o):
                    for oi in o:
                        j = int(np.searchsorted(r, oi))
                        best = 10**9
                        for cand in (j - 1, j):
                            if 0 <= cand < len(r):
                                best = min(best, abs(int(r[cand]) - oi))
                        if best <= w:
                            deltas.append(best)
        inter = min(o_hit, r_hit)
        union = n_o + n_r - inter
        d_pooled.append(
            {
                "window_bars": w,
                "n_owner": n_o,
                "n_rule": n_r,
                "owner_hit": o_hit,
                "rule_hit": r_hit,
                "jaccard_approx": round(inter / union, 4) if union else None,
                "owner_recall": round(o_hit / n_o, 4) if n_o else None,
                "rule_precision": round(r_hit / n_r, 4) if n_r else None,
                "median_abs_delta_on_owner_hits": (
                    float(np.median(deltas)) if deltas else None
                ),
            }
        )

    out_dir = PROJECT / "analysis" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "tag": tag,
        "discipline": "train_only_open_time_lt_2026-05-04",
        "n_symbols": n_sym,
        "time_min": str(t_min),
        "time_max": str(t_max),
        "n_owner_short_boxes_train": int(len(owner_all)),
        "A_entry_vs_exit": {
            "fixed_entry_vary_exit": a_exit_rows,
            "fixed_exit_vary_entry": a_entry_rows,
            "lift": a_lift,
        },
        "B_regime": {
            "base": "spread_expand_chg8 short + no_tp_sl2_h144",
            "monthly": b_month,
            "atr_quartile": b_atr,
            "btc_trend": b_btc,
        },
        "C_feature_filter": c_summary,
        "D_overlap": {
            "pooled": d_pooled,
            "match_windows_bars": list(MATCH_WINDOWS),
            "note": (
                "Jaccard approx = min(owner_hit, rule_hit) / (n_o+n_r-inter); "
                "matches are within-symbol nearest neighbor ≤ window"
            ),
        },
    }
    json_path = out_dir / f"{tag}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str))

    # CSVs
    def _write(path: Path, rows: list[dict]) -> None:
        if not rows:
            path.write_text("")
            return
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    _write(out_dir / f"{tag}_A.csv", a_exit_rows + a_entry_rows)
    _write(
        out_dir / f"{tag}_B.csv",
        (
            [{**r, "axis": "month"} for r in b_month]
            + [{**r, "axis": "atr_q"} for r in b_atr]
            + [{**r, "axis": "btc"} for r in b_btc]
        ),
    )
    _write(out_dir / f"{tag}_C_wf_months.csv", wf_month_rows)
    _write(out_dir / f"{tag}_D_overlap.csv", d_pooled)

    print(json.dumps({"wrote": str(json_path), "n_symbols": n_sym, "A_lift": a_lift, "D": d_pooled}, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=0, help="0 = all")
    ap.add_argument("--tag", default="chain_failure_attribution")
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    args = ap.parse_args()
    return run(n_symbols=args.n_symbols, tag=args.tag, sheet=args.sheet)


if __name__ == "__main__":
    raise SystemExit(main())
