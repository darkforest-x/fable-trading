"""Independent V8 admission filters replayed on the receipt-bound two-year pool.

The module changes only permission to create a new V8 position.  ``rv`` and
``expansion`` always mean the final V8 confirmation bar's ``bars.rv`` and
``bars.expansion``: they are neither a preceding impulse bar nor a maximum
over V6's delay/wait window.  Raw V6
opposite confirmations retain their original close-at-next-open behaviour,
including when the new opposite entry is rejected.  The seven source rules
and two clock sensitivities are fixed in ``POLICIES``; their historical
outcomes are nonblind evidence, never a claim of prospective validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
import subprocess
from time import perf_counter, time_ns
from typing import Any

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as be05


EXP = Path("experiments/active/exp-spike-v8-six-filters-20260914-v1")
ADMISSION = EXP / "admission"
CONFIG = ADMISSION / "config.json"
TEST = Path("tests/test_spike_v8_six_filters.py")
SOURCE = Path("experiments/active/exp-spike-v1-v8-be05-20260914-v1/common/results/full_v3")
CATALOG = Path("experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/data/catalog.json")
ARM = "v8"
POLICIES = (
    "rv_gt50", "risk_gt30pct", "usdc_base", "stock_linked_all",
    "h00_utc", "sunday_utc", "joint_rv_gt50_tratr_gt10",
    "h00_asia_shanghai", "sunday_asia_shanghai",
)
MAIN_POLICIES = POLICIES[:7]
SENSITIVITY_POLICIES = POLICIES[7:]
DECISION_COLUMNS = (
    "stream_key", "venue", "symbol", "asset", "timeframe_min", "policy", "signal_i", "signal_bar_open",
    "entry_time", "side", "raw_side", "v8_allowed", "rv", "tr_atr_expansion", "signal_atr_pct",
    "actual_entry_price", "actual_initial_stop", "actual_initial_risk", "actual_initial_risk_pct",
    "utc_hour", "utc_weekday", "china_hour", "china_weekday", "stock_class", "stock_source",
    "gate_state", "gate_reason", "rejected_at", "entry_attempted", "entry_filled", "entry_blocked_reason",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _committed(paths: tuple[Path, ...]) -> bool:
    root = Path.cwd().resolve()
    for path in paths:
        try: relative = path.resolve().relative_to(root)
        except ValueError: return False
        if subprocess.run(["git", "cat-file", "-e", f"HEAD:{relative}"], capture_output=True).returncode:
            return False
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(relative)]).returncode:
            return False
    return True


def _source_completion(key: str) -> dict[str, Any]:
    receipt = SOURCE / "streams" / key / "completion.json"
    if not receipt.is_file(): raise ValueError(f"missing source receipt: {receipt}")
    item = json.loads(receipt.read_text())
    if item.get("status") != "complete" or item.get("stream_key") != key:
        raise ValueError(f"invalid source receipt: {receipt}")
    for name, digest in item.get("files", {}).items():
        path = receipt.parent / name
        if not path.is_file() or sha256(path) != digest: raise ValueError(f"source output drift: {path}")
    return item


def _catalog_map(path: Path = CATALOG) -> dict[tuple[str, str], dict[str, Any]]:
    rows = json.loads(path.read_text())
    if not isinstance(rows, list): raise ValueError("frozen catalog must be a list")
    result = {(str(row["venue"]), str(row["symbol"])): row for row in rows}
    if len(result) != len(rows): raise ValueError("duplicate frozen catalog keys")
    return result


def stock_class(identity: dict[str, object], catalog: dict[tuple[str, str], dict[str, Any]]) -> tuple[str, str]:
    """Classify only explicit exchange metadata; absence remains unknown."""
    venue, symbol, asset = str(identity["venue"]), str(identity["symbol"]), str(identity["asset"])
    if asset in {"PAXG", "XAUT", "XAU", "XAG"}: return "not_stock_linked", "explicit_metal_exclusion"
    row = catalog.get((venue, symbol))
    if row is None: return "unknown", "catalog_missing"
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    if venue == "binance":
        value = str(raw.get("underlyingType", ""))
        return ("stock_linked", f"binance_underlyingType={value}") if value in {"EQUITY", "HK_EQUITY", "KR_EQUITY", "CN_EQUITY"} else ("not_stock_linked", f"binance_underlyingType={value or 'missing'}")
    if venue == "okx":
        value = str(raw.get("instCategory", ""))
        return ("stock_linked", "okx_instCategory=3") if value == "3" else ("not_stock_linked", f"okx_instCategory={value or 'missing'}")
    if venue == "gate":
        value = str(raw.get("contract_type", ""))
        return ("stock_linked", "gate_contract_type=stocks") if value == "stocks" else ("not_stock_linked", f"gate_contract_type={value or 'missing'}")
    return "unknown", "unsupported_venue"


@dataclass(frozen=True)
class Candidate:
    i: int
    original_i: int
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp | pd.NaT
    side: int
    rv: float
    expansion: float
    atr_pct: float
    entry: float
    stop: float
    risk: float
    risk_pct: float


def candidates(prepared: be05.PreparedArm) -> dict[int, Candidate]:
    """Precompute V8-admitted causal signal and next-open risk facts once."""
    out: dict[int, Candidate] = {}
    frame = prepared.frame
    for i in np.flatnonzero(prepared.allowed):
        side = int(prepared.raw_side[i])
        if side == 0: raise ValueError("V8 allowed candidate lacks a raw side")
        original_i = prepared.ordinal.get(frame.index[i])
        if original_i is None: raise ValueError("candidate missing original ordinal")
        made = base._initial_position_fast(frame.index, prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr,
                                           prepared.gap, int(i), side, prepared.spec)
        entry_time: pd.Timestamp | pd.NaT = frame.index[i + 1] if i + 1 < len(frame) else pd.NaT
        rv, expansion = float(frame.rv.iloc[i]), float(frame.expansion.iloc[i])
        close, atr = float(prepared.close[i]), float(prepared.atr[i])
        if made is None:
            entry = stop = risk = risk_pct = math.nan
        else:
            entry, stop, risk = (float(made[k]) for k in ("entry_price", "initial_stop", "initial_risk"))
            risk_pct = risk / entry
        out[int(i)] = Candidate(int(i), int(original_i), frame.index[i], entry_time, side, rv, expansion,
                                atr / close if math.isfinite(atr) and math.isfinite(close) and close > 0 else math.nan,
                                entry, stop, risk, risk_pct)
    return out


def _gate(candidate: Candidate, policy: str, *, asset: str, stock: tuple[str, str]) -> tuple[str, str, object]:
    """Return pass/rejected/unknown_untested using only admission-time facts."""
    if policy == "baseline_noop": return "passed", "baseline_noop", candidate.signal_time
    if policy == "rv_gt50":
        return ("rejected", "rv_gt50", candidate.signal_time) if math.isfinite(candidate.rv) and candidate.rv > 50 else ("passed", "rv_not_gt50", candidate.signal_time) if math.isfinite(candidate.rv) else ("unknown_untested", "rv_missing", pd.NaT)
    if policy == "joint_rv_gt50_tratr_gt10":
        if not math.isfinite(candidate.rv) or not math.isfinite(candidate.expansion): return "unknown_untested", "rv_or_tratr_missing", pd.NaT
        return ("rejected", "rv_gt50_and_tratr_gt10", candidate.signal_time) if candidate.rv > 50 and candidate.expansion > 10 else ("passed", "joint_not_met", candidate.signal_time)
    if policy == "risk_gt30pct":
        if not math.isfinite(candidate.risk_pct): return "unknown_untested", "actual_initial_risk_unknown", pd.NaT
        return ("rejected", "actual_initial_risk_gt30pct", candidate.entry_time) if candidate.risk_pct > .30 else ("passed", "actual_initial_risk_not_gt30pct", candidate.entry_time)
    if policy == "usdc_base": return ("rejected", "asset_usdc", candidate.signal_time) if asset == "USDC" else ("passed", "asset_not_usdc", candidate.signal_time)
    if policy == "stock_linked_all":
        if stock[0] == "unknown": return "unknown_untested", stock[1], pd.NaT
        return ("rejected", stock[1], candidate.signal_time) if stock[0] == "stock_linked" else ("passed", stock[1], candidate.signal_time)
    if pd.isna(candidate.entry_time): return "unknown_untested", "entry_time_missing", pd.NaT
    clock = candidate.entry_time if policy.endswith("utc") else candidate.entry_time.tz_convert("Asia/Shanghai")
    if policy.startswith("h00"):
        return ("rejected", "entry_hour_00", candidate.signal_time) if clock.hour == 0 else ("passed", "entry_hour_not_00", candidate.signal_time)
    if policy.startswith("sunday"):
        return ("rejected", "entry_sunday", candidate.signal_time) if clock.dayofweek == 6 else ("passed", "entry_not_sunday", candidate.signal_time)
    raise ValueError(f"unknown policy: {policy}")


def decision_frame(prepared: be05.PreparedArm, policy: str, *, catalog: dict[tuple[str, str], dict[str, Any]] | None = None) -> pd.DataFrame:
    if policy not in {*POLICIES, "baseline_noop"}: raise ValueError(policy)
    catalog = _catalog_map() if catalog is None else catalog
    context = prepared.context; stock = stock_class(context.identity, catalog)
    rows: list[dict[str, object]] = []
    for candidate in candidates(prepared).values():
        state, reason, rejected_at = _gate(candidate, policy, asset=str(context.identity["asset"]), stock=stock)
        local = candidate.entry_time.tz_convert("Asia/Shanghai") if not pd.isna(candidate.entry_time) else pd.NaT
        rows.append({"stream_key": context.key, **context.identity, "policy": policy, "signal_i": candidate.original_i,
                     "signal_bar_open": candidate.signal_time, "entry_time": candidate.entry_time, "side": candidate.side,
                     "raw_side": candidate.side, "v8_allowed": True, "rv": candidate.rv, "tr_atr_expansion": candidate.expansion,
                     "signal_atr_pct": candidate.atr_pct, "actual_entry_price": candidate.entry, "actual_initial_stop": candidate.stop,
                     "actual_initial_risk": candidate.risk, "actual_initial_risk_pct": candidate.risk_pct,
                     "utc_hour": candidate.entry_time.hour if not pd.isna(candidate.entry_time) else math.nan,
                     "utc_weekday": candidate.entry_time.dayofweek if not pd.isna(candidate.entry_time) else math.nan,
                     "china_hour": local.hour if not pd.isna(local) else math.nan, "china_weekday": local.dayofweek if not pd.isna(local) else math.nan,
                     "stock_class": stock[0], "stock_source": stock[1], "gate_state": state, "gate_reason": reason,
                     "rejected_at": rejected_at, "entry_attempted": False, "entry_filled": False, "entry_blocked_reason": ""})
    return pd.DataFrame(rows, columns=DECISION_COLUMNS)


def _close(position: dict[str, object], *, i: int, stamp: pd.Timestamp, price: float, reason: str, fills: list[dict[str, object]], context: base.StreamContext, phase: str) -> dict[str, object]:
    fraction, side = float(position["qty_remaining"]), int(position["side"])
    gross = fraction * side * (price / float(position["entry_price"]) - 1)
    position["qty_realized"] += fraction; position["qty_remaining"] = 0.
    position["realized_gross_return"] += gross; position["realized_net_return"] += gross - fraction * base.EXIT_COST
    position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, price, reason
    base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                      phase=phase, precision="bar_open" if phase == "open" else "within_bar", kind="exit", fraction=fraction,
                      price=price, cost=fraction * base.EXIT_COST, reason=reason, context=context)
    return base._trade_row(position, censored=False, precision="bar_open_or_intrabar_window")


def replay_serial(context: base.StreamContext, *, policy: str, prepared: be05.PreparedArm | None = None,
                  catalog: dict[tuple[str, str], dict[str, Any]] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Clean V8 serial replay with one independent new-entry rejection rule."""
    prepared = be05.prepare_arm(context, arm=ARM) if prepared is None else prepared
    if prepared.arm != ARM: raise ValueError("requires prepared V8 arm")
    decisions = decision_frame(prepared, policy, catalog=catalog)
    by_local = {int(i): row for i, row in decisions.set_index("signal_i").iterrows()}
    frame, spec, gap, allowed, raw = prepared.frame, prepared.spec, prepared.gap, prepared.allowed, prepared.raw_side
    oa, ha, la, ca, aa, ordinal = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr, prepared.ordinal
    trades: list[dict[str, object]] = []; fills: list[dict[str, object]] = []
    position: dict[str, object] | None = None; pending_entry: tuple[int, int] | None = None; pending_reverse: tuple[int, int] | None = None; next_id = 0
    for i, stamp in enumerate(frame.index):
        ended_side = 0
        if gap[i]:
            if position is not None:
                base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp, phase="close", precision="unknown_gap", kind="censor", fraction=float(position["qty_remaining"]), price=math.nan, cost=0., reason="data_gap_censored", context=prepared.context)
                position["last_exit_i"], position["last_exit_time"], position["last_exit_reason"] = i, stamp, "data_gap_censored"; trades.append(base._trade_row(position, censored=True, precision="unknown_gap"))
            position = None; pending_entry = pending_reverse = None; continue
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            if oa[i] <= protection if side == 1 else oa[i] >= protection:
                reason = "trailing_stop_gap" if protection != float(position["initial_stop"]) else "initial_stop_gap"
                trades.append(_close(position, i=i, stamp=stamp, price=oa[i], reason=reason, fills=fills, context=prepared.context, phase="open")); ended_side, position, pending_reverse = side, None, None
        if position is not None and pending_reverse is not None:
            _, old_side = pending_reverse
            if int(position["side"]) == old_side:
                trades.append(_close(position, i=i, stamp=stamp, price=oa[i], reason="opposite_v6_next_open", fills=fills, context=prepared.context, phase="open")); ended_side, position = old_side, None
            pending_reverse = None
        if pending_entry is not None:
            signal_i, side = pending_entry; original_i = ordinal.get(frame.index[signal_i]); row = by_local.get(int(original_i))
            if position is None and side != ended_side and row is not None:
                decisions.loc[decisions.signal_i.eq(int(original_i)), "entry_attempted"] = True
                if str(row.gate_state) == "rejected":
                    decisions.loc[decisions.signal_i.eq(int(original_i)), "entry_blocked_reason"] = str(row.gate_reason)
                else:
                    made = base._initial_position_fast(frame.index, oa, ha, la, ca, aa, gap, signal_i, side, spec)
                    if made is not None:
                        made["initial_risk_frac"] = float(made["initial_risk"]) / float(made["entry_price"])
                        made["signal_i"], made["entry_i"], made["frozen_index_offset"] = int(original_i), int(original_i) + 1, int(original_i) - signal_i
                        next_id += 1; position = base._new_trade(made, trade_id=f"{context.key}:{ARM}:{policy}:{next_id}", cohort=prepared.cohort, policy=policy, context=prepared.context)
                        position.update(protection=float(made["initial_stop"]), mfe_r=0., trail_armed=False)
                        base._append_fill(fills, position, leg_no=1, bar_open=stamp, event_time=stamp, phase="open", precision="bar_open", kind="entry", fraction=1., price=float(position["entry_price"]), cost=base.ENTRY_COST, reason="next_open_entry", context=prepared.context)
                        decisions.loc[decisions.signal_i.eq(int(original_i)), "entry_filled"] = True
                    else: decisions.loc[decisions.signal_i.eq(int(original_i)), "entry_blocked_reason"] = "initial_position_unavailable"
            pending_entry = None
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            stopped = la[i] <= protection if side == 1 else ha[i] >= protection
            if stopped:
                price = min(oa[i], protection) if side == 1 else max(oa[i], protection); reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
                if oa[i] <= protection if side == 1 else oa[i] >= protection: reason += "_gap"
                trades.append(_close(position, i=i, stamp=stamp, price=price, reason=reason, fills=fills, context=prepared.context, phase="open" if reason.endswith("_gap") else "intrabar")); ended_side, position, pending_reverse = side, None, None
            else: be05._close_updates(position, high=ha[i], low=la[i], close=ca[i], atr=aa[i], spec=spec, enable_be=False, events=[], context=prepared.context, arm=ARM, policy=policy, i=i)
        signal_side = int(raw[i])
        if signal_side:
            original_i = ordinal.get(frame.index[i]); row = by_local.get(int(original_i)) if original_i is not None else None
            # The risk-width rule is explicitly an opening execution gate: it
            # must reach the actual next-open quote before refusing the fill.
            permitted = bool(allowed[i]) and row is not None and (policy == "risk_gt30pct" or str(row.gate_state) != "rejected")
            if position is not None and signal_side != int(position["side"]):
                pending_reverse = (i, int(position["side"]))
                if permitted: pending_entry = (i, signal_side)
            elif position is None and signal_side != ended_side and permitted: pending_entry = (i, signal_side)
    if position is not None:
        last, stamp = len(frame) - 1, frame.index[-1]
        base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp + pd.Timedelta(minutes=prepared.context.minutes), phase="close", precision="last_complete_close", kind="censor", fraction=float(position["qty_remaining"]), price=ca[last], cost=0., reason="boundary_mark", context=prepared.context)
        position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = last, stamp, ca[last], "boundary_mark"; trades.append(base._trade_row(position, censored=True, precision="last_complete_close"))
    ledger = pd.DataFrame(trades, columns=base.TRADE_COLUMNS)
    extra = decisions[["signal_i", "rv", "tr_atr_expansion", "signal_atr_pct", "stock_class"]].copy()
    ledger = ledger.merge(extra, on="signal_i", how="left", validate="many_to_one")
    return ledger, pd.DataFrame(fills, columns=base.FILL_COLUMNS), decisions


