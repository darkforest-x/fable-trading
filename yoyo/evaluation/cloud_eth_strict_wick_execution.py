"""Execute frozen ETH first-wick selections with the existing native hourly adapter."""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.cloud_eth_early_execution import drawdown, enrich, paired_weeks, stats
from yoyo.evaluation.cloud_eth_early_inputs import HOUR_MS, STEP_12H_MS, _bars, _cross_index
from yoyo.layers.l3_backtest.nautilus_cloud_hourly import run_cloud_hourly


ACTIVE = Path(__file__).resolve().parents[2] / "experiments/active"
OUTPUT = ACTIVE / "exp-cloud-eth-first-wick-execution-20261116-v1"


def read_csv(path):
    return pd.read_csv(path, float_precision="round_trip")


def build_plans(cfg, hourly):
    """Only the selected closed candle authorizes entry. Parent outcomes label trades retrospectively."""
    folder = ACTIVE / cfg["signals"]
    weeks = read_csv(folder / "weekly_selections.csv")
    candles = read_csv(folder / "mother_candidates.csv")
    rails = read_csv(folder / "side_first_crosses.csv").set_index(["week", "side"])
    parents = candles[candles.timeframe == "12H"].copy()
    parents["passes"] = (parents.bar_exists & parents.signal_bar_inside_anchor_window
                         & parents.morphology_pass & parents.trend_match)
    parent_index = parents.set_index(["week", "side"], verify_integrity=True)
    chosen = weeks[weeks.morphology_trend_strict_status == "selected"].morphology_trend_strict_candidate_id
    ladder = candles.set_index("candidate_id", drop=False, verify_integrity=True).loc[chosen]
    strict = parents[parents.passes].sort_values(["week", "decision_close_ms"])
    if strict.duplicated(["week", "decision_close_ms"]).any():
        raise ValueError("Ambiguous simultaneous 12H alternatives")
    records = []
    for arm, candidates in (("ladder", ladder),
                            ("wait_12h", strict.drop_duplicates("week"))):
        for row in candidates.itertuples(index=False):
            key = (row.week, row.side)
            parent = parent_index.loc[key]
            entry_ms = int(row.decision_close_ms)
            parent_ms = int(parent.decision_close_ms)
            side = int(row.trade_direction)
            rail = float(rails.loc[key, "lower_1618" if side == 1 else "upper_1618"])
            if parent_ms < entry_ms or not row.morphology_pass or not row.trend_match:
                raise ValueError(f"Invalid frozen selection {row.candidate_id}")
            records.append(dict(arm=arm, week=row.week, candidate_id=row.candidate_id,
                                timeframe=row.timeframe, side=side, entry_ms=entry_ms,
                                entry_price=float(hourly.at[entry_ms, "open"]), rail=rail,
                                stop=rail * (1 - side * cfg["stop_beyond_rail"]), fail_at_ms=None,
                                parent_close_ms=parent_ms, parent_pass=bool(parent.passes)))
    plans = pd.DataFrame(records)
    if plans.duplicated(["arm", "week"]).any():
        raise ValueError("Repeated weekly entry within one policy")
    return plans


def plan_key(row, tick):
    """Share a native fill only for equal direction, open, stop and scheduled exit."""
    stop_ticks = row.stop / tick
    executed_tick = math.floor(stop_ticks + 1e-10) if row.side == 1 else math.ceil(stop_ticks - 1e-10)
    fail = int(row.fail_at_ms) if pd.notna(row.fail_at_ms) else None
    return int(row.side), int(row.entry_ms), round(row.stop, 9), executed_tick, fail


