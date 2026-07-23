#!/usr/bin/env python3
"""E1 entry-align (owner short) + E2 regime gate — train-only discovery.

Hard cut: signal open_time < 2026-05-04. Never touches holdout / ACTIVE / live.

E1 — Learn 1–3 pre-declared causal short entry rules from owner_side=short
     moments vs random negatives (rich OHLCV features). Full-market scan;
     report owner overlap (vs spread_expand baseline) AND causal PF under
     baseline TP5/SL2 + no_tp_sl2 + trail4. Overlap lift alone is not success.

E2 — Single-variable regime gates on OLD spread_expand short (+ no_tp):
     not_btc_up / atr_q34. Report PF + 2026-04 before/after. Optional
     "+regime on best E1" row is a separate control, not a magic combo claim.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/entry_align_and_regime.py --n-symbols 20
  PYTHONPATH=. .venv/bin/python scripts/entry_align_and_regime.py \\
      --n-symbols 0 --tag entry_align_and_regime
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]

from scripts.chain_failure_attribution import (  # noqa: E402
    MATCH_WINDOWS,
    _btc_bucket,
    _load_btc_ret96,
    _month_key,
    _overlap_stats,
)
from scripts.direction_select_base_rate import (  # noqa: E402
    HOLDOUT_START,
    MIN_GAP_BARS,
    SL_MULT,
    TP_MULT,
    WARMUP,
    collect_signals,
)
from scripts.owner_side_rich_features import (  # noqa: E402
    add_rich_features,
    feature_group,
    rich_feature_columns,
)
from scripts.owner_side_rich_features_verdict import (  # noqa: E402
    build_causal_rule,
    rule_mask,
    sample_feature_row,
)
from scripts.short_trend_ab import (  # noqa: E402
    EXIT_RESOLVERS,
    _load_owner_shorts,
    _oracle_cuts_for_symbol,
    _stats,
)
from src.costs import FORWARD_COST, LEGACY_P0_ROUND_TRIP  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.labeling import HORIZON_BARS  # noqa: E402

DEFAULT_SHEET = PROJECT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"
SUCCESS_PF = 1.3
OVERLAP_RECALL_LINE = 0.45
OVERLAP_JACCARD_LINE = 0.12
NEG_RATIO = 4.0
EXIT_KEYS = ("baseline_tp5_sl2_h72", "no_tp_sl2_h144", "trail4_atr_h144")
PRIMARY_EXIT = "no_tp_sl2_h144"
OVERLAP_WINDOW = 18  # = MIN_GAP; matches chain_failure headline


def _pf_block(gross: list[float], holds: list[int] | None = None) -> dict:
    h = holds if holds is not None else [0] * len(gross)
    return {
        "maker": _stats(gross, h, FORWARD_COST),
        "legacy": _stats(gross, h, LEGACY_P0_ROUND_TRIP),
    }


def _empty() -> dict[str, list]:
    return {"gross": [], "hold": [], "time": []}


def _append(bag: dict[str, list], *, g: float, h: int, t: pd.Timestamp) -> None:
    bag["gross"].append(g)
    bag["hold"].append(h)
    bag["time"].append(t)


def _month_pf(bag: dict[str, list], month: str = "2026-04") -> dict:
    g = [
        float(x)
        for x, t in zip(bag["gross"], bag["time"])
        if _month_key(t) == month
    ]
    h = [
        int(x)
        for x, t in zip(bag["hold"], bag["time"])
        if _month_key(t) == month
    ]
    return _stats(g, h, FORWARD_COST)


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    keys: list[str] = []
    seen = set()
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


def _train_lgbm(
    train_df: pd.DataFrame, feat_cols: list[str], *, seed: int
) -> tuple[float, list[tuple[str, float]]]:
    X = train_df[feat_cols].to_numpy(dtype=float)
    y = train_df["label"].to_numpy(dtype=int)
    order = np.argsort(pd.to_datetime(train_df["open_time"], utc=True).to_numpy())
    cut = max(40, int(len(order) * 0.7))
    if cut >= len(order) - 20:
        cut = max(20, len(order) - 20)
    tr_idx, va_idx = order[:cut], order[cut:]
    dtrain = lgb.Dataset(X[tr_idx], label=y[tr_idx], feature_name=feat_cols)
    dval = lgb.Dataset(X[va_idx], label=y[va_idx], feature_name=feat_cols, reference=dtrain)
    params = {
        "objective": "binary",
        "metric": "auc",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_data_in_leaf": 40,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "verbosity": -1,
        "seed": seed,
    }
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=300,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(40, verbose=False)],
    )
    from sklearn.metrics import roc_auc_score

    va_pred = booster.predict(X[va_idx])
    auc = (
        float(roc_auc_score(y[va_idx], va_pred))
        if len(np.unique(y[va_idx])) > 1
        else float("nan")
    )
    gain_raw = booster.feature_importance(importance_type="gain")
    gains = sorted(
        [(feat_cols[i], float(gain_raw[i])) for i in range(len(feat_cols))],
        key=lambda x: -x[1],
    )
    return auc, gains


def _best_stump(
    pos_df: pd.DataFrame, neg_df: pd.DataFrame, feat_cols: list[str]
) -> dict:
    """Single-feature threshold maximizing Youden J on pos vs neg."""
    best: Optional[dict] = None
    y_pos = np.ones(len(pos_df))
    y_neg = np.zeros(len(neg_df))
    for name in feat_cols:
        p = pos_df[name].to_numpy(dtype=float)
        n = neg_df[name].to_numpy(dtype=float)
        vals = np.concatenate([p, n])
        labels = np.concatenate([y_pos, y_neg])
        finite = np.isfinite(vals)
        vals, labels = vals[finite], labels[finite]
        if len(vals) < 80 or len(np.unique(labels)) < 2:
            continue
        # Candidate thresholds from pos quantiles
        pq = np.nanpercentile(p[np.isfinite(p)], [10, 25, 40, 50, 60, 75, 90])
        for thr in pq:
            for op in ("<=", ">="):
                if op == "<=":
                    pred = vals <= thr
                else:
                    pred = vals >= thr
                tp = float(((pred) & (labels == 1)).sum())
                fn = float(((~pred) & (labels == 1)).sum())
                fp = float(((pred) & (labels == 0)).sum())
                tn = float(((~pred) & (labels == 0)).sum())
                tpr = tp / (tp + fn) if (tp + fn) else 0.0
                fpr = fp / (fp + tn) if (fp + tn) else 0.0
                j = tpr - fpr
                prec = tp / (tp + fp) if (tp + fp) else 0.0
                if best is None or j > best["youden"]:
                    best = {
                        "feature": name,
                        "group": feature_group(name),
                        "op": op,
                        "threshold": float(thr),
                        "youden": float(j),
                        "tpr": float(tpr),
                        "fpr": float(fpr),
                        "precision": float(prec),
                    }
    if best is None:
        return {"logic": "AND", "clauses": []}
    return {
        "logic": "AND",
        "clauses": [
            {
                "feature": best["feature"],
                "group": best["group"],
                "op": best["op"],
                "threshold": best["threshold"],
                "gain": best["youden"],
                "pos_median": None,
                "neg_median": None,
            }
        ],
        "stump_metrics": {k: best[k] for k in ("youden", "tpr", "fpr", "precision")},
    }


def _confirm_rule(pos_df: pd.DataFrame, neg_df: pd.DataFrame) -> dict:
    """Pre-declared narrative: already falling + spread expanding + order bearish.

    Thresholds fit on owner-short pos vs neg (optimistic for overlap).
    """
    needed = ("close_vs_sma20", "order_score", "spread_chg8", "ret_8")
    for c in needed:
        if c not in pos_df.columns:
            return {"logic": "AND", "clauses": [], "error": f"missing {c}"}

    # Direction fixed by short narrative; thr from pos quartile toward neg.
    clauses = []
    specs = [
        ("close_vs_sma20", "<=", 0.75),  # pos more negative → upper quartile of pos
        ("order_score", "<=", 0.75),
        ("spread_chg8", ">=", 0.25),
        ("ret_8", "<=", 0.75),
    ]
    for name, op, q in specs:
        p = pos_df[name].dropna()
        n = neg_df[name].dropna()
        if len(p) < 30:
            continue
        thr = float(p.quantile(q))
        # Soft clamp order_score to integer-ish short side
        if name == "order_score":
            thr = min(thr, 0.0)
        clauses.append(
            {
                "feature": name,
                "group": feature_group(name),
                "op": op,
                "threshold": thr,
                "gain": 0.0,
                "pos_median": float(p.median()),
                "neg_median": float(n.median()) if len(n) else None,
            }
        )
    return {
        "logic": "AND",
        "clauses": clauses,
        "narrative": "already_falling + spread_expanding + order_bearish",
    }


def _rule_fires(featured: pd.DataFrame, rule: dict) -> list[int]:
    if not rule.get("clauses"):
        return []
    mask = rule_mask(featured, rule)
    idxs = np.where(mask)[0]
    out: list[int] = []
    last = -10**9
    for i in idxs:
        i = int(i)
        if i < WARMUP or i >= len(featured) - 2:
            continue
        if i - last < MIN_GAP_BARS:
            continue
        out.append(i)
        last = i
    return out


def _rule_to_text(rule: dict) -> str:
    parts = []
    for c in rule.get("clauses") or []:
        parts.append(f"{c['feature']}{c['op']}{c['threshold']:.6g}")
    return " ∧ ".join(parts) if parts else "(empty)"


def collect_pos_neg(
    owner_by_sym: dict[str, list[dict]],
    *,
    n_symbols: int,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], int]:
    rng = np.random.default_rng(seed)
    pos_rows: list[dict] = []
    neg_rows: list[dict] = []
    used_cols: list[str] | None = None
    n_sym = 0
    for symbol, frame in _iter_okx_train(n_symbols):
        items = owner_by_sym.get(symbol)
        if not items:
            continue
        featured = add_rich_features(frame)
        if used_cols is None:
            used_cols = rich_feature_columns(featured)
        used = [c for c in used_cols if c in featured.columns]
        cuts = _oracle_cuts_for_symbol(featured, items)
        if not cuts:
            continue
        label_cuts = set(cuts)
        for ci in cuts:
            feat = sample_feature_row(featured, ci, used, None)
            if feat is None:
                continue
            feat["label"] = 1
            feat["symbol"] = symbol
            pos_rows.append(feat)
        banned = set()
        for c in label_cuts:
            for d in range(-MIN_GAP_BARS, MIN_GAP_BARS + 1):
                banned.add(c + d)
        pool = [
            i
            for i in range(WARMUP, len(featured) - HORIZON_BARS - 2)
            if i not in banned
        ]
        n_neg = min(len(pool), max(1, int(len(label_cuts) * NEG_RATIO)))
        if pool and n_neg:
            for i in rng.choice(pool, size=n_neg, replace=False):
                feat = sample_feature_row(featured, int(i), used, None)
                if feat is None:
                    continue
                feat["label"] = 0
                feat["symbol"] = symbol
                neg_rows.append(feat)
        n_sym += 1
        if n_sym % 30 == 0:
            print(f"  [fit] symbols with owner short: {n_sym}", flush=True)
    return (
        pd.DataFrame(pos_rows),
        pd.DataFrame(neg_rows),
        (used_cols or []),
        n_sym,
    )


def _settle_short(
    enriched: pd.DataFrame, i: int, exit_name: str
) -> tuple[float, int] | None:
    out = EXIT_RESOLVERS[exit_name](enriched, i, -1)
    if out is None:
        return None
    return float(out.realized_ret), int(out.exit_offset)


def run(*, n_symbols: int, tag: str, sheet: Path, seed: int) -> int:
    t0 = time.time()
    out_dir = PROJECT / "analysis" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    btc = _load_btc_ret96()
    owner_all = _load_owner_shorts(sheet)
    owner_by_sym: dict[str, list[dict]] = defaultdict(list)
    for rec in owner_all.to_dict(orient="records"):
        owner_by_sym[str(rec["symbol"])].append(rec)

    print(
        f"E1/E2 entry_align_and_regime  n_owner_short={len(owner_all)}  "
        f"n_symbols_cap={n_symbols or 'all'}  holdout=FORBIDDEN",
        flush=True,
    )

    # ---------- E1 fit ----------
    print("=== E1: fit rules on owner short vs neg ===", flush=True)
    pos_df, neg_df, feat_cols, n_fit_sym = collect_pos_neg(
        owner_by_sym, n_symbols=n_symbols, seed=seed
    )
    if len(pos_df) < 40 or len(neg_df) < 40:
        raise SystemExit(
            f"ERROR: insufficient fit rows pos={len(pos_df)} neg={len(neg_df)}"
        )
    train_df = pd.concat([pos_df, neg_df], ignore_index=True)
    used = [c for c in feat_cols if c in train_df.columns]
    auc, gains = _train_lgbm(train_df, used, seed=seed)
    print(f"  fit pos={len(pos_df)} neg={len(neg_df)} auc={auc:.3f} feats={len(used)}", flush=True)

    rules: dict[str, dict] = {
        "R1_stump": _best_stump(pos_df, neg_df, used),
        "R2_and3": build_causal_rule(pos_df, neg_df, gains, used, top_k=3),
        "R3_confirm": _confirm_rule(pos_df, neg_df),
    }
    for name, rule in rules.items():
        print(f"  {name}: {_rule_to_text(rule)}", flush=True)

    # ---------- Scan bags ----------
    entry_names = ["spread_expand_chg8"] + list(rules.keys())
    # bags[entry][exit]
    bags: dict[str, dict[str, dict[str, list]]] = {
        e: {x: _empty() for x in EXIT_KEYS} for e in entry_names
    }
    # For E2: raw spread fires with regime tags (settled no_tp)
    spread_raw: list[dict] = []
    # Best-E1 + regime (filled after we know best; collect all E1 fires for all rules)
    e1_raw: dict[str, list[dict]] = {k: [] for k in rules}

    pooled_owner: list[tuple[str, int]] = []
    pooled_rule: dict[str, list[tuple[str, int]]] = {e: [] for e in entry_names}

    n_sym = 0
    t_min = t_max = None
    print("=== full-market scan ===", flush=True)
    for symbol, frame in _iter_okx_train(n_symbols):
        featured = add_rich_features(frame)
        t = pd.to_datetime(featured["open_time"], utc=True)
        owner_cuts = _oracle_cuts_for_symbol(featured, owner_by_sym.get(symbol, []))
        for oi in owner_cuts:
            pooled_owner.append((symbol, oi))

        # Baseline: spread_expand short
        sigs, _ = collect_signals(featured)
        spread_shorts = [i for i, d in sigs.get("spread_expand_chg8", []) if d < 0]
        for i in spread_shorts:
            pooled_rule["spread_expand_chg8"].append((symbol, i))
            ti = t.iloc[i]
            if t_min is None or ti < t_min:
                t_min = ti
            if t_max is None or ti > t_max:
                t_max = ti
            for ename in EXIT_KEYS:
                settled = _settle_short(featured, i, ename)
                if settled is None:
                    continue
                g, h = settled
                _append(bags["spread_expand_chg8"][ename], g=g, h=h, t=ti)
            # E2 tags on no_tp
            settled_ntp = _settle_short(featured, i, PRIMARY_EXIT)
            if settled_ntp is not None:
                atr = float(featured["atr_pct"].iloc[i])
                if ti in btc.index:
                    btc_ret = float(btc.loc[ti, "btc_ret96"])
                else:
                    pos = btc.index.searchsorted(ti, side="right") - 1
                    btc_ret = float(btc.iloc[pos]["btc_ret96"]) if pos >= 0 else float("nan")
                spread_raw.append(
                    {
                        "gross": settled_ntp[0],
                        "hold": settled_ntp[1],
                        "time": ti,
                        "atr_pct": atr,
                        "btc_ret96": btc_ret,
                        "btc_bucket": _btc_bucket(btc_ret),
                    }
                )

        # E1 rules
        for rname, rule in rules.items():
            fires = _rule_fires(featured, rule)
            for i in fires:
                pooled_rule[rname].append((symbol, i))
                ti = t.iloc[i]
                for ename in EXIT_KEYS:
                    settled = _settle_short(featured, i, ename)
                    if settled is None:
                        continue
                    g, h = settled
                    _append(bags[rname][ename], g=g, h=h, t=ti)
                settled_ntp = _settle_short(featured, i, PRIMARY_EXIT)
                if settled_ntp is not None:
                    atr = float(featured["atr_pct"].iloc[i])
                    if ti in btc.index:
                        btc_ret = float(btc.loc[ti, "btc_ret96"])
                    else:
                        pos = btc.index.searchsorted(ti, side="right") - 1
                        btc_ret = (
                            float(btc.iloc[pos]["btc_ret96"]) if pos >= 0 else float("nan")
                        )
                    e1_raw[rname].append(
                        {
                            "gross": settled_ntp[0],
                            "hold": settled_ntp[1],
                            "time": ti,
                            "atr_pct": atr,
                            "btc_ret96": btc_ret,
                            "btc_bucket": _btc_bucket(btc_ret),
                        }
                    )

        n_sym += 1
        if n_sym % 20 == 0:
            print(f"  scanned {n_sym} symbols …", flush=True)

    # ---------- Overlap ----------
    def pooled_overlap(rule_key: str, window: int) -> dict:
        # Group by symbol
        ow: dict[str, list[int]] = defaultdict(list)
        ru: dict[str, list[int]] = defaultdict(list)
        for s, i in pooled_owner:
            ow[s].append(i)
        for s, i in pooled_rule[rule_key]:
            ru[s].append(i)
        tot_o = tot_r = hit_o = hit_r = 0
        deltas: list[int] = []
        for s in set(ow) | set(ru):
            st = _overlap_stats(ow.get(s, []), ru.get(s, []), window)
            tot_o += st["n_owner"]
            tot_r += st["n_rule"]
            hit_o += st["owner_hit"]
            hit_r += st["rule_hit"]
            if st["median_abs_delta_owner_hits"] is not None and st["owner_hit"]:
                # reconstruct rough deltas via recompute if needed — skip; use count
                pass
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

    overlap_rows = []
    for ek in entry_names:
        for w in MATCH_WINDOWS:
            row = pooled_overlap(ek, w)
            row["entry"] = ek
            overlap_rows.append(row)

    # Headline overlap @18
    overlap_headline = {
        ek: next(
            r for r in overlap_rows if r["entry"] == ek and r["window"] == OVERLAP_WINDOW
        )
        for ek in entry_names
    }

    # ---------- E1 PF rows ----------
    e1_main = []
    for ek in entry_names:
        ov = overlap_headline[ek]
        for ename in EXIT_KEYS:
            blk = _pf_block(bags[ek][ename]["gross"], bags[ek][ename]["hold"])
            m04 = _month_pf(bags[ek][ename], "2026-04")
            e1_main.append(
                {
                    "panel": "E1",
                    "entry": ek,
                    "exit": ename,
                    "n": blk["maker"]["n"],
                    "pf_maker": blk["maker"]["profit_factor"],
                    "sum_net_maker": blk["maker"]["sum_net"],
                    "win_rate": blk["maker"]["win_rate"],
                    "pf_legacy": blk["legacy"]["profit_factor"],
                    "owner_recall_w18": ov["owner_recall"],
                    "jaccard_w18": ov["jaccard_approx"],
                    "rule_precision_w18": ov["rule_precision_vs_owner"],
                    "n_owner_w18": ov["n_owner"],
                    "n_rule_overlap_w18": ov["n_rule"],
                    "pf_2026_04_maker": m04["profit_factor"],
                    "n_2026_04": m04["n"],
                    "sum_net_2026_04": m04["sum_net"],
                    "pass_pf_1_3": (
                        blk["maker"]["profit_factor"] is not None
                        and blk["maker"]["profit_factor"] >= SUCCESS_PF
                    ),
                    "pass_overlap_line": (
                        (ov["owner_recall"] or 0) >= OVERLAP_RECALL_LINE
                        or (ov["jaccard_approx"] or 0) >= OVERLAP_JACCARD_LINE
                    ),
                }
            )

    # Pick best E1 by (overlap pass, then pf@no_tp, then recall)
    e1_ntp = [r for r in e1_main if r["exit"] == PRIMARY_EXIT and r["entry"] != "spread_expand_chg8"]
    def _e1_rank(r: dict):
        return (
            int(bool(r["pass_overlap_line"])),
            r["pf_maker"] if r["pf_maker"] is not None else -1.0,
            r["owner_recall_w18"] if r["owner_recall_w18"] is not None else -1.0,
        )

    best_e1 = max(e1_ntp, key=_e1_rank) if e1_ntp else None
    best_e1_name = best_e1["entry"] if best_e1 else None

    # ---------- E2 regime gates ----------
    atr_arr = np.asarray([r["atr_pct"] for r in spread_raw], dtype=float)
    atr_med = float(np.nanmedian(atr_arr)) if len(atr_arr) else float("nan")
    atr_q75 = float(np.nanpercentile(atr_arr, 75)) if len(atr_arr) >= 50 else float("nan")

    def gate_slice(raw: list[dict], predicate) -> dict[str, list]:
        bag = _empty()
        for r in raw:
            if predicate(r):
                _append(bag, g=r["gross"], h=r["hold"], t=r["time"])
        return bag

    e2_defs = [
        ("spread_no_gate", lambda r: True),
        ("spread_not_btc_up", lambda r: r["btc_bucket"] != "btc_up"),
        (
            "spread_atr_ge_med",
            lambda r: np.isfinite(r["atr_pct"]) and r["atr_pct"] >= atr_med,
        ),
        (
            "spread_atr_q34",
            lambda r: np.isfinite(r["atr_pct"])
            and np.isfinite(atr_q75)
            and r["atr_pct"] >= atr_med,  # q3∪q4 ≈ >= median of fire atrs
        ),
    ]
    # atr_ge_med and atr_q34 identical by construction (q3∪q4 = >=p50); keep one name
    e2_defs = [
        ("spread_no_gate", lambda r: True),
        ("spread_not_btc_up", lambda r: r["btc_bucket"] != "btc_up"),
        (
            "spread_atr_q34",
            lambda r: np.isfinite(r["atr_pct"])
            and np.isfinite(atr_med)
            and r["atr_pct"] >= atr_med,
        ),
    ]

    e2_main = []
    for name, pred in e2_defs:
        bag = gate_slice(spread_raw, pred)
        blk = _pf_block(bag["gross"], bag["hold"])
        m04 = _month_pf(bag, "2026-04")
        e2_main.append(
            {
                "panel": "E2",
                "gate": name,
                "base_entry": "spread_expand_chg8",
                "exit": PRIMARY_EXIT,
                "n": blk["maker"]["n"],
                "pf_maker": blk["maker"]["profit_factor"],
                "sum_net_maker": blk["maker"]["sum_net"],
                "win_rate": blk["maker"]["win_rate"],
                "pf_legacy": blk["legacy"]["profit_factor"],
                "pf_2026_04_maker": m04["profit_factor"],
                "n_2026_04": m04["n"],
                "sum_net_2026_04": m04["sum_net"],
                "n_frac_of_ungated": (
                    round(blk["maker"]["n"] / max(1, len(spread_raw)), 4)
                    if blk["maker"]["n"]
                    else 0.0
                ),
                "pass_pf_1_3": (
                    blk["maker"]["profit_factor"] is not None
                    and blk["maker"]["profit_factor"] >= SUCCESS_PF
                ),
            }
        )

    # Optional: best E1 + not_btc_up (separate control row)
    e2_on_e1 = None
    if best_e1_name and e1_raw.get(best_e1_name):
        raw = e1_raw[best_e1_name]
        for gname, pred in (
            (f"{best_e1_name}_no_gate", lambda r: True),
            (f"{best_e1_name}_not_btc_up", lambda r: r["btc_bucket"] != "btc_up"),
            (
                f"{best_e1_name}_atr_q34",
                lambda r, med=float(np.nanmedian([x["atr_pct"] for x in raw])): (
                    np.isfinite(r["atr_pct"]) and r["atr_pct"] >= med
                ),
            ),
        ):
            bag = gate_slice(raw, pred)
            blk = _pf_block(bag["gross"], bag["hold"])
            m04 = _month_pf(bag, "2026-04")
            row = {
                "panel": "E2_on_E1_control",
                "gate": gname,
                "base_entry": best_e1_name,
                "exit": PRIMARY_EXIT,
                "n": blk["maker"]["n"],
                "pf_maker": blk["maker"]["profit_factor"],
                "sum_net_maker": blk["maker"]["sum_net"],
                "win_rate": blk["maker"]["win_rate"],
                "pf_legacy": blk["legacy"]["profit_factor"],
                "pf_2026_04_maker": m04["profit_factor"],
                "n_2026_04": m04["n"],
                "sum_net_2026_04": m04["sum_net"],
                "n_frac_of_ungated": (
                    round(blk["maker"]["n"] / max(1, len(raw)), 4)
                    if blk["maker"]["n"]
                    else 0.0
                ),
                "pass_pf_1_3": (
                    blk["maker"]["profit_factor"] is not None
                    and blk["maker"]["profit_factor"] >= SUCCESS_PF
                ),
                "note": "control only — do not claim E1×E2 combo success",
            }
            e2_main.append(row)
            if e2_on_e1 is None:
                e2_on_e1 = []
            e2_on_e1.append(row)

    # Long control (optional thin): spread_expand long under baseline — skip heavy;
    # report short-only as primary per owner request.

    # ---------- Verdicts ----------
    base_ov = overlap_headline["spread_expand_chg8"]
    e1_verdict = {
        "question": (
            "Do owner-fit causal short rules lift overlap vs spread_expand AND "
            "still show deployable causal PF@maker?"
        ),
        "baseline_overlap_w18": {
            "owner_recall": base_ov["owner_recall"],
            "jaccard": base_ov["jaccard_approx"],
        },
        "success_line_overlap": {
            "owner_recall>=": OVERLAP_RECALL_LINE,
            "or_jaccard>=": OVERLAP_JACCARD_LINE,
        },
        "success_line_pf": SUCCESS_PF,
        "best_e1": best_e1,
        "honesty": (
            "Rules fitted on owner short moments → train overlap is optimistic; "
            "verdict requires causal full-scan PF, not recall alone."
        ),
    }
    if best_e1:
        lift_recall = (best_e1["owner_recall_w18"] or 0) - (base_ov["owner_recall"] or 0)
        lift_j = (best_e1["jaccard_w18"] or 0) - (base_ov["jaccard_approx"] or 0)
        e1_verdict["overlap_lift"] = {
            "delta_recall": round(lift_recall, 4),
            "delta_jaccard": round(lift_j, 4),
            "overlap_pass": bool(best_e1["pass_overlap_line"]),
        }
        e1_verdict["causal_edge"] = {
            "pf_maker_no_tp": best_e1["pf_maker"],
            "pf_2026_04": best_e1["pf_2026_04_maker"],
            "pass_1_3": bool(best_e1["pass_pf_1_3"]),
            "dead": (
                best_e1["pf_maker"] is None
                or best_e1["pf_maker"] < 1.05
                or (
                    best_e1["pf_2026_04_maker"] is not None
                    and best_e1["pf_2026_04_maker"] < 0.9
                    and best_e1["pf_maker"] < SUCCESS_PF
                )
            ),
        }
        # Chinese-ready flags for report
        e1_verdict["cn_flags"] = {
            "raised_overlap": lift_recall > 0.05 or lift_j > 0.02,
            "causal_edge_still_dead": bool(e1_verdict["causal_edge"]["dead"])
            or not best_e1["pass_pf_1_3"],
        }

    ungated = next(r for r in e2_main if r["gate"] == "spread_no_gate")
    e2_verdict = {
        "question": (
            "Does a single regime gate on spread_expand short lift PF / fix 2026-04 "
            "without deleting the sample?"
        ),
        "ungated": ungated,
        "gates": [r for r in e2_main if r["panel"] == "E2" and r["gate"] != "spread_no_gate"],
        "kill_sample_line": "n_frac < 0.25 with only bad-month deleted → suspect",
    }
    # Flag: only cuts bad month but kills sample / no overall lift
    flags = []
    for g in e2_verdict["gates"]:
        d_pf = (g["pf_maker"] or 0) - (ungated["pf_maker"] or 0)
        d04 = (g["pf_2026_04_maker"] or 0) - (ungated["pf_2026_04_maker"] or 0)
        flags.append(
            {
                "gate": g["gate"],
                "delta_pf": round(d_pf, 3),
                "delta_2026_04": round(d04, 3),
                "n_frac": g["n_frac_of_ungated"],
                "only_cuts_bad_month": d04 > 0.15 and d_pf < 0.05,
                "kills_sample": g["n_frac_of_ungated"] < 0.25,
            }
        )
    e2_verdict["gate_flags"] = flags

    payload = {
        "tag": tag,
        "holdout": "FORBIDDEN — train open_time < 2026-05-04 only",
        "n_symbols": n_sym,
        "n_fit_symbols_with_owner": n_fit_sym,
        "time_range": {"min": str(t_min), "max": str(t_max)},
        "n_owner_short_train": int(len(owner_all)),
        "fit": {
            "n_pos": int(len(pos_df)),
            "n_neg": int(len(neg_df)),
            "auc_disclosure_only": round(auc, 4),
            "top_gains": [{"feature": n, "gain": g} for n, g in gains[:15]],
            "rules": {
                k: {
                    "text": _rule_to_text(v),
                    "clauses": v.get("clauses"),
                    "extra": {
                        kk: vv
                        for kk, vv in v.items()
                        if kk not in ("logic", "clauses")
                    },
                }
                for k, v in rules.items()
            },
        },
        "E1_verdict": e1_verdict,
        "E2_verdict": e2_verdict,
        "atr_gate_thresholds": {
            "atr_med_of_spread_fires": atr_med,
            "atr_p75_of_spread_fires": atr_q75,
            "note": "atr_q34 = atr_pct >= median of ungated spread short fires",
        },
        "elapsed_sec": round(time.time() - t0, 1),
        "costs": {"maker": FORWARD_COST, "legacy": LEGACY_P0_ROUND_TRIP},
        "exits": list(EXIT_KEYS),
        "overlap_windows": list(MATCH_WINDOWS),
    }

    json_path = out_dir / f"{tag}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str))
    _write_csv(out_dir / f"{tag}_E1_main.csv", e1_main)
    _write_csv(out_dir / f"{tag}_E2_main.csv", e2_main)
    _write_csv(out_dir / f"{tag}_overlap.csv", overlap_rows)

    print("\n=== E1 headline (no_tp) ===", flush=True)
    for r in e1_main:
        if r["exit"] != PRIMARY_EXIT:
            continue
        print(
            f"  {r['entry']}: n={r['n']} PF@m={r['pf_maker']} "
            f"recall18={r['owner_recall_w18']} J={r['jaccard_w18']} "
            f"2026-04 PF={r['pf_2026_04_maker']}",
            flush=True,
        )
    print("=== E2 headline ===", flush=True)
    for r in e2_main:
        if r["panel"] != "E2":
            continue
        print(
            f"  {r['gate']}: n={r['n']} ({r['n_frac_of_ungated']:.0%}) "
            f"PF@m={r['pf_maker']} 2026-04={r['pf_2026_04_maker']}",
            flush=True,
        )
    print(f"wrote {json_path}  elapsed={payload['elapsed_sec']}s", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=20)
    ap.add_argument("--tag", default="entry_align_and_regime")
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    return run(
        n_symbols=args.n_symbols,
        tag=args.tag,
        sheet=args.sheet,
        seed=args.seed,
    )


if __name__ == "__main__":
    raise SystemExit(main())
