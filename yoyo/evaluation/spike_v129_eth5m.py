"""SPIKE V12.9 frozen-rule replay on OKX ETHUSDT.P (ETH-USDT-SWAP) 5m.

Source: owner request 2026-10-10 (Notion task #46, "回测 ethusdt.p 5min级别的
给出详细回测数据"). The owner's TradingView chart is OKX ETHUSDT.P. Frozen
config and scope live in ``experiments/active/exp-spike-v129-eth5m-20261010-v1/``.

Decision basis: V12.9 changes only the 1h close trail (2ATR); on 5m it is
V12.8 = V12.6 entries/exits plus display-only add hints, with the completed
15m EMA120 direction gate. The committed ports therefore apply unchanged:
``spike_lowtf_v1_v126`` (OKX 1m loading, complete-bucket aggregation, 5m V9
admission incl. the EMA120 gate, 15m auto-HTF joint pairing) and the
``spike_v128_recent`` side-aware next-open fixed exit (5-bar extreme + 0.2ATR
stop, minimum 2ATR risk, close-2R arms a 4ATR close trail, raw opposite exit,
20bp round trip). Ordinary both-side and long-only joint signals are separate,
overlapping serial ledgers warmed from the first archive bar. The 2026-09-22
OKX low-timeframe run (exp-spike-lowtf-v1-v126-20260922-v1, joint arm "v126")
must be reproduced exactly on every trade it closed; this run only appends
confirmed OKX 1m rows to 2026-10-10 and adds the ordinary arm and controls.

Causality: chart facts use bars through the signal close; 15m EMA120 and
15m line breaks become visible only after their 15m bucket closes. Matched
controls draw one random bar per trade from the same side x UTC week x fold
x causal ATR/close bucket and replay the same exit and cost.
No training, tuning, Pine, monitor or account change.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v128_recent as p
from yoyo.evaluation import spike_v128_recent_report as rep
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-v129-eth5m-20261010-v1")
CONFIG = EXP / "config.json"
TEST = Path("tests/evaluation/test_spike_v129_eth5m.py")
PINE_V129 = Path("yoyo/evaluation/pine/spike_burst_v12_9.pine")
PARITY_FIELDS = ["entry_time", "entry_price", "initial_stop", "exit_time", "exit_price", "exit_reason", "net_r"]
PARENT_END = pd.Timestamp("2026-09-22T14:00:00+00:00")


def config() -> dict:
    cfg = json.loads(CONFIG.read_text())
    if (cfg["experiment_id"] != EXP.name or cfg["timeframe_min"] != 5 or cfg["htf_min"] != low.HTF_OF[5]
            or float(cfg["round_trip_cost"]) != .002 or cfg["venue"] != "okx"):
        raise ValueError("invalid frozen V12.9 ETH 5m config")
    return cfg


def sources() -> dict[str, str]:
    """Refuse to build unless every builder, test, config and imported source is committed."""
    paths = tuple(dict.fromkeys([CONFIG, Path(__file__), TEST, PINE_V129, p.PINE, p.PINE_V128,
                                 *_local_transitive_python((Path(__file__),))]))
    if not _committed(paths):
        raise ValueError("Commit every builder, test, config and imported source before construction")
    return {str(x): p.digest(x) for x in paths}


def prepare_data(cfg: dict) -> dict:
    """Verify the registered archive/API 1m files and append a confirmed OKX 1m tail."""
    root = Path(cfg["data_dir"])
    receipt_path = root / "receipt.json"
    if receipt_path.exists():
        saved = json.loads(receipt_path.read_text())
        if saved["config_sha256"] != p.digest(CONFIG) or any(p.digest(Path(k)) != v for k, v in saved["inputs"].items()):
            raise ValueError("existing research data changed")
        return saved
    code = sources()
    archive, api = json.loads(Path(cfg["archive_receipt"]).read_text()), json.loads(Path(cfg["api_receipt"]).read_text())
    archive_path = Path(cfg["archive_receipt"]).parent / Path(archive["output_path"]).name
    if archive["status"] != "complete" or archive["months_missing"] or p.digest(archive_path) != archive["output_sha256"]:
        raise ValueError("archive incomplete or changed")
    if p.digest(Path(api["path"])) != api["sha256"] or pd.Timestamp(api["end_exclusive"]) != pd.Timestamp(cfg["tail_start"]):
        raise ValueError("registered API 1m file changed")
    tail = low.fetch_recent(cfg["symbol"], pd.Timestamp(cfg["tail_start"]), pd.Timestamp(cfg["end"]), root)
    paths = [archive_path, Path(api["path"]), Path(tail["path"])]
    base = low.load_base(paths, pd.Timestamp(cfg["end"]))
    bars, partial = low.complete_bars(base, 5)
    expected = pd.date_range(base.index[0], pd.Timestamp(cfg["end"]), freq="5min", inclusive="left")
    saved = {"inputs": {str(x): p.digest(x) for x in paths}, "rows_1m": len(base), "rows_5m": len(bars),
             "missing_5m_slots": int(len(expected) - len(bars)), "partial_5m_buckets": partial,
             "tail_missing_minutes": tail["missing_minutes"], "first": str(base.index[0]), "last_1m": str(base.index[-1]),
             "config_sha256": p.digest(CONFIG), "code": code,
             "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    p.dump(receipt_path, saved)
    return saved


def replay(cfg: dict, data: dict) -> dict[str, Any]:
    """Ordinary (both sides) and joint (long) serial ledgers, the parity ledger, and controls."""
    paths = [Path(x) for x in data["inputs"]]
    if any(p.digest(x) != data["inputs"][str(x)] for x in paths):
        raise ValueError("data hash drift")
    base = low.load_base(paths, pd.Timestamp(cfg["end"]))
    minutes, tick = int(cfg["timeframe_min"]), float(cfg["tick"])
    bars, _ = low.complete_bars(base, minutes)
    facts = low.pine_facts(bars, bars, cfg["asset"], tick, minutes)
    frame = facts["frame"]
    joints, info = low.v126_joints(frame, facts, base, tick, minutes)
    identity = {"venue": cfg["venue"], "symbol": cfg["symbol"], "asset": cfg["asset"], "timeframe_min": minutes}
    key = f"okx:{cfg['symbol']}:{minutes}m"
    prepared = p.source.prepared_arm(frame, facts["gap"], facts["side"], key, identity, minutes, tick)
    start, end = pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"])
    family = {"ordinary": [(int(i), int(facts["side"][i]), {}) for i in np.flatnonzero(facts["v9"])],
              "joint": [(int(j["joint_i"]), 1, {"pair_source": j["source"], "box_entry_i": int(j["box_entry_i"])})
                        for j in joints]}
    trades, statuses, candidates, parity_events = [], [], [], []
    for arm, points in family.items():
        events = []
        for i, side, extra in sorted(points, key=lambda x: x[0]):
            close_time = frame.index[i] + pd.Timedelta(minutes=minutes)
            if close_time >= end:
                continue
            events.append({**identity, **extra, "arm": arm, "signal_i": i, "side": side,
                           "signal_bar_open": frame.index[i], "signal_close": close_time,
                           "in_window": bool(close_time >= start), "signal_price": float(frame.close.iloc[i]),
                           "signal_atr_pct": float(frame.atr.iloc[i] / frame.close.iloc[i]),
                           "m15_ema120": float(facts["ma"][i])})
        t, s = p.serial(prepared, events, arm, key)
        trades.extend(t); statuses.extend(s)
        candidates.extend(e for e in events if e["in_window"])
        if arm == "joint":
            # The parent run occupied positions only from its window start; mirror that for parity.
            parity_events = [e for e in events if e["in_window"] and e["signal_close"] < PARENT_END]
    parity_trades, _ = p.serial(prepared, parity_events, "joint", key)
    table = pd.DataFrame(trades)
    table["censored"] = table.censored.astype(bool)
    closed = table.loc[~table.censored]
    np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
    np.testing.assert_allclose(closed.net_r, closed.net_return / closed.initial_risk_frac, atol=1e-10)
    if table.trade_key.duplicated().any():
        raise ValueError("duplicate trade identity")
    controls = p.matched_controls(prepared, trades, cfg)
    info = {**info, "rows_5m": len(frame), "first": str(frame.index[0]), "last": str(frame.index[-1]),
            "gaps": int(facts["gap"].sum()), "native_pine_parity": False}
    return {"trades": table, "controls": controls, "statuses": pd.DataFrame(statuses),
            "candidates": pd.DataFrame(candidates), "parity_trades": pd.DataFrame(parity_trades), "info": info}


def parent_parity(parity_trades: pd.DataFrame, cfg: dict) -> dict:
    """Every joint trade the parent closed must reappear field-for-field, and no extra one may appear."""
    parent = pd.read_csv(cfg["parent_trades"])
    parent = parent.loc[(parent.arm == "v126") & ~parent.censored.astype(bool)].copy()
    parent["signal_i"] = parent.signal_i.astype(int)
    mine = parity_trades.loc[~parity_trades.censored.astype(bool)
                             & (pd.to_datetime(parity_trades.exit_time, utc=True) < PARENT_END)].copy()
    mine = mine.assign(signal_i=mine.signal_i.astype(int)).set_index("signal_i")
    missing, extra = sorted(set(parent.signal_i) - set(mine.index)), sorted(set(mine.index) - set(parent.signal_i))
    if missing or extra:
        raise ValueError(f"parent joint ledger differs: missing {missing[:5]} extra {extra[:5]}")
    left = parent.set_index("signal_i")[PARITY_FIELDS].sort_index()
    right = mine.loc[left.index, PARITY_FIELDS]
    for col in ("entry_time", "exit_time"):
        left[col], right[col] = pd.to_datetime(left[col], utc=True), pd.to_datetime(right[col], utc=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False, check_exact=False, rtol=1e-9, atol=1e-9)
    return {"parent_closed_joint_trades_checked": len(left), "exact": True}


def _clean(value):
    """JSON-safe copy: numpy scalars to Python, non-finite floats to null."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _streaks(values: np.ndarray) -> int:
    best = run = 0
    for loss in values:
        run = run + 1 if loss else 0
        best = max(best, run)
    return best


