#!/usr/bin/env python3
"""Owner-label technique → causal feature disclosure → train-period base rate.

Question (owner 2026-07-23): audits say dense geometry is thin (emergence PF~0.87),
but manual fills on "perfect dense" feel like alpha. Can 5k–10k owner boxes disclose
which causal features define the technique, and does a rule built from those features
beat emergence base rate on history?

Protocol (causal annotation principle):
  1. Walk dense_owner_v11 (or --src) positives; MAD-resolve window like pad200;
     boxes_cut_and_spans → cut_global = box right-edge bar.
  2. At cut_global, compute add_indicators + add_features (+ box geometry for
     disclosure only). Features use bars <= cut only.
  3. Negatives: same-symbol random non-label bars (optional hard-neg: dense but
     unlabeled). LightGBM classifies label(1) vs neg(0) → gain = technique
     disclosure (NOT the verdict).
  4. Top features → simple interpretable AND-rule (thresholds from pos medians /
     direction vs neg means). Scan every train bar (<2026-05-04) causally;
     TP5/SL2/72bar triple-barrier base rate. Holdout never touched.

Honesty traps (must appear in the report):
  - Box right edge may be labeled with hindsight → LGBM AUC can look great;
    trust only the causal base-rate of the derived rule.
  - Random negatives ≠ "almost-dense"; gain may overstate separability.
    --hard-neg adds a dense-unlabeled contrast when time allows.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/owner_label_feature_verdict.py
  PYTHONPATH=. .venv/bin/python scripts/owner_label_feature_verdict.py \\
      --limit 200 --tag owner_label_smoke
  PYTHONPATH=. .venv/bin/python scripts/owner_label_feature_verdict.py \\
      --hard-neg --n-symbols 0 --tag owner_label_feature_verdict
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.build_crop_pad200_dataset import (  # noqa: E402
    MAX_STORED_MAD,
    boxes_cut_and_spans,
    resolve_win_start,
)
from scripts.build_htip_dataset import (  # noqa: E402
    WINDOW,
    parse_stem,
    read_boxes,
    resolve_series,
)
from src.costs import FORWARD_COST, LEGACY_P0_ROUND_TRIP  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_stem, is_eval_symbol  # noqa: E402
from src.detection.render import make_chart_transform  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS, add_features  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN, HORIZON_BARS  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
TP_MULT, SL_MULT = 5.0, 2.0  # live / tip-replay default (not labeling.py's TP4)
MIN_GAP_BARS = 18
WARMUP = 288
FAST_MAX, FULL_MAX = 0.0028, 0.0055
DEFAULT_SRC = PROJECT / "datasets" / "_deprecated_pretip" / "dense_owner_v11"
# Box geometry is disclosure-only (needs a label); never part of the trading rule.
BOX_FEATS = ("box_width_bars", "box_height_pct", "box_right_frac")
EMERGENCE_PF_MAKER = 0.874  # analysis/p_base_rate_dense_verdict.md @ FORWARD_COST


def _find_image(src: Path, stem: str) -> Path | None:
    for split in ("train", "val"):
        p = src / "images" / split / f"{stem}.png"
        if p.exists():
            return p
    return None


def _sym_key(body: str) -> str:
    """Normalize stem body toward SWAP series names."""
    b = body.replace("_SWAP", "")
    if b.endswith("_USDT"):
        return f"{b}_SWAP"
    return body


def extract_owner_cuts(
    src: Path,
    *,
    limit: int = 0,
    series_cache: dict[str, pd.DataFrame],
) -> tuple[list[dict], dict]:
    """Return per-box cut rows + skip stats. One row per YOLO box (not per stem)."""
    skips: dict[str, int] = defaultdict(int)
    rows: list[dict] = []
    label_paths: list[Path] = []
    for split in ("train", "val"):
        d = src / "labels" / split
        if d.is_dir():
            label_paths.extend(sorted(d.glob("*.txt")))

    for lbl in label_paths:
        if limit and len(rows) >= limit:
            break
        stem = lbl.stem
        if is_eval_stem(stem):
            skips["eval_stem"] += 1
            continue
        boxes = read_boxes(lbl)
        if not boxes:
            skips["empty"] += 1
            continue
        parsed = parse_stem(stem)
        if not parsed:
            skips["bad_stem"] += 1
            continue
        body, idx = parsed
        cache_key = body
        if cache_key not in series_cache:
            df = resolve_series(body)
            if df is None:
                df = resolve_series(_sym_key(body))
            series_cache[cache_key] = df  # may be None
        df = series_cache[cache_key]
        if df is None or len(df) < WINDOW + WARMUP:
            skips["no_series"] += 1
            continue
        enriched_mas = add_mas(df)
        img_path = _find_image(src, stem)
        stored = cv2.imread(str(img_path)) if img_path is not None else None
        if stored is None:
            skips["no_image"] += 1
            # Blind end_incl on okx_* is known poison — skip rather than guess.
            if stem.startswith("okx_"):
                skips["okx_no_mad"] += 1
                continue
        try:
            resolved = resolve_win_start(
                len(df), idx, enriched=enriched_mas, stored_img=stored
            )
        except Exception:
            skips["resolve_err"] += 1
            continue
        if resolved is None:
            skips["no_win"] += 1
            continue
        win_mode, win_start, mad = resolved
        if stored is not None and np.isfinite(mad) and mad > MAX_STORED_MAD:
            skips["mad_fail"] += 1
            continue
        if stored is None and stem.startswith("okx_") and win_mode == "end_incl":
            skips["okx_blind_end_incl"] += 1
            continue
        sub = enriched_mas.iloc[win_start : win_start + WINDOW].reset_index(drop=True)
        if len(sub) != WINDOW:
            skips["short_win"] += 1
            continue
        tf = make_chart_transform(sub)
        cut_local, spans = boxes_cut_and_spans(boxes, tf)
        cut_global = win_start + cut_local
        if cut_global < WARMUP or cut_global >= len(df) - 1:
            skips["cut_oob"] += 1
            continue
        t = pd.to_datetime(df["open_time"].iloc[cut_global], utc=True)
        if t >= HOLDOUT_START:
            skips["holdout_cut"] += 1
            continue
        # One row per span (box); primary geometry from that span.
        for b0, b1, price_hi, price_lo in spans:
            g1 = win_start + b1
            if g1 != cut_global and len(spans) > 1:
                # Keep all boxes; non-rightmost still get their own right edge.
                cut_i = win_start + b1
                if cut_i < WARMUP or cut_i >= len(df) - 1:
                    continue
                t_i = pd.to_datetime(df["open_time"].iloc[cut_i], utc=True)
                if t_i >= HOLDOUT_START:
                    continue
            else:
                cut_i = cut_global
                t_i = t
            width_bars = max(1, b1 - b0 + 1)
            mid = (float(price_hi) + float(price_lo)) / 2.0
            height_pct = abs(float(price_hi) - float(price_lo)) / max(mid, 1e-12)
            rows.append(
                {
                    "stem": stem,
                    "symbol_body": body,
                    "cut_global": int(cut_i),
                    "open_time": str(t_i),
                    "win_mode": win_mode,
                    "stored_mad": float(mad) if np.isfinite(mad) else None,
                    "box_width_bars": float(width_bars),
                    "box_height_pct": float(height_pct),
                    "box_right_frac": float((b1 + 0.5) / WINDOW),
                    "split": "val" if "/val/" in str(lbl).replace("\\", "/") else "train",
                }
            )
            if limit and len(rows) >= limit:
                break
    return rows, dict(skips)


def _feat_frame(df: pd.DataFrame) -> pd.DataFrame:
    return add_features(add_indicators(add_mas(df)))


def sample_feature_row(
    featured: pd.DataFrame,
    i: int,
    box: dict | None = None,
) -> dict | None:
    if i < 0 or i >= len(featured):
        return None
    row = featured.iloc[i]
    out: dict = {"cut_global": int(i), "open_time": str(row.get("open_time", ""))}
    for c in FEATURE_COLUMNS:
        v = row.get(c, np.nan)
        out[c] = float(v) if pd.notna(v) else np.nan
    if box is not None:
        for c in BOX_FEATS:
            out[c] = float(box[c])
    else:
        for c in BOX_FEATS:
            out[c] = np.nan
    # atr gate later for barrier; keep atr_pct for diagnostics
    out["atr_pct"] = float(row["atr_pct"]) if pd.notna(row.get("atr_pct")) else np.nan
    out["fast_spread"] = float(row["fast_spread"]) if pd.notna(row.get("fast_spread")) else np.nan
    out["full_spread"] = float(row["full_spread"]) if pd.notna(row.get("full_spread")) else np.nan
    return out


def resolve_net(enriched: pd.DataFrame, i: int, cost: float) -> float | None:
    """TP5/SL2/72bar from next open, minus cost. Mirrors base_rate_dense_offline."""
    entry_i = i + 1
    if entry_i >= len(enriched):
        return None
    atr = float(enriched["atr14"].iloc[i])
    atr_pct = float(enriched["atr_pct"].iloc[i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    entry = float(enriched["open"].iloc[entry_i])
    if not np.isfinite(entry) or entry <= 0:
        return None
    last_i = min(entry_i + HORIZON_BARS - 1, len(enriched) - 1)
    if last_i < entry_i:
        return None
    highs = enriched["high"].to_numpy()[entry_i : last_i + 1]
    lows = enriched["low"].to_numpy()[entry_i : last_i + 1]
    upper, lower = entry + TP_MULT * atr, entry - SL_MULT * atr
    up = int(np.argmax(highs >= upper)) if (highs >= upper).any() else len(highs)
    dn = int(np.argmax(lows <= lower)) if (lows <= lower).any() else len(highs)
    if up < dn:
        gross = upper / entry - 1
    elif dn < up:
        gross = lower / entry - 1
    elif (lows <= lower).any():
        gross = lower / entry - 1
    elif last_i - entry_i + 1 >= HORIZON_BARS:
        gross = float(enriched["close"].iloc[last_i]) / entry - 1
    else:
        return None
    return float(gross - cost)


def stats(net: np.ndarray) -> dict:
    if not len(net):
        return {
            "n": 0,
            "win_rate": None,
            "profit_factor": None,
            "mean_net": None,
            "mean_gross": None,
            "total_net": 0.0,
        }
    w, l = net[net > 0].sum(), net[net < 0].sum()
    return {
        "n": int(len(net)),
        "win_rate": round(float((net > 0).mean()), 4),
        "profit_factor": round(float(w / -l), 3) if l < 0 else None,
        "mean_net": round(float(net.mean()), 5),
        "total_net": round(float(net.sum()), 4),
    }


def build_causal_rule(
    pos_df: pd.DataFrame,
    neg_df: pd.DataFrame,
    gains: list[tuple[str, float]],
    *,
    top_k: int = 4,
) -> dict:
    """Interpretable AND-rule from top gain features (no box geometry).

    If LGBM gains are near-zero (common when classes barely separate on market
    features alone), fall back to largest |median(pos)-median(neg)| / pooled
    scale among FEATURE_COLUMNS.
    """
    gain_map = {n: g for n, g in gains}
    ranked = [n for n, g in gains if n in FEATURE_COLUMNS and g > 1e-6]
    if len(ranked) < top_k:
        gaps: list[tuple[str, float]] = []
        for name in FEATURE_COLUMNS:
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
            if len(ranked) >= max(top_k, 8):
                break

    clauses: list[dict] = []
    for name in ranked:
        p = pos_df[name].dropna()
        n = neg_df[name].dropna()
        if len(p) < 30 or len(n) < 30:
            continue
        p_med, n_med = float(p.median()), float(n.median())
        # Direction from medians; threshold = pos quartile toward the neg side
        # so the clause still covers most owner positives.
        if p_med <= n_med:
            thr = float(p.quantile(0.75))
            op = "<="
        else:
            thr = float(p.quantile(0.25))
            op = ">="
        clauses.append(
            {
                "feature": name,
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--limit", type=int, default=0, help="cap positive boxes (0=all)")
    ap.add_argument("--neg-ratio", type=float, default=3.0)
    ap.add_argument("--hard-neg", action="store_true", help="also sample dense-unlabeled negs")
    ap.add_argument("--n-symbols", type=int, default=0, help="base-rate scan: 0=all SWAP")
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--tag", default="owner_label_feature_verdict")
    ap.add_argument("--seed", type=int, default=20260723)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    print(f"SRC={args.src}")
    series_cache: dict[str, pd.DataFrame] = {}
    cuts, skip_stats = extract_owner_cuts(
        args.src, limit=args.limit, series_cache=series_cache
    )
    print(f"cuts_extracted={len(cuts)} skips={skip_stats}")
    if len(cuts) < 50:
        print("ERROR: too few cuts; abort")
        return 1

    # Group cuts by series body for feature sampling + neg sampling.
    by_body: dict[str, list[dict]] = defaultdict(list)
    for r in cuts:
        by_body[r["symbol_body"]].append(r)

    pos_rows: list[dict] = []
    neg_rows: list[dict] = []
    hard_rows: list[dict] = []
    for body, items in by_body.items():
        df = series_cache.get(body)
        if df is None:
            continue
        times = pd.to_datetime(df["open_time"], utc=True)
        df_tr = df[times < HOLDOUT_START].reset_index(drop=True)
        if len(df_tr) < WARMUP + HORIZON_BARS + 50:
            continue
        featured = _feat_frame(df_tr)
        label_cuts = set()
        for it in items:
            # Remap cut_global from full series → train-truncated index via time.
            t = pd.Timestamp(it["open_time"])
            if t.tzinfo is None:
                t = t.tz_localize("UTC")
            else:
                t = t.tz_convert("UTC")
            # find matching bar in truncated frame
            tt = pd.to_datetime(featured["open_time"], utc=True)
            hits = np.where(tt == t)[0]
            if len(hits) == 0:
                # nearest
                diffs = (tt - t).total_seconds().to_numpy()
                j = int(np.argmin(np.abs(diffs)))
                if abs(diffs[j]) > 15 * 60:
                    continue
                cut_i = j
            else:
                cut_i = int(hits[0])
            label_cuts.add(cut_i)
            feat = sample_feature_row(featured, cut_i, it)
            if feat is None or not np.isfinite(feat.get("ma_spread_pct", np.nan)):
                continue
            feat["label"] = 1
            feat["symbol"] = body
            feat["stem"] = it["stem"]
            feat["neg_kind"] = "pos"
            pos_rows.append(feat)

        if not label_cuts:
            continue
        # Random negatives away from labels.
        banned = set()
        for c in label_cuts:
            for d in range(-MIN_GAP_BARS, MIN_GAP_BARS + 1):
                banned.add(c + d)
        pool = [
            i
            for i in range(WARMUP, len(featured) - HORIZON_BARS - 2)
            if i not in banned
        ]
        n_neg = min(len(pool), max(1, int(len(label_cuts) * args.neg_ratio)))
        if pool and n_neg:
            chosen = rng.choice(pool, size=n_neg, replace=False)
            for i in chosen:
                feat = sample_feature_row(featured, int(i), None)
                if feat is None or not np.isfinite(feat.get("ma_spread_pct", np.nan)):
                    continue
                feat["label"] = 0
                feat["symbol"] = body
                feat["stem"] = ""
                feat["neg_kind"] = "random"
                neg_rows.append(feat)

        if args.hard_neg:
            fast = featured["fast_spread"].to_numpy()
            full = featured["full_spread"].to_numpy()
            dense = (fast <= FAST_MAX) & (full <= FULL_MAX) & np.isfinite(fast) & np.isfinite(full)
            hard_pool = [
                i
                for i in range(WARMUP, len(featured) - HORIZON_BARS - 2)
                if dense[i] and i not in banned
            ]
            n_h = min(len(hard_pool), max(1, len(label_cuts)))
            if hard_pool and n_h:
                chosen = rng.choice(hard_pool, size=n_h, replace=False)
                for i in chosen:
                    feat = sample_feature_row(featured, int(i), None)
                    if feat is None:
                        continue
                    feat["label"] = 0
                    feat["symbol"] = body
                    feat["stem"] = ""
                    feat["neg_kind"] = "hard_dense"
                    hard_rows.append(feat)

    pos_df = pd.DataFrame(pos_rows)
    neg_df = pd.DataFrame(neg_rows)
    hard_df = pd.DataFrame(hard_rows) if hard_rows else pd.DataFrame()
    print(
        f"pos={len(pos_df)} random_neg={len(neg_df)} hard_neg={len(hard_df)} "
        f"symbols={pos_df['symbol'].nunique() if len(pos_df) else 0}"
    )
    if len(pos_df) < 50 or len(neg_df) < 50:
        print("ERROR: insufficient feature rows")
        return 1

    # --- LightGBM disclosure (label vs random; optional hard-neg separate AUC) ---
    # Box geometry is UNDEFINED for random bars — including it yields AUC≈1 via
    # "has finite box_*" leakage. Disclosure features = market columns only.
    feat_cols = list(FEATURE_COLUMNS)
    train_df = pd.concat([pos_df, neg_df], ignore_index=True)
    X = train_df[feat_cols].to_numpy(dtype=float)
    y = train_df["label"].to_numpy(dtype=int)
    # Diagnostic: are owner cuts still "dense" at the labeled right edge?
    dense_at_cut = (
        (pos_df["fast_spread"] <= FAST_MAX) & (pos_df["full_spread"] <= FULL_MAX)
    ).mean()
    print(
        f"owner_cuts_still_dense_at_right_edge={float(dense_at_cut):.3f} "
        f"(fast<={FAST_MAX}, full<={FULL_MAX})"
    )
    print(
        "box_geom_pos_median:",
        {
            c: round(float(pos_df[c].median()), 4)
            for c in BOX_FEATS
            if c in pos_df.columns
        },
    )
    # Time-ish split by open_time within pre-holdout (not random).
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
        "seed": args.seed,
    }
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=200,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )
    from sklearn.metrics import roc_auc_score

    va_pred = booster.predict(X[va_idx])
    auc = float(roc_auc_score(y[va_idx], va_pred)) if len(np.unique(y[va_idx])) > 1 else float("nan")
    gain_raw = booster.feature_importance(importance_type="gain")
    gains = sorted(
        [(feat_cols[i], float(gain_raw[i])) for i in range(len(feat_cols))],
        key=lambda x: -x[1],
    )
    print(f"lgbm_val_auc={auc:.4f} (disclosure only; may be hindsight-inflated)")
    print("top gains:")
    for name, g in gains[:12]:
        print(f"  {name:20s} {g:.1f}")

    hard_auc = None
    if len(hard_df) >= 50:
        Xh = pd.concat([pos_df, hard_df], ignore_index=True)[feat_cols].to_numpy(dtype=float)
        yh = np.concatenate(
            [np.ones(len(pos_df), dtype=int), np.zeros(len(hard_df), dtype=int)]
        )
        # Refit quick model for hard-neg contrast AUC (same params, no early stop on holdout).
        dh = lgb.Dataset(Xh, label=yh, feature_name=feat_cols)
        bh = lgb.train(params, dh, num_boost_round=min(120, booster.best_iteration or 100))
        # In-sample AUC is optimistic; report as diagnostic only.
        hard_auc = float(roc_auc_score(yh, bh.predict(Xh)))
        print(f"hardneg_in_sample_auc={hard_auc:.4f} (diagnostic; dense-unlabeled contrast)")

    rule = build_causal_rule(pos_df, neg_df, gains, top_k=args.top_k)
    print("causal rule:", json.dumps(rule, indent=2))
    if not rule["clauses"]:
        print("ERROR: empty rule")
        return 1

    # --- Base rate at the owner cut bars themselves (technique moments, not rule) ---
    owner_nets_m: list[float] = []
    owner_nets_l: list[float] = []
    for body, items in by_body.items():
        df = series_cache.get(body)
        if df is None:
            continue
        times = pd.to_datetime(df["open_time"], utc=True)
        df_tr = df[times < HOLDOUT_START].reset_index(drop=True)
        if len(df_tr) < WARMUP + HORIZON_BARS + 50:
            continue
        featured = _feat_frame(df_tr)
        tt = pd.to_datetime(featured["open_time"], utc=True)
        cut_indices: list[int] = []
        for it in items:
            t = pd.Timestamp(it["open_time"])
            if t.tzinfo is None:
                t = t.tz_localize("UTC")
            else:
                t = t.tz_convert("UTC")
            hits = np.where(tt == t)[0]
            if len(hits) == 0:
                continue
            cut_indices.append(int(hits[0]))
        cut_indices = sorted(set(cut_indices))
        last = -10**9
        for cut_i in cut_indices:
            if cut_i - last < MIN_GAP_BARS:
                continue
            last = cut_i
            nm = resolve_net(featured, cut_i, FORWARD_COST)
            nl = resolve_net(featured, cut_i, LEGACY_P0_ROUND_TRIP)
            if nm is None or nl is None:
                continue
            owner_nets_m.append(nm)
            owner_nets_l.append(nl)
    s_owner_m = stats(np.asarray(owner_nets_m, dtype=float))
    s_owner_l = stats(np.asarray(owner_nets_l, dtype=float))
    print("owner_cut_base_rate @ maker:", s_owner_m)
    print("owner_cut_base_rate @ 0.20%:", s_owner_l)

    # --- Causal base-rate scan on train period ---
    nets_maker: list[float] = []
    nets_legacy: list[float] = []
    gross_only: list[float] = []
    n_sym = 0
    n_fires_raw = 0
    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        if is_eval_symbol(symbol):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < WARMUP + HORIZON_BARS + 50:
            continue
        featured = _feat_frame(frame)
        mask = rule_mask(featured, rule)
        # emergence-style: also require atr floor via resolve_net
        fires = [i for i in range(WARMUP, len(featured) - 1) if mask[i]]
        deduped: list[int] = []
        for i in fires:
            if not deduped or i - deduped[-1] >= MIN_GAP_BARS:
                deduped.append(i)
        n_fires_raw += len(deduped)
        for i in deduped:
            nm = resolve_net(featured, i, FORWARD_COST)
            nl = resolve_net(featured, i, LEGACY_P0_ROUND_TRIP)
            if nm is None or nl is None:
                continue
            nets_maker.append(nm)
            nets_legacy.append(nl)
            gross_only.append(nm + FORWARD_COST)  # undo maker cost → gross
        n_sym += 1
        if args.n_symbols and n_sym >= args.n_symbols:
            break
        if n_sym % 40 == 0:
            print(f"  scanned {n_sym} symbols, fires_kept={len(nets_maker)}")

    # Emergence recompute on same universe/cost for fair delta (rule scan subset).
    # We do NOT re-run full emergence here if n_symbols limited; report vs published 0.874.
    s_maker = stats(np.asarray(nets_maker, dtype=float))
    s_legacy = stats(np.asarray(nets_legacy, dtype=float))
    s_gross = stats(np.asarray(gross_only, dtype=float))
    # rename gross stats: profit_factor on gross is meaningful
    print("base_rate @ SWAP_MAKER 0.06%:", s_maker)
    print("base_rate @ LEGACY 0.20%:", s_legacy)
    print("gross (pre-cost):", {**s_gross, "note": "net+FORWARD_COST"})

    # Coverage of owner cuts by the rule (recall of technique).
    rule_hit = 0
    for _, r in pos_df.iterrows():
        ok = True
        for c in rule["clauses"]:
            v = r.get(c["feature"], np.nan)
            if not np.isfinite(v):
                ok = False
                break
            if c["op"] == "<=" and not (v <= c["threshold"]):
                ok = False
                break
            if c["op"] == ">=" and not (v >= c["threshold"]):
                ok = False
                break
        if ok:
            rule_hit += 1
    recall = rule_hit / max(len(pos_df), 1)

    t_min = str(pd.to_datetime(pos_df["open_time"], utc=True).min())
    t_max = str(pd.to_datetime(pos_df["open_time"], utc=True).max())
    pf_m = s_maker.get("profit_factor")
    pf_owner = s_owner_m.get("profit_factor")
    delta_vs_emergence = (
        round(float(pf_m) - EMERGENCE_PF_MAKER, 3) if pf_m is not None else None
    )
    delta_owner_vs_em = (
        round(float(pf_owner) - EMERGENCE_PF_MAKER, 3) if pf_owner is not None else None
    )
    # Verdict: separate oracle (owner cuts) from deployable causal rule.
    if pf_owner is None or s_owner_m["n"] < 30:
        alpha_call = "inconclusive"
        verdict = f"owner 标框时刻样本不足（n={s_owner_m['n']}），无法裁决。"
    else:
        rule_flat = (
            pf_m is not None
            and abs(float(pf_m) - EMERGENCE_PF_MAKER) < 0.05
        )
        oracle_up = pf_owner >= EMERGENCE_PF_MAKER + 0.05
        if oracle_up and rule_flat:
            alpha_call = "oracle_incremental_causal_rule_none"
            verdict = (
                f"手法相对 emergence：oracle 标框时刻有增量（PF {pf_owner} vs "
                f"{EMERGENCE_PF_MAKER}，Δ={delta_owner_vs_em}），可部署因果规则无增量"
                f"（PF {pf_m}，Δ={delta_vs_emergence}）。增量来自事后确认态而非 tip；"
                "未耗 holdout，不作上线裁决。"
            )
        elif oracle_up and pf_m is not None and pf_m >= EMERGENCE_PF_MAKER + 0.05:
            alpha_call = "incremental_vs_emergence"
            verdict = (
                f"oracle PF={pf_owner} 且因果规则 PF={pf_m} 均高于 emergence "
                f"{EMERGENCE_PF_MAKER}；仍须 holdout 终审才谈上线。未耗 holdout。"
            )
        elif pf_owner >= EMERGENCE_PF_MAKER - 0.03:
            alpha_call = "no_incremental_alpha"
            verdict = (
                f"owner 标框时刻 train PF={pf_owner} ≈ emergence {EMERGENCE_PF_MAKER}"
                f"（Δ={delta_owner_vs_em}）；因果规则 PF={pf_m}。几乎无增量。"
            )
        else:
            alpha_call = "worse_or_no_better"
            verdict = (
                f"owner 标框时刻 train PF={pf_owner} 低于 emergence {EMERGENCE_PF_MAKER}"
                f"（Δ={delta_owner_vs_em}）；因果规则 PF={pf_m}。"
            )

    out = {
        "tag": args.tag,
        "src": str(args.src),
        "discipline": {
            "holdout_start": str(HOLDOUT_START),
            "holdout_touched": False,
            "tp_sl_horizon": [TP_MULT, SL_MULT, HORIZON_BARS],
            "costs_reported": {
                "swap_maker": FORWARD_COST,
                "legacy_p0": LEGACY_P0_ROUND_TRIP,
            },
            "primary_compare_cost": "swap_maker_vs_emergence_0.874",
        },
        "data": {
            "cuts_extracted": len(cuts),
            "pos_feature_rows": len(pos_df),
            "random_neg": len(neg_df),
            "hard_neg": len(hard_df),
            "symbols_labeled": int(pos_df["symbol"].nunique()),
            "time_range_pos": [t_min, t_max],
            "skip_stats": skip_stats,
            "note_box_count": (
                "v11 positives ~5831 boxes / ~5550 stems; v12_htip ~10k includes tip clones. "
                "This run uses v11 owner originals with MAD window resolve."
            ),
        },
        "lgbm_disclosure": {
            "val_auc": round(auc, 4),
            "hardneg_in_sample_auc": None if hard_auc is None else round(hard_auc, 4),
            "feature_gain_top": [{"feature": n, "gain": round(g, 2)} for n, g in gains[:20]],
            "owner_cuts_still_dense_frac": round(float(dense_at_cut), 4),
            "box_geom_pos_median": {
                c: round(float(pos_df[c].median()), 4)
                for c in BOX_FEATS
                if c in pos_df.columns
            },
            "warning": (
                "AUC may be hindsight-inflated (box right edge labeled after seeing launch). "
                "Gain ranks technique cues; do NOT trade the booster. "
                "Box geometry excluded from LGBM (undefined on random negs)."
            ),
        },
        "causal_rule": rule,
        "rule_recall_on_owner_pos": round(recall, 4),
        "base_rate_train": {
            "symbols_scanned": n_sym,
            "raw_deduped_fires": n_fires_raw,
            "owner_cut_moments": {
                "swap_maker_0.06pct": s_owner_m,
                "legacy_0.20pct": s_owner_l,
                "delta_pf_vs_emergence": delta_owner_vs_em,
                "note": "Direct entries at owner box right-edge bars (technique moments).",
            },
            "causal_rule_scan": {
                "swap_maker_0.06pct": s_maker,
                "legacy_0.20pct": s_legacy,
                "gross_pre_cost": s_gross,
                "delta_pf_vs_emergence": delta_vs_emergence,
            },
            "emergence_pf_maker_published": EMERGENCE_PF_MAKER,
        },
        "verdict": {
            "alpha_call": alpha_call,
            "sentence_zh": verdict,
        },
        "honesty_traps": [
            "框右缘可能事后标 → 只信因果 base rate，不信 LGBM AUC",
            "随机负样本不是差一点的密集 → gain 可能夸大可分性；见 hard-neg AUC",
            "本轮未消耗 holdout；PF 为 train 段发现级，不作上线裁决",
            "规则阈值来自正样本分位，非 holdout 校准；存在轻度自我拟合",
        ],
    }
    out_path = PROJECT / "analysis" / "output" / f"{args.tag}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    gain_csv = PROJECT / "analysis" / "output" / f"{args.tag}_feature_gain.csv"
    pd.DataFrame(gains, columns=["feature", "gain"]).to_csv(gain_csv, index=False)
    print(f"WROTE {out_path}")
    print(f"WROTE {gain_csv}")
    print("VERDICT:", verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
