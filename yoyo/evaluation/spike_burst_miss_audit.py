"""Explain three owner-selected missed launches using frozen Burst V1 gates.

Source features and state are authenticated from the earlier V1 matching file.
Every feature uses current/prior OHLCV only: prior20 median volume, prior ATR14,
previous12 ribbon widths/crosses, and the existing replay's chronological state.
This diagnostic DOES NOT select parameters, score outcomes, simulate trades, or
infer recall from three selected successes. Full source history provides state;
the exported window is August18--21 Beijing time. Screenshot times are OPEN
timestamps, so the corresponding closed-bar decision is one hour later.

The state engine is reused unchanged. Explanatory columns independently expose
its branch prerequisites and assert their predicted arrow agrees with replay.
The chart's last-bar table is deliberately not treated as a cursor-bar reading.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_replay as engine

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "experiments/active/exp-spike-burst-validation-20260910-v1/results"
OUTPUT = ROOT / "experiments/active/exp-spike-burst-launch-recall-20260910-v2/diagnostics"
SCREENSHOTS = Path("/tmp/codex-remote-attachments/01a086ad-0b77-7380-b6a6-2b62f505d12b/2AABAF25-2432-4870-90F1-77A52218350D")
CASES = (
    dict(asset="HYPE", symbol="HYPE-USDT-SWAP", open_bjt="2026-08-19 23:00:00+08:00",
         prices=dict(open=59.880, high=61.880, low=59.643, close=61.851), precision=0.001,
         screenshot=str(SCREENSHOTS / "1-照片-1.jpg")),
    dict(asset="NEAR", symbol="NEAR-USDT-SWAP", open_bjt="2026-08-19 22:00:00+08:00",
         prices=dict(open=1.619, high=1.640, low=1.619, close=1.638), precision=0.001,
         screenshot=str(SCREENSHOTS / "2-照片-2.jpg")),
    dict(asset="PEPE", symbol="PEPE-USDT-SWAP", open_bjt="2026-08-19 22:00:00+08:00",
         prices=dict(open=0.000002595, high=0.000002656, low=0.000002594, close=0.000002650),
         precision=1e-9, screenshot=str(SCREENSHOTS / "3-照片-3.jpg")),
)
NAN = float("nan")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked(path, expected):
    path = Path(path).resolve()
    if sha(path) != expected:
        raise ValueError("Source hash mismatch: " + str(path))
    return path


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def force_gates(row, previous_md, previous_mi, high, low, price_first):
    """Explain CURRENT long force gates; high/low are already frozen prior boxes."""
    span = row.high - row.low
    known = span > 0 and all(math.isfinite(v) for v in
        (row.rv, row.expansion, previous_mi if price_first else previous_md,
         row.ropeHigh, row.ropeLow, high, low))
    body = abs(row.close - row.open) / span if span > 0 else NAN
    end = (row.close - row.low) / span if span > 0 else NAN
    gates = dict(known=known, bullish_close=row.close > row.open,
        frozen_box_break=row.close > high, above_six_ma=row.close > row.ropeHigh,
        md_direction=row.md >= 0 if price_first else row.md > 0,
        md_vs_signal=row.md >= row.sb if price_first else row.md > row.sb,
        momentum_increasing=row.middle > previous_mi if price_first else row.md > previous_md,
        rv4=row.rv >= engine.MIN_VOLUME, tr3=row.expansion >= engine.MIN_EXPANSION,
        body55=body >= engine.MIN_BODY, end75=end >= engine.MIN_END)
    return gates, body, end


def audit_trace(frame, state):
    """Return before/after state and each gate, without changing the V1 machine."""
    if not frame.index.equals(state.index):
        raise ValueError("Feature/state index mismatch")
    records = []
    default = dict(quiet_count=0, pending_side=0, trend_side=0, frozen_band=NAN,
                   quiet_high=NAN, quiet_low=NAN, launch_high=NAN, launch_low=NAN,
                   launch_band=NAN, release_bar=NAN, entry_bar=NAN, state="等待蓄势")
    for i, (_, row) in enumerate(frame.iterrows()):
        prev = default if i == 0 else state.iloc[i - 1].to_dict()
        actual = state.iloc[i]
        ready, ended = bool(row.ready), bool(actual["exit"])
        prior_md = float(frame.md.iloc[i - 1]) if i else NAN
        prior_mi = float(frame.middle.iloc[i - 1]) if i else NAN
        prior_atr = float(frame.atr.iloc[i - 1]) if i else NAN
        quiet, pending_before, trend_before = int(prev["quiet_count"]), int(prev["pending_side"]), int(prev["trend_side"])
        pending = 0 if ended else pending_before
        trend = 0 if ended else trend_before
        frozen = quiet >= engine.MIN_QUIET and math.isfinite(prev["frozen_band"])
        band = prev["frozen_band"] if frozen else engine.NEAR_ATR * prior_atr
        dense_width = bool(row.pastWidth <= engine.DENSE_WIDTH)
        dense_crosses = bool(row.pastCrosses >= engine.DENSE_CROSSES)
        dense = dense_width and dense_crosses
        can_lead = ready and quiet >= engine.MIN_QUIET and pending == trend == 0 and not ended and dense
        price, body, end = force_gates(row, prior_md, prior_mi, prev["quiet_high"], prev["quiet_low"], True)
        leading = can_lead and all(price.values())
        near = pending == 0 and max(abs(row.md), abs(row.sb)) <= band
        release = (ready and not leading and not near and quiet >= engine.MIN_QUIET
                   and dense and row.md > band and trend == 0 and not ended)
        born = leading or release
        during = 1 if born else pending
        zone_high = prev["quiet_high"] if born else prev["launch_high"]
        zone_low = prev["quiet_low"] if born else prev["launch_low"]
        zone_band = band if born else prev["launch_band"]
        released_i = i if born else prev["release_bar"]
        age = i - released_i if pd.notna(released_i) else NAN
        window_age = bool(0 <= age < engine.OPPORTUNITY)
        window_direction = bool(row.md > zone_band)
        window_structure = bool(row.close >= zone_low)
        window_valid = bool(leading or (window_age and window_direction and window_structure))
        force, _, _ = force_gates(row, prior_md, prior_mi, zone_high, zone_low, False)
        evaluate_pending = ready and during == 1 and trend == 0 and not ended
        predicted = evaluate_pending and window_valid and (leading or all(force.values()))
        if predicted != bool(actual.burst):
            raise AssertionError("Explanatory gates disagree with immutable replay at " + str(frame.index[i]))
        reasons = []
        if not ready:
            reasons.append("warmup_not_ready")
        if trend:
            reasons.append("reference_position_active")
        if ended:
            reasons.append("reference_exit_this_bar_no_reentry")
        if ready and not trend and not ended and not predicted:
            if during == 0:
                if quiet < engine.MIN_QUIET:
                    reasons.append("prior_quiet_count_below12")
                if not dense_width:
                    reasons.append("prior12_width_above3_atr")
                if not dense_crosses:
                    reasons.append("prior12_crosses_below2")
                if quiet >= engine.MIN_QUIET and near:
                    reasons.append("still_near_zero_price_first_incomplete")
                elif quiet >= engine.MIN_QUIET and row.md <= band:
                    reasons.append("release_md_not_above_frozen_band")
                reasons.extend("price_first_" + name for name, ok in price.items() if not ok)
            elif not window_valid:
                reasons.extend(name for name, ok in [("pending_age_outside0to5", window_age),
                    ("pending_md_back_inside_band", window_direction), ("pending_close_below_box_low", window_structure)] if not ok)
            else:
                reasons.extend("release_confirm_" + name for name, ok in force.items() if not ok)
        t = frame.index[i]
        item = dict(open_time=t, open_time_bjt=t.tz_convert("Asia/Shanghai"),
            confirm_time=t + pd.Timedelta(hours=1), confirm_time_bjt=(t + pd.Timedelta(hours=1)).tz_convert("Asia/Shanghai"),
            bar_i=i, ready=ready, quiet_before=quiet, quiet_after=int(actual.quiet_count),
            quiet_threshold=engine.MIN_QUIET, frozen_band_used=frozen, near_band=band,
            near_zero_max=max(abs(row.md), abs(row.sb)), near_current=bool(near),
            quiet_high_before=prev["quiet_high"], quiet_low_before=prev["quiet_low"],
            past_width=row.pastWidth, width_limit=engine.DENSE_WIDTH, width_pass=dense_width,
            past_crosses=row.pastCrosses, crosses_limit=engine.DENSE_CROSSES, crosses_pass=dense_crosses,
            rv=row.rv, rv_limit=engine.MIN_VOLUME, expansion=row.expansion, expansion_limit=engine.MIN_EXPANSION,
            body_ratio=body, body_limit=engine.MIN_BODY, close_location=end, close_location_limit=engine.MIN_END,
            md=row.md, previous_md=prior_md, sb=row.sb, middle=row.middle, previous_middle=prior_mi,
            atr=row.atr, previous_atr=prior_atr, rope_high=row.ropeHigh, rope_low=row.ropeLow,
            pending_before=pending_before, pending_during=during, pending_after=int(actual.pending_side),
            release_born=bool(release), price_first_born=bool(leading), can_price_first=bool(can_lead),
            frozen_launch_high=zone_high, frozen_launch_low=zone_low, frozen_launch_band=zone_band,
            pending_age=age, pending_age_pass=window_age, pending_md_pass=window_direction,
            pending_structure_pass=window_structure, pending_window_valid=window_valid,
            pending_evaluated=bool(evaluate_pending), opportunity_bars=engine.OPPORTUNITY,
            trend_before=trend_before, trend_after=int(actual.trend_side), reference_exit=ended,
            prior_entry_bar=prev["entry_bar"], state_before=prev["state"], state_after=actual.state,
            triggered=bool(actual.burst), route=actual.route, predicted_trigger=bool(predicted),
            blocking_reasons=" | ".join(reasons),
            force_failed=" | ".join(name for name, ok in force.items() if not ok),
            price_first_failed=" | ".join(name for name, ok in price.items() if not ok))
        for c in ("open", "high", "low", "close", "volume", "quote_volume", "pastVolume", "tr", "width", "flips"):
            item[c] = row.get(c, NAN)
        item.update({"price_first_" + k: bool(v) for k, v in price.items()})
        item.update({"release_confirm_" + k: bool(v) for k, v in force.items()})
        records.append(item)
    return pd.DataFrame(records, index=frame.index)


def screenshot_check(row, case):
    """Compare displayed decimal prices, not an unproven screenshot venue label."""
    details = {c: dict(screen=price, okx=float(row[c]), difference=float(row[c]) - price,
        matches_display=abs(float(row[c]) - price) <= case["precision"] / 2 + 1e-15)
        for c, price in case["prices"].items()}
    return dict(matches_all_displayed_prices=all(v["matches_display"] for v in details.values()),
        fields=details, venue_claim="Screenshot venue not legible; price agreement is supporting evidence, not proof of venue.")


def committed_sources():
    paths = [Path(__file__), Path(engine.__file__), ROOT / "yoyo/evaluation/pine/spike_burst_v1.pine"]
    for path in paths:
        relative = str(path.resolve().relative_to(ROOT))
        subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=ROOT,
                       stdout=subprocess.DEVNULL, check=True)
        subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT, check=True)
    if sha(paths[2]) != engine.SOURCE_SHA256:
        raise ValueError("Frozen Pine source changed")
    return {str(p.resolve()): sha(p) for p in paths}


def build(output=OUTPUT, prior_results=OLD):
    """Authenticate old features first, then write only V2 diagnostic artifacts."""
    builders = committed_sources()
    old = Path(prior_results).resolve()
    manifest_path = old / "dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest["status"] != "complete":
        raise ValueError("Prior dataset is incomplete")
    records = {str(Path(r["path"]).resolve()): r["sha256"] for r in manifest["artifacts"]}
    matching_path = checked(old / "matching.json", records[str(old / "matching.json")])
    matching = json.loads(matching_path.read_text())
    cov_record = next(r for r in manifest["prior_aggregate_sources"] if Path(r["path"]).name == "coverage.csv")
    coverage_path = checked(cov_record["path"], cov_record["sha256"])
    coverage = pd.read_csv(coverage_path, usecols=["venue", "symbol", "identity"])
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp("2026-08-18T00:00:00+08:00").tz_convert("UTC")
    end = pd.Timestamp("2026-08-22T00:00:00+08:00").tz_convert("UTC")
    summaries, artifacts = [], []
    for case in CASES:
        jobs = [j for j in matching["jobs"] if j["venue"] == "okx" and j["symbol"] == case["symbol"] and j["minutes"] == 60]
        if len(jobs) != 1:
            raise ValueError("Expected one authenticated OKX60m segment: " + case["symbol"])
        job = jobs[0]
        cached = pd.read_pickle(checked(job["features_path"], job["features_sha256"]))
        original = pd.read_pickle(checked(job["source_features_path"], job["source_features_sha256"]))
        cols = ["open", "high", "low", "close", "volume", "quote_volume"]
        pd.testing.assert_frame_equal(cached[cols], original[cols], check_dtype=False, check_exact=True)
        identities = coverage.loc[coverage.venue.eq("okx") & coverage.symbol.eq(case["symbol"]), "identity"]
        if len(identities) != 1:
            raise ValueError("Expected one native-source identity")
        identity = ast.literal_eval(identities.iloc[0])
        native_path = checked(identity["source_path"], identity["source_sha256"])
        native = pd.read_csv(native_path)
        native.index = pd.to_datetime(native.ts, unit="ms", utc=True).dt.as_unit("ns")
        if not native.index.is_unique or not native.index.is_monotonic_increasing:
            raise ValueError("Native candle timestamps must be unique and chronological")
        np.testing.assert_allclose(native.loc[cached.index, cols].to_numpy(dtype=float),
                                   cached[cols].to_numpy(dtype=float), rtol=0, atol=0)
        f = engine.features(cached[cols])
        for c in f.columns:
            pd.testing.assert_series_equal(f[c], cached[c], check_dtype=False, check_exact=True)
        state = engine.replay(f, float(job["tick"]))
        pd.testing.assert_frame_equal(state, cached[["replay_" + c for c in state.columns]].rename(
            columns=lambda c: c.removeprefix("replay_")), check_dtype=False, check_exact=True)
        trace = audit_trace(f, state)
        selected = trace.loc[(trace.index >= start) & (trace.index < end)]
        if len(selected) != 96:
            raise ValueError("Expected 96 uninterrupted hourly audit rows")
        path = output / (case["asset"].lower() + "_v1_gates_20260818_21.csv")
        selected.to_csv(path, index=False)
        artifacts.append(dict(path=str(path), sha256=sha(path), rows=len(selected)))
        t = pd.Timestamp(case["open_bjt"]).tz_convert("UTC")
        prior_arrows = state.loc[state.index < t].query("burst == True")
        target_rows = selected.loc[[pd.Timestamp("2026-08-19T14:00Z"), pd.Timestamp("2026-08-19T15:00Z")]]
        screen = Path(case["screenshot"])
        summaries.append(dict(asset=case["asset"], symbol=case["symbol"], tick=job["tick"],
            source_features=dict(path=job["source_features_path"], sha256=job["source_features_sha256"]),
            cached_features=dict(path=job["features_path"], sha256=job["features_sha256"]),
            native_hourly=dict(path=str(native_path), sha256=identity["source_sha256"],
                               six_column_exact_match_rows=len(cached)),
            all_history_bars=len(f), exported_bars=len(selected), screenshot_case=case,
            screenshot_sha256=sha(screen) if screen.exists() else None,
            screenshot_check=screenshot_check(f.loc[t], case), target=trace.loc[t].to_dict(),
            both_22_23_bjt=target_rows.to_dict("records"),
            prior_burst_open=prior_arrows.index[-1] if len(prior_arrows) else None,
            arrows_in_window=selected.loc[selected.triggered, ["open_time_bjt", "confirm_time_bjt", "route"]].to_dict("records"),
            exits_in_window=selected.loc[selected.reference_exit, ["open_time_bjt", "state_after"]].to_dict("records")))
    result = dict(schema="spike-burst-owner-miss-audit-v1", generated_at=pd.Timestamp.now(tz="UTC"),
        builder_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        builder_sources=builders, prior_manifest=dict(path=str(manifest_path), sha256=sha(manifest_path)),
        prior_matching=dict(path=str(matching_path), sha256=sha(matching_path)), cases=summaries, artifacts=artifacts,
        prior_coverage=dict(path=str(coverage_path), sha256=cov_record["sha256"]),
        status="complete", rules_changed=False, outcomes_scored=False,
        caveats=["Only three owner-selected positive examples: no recall/precision claim or parameter optimization.",
                 "Screenshots show opening times; signals confirm one hour later. Last-bar tables are not cursor data.",
                 "Full-source chronological replay is used; fresh features/state must equal authenticated V1 cached values.",
                 "Reference trend state is Pine's virtual signal-close path, not a real exchange position.",
                 "Price agreement cannot independently identify the screenshot venue or saved script input settings."])
    path = output / "owner_missed_launches_audit.json"
    path.write_text(json.dumps(clean(result), ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return dict(path=str(path), sha256=sha(path), cases=len(summaries), rows=sum(a["rows"] for a in artifacts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--prior-results", type=Path, default=OLD)
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.prior_results), ensure_ascii=False))


if __name__ == "__main__":
    main()
