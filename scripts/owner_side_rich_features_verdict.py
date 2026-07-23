#!/usr/bin/env python3
"""Expanded-feature owner-side disclosure + causal triple-barrier verdict.

Owner critique: narrow FEATURE_COLUMNS reuse was an artificial ceiling. This
round factorizes as much as local OHLCV allows (see owner_side_rich_features),
then re-runs the same per-side protocol:

  1. Load review_sheet long/short (skip dropped).
  2. LGBM gain disclosure on rich market features (AUC = disclosure only).
  3. Interpretable AND causal rule from top gains (no box geometry).
  4. Full-market train scan (<2026-05-04) + TP5/SL2/72 triple-barrier.
  5. Optional walk-forward score-threshold on labeled samples — marked
     non-deployable; main verdict remains causal-rule PF.

Success: per-side causal-rule PF @ SWAP maker ≥ 1.3.
Else: honest "扩特征仍未救出可部署边".

Usage:
  PYTHONPATH=. .venv/bin/python scripts/owner_side_rich_features_verdict.py
  PYTHONPATH=. .venv/bin/python scripts/owner_side_rich_features_verdict.py \\
      --n-symbols 0 --tag owner_side_rich_features_verdict
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.owner_label_feature_verdict import (  # noqa: E402
    EMERGENCE_PF_MAKER,
    HOLDOUT_START,
    MIN_GAP_BARS,
    SL_MULT,
    TP_MULT,
    WARMUP,
    stats,
)
from scripts.owner_side_rich_features import (  # noqa: E402
    BOX_FEATS,
    add_rich_features,
    feature_group,
    rich_feature_columns,
)
from src.costs import FORWARD_COST, LEGACY_P0_ROUND_TRIP  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.labeling import (  # noqa: E402
    HORIZON_BARS,
    label_candidate,
    label_short_candidate,
)

DEFAULT_SHEET = PROJECT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"
SUCCESS_PF = 1.3
VALID = frozenset({"long", "short"})
NARROW_REF = {
    "long_pf_maker": 0.917,
    "short_pf_maker": 1.127,
    "source": "analysis/p_owner_side_feature_verdict.md",
}


def _resolve_gross(frame: pd.DataFrame, signal_i: int, side: str) -> float | None:
    if side == "long":
        out = label_candidate(frame, signal_i, tp_mult=TP_MULT, sl_mult=SL_MULT)
    else:
        out = label_short_candidate(frame, signal_i, tp_mult=TP_MULT, sl_mult=SL_MULT)
    if out is None:
        return None
    return float(out.realized_ret)


def _load_labeled(sheet: Path) -> tuple[pd.DataFrame, int, int]:
    if not sheet.exists():
        raise SystemExit(f"ERROR: sheet missing: {sheet}")
    df = pd.read_csv(sheet, dtype=str).fillna("")
    if "owner_side" not in df.columns or "box_id" not in df.columns:
        raise SystemExit("ERROR: sheet needs box_id + owner_side")
    df["owner_side"] = df["owner_side"].str.strip().str.lower()
    labeled = df[df["owner_side"].isin(VALID)].copy()
    n_skip = int((df["owner_side"] == "skip").sum())
    n_empty = int((~df["owner_side"].isin(VALID | {"skip"})).sum())
    if len(labeled) == 0:
        raise SystemExit(
            f"ERROR: 0 行已标注 side。sheet={sheet} empty={n_empty} skip={n_skip}"
        )
    for c in ("cut_global", "width_bars", "box_height_pct", "box_right_frac"):
        if c in labeled.columns:
            labeled[c] = pd.to_numeric(labeled[c], errors="coerce")
    labeled["open_time"] = labeled["cut_time"]
    labeled["symbol_body"] = labeled["symbol"]
    return labeled, n_skip, n_empty


def _side_block(gross: list[float]) -> dict:
    g = np.asarray(gross, dtype=float)
    if not len(g):
        empty = stats(g)
        return {
            "maker_0.06pct": empty,
            "legacy_0.20pct": empty,
            "gross_pre_cost": empty,
        }
    return {
        "maker_0.06pct": stats(g - FORWARD_COST),
        "legacy_0.20pct": stats(g - LEGACY_P0_ROUND_TRIP),
        "gross_pre_cost": stats(g),
    }


def _feat_frame(df: pd.DataFrame) -> pd.DataFrame:
    return add_rich_features(df)


def _get_featured(
    cache: dict[str, pd.DataFrame],
    body: str,
    df: pd.DataFrame,
) -> pd.DataFrame | None:
    """Train-only featured frame, cached per symbol body."""
    if body in cache:
        return cache[body]
    times = pd.to_datetime(df["open_time"], utc=True)
    df_tr = df[times < HOLDOUT_START].reset_index(drop=True)
    if len(df_tr) < WARMUP + HORIZON_BARS + 50:
        cache[body] = None  # type: ignore[assignment]
        return None
    featured = _feat_frame(df_tr)
    cache[body] = featured
    return featured


def sample_feature_row(
    featured: pd.DataFrame,
    i: int,
    feat_cols: list[str],
    box: dict | None = None,
) -> dict | None:
    if i < 0 or i >= len(featured):
        return None
    row = featured.iloc[i]
    out: dict = {"cut_global": int(i), "open_time": str(row.get("open_time", ""))}
    for c in feat_cols:
        v = row.get(c, np.nan)
        out[c] = float(v) if pd.notna(v) else np.nan
    if box is not None:
        for c in BOX_FEATS:
            out[c] = float(box[c])
    else:
        for c in BOX_FEATS:
            out[c] = np.nan
    # helpers for dense-neg sampling / diagnostics
    for c in ("atr_pct", "fast_spread", "full_spread"):
        out[c] = float(row[c]) if pd.notna(row.get(c)) else np.nan
    return out


def build_causal_rule(
    pos_df: pd.DataFrame,
    neg_df: pd.DataFrame,
    gains: list[tuple[str, float]],
    feat_cols: list[str],
    *,
    top_k: int = 5,
) -> dict:
    """AND-rule from top gains; thresholds from pos quartile toward neg."""
    gain_map = {n: g for n, g in gains}
    ranked = [n for n, g in gains if n in feat_cols and g > 1e-6]
    if len(ranked) < top_k:
        gaps: list[tuple[str, float]] = []
        for name in feat_cols:
            p = pos_df[name].dropna()
            n = neg_df[name].dropna()
            if len(p) < 30 or len(n) < 30:
                continue
            scale = float(pd.concat([p, n]).std()) or 1e-12
            gaps.append((name, abs(float(p.median()) - float(n.median())) / scale))
        gaps.sort(key=lambda x: -x[1])
        for name, _ in gaps:
            if name not in ranked:
                ranked.append(name)
            if len(ranked) >= max(top_k, 10):
                break

    clauses: list[dict] = []
    for name in ranked:
        p = pos_df[name].dropna()
        n = neg_df[name].dropna()
        if len(p) < 30 or len(n) < 30:
            continue
        p_med, n_med = float(p.median()), float(n.median())
        if p_med <= n_med:
            thr = float(p.quantile(0.75))
            op = "<="
        else:
            thr = float(p.quantile(0.25))
            op = ">="
        clauses.append(
            {
                "feature": name,
                "group": feature_group(name),
                "op": op,
                "threshold": thr,
                "gain": float(gain_map.get(name, 0.0)),
                "pos_median": p_med,
                "neg_median": n_med,
            }
        )
        if len(clauses) >= top_k:
            break
    return {"logic": "AND", "clauses": clauses}


def rule_mask(featured: pd.DataFrame, rule: dict) -> np.ndarray:
    mask = np.ones(len(featured), dtype=bool)
    for c in rule["clauses"]:
        col = featured[c["feature"]].to_numpy(dtype=float)
        thr = float(c["threshold"])
        if c["op"] == "<=":
            mask &= np.isfinite(col) & (col <= thr)
        else:
            mask &= np.isfinite(col) & (col >= thr)
    return mask


def _train_lgbm(
    train_df: pd.DataFrame,
    feat_cols: list[str],
    *,
    seed: int,
) -> tuple[lgb.Booster, float, list[tuple[str, float]], np.ndarray]:
    X = train_df[feat_cols].to_numpy(dtype=float)
    y = train_df["label"].to_numpy(dtype=int)
    order = np.argsort(pd.to_datetime(train_df["open_time"], utc=True).to_numpy())
    cut = int(len(order) * 0.7)
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
    all_pred = booster.predict(X)
    return booster, auc, gains, all_pred


def _walkforward_score_filter(
    train_df: pd.DataFrame,
    feat_cols: list[str],
    series_by_sym: dict[str, pd.DataFrame],
    feat_cache: dict[str, pd.DataFrame],
    side: str,
    *,
    seed: int,
) -> dict:
    """Time-ordered score threshold on labeled+neg rows — NOT deployable.

    Train LGBM on first 60% by time; on last 40% pick score quantile that
    maximizes maker PF among scored positives settled on `side`. Report only.
    """
    order = np.argsort(pd.to_datetime(train_df["open_time"], utc=True).to_numpy())
    cut = int(len(order) * 0.6)
    tr_idx, te_idx = order[:cut], order[cut:]
    if len(tr_idx) < 80 or len(te_idx) < 40:
        return {"deployable": False, "error": "too few rows for walk-forward"}

    X = train_df[feat_cols].to_numpy(dtype=float)
    y = train_df["label"].to_numpy(dtype=int)
    dtrain = lgb.Dataset(X[tr_idx], label=y[tr_idx], feature_name=feat_cols)
    params = {
        "objective": "binary",
        "metric": "auc",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_data_in_leaf": 30,
        "verbosity": -1,
        "seed": seed,
    }
    booster = lgb.train(params, dtrain, num_boost_round=150)
    scores = booster.predict(X[te_idx])
    te = train_df.iloc[te_idx].copy()
    te["score"] = scores

    # Settle only labeled positives in the test fold
    pos_te = te[te["label"] == 1]
    gross_by_q: dict[str, list[float]] = {}
    best = {"q": None, "pf_maker": None, "n": 0}
    for q in (0.5, 0.6, 0.7, 0.8, 0.9):
        thr = float(np.quantile(scores, q))
        sel = pos_te[pos_te["score"] >= thr]
        g_list: list[float] = []
        for _, r in sel.iterrows():
            body = str(r["symbol"])
            df = series_by_sym.get(body)
            if df is None:
                continue
            featured = _get_featured(feat_cache, body, df)
            if featured is None:
                continue
            cut_i = int(r["cut_global"])
            if cut_i < 0 or cut_i >= len(featured):
                continue
            g = _resolve_gross(featured, cut_i, side)
            if g is not None:
                g_list.append(g)
        block = _side_block(g_list)
        pf = (block["maker_0.06pct"] or {}).get("profit_factor")
        gross_by_q[str(q)] = {
            "threshold": thr,
            "n": int(len(g_list)),
            "pf_maker": pf,
        }
        if pf is not None and (best["pf_maker"] is None or pf > best["pf_maker"]):
            best = {"q": q, "pf_maker": pf, "n": len(g_list), "threshold": thr}

    return {
        "deployable": False,
        "reason": (
            "分数阈值在已标样本 walk-forward 上挑选；"
            "未做全市场因果扫描，且正样本本身含确认态选点 —— 不可部署"
        ),
        "by_quantile": gross_by_q,
        "best": best,
    }


def collect_side_rows(
    side: str,
    pos_items: pd.DataFrame,
    *,
    neg_ratio: float,
    seed: int,
    series_by_sym: dict[str, pd.DataFrame],
    feat_cols: list[str],
    feat_cache: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    rng = np.random.default_rng(seed + (1 if side == "long" else 2))
    by_body: dict[str, list[dict]] = defaultdict(list)
    for _, r in pos_items.iterrows():
        by_body[str(r["symbol"])].append(r.to_dict())

    pos_rows: list[dict] = []
    neg_rows: list[dict] = []
    used_cols: list[str] | None = None

    for body, items in by_body.items():
        df = series_by_sym.get(body)
        if df is None:
            continue
        featured = _get_featured(feat_cache, body, df)
        if featured is None:
            continue
        if used_cols is None:
            used_cols = rich_feature_columns(featured)
            # prefer caller's feat_cols intersect
            used_cols = [c for c in feat_cols if c in featured.columns] or used_cols
        tt = pd.to_datetime(featured["open_time"], utc=True)
        label_cuts: set[int] = set()
        for it in items:
            t = pd.Timestamp(it["cut_time"])
            if t.tzinfo is None:
                t = t.tz_localize("UTC")
            else:
                t = t.tz_convert("UTC")
            hits = np.where(tt == t)[0]
            if len(hits) == 0:
                diffs = (tt - t).total_seconds().to_numpy()
                j = int(np.argmin(np.abs(diffs)))
                if abs(diffs[j]) > 15 * 60:
                    continue
                cut_i = j
            else:
                cut_i = int(hits[0])
            label_cuts.add(cut_i)
            box = {
                "box_width_bars": float(it.get("width_bars") or np.nan),
                "box_height_pct": float(it.get("box_height_pct") or np.nan),
                "box_right_frac": float(it.get("box_right_frac") or np.nan),
            }
            feat = sample_feature_row(featured, cut_i, used_cols, box)
            if feat is None or not np.isfinite(feat.get("ma_spread_pct", np.nan)):
                continue
            feat["label"] = 1
            feat["symbol"] = body
            feat["stem"] = it.get("stem", "")
            pos_rows.append(feat)

        if not label_cuts:
            continue
        banned = set()
        for c in label_cuts:
            for d in range(-MIN_GAP_BARS, MIN_GAP_BARS + 1):
                banned.add(c + d)
        pool = [
            i
            for i in range(WARMUP, len(featured) - HORIZON_BARS - 2)
            if i not in banned
        ]
        n_neg = min(len(pool), max(1, int(len(label_cuts) * neg_ratio)))
        if pool and n_neg:
            for i in rng.choice(pool, size=n_neg, replace=False):
                feat = sample_feature_row(featured, int(i), used_cols, None)
                if feat is None or not np.isfinite(feat.get("ma_spread_pct", np.nan)):
                    continue
                feat["label"] = 0
                feat["symbol"] = body
                feat["stem"] = ""
                neg_rows.append(feat)

    return pd.DataFrame(pos_rows), pd.DataFrame(neg_rows), (used_cols or feat_cols)


def run_side(
    side: str,
    pos_items: pd.DataFrame,
    *,
    neg_ratio: float,
    top_k: int,
    n_symbols: int,
    seed: int,
    series_by_sym: dict[str, pd.DataFrame],
    feat_cols: list[str],
    feat_cache: dict[str, pd.DataFrame],
    do_walkforward: bool,
) -> dict:
    t0 = time.time()
    pos_df, neg_df, used_cols = collect_side_rows(
        side,
        pos_items,
        neg_ratio=neg_ratio,
        seed=seed,
        series_by_sym=series_by_sym,
        feat_cols=feat_cols,
        feat_cache=feat_cache,
    )
    if len(pos_df) < 20 or len(neg_df) < 20:
        return {
            "side": side,
            "error": f"insufficient rows pos={len(pos_df)} neg={len(neg_df)}",
            "n_labeled_boxes": int(len(pos_items)),
            "pos_feature_rows": int(len(pos_df)),
            "n_features": len(used_cols),
        }

    train_df = pd.concat([pos_df, neg_df], ignore_index=True)
    booster, auc, gains, _ = _train_lgbm(train_df, used_cols, seed=seed)
    rule = build_causal_rule(pos_df, neg_df, gains, used_cols, top_k=top_k)

    # Box geometry gain disclosure (separate model, not for rules)
    box_gain = []
    box_present = all(c in pos_df.columns for c in BOX_FEATS) and pos_df[list(BOX_FEATS)].notna().any().any()
    if box_present:
        disc_cols = used_cols + list(BOX_FEATS)
        # Negatives have NaN box → fill with train pos medians so LGBM can run;
        # this is disclosure-only and must not be used for rules.
        disc_df = train_df.copy()
        for c in BOX_FEATS:
            med = float(pos_df[c].median())
            disc_df[c] = disc_df[c].fillna(med)
        _, _, box_gains, _ = _train_lgbm(disc_df, disc_cols, seed=seed + 7)
        box_gain = [
            {"feature": n, "gain": round(g, 2), "group": feature_group(n)}
            for n, g in box_gains[:15]
        ]

    # Owner-cut oracle on this side
    by_body: dict[str, list[dict]] = defaultdict(list)
    for _, r in pos_items.iterrows():
        by_body[str(r["symbol"])].append(r.to_dict())
    owner_gross: list[float] = []
    for body, items in by_body.items():
        df = series_by_sym.get(body)
        if df is None:
            continue
        featured = _get_featured(feat_cache, body, df)
        if featured is None:
            continue
        tt = pd.to_datetime(featured["open_time"], utc=True)
        cuts: list[int] = []
        for it in items:
            t = pd.Timestamp(it["cut_time"])
            if t.tzinfo is None:
                t = t.tz_localize("UTC")
            else:
                t = t.tz_convert("UTC")
            hits = np.where(tt == t)[0]
            if len(hits):
                cuts.append(int(hits[0]))
        cuts = sorted(set(cuts))
        last = -10**9
        for cut_i in cuts:
            if cut_i - last < MIN_GAP_BARS:
                continue
            last = cut_i
            g = _resolve_gross(featured, cut_i, side)
            if g is not None:
                owner_gross.append(g)

    # Causal rule full-market scan
    nets_g: list[float] = []
    n_sym = 0
    n_fires = 0
    if rule.get("clauses"):
        for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
            if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
                continue
            if is_eval_symbol(symbol):
                continue
            featured = _get_featured(feat_cache, symbol, frame)
            if featured is None:
                continue
            mask = rule_mask(featured, rule)
            idx = np.flatnonzero(mask)
            idx = idx[(idx >= WARMUP) & (idx < len(featured) - 1)]
            deduped: list[int] = []
            for i in idx.tolist():
                if not deduped or i - deduped[-1] >= MIN_GAP_BARS:
                    deduped.append(i)
            n_fires += len(deduped)
            for i in deduped:
                g = _resolve_gross(featured, i, side)
                if g is not None:
                    nets_g.append(g)
            n_sym += 1
            if n_symbols and n_sym >= n_symbols:
                break

    wf = None
    if do_walkforward:
        wf = _walkforward_score_filter(
            train_df, used_cols, series_by_sym, feat_cache, side, seed=seed
        )

    owner_block = _side_block(owner_gross)
    rule_block = _side_block(nets_g)
    pf_m = (rule_block["maker_0.06pct"] or {}).get("profit_factor")
    pf_owner = (owner_block["maker_0.06pct"] or {}).get("profit_factor")
    pass_line = pf_m is not None and float(pf_m) >= SUCCESS_PF

    # Grouped gain summary
    group_gain: dict[str, float] = defaultdict(float)
    for n, g in gains:
        group_gain[feature_group(n)] += g

    return {
        "side": side,
        "n_labeled_boxes": int(len(pos_items)),
        "pos_feature_rows": int(len(pos_df)),
        "random_neg": int(len(neg_df)),
        "n_features": len(used_cols),
        "feature_list": used_cols,
        "lgbm_val_auc": round(auc, 4),
        "feature_gain_top": [
            {
                "feature": n,
                "gain": round(g, 2),
                "group": feature_group(n),
            }
            for n, g in gains[:20]
        ],
        "feature_gain_by_group": {
            k: round(v, 2) for k, v in sorted(group_gain.items(), key=lambda x: -x[1])
        },
        "box_geometry_disclosure_top": box_gain,
        "causal_rule": rule,
        "owner_cut_oracle": owner_block,
        "causal_rule_scan": {
            **rule_block,
            "symbols_scanned": n_sym,
            "raw_deduped_fires": n_fires,
        },
        "walkforward_score_filter": wf,
        "success_line": {
            "metric": "causal_rule_PF_maker",
            "threshold": SUCCESS_PF,
            "value": pf_m,
            "pass": pass_line,
        },
        "oracle_pf_maker": pf_owner,
        "delta_vs_narrow": (
            round(float(pf_m) - NARROW_REF[f"{side}_pf_maker"], 3)
            if pf_m is not None
            else None
        ),
        "delta_vs_emergence": (
            round(float(pf_m) - EMERGENCE_PF_MAKER, 3) if pf_m is not None else None
        ),
        "elapsed_sec": round(time.time() - t0, 1),
        "_booster_ref": booster,  # stripped before JSON write
    }


def long_vs_short_disclosure(
    labeled: pd.DataFrame,
    series_by_sym: dict[str, pd.DataFrame],
    feat_cols: list[str],
    feat_cache: dict[str, pd.DataFrame],
    *,
    seed: int,
) -> dict:
    """Optional: which rich features separate owner long vs short (disclosure)."""
    rows: list[dict] = []
    used: list[str] | None = None
    for _, r in labeled.iterrows():
        body = str(r["symbol"])
        df = series_by_sym.get(body)
        if df is None:
            continue
        featured = _get_featured(feat_cache, body, df)
        if featured is None:
            continue
        if used is None:
            used = [c for c in feat_cols if c in featured.columns]
        tt = pd.to_datetime(featured["open_time"], utc=True)
        t = pd.Timestamp(r["cut_time"])
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        else:
            t = t.tz_convert("UTC")
        hits = np.where(tt == t)[0]
        if not len(hits):
            continue
        feat = sample_feature_row(featured, int(hits[0]), used, None)
        if feat is None:
            continue
        feat["label"] = 1 if r["owner_side"] == "long" else 0
        feat["symbol"] = body
        rows.append(feat)
    if len(rows) < 80:
        return {"error": f"too few rows {len(rows)}"}
    df = pd.DataFrame(rows)
    assert used is not None
    _, auc, gains, _ = _train_lgbm(df, used, seed=seed)
    return {
        "n": int(len(df)),
        "n_long": int((df["label"] == 1).sum()),
        "n_short": int((df["label"] == 0).sum()),
        "lgbm_val_auc": round(auc, 4),
        "note": "long=1 vs short=0 among owner labels only; disclosure, not a rule",
        "feature_gain_top": [
            {"feature": n, "gain": round(g, 2), "group": feature_group(n)}
            for n, g in gains[:15]
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    ap.add_argument("--neg-ratio", type=float, default=3.0)
    ap.add_argument("--n-symbols", type=int, default=0, help="0=all SWAP")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--tag", default="owner_side_rich_features_verdict")
    ap.add_argument("--seed", type=int, default=20260723)
    ap.add_argument("--no-walkforward", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    labeled, n_skip, n_empty = _load_labeled(args.sheet)
    n_long = int((labeled["owner_side"] == "long").sum())
    n_short = int((labeled["owner_side"] == "short").sum())
    print(
        f"labeled long={n_long} short={n_short} skip={n_skip} empty={n_empty} "
        f"sheet={args.sheet}"
    )
    if args.dry_run:
        # Probe feature count on one series
        for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
            if source != "okx" or not symbol.endswith("_USDT_SWAP"):
                continue
            times = pd.to_datetime(frame["open_time"], utc=True)
            frame = frame[times < HOLDOUT_START].reset_index(drop=True)
            feat = _feat_frame(frame)
            cols = rich_feature_columns(feat)
            print(f"dry-run features={len(cols)} sample_symbol={symbol}")
            print("groups:", sorted({feature_group(c) for c in cols}))
            break
        return 0

    need = set(labeled["symbol"].unique())
    series_by_sym: dict[str, pd.DataFrame] = {}
    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if symbol in need:
            series_by_sym[symbol] = frame
    missing = sorted(need - set(series_by_sym))
    if missing:
        print(f"WARN: missing series for {len(missing)} symbols e.g. {missing[:5]}")

    # Establish feature column list from one featured frame
    feat_cache: dict[str, pd.DataFrame] = {}
    probe_sym, probe = next(iter(series_by_sym.items()))
    probe_feat = _get_featured(feat_cache, probe_sym, probe)
    assert probe_feat is not None
    feat_cols = rich_feature_columns(probe_feat)
    print(f"rich feature count={len(feat_cols)}", flush=True)

    results: dict = {}
    for side in ("long", "short"):
        sub = labeled[labeled["owner_side"] == side]
        if len(sub) == 0:
            results[side] = {
                "side": side,
                "n_labeled_boxes": 0,
                "error": "no boxes",
                "success_line": {"pass": False, "threshold": SUCCESS_PF, "value": None},
            }
            continue
        print(f"[{side}] running n_boxes={len(sub)} features={len(feat_cols)} …", flush=True)
        results[side] = run_side(
            side,
            sub,
            neg_ratio=args.neg_ratio,
            top_k=args.top_k,
            n_symbols=args.n_symbols,
            seed=args.seed,
            series_by_sym=series_by_sym,
            feat_cols=feat_cols,
            feat_cache=feat_cache,
            do_walkforward=not args.no_walkforward,
        )
        results[side].pop("_booster_ref", None)
        sl = results[side].get("success_line", {})
        print(
            f"[{side}] rule_PF_maker={sl.get('value')} "
            f"pass>={SUCCESS_PF}? {sl.get('pass')} "
            f"oracle_PF={results[side].get('oracle_pf_maker')} "
            f"elapsed={results[side].get('elapsed_sec')}s",
            flush=True,
        )

    print("[long_vs_short] disclosure …", flush=True)
    lvs = long_vs_short_disclosure(
        labeled, series_by_sym, feat_cols, feat_cache, seed=args.seed + 99
    )
    print(
        f"[long_vs_short] auc={lvs.get('lgbm_val_auc')} top={lvs.get('feature_gain_top', [])[:3]}",
        flush=True,
    )

    table = []
    for side in ("long", "short"):
        r = results[side]
        rb = (r.get("causal_rule_scan") or {}).get("maker_0.06pct") or {}
        ob = (r.get("owner_cut_oracle") or {}).get("maker_0.06pct") or {}
        table.append(
            {
                "side": side,
                "n_boxes": r.get("n_labeled_boxes"),
                "n_features": r.get("n_features"),
                "lgbm_auc": r.get("lgbm_val_auc"),
                "oracle_n": ob.get("n"),
                "oracle_pf_maker": ob.get("profit_factor"),
                "rule_n": rb.get("n"),
                "rule_pf_maker": rb.get("profit_factor"),
                "narrow_pf_maker": NARROW_REF[f"{side}_pf_maker"],
                "delta_vs_narrow": r.get("delta_vs_narrow"),
                "pass_1.3": (r.get("success_line") or {}).get("pass"),
            }
        )

    any_pass = any(
        (results[s].get("success_line") or {}).get("pass") for s in ("long", "short")
    )
    verdict = (
        f"至少一边扩特征因果规则 PF@maker ≥ {SUCCESS_PF} — 该边值得继续挖（仍是发现级）。"
        if any_pass
        else (
            f"扩特征后 long/short 两边因果规则均未过 PF@maker {SUCCESS_PF}；"
            "扩特征仍未救出可部署边。"
        )
    )

    # Strip bulky feature_list from by_side for readability; keep at top level
    feature_list = feat_cols
    for s in results:
        if isinstance(results[s], dict):
            results[s].pop("feature_list", None)

    out = {
        "tag": args.tag,
        "sheet": str(args.sheet),
        "discipline": {
            "holdout_start": str(HOLDOUT_START),
            "holdout_touched": False,
            "tp_sl_horizon": [TP_MULT, SL_MULT, HORIZON_BARS],
            "long_settlement": "label_candidate",
            "short_settlement": "label_short_candidate",
            "success_line": f"per-side causal-rule PF@maker >= {SUCCESS_PF}",
            "costs": {"swap_maker": FORWARD_COST, "legacy_p0": LEGACY_P0_ROUND_TRIP},
            "experiment_theme": "owner-approved rich factorization (single variable)",
        },
        "label_counts": {
            "long": n_long,
            "short": n_short,
            "skip": n_skip,
            "empty": n_empty,
        },
        "n_features": len(feature_list),
        "feature_list": feature_list,
        "feature_groups": sorted({feature_group(c) for c in feature_list}),
        "narrow_reference": NARROW_REF,
        "main_table": table,
        "by_side": results,
        "long_vs_short_disclosure": lvs,
        "emergence_pf_maker_published": EMERGENCE_PF_MAKER,
        "verdict_zh": verdict,
        "honesty_traps": [
            "特征再多也可能复制不了事后选点；裁决只看因果规则 PF",
            "LGBM AUC / gain 仅披露，不作交易信号",
            "框几何只作披露，未进因果规则",
            "walk-forward 分数阈值不可部署（已标样本内挑选）",
            "未消耗 holdout；过 1.3 仍是发现级",
            "owner 标 side 若看了框后走势，oracle 含 hindsight",
        ],
    }
    out_path = PROJECT / "analysis" / "output" / f"{args.tag}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str) + "\n")
    pd.DataFrame(table).to_csv(
        PROJECT / "analysis" / "output" / f"{args.tag}_main.csv", index=False
    )
    print(f"WROTE {out_path}")
    print("MAIN TABLE:")
    print(pd.DataFrame(table).to_string(index=False))
    print("VERDICT:", verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
