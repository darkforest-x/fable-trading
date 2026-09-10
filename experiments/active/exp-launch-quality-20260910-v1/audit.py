"""Committed, independent historical audit for the fixed launch-quality study.

Selection is outcome-blind: two valid event_ids per era/venue in lexical order,
plus the chronologically last valid earlier event per venue, deduplicated. The
maximum is17 after additionally checking each era's largest baseline account
profit contribution, an explicit anomaly audit requested before this run.
Native source OHLCV independently rebuilds ATR14, IMACD34 and
current/prior20 volume and TR/priorATR. The replay never imports the production
engine or its simulator. Known event identity/price/time/return parity is also
checked exhaustively against the previously frozen event file.

This is configuration holdout consumption2: independent verification only,
without optimization or account edits. All source/artifact SHA values are
verified before data reads; the audit file and start marker never overwrite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = Path(__file__).resolve().parent
RESULTS = EXPERIMENT / "results"
STEP = pd.Timedelta(hours=1)
BOUNDS = {
    "earlier": (pd.Timestamp("2026-05-15T00:00Z"), pd.Timestamp("2026-07-10T00:00Z")),
    "known": (pd.Timestamp("2026-07-10T00:00Z"), pd.Timestamp("2026-09-09T00:00Z")),
}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            result.update(block)
    return result.hexdigest()


def verify(path, expected, inputs):
    path = Path(path).resolve()
    actual = digest(path)
    if actual != expected:
        raise ValueError("SHA mismatch: " + str(path))
    inputs[str(path)] = actual
    return path


def equal(a, b):
    return bool(np.isclose(float(a), float(b), rtol=1e-10, atol=1e-12, equal_nan=True))


def manual_features(bars):
    """Rebuild recurrences from this continuous segment's native origin only."""
    high, low, close = (bars[x].to_numpy(float) for x in ("high", "low", "close"))
    source = (high+low+close)/3
    n = len(bars)
    ema1, ema2 = np.empty(n), np.empty(n)
    alpha = 2/35
    for i in range(n):
        ema1[i] = source[i] if i == 0 else alpha*source[i]+(1-alpha)*ema1[i-1]
        ema2[i] = ema1[i] if i == 0 else alpha*ema1[i]+(1-alpha)*ema2[i-1]
    def smoothed(values, period):
        out = np.full(n, np.nan)
        if n >= period:
            out[period-1] = sum(values[:period])/period
        for j in range(period, n):
            out[j] = (out[j-1]*(period-1)+values[j])/period
        return out
    hi, lo = smoothed(high, 34), smoothed(low, 34)
    middle = 2*ema1-ema2
    md = np.where(middle>hi, middle-hi, np.where(middle<lo, middle-lo, 0.))
    tr = np.array([max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1]))
                   if i else high[i]-low[i] for i in range(n)])
    return dict(md=md, atr=smoothed(tr, 14), tr=tr)


def replay(bars, md, atr, decision_i):
    """Long next-open entry, fixed2ATR hard stop and md close-to-next-open exit."""
    first = decision_i+1
    entry = float(bars.open.iloc[first])
    risk = 2*float(atr[decision_i])
    stop = entry-risk
    if not 0 < risk < entry:
        raise ValueError("Invalid frozen initial risk in selected valid event")
    pending = None
    mfe = mae = 0.
    for j in range(first, len(bars)):
        o, h, l, c = [float(bars[x].iloc[j]) for x in ("open", "high", "low", "close")]
        mfe, mae = max(mfe, o/entry-1), max(mae, 1-o/entry)
        if o <= stop:
            price, reason, timing = o, "initial_stop_gap", "open"
            break
        if pending is not None:
            price, reason, timing = o, "md", "open"
            break
        if l <= stop:
            price, reason, timing = stop, "initial_stop", "intrabar_unknown"
            break
        mfe, mae = max(mfe, h/entry-1), max(mae, 1-l/entry)
        if j == len(bars)-1:
            price, reason, timing = c, "boundary_mark", "close"
            break
        if md[j] <= 0:
            pending = j
    gross, net = price/entry-1, price/entry-1-.002
    mfe, mae = max(mfe, gross), max(mae, -gross)
    lower = bars.index[j] + (STEP if timing == "close" else pd.Timedelta(0))
    upper = lower + (STEP if timing == "intrabar_unknown" else pd.Timedelta(0))
    return dict(entry_i=first, entry_time=bars.index[first], entry_price=entry,
                initial_risk=risk, initial_stop=stop, exit_i=j, exit_price=price,
                exit_reason=reason, exit_timing=timing, exit_time=upper,
                exit_time_lower=lower, exit_time_upper=upper,
                censored=reason == "boundary_mark", natural_exit=reason != "boundary_mark",
                gross_return=gross, net_return=net, net_bp=net*10000,
                net_r=net/(risk/entry), mfe_return=mfe, mae_return=mae)


