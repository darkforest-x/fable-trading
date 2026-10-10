"""SPIKE V12.9 15m signal-threshold grid with select-then-validate (owner 2026-10-11).

Owner: "做参数优化，先用15m ETH试". Frozen config and rules:
``experiments/active/exp-spike-v129-eth15m-paramopt-20261011-v1/config.json``.

Three thresholds that V12.9 hard-codes are opened up, nothing else:
* dense width W - ``spike_v126_htf_recheck._legacy``: prior 12-bar mean six-MA width <= W ATR;
* advance A and volume V - the same function's quality gate (3-bar net advance >= A ATR,
  3-bar volume ratio >= V) and the V6 structural evidence envelope in
  ``spike_v7_fast._detect_confirmed_fast`` (same A and V there).
``legacy_param`` and ``detect_param`` are line-for-line copies with those literals as
arguments; at the default (3, 1.5, 1.5) the admitted signals and raw sides must equal
``spike_v128_recent.facts_for`` exactly on both datasets (checked at run time). Admission,
the 1h SMA60 gate, exits and costs are untouched; opposite raw signals still exit trades,
so a threshold change also changes which opposite signals exist (part of the parameter).

Selection on Binance ETH 15m 2020-07..2024-01, validation on OKX ETH 15m and Binance ETH
15m 2024-02..2026-10; the selection and pass rules are in the config.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import itertools
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as p
from yoyo.evaluation import spike_v128_recent_report as rep
from yoyo.evaluation import spike_v129_eth15m as fifteen
from yoyo.evaluation import spike_v129_eth5m as five
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v126_htf_recheck import bb_admission
from yoyo.evaluation.spike_v7_fast import _data_gap, _finite, _shape
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_v9_htf_sma import confirmed_sma, side_gate

EXP = Path("experiments/active/exp-spike-v129-eth15m-paramopt-20261011-v1")
CONFIG = EXP / "config.json"
TEST = Path("tests/evaluation/test_spike_v129_param_opt.py")
MINUTES = 15


def config() -> dict:
    cfg = json.loads(CONFIG.read_text())
    if cfg["experiment_id"] != EXP.name or float(cfg["round_trip_cost"]) != .002:
        raise ValueError("invalid frozen parameter-optimization config")
    d = cfg["default"]
    if (d["dense_width_atr"], d["advance_atr"], d["volume_ratio"]) != (3.0, 1.5, 1.5):
        raise ValueError("default must be V12.9's literals")
    return cfg


def sources() -> dict[str, str]:
    paths = tuple(dict.fromkeys([CONFIG, Path(__file__), TEST, *_local_transitive_python((Path(__file__),))]))
    if not _committed(paths):
        raise ValueError("Commit every builder, test, config and imported source before construction")
    return {str(x): p.digest(x) for x in paths}


def legacy_param(frame, gap, advance, volume_ratio, side, width, adv, vol):
    """``spike_v126_htf_recheck._legacy`` with the 3 / 1.5 / 1.5 literals as arguments."""
    dense = frame.ready & frame.pastWidth.le(width) & frame.pastCrosses.ge(2)
    recent = dense.shift().rolling(12, min_periods=12).sum().gt(0)
    ph = frame.high.shift().rolling(12, min_periods=12).max().to_numpy()
    pl = frame.low.shift().rolling(12, min_periods=12).min().to_numpy()
    c = frame.close.to_numpy()
    fast = frame[['s20', 'e20']]
    early = (frame.ready & (frame.close.gt(ph) & frame.close.gt(fast.max(axis=1)) if side == 1
             else frame.close.lt(pl) & frame.close.lt(fast.min(axis=1)))).to_numpy(bool)
    quality = (frame.ready & recent & volume_ratio.ge(vol)
        & (advance.ge(adv) & frame.md.ge(frame.sb) & frame.middle.gt(frame.middle.shift()) if side == 1
           else advance.le(-adv) & frame.md.le(frame.sb) & frame.middle.lt(frame.middle.shift()))).to_numpy(bool)
    n = len(frame)
    conf, high, low = np.zeros(n, bool), np.full(n, np.nan), np.full(n, np.nan)
    previous, last, parent, done, hi, lo = False, -1, -1, False, np.nan, np.nan
    for i in range(n):
        if gap[i]:
            previous, last, parent, done = False, -1, -1, False
            hi = lo = np.nan
        fresh = early[i] and not previous and (last < 0 or i - last >= 12)
        previous = early[i]
        if fresh:
            last = parent = i
            hi, lo, done = ph[i], pl[i], False
        if parent >= 0 and i - parent > 3:
            parent, done, hi, lo = -1, False, np.nan, np.nan
        if parent >= 0 and not done and quality[i] and (c[i] > hi if side == 1 else c[i] < lo):
            conf[i], high[i], low[i], done = True, hi, lo, True
    return conf, high, low


def detect_param(frame: pd.DataFrame, side: int, adv: float, vol: float) -> np.ndarray:
    """``spike_v7_fast._detect_confirmed_fast`` with the 1.5 / 1.5 envelope as arguments."""
    rope_name = "ropeHigh" if side == 1 else "ropeLow"
    arrays = {name: frame[name].to_numpy(float) for name in ("open", "high", "low", "close", "md", "sb", "atr", rope_name,
                                                               "legacy_parent_high", "legacy_parent_low", "advance3", "volume_ratio3")}
    open_a, high_a, low_a, close_a = (arrays[name] for name in ("open", "high", "low", "close"))
    md_a, sb_a, atr_a, rope_a = (arrays[name] for name in ("md", "sb", "atr", rope_name))
    parent_high_a, parent_low_a = arrays["legacy_parent_high"], arrays["legacy_parent_low"]
    advance_a, volume_a = arrays["advance3"], arrays["volume_ratio3"]
    ready_a = frame.ready.to_numpy(bool)
    gap_a = frame.data_gap.to_numpy(bool)
    legacy_a = frame.legacy_confirmed.to_numpy(bool)
    out = np.zeros(len(frame), dtype=bool)
    body_support_i = parent_high = parent_low = evidence_i = prior_md = None
    pending = evidence = False
    segment_start_i = 0
    for i in range(len(frame)):
        valid_ohlc = (np.isfinite((open_a[i], high_a[i], low_a[i], close_a[i])).all() and low_a[i] > 0
                      and high_a[i] >= max(open_a[i], close_a[i], low_a[i])
                      and low_a[i] <= min(open_a[i], close_a[i], high_a[i]))
        valid = (not gap_a[i] and ready_a[i] and valid_ohlc
                 and np.isfinite((md_a[i], sb_a[i], atr_a[i], rope_a[i])).all() and atr_a[i] > 0)
        if not valid:
            body_support_i = None
            parent_high = parent_low = None
            pending = evidence = False
            evidence_i = None
            segment_start_i = i + 1
            prior_md = float(md_a[i]) if _finite(md_a[i]) else None
            continue
        o, close, md, sb, rope_value = open_a[i], close_a[i], md_a[i], sb_a[i], rope_a[i]
        full_body = min(o, close) > rope_value if side == 1 else max(o, close) < rope_value
        if side * (close - rope_value) <= 0:
            body_support_i = None
        elif full_body:
            body_support_i = i
        parent_valid = (legacy_a[i] and _finite(parent_high_a[i]) and _finite(parent_low_a[i])
                        and parent_low_a[i] <= parent_high_a[i])
        if parent_valid:
            parent_high, parent_low, pending = float(parent_high_a[i]), float(parent_low_a[i]), True
            window_start = max(segment_start_i, i - 2)
            shape_i = next((j for j in range(i, window_start - 1, -1)
                            if _shape(open_a, high_a, low_a, close_a, j, side)), None)
            envelope = np.isfinite((advance_a[i], volume_a[i])).all() and side * advance_a[i] >= adv and volume_a[i] >= vol
            evidence = bool(envelope and shape_i is not None)
            evidence_i = shape_i if evidence else None
        elif pending and _shape(open_a, high_a, low_a, close_a, i, side):
            envelope = np.isfinite((advance_a[i], volume_a[i])).all() and side * advance_a[i] >= adv and volume_a[i] >= vol
            if envelope:
                evidence, evidence_i = True, i
        invalidated = pending and (close < float(parent_low) if side == 1 else close > float(parent_high))
        if invalidated:
            parent_high = parent_low = None
            pending = evidence = False
            evidence_i = None
        elif (pending and evidence and body_support_i is not None and parent_high is not None and parent_low is not None
              and prior_md is not None and side * (close - rope_value) > 0
              and (close > parent_high if side == 1 else close < parent_low)
              and side * (md - sb) > 0 and side * (md - prior_md) > 0):
            out[i] = True
            pending = evidence = False
            evidence_i = None
        prior_md = float(md)
    return out


def prepare(base5m: pd.DataFrame, asset: str) -> dict:
    """Everything that does not depend on the three thresholds (as in pine_facts)."""
    bars, partial = p.complete_bars(base5m, MINUTES)
    if partial:
        raise ValueError("partial 15m buckets")
    frame = features(bars)
    gap = _data_gap(frame, MINUTES).to_numpy(bool)
    ages = np.arange(len(frame)) - np.maximum.accumulate(np.where(gap, np.arange(len(frame)), 0))
    frame["ready"] &= ages >= 12
    segments = pd.Series(gap.astype(int).cumsum(), index=frame.index)
    med = frame.volume.groupby(segments).transform(lambda v: v.rolling(20, min_periods=20).median())
    frame["rv"] = (frame.volume / med.shift()).where(med.shift().gt(0))
    advance = (frame.close - frame.close.shift(3)) / frame.atr.shift(3)
    volume_ratio = (frame.volume.rolling(3).sum() / (3 * med.shift(3))).where(med.shift(3).gt(0))
    rv = frame.rv.to_numpy()
    clock = frame.index + pd.Timedelta(minutes=MINUTES)
    bundle = (asset not in ("", "USDC")) & np.isfinite(rv) & (rv >= 0) & (rv <= 50) & np.asarray(clock.dayofweek != 6)
    h1 = confirmed_sma(base5m, frame.index, 60, (60,)).sma_60.to_numpy()
    return {"frame": frame, "gap": gap, "advance": advance, "volume_ratio": volume_ratio,
            "bb": bb_admission(frame.close, ages + 1), "bundle": bundle, "h1": h1}


def facts_param(ctx: dict, width: float, adv: float, vol: float) -> dict:
    frame, gap = ctx["frame"], ctx["gap"]
    detected = {}
    for side, name in ((1, "long"), (-1, "short")):
        conf, hi, lo = legacy_param(frame, gap, ctx["advance"], ctx["volume_ratio"], side, width, adv, vol)
        supplied = frame[["open", "high", "low", "close", "md", "sb", "atr", "ropeHigh", "ropeLow", "ready"]].copy()
        supplied["legacy_confirmed"], supplied["legacy_parent_high"], supplied["legacy_parent_low"] = conf, hi, lo
        supplied["advance3"], supplied["volume_ratio3"], supplied["data_gap"] = ctx["advance"], ctx["volume_ratio"], gap
        detected[name] = detect_param(supplied, side, adv, vol)
    if (detected["long"] & detected["short"]).any():
        raise ValueError("ambiguous raw direction")
    side = np.where(detected["long"], 1, np.where(detected["short"], -1, 0))
    c, atr = frame.close.to_numpy(), frame.atr.to_numpy()
    edge = np.where(side == 1, frame.ropeHigh, frame.ropeLow)
    with np.errstate(divide="ignore", invalid="ignore"):
        distance = side * (c - edge) / np.where(atr > 0, atr, np.nan)
    final = (side != 0) & ctx["bb"] & ctx["bundle"] & (distance <= 3) & side_gate(c, side, ctx["h1"])
    return {"frame": frame, "gap": gap, "side": side, "v9": final}


_DATA: dict[str, tuple[dict, float, str]] = {}


def dataset(venue: str) -> tuple[dict, float, str]:
    """Per-process cache of the threshold-free context."""
    if venue not in _DATA:
        fcfg = fifteen.config()
        if venue == "okx":
            _DATA[venue] = (prepare(fifteen.okx_base5m(fcfg), fcfg["asset"]), float(fcfg["tick"]), fcfg["asset"])
        else:
            meta = p.source.symbol_meta()["ETHUSDT"]
            _DATA[venue] = (prepare(fifteen.binance_base5m(fcfg), meta["asset"]), float(meta["tick"]), meta["asset"])
    return _DATA[venue]


def trades_for(venue: str, width: float, adv: float, vol: float, windows: list[dict]) -> tuple[list[dict], object]:
    ctx, tick, asset = dataset(venue)
    facts = facts_param(ctx, width, adv, vol)
    frame = facts["frame"]
    identity = {"venue": venue, "symbol": "ETH", "asset": asset, "timeframe_min": MINUTES}
    key = f"{venue}:ETH:15m:{width}:{adv}:{vol}"
    prepared = p.source.prepared_arm(frame, facts["gap"], facts["side"], key, identity, MINUTES, tick)
    first = min(pd.Timestamp(w["start"]) for w in windows)
    last = max(pd.Timestamp(w["end"]) for w in windows)
    events = []
    for i in np.flatnonzero(facts["v9"]):
        close_time = frame.index[i] + pd.Timedelta(minutes=MINUTES)
        if close_time >= last:
            continue
        events.append({**identity, "arm": "ordinary", "signal_i": int(i), "side": int(facts["side"][i]),
                       "signal_bar_open": frame.index[i], "signal_close": close_time, "in_window": bool(close_time >= first)})
    trades, _ = p.serial(prepared, events, "ordinary", key)
    return trades, prepared


def metrics(trades: list[dict], start, end) -> dict:
    t = pd.DataFrame(trades)
    if t.empty:
        return {"n": 0}
    t = t.loc[~t.censored.astype(bool)]
    sc = pd.to_datetime(t.signal_close, utc=True)
    t = t.loc[(sc >= pd.Timestamp(start)) & (sc < pd.Timestamp(end))].sort_values("entry_time")
    r = t.net_r
    if not len(r):
        return {"n": 0}
    run = best = 0
    for v in r < 0:
        run = run + 1 if v else 0
        best = max(best, run)
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {"n": int(len(r)), "win_rate": float((r > 0).mean()), "mean_r": float(r.mean()), "sum_r": float(r.sum()),
            "sum_wo_top5": float(r.sum() - r.nlargest(5).sum()), "pf": float(wins / losses) if losses > 0 else math.inf,
            "max_losses_in_row": int(best)}


def cell(args: tuple) -> dict:
    width, adv, vol, cfg = args
    row = {"dense_width_atr": width, "advance_atr": adv, "volume_ratio": vol}
    sel = cfg["select"]
    btrades, _ = trades_for("binance", width, adv, vol, [sel, cfg["validate"][1]])
    row.update({f"select_{k}": v for k, v in metrics(btrades, sel["start"], sel["end"]).items()})
    vb = cfg["validate"][1]
    row.update({f"binance_val_{k}": v for k, v in metrics(btrades, vb["start"], vb["end"]).items()})
    va = cfg["validate"][0]
    otrades, _ = trades_for("okx", width, adv, vol, [va])
    row.update({f"okx_val_{k}": v for k, v in metrics(otrades, va["start"], va["end"]).items()})
    return row


def smoothed(grid: pd.DataFrame, cfg: dict, min_n: int = 60) -> pd.DataFrame:
    """Mean of eligible cells' selection mean R over the cell and its +-1 axis neighbours."""
    axes = [cfg["grid"][k] for k in ("dense_width_atr", "advance_atr", "volume_ratio")]
    idx = {(r.dense_width_atr, r.advance_atr, r.volume_ratio): r for r in grid.itertuples()}
    out = []
    for r in grid.itertuples():
        pos = [axes[0].index(r.dense_width_atr), axes[1].index(r.advance_atr), axes[2].index(r.volume_ratio)]
        vals = []
        for axis in range(3):
            for step in (-1, 0, 1):
                if axis > 0 and step == 0:
                    continue
                q = list(pos)
                q[axis] += step
                if 0 <= q[axis] < len(axes[axis]):
                    key = (axes[0][q[0]], axes[1][q[1]], axes[2][q[2]])
                    other = idx.get(key)
                    if other is not None and (getattr(other, "select_n", 0) or 0) >= min_n:
                        vals.append(other.select_mean_r)
        out.append(float(np.mean(vals)) if vals and (r.select_n or 0) >= min_n else np.nan)
    return grid.assign(select_smoothed=out)


