"""SPIKE V1 vs V12.6 research replay on OKX ETH/BTC 1m, 3m and 5m.

Source: owner requests on 2026-09-22 ("test v1 and v12.6 on 1m/3m/5m", "v1 on
the last two months", "through today"). Frozen plan and config live in
``experiments/active/exp-spike-lowtf-v1-v126-20260922-v1/``.

Arms (long only, next-open fills, fixed 20bp round trip on entry notional):

* ``v1_native``  frozen V1 Pine replay (``spike_burst_replay``) on continuous
  segments, every burst independent, V1's own protection path.  The trade rule
  is the 2026-09-11 two-year ledger's ``_trade_rows`` with the window made a
  parameter; a test pins row equality on the old window.
* ``v1_common``  the same V1 burst bars executed by the V12.6 study's shared
  exit engine (5-bar extreme+0.2ATR >= 2ATR stop, close 2R arms a 4ATR trail,
  raw V9 reverse exit) with serial occupancy.
* ``v126``       the V12.6 research port (``spike_v126_engine``) at the chart
  timeframe.  The higher timeframe follows Pine ``f_v11`` auto mapping
  (1m->5m, 3m->15m, 5m->15m).  V9 admission follows Pine defaults; the H1 SMA60
  gate only applies to 15m charts and is therefore absent here, and the
  completed M15 EMA120 gate applies to 5m charts only.

``v1_common`` and ``v126`` differ only in the entry signal.  Matched random
controls draw one bar per trade from the same stream x UTC month x fold x
causal ATR14/close bucket and replay the same exit and cost.

Causality: chart features use bars through the signal close.  Higher-timeframe
lines become visible on the first chart bar opening at or after the HTF close;
the M15 EMA120 uses only 15m buckets closed at or before the chart bar open.
Data: OKX official monthly 1m archive plus confirmed public ``history-candles``
1m for the final partial month; 3m/5m/15m are complete UTC epoch buckets of the
same 1m rows.  This is not native TradingView parity (pivot ties unverified).
No training, parameter search, Pine, monitor or account change.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_increment as execution
from yoyo.evaluation import spike_v10_4_study as source
from yoyo.evaluation import spike_v126_htf_recheck as v126study
from yoyo.evaluation.spike_burst_replay import features, replay
from yoyo.evaluation.spike_v10_4 import reference_long_exits
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v6_wvf_study import _data_gap
from yoyo.evaluation.spike_v7_fast import _detect_confirmed_fast
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_v9_5m_15m_ma import confirmed_ma
from yoyo.evaluation.spike_v9_htf_sma import side_gate

EXP = Path("experiments/active/exp-spike-lowtf-v1-v126-20260922-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
TEST = Path("tests/evaluation/test_spike_lowtf_v1_v126.py")
PINE_V126 = Path("yoyo/evaluation/pine/spike_burst_v12_6.pine")
PINE_V1 = Path("yoyo/evaluation/pine/spike_burst_v1.pine")
# Pine f_v11 auto HTF: s <= 60 -> "5", s <= 300 -> "15" (spike_burst_v12_6.pine:1276).
HTF_OF = {1: 5, 3: 15, 5: 15}
TABLES = ("decisions", "trades", "statuses", "controls")
API = "https://www.okx.com/api/v5/market/history-candles"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept": "application/json"}


def config() -> dict:
    return json.loads(CONFIG.read_text())


def window(cfg: dict) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    return tuple(pd.Timestamp(cfg[k]) for k in ("start", "split", "recent_start", "end"))


def dump(path: Path, data) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n")


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- data

def fetch_recent(symbol: str, start: pd.Timestamp, end: pd.Timestamp, out_dir: Path) -> dict:
    """Confirmed public 1m candles with open in [start, end); pages backwards.

    Columns match the archive output (ts, open, high, low, close, volume,
    open_time).  Only rows with confirm == "1" are kept; a receipt pins SHA.
    """
    instrument = symbol.replace("_", "-")
    start_ms, end_ms = start.value // 10**6, end.value // 10**6
    rows, cursor, pages = {}, end_ms, 0
    while True:
        url = f"{API}?instId={instrument}&bar=1m&after={cursor}&limit=100"
        for attempt in range(6):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as response:
                    payload = json.loads(response.read())
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt)
        pages += 1
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX error {payload.get('code')}: {payload.get('msg')}")
        data = payload["data"]
        if not data:
            break
        for r in data:
            ts = int(r[0])
            if start_ms <= ts < end_ms and str(r[8]) == "1":
                rows[ts] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        oldest = int(data[-1][0])
        if oldest <= start_ms:
            break
        cursor = oldest
        time.sleep(0.12)
    frame = pd.DataFrame([[ts, *v] for ts, v in sorted(rows.items())],
                         columns=["ts", "open", "high", "low", "close", "volume"])
    frame["open_time"] = pd.to_datetime(frame.ts, unit="ms", utc=True).astype(str)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"okx_api_{symbol}_1m_{start:%Y%m%dT%H%M}_{end:%Y%m%dT%H%M}.csv"
    frame.to_csv(path, index=False)
    expected = (end_ms - start_ms) // 60_000
    receipt = {"symbol": symbol, "bar": "1m", "start": start.isoformat(), "end_exclusive": end.isoformat(),
               "rows": len(frame), "expected_minutes": int(expected), "missing_minutes": int(expected - len(frame)),
               "pages": pages, "path": str(path), "sha256": digest(path), "confirm_only": True,
               "fetched_at": pd.Timestamp.now(tz="UTC").isoformat()}
    dump(out_dir / f"api_{symbol}.json", receipt)
    return receipt


def input_files(cfg: dict) -> dict[str, list[Path]]:
    """Archive output plus API tail per symbol, both registered by receipts."""
    root = Path(cfg["data_dir"])
    files = {}
    for symbol in cfg["symbols"]:
        archive = json.loads((root / f"archive_{symbol}.json").read_text())
        api = json.loads((root / f"api_{symbol}.json").read_text())
        if archive["status"] != "complete" or archive["months_missing"]:
            raise ValueError(f"archive incomplete for {symbol}: {archive['months_missing']}")
        paths = [root / Path(archive["output_path"]).name, Path(api["path"])]
        if digest(paths[0]) != archive["output_sha256"] or digest(paths[1]) != api["sha256"]:
            raise ValueError(f"input drift for {symbol}")
        files[symbol] = paths
    return files


def load_base(paths: list[Path], end: pd.Timestamp) -> pd.DataFrame:
    """Merge archive and API 1m rows; conflicting duplicates fail closed."""
    raw = pd.concat([pd.read_csv(p, usecols=["ts", "open", "high", "low", "close", "volume"]) for p in paths])
    raw = raw.loc[raw.ts < end.value // 10**6]
    dup = raw[raw.ts.duplicated(keep=False)]
    if len(dup) and len(dup.drop_duplicates()) != dup.ts.nunique():
        raise ValueError("conflicting duplicate 1m rows between archive and API")
    raw = raw.drop_duplicates("ts").sort_values("ts")
    index = pd.DatetimeIndex(pd.to_datetime(raw.ts.to_numpy(), unit="ms", utc=True))
    if (index.asi8 % pd.Timedelta(minutes=1).value).any():
        raise ValueError("unaligned 1m clock")
    return pd.DataFrame(raw[["open", "high", "low", "close", "volume"]].to_numpy(float), index=index,
                        columns=["open", "high", "low", "close", "volume"])


def complete_bars(base: pd.DataFrame, minutes: int) -> tuple[pd.DataFrame, int]:
    """Complete valid UTC epoch buckets of 1m rows; others stay timestamp gaps."""
    raw = base[["open", "high", "low", "close", "volume"]]
    valid = (np.isfinite(raw).all(axis=1) & raw.low.gt(0) & raw.volume.ge(0)
             & raw.high.ge(raw[["open", "close", "low"]].max(axis=1))
             & raw.low.le(raw[["open", "close", "high"]].min(axis=1)))
    if minutes == 1:
        return raw.loc[valid].copy(), int((~valid).sum())
    groups = raw.resample(f"{minutes}min", origin="epoch", label="left", closed="left")
    bars = groups.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    count = groups.close.count()
    nvalid = valid.astype(int).resample(f"{minutes}min", origin="epoch", label="left", closed="left").sum()
    keep = count.eq(minutes) & nvalid.eq(minutes)
    return bars.loc[keep], int((count.gt(0) & ~keep).sum())


# ------------------------------------------------------------------- V12.6 facts

def pine_facts(bars: pd.DataFrame, base5m: pd.DataFrame, asset: str, tick: float, minutes: int) -> dict:
    """V12.6 V9 facts at any minute chart (copy of the 15m study's ``pine_facts``).

    Differences from the 15m study are only the chart minutes and the Pine
    timeframe gates: no H1 SMA60 (15m-only); completed M15 EMA120 on 5m only.
    """
    frame = features(bars)
    gap = _data_gap(frame, minutes).to_numpy(bool)
    ages = np.arange(len(frame)) - np.maximum.accumulate(np.where(gap, np.arange(len(frame)), 0))
    frame["ready"] &= ages >= 12
    segments = pd.Series(gap.astype(int).cumsum(), index=frame.index)
    med = frame.volume.groupby(segments).transform(lambda v: v.rolling(20, min_periods=20).median())
    frame["rv"] = (frame.volume / med.shift()).where(med.shift().gt(0))
    advance = (frame.close - frame.close.shift(3)) / frame.atr.shift(3)
    volume_ratio = (frame.volume.rolling(3).sum() / (3 * med.shift(3))).where(med.shift(3).gt(0))
    result = {}
    for side, name in ((1, "long"), (-1, "short")):
        conf, hi, lo = v126study._legacy(frame, gap, advance, volume_ratio, side)
        supplied = frame[["open", "high", "low", "close", "md", "sb", "atr", "ropeHigh", "ropeLow", "ready"]].copy()
        supplied["legacy_confirmed"], supplied["legacy_parent_high"], supplied["legacy_parent_low"] = conf, hi, lo
        supplied["advance3"], supplied["volume_ratio3"] = advance, volume_ratio
        supplied["data_gap"], supplied["confirmed"] = gap, True
        result[name] = _detect_confirmed_fast(supplied, side)
        if side == 1:
            known = (frame.ready.to_numpy(bool) & ~gap & frame.atr.gt(0).to_numpy()
                     & np.isfinite(frame[["md", "sb", "atr", "ropeHigh", "middle"]]).all(axis=1).to_numpy())
            result["parent_high"], result["parent_low"] = v126study.structure_parents(
                frame.close.to_numpy(), known, conf, hi, lo, result[name])
    if (result["long"] & result["short"]).any():
        raise ValueError("ambiguous raw direction")
    side = np.where(result["long"], 1, np.where(result["short"], -1, 0))
    c, atr = frame.close.to_numpy(), frame.atr.to_numpy()
    edge = np.where(side == 1, frame.ropeHigh, frame.ropeLow)
    distance = side * (c - edge) / np.where(atr > 0, atr, np.nan)
    bb = v126study.bb_admission(frame.close, ages + 1)
    rv = frame.rv.to_numpy()
    clock = frame.index + pd.Timedelta(minutes=minutes)
    bundle = (asset not in ("", "USDC")) & np.isfinite(rv) & (rv >= 0) & (rv <= 50) & np.asarray(clock.dayofweek != 6)
    if minutes == 5:
        ma = confirmed_ma(base5m, frame.index, lengths=(120,)).ema_120.to_numpy()
        ma_gate = side_gate(c, side, ma)
    else:
        ma, ma_gate = np.full(len(frame), np.nan), np.ones(len(frame), bool)
    final = (side != 0) & bb & bundle & (distance <= 3) & ma_gate
    state: dict = {}
    exits = reference_long_exits(frame.high, frame.low, frame.close, frame.atr, ready=frame.ready, gap=gap,
                                 raw_side=side, signal_side=np.where(final, side, 0), tick=tick, state=state)
    result.update(frame=frame, gap=gap, side=side, v9=final, v9_long=final & (side == 1), ma=ma,
                  ref_long_exit=exits, box=state, ready=frame.ready.to_numpy(bool),
                  can_run=~gap & np.isfinite(atr) & (atr > 0),
                  long_alive=frame.ready.to_numpy(bool) & (atr > 0) & (c > frame.ropeHigh.to_numpy()) & (side != -1))
    return result


def v126_joints(f: pd.DataFrame, facts: dict, base: pd.DataFrame, tick: float, minutes: int) -> tuple[list, dict]:
    """Chart + auto-HTF line events paired with V9 boxes (study ``run_stream`` logic)."""
    from yoyo.evaluation.spike_v126_engine import line_events, pair_events

    htf = HTF_OF[minutes]
    local = line_events(f.open, f.high, f.low, f.close, f.atr, can_run=facts["can_run"], gap=facts["gap"],
                        tick=tick, confirmed_long=facts["v9_long"], parent_high=facts["parent_high"],
                        parent_low=facts["parent_low"], raw_side=facts["side"], long_alive=facts["long_alive"],
                        ref_long_exit=facts["ref_long_exit"])
    higher, hpartial = complete_bars(base, htf)
    hf = features(higher)
    hgap = _data_gap(hf, htf).to_numpy(bool)
    ht = line_events(hf.open, hf.high, hf.low, hf.close, hf.atr,
                     can_run=~hgap & np.isfinite(hf.atr) & hf.atr.gt(0), gap=hgap, tick=tick, htf=True)
    clock = f.index.asi8 // 60_000_000_000
    hclock = hf.index.asi8 // 60_000_000_000
    mapped = []
    for e in ht.winner_events:
        visible_time = int(hclock[int(e.get("i", e.get("break_i")))]) + htf
        j = int(np.searchsorted(clock, visible_time))
        if j >= len(clock) or clock[j] - visible_time >= minutes:
            continue
        mapped.append({**e, "visible_i": j, **{k + "_t": int(hclock[int(e[k])]) for k in ("ax", "bx", "cx")}})
    joints = pair_events(f.close.to_numpy(), bar_times=clock, chart_breaks=local.events, htf_breaks=mapped,
                         box_id=facts["box"]["box_entry"], confirmed_long=facts["v9_long"], gap=facts["gap"])
    info = {"chart_breaks": len(local.events), "htf_breaks_visible": len(mapped), "htf_minutes": htf,
            "htf_partial_buckets": hpartial, "chart_pivot_tie_counts": local.trace["pivot_tie_counts"],
            "htf_pivot_tie_counts": ht.trace["pivot_tie_counts"]}
    return joints, info


# ------------------------------------------------------------------------- V1

def v1_native_rows(bars: pd.DataFrame, replayed: pd.DataFrame, feature_frame: pd.DataFrame, *, minutes: int,
                   start: pd.Timestamp, end: pd.Timestamp, key: str) -> list[dict]:
    """``spike_v1_twoyear_allmarkets._trade_rows`` with the window as parameters.

    Next-open entry, frozen V1 stop, conservative stop-first on a touch, close
    ratchet of V1 ``protection`` only while V1 ``trend_side`` is live, 20bp.
    """
    step, rows = pd.Timedelta(minutes=minutes), []
    for signal_time, signal in replayed.loc[replayed.burst].iterrows():
        position = bars.index.get_loc(signal_time)
        if position + 1 >= len(bars):
            continue
        entry_time, entry = bars.index[position + 1], float(bars.open.iloc[position + 1])
        if not (start <= entry_time < end):
            continue
        protection, exit_position, exit_price, reason = float(signal.initial_stop), None, np.nan, "censored"
        if entry <= protection:
            exit_position, exit_price, reason = position + 1, entry, "entry_gap_through_stop"
        else:
            low, trend, prot = bars.low.to_numpy(float), replayed.trend_side.to_numpy(), replayed.protection.to_numpy(float)
            opens = bars.open.to_numpy(float)
            for j in range(position + 1, len(bars)):
                if low[j] <= protection:
                    exit_position, exit_price, reason = j, min(opens[j], protection), "protective_stop"
                    break
                if bool(trend[j]) and np.isfinite(prot[j]):
                    protection = prot[j]
        if exit_position is None:
            exit_position, exit_price = len(bars) - 1, float(bars.close.iloc[-1])
            exit_time = min(end, bars.index[-1] + step)
        else:
            exit_time = entry_time if reason == "entry_gap_through_stop" else bars.index[exit_position] + step
        held_end = exit_position if reason == "protective_stop" else exit_position + 1
        held = bars.iloc[position + 1:held_end]
        highs = held.high.to_numpy(float) if len(held) else np.array([entry])
        mfe = float(highs.max() / entry - 1)
        gross, net = exit_price / entry - 1, exit_price / entry - 1 - .002
        stop = float(signal.initial_stop)
        risk_fraction = (entry - stop) / entry if entry > stop else np.nan
        rows.append({"trade_key": f"{key}:v1_native:{signal_time.isoformat()}", "arm": "v1_native",
                     "signal_bar_open": signal_time, "signal_close": signal_time + step, "entry_time": entry_time,
                     "entry_price": entry, "initial_stop": stop, "initial_risk_frac": risk_fraction,
                     "exit_time": exit_time, "exit_price": exit_price, "exit_reason": reason,
                     "gross_return": gross, "net_return": net,
                     "gross_r": gross / risk_fraction if np.isfinite(risk_fraction) else np.nan,
                     "net_r": net / risk_fraction if np.isfinite(risk_fraction) else np.nan,
                     "mfe_r": mfe / risk_fraction if np.isfinite(risk_fraction) else np.nan,
                     "censored": reason == "censored", "status": "censored_boundary" if reason == "censored" else "closed"})
    return rows


def v1_signals(bars: pd.DataFrame, tick: float, minutes: int, *, start, end, key) -> tuple[list[pd.Timestamp], list[dict]]:
    """Run frozen V1 per continuous segment (the two-year ledger convention)."""
    step = pd.Timedelta(minutes=minutes).value
    cuts = np.flatnonzero(np.diff(bars.index.asi8) != step) + 1
    bursts, rows = [], []
    for part in np.split(bars, cuts):
        if len(part) < 341:
            continue
        ff = features(part)
        rep = replay(ff, tick)
        bursts.extend(rep.index[rep.burst.to_numpy(bool)].tolist())
        rows.extend(v1_native_rows(part, rep, ff, minutes=minutes, start=start, end=end, key=key))
    return bursts, rows


# -------------------------------------------------------------------- execution

def serial(indices, arm: str, evaluate, n: int, key: str, times) -> tuple[list[dict], list[dict]]:
    """Take a candidate only when flat at its close; every status is kept."""
    rows, statuses, flat, holder = [], [], -1, None
    for i in indices:
        i = int(i)
        base = {"arm": arm, "signal_i": i, "signal_bar_open": times[i]}
        if i < flat:
            statuses.append({**base, "status": "skipped_in_position", "blocking_trade": holder})
            continue
        status, result = evaluate(i)
        statuses.append({**base, "status": status, "blocking_trade": None})
        if result is None:
            continue
        trade_key = f"{key}:{arm}:{times[i].isoformat()}"
        rows.append({**{k: result.get(k) for k in source.TRADE_KEEP}, "status": status, "arm": arm, "trade_key": trade_key})
        holder = trade_key
        flat = n + 1 if status == "censored_boundary" else int(result["exit_i"])
    return rows, statuses


def controls(f: pd.DataFrame, gap, ready, trades: list[dict], evaluate, *, minutes: int, cfg: dict) -> pd.DataFrame:
    """Same stream x UTC month x fold x ATR/close bucket; same exit; no redraw."""
    start, split, _, end = window(cfg)
    clock = f.index + pd.Timedelta(minutes=minutes)
    month = np.asarray(clock.strftime("%Y-%m"))
    folds = np.where(clock < split, "earlier", "later")
    atr, close = f.atr.to_numpy(float), f.close.to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        bins = np.searchsorted(np.asarray(cfg["vol_bins"]), atr / close, side="left")
    valid = ready & ~gap & np.isfinite(atr) & (atr > 0) & (clock >= start) & (clock < end)
    pools, rows = {}, []
    for row in trades:
        i = int(row["signal_i"])
        stratum = (month[i], folds[i], int(bins[i]))
        if stratum not in pools:
            pools[stratum] = np.flatnonzero(valid & (month == stratum[0]) & (folds == stratum[1]) & (bins == stratum[2]))
        options = pools[stratum][pools[stratum] != i]
        j, result, status = None, None, "empty_stratum"
        if len(options):
            token = int(hashlib.sha256(f"{cfg['control_seed']}|{row['trade_key']}".encode()).hexdigest(), 16)
            j = int(options[token % len(options)])
            status, result = evaluate(j)
        matched = result is not None and not result["censored"] and not row["censored"]
        rows.append({"trade_key": row["trade_key"], "arm": row["arm"], "matched": bool(matched),
                     "reason": "target_censored" if row["censored"] else "matched" if matched else status,
                     "control_signal_i": j, "control_signal_time": None if j is None else clock[j],
                     "control_net_r": result["net_r"] if matched else np.nan,
                     "control_net_return": result["net_return"] if matched else np.nan,
                     "month": stratum[0], "fold": stratum[1], "vol_bin": stratum[2]})
    return pd.DataFrame(rows)


def run_stream(base: pd.DataFrame, symbol: str, minutes: int, meta: dict, cfg: dict) -> tuple[dict, dict]:
    """All three arms and controls for one symbol x chart timeframe."""
    start, _, _, end = window(cfg)
    tick, asset = float(meta["tick"]), meta["asset"]
    key = f"okx:{symbol}:{minutes}m"
    bars, partial = complete_bars(base, minutes)
    base5m, _ = complete_bars(base, 5)
    facts = pine_facts(bars, base5m, asset, tick, minutes)
    f = facts["frame"]
    joints, info = v126_joints(f, facts, base, tick, minutes)
    times = f.index
    decisions = []
    for e in joints:
        i = int(e["joint_i"])
        close_time = times[i] + pd.Timedelta(minutes=minutes)
        if not (start <= close_time < end):
            continue
        decisions.append({"signal_i": i, "signal_bar_open": times[i], "signal_close": close_time,
                          "pair_source": e["source"], "order": e.get("order"), "box_entry_i": int(e["box_entry_i"]),
                          "v9_reference_time": times[int(e["box_entry_i"])] + pd.Timedelta(minutes=minutes),
                          "bars_after_v9": i - int(e["box_entry_i"]), "joint_close": float(f.close.iloc[i]),
                          "m15_ema120": float(facts["ma"][i]) if minutes == 5 else np.nan})
    decisions.sort(key=lambda d: d["signal_i"])
    if len({d["box_entry_i"] for d in decisions}) != len(decisions):
        raise ValueError("a V9 box generated multiple joint candidates")
    prepared = source.prepared_arm(f, facts["gap"], facts["side"], key, {"symbol": symbol, "timeframe": f"{minutes}m"},
                                   minutes, tick)
    cache: dict = {}

    def evaluate(i):
        if i not in cache:
            cache[i] = execution.attempt(prepared, i)
        return cache[i]

    bursts, native = v1_signals(bars, tick, minutes, start=start, end=end, key=key)
    burst_i = f.index.get_indexer(pd.DatetimeIndex(bursts))
    if (burst_i < 0).any():
        raise ValueError("V1 burst not on the chart clock")
    close_times = times[burst_i] + pd.Timedelta(minutes=minutes)
    burst_i = burst_i[(close_times >= start) & (close_times < end)]
    trades, statuses = [], []
    for arm, idx in (("v126", [d["signal_i"] for d in decisions]), ("v1_common", sorted(set(burst_i.tolist())))):
        t, s = serial(idx, arm, evaluate, len(f), key, times)
        trades.extend(t); statuses.extend(s)
    ctrl = controls(f, facts["gap"], facts["ready"], trades, evaluate, minutes=minutes, cfg=cfg)
    table = pd.DataFrame(trades + native)
    if len(table):
        table.insert(0, "symbol", symbol); table.insert(1, "timeframe_min", minutes)
        closed = table.loc[~table.censored.astype(bool)]
        np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
    parts = {"decisions": pd.DataFrame(decisions), "trades": table, "statuses": pd.DataFrame(statuses), "controls": ctrl}
    summary = {"symbol": symbol, "minutes": minutes, "chart_bars": len(f), "partial_chart_buckets": partial,
               "chart_gaps": int(facts["gap"].sum()), "v9_long_confirmations": int(facts["v9_long"].sum()),
               "v126_candidates": len(decisions), "v1_bursts_in_window": int(len(burst_i)),
               "v1_native_rows": len(native), **info, "native_pine_parity": False,
               "pivot_tie_policy": "strict_unverified_native"}
    return parts, summary


# --------------------------------------------------------------------- runner

def declared_sources() -> tuple[Path, ...]:
    code = _local_transitive_python((Path(__file__), Path("yoyo/evaluation/spike_v126_engine.py")))
    return (*code, PINE_V126, PINE_V1, CONFIG, PLAN, TEST)


def worker(args) -> dict:
    symbol, minutes, paths, meta, out, run_hash, input_sha, cfg = args
    directory = Path(out) / "streams" / f"{symbol}_{minutes}m"
    if (directory / "receipt.json").exists():
        receipt = json.loads((directory / "receipt.json").read_text())
        if receipt["run_identity"] != run_hash:
            raise ValueError("resume identity mismatch")
        return receipt
    if directory.exists():
        raise ValueError(f"incomplete output: {directory}")
    before = time.perf_counter()
    if [digest(Path(p)) for p in paths] != input_sha:
        raise ValueError("input changed since registration")
    base = load_base([Path(p) for p in paths], window(cfg)[3])
    parts, summary = run_stream(base, symbol, minutes, meta, cfg)
    directory.mkdir(parents=True)
    for name, frame in parts.items():
        frame.to_csv(directory / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    receipt = {"symbol": symbol, "minutes": minutes, "run_identity": run_hash, "summary": summary,
               "files": {f"{n}.csv.gz": digest(directory / f"{n}.csv.gz") for n in TABLES},
               "wall_seconds": time.perf_counter() - before, "status": "complete"}
    dump(directory / "receipt.json", receipt)
    return receipt


def run(out: Path, workers: int = 6) -> None:
    cfg = config()
    declared = declared_sources()
    if not _committed(declared):
        raise ValueError("commit builder/plan/config/tests before market replay")
    spec = source.ExecutionSpec()
    if (spec.round_trip_cost, spec.arm_r, spec.trail_atr) != (cfg["round_trip_cost"], 2., 4.):
        raise ValueError("execution contract changed")
    files = input_files(cfg)
    inputs = {s: [digest(p) for p in ps] for s, ps in files.items()}
    identity = {"config": cfg, "inputs": inputs, "input_paths": {s: [str(p) for p in ps] for s, ps in files.items()},
                "source": {str(p): digest(p) for p in declared},
                "python": subprocess.check_output([".venv/bin/python", "--version"], text=True).strip(),
                "numpy": np.__version__, "pandas": pd.__version__}
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
    tasks = [(s, m, [str(p) for p in files[s]], cfg["symbols"][s], str(out), run_hash, inputs[s], cfg)
             for m in sorted(cfg["timeframes_min"]) for s in cfg["symbols"]]
    results, errors = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(worker, t): (t[0], t[1]) for t in tasks}
        for future in as_completed(pending):
            name = pending[future]
            try:
                r = future.result(); results.append(r)
                print(json.dumps({"done": len(results), "total": len(tasks), "stream": name,
                                  "v126": r["summary"]["v126_candidates"], "v1": r["summary"]["v1_native_rows"],
                                  "seconds": round(r["wall_seconds"], 1)}), flush=True)
            except Exception as exc:
                errors.append({"stream": name, "error": repr(exc)})
                print(json.dumps(errors[-1]), flush=True)
    dump(out / "manifest.json", {"complete": not errors and len(results) == len(tasks), "run_identity": run_hash,
                                 "errors": errors, "streams": sorted(f"{r['symbol']}_{r['minutes']}m" for r in results),
                                 "native_pine_parity": False, "training_eligible": False, "production_eligible": False})
    if errors:
        raise RuntimeError(f"{len(errors)} failed streams")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch-recent", help="confirmed API 1m rows from api_start to end")
    replay_parser = sub.add_parser("replay")
    replay_parser.add_argument("--output", type=Path, required=True)
    replay_parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    cfg = config()
    if args.command == "fetch-recent":
        for symbol in cfg["symbols"]:
            print(json.dumps(fetch_recent(symbol, pd.Timestamp(cfg["api_start"]), pd.Timestamp(cfg["end"]),
                                          Path(cfg["data_dir"])), ensure_ascii=False), flush=True)
    else:
        run(args.output, args.workers)


if __name__ == "__main__":
    main()