def execute(plans, frame, crosses, cfg, reuse_fills):
    archive = ACTIVE / cfg["archive"]
    old_cfg = json.loads((archive / "config.json").read_text())
    for field in ("history", "symbol", "cutoff_utc", "risk_usdt", "maker", "taker", "price_tick"):
        if cfg[field] != old_cfg[field]:
            raise ValueError(f"Archive assumption mismatch: {field}")
    old_fills = json.loads((archive / "native_fills.json").read_text())
    by_id = {f["trade_id"]: f for f in old_fills["positions"]}
    tick = cfg["price_tick"]
    prior = {plan_key(row, tick): by_id[row.trade_id]
             for row in read_csv(archive / "trades.csv").itertuples()}
    selected, pending, originals = {}, [], {}
    for row in plans.itertuples(index=False):
        key = plan_key(row, tick)
        if key in selected:
            continue
        if key in prior:
            fill = prior[key]
            selected[key] = (fill["trade_id"], "archived_native")
            originals[fill["trade_id"]] = fill
        else:
            trade_id = f"wick-{len(pending):03d}"
            selected[key] = (trade_id, "new_native")
            pending.append(dict(trade_id=trade_id, side=row.side, entry_ms=row.entry_ms,
                                entry_price=row.entry_price, stop=row.stop, fail_at_ms=row.fail_at_ms))
    if reuse_fills:
        saved = json.loads((OUTPUT / "native_fills.json").read_text())
        requested = {p["trade_id"] for p in pending}
        new_records = [r for r in saved["positions"] if r["trade_id"] in requested]
        audit = saved["audit"]
    else:
        new, audit = run_cloud_hourly(frame, pd.DataFrame(pending), crosses, tick=tick,
                                      risk_usdt=cfg["risk_usdt"], maker=cfg["maker"], taker=cfg["taker"])
        new_records = new.to_dict("records")
    if {r["trade_id"] for r in new_records} != {r["trade_id"] for r in pending} or len(new_records) != len(pending):
        raise ValueError("Native fills differ from requested new plans")
    raw = dict(audit=audit, used_positions=len(new_records), positions=new_records)
    originals.update({f["trade_id"]: f for f in new_records})
    result = []
    for row in plans.itertuples(index=False):
        trade_id, source = selected[plan_key(row, tick)]
        f = originals[trade_id]
        if (f["entry_ms"] != row.entry_ms or f["entry_px"] != row.entry_price or
                f["side"] != row.side or abs(f["fixed_stop"] - row.stop) > 1e-9):
            raise ValueError(f"Native execution mismatch: {trade_id}")
        if f["exit_reason"] not in {"fixed_stop", "cloud_flip", "data_end"}:
            raise ValueError(f"Unrequested exit: {trade_id}, {f['exit_reason']}")
        expected = (f["entry_px"] + f["exit_px"]) * f["quantity"] * cfg["taker"]
        if not np.isclose(expected, f["fee_usdt"], atol=2e-8, rtol=1e-7):
            raise ValueError(f"Native commission mismatch: {trade_id}")
        result.append({**row._asdict(), **f, "fill_source": source})
    return enrich(frame, pd.DataFrame(result), cfg["risk_usdt"]), raw


def summarize(frame, rows, cfg):
    peaks = {}
    for threshold in cfg["thresholds_r"]:
        peaks[threshold] = dict(definite=int((rows.peak_lower_r_actual >= threshold).sum()),
                                possible=int((rows.peak_upper_r_actual >= threshold).sum()),
                                realized_net=int((rows.net_r >= threshold).sum()))
    return dict(**stats(rows), **drawdown(frame, rows, cfg["risk_usdt"]), peaks=peaks,
                by_timeframe={k: stats(v) for k, v in rows.groupby("timeframe")},
                by_parent_confirmation={str(k): stats(v) for k, v in rows.groupby("parent_pass")},
                by_exit={k: stats(v) for k, v in rows.groupby("exit_reason")},
                top_three_net_r=float(rows.net_r.nlargest(3).sum()))


def main(reuse_fills=False):
    cfg = json.loads((OUTPUT / "config.json").read_text())
    folder = ACTIVE / cfg["history"] / "inputs/ETH"
    hourly = _bars(folder / "bars1h.csv.gz", HOUR_MS)
    higher = _bars(folder / "bars12h.csv.gz", STEP_12H_MS)
    end = pd.Timestamp(cfg["cutoff_utc"]).value // 1_000_000
    if (hourly.index[-1] + HOUR_MS != end or higher.index[-1] + STEP_12H_MS != end
            or not np.all(np.diff(hourly.index) == HOUR_MS)):
        raise ValueError("Source hours or replay cutoff differ from archive")
    plans = build_plans(cfg, hourly)
    frame = hourly.set_axis(pd.to_datetime(hourly.index, unit="ms", utc=True))
    rows, raw = execute(plans, frame, pd.DataFrame(_cross_index(higher)), cfg, reuse_fills)
    if not np.allclose(rows.net_r * cfg["risk_usdt"], rows.net_usdt, atol=2e-8, rtol=0.):
        raise ValueError("Native net P&L does not reconcile with reported R")
    for col in ("entry_ms", "exit_hour_ms"):
        rows[col.replace("_ms", "_bjt")] = pd.to_datetime(rows[col], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
    rows = rows.sort_values(["entry_ms", "arm"])
    comparison = rows[rows.arm.isin(["ladder", "wait_12h"])].assign(
        cohort=lambda f: f.arm.map({"ladder": "early", "wait_12h": "confirmed"}))
    result = dict(cutoff_utc=cfg["cutoff_utc"],
                  native_new=len(raw["positions"]), native_archived=rows.loc[
                      rows.fill_source == "archived_native", "trade_id"].nunique(),
                  arms={arm: summarize(frame, group, cfg) for arm, group in rows.groupby("arm")},
                  paired_ladder_minus_wait=paired_weeks(comparison))
    for name, content in (("native_fills.json", raw), ("summary.json", result)):
        (OUTPUT / name).write_text(json.dumps(content, allow_nan=False, indent=2) + "\n")
    rows.to_csv(OUTPUT / "trades.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-fills", action="store_true", help="Use native fills saved for this experiment")
    main(parser.parse_args().reuse_fills)
