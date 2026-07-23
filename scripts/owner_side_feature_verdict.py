#!/usr/bin/env python3
"""Per-side owner-label feature disclosure + causal triple-barrier base rate.

Reads review_sheet.csv after owner fills owner_side ∈ {long, short, skip}.
Refuse to run when 0 rows have a filled side.

Protocol (train only <2026-05-04; holdout never touched):
  1. Load labeled boxes (long / short; skip dropped).
  2. Per side: LightGBM disclosure vs random negatives (market features only).
  3. Per side: interpretable AND causal rule from top gains.
  4. Per side: scan train bars; settle with
       long  → label_candidate (TP5/SL2/72)
       short → label_short_candidate
     Costs: SWAP maker 0.06% + legacy 0.20%.
  5. Main table: long | short rows. Also report owner-cut oracle moments
     settled on the matching side.

Success line (deployable incremental technique on a side):
  causal-rule PF @ SWAP maker ≥ 1.3 on that side.
  Below 1.3 = no deployable side-alpha from this labeling round (discovery-level).

Usage:
  PYTHONPATH=. .venv/bin/python scripts/owner_side_feature_verdict.py
  PYTHONPATH=. .venv/bin/python scripts/owner_side_feature_verdict.py \\
      --sheet analysis/output/owner_side_review/review_sheet.csv \\
      --n-symbols 0 --tag owner_side_feature_verdict
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.owner_label_feature_verdict import (  # noqa: E402
    BOX_FEATS,
    EMERGENCE_PF_MAKER,
    HOLDOUT_START,
    MIN_GAP_BARS,
    SL_MULT,
    TP_MULT,
    WARMUP,
    _feat_frame,
    build_causal_rule,
    rule_mask,
    sample_feature_row,
    stats,
)
from src.costs import FORWARD_COST, LEGACY_P0_ROUND_TRIP  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS  # noqa: E402
from src.judgment.labeling import (  # noqa: E402
    HORIZON_BARS,
    label_candidate,
    label_short_candidate,
)

DEFAULT_SHEET = PROJECT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"
SUCCESS_PF = 1.3
VALID = frozenset({"long", "short"})


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
        raise SystemExit(
            f"ERROR: sheet missing: {sheet}\n"
            "Run: PYTHONPATH=. .venv/bin/python scripts/build_owner_side_review_pack.py"
        )
    df = pd.read_csv(sheet, dtype=str).fillna("")
    if "owner_side" not in df.columns or "box_id" not in df.columns:
        raise SystemExit("ERROR: sheet needs box_id + owner_side columns")
    df["owner_side"] = df["owner_side"].str.strip().str.lower()
    labeled = df[df["owner_side"].isin(VALID)].copy()
    n_skip = int((df["owner_side"] == "skip").sum())
    n_empty = int((~df["owner_side"].isin(VALID | {"skip"})).sum())
    if len(labeled) == 0:
        raise SystemExit(
            f"ERROR: 0 行已标注 side（long/short），请先填 review_sheet。\n"
            f"  sheet={sheet}\n"
            f"  rows={len(df)} empty={n_empty} skip={n_skip}\n"
            f"  打开审阅: PYTHONPATH=. .venv/bin/python scripts/serve_owner_side_review.py"
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
    net_m = g - FORWARD_COST
    net_l = g - LEGACY_P0_ROUND_TRIP
    return {
        "maker_0.06pct": stats(net_m),
        "legacy_0.20pct": stats(net_l),
        "gross_pre_cost": stats(g),
    }


def run_side(
    side: str,
    pos_items: pd.DataFrame,
    *,
    neg_ratio: float,
    hard_neg: bool,
    top_k: int,
    n_symbols: int,
    seed: int,
    series_by_sym: dict[str, pd.DataFrame],
) -> dict:
    """Disclosure + causal rule + base-rate scan for one side."""
    rng = np.random.default_rng(seed + (1 if side == "long" else 2))
    FAST_MAX, FULL_MAX = 0.0028, 0.0055

    by_body: dict[str, list[dict]] = defaultdict(list)
    for _, r in pos_items.iterrows():
        by_body[str(r["symbol"])].append(r.to_dict())

    pos_rows: list[dict] = []
    neg_rows: list[dict] = []
    hard_rows: list[dict] = []

    for body, items in by_body.items():
        df = series_by_sym.get(body)
        if df is None:
            # try resolve on the fly
            continue
        times = pd.to_datetime(df["open_time"], utc=True)
        df_tr = df[times < HOLDOUT_START].reset_index(drop=True)
        if len(df_tr) < WARMUP + HORIZON_BARS + 50:
            continue
        featured = _feat_frame(df_tr)
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
            feat = sample_feature_row(featured, cut_i, box)
            if feat is None or not np.isfinite(feat.get("ma_spread_pct", np.nan)):
                continue
            feat["label"] = 1
            feat["symbol"] = body
            feat["stem"] = it.get("stem", "")
            feat["neg_kind"] = "pos"
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
                feat = sample_feature_row(featured, int(i), None)
                if feat is None or not np.isfinite(feat.get("ma_spread_pct", np.nan)):
                    continue
                feat["label"] = 0
                feat["symbol"] = body
                feat["stem"] = ""
                feat["neg_kind"] = "random"
                neg_rows.append(feat)
        if hard_neg:
            fast = featured["fast_spread"].to_numpy()
            full = featured["full_spread"].to_numpy()
            dense = (
                (fast <= FAST_MAX)
                & (full <= FULL_MAX)
                & np.isfinite(fast)
                & np.isfinite(full)
            )
            hard_pool = [
                i
                for i in range(WARMUP, len(featured) - HORIZON_BARS - 2)
                if dense[i] and i not in banned
            ]
            n_h = min(len(hard_pool), max(1, len(label_cuts)))
            if hard_pool and n_h:
                for i in rng.choice(hard_pool, size=n_h, replace=False):
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
    if len(pos_df) < 20 or len(neg_df) < 20:
        return {
            "side": side,
            "error": f"insufficient rows pos={len(pos_df)} neg={len(neg_df)}",
            "n_labeled_boxes": int(len(pos_items)),
            "pos_feature_rows": int(len(pos_df)),
        }

    feat_cols = list(FEATURE_COLUMNS)
    train_df = pd.concat([pos_df, neg_df], ignore_index=True)
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
        num_boost_round=200,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(30, verbose=False)],
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
    rule = build_causal_rule(pos_df, neg_df, gains, top_k=top_k)

    # Owner-cut oracle settled on this side
    owner_gross: list[float] = []
    for body, items in by_body.items():
        df = series_by_sym.get(body)
        if df is None:
            continue
        times = pd.to_datetime(df["open_time"], utc=True)
        df_tr = df[times < HOLDOUT_START].reset_index(drop=True)
        if len(df_tr) < WARMUP + HORIZON_BARS + 50:
            continue
        featured = _feat_frame(df_tr)
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

    # Causal rule scan
    nets_g: list[float] = []
    n_sym = 0
    n_fires = 0
    if rule.get("clauses"):
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
            fires = [i for i in range(WARMUP, len(featured) - 1) if mask[i]]
            deduped: list[int] = []
            for i in fires:
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

    owner_block = _side_block(owner_gross)
    rule_block = _side_block(nets_g)
    pf_m = (rule_block["maker_0.06pct"] or {}).get("profit_factor")
    pf_owner = (owner_block["maker_0.06pct"] or {}).get("profit_factor")
    pass_line = pf_m is not None and float(pf_m) >= SUCCESS_PF

    return {
        "side": side,
        "n_labeled_boxes": int(len(pos_items)),
        "pos_feature_rows": int(len(pos_df)),
        "random_neg": int(len(neg_df)),
        "hard_neg": int(len(hard_df)),
        "lgbm_val_auc": round(auc, 4),
        "feature_gain_top": [
            {"feature": n, "gain": round(g, 2)} for n, g in gains[:12]
        ],
        "causal_rule": rule,
        "owner_cut_oracle": owner_block,
        "causal_rule_scan": {
            **rule_block,
            "symbols_scanned": n_sym,
            "raw_deduped_fires": n_fires,
        },
        "success_line": {
            "metric": "causal_rule_PF_maker",
            "threshold": SUCCESS_PF,
            "value": pf_m,
            "pass": pass_line,
        },
        "oracle_pf_maker": pf_owner,
        "delta_rule_vs_emergence": (
            round(float(pf_m) - EMERGENCE_PF_MAKER, 3) if pf_m is not None else None
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    ap.add_argument("--neg-ratio", type=float, default=3.0)
    ap.add_argument("--hard-neg", action="store_true")
    ap.add_argument("--n-symbols", type=int, default=0)
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--tag", default="owner_side_feature_verdict")
    ap.add_argument("--seed", type=int, default=20260723)
    ap.add_argument("--dry-run", action="store_true", help="count sides and exit")
    args = ap.parse_args()

    labeled, n_skip, n_empty = _load_labeled(args.sheet)
    n_long = int((labeled["owner_side"] == "long").sum())
    n_short = int((labeled["owner_side"] == "short").sum())
    print(
        f"labeled long={n_long} short={n_short} skip={n_skip} empty={n_empty} "
        f"sheet={args.sheet}"
    )
    if args.dry_run:
        print("dry-run ok — not computing features/base-rate")
        return 0

    # Prefetch series for symbols we need
    need = set(labeled["symbol"].unique())
    series_by_sym: dict[str, pd.DataFrame] = {}
    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if symbol in need:
            series_by_sym[symbol] = frame
        # also accept body without insisting all loaded mid-loop
    missing = sorted(need - set(series_by_sym))
    if missing:
        print(f"WARN: missing series for {len(missing)} symbols e.g. {missing[:5]}")

    results = {}
    for side in ("long", "short"):
        sub = labeled[labeled["owner_side"] == side]
        if len(sub) == 0:
            results[side] = {
                "side": side,
                "n_labeled_boxes": 0,
                "error": "no boxes labeled this side",
                "success_line": {"pass": False, "threshold": SUCCESS_PF, "value": None},
            }
            print(f"[{side}] skipped — 0 labels")
            continue
        print(f"[{side}] running n_boxes={len(sub)} …")
        results[side] = run_side(
            side,
            sub,
            neg_ratio=args.neg_ratio,
            hard_neg=args.hard_neg,
            top_k=args.top_k,
            n_symbols=args.n_symbols,
            seed=args.seed,
            series_by_sym=series_by_sym,
        )
        sl = results[side].get("success_line", {})
        print(
            f"[{side}] rule_PF_maker={sl.get('value')} "
            f"pass>={SUCCESS_PF}? {sl.get('pass')} "
            f"oracle_PF={results[side].get('oracle_pf_maker')}"
        )

    # Main comparison table
    table = []
    for side in ("long", "short"):
        r = results[side]
        rb = (r.get("causal_rule_scan") or {}).get("maker_0.06pct") or {}
        ob = (r.get("owner_cut_oracle") or {}).get("maker_0.06pct") or {}
        table.append(
            {
                "side": side,
                "n_boxes": r.get("n_labeled_boxes"),
                "lgbm_auc": r.get("lgbm_val_auc"),
                "oracle_n": ob.get("n"),
                "oracle_pf_maker": ob.get("profit_factor"),
                "rule_n": rb.get("n"),
                "rule_pf_maker": rb.get("profit_factor"),
                "pass_1.3": (r.get("success_line") or {}).get("pass"),
            }
        )

    any_pass = any((results[s].get("success_line") or {}).get("pass") for s in ("long", "short"))
    verdict = (
        f"至少一边因果规则 PF@maker ≥ {SUCCESS_PF} — 该边有可部署增量候选（仍须另批 holdout）。"
        if any_pass
        else f"long/short 两边因果规则均未过 PF@maker {SUCCESS_PF}；分边手法未给出可部署增量。"
    )

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
        },
        "label_counts": {
            "long": n_long,
            "short": n_short,
            "skip": n_skip,
            "empty": n_empty,
        },
        "main_table": table,
        "by_side": results,
        "emergence_pf_maker_published": EMERGENCE_PF_MAKER,
        "verdict_zh": verdict,
        "honesty_traps": [
            "若标注时看了框后走势，side 标签带 hindsight；只信因果 base rate",
            "LGBM AUC 仅披露，不作交易信号",
            "未消耗 holdout；过 1.3 仍是发现级",
            "skip 未进入任一边正样本",
        ],
    }
    out_path = PROJECT / "analysis" / "output" / f"{args.tag}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    # Flat CSV main table
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