def assert_baseline_parity(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    """Exact replay parity, including censored rows; policy labels are excluded."""
    cols = [c for c in base.TRADE_COLUMNS if c not in {"policy", "trade_id"}]
    assert_frame_equal(actual.reindex(columns=cols).reset_index(drop=True), expected.reindex(columns=cols).reset_index(drop=True), check_dtype=False, check_like=False)


def fixed_event(source_fixed: pd.DataFrame, decisions: pd.DataFrame, *, policy: str) -> pd.DataFrame:
    """Filter only original baseline events; this deliberately creates no re-entry."""
    gate = decisions.set_index("signal_i")[["gate_state", "gate_reason", "rv", "tr_atr_expansion", "signal_atr_pct", "stock_class"]]
    out = source_fixed.copy(); out["fixed_event_status"] = "kept"; out["fixed_event_reason"] = ""; out["gate_rejected"] = False; out["feature_known"] = True
    for i, row in out.iterrows():
        item = gate.loc[int(row.signal_i)] if int(row.signal_i) in gate.index else None
        if item is None:
            out.loc[i, ["fixed_event_status", "fixed_event_reason", "feature_known"]] = ["unknown_kept", "candidate_missing", False]
            continue
        for col in ("rv", "tr_atr_expansion", "signal_atr_pct", "stock_class"): out.loc[i, col] = item[col]
        if str(item.gate_state) == "unknown_untested": out.loc[i, ["fixed_event_status", "fixed_event_reason", "feature_known"]] = ["unknown_kept", str(item.gate_reason), False]
        elif str(item.gate_state) == "rejected": out.loc[i, ["fixed_event_status", "fixed_event_reason", "gate_rejected"]] = ["rejected", str(item.gate_reason), True]
    out["policy"] = policy
    return out


def _completed(output: Path, key: str) -> dict[str, Any] | None:
    receipt = output / "streams" / key / "completion.json"
    if not receipt.exists(): return None
    item = json.loads(receipt.read_text())
    for name, digest in item.get("files", {}).items():
        if sha256(receipt.parent / name) != digest: raise ValueError(f"output drift: {receipt.parent / name}")
    return item


def run(output: Path, *, limit: int | None = None, official: bool = False) -> pd.DataFrame:
    """Replay all independent admissions, writing hash-bound resumable streams."""
    if official and not _committed((Path(__file__), TEST, CONFIG)):
        raise ValueError("official replay requires committed builder, tests and config")
    config = json.loads(CONFIG.read_text())
    if sha256(CATALOG) != config["catalog_sha256"]: raise ValueError("frozen catalog changed")
    folders = sorted(p for p in (SOURCE / "streams").iterdir() if (p / "completion.json").is_file())
    if len(folders) != int(config["expected_streams"]): raise ValueError("unexpected source stream count")
    folders = folders if limit is None else folders[:limit]; output.mkdir(parents=True, exist_ok=True); (output / "streams").mkdir(exist_ok=True); (output / "failures").mkdir(exist_ok=True)
    identity = {"builder_sha256": sha256(Path(__file__)), "test_sha256": sha256(TEST), "config_sha256": sha256(CONFIG), "source_manifest_sha256": sha256(SOURCE / "manifest.json"), "catalog_sha256": sha256(CATALOG)}
    identity_path = output / "identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity: raise ValueError("output identity changed; choose new directory")
    identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True)); catalog = _catalog_map(); summaries: list[dict[str, object]] = []
    for n, folder in enumerate(folders, 1):
        existing = _completed(output, folder.name)
        if existing is not None: summaries.extend(existing["summaries"]); continue
        started = perf_counter(); staging = output / "streams" / f".{folder.name}.staging"; staging.mkdir()
        try:
            _source_completion(folder.name); context = base.load_verified_stream(folder); prepared = be05.prepare_arm(context, arm=ARM)
            saved_serial = pd.read_csv(SOURCE / "streams" / folder.name / "v8.serial_baseline.csv.gz")
            noop, _, _ = replay_serial(context, policy="baseline_noop", prepared=prepared, catalog=catalog)
            assert_baseline_parity(noop, saved_serial)
            rows: list[dict[str, object]] = []
            source_fixed = pd.read_csv(SOURCE / "streams" / folder.name / "v8.fixed_baseline.csv.gz")
            for policy in POLICIES:
                serial, _, decisions = replay_serial(context, policy=policy, prepared=prepared, catalog=catalog)
                fixed = fixed_event(source_fixed, decisions, policy=policy)
                serial.to_csv(staging / f"v8.serial_{policy}.csv.gz", index=False, compression={"method":"gzip", "mtime":0})
                fixed.to_csv(staging / f"v8.fixed_{policy}.csv.gz", index=False, compression={"method":"gzip", "mtime":0})
                decisions.to_csv(staging / f"v8.{policy}.decisions.csv.gz", index=False, compression={"method":"gzip", "mtime":0})
                rows.append({"stream_key": context.key, **context.identity, "policy": policy, "signals": len(decisions), "gate_rejected": int(decisions.gate_state.eq("rejected").sum()), "gate_unknown": int(decisions.gate_state.eq("unknown_untested").sum()), "serial_rows": len(serial), "serial_closed": int((~serial.censored.astype(bool)).sum()), "serial_net_r": float(serial.loc[~serial.censored.astype(bool), "net_r"].sum()), "fixed_rejected": int(fixed.fixed_event_status.eq("rejected").sum())})
            pd.DataFrame(rows).to_csv(staging / "stream_summary.csv", index=False); files = {p.name: sha256(p) for p in staging.glob("*.csv.gz")}; wall = perf_counter() - started
            receipt = {"status":"complete", "stream_key":context.key, "stream_wall_seconds":wall, "source_completion_sha256":sha256(SOURCE / "streams" / folder.name / "completion.json"), "files":files, "summaries":rows}
            (staging / "completion.json").write_text(json.dumps(receipt, indent=2)); staging.replace(output / "streams" / folder.name); summaries.extend(rows)
            print(json.dumps({"stream":n,"target":len(folders),"stream_key":context.key,"stream_wall_seconds":round(wall,4)}), flush=True)
        except Exception as exc:
            (staging / "failure.json").write_text(json.dumps({"stream_key":folder.name,"error_type":type(exc).__name__,"error":str(exc)}, indent=2)); staging.replace(output / "failures" / f"{folder.name}.{time_ns()}.failed"); raise
    summary = pd.DataFrame(summaries); summary.to_csv(output / "stream_summary.csv", index=False)
    (output / "manifest.json").write_text(json.dumps({"complete":limit is None,"streams":len(folders),"expected_streams":config["expected_streams"],"configuration_exposure":1,"history":"authorized reused nonblind history","source":"full_v3 V8 serial/fixed baseline receipt-bound","policies":list(POLICIES),"stock_taxonomy":"frozen 2026 exchange catalog snapshot; identity classification, not historical point-in-time"}, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--limit", type=int); parser.add_argument("--official", action="store_true")
    args = parser.parse_args(); run(args.output, limit=args.limit, official=args.official)