def _group_metrics(g: pd.DataFrame, cfg: dict) -> dict:
    g = g.sort_values(["exit_time", "trade_key"])
    row = rep.metrics(g)
    if not len(g):
        return row
    row["max_consecutive_losses"] = _streaks((g.net_r < 0).to_numpy())
    row["mean_gross_r"] = float(g.gross_r.mean())
    row["mean_fee_r"] = float(g.fee_r.mean())
    row["sum_net_r_without_top5"] = float(g.net_r.sum() - g.net_r.nlargest(5).sum())
    row["sum_net_return_pct"] = float(100 * g.net_return.sum())
    row["exit_reasons"] = g.exit_reason.value_counts().to_dict()
    m = g.loc[g.matched]
    row["matched"] = int(len(m))
    row["control_mean_net_r"] = float(m.control_net_r.mean())
    row["control_mean_net_bp"] = float(m.control_net_bp.mean())
    row["mean_net_bp_matched"] = float(m.net_bp.mean())
    row["random_excess"] = rep.block_inference(m.net_bp - m.control_net_bp, m.week, seed=cfg["stat_seed"])
    return row


def summarize(tables: dict, cfg: dict) -> dict:
    """Closed-trade metrics per arm/side and window; random excess with UTC-week blocks."""
    trades = rep.enrich(tables["trades"], pd.Timestamp(cfg["split"]))
    trades["side_name"] = np.where(trades.side == 1, "long", "short")
    trades["year"] = trades.signal_close.dt.year
    trades["group"] = np.where(trades.arm.eq("joint"), "joint_long", "ordinary_" + trades.side_name)
    controls = tables["controls"].set_index("trade_key")
    trades["control_net_r"] = trades.trade_key.map(controls.control_net_r)
    trades["control_net_bp"] = trades.trade_key.map(controls.control_net_return) * 1e4
    trades["matched"] = trades.trade_key.map(controls.matched).fillna(False).astype(bool)
    closed = trades.loc[~trades.censored].copy()
    end = pd.Timestamp(cfg["end"])
    windows = {"full": closed.signal_close >= pd.Timestamp(cfg["start"]),
               "earlier": closed.period.eq("earlier"), "last_12m": closed.signal_close >= pd.Timestamp(cfg["split"]),
               "last_90d": closed.signal_close >= end - pd.Timedelta(days=90),
               "last_30d": closed.signal_close >= end - pd.Timedelta(days=30)}
    groups = {"ordinary_all": closed.arm.eq("ordinary"), "ordinary_long": closed.group.eq("ordinary_long"),
              "ordinary_short": closed.group.eq("ordinary_short"), "joint_long": closed.arm.eq("joint")}
    out = {w: {g: _group_metrics(closed.loc[wm & gm], cfg) for g, gm in groups.items()} for w, wm in windows.items()}
    out["status_counts"] = tables["statuses"].groupby(["arm", "status"]).size().unstack(fill_value=0).to_dict("index")
    out["censored_open"] = trades.loc[trades.censored, ["arm", "side", "signal_close", "entry_price", "initial_stop",
                                                        "exit_reason"]].astype(str).to_dict("records")
    yearly = rep.grouped_metrics(closed, ["group", "year"])
    monthly = rep.grouped_metrics(closed.assign(group=np.where(closed.arm.eq("joint"), "joint_long", "ordinary_all")),
                                  ["group", "month"])
    return {"summary": out, "yearly": yearly, "monthly": monthly, "closed": closed}


