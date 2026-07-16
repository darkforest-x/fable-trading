"""ETH-only expanded dense-MA + LGB + stage-3 portfolio backtest per micro bar."""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from datetime import datetime, timezone

from src.backtest.run import ACCEPT_START, BASE_COST, COST_SWEEP, MAX_CONCURRENT, SCORE_QUANTILE, simulate, window_metrics
from src.data.bars import bar_to_timedelta
from src.data.loader import load_series, list_series
from src.eth_micro.config import (
    BACKTEST_JSON,
    MODELS_DIR,
    POOLS_DIR,
    SOURCE,
    SYMBOL,
    BarConfig,
    bar_configs,
    ensure_dirs,
)
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate
from src.data.bars import purge_window
from src.judgment.train import HOLDOUT_START, LGB_PARAMS, TRAIN_FRACTION, evaluate, permutation_pvalue


def split_eth_pool(df: pd.DataFrame, *, horizon_bars: int, bar: str) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Prefer holdout-aware split; if data is entirely post-holdout, use local 80/20.

    Local split is ETH-micro only and never claimed as holdout evaluation.
    """
    data = df.sort_values("signal_time").reset_index(drop=True)
    data["signal_time"] = pd.to_datetime(data["signal_time"], utc=True)
    purge = purge_window(horizon_bars, bar)
    dev = data[data["signal_time"] < HOLDOUT_START - purge].reset_index(drop=True)
    if len(dev) >= 60 and dev["label"].nunique() >= 2:
        split_i = int(len(dev) * TRAIN_FRACTION)
        train, val = dev.iloc[:split_i], dev.iloc[split_i:]
        val_start = val["signal_time"].min()
        train = train[train["signal_time"] < val_start - purge]
        if (
            len(train) >= 40
            and len(val) >= 15
            and train["label"].nunique() >= 2
            and val["label"].nunique() >= 2
        ):
            return train.reset_index(drop=True), val.reset_index(drop=True), "holdout_aware"
    # Local chronological split (side channel only)
    split_i = max(1, int(len(data) * TRAIN_FRACTION))
    train, val = data.iloc[:split_i].copy(), data.iloc[split_i:].copy()
    if len(val) and len(train):
        val_start = val["signal_time"].min()
        train = train[train["signal_time"] < val_start - purge]
    return train.reset_index(drop=True), val.reset_index(drop=True), "local_time_80_20"


def _load_eth_frame(bar: str) -> pd.DataFrame | None:
    groups = list_series(bar=bar)
    paths = groups.get((SOURCE, SYMBOL))
    if not paths:
        # try any eth swap
        for (src, sym), ps in groups.items():
            if sym == SYMBOL:
                paths = ps
                break
    if not paths:
        return None
    frame = load_series(paths)
    return frame if not frame.empty else None


def build_eth_pool(cfg: BarConfig) -> tuple[pd.DataFrame, dict]:
    frame = _load_eth_frame(cfg.bar)
    if frame is None or len(frame) < max(500, cfg.horizon_bars + 300):
        return pd.DataFrame(), {
            "bar": cfg.bar,
            "horizon_bars": cfg.horizon_bars,
            "wall_hours": cfg.wall_hours,
            "n_bars": 0 if frame is None else int(len(frame)),
            "n_candidates": 0,
            "status": "no_or_short_data",
        }
    enriched = add_indicators(frame)
    idxs = scan_candidates(enriched, horizon_bars=cfg.horizon_bars, mode="expanded")
    if not idxs:
        return pd.DataFrame(), {
            "bar": cfg.bar,
            "horizon_bars": cfg.horizon_bars,
            "wall_hours": cfg.wall_hours,
            "n_bars": int(len(frame)),
            "n_candidates": 0,
            "status": "no_candidates",
            "range": [str(frame["open_time"].iloc[0]), str(frame["open_time"].iloc[-1])],
        }
    featured = add_features(enriched)
    feats = extract_feature_rows(featured, idxs)
    rows = []
    for row_pos, signal_i in enumerate(idxs):
        outcome = label_candidate(
            enriched, signal_i, tp_mult=5.0, sl_mult=2.0, horizon=cfg.horizon_bars
        )
        if outcome is None:
            continue
        entry_i = signal_i + 1
        maker_filled = bool(
            entry_i < len(enriched)
            and float(enriched["low"].iloc[entry_i]) < float(enriched["open"].iloc[entry_i])
        )
        rows.append(
            {
                "source": SOURCE,
                "symbol": SYMBOL,
                "bar": cfg.bar,
                "signal_i": int(signal_i),
                "signal_time": enriched["open_time"].iloc[signal_i],
                "label": outcome.label,
                "outcome": outcome.outcome,
                "exit_offset": outcome.exit_offset,
                "entry_price": outcome.entry_price,
                "realized_ret": outcome.realized_ret,
                "maker_filled": maker_filled,
                "atr14": float(enriched["atr14"].iloc[signal_i]),
                "atr_pct": float(enriched["atr_pct"].iloc[signal_i]),
                **feats.iloc[row_pos].to_dict(),
            }
        )
    df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    cov = {
        "bar": cfg.bar,
        "horizon_bars": cfg.horizon_bars,
        "wall_hours": cfg.wall_hours,
        "n_bars": int(len(frame)),
        "n_candidates": int(len(df)),
        "status": "ok",
        "range": [str(frame["open_time"].iloc[0]), str(frame["open_time"].iloc[-1])],
        "pos_rate": round(float(df["label"].mean()), 4) if len(df) else None,
    }
    return df, cov


def train_bar(cfg: BarConfig, df: pd.DataFrame) -> dict:
    ensure_dirs()
    pool_path = POOLS_DIR / f"eth_{cfg.bar}_h{cfg.horizon_bars}.csv"
    df.to_csv(pool_path, index=False)
    train, val, split_mode = split_eth_pool(df, horizon_bars=cfg.horizon_bars, bar=cfg.bar)
    if len(train) < 30 or len(val) < 10 or train["label"].nunique() < 2 or val["label"].nunique() < 2:
        return {
            "bar": cfg.bar,
            "status": "skipped_insufficient",
            "split_mode": split_mode,
            "n_train": int(len(train)),
            "n_val": int(len(val)),
            "n_candidates": int(len(df)),
            "dataset": str(pool_path),
        }

    # Prefer regression (economic ranking); fall back binary if needed
    params = dict(LGB_PARAMS)
    params["objective"] = "regression"
    dtrain = lgb.Dataset(train[FEATURE_COLUMNS], label=train["realized_ret"])
    dval = lgb.Dataset(val[FEATURE_COLUMNS], label=val["realized_ret"], reference=dtrain)
    model = lgb.train(
        params,
        dtrain,
        num_boost_round=600,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    best_it = int(model.best_iteration or model.current_iteration())
    model_path = MODELS_DIR / f"eth_{cfg.bar}_reg.txt"
    model.save_model(str(model_path), num_iteration=best_it)

    val_scores = model.predict(val[FEATURE_COLUMNS], num_iteration=best_it)
    thr = float(np.quantile(val_scores, SCORE_QUANTILE))
    y = val["label"].to_numpy()
    rets = val["realized_ret"].to_numpy()
    metrics = evaluate(y, val_scores, rets)

    full = df.copy()
    full["score"] = model.predict(full[FEATURE_COLUMNS], num_iteration=best_it)
    bar_td = bar_to_timedelta(cfg.bar)
    full["entry_time"] = pd.to_datetime(full["signal_time"], utc=True) + bar_td
    full["exit_time"] = full["entry_time"] + full["exit_offset"].astype(int) * bar_td
    full = full.sort_values(["entry_time", "score"], ascending=[True, False])
    trades = simulate(full, thr)
    accept = trades[trades["entry_time"] >= ACCEPT_START] if not trades.empty else trades
    insample = trades[trades["entry_time"] < ACCEPT_START] if not trades.empty else trades

    meta = {
        "bar": cfg.bar,
        "horizon_bars": cfg.horizon_bars,
        "wall_hours": cfg.wall_hours,
        "objective": "regression",
        "model_path": f"data/eth_micro/models/{model_path.name}",
        "dataset": f"data/eth_micro/pools/{pool_path.name}",
        "threshold_val_q90": thr,
        "best_iteration": best_it,
        "feature_columns": list(FEATURE_COLUMNS),
        "score_quantile": SCORE_QUANTILE,
        "symbol": SYMBOL,
        "tp_mult": 5.0,
        "sl_mult": 2.0,
    }
    (MODELS_DIR / f"eth_{cfg.bar}_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "bar": cfg.bar,
        "status": "ok",
        "horizon_bars": cfg.horizon_bars,
        "wall_hours": cfg.wall_hours,
        "n_candidates": int(len(df)),
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "split_mode": split_mode,
        "val_range": [str(val["signal_time"].min()), str(val["signal_time"].max())],
        "pos_rate_val": round(float(val["label"].mean()), 4),
        "threshold_val_q90": thr,
        "best_iteration": best_it,
        "model_path": meta["model_path"],
        "val_auc": metrics["roc_auc"],
        "perm_p": round(permutation_pvalue(y, val_scores), 4),
        "top_decile": metrics["top_decile"],
        "top_net_0p2": metrics["top_decile"]["mean_net_ret"],
        "portfolio": {
            "n_eligible": int((full["score"] >= thr).sum()),
            "accept": {f"{c:.3f}": window_metrics(accept, c) for c in COST_SWEEP},
            "full": window_metrics(trades, BASE_COST),
            "insample_pre": window_metrics(insample, BASE_COST),
        },
        "meta": meta,
    }


def run_all() -> dict:
    ensure_dirs()
    coverage = []
    results = []
    for cfg in bar_configs():
        print(f"[eth_micro] {cfg.bar} h={cfg.horizon_bars} ...", flush=True)
        df, cov = build_eth_pool(cfg)
        coverage.append(cov)
        if df.empty or len(df) < 60:
            results.append(
                {
                    "bar": cfg.bar,
                    "status": cov.get("status", "too_few"),
                    "n_candidates": int(len(df)),
                    "horizon_bars": cfg.horizon_bars,
                    "wall_hours": cfg.wall_hours,
                }
            )
            continue
        res = train_bar(cfg, df)
        results.append(res)
        if res.get("status") == "ok":
            a = res["portfolio"]["accept"]["0.003"]
            print(
                f"  ok AUC={res['val_auc']:.3f} top_net={res['top_net_0p2']:+.4f} "
                f"accept_n={a.get('n_trades')} PF={a.get('profit_factor')}",
                flush=True,
            )

    ok = [r for r in results if r.get("status") == "ok"]
    best = max(ok, key=lambda r: r.get("top_net_0p2") or -1e9) if ok else None
    payload = {
        "channel": "eth_micro",
        "symbol": SYMBOL,
        "source": SOURCE,
        "discipline": "ETH-only; rules expanded TP5/SL2; LGB regression; val-only no holdout",
        "mainline_note": "Separate from 15m YOLO mainline ACTIVE; discovery channel",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage": coverage,
        "results": results,
        "best_bar_by_top_net": best["bar"] if best else None,
        "costs": {"portfolio_base": BASE_COST, "top_decile_rt": 0.002},
        "max_concurrent": MAX_CONCURRENT,
    }
    BACKTEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