def self_test():
    """Exercise chronology and ambiguity using synthetic prices, not history."""
    index = pd.date_range("2020-01-01", periods=5, freq="h", tz="UTC")
    b = pd.DataFrame(dict(open=100., high=101., low=99., close=100.), index=index)
    atr, md = np.full(5, 5.), np.ones(5)
    r = replay(b, md, atr, 0)
    assert r["censored"] and r["exit_i"] == 4 and r["exit_time"] == index[-1]+STEP
    assert equal(r["net_return"], -.002)
    md[2] = 0
    b.loc[index[3], ["open", "high", "close"]] = [103., 104., 103.]
    r = replay(b, md, atr, 0)
    assert r["exit_reason"] == "md" and r["exit_i"] == 3 and r["exit_price"] == 103.
    b.loc[index[3], ["open", "high", "low", "close"]] = [85., 86., 84., 85.]
    r = replay(b, md, atr, 0)
    assert r["exit_reason"] == "initial_stop_gap" and r["exit_price"] == 85.
    b.loc[index[1], ["low", "high"]] = [89., 1000.]
    r = replay(b, md, atr, 0)
    assert r["exit_reason"] == "initial_stop" and r["exit_price"] == 90.
    assert r["mfe_return"] == 0 and r["exit_time_lower"] < r["exit_time_upper"]
    print(json.dumps(dict(synthetic_cases=4, failures=0)))