def run(stage: str) -> None:
    cfg = config()
    data = prepare_data(cfg)
    if stage == "data":
        print(json.dumps({k: v for k, v in data.items() if k != "code"}))
        return
    code = sources()
    out = EXP / "run_v1"
    out.mkdir(parents=True, exist_ok=True)
    tables = replay(cfg, data)
    parity = parent_parity(tables["parity_trades"], cfg)
    for name in ("trades", "controls", "statuses", "candidates"):
        tables[name].to_csv(out / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    result = summarize(tables, cfg)
    result["yearly"].to_csv(out / "yearly.csv", index=False)
    result["monthly"].to_csv(out / "monthly.csv", index=False)
    detail = result["closed"][["arm", "side_name", "signal_close", "entry_time", "entry_price", "initial_stop",
                               "exit_time", "exit_price", "exit_reason", "initial_risk_frac", "gross_r", "net_r",
                               "net_bp", "mfe_known_r", "close_peak_r", "holding_hours", "control_net_r"]].copy()
    for col in ("signal_close", "entry_time", "exit_time"):
        detail[col] = detail[col].dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d %H:%M")
    detail.sort_values(["arm", "entry_time"]).to_csv(out / "trades_beijing.csv", index=False)
    files = ("trades.csv.gz", "controls.csv.gz", "statuses.csv.gz", "candidates.csv.gz", "yearly.csv", "monthly.csv",
             "trades_beijing.csv")
    receipt = {"run": "v1", "config_sha256": p.digest(CONFIG), "data": data["inputs"], "code": code, "parity": parity,
               "info": tables["info"], "summary": result["summary"], "files": {f: p.digest(out / f) for f in files},
               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    p.dump(out / "receipt.json", _clean(receipt))
    print(json.dumps({"parity": parity, "info": tables["info"]}, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="SPIKE V12.9 OKX ETH 5m replay")
    parser.add_argument("stage", choices=("data", "replay"))
    run(parser.parse_args().stage)


if __name__ == "__main__":
    main()
