"""V10.4 joint 1h: one-shot pre-registered test on Binance data from 2026-05.

Source: owner request 2026-09-22 (win rate >= 77% by any means, even with very
few trades) and approval ("开始") of the plan in
``experiments/active/exp-spike-v104-1h-forward-20260922-v1/``.

Signal: the frozen V10.4 joint (break+spike, long only) at 1h, computed by the
unchanged ``spike_v10_4_study.v9_facts`` and ``spike_v10_4.joint_events`` on
the same 638 Binance USD-M symbols.  The pipeline copy here only makes the
evaluation window a parameter; a test pins trade-by-trade equality with the
original ``run_v1`` 1h joint ledger on the original window.

Exits (independent serial books per arm): ``original`` is the published
fixed-entry engine (initial stop, close 2R arms a 4ATR trail, raw reverse
signal exits next open).  ``tp_x`` adds a resting limit at entry + x * initial
risk; a bar touching both stop and target counts as the stop (conservative);
an open gapping through the target fills at the target price.  With no target
the loop reproduces ``spike_v1_v8_be05.replay_fixed_entry`` exactly (tested).

Data: frozen 5m archive (to 2026-05-01) as warmup, plus Binance official
monthly 5m archives (checksum verified) for 2026-05..2026-08 and closed REST
5m klines to ``data_end``.  1h bars use the study's ``aggregate``.  Features
use bars through the signal close; fills start at the next open.  Matched
random: same symbol, UTC month and ATR/close bucket, same exit and cost.
No training, Pine, monitor, sizing or account change.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

from yoyo.data import binance_um_archives as archives
from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v1_v8_be05 as fixed
from yoyo.evaluation.spike_v10_4 import V104Params, joint_events
from yoyo.evaluation.spike_v7_fast import _initial_position_fast
from yoyo.evaluation.spike_v8_six_filters import _committed

base = fixed.base
EXP = Path("experiments/active/exp-spike-v104-1h-forward-20260922-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
TEST = Path("tests/evaluation/test_spike_v104_forward.py")
MINUTES = 60
REST = "https://fapi.binance.com/fapi/v1/klines"
_lock = threading.Lock()
_last = [0.0]


def config() -> dict:
    return json.loads(CONFIG.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path: Path, value) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n")


# ---------------------------------------------------------------------- data

def _rest_get(url: str) -> list | None:
    """Throttled public GET (<= 4 req/s, klines weight 10 -> 2400/min cap)."""
    for attempt in range(6):
        with _lock:
            wait = 0.25 - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            request = urllib.request.Request(url, headers=archives.REQUEST_HEADERS)
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 400:
                return None          # unknown / delisted symbol
            if attempt == 5:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 5:
                raise
        time.sleep(2 ** attempt)
    return None


def fetch_rest(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame | None:
    """Closed 5m klines with open in [start, end); ``None`` when the symbol is unknown."""
    rows, cursor, end_ms = [], start.value // 10**6, end.value // 10**6
    while cursor < end_ms:
        data = _rest_get(f"{REST}?symbol={symbol}&interval=5m&startTime={cursor}&endTime={end_ms - 1}&limit=1500")
        if data is None:
            return None if not rows else pd.DataFrame(rows)
        if not data:
            break
        for r in data:
            ts = int(r[0])
            if ts + 300_000 <= end_ms:
                rows.append({"ts": ts, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                             "close": float(r[4]), "volume": float(r[5])})
        nxt = int(data[-1][0]) + 300_000
        if nxt <= cursor:
            break
        cursor = nxt
    frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return frame.drop_duplicates("ts").sort_values("ts")


def fetch_symbol(symbol: str, cfg: dict) -> dict:
    out = Path(cfg["data_dir"])
    audit_path = out / "audits" / f"{symbol}.json"
    if audit_path.exists():
        prior = json.loads(audit_path.read_text())
        if prior.get("status") in ("complete", "no_data"):
            return prior
    frames, months = [], []
    for month in cfg["archive_months"]:
        frame, audit = archives._download_month(symbol=symbol, month=month, download_dir=out / "downloads",
                                                interval="5m")
        months.append({k: audit.get(k) for k in ("month", "status", "reason", "rows", "sha256", "zip_path")})
        if frame is not None:
            frames.append(frame[["ts", "open", "high", "low", "close", "volume"]])
    rest = fetch_rest(symbol, pd.Timestamp(cfg["rest_start"]), pd.Timestamp(cfg["data_end"]))
    if rest is not None and len(rest):
        frames.append(rest)
    result = {"symbol": symbol, "months": months, "rest_rows": 0 if rest is None else len(rest),
              "rest_status": "unknown_symbol" if rest is None else "ok", "status": "no_data", "path": None}
    if frames:
        combined = pd.concat(frames, ignore_index=True).drop_duplicates("ts").sort_values("ts")
        if combined.ts.duplicated().any():
            raise ValueError(f"conflicting rows for {symbol}")
        combined["open_time"] = pd.to_datetime(combined.ts, unit="ms", utc=True).astype(str)
        path = out / "series" / f"{symbol}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(path, index=False)
        result.update(status="complete", path=str(path), sha256=digest(path), rows=len(combined),
                      first_time=str(combined.open_time.iloc[0]), last_time=str(combined.open_time.iloc[-1]))
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    dump(audit_path, result)
    return result


def fetch(workers: int = 8) -> None:
    cfg = config()
    if not _committed((Path(__file__), CONFIG, PLAN, TEST)):
        raise ValueError("commit fetch code/plan/config/tests before downloading")
    symbols = sorted(study.series_files())
    done, failed = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_symbol, s, cfg): s for s in symbols}
        for n, future in enumerate(as_completed(futures), 1):
            try:
                done.append(future.result())
            except Exception as exc:  # recorded, rerun resumes
                failed.append({"symbol": futures[future], "error": repr(exc)})
            if n % 50 == 0:
                print(json.dumps({"done": n, "total": len(symbols), "failed": len(failed)}), flush=True)
    summary = {"symbols": len(symbols), "complete": sum(r["status"] == "complete" for r in done),
               "no_data": sum(r["status"] == "no_data" for r in done), "failed": failed,
               "fetched_at": pd.Timestamp.now(tz="UTC").isoformat()}
    dump(Path(cfg["data_dir"]) / "fetch_summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "failed"} | {"failed": len(failed)}))


def load_5m(old: Path, new: Path | None, since: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Frozen archive rows plus forward rows; overlapping timestamps must agree."""
    cols = ["ts", "open", "high", "low", "close", "volume"]
    parts = [pd.read_csv(old, usecols=cols)]
    if new is not None and Path(new).exists():
        parts.append(pd.read_csv(new, usecols=cols))
    raw = pd.concat(parts, ignore_index=True)
    raw = raw[(raw.ts >= since.value // 10**6) & (raw.ts + 300_000 <= end.value // 10**6)]
    dup = raw[raw.ts.duplicated(keep=False)]
    if len(dup) and len(dup.drop_duplicates()) != dup.ts.nunique():
        raise ValueError("frozen and forward rows disagree")
    raw = raw.drop_duplicates("ts").sort_values("ts")
    index = pd.DatetimeIndex(pd.to_datetime(raw.ts.to_numpy(), unit="ms", utc=True))
    return pd.DataFrame(raw[cols[1:]].to_numpy(float), index=index, columns=cols[1:])


# -------------------------------------------------------------------- exits

def replay_tp(prepared, row: pd.Series, tp_r: float | None) -> dict:
    """``spike_v1_v8_be05.replay_fixed_entry`` (BE off, long) plus an optional target."""
    context, frame, gap, raw_side, spec = prepared.context, prepared.frame, prepared.gap, prepared.raw_side, prepared.spec
    oa, ha, la, ca, aa = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr
    side = int(row.side)
    if side != 1:
        raise ValueError("long only")
    start = int(frame.index.get_loc(pd.Timestamp(row.entry_time)))
    pos = {k: row[k] for k in ("signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "entry_price",
                               "initial_stop", "initial_risk", "initial_risk_frac")}
    pos["frozen_index_offset"] = int(row.entry_i) - start
    pos = base._new_trade(pos, trade_id=f"{context.key}:tp:fixed", cohort=prepared.cohort, policy="baseline",
                          context=context)
    pos.update(protection=float(row.initial_stop), mfe_r=0., trail_armed=False, be_armed=False, be_trigger_count=0)
    entry, risk = float(pos["entry_price"]), float(pos["initial_risk"])
    target = math.inf if tp_r is None else math.ceil((entry + tp_r * risk) / spec.tick - 1e-9) * spec.tick

    def close_out(i, price, reason):
        pos["qty_realized"], pos["qty_remaining"] = 1., 0.
        pos["realized_gross_return"] = price / entry - 1
        pos["realized_net_return"] = pos["realized_gross_return"] - base.ENTRY_COST - base.EXIT_COST
        pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, frame.index[i], price, reason
        return base._trade_row(pos, censored=False, precision="bar_open_or_intrabar_window")

    pending_reverse = False
    for i in range(start, len(frame)):
        if gap[i]:
            pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_reason"] = i, frame.index[i], "data_gap_censored"
            return base._trade_row(pos, censored=True, precision="unknown_gap")
        protection = float(pos["protection"])
        if pending_reverse:
            price = oa[i]
            if price <= protection:
                reason = "trailing_stop_gap" if protection != float(pos["initial_stop"]) else "initial_stop_gap"
            elif price >= target:
                return close_out(i, target, "take_profit_gap")
            else:
                reason = "opposite_v6_next_open"
            return close_out(i, price, reason)
        if la[i] <= protection:
            price = min(oa[i], protection)
            reason = "trailing_stop" if protection != float(pos["initial_stop"]) else "initial_stop"
            if oa[i] <= protection:
                reason += "_gap"
            return close_out(i, price, reason)
        if ha[i] >= target:
            return close_out(i, target, "take_profit_gap" if oa[i] >= target else "take_profit")
        fixed._close_updates(pos, high=ha[i], low=la[i], close=ca[i], atr=aa[i], spec=spec, enable_be=False,
                             events=[], context=context, arm="v8", policy="baseline", i=i)
        if int(raw_side[i]) == -1:
            pending_reverse = True
    pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = len(frame) - 1, frame.index[-1], ca[-1], "boundary_mark"
    return base._trade_row(pos, censored=True, precision="last_complete_close")


def attempt(prepared, i: int, tp_r: float | None) -> tuple[str, dict | None]:
    """One long signalled at bar i's close, filled at the next open."""
    if i + 1 >= len(prepared.frame):
        return "no_next_bar", None
    if prepared.gap[i + 1]:
        return "next_bar_is_gap", None
    row = _initial_position_fast(prepared.frame.index, prepared.open, prepared.high, prepared.low,
                                 prepared.close, prepared.atr, prepared.gap, i, 1, prepared.spec)
    if row is None:
        return "risk_invalid", None
    result = replay_tp(prepared, pd.Series(row), tp_r)
    if not result["censored"]:
        return "closed", result
    return ("censored_boundary" if result["exit_reason"] == "boundary_mark" else "censored_gap"), result


# -------------------------------------------------------------------- stream

def joint_candidates(bars: pd.DataFrame, meta: dict, start: pd.Timestamp, end: pd.Timestamp):
    """The study's V9 facts and V10.4 joint events, restricted to [start, end) closes."""
    tick, asset = float(meta["tick"]), meta["asset"]
    facts = study.v9_facts(bars, MINUTES, asset, tick)
    frame = facts["frame"]
    joint = joint_events(frame.open, frame.high, frame.low, frame.close, frame.atr,
                         can_run=facts["can_run"], confirmed_long=facts["v9_long"],
                         parent_high=facts["parent_high"], parent_low=facts["parent_low"],
                         raw_side=facts["side"], long_alive=facts["long_alive"], momentum=facts["momentum"],
                         current_gate=facts["current_gate"], ref_long_exit=facts["ref_long_exit"],
                         tick=tick, params=V104Params())
    close_time = frame.index + pd.Timedelta(minutes=MINUTES)
    window = np.asarray((close_time >= start) & (close_time < end))
    return facts, joint, window


def serial(prepared, candidates, tp_r) -> tuple[list[dict], dict]:
    trades, counts, flat_from = [], {"candidates": int(len(candidates)), "skipped_in_position": 0, "invalid": 0}, -1
    for i in candidates:
        i = int(i)
        if i < flat_from:
            counts["skipped_in_position"] += 1
            continue
        status, result = attempt(prepared, i, tp_r)
        if result is None:
            counts["invalid"] += 1
            continue
        trades.append({**{k: result.get(k) for k in study.TRADE_KEEP}, "status": status})
        flat_from = len(prepared.frame) + 1 if status == "censored_boundary" else int(result["exit_i"])
    return trades, counts


def controls(prepared, trades: pd.DataFrame, ready, window, tp_r, cfg: dict) -> pd.DataFrame:
    frame = prepared.frame
    with np.errstate(invalid="ignore", divide="ignore"):
        vol = np.searchsorted(np.asarray(cfg["vol_bins"]), prepared.atr / prepared.close, side="left")
    month = np.asarray(frame.index.strftime("%Y-%m"))
    eligible = ready & np.isfinite(prepared.atr) & (prepared.atr > 0) & (prepared.close > 0) & window
    pools, rows = {}, []
    for t in trades.itertuples(index=False):
        i = int(t.signal_i)
        key = (month[i], int(vol[i]))
        if key not in pools:
            pools[key] = np.flatnonzero(eligible & (month == key[0]) & (vol == key[1]))
        choices = pools[key][pools[key] != i]
        chosen, result, reason = None, None, "empty_stratum"
        if len(choices):
            token = int(hashlib.sha256(f"{cfg['control_seed']}|{t.trade_key}".encode()).hexdigest(), 16)
            chosen = int(choices[token % len(choices)])
            _, result = attempt(prepared, chosen, tp_r)
            reason = "invalid" if result is None else "censored" if result["censored"] else "matched"
        matched = reason == "matched" and not bool(t.censored)
        rows.append({"trade_key": t.trade_key, "arm": t.arm, "matched": matched,
                     "reason": "target_censored" if t.censored else reason, "control_signal_i": chosen,
                     "control_net_r": result["net_r"] if matched else math.nan,
                     "control_net_return": result["net_return"] if matched else math.nan,
                     "month": key[0], "vol_bin": key[1]})
    return pd.DataFrame(rows)


def run_stream(symbol: str, bars: pd.DataFrame, meta: dict, start: pd.Timestamp, end: pd.Timestamp,
               arms: dict, cfg: dict) -> dict:
    facts, joint, window = joint_candidates(bars, meta, start, end)
    frame = facts["frame"]
    key = f"binance_um:{symbol}:1h"
    identity = {"venue": "binance_um", "symbol": symbol, "asset": meta["asset"], "timeframe": "1h",
                "timeframe_min": MINUTES}
    prepared = study.prepared_arm(frame, facts["gap"], facts["side"], key, identity, MINUTES, float(meta["tick"]))
    candidates = np.flatnonzero(joint.joint_event & window)
    tables, ctrl, counts = [], [], {}
    for arm, tp_r in arms.items():
        rows, counts[arm] = serial(prepared, candidates, tp_r)
        table = pd.DataFrame(rows, columns=[*study.TRADE_KEEP, "status"])
        table["arm"], table["symbol"] = arm, symbol
        table["trade_key"] = key + ":" + arm + ":" + table.signal_i.astype(int).astype(str)
        table["censored"] = table.censored.astype(bool)
        tables.append(table)
        if len(table):
            ctrl.append(controls(prepared, table, facts["ready"], window, tp_r, cfg))
    trades = pd.concat(tables, ignore_index=True)
    summary = {"symbol": symbol, "bars": len(frame), "bars_in_window": int(window.sum()),
               "joint_in_window": int(len(candidates)), "v9_long_in_window": int((facts["v9_long"] & window).sum()),
               "gaps": int(facts["gap"].sum()), "serial": counts}
    return {"trades": trades, "controls": pd.concat(ctrl, ignore_index=True) if ctrl else pd.DataFrame(),
            "summary": summary}


def worker(args) -> dict:
    symbol, old, new, meta, out, run_hash, cfg = args
    directory = Path(out) / "streams" / symbol
    if (directory / "receipt.json").exists():
        receipt = json.loads((directory / "receipt.json").read_text())
        if receipt["run_identity"] != run_hash:
            raise ValueError("resume identity mismatch")
        return receipt
    start, end = pd.Timestamp(cfg["window_start"]), pd.Timestamp(cfg["data_end"])
    since = start - pd.Timedelta(minutes=cfg["warmup_bars"] * MINUTES)
    base5m = load_5m(Path(old), None if new is None else Path(new), since, end)
    bars, partial = study.aggregate(base5m.loc[base5m.index >= since.floor("60min")], MINUTES)
    close_time = bars.index + pd.Timedelta(minutes=MINUTES)
    directory.mkdir(parents=True, exist_ok=True)
    if len(bars) == 0 or not ((close_time >= start) & (close_time < end)).any():
        receipt = {"symbol": symbol, "run_identity": run_hash, "status": "no_bar_in_window", "files": {}}
        dump(directory / "receipt.json", receipt)
        return receipt
    out_parts = run_stream(symbol, bars, meta, start, end, cfg["arms"], cfg)
    files = {}
    for name in ("trades", "controls"):
        path = directory / f"{name}.csv.gz"
        out_parts[name].to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
        files[path.name] = digest(path)
    receipt = {"symbol": symbol, "run_identity": run_hash, "status": "complete", "partial_buckets": partial,
               "summary": out_parts["summary"], "files": files}
    dump(directory / "receipt.json", receipt)
    return receipt


def run(out: Path, workers: int = 8) -> None:
    cfg = config()
    code = (Path(__file__), CONFIG, PLAN, TEST)
    if not _committed(code):
        raise ValueError("commit code/plan/config/tests before replay")
    if (base.ENTRY_COST + base.EXIT_COST) != cfg["round_trip_cost"]:
        raise ValueError("cost contract changed")
    olds = study.series_files()
    meta = study.symbol_meta()
    data = Path(cfg["data_dir"])
    audits = {s: json.loads((data / "audits" / f"{s}.json").read_text()) for s in olds}
    news = {s: (a["path"] if a["status"] == "complete" else None) for s, a in audits.items()}
    for s, a in audits.items():
        if a["status"] == "complete" and digest(Path(a["path"])) != a["sha256"]:
            raise ValueError(f"forward data drift for {s}")
    identity = {"config": cfg, "code": {str(p): digest(p) for p in code},
                "old_inputs": {s: digest(p) for s, p in olds.items()},
                "new_inputs": {s: audits[s].get("sha256") for s in olds},
                "exchange_info": digest(study.EXCHANGE_INFO)}
    run_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    out = Path(out)
    if (out / "identity.json").exists():
        if json.loads((out / "identity.json").read_text()) != identity:
            raise ValueError("resume identity changed")
    else:
        out.mkdir(parents=True, exist_ok=True)
        dump(out / "identity.json", identity)
        dump(out / "started.json", {"run_identity": run_hash, "started_at": pd.Timestamp.now(tz="UTC"),
                                    "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()})
    tasks = [(s, str(olds[s]), news[s], meta.get(s, {"asset": "", "tick": math.nan}), str(out), run_hash, cfg)
             for s in sorted(olds)]
    results, errors = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, t): t[0] for t in tasks}
        for n, future in enumerate(as_completed(futures), 1):
            try:
                results.append(future.result())
            except Exception as exc:
                errors.append({"symbol": futures[future], "error": repr(exc)})
            if n % 50 == 0:
                print(json.dumps({"done": n, "total": len(tasks), "errors": len(errors)}), flush=True)
    dump(out / "manifest.json", {"complete": not errors, "run_identity": run_hash, "errors": errors,
                                 "streams": len(results), "with_window": sum(r["status"] == "complete" for r in results),
                                 "training_eligible": False, "production_eligible": False})
    print(json.dumps({"streams": len(results), "errors": len(errors)}))


# --------------------------------------------------------------------- stats

def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return math.nan, math.nan
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def block_test(x: pd.Series, blocks: pd.Series, rng, reps: int, flips: int):
    frame = pd.DataFrame({"x": x.to_numpy(float), "b": blocks.to_numpy()})
    g = frame.groupby("b").x
    sums, counts = g.sum().to_numpy(), g.size().to_numpy()
    if len(sums) < 2:
        return math.nan, math.nan, math.nan
    draws = rng.integers(0, len(sums), size=(reps, len(sums)))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    signs = rng.choice([-1.0, 1.0], size=(flips, len(sums)))
    p = float(((signs * sums).sum(axis=1) >= sums.sum()).mean())
    return float(np.quantile(means, .025)), float(np.quantile(means, .975)), p


def report(run_dir: Path, out: Path) -> None:
    cfg = json.loads((run_dir / "identity.json").read_text())["config"]
    rng = np.random.default_rng(cfg["stat_seed"])
    trades, ctrls = [], []
    for d in sorted((run_dir / "streams").iterdir()):
        r = json.loads((d / "receipt.json").read_text())
        if r["status"] != "complete":
            continue
        for name, sha in r["files"].items():
            if digest(d / name) != sha:
                raise ValueError(f"artifact changed: {d / name}")
        trades.append(pd.read_csv(d / "trades.csv.gz"))
        try:  # streams without trades write a column-less controls file
            c = pd.read_csv(d / "controls.csv.gz")
        except pd.errors.EmptyDataError:
            c = pd.DataFrame()
        if len(c):
            ctrls.append(c)
    t = pd.concat(trades, ignore_index=True)
    c = pd.concat(ctrls, ignore_index=True)
    t["entry_time"] = pd.to_datetime(t.entry_time, utc=True)
    t["week"] = t.entry_time.dt.strftime("%G-W%V")
    t["month"] = t.entry_time.dt.strftime("%Y-%m")
    m = t.merge(c[["trade_key", "arm", "matched", "control_net_r", "control_net_return"]], on=["trade_key", "arm"], how="left")
    rows, monthly = [], []
    for arm in cfg["arms"]:
        g = m[m.arm == arm]
        closed = g[~g.censored.astype(bool)]
        r = closed.net_r.astype(float)
        wins = int((r > 0).sum())
        lo, hi = wilson(wins, len(r))
        pairs = closed[closed.matched.fillna(False).astype(bool)]
        ex_r = pairs.net_r - pairs.control_net_r
        ex_bp = (pairs.net_return - pairs.control_net_return) * 1e4
        r_lo, r_hi, r_p = block_test(ex_r, pairs.week, rng, cfg["bootstrap"], cfg["flips"])
        b_lo, b_hi, b_p = block_test(ex_bp, pairs.week, rng, cfg["bootstrap"], cfg["flips"])
        row = {"arm": arm, "entries": len(g), "closed": len(closed), "censored": int(g.censored.astype(bool).sum()),
               "wins": wins, "win_rate": wins / len(r) if len(r) else math.nan, "win_ci_low": lo, "win_ci_high": hi,
               "mean_net_r": r.mean(), "mean_net_bp": closed.net_return.mean() * 1e4, "sum_net_r": r.sum(),
               "pf_r": r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else math.nan,
               "gt5r": int((r > 5).sum()), "gt10r": int((r > 10).sum()),
               "median_risk_bp": closed.initial_risk_frac.median() * 1e4, "symbols": int(closed.symbol.nunique()),
               "pairs": len(pairs), "weeks": int(pairs.week.nunique()),
               "control_net_r": pairs.control_net_r.mean(), "control_net_bp": pairs.control_net_return.mean() * 1e4,
               "excess_r": ex_r.mean(), "excess_r_ci_low": r_lo, "excess_r_ci_high": r_hi, "excess_r_p": r_p,
               "excess_bp": ex_bp.mean(), "excess_bp_ci_low": b_lo, "excess_bp_ci_high": b_hi, "excess_bp_p": b_p}
        row["criterion_a"] = bool(row["win_rate"] >= cfg["win_rate_target"] and row["mean_net_r"] > 0 and row["mean_net_bp"] > 0)
        row["criterion_b"] = bool(row["excess_r"] > 0 and row["excess_bp"] > 0 and r_p < cfg["p_threshold"] and b_p < cfg["p_threshold"])
        rows.append(row)
        for month, mg in closed.groupby("month"):
            mr = mg.net_r.astype(float)
            monthly.append({"arm": arm, "month": month, "closed": len(mg), "win_rate": (mr > 0).mean(),
                            "mean_net_r": mr.mean(), "sum_net_r": mr.sum(), "mean_net_bp": mg.net_return.mean() * 1e4})
    out.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(rows).to_csv(out / "summary.csv", index=False)
    pd.DataFrame(monthly).to_csv(out / "monthly.csv", index=False)
    top = t[~t.censored.astype(bool)].sort_values("net_r", ascending=False).head(20)
    top[["arm", "symbol", "entry_time", "exit_time", "exit_reason", "net_r", "net_return"]].to_csv(out / "top_trades.csv", index=False)
    dump(out / "receipt.json", {"run": str(run_dir), "trades": len(t), "controls": len(c),
                                "files": {n: digest(out / n) for n in ("summary.csv", "monthly.csv", "top_trades.csv")}})
    print(pd.DataFrame(rows)[["arm", "closed", "win_rate", "win_ci_low", "mean_net_r", "mean_net_bp", "excess_r",
                              "excess_r_p", "excess_bp", "excess_bp_p", "criterion_a", "criterion_b"]].round(4).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch"); f.add_argument("--workers", type=int, default=8)
    r = sub.add_parser("replay"); r.add_argument("--output", type=Path, required=True); r.add_argument("--workers", type=int, default=8)
    s = sub.add_parser("report"); s.add_argument("--run", type=Path, required=True); s.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "fetch":
        fetch(args.workers)
    elif args.command == "replay":
        run(args.output, args.workers)
    else:
        report(args.run, args.output)


if __name__ == "__main__":
    main()