def run():
    output, started = RESULTS / "audit.json", RESULTS / "audit_started.json"
    if output.exists() or started.exists():
        raise ValueError("Audit already started or completed; will not overwrite historical evidence")
    saved = subprocess.check_output(["git", "show", "HEAD:"+str(Path(__file__).relative_to(ROOT))], cwd=ROOT)
    if saved != Path(__file__).read_bytes():
        raise ValueError("Commit exact audit builder before historical verification")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    with started.open("x") as stream:
        json.dump(dict(started_at=pd.Timestamp.now(tz="UTC").isoformat(), code_commit=commit,
                       holdout_consumption=2, scope="independent verification; no optimization"), stream)
    inputs, errors, cases = {}, [], []
    manifest_path = RESULTS / "dataset_manifest.json"
    inputs[str(manifest_path)] = digest(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    artifact_map = {Path(x["path"]).name: x for x in manifest["artifacts"]}
    def read_artifact(name):
        record = artifact_map[name]
        return pd.read_csv(verify(record["path"], record["sha256"], inputs), float_precision="round_trip")
    frames = {}
    for era in BOUNDS:
        f = read_artifact(era+"_events.csv.gz")
        f["audit_era"] = era
        frames[era] = f
    coverage = read_artifact("coverage.csv")
    coverage = {(r["venue"], r["symbol"]): r for r in coverage.to_dict("records")}
    feature_map = {str(Path(x["path"]).resolve()): x["sha256"] for x in manifest["feature_sources"]}
    selected = []
    for era, frame in frames.items():
        for venue, group in frame.loc[frame.valid.eq(True)].groupby("venue", sort=True):
            first = group.sort_values("event_id").head(2).copy()
            first["selection_reason"] = "first2_event_ids"
            selected.append(first)
            if era == "earlier":
                latest = group.sort_values(["decision_time", "event_id"]).tail(1).copy()
                latest["selection_reason"] = "last_earlier_decision"
                selected.append(latest)
    account_manifest_path = RESULTS / "accounts_manifest.json"
    inputs[str(account_manifest_path)] = digest(account_manifest_path)
    account_manifest = json.loads(account_manifest_path.read_text())
    if account_manifest["input_manifest_sha256"] != digest(manifest_path):
        raise ValueError("Accounts refer to a different dataset manifest")
    account_artifacts = {Path(x["path"]).name: x for x in account_manifest["artifacts"]}
    account_outliers = {}
    for era, frame in frames.items():
        record = account_artifacts[era+"_combined_baseline_all_assets_full_ledger.csv.gz"]
        ledger = pd.read_csv(verify(record["path"], record["sha256"], inputs), float_precision="round_trip")
        top = ledger.sort_values(["realized_net_pnl", "event_id"], ascending=[False, True]).iloc[0]
        chosen = frame.loc[frame.event_id.eq(top.event_id)].copy()
        if len(chosen) != 1:
            raise ValueError("Account outlier is not exactly one original event")
        chosen["selection_reason"] = "largest_account_profit_anomaly"
        selected.append(chosen)
        account_outliers[(era, top.event_id)] = top.to_dict()
    selected = pd.concat(selected, ignore_index=True).drop_duplicates(["audit_era", "event_id"])
    assert len(selected) <= 17
    feature_cache, source_cache = {}, {}
    fields = ["open", "high", "low", "close", "volume", "quote_volume"]
    def check(event_id, name, condition):
        if not bool(condition):
            errors.append(dict(event_id=event_id, check=name))
    for row in selected.to_dict("records"):
        event_id, era = row["event_id"], row["audit_era"]
        meta = coverage[(row["venue"], row["symbol"])]
        path = str(Path(row["features_path"]).resolve())
        if path not in feature_cache:
            feature_cache[path] = pd.read_pickle(verify(path, feature_map[path], inputs))
        f = feature_cache[path]
        source_path = str(Path(meta["source_path"]).resolve())
        if source_path not in source_cache:
            raw = pd.read_csv(verify(source_path, meta["source_sha256"], inputs), float_precision="round_trip")
            raw.index = pd.DatetimeIndex(pd.to_datetime(raw.ts, unit="ms", utc=True))
            repeated = raw.loc[raw.index.duplicated(keep=False)]
            if len(repeated) and (repeated.groupby(level=0)[fields].nunique(dropna=False)>1).any().any():
                raise ValueError("Conflicting duplicate native candles")
            source_cache[source_path] = raw.loc[~raw.index.duplicated()].sort_index()
        raw = source_cache[source_path]
        prefix = f.loc[f.index < BOUNDS[era][1]]
        b = raw.loc[prefix.index].copy()
        i = int(row["decision_i"])
        check(event_id, "one_hour_contiguous_source", (np.diff(b.index.asi8) == STEP.value).all())
        check(event_id, "source_feature_OHLCV_parity", np.allclose(b[fields].to_numpy(float), prefix[fields].to_numpy(float), rtol=1e-12, atol=1e-14))
        check(event_id, "decision_clock", pd.Timestamp(row["decision_time"]) == b.index[i]+STEP)
        check(event_id, "decision_in_era", BOUNDS[era][0] <= pd.Timestamp(row["decision_time"]) < BOUNDS[era][1])
        manual = manual_features(b)
        check(event_id, "source_ATR14_at_decision", equal(manual["atr"][i], prefix.atr.iloc[i]))
        check(event_id, "source_MD_full_prefix", np.allclose(manual["md"], prefix.md, rtol=1e-9, atol=1e-12, equal_nan=True))
        median = float(np.median(b.volume.iloc[i-20:i]))
        volume_ratio = float(b.volume.iloc[i])/median if median else np.nan
        previous_atr = manual["atr"][i-1]
        expansion = manual["tr"][i]/previous_atr if previous_atr else np.nan
        check(event_id, "relative_volume_prior20", equal(volume_ratio, row["relative_volume"]))
        check(event_id, "TR_over_priorATR", equal(expansion, row["tr_expansion"]))
        got = replay(b, manual["md"], manual["atr"], i)
        for name in ("entry_i", "exit_i", "exit_reason", "exit_timing", "censored", "natural_exit"):
            check(event_id, "replay_"+name, got[name] == row[name])
        for name in ("entry_time", "exit_time", "exit_time_lower", "exit_time_upper"):
            check(event_id, "replay_"+name, got[name] == pd.Timestamp(row[name]))
        for name in ("entry_price", "initial_risk", "initial_stop", "exit_price", "gross_return", "net_return", "net_bp", "net_r", "mfe_return", "mae_return"):
            check(event_id, "replay_"+name, equal(got[name], row[name]))
        check(event_id, "boundary_no_future_prices", b.index[-1] < BOUNDS[era][1] and got["exit_time"] <= BOUNDS[era][1])
        if row["censored"]:
            check(event_id, "censored_last_source_close", got["exit_i"] == len(b)-1 and equal(got["exit_price"], b.close.iloc[-1]))
        ledger = account_outliers.get((era, event_id))
        if ledger is not None:
            expected_pnl = float(ledger["quantity"])*(got["exit_price"]-got["entry_price"]-.002*got["entry_price"])
            check(event_id, "outlier_realized_cash_pnl", equal(expected_pnl, ledger["realized_net_pnl"]))
            check(event_id, "outlier_quantity_notional", equal(ledger["quantity"]*got["entry_price"], ledger["notional"]))
        cases.append(dict(era=era, venue=row["venue"], symbol=row["symbol"], event_id=event_id,
                          selection_reason=row["selection_reason"], decision_time=row["decision_time"],
                          exit_reason=got["exit_reason"], censored=got["censored"],
                          relative_volume_manual=volume_ratio, tr_expansion_manual=expansion,
                          entry_price=got["entry_price"], exit_price=got["exit_price"],
                          account_outlier=ledger is not None,
                          account_realized_net_pnl=ledger["realized_net_pnl"] if ledger else None,
                          last_source_open=b.index[-1].isoformat(), failures=sum(e["event_id"] == event_id for e in errors)))
    old_record = manifest["old_results_manifest"]
    old_manifest = json.loads(verify(old_record["path"], old_record["sha256"], inputs).read_text())
    old_events_record = next(x for x in old_manifest["artifacts"] if Path(x["path"]).name == "events.csv.gz")
    old_path = Path(old_events_record["path"])
    if not old_path.is_absolute():
        old_path = ROOT / old_path
    old_events = pd.read_csv(verify(old_path, old_events_record["sha256"], inputs), float_precision="round_trip")
    old_events = old_events.loc[old_events.minutes.eq(60) & old_events.arm.eq("focus_md")].set_index("event_id").sort_index()
    known = frames["known"].set_index("event_id").sort_index()
    check("known_all", "identical_event_ids", old_events.index.equals(known.index))
    shared = known.index.intersection(old_events.index)
    for name in ("entry_price", "exit_price", "initial_risk", "initial_stop", "net_return", "net_bp", "net_r", "decision_i", "entry_i", "exit_i"):
        check("known_all", "original_"+name, np.allclose(known.loc[shared, name], old_events.loc[shared, name], rtol=0, atol=0, equal_nan=True))
    for name in ("decision_time", "entry_time", "exit_time", "exit_time_lower", "exit_time_upper"):
        left = pd.to_datetime(known.loc[shared, name], utc=True)
        right = pd.to_datetime(old_events.loc[shared, name], utc=True)
        check("known_all", "original_"+name, ((left == right) | (left.isna() & right.isna())).all())
    boundary_rows = 0
    for era, frame in frames.items():
        for path_value, group in frame.loc[frame.valid.eq(True) & frame.censored.eq(True)].groupby("features_path"):
            path = str(Path(path_value).resolve())
            if path not in feature_cache:
                feature_cache[path] = pd.read_pickle(verify(path, feature_map[path], inputs))
            prefix = feature_cache[path].loc[lambda f: f.index < BOUNDS[era][1]]
            for row in group.to_dict("records"):
                boundary_rows += 1
                ok = (row["exit_reason"] == "boundary_mark" and row["exit_timing"] == "close"
                      and not row["natural_exit"] and int(row["exit_i"]) == len(prefix)-1
                      and pd.Timestamp(row["exit_time"]) == prefix.index[-1]+STEP
                      and equal(row["exit_price"], prefix.close.iloc[-1]))
                check(row["event_id"], "all_censored_frozen_boundary", ok)
    result = dict(generated_at=pd.Timestamp.now(tz="UTC").isoformat(), code_commit=commit,
                  audit_source_sha256=digest(__file__), holdout_consumption=2,
                  scope="Independent verification of fixed outputs; no optimization, new threshold or account mutation",
                  selection="First2 valid event_ids per era/venue plus last valid earlier decision per venue; additionally each era's largest baseline account profit contribution as a declared anomaly audit",
                  selected=len(selected), known_parity_rows=len(known),
                  censored_selected=sum(x["censored"] for x in cases),
                  all_censored_boundary_rows=boundary_rows,
                  all_censored_boundary_checks="Every valid censored event checked against its SHA-verified feature segment's era endpoint and close; selected cases additionally independently replayed from native OHLCV",
                  cases=cases, input_sha256=inputs, errors=errors,
                  limitations="Finite independent replay sample; exhaustive known key-field parity does not prove future strategy performance")
    earlier_valid = frames["earlier"].loc[frames["earlier"].valid.eq(True)]
    check("earlier_all", "exit_not_after_era", (pd.to_datetime(earlier_valid.exit_time, utc=True) <= BOUNDS["earlier"][1]).all())
    result["earlier_exit_clock_rows"] = len(earlier_valid)
    with output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, default=str)
        stream.write("\n")
    print(json.dumps(dict(output=str(output), selected=len(selected), known_parity_rows=len(known),
                         censored_selected=result["censored_selected"], failures=len(errors))))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    self_test() if args.self_test else run()
