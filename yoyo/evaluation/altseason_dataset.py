"""Materialize fixed multivenue event/control artifacts for the altcoin study.

Only confirmed native1H OHLCV and catalog metadata are inputs. Missing hours
split independent histories; indicators restart and no price is interpolated.
Known decision features use the causal engine. Higher permission joins only
completed, ready4H observations. Calendar daily/weekly returns are explicitly
future outcome diagnostics and never participate in event or control selection.

Controls are frozen before any simulation: exact venue/coin/timeframe, decision
CLOSE UTC Monday week and current causal ATR% rolling240(min60) rank quintile,
excluding every eligible candidate +/-12 bars. Up to3 controls are drawn without
replacement per decision, reused across that decision's exit-rule comparisons.
Main controls require history>=340; short-history controls require60..339.
Different actual decisions may share controls; matched ids remain distinct.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Dict

import numpy as np
import pandas as pd

from yoyo.data import altcoin_features
from yoyo.data.altseason_sources import atomic_json, digest, utc_now
from yoyo.evaluation import altseason_engine as engine


SOURCE_START = pd.Timestamp("2026-05-01T00:00:00Z")
EVAL_START = pd.Timestamp("2026-07-10T00:00:00Z")
PERIOD_CUT = pd.Timestamp("2026-08-10T00:00:00Z")
EVAL_END = pd.Timestamp("2026-09-09T00:00:00Z")
STABLE_ASSETS = frozenset(("USDT", "USDC", "DAI", "FDUSD", "USDE", "USDD", "USD1", "TUSD", "BUSD", "PYUSD", "USDP", "USDS", "FRAX", "LUSD", "GUSD", "GHO", "RLUSD", "USDF", "USD0", "SUSDE", "EURC", "EURI"))
CONFIG = {"schema": "altseason-dataset-v1", "source_start": SOURCE_START.isoformat(), "evaluation_start": EVAL_START.isoformat(),
          "period_cut": PERIOD_CUT.isoformat(), "exclusive_end": EVAL_END.isoformat(), "arms": engine.ARMS,
          "control_maximum": 3, "control_seed": 20260910, "control_exclusion_bars": 12,
          "atr_rank_window": 240, "atr_rank_minimum": 60, "stable_exclusions": sorted(STABLE_ASSETS)}
ID_COLS = ["event_id", "venue", "symbol", "asset", "instrument", "features_path", "minutes", "arm", "exit_rule", "period", "decision_i", "decision_time", "valid", "net_bp", "signal_quote_volume"]
CALENDAR_COLS = ["venue", "symbol", "asset", "instrument", "kind", "window_start", "window_end", "open", "close", "high", "peak_time", "close_return", "peak_return", "coverage_hours", "features_path_60", "features_path_240"]
DAILY_COLS = ["time", "venue", "symbol", "asset", "instrument", "above_sma60", "md_positive", "quote_volume_24h", "ready"]


def _hash(path: Path) -> str:
    return digest(path.read_bytes())


def _json_hash(value) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode())


def _id(*parts) -> str:
    return digest("|".join(str(x) for x in parts).encode())[:28]


def _period(stamp) -> str:
    return "first31" if stamp < PERIOD_CUT else "last30"


def split_hourly(raw: pd.DataFrame):
    """Reject conflicting duplicate prices, then split every missing-hour gap."""
    required = ["ts", *engine.BAR_COLUMNS]
    if not set(required).issubset(raw.columns):
        raise ValueError("Missing native normalized OHLCV columns")
    data = raw.copy()
    numeric_ts = pd.to_numeric(data.ts, errors="raise")
    if numeric_ts.isna().any() or (numeric_ts % 3600000).any():
        raise ValueError("Non-hour-aligned or missing timestamps")
    data["ts"] = numeric_ts.astype("int64")
    for field in engine.BAR_COLUMNS:
        data[field] = pd.to_numeric(data[field], errors="raise")
    for field in ("confirmed", "confirm"):
        if field in data and not data[field].map(lambda value: str(value) in ("1", "1.0", "True")).all():
            raise ValueError("Unconfirmed source candle")
    duplicate = data[data.duplicated("ts", keep=False)]
    if len(duplicate) and (duplicate.groupby("ts")[list(engine.BAR_COLUMNS)].nunique(dropna=False) > 1).any().any():
        raise ValueError("Conflicting duplicate OHLCV bars")
    duplicate_count = int(data.duplicated("ts").sum())
    data = data.drop_duplicates("ts").sort_values("ts")
    data.index = pd.DatetimeIndex(pd.to_datetime(data.ts, unit="ms", utc=True), name="open_time")
    data = data.loc[(data.index >= SOURCE_START) & (data.index < EVAL_END), list(engine.BAR_COLUMNS)]
    if not len(data):
        return [], {"duplicate_rows_removed": duplicate_count, "filtered_rows": 0, "gap_count": 0, "missing_hours_between_rows": 0}
    differences = np.diff(data.index.asi8) // pd.Timedelta(hours=1).value
    boundaries = np.r_[0, np.flatnonzero(differences != 1) + 1, len(data)]
    segments = [data.iloc[int(a):int(b)].copy() for a, b in zip(boundaries[:-1], boundaries[1:])]
    for segment in segments:
        segment.attrs.update(minutes=60, period_seconds=3600)
    return segments, {"duplicate_rows_removed": duplicate_count, "filtered_rows": len(data), "gap_count": len(segments) - 1,
                      "missing_hours_between_rows": int(np.maximum(differences - 1, 0).sum())}


def higher_permission(one: pd.DataFrame, four: pd.DataFrame) -> pd.Series:
    """Join4H decision closes, never a still-open4H bar or unready history."""
    if not len(four):
        return pd.Series(np.nan, index=one.index, dtype=float)
    permission = ((four.md > four.sb) & (four.md > 0)).astype(float).where(four.ready.astype(bool))
    permission.index = four.index + pd.Timedelta(hours=4)
    values = permission.reindex(one.index + pd.Timedelta(hours=1), method="ffill")
    values.index = one.index
    return values


def _week(stamps: pd.DatetimeIndex) -> pd.DatetimeIndex:
    days = stamps.normalize()
    return days - pd.to_timedelta(days.dayofweek, unit="D")


def match_controls(features: pd.DataFrame, all_candidates: pd.DataFrame, evaluation_candidates: pd.DataFrame, instrument: str, minutes: int):
    """Return a matching-only decision map before any future outcome exists."""
    if not len(evaluation_candidates):
        return {}
    rank = features.atr_pct.rolling(240, min_periods=60).rank(pct=True)
    buckets = np.ceil(rank.to_numpy(dtype=float) * 5)
    closes = features.index + pd.Timedelta(minutes=minutes)
    weeks = _week(closes).asi8
    history = features.history_count.to_numpy(dtype=int)
    excluded = np.zeros(len(features), dtype=bool)
    for value in all_candidates.decision_i.unique():
        i = int(value)
        excluded[max(0, i - 12):min(len(features), i + 13)] = True
    pool = (~excluded & np.isfinite(buckets) & (closes >= EVAL_START) & (closes < EVAL_END))
    if len(pool):
        pool[-1] = False  # Controls require a known next open.
    seed = (20260910 + int(hashlib.sha256((instrument + ":" + str(minutes)).encode()).hexdigest()[:16], 16)) % (2 ** 64)
    rng = np.random.default_rng(seed)
    matches = {}
    for i in sorted(int(x) for x in evaluation_candidates.decision_i.unique()):
        if not np.isfinite(buckets[i]):
            matches[i] = []
            continue
        young = history[i] < 340
        history_pool = (history >= 60) & (history < 340) if young else history >= 340
        choices = np.flatnonzero(pool & history_pool & (weeks == weeks[i]) & (buckets == buckets[i]))
        matches[i] = [int(x) for x in rng.choice(choices, size=min(3, len(choices)), replace=False)]
    return matches


def calendar_artifacts(one: pd.DataFrame, identity: Dict[str, Any], path60: Path, path240: Path):
    """Full UTC-day/week future diagnostics, wholly separate from candidates."""
    calendar, context = [], []
    if not len(one):
        return calendar, context
    for kind, hours in (("day", 24), ("week", 168)):
        keys = one.index.normalize() if kind == "day" else _week(one.index)
        for start, group in one.groupby(keys, sort=True):
            end = start + pd.Timedelta(hours=hours)
            complete = len(group) == hours and group.index[0] == start and group.index[-1] == end - pd.Timedelta(hours=1)
            if not complete:
                continue
            if kind == "day":
                last = group.iloc[-1]
                context.append(dict(identity, time=end, above_sma60=float(last.close > last.sma60) if pd.notna(last.sma60) else np.nan,
                                    md_positive=float(last.md > 0) if pd.notna(last.md) else np.nan,
                                    quote_volume_24h=float(group.quote_volume.sum()), ready=bool(last.ready)))
            if start < EVAL_START or end > EVAL_END:
                continue
            opening, closing, high = float(group.open.iloc[0]), float(group.close.iloc[-1]), float(group.high.max())
            calendar.append(dict(identity, kind=kind, window_start=start, window_end=end, open=opening, close=closing, high=high,
                                 peak_time=group.high.idxmax(), close_return=closing/opening-1, peak_return=high/opening-1,
                                 coverage_hours=hours, features_path_60=str(path60), features_path_240=str(path240)))
    return calendar, context


def _csv(path: Path, frame: pd.DataFrame):
    tmp = path.with_name(path.name + ".tmp")
    frame.to_csv(tmp, index=False, compression={"method": "gzip", "mtime": 0} if path.name.endswith(".gz") else None)
    tmp.replace(path)


def _feature_context(features: pd.DataFrame, i: int, minutes: int):
    result = {name: features[name].iloc[i] for name in engine.CONTEXT_COLUMNS if name in features}
    result.update(signal_i=i, decision_i=i, minutes=minutes, decision_bar_open_time=features.index[i],
                  decision_time=features.index[i] + pd.Timedelta(minutes=minutes),
                  signal_quote_volume=float(features.quote_volume.iloc[i]), higher_permission=features.higher_permission.iloc[i])
    return result


def process_market(input_csv: Path, market: Dict[str, Any], outdir: Path, source_manifest=None):
    """Write market artifacts and explicit coverage/errors; no silent skipping."""
    input_csv, outdir = Path(input_csv), Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    venue, symbol, asset = market["venue"], market["symbol"], market["base_symbol"]
    coverage_path = outdir / "coverage.json"
    identity = {"source_path": str(input_csv.resolve()), "source_sha256": _hash(input_csv) if input_csv.exists() else None, "source_manifest_hash": _json_hash(source_manifest),
                "builder_sha256": _hash(Path(__file__)), "engine_sha256": _hash(Path(engine.__file__)),
                "feature_builder_sha256": _hash(Path(altcoin_features.__file__)), "config_sha256": _json_hash(CONFIG), "market_hash": _json_hash(market)}
    if coverage_path.exists():
        prior = json.loads(coverage_path.read_text())
        if prior.get("identity") == identity and not prior.get("errors") and all(Path(x["path"]).exists() and _hash(Path(x["path"])) == x["sha256"] for x in prior.get("artifacts", [])):
            return prior
    coverage = {"venue": venue, "symbol": symbol, "asset": asset, "identity": identity, "config": CONFIG, "generated_at": utc_now(),
                "source_manifest": source_manifest, "catalog_listing_ms": market.get("listing_ms"), "catalog_delisting_ms": market.get("delisting_ms"),
                "catalog_status": market.get("status"), "catalog_asof": market.get("asof"), "errors": [], "segments": [], "artifacts": []}
    outputs, events, controls, calendars, daily = [], [], [], [], []
    exclude = "background_btc_eth" if asset.upper() in ("BTC", "ETH") else "stable_asset" if asset.upper() in STABLE_ASSETS else "catalog_ineligible" if not market.get("eligible", True) else ""
    coverage["exclude_reason"] = exclude
    try:
        if not input_csv.exists():
            raise ValueError("Normalized input file is missing")
        if source_manifest is not None and source_manifest.get("sha256") != identity["source_sha256"]:
            raise ValueError("Normalized source differs from acquisition manifest SHA")
        raw = pd.read_csv(input_csv)
        coverage["raw_rows"] = len(raw)
        if "venue" in raw and len(raw) and not raw.venue.eq(venue).all():
            raise ValueError("Input venue mismatch")
        if "symbol" in raw and len(raw) and not raw.symbol.eq(symbol).all():
            raise ValueError("Input symbol mismatch")
        segments, summary = split_hourly(raw)
        coverage.update(summary)
        if not segments:
            coverage["empty_reason"] = "no_complete_source_hours_in_acquisition_window"
        for number, hourly in enumerate(segments):
            instrument = "%s:%s:segment%d" % (venue, symbol, number)
            common = {"venue": venue, "symbol": symbol, "asset": asset, "instrument": instrument}
            allow_young = number == 0 and hourly.index[0] > SOURCE_START
            tables = engine.build_features(hourly)
            tables[60]["higher_permission"] = higher_permission(tables[60], tables[240])
            tables[240]["higher_permission"] = np.nan
            paths = {m: (outdir / ("segment%d_%d_features.pkl.gz" % (number, m))).resolve() for m in (60, 240)}
            seginfo = {"instrument": instrument, "segment": number, "source_hours": len(hourly), "source_first_open": hourly.index[0].isoformat(),
                       "source_last_open": hourly.index[-1].isoformat(), "young_source_allowed": allow_young, "reset_after_gap": number > 0,
                       "listing_date_does_not_prove_continuous_source": True, "timeframes": {}}
            for minutes, features in tables.items():
                path = paths[minutes]
                features.to_pickle(path, compression={"method": "gzip", "mtime": 0})
                outputs.append(path)
                if not len(features):
                    seginfo["timeframes"][str(minutes)] = {"bars": 0, "mature_evaluable_bars": 0, "candidates": 0, "valid": 0}
                    continue
                all_candidates = engine.candidate_events(features, minutes)
                if not allow_young:
                    all_candidates = all_candidates.loc[all_candidates.arm != "young_breakout_sma20"]
                candidates = all_candidates.loc[(all_candidates.decision_time >= EVAL_START) & (all_candidates.decision_time < EVAL_END)].copy()
                if exclude:
                    candidates = candidates.iloc[:0]
                matching = match_controls(features, all_candidates, candidates, instrument, minutes)
                # Match decisions are now frozen, before constructing any outcomes.
                prepared = engine.prepare_simulation(features, features) if len(candidates) else None
                simulated = {}
                local_events, local_controls = [], []

                def outcome(index, rule):
                    key = (int(index), rule)
                    if key not in simulated:
                        simulated[key] = engine.simulate_prepared(prepared, int(index), rule, len(features)-1)
                    return dict(simulated[key])

                for candidate in candidates.to_dict("records"):
                    i, arm, rule = int(candidate["decision_i"]), candidate["arm"], candidate["exit_rule"]
                    event_id = _id(instrument, minutes, arm, int(features.index[i].value))
                    result = dict(candidate)
                    result.update(outcome(i, rule))
                    result.update(common, event_id=event_id, features_path=str(path), arm=arm, minutes=minutes,
                                  signal_quote_volume=float(features.quote_volume.iloc[i]), higher_permission=features.higher_permission.iloc[i],
                                  period=_period(candidate["decision_time"]))
                    matched = []
                    for position, control_i in enumerate(matching[i]):
                        control = _feature_context(features, control_i, minutes)
                        control.update(outcome(control_i, rule))
                        control.update(common, event_id=_id(event_id, "control", position, control_i), matched_event_id=event_id,
                                       matched_decision_i=i, matched_decision_time=candidate["decision_time"], control_number=position,
                                       arm=arm, exit_rule=rule, features_path=str(path), period=_period(control["decision_time"]))
                        local_controls.append(control)
                        if control.get("valid"):
                            matched.append(float(control["net_bp"]))
                    result["matched_control_count"] = len(matching[i])
                    result["control_count"] = len(matched)
                    result["control_mean_net_bp"] = float(np.mean(matched)) if matched else np.nan
                    result["excess_bp"] = result.get("net_bp", np.nan) - result["control_mean_net_bp"] if result.get("valid") else np.nan
                    local_events.append(result)
                events.extend(local_events)
                controls.extend(local_controls)
                closes = features.index + pd.Timedelta(minutes=minutes)
                mask = (closes >= EVAL_START) & (closes < EVAL_END)
                seginfo["timeframes"][str(minutes)] = {"bars": len(features), "evaluation_bars": int(mask.sum()),
                    "mature_evaluable_bars": int((mask & features.ready.astype(bool).to_numpy()).sum()),
                    "young_evaluable_bars": int((mask & features.history_count.ge(60).to_numpy() & features.history_count.lt(340).to_numpy()).sum()) if allow_young else 0,
                    "candidates": len(local_events), "valid": sum(bool(x.get("valid")) for x in local_events), "matched_control_rows": len(local_controls), "unique_simulations": len(simulated)}
            calendar, context = calendar_artifacts(tables[60], common, paths[60], paths[240])
            calendars.extend(calendar)
            daily.extend(context)
            coverage["segments"].append(seginfo)
        coverage.update(candidates=len(events), valid=sum(bool(x.get("valid")) for x in events), controls=len(controls),
                        calendar_windows=len(calendars), daily_context_rows=len(daily), segment_count=len(segments))
    except Exception as exc:
        coverage["errors"].append({"type": type(exc).__name__, "message": str(exc)})
        events, controls, calendars, daily, outputs = [], [], [], [], []
        coverage.update(candidates=0, valid=0, controls=0, calendar_windows=0, daily_context_rows=0)
    tables = {"events.csv.gz": pd.DataFrame(events) if events else pd.DataFrame(columns=ID_COLS + ["control_count", "control_mean_net_bp", "excess_bp"]),
              "controls.csv.gz": pd.DataFrame(controls) if controls else pd.DataFrame(columns=ID_COLS + ["matched_event_id", "matched_decision_i", "control_number"]),
              "calendar.csv.gz": pd.DataFrame(calendars, columns=CALENDAR_COLS), "daily_context.csv": pd.DataFrame(daily, columns=DAILY_COLS)}
    for name, table in tables.items():
        path = (outdir / name).resolve()
        _csv(path, table)
        outputs.append(path)
        coverage[name.replace(".csv.gz", "").replace(".csv", "") + "_path"] = str(path)
    coverage["artifacts"] = [{"path": str(path), "sha256": _hash(path)} for path in outputs]
    atomic_json(coverage_path, coverage)
    return coverage


def _verify_committed():
    repo = Path(__file__).resolve().parents[2]
    for path in (Path(__file__).resolve(), Path(engine.__file__).resolve(), Path(altcoin_features.__file__).resolve()):
        relative = str(path.relative_to(repo))
        committed = subprocess.run(["git", "show", "HEAD:" + relative], cwd=repo, capture_output=True, check=True).stdout
        if committed != path.read_bytes():
            raise ValueError("Commit builder before real data evaluation: " + relative)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--venue", required=True, choices=("binance", "gate", "okx"))
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("workers must be1..4")
    _verify_committed()
    catalog = json.loads((args.data / "catalog" / (args.venue + ".json")).read_text())
    lookup = {item["symbol"]: item for item in catalog["markets"]}
    skipped, completed, jobspecs = [], [], []
    for csv in sorted((args.data / "normalized" / args.venue).glob("*_1h.csv.gz")):
        symbol = csv.name.removesuffix("_1h.csv.gz")
        meta_path = csv.with_suffix(".manifest.json")
        if not meta_path.exists():
            skipped.append({"symbol": symbol, "reason": "acquisition_manifest_missing"})
            continue
        try:
            metadata = json.loads(meta_path.read_text())
        except (OSError, ValueError) as exc:
            skipped.append({"symbol": symbol, "reason": "acquisition_manifest_unreadable", "detail": str(exc)})
            continue
        if metadata.get("sha256") != _hash(csv):
            skipped.append({"symbol": symbol, "reason": "acquisition_sha_not_ready"})
            continue
        if symbol not in lookup:
            skipped.append({"symbol": symbol, "reason": "catalog_missing"})
            continue
        jobspecs.append((csv, lookup[symbol], args.results / "markets" / args.venue / symbol, metadata))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = {pool.submit(process_market, *spec): spec[1]["symbol"] for spec in jobspecs}
        for future in as_completed(tasks):
            try:
                coverage = future.result()
                completed.append({"symbol": coverage["symbol"], "candidates": coverage["candidates"], "valid": coverage["valid"], "errors": coverage["errors"]})
            except Exception as exc:
                completed.append({"symbol": tasks[future], "candidates": 0, "valid": 0, "errors": [{"message": str(exc)}]})
            if len(completed) % 20 == 0:
                print(json.dumps({"venue": args.venue, "completed": len(completed), "total": len(jobspecs), "errors": sum(bool(x["errors"]) for x in completed)}), flush=True)
    atomic_json(args.results / (args.venue + "_dataset_manifest.json"), {"config": CONFIG, "generated_at": utc_now(), "completed": completed, "skipped": skipped})
    print(json.dumps({"venue": args.venue, "completed": len(completed), "skipped": len(skipped), "errors": sum(bool(x["errors"]) for x in completed), "candidates": sum(x["candidates"] for x in completed)}), flush=True)


if __name__ == "__main__":
    main()