def excess(prepared, trades: list[dict], cfg: dict) -> dict:
    va = cfg["validate"][0]
    scfg = {"start": va["start"], "split": cfg["okx_split"], "end": va["end"], "vol_bins": cfg["vol_bins"],
            "control_seed": cfg["control_seed"]}
    window = [t for t in trades if pd.Timestamp(va["start"]) <= pd.Timestamp(t["signal_close"]) < pd.Timestamp(va["end"])]
    ctrl = p.matched_controls(prepared, window, scfg)
    t = rep.enrich(pd.DataFrame(window), pd.Timestamp(cfg["okx_split"]))
    t = t.assign(control_net_return=t.trade_key.map(ctrl.set_index("trade_key").control_net_return),
                 matched=t.trade_key.map(ctrl.set_index("trade_key").matched).fillna(False).astype(bool))
    m = t.loc[t.matched & ~t.censored]
    return rep.block_inference((m.net_return - m.control_net_return) * 1e4, m.week, seed=cfg["stat_seed"])


def main() -> None:
    cfg = config()
    code = sources()
    for venue in ("okx", "binance"):
        ctx, tick, asset = dataset(venue)
        ours = facts_param(ctx, 3.0, 1.5, 1.5)
        base = fifteen.okx_base5m(fifteen.config()) if venue == "okx" else fifteen.binance_base5m(fifteen.config())
        bars, _ = p.complete_bars(base, MINUTES)
        ref = p.facts_for(bars, base, asset, tick, MINUTES)
        if not (np.array_equal(ours["v9"], ref["v9"]) and np.array_equal(ours["side"], ref["side"])):
            raise ValueError(f"default parameters do not reproduce V12.9 on {venue}")
    jobs = [(w, a, v, cfg) for w, a, v in itertools.product(*(cfg["grid"][k] for k in ("dense_width_atr", "advance_atr", "volume_ratio")))]
    with ProcessPoolExecutor(max_workers=int(cfg["workers"])) as pool:
        rows = list(pool.map(cell, jobs))
    grid = smoothed(pd.DataFrame(rows), cfg)
    out = EXP / "run_v1"
    out.mkdir(parents=True, exist_ok=True)
    grid.to_csv(out / "grid.csv", index=False)
    d = cfg["default"]
    default = grid.loc[(grid.dense_width_atr == d["dense_width_atr"]) & (grid.advance_atr == d["advance_atr"])
                       & (grid.volume_ratio == d["volume_ratio"])].iloc[0]
    eligible = grid.loc[grid.select_smoothed.notna()]
    chosen = eligible.sort_values("select_smoothed", ascending=False).iloc[0]
    raw_best = grid.loc[grid.select_n >= 60].sort_values("select_mean_r", ascending=False).iloc[0]
    results = {}
    for name, r in (("default", default), ("selected", chosen), ("raw_best", raw_best)):
        trades, prepared = trades_for("okx", r.dense_width_atr, r.advance_atr, r.volume_ratio, [cfg["validate"][0]])
        results[name] = {**{k: (v.item() if hasattr(v, "item") else v) for k, v in r.to_dict().items()},
                         "okx_excess": excess(prepared, trades, cfg)}
    s, dflt = results["selected"], results["default"]
    passed = bool(s["okx_val_mean_r"] > 0 and s["binance_val_mean_r"] > 0 and s["okx_val_mean_r"] > dflt["okx_val_mean_r"]
                  and s["binance_val_mean_r"] > dflt["binance_val_mean_r"])
    both = grid.loc[(grid.select_n >= 30) & (grid.okx_val_n >= 30) & (grid.binance_val_n >= 30)]
    top10 = grid.loc[grid.select_n >= 60].sort_values("select_mean_r", ascending=False).head(10)
    verdict = {"selected_beats_default_and_positive_both": passed,
               "okx_excess_p": s["okx_excess"].get("p_one_sided"),
               "label": ("project bar" if passed and s["okx_excess"].get("p_one_sided", 1) < .01 else
                         "worth forward observation" if passed and s["okx_excess"].get("p_one_sided", 1) < .05 else
                         "not passed" if not passed else "passed comparison but excess not significant"),
               "spearman_select_vs_okx": float(both.select_mean_r.corr(both.okx_val_mean_r, method="spearman")),
               "spearman_select_vs_binance_val": float(both.select_mean_r.corr(both.binance_val_mean_r, method="spearman")),
               "top10_okx_positive": int((top10.okx_val_mean_r > 0).sum()),
               "top10_binance_val_positive": int((top10.binance_val_mean_r > 0).sum()),
               "cells": int(len(grid)), "eligible": int(len(eligible))}
    p.dump(out / "receipt.json", five._clean({"config_sha256": p.digest(CONFIG), "code": code, "results": results,
                                               "verdict": verdict, "top10": top10.to_dict("records"),
                                               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}))
    print(json.dumps(five._clean({"verdict": verdict, "results": results}), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
