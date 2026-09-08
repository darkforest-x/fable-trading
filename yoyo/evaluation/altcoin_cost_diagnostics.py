"""Post-selection cost diagnostics on frozen event paths, never a new backtest.

No signal, arm, entry, exit, quantity or matched-control mapping is selected or
changed here. 40/60bp and 10bp-per-leg turnover fees are sensitivity assumptions,
not the owner's fee tier. Funding is a retrospective conditional approximation:
actual realized rates times the exact-timestamp FUTURES TRADE BAR OPEN, NOT mark
price. No forward fill, predicted rate substitution or missing-to-zero fill.

Sources: https://www.okx.com/en-us/help/perps-funding-fee-mechanism and
https://www.okx.com/en-us/help/how-to-calculate-the-contract-transaction-fee .
An assessment may occur in [funding_time, funding_time + 60 seconds]. Unknown
intrabar exits span [exit-bar open, close]; equality at an entry/exit boundary
is uncertain. Bounds describe inclusion of OBSERVED settlements only: a source
span does not prove a complete schedule or bound unobserved fees. Prices during
assessment, real execution order and historical publication delays are unknown.

Inputs use only frozen event outcomes and retrospective cost labels, not trading
features. Funding changes cash available for later entries: these static-path
returns must NOT be called funding-adjusted compounded portfolio performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "altcoin-static-cost-diagnostics-v1"
PROXY_LABEL = "futures_trade_bar_open_proxy_NOT_settlement_mark"
NOTICE = "Conditional static-path event diagnostics; observed funding schedule only; no complete funding coverage or re-compounded portfolio claim."
METRICS = ["net_20bp", "net_40bp", "net_60bp", "net_notional_fee_bp"]
GROUPS = ["fold", "minutes", "cohort", "arm"]
CONTROL_KEYS = ["symbol", "minutes", "fold", "cohort", "parameter", "domain", "exit_rule", "signal_i"]
PARAMETER_ARMS = {"ma21", "ma55", "signal5", "signal13", "focus6", "focus24", "focus36", "band005", "band020", "band030"}
BASE_ARMS = {"base", "gate_rvol2", "gate_tr15", "gate_close70", "gate_box_break", "gate_bb20", "exit_fixed3r", "exit_chandelier", "exit_sma60"}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _clock(value):
    t = pd.Timestamp(value)
    if pd.isna(t) or t.tzinfo is None or t.utcoffset().total_seconds() != 0:
        raise ValueError("timestamps must explicitly use UTC")
    return t.tz_convert("UTC").as_unit("ns")


def _truth(values):
    mapped = values.map({True: True, False: False, "True": True, "False": False, "true": True, "false": False})
    if mapped.isna().any():
        raise ValueError("boolean columns require explicit true/false values")
    return mapped.astype(bool)


def _funding_arrays(funding, proxy_opens, source_start, source_end):
    if funding is None or funding.empty:
        return None
    if not {"funding_time", "realized_rate", "rate_status"} <= set(funding):
        raise ValueError("funding requires Unix-ms funding_time, realized_rate and rate_status")
    ms = pd.to_numeric(funding.funding_time, errors="raise").to_numpy(float)
    if not np.isfinite(ms).all() or (ms <= 0).any() or (ms != np.floor(ms)).any():
        raise ValueError("funding_time must be finite integer Unix milliseconds")
    f = funding.copy()
    f.index = pd.to_datetime(ms.astype("int64"), unit="ms", utc=True)
    if f.index.has_duplicates:
        raise ValueError("duplicate funding settlements are not accepted")
    f = f.sort_index()
    if (source_start is not None and (f.index < _clock(source_start)).any()) or (source_end is not None and (f.index >= _clock(source_end)).any()):
        raise ValueError("funding rows exceed frozen [start, end) metadata")
    rates = pd.to_numeric(f.realized_rate, errors="coerce").to_numpy(float)
    actual = f.rate_status.eq("actual_available").to_numpy() & np.isfinite(rates)
    rates[~actual] = np.nan
    prices = np.full(len(f), np.nan)
    if proxy_opens is not None:
        if not isinstance(proxy_opens.index, pd.DatetimeIndex) or proxy_opens.index.has_duplicates:
            raise ValueError("proxy opens require a unique UTC DatetimeIndex")
        _clock(proxy_opens.index[0]) if len(proxy_opens) else None
        p = proxy_opens.astype(float)
        if not np.isfinite(p).all() or (p <= 0).any():
            raise ValueError("proxy opens must be finite and positive")
        prices = p.reindex(f.index).to_numpy(float)  # exact timestamp only
    return f.index.as_unit("ns").asi8, rates, prices


def diagnose_events(events, funding=None, proxy_opens=None, *, source_start=None, source_end=None):
    """Return unchanged event rows plus fee sensitivities and conditional bounds.

    funding_time is integer Unix ms; proxy_opens is a UTC-indexed Series. Event
    entry_time/exit_time are explicit UTC. period_seconds supplies the exit-bar
    interval for intrabar_unknown. Invalid events retain NaN cost diagnostics.
    """
    out = events.copy()
    if not out.index.is_unique:
        raise ValueError("event row index must be unique")
    required = {"valid", "side", "entry_price", "exit_price", "gross_bp", "entry_time", "exit_time", "exit_timing", "period_seconds"}
    if not required <= set(out):
        raise ValueError("missing event columns: " + ", ".join(sorted(required-set(out))))
    valid = _truth(out.valid)
    for name in ("side", "entry_price", "exit_price", "gross_bp", "period_seconds"):
        out[name] = pd.to_numeric(out[name], errors="coerce")
    numeric = out.loc[valid, ["side", "entry_price", "exit_price", "gross_bp", "period_seconds"]].astype(float)
    if not np.isfinite(numeric).all().all() or not numeric.side.isin([-1, 1]).all() or (numeric[["entry_price", "exit_price"]] <= 0).any().any() or not numeric.period_seconds.isin([3600, 14400]).all():
        raise ValueError("valid events require finite prices/returns, side +/-1 and 1H/4H period")
    if "minutes" in out and not out.loc[valid, "minutes"].mul(60).eq(numeric.period_seconds).all():
        raise ValueError("event minutes and period_seconds disagree")
    gross = numeric.side * (numeric.exit_price/numeric.entry_price-1)*10000
    if not np.allclose(gross, numeric.gross_bp, atol=1e-6, rtol=1e-10):
        raise ValueError("event gross_bp disagrees with side and prices")
    out["break_even_cost_bp"] = out.gross_bp.where(valid)
    for bp in (20, 40, 60):
        out[f"net_{bp}bp"] = out.break_even_cost_bp - bp
    out["notional_fee_bp"] = (10 * (1 + out.exit_price/out.entry_price)).where(valid)
    out["net_notional_fee_bp"] = out.break_even_cost_bp - out.notional_fee_bp
    if "net_bp" in out and not np.allclose(out.loc[valid, "net_bp"], out.loc[valid, "net_20bp"], atol=1e-6, rtol=1e-10):
        raise ValueError("event baseline is not the frozen 20bp cost assumption")
    for col in ("funding_bp_low", "funding_bp_high"):
        out[col] = np.nan
    for col in ("funding_certain_count", "funding_uncertain_count", "funding_missing_rate_count", "funding_missing_proxy_count"):
        out[col] = 0
    out["funding_status"] = np.where(valid, "source_missing", "invalid_event")
    out["funding_price_basis"], out["funding_coverage_complete"] = PROXY_LABEL, False
    arrays = _funding_arrays(funding, proxy_opens, source_start, source_end)
    cache = {}
    for i, event in out.loc[valid].iterrows():
        entry, end = _clock(event.entry_time), _clock(event.exit_time)
        if end < entry or event.exit_timing not in {"open", "close", "intrabar_unknown"}:
            raise ValueError("invalid event holding interval or exit_timing")
        lower = max(entry, end-pd.Timedelta(seconds=event.period_seconds)) if event.exit_timing == "intrabar_unknown" else end
        if arrays is None:
            continue
        ts, rates, prices = arrays
        key = (entry.value, lower.value, end.value, event.side, event.entry_price)
        if key not in cache:
            result = {"funding_status": "outside_observed_span"}
            in_span = entry.value >= ts[0] and end.value <= ts[-1]+60_000_000_000
            in_span &= source_start is None or entry >= _clock(source_start)
            in_span &= source_end is None or end <= _clock(source_end)
            if in_span:
                lo = np.searchsorted(ts, entry.value-60_000_000_000, side="left")
                hi = np.searchsorted(ts, end.value, side="right")
                times, r, p = ts[lo:hi], rates[lo:hi], prices[lo:hi]
                certain = (times > entry.value) & (times+60_000_000_000 < lower.value)
                result.update(funding_certain_count=int(certain.sum()), funding_uncertain_count=int((~certain).sum()),
                              funding_missing_rate_count=int(np.isnan(r).sum()), funding_missing_proxy_count=int(np.isnan(p).sum()))
                if np.isnan(r).any() or np.isnan(p).any():
                    result["funding_status"] = "missing_rate_or_proxy"
                else:
                    cash = -event.side * (p/event.entry_price) * r * 10000
                    fixed = cash[certain].sum()
                    result.update(funding_bp_low=float(fixed+np.minimum(0, cash[~certain]).sum()),
                                  funding_bp_high=float(fixed+np.maximum(0, cash[~certain]).sum()),
                                  funding_status="observed_schedule_only")
            cache[key] = result
        for name, value in cache[key].items():
            out.at[i, name] = value
    out["net_20bp_funding_low"] = out.net_20bp + out.funding_bp_low
    out["net_20bp_funding_high"] = out.net_20bp + out.funding_bp_high
    return out


def _arm_context(arm):
    if arm in PARAMETER_ARMS:
        return arm, "all"
    if arm in BASE_ARMS:
        return "base", "all"
    domains = {"deriv_oi24_pos": "oi24", "deriv_oi24_down": "oi24", "deriv_oi24_available": "oi24",
               "deriv_oi4_pos": "oi4", "deriv_oi4_available": "oi4", "deriv_taker55": "taker", "deriv_taker_available": "taker"}
    if arm not in domains:
        raise ValueError("unknown frozen arm; export explicit parameter/domain metadata: " + str(arm))
    return "base", domains[arm]


def summarize_cases(cases, controls):
    """Event-equal paired means; funding comparison requires ALL mapped controls.

    Mapping uses the original control_indexes, never fresh sampling. Bounds of
    case-minus-control use low-minus-high/high-minus-low. Selected-case rows use
    original portfolio_selected; controls are NOT a simulated control portfolio.
    """
    if cases.empty:
        return pd.DataFrame(), pd.DataFrame()
    controls = controls.loc[_truth(controls.valid)].copy() if len(controls) else controls
    if len(controls) and controls.duplicated(CONTROL_KEYS).any():
        raise ValueError("duplicate original control identity")
    lookup = {tuple(row[k] for k in CONTROL_KEYS): row for row in controls.to_dict("records")}
    paired = cases.loc[_truth(cases.valid)].copy()
    if paired.empty:
        return paired, pd.DataFrame()
    for i, row in paired.iterrows():
        parameter, domain = (row.parameter, row.domain) if "parameter" in row and "domain" in row else _arm_context(row.arm)
        text = "" if pd.isna(row.control_indexes) else str(row.control_indexes)
        ids = [int(x) for x in text.split()]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate original control_indexes")
        prefix = [row.symbol, row.minutes, row.fold, row.cohort, parameter, domain, row.exit_rule]
        matches = [lookup[tuple(prefix+[j])] for j in ids if tuple(prefix+[j]) in lookup]
        if "control_count" in row and len(matches) != row.control_count:
            raise ValueError("original matched control_count cannot be reconstructed")
        supported = [x for x in matches if x["funding_status"] == "observed_schedule_only"]
        paired.at[i, "matched_control_count"] = len(matches)
        paired.at[i, "funding_control_observed_count"] = len(supported)
        common = row.funding_status == "observed_schedule_only" and bool(matches) and len(supported) == len(matches)
        paired.at[i, "funding_common_support"] = common
        for metric in METRICS:
            mean = float(np.mean([x[metric] for x in matches])) if matches else np.nan
            paired.at[i, "control_"+metric] = mean
            paired.at[i, "excess_"+metric] = row[metric]-mean
        for bound, opposite in (("low", "high"), ("high", "low")):
            col = "net_20bp_funding_"+bound
            mean = float(np.mean([x[col] for x in matches])) if common else np.nan
            other = float(np.mean([x["net_20bp_funding_"+opposite] for x in matches])) if common else np.nan
            paired.at[i, "control_"+col] = mean
            paired.at[i, "excess_"+col] = row[col]-other if common else np.nan
    rows = []
    scopes = [("all_events", paired)]
    if "portfolio_selected" in paired:
        scopes.append(("portfolio_selected", paired.loc[_truth(paired.portfolio_selected)]))
    for scope, frame in scopes:
        for key, g in frame.groupby(GROUPS, sort=True):
            matched, common = g.loc[g.matched_control_count.gt(0)], g.loc[g.funding_common_support.eq(True)]
            r = dict(zip(GROUPS, key), scope=scope, case_count=len(g), matched_case_count=len(matched),
                     control_link_count=int(g.matched_control_count.sum()), funding_case_observed_count=int(g.funding_status.eq("observed_schedule_only").sum()),
                     funding_control_observed_link_count=int(g.funding_control_observed_count.sum()), funding_common_case_count=len(common),
                     funding_common_control_link_count=int(common.matched_control_count.sum()), funding_coverage_complete=False)
            for metric in METRICS:
                r["case_mean_"+metric] = g[metric].mean()
                r["matched_case_mean_"+metric] = matched[metric].mean()
                r["matched_control_mean_"+metric] = matched["control_"+metric].mean()
                r["matched_excess_mean_"+metric] = matched["excess_"+metric].mean()
            for bound in ("low", "high"):
                metric = "net_20bp_funding_"+bound
                for label, col in (("case", metric), ("control", "control_"+metric), ("excess", "excess_"+metric)):
                    r["funding_common_"+label+"_mean_"+bound] = common[col].mean()
            rows.append(r)
    return paired, pd.DataFrame(rows)


def run(results, history, derivatives, out_dir):
    """Read verified frozen sources, write event diagnostics and provenance only."""
    results, history, derivatives, out_dir = map(Path, (results, history, derivatives, out_dir))
    if out_dir.exists():
        raise ValueError("output already exists; use a new diagnostics directory")
    manifest = json.loads(history.read_text())
    records = {x["symbol"]: x for x in manifest["symbols"]}
    if len(records) != len(manifest["symbols"]):
        raise ValueError("duplicate history symbols")
    cutoff = _clock(manifest["end_exclusive"])
    derivative_manifest = derivatives/"manifest.json"
    identity = json.loads(derivative_manifest.read_text())["identity"] if derivative_manifest.exists() else None
    if identity is not None and (identity["period"] != "4H" or identity["end_ms"] > cutoff.value//1_000_000):
        raise ValueError("derivatives require a frozen 4H source ending no later than history")
    inputs = {str(history): sha(history)}
    if identity is not None:
        inputs[str(derivative_manifest)] = sha(derivative_manifest)
    history_cache, funding_cache, cases, controls, skipped = {}, {}, [], [], []
    for event_file in sorted(results.glob("*/events.csv.gz")):
        if event_file.parent.name.endswith("_15"):
            skipped.append(str(event_file)); continue
        try:
            e = pd.read_csv(event_file, dtype={"control_indexes": "string"})
        except pd.errors.EmptyDataError:
            skipped.append(str(event_file)); continue
        if e.empty:
            skipped.append(str(event_file)); continue
        symbols, periods = e.symbol.unique(), e.minutes.unique()
        if len(symbols) != 1 or len(periods) != 1:
            raise ValueError("each source directory must contain one symbol/timeframe")
        symbol, minutes = symbols[0], int(periods[0])
        if minutes not in (60, 240):
            skipped.append(str(event_file)); continue
        control_file = event_file.with_name("controls.csv.gz")
        try:
            c = pd.read_csv(control_file)
        except pd.errors.EmptyDataError:
            c = pd.DataFrame()
        if len(c) and (not c.symbol.eq(symbol).all() or not c.minutes.eq(minutes).all()):
            raise ValueError("control source does not match case symbol/timeframe")
        for path in (event_file, control_file): inputs[str(path)] = sha(path)
        if symbol not in history_cache:
            record = records.get(symbol)
            if record is None or record.get("status") != "complete":
                raise ValueError("history symbol is not verified complete: " + symbol)
            path = Path(record["output_path"])
            path = path if path.is_absolute() else ROOT/path
            if sha(path) != record["output_sha256"]:
                raise ValueError("history SHA mismatch: " + symbol)
            raw = pd.read_csv(path, usecols=["ts", "open"])
            raw.index = pd.to_datetime(raw.pop("ts"), unit="ms", utc=True)
            if len(raw) and (raw.index.max()+pd.Timedelta(minutes=15) > cutoff):
                raise ValueError("history exceeds frozen end")
            history_cache[symbol], inputs[str(path)] = raw.open, record["output_sha256"]
            path = derivatives/"normalized"/f"{symbol}-USDT-SWAP_4H_funding.csv"
            funding_cache[symbol] = pd.read_csv(path) if path.exists() and identity is not None else None
            if funding_cache[symbol] is not None:
                if not funding_cache[symbol].inst_id.eq(symbol+"-USDT-SWAP").all():
                    raise ValueError("funding instrument mismatch")
                inputs[str(path)] = sha(path)
        opts = {} if identity is None else dict(source_start=pd.to_datetime(identity["start_ms"], unit="ms", utc=True), source_end=pd.to_datetime(identity["end_ms"], unit="ms", utc=True))
        for source, output in ((e, cases), (c, controls)):
            if len(source):
                if any(_clock(x) > cutoff for x in source.exit_time):
                    raise ValueError("event exceeds frozen history end")
                output.append(diagnose_events(source, funding_cache[symbol], history_cache[symbol], **opts))
    cases = pd.concat(cases, ignore_index=True) if cases else pd.DataFrame()
    controls = pd.concat(controls, ignore_index=True) if controls else pd.DataFrame()
    paired, summary = summarize_cases(cases, controls)
    # Detect changes during the read before claiming frozen provenance.
    if any(sha(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("input changed while diagnostics were running")
    out_dir.mkdir(parents=True, exist_ok=False)
    for name, frame in (("cost_events.csv.gz", paired), ("cost_controls.csv.gz", controls), ("summary.csv", summary)):
        frame.to_csv(out_dir/name, index=False)
    receipt = dict(schema=SCHEMA, generated_at=pd.Timestamp.now(tz="UTC").isoformat(), notice=NOTICE,
                   funding_price_basis=PROXY_LABEL, assessment_window_seconds=60, funding_coverage_complete=False,
                   candidate_reselection=False, portfolio_recomputed=False, training_eligible=False, production_eligible=False,
                   case_count=len(cases), control_count=len(controls), skipped_files=skipped, input_sha256=inputs,
                   builder_sha256=sha(Path(__file__)))
    (out_dir/"manifest.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False)+"\n")
    return receipt


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("results", "history", "derivatives", "out-dir"):
        p.add_argument("--"+name, type=Path, required=True)
    args = p.parse_args(argv)
    relative = str(Path(__file__).resolve().relative_to(ROOT))
    if subprocess.check_output(["git", "show", "HEAD:"+relative], cwd=ROOT) != Path(__file__).read_bytes():
        p.error("commit the diagnostics builder before scoring frozen results")
    print(json.dumps(run(args.results, args.history, args.derivatives, args.out_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
