"""Read-only V7/V8 cross-venue event folding over frozen SPIKE replay receipts.

Each source confirmation is available only at ``signal_bar_open + timeframe``.
Events use that availability clock, never prices or outcomes.  The first source
confirmation is the leader; only confirmations from a different venue no more
than one whole source timeframe later can confirm it.  Future trade outcomes
are joined after this causal grouping solely for historical descriptive metrics.
This module neither imports execution code nor changes any live surface.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


EXP = Path("experiments/active/exp-spike-cross-venue-events-20260913-v1")
SOURCE = Path("experiments/active/exp-spike-v8-noise-filter-20260913-v1/replay_v1")
ARMS = ("v7", "v8")
INPUT_KINDS = ("signals", "trades", "controls")
REQUIRED_SIGNAL_COLUMNS = {
    "v7", "v8", "side", "signal_bar_open", "period", "stream_key", "venue", "asset", "timeframe_min",
}
TRADE_KEY = ["stream_key", "signal_bar_open", "side", "arm"]


def _as_bool(values: pd.Series, column: str) -> pd.Series:
    """Parse receipt booleans strictly; unknown encodings fail closed."""
    if values.dtype == bool:
        return values.astype(bool)
    normalized = values.astype(str).str.lower()
    valid = normalized.isin({"true", "false"}) | values.isna()
    if not valid.all():
        raise ValueError(f"nonboolean values in {column}")
    return normalized.eq("true")


def source_event_id(frame: pd.DataFrame, arm: str) -> pd.Series:
    """Return a portable exact identity that preserves receipt provenance."""
    stamp = pd.to_datetime(frame["signal_bar_open"], utc=True).astype(str)
    return frame["stream_key"].astype(str) + ":" + arm + ":" + stamp + ":" + frame["side"].astype(int).astype(str)


def prepare_signals(signals: pd.DataFrame, arm: str) -> pd.DataFrame:
    """Select one frozen arm and attach its causal availability time and ID."""
    if arm not in ARMS:
        raise ValueError(f"unsupported arm: {arm}")
    missing = REQUIRED_SIGNAL_COLUMNS - set(signals.columns)
    if missing:
        raise ValueError("signals missing columns: " + ", ".join(sorted(missing)))
    raw = signals.copy()
    raw["signal_bar_open"] = pd.to_datetime(raw.signal_bar_open, utc=True)
    raw["timeframe_min"] = pd.to_numeric(raw.timeframe_min, errors="raise").astype(int)
    raw["side"] = pd.to_numeric(raw.side, errors="raise").astype(int)
    if not raw.side.isin((-1, 1)).all() or not raw.timeframe_min.gt(0).all():
        raise ValueError("signals require side +/-1 and positive timeframe")
    raw["admitted"] = _as_bool(raw[arm], arm)
    out = raw.loc[raw.admitted].copy()
    out["arm"] = arm
    out["availability_time"] = out.signal_bar_open + pd.to_timedelta(out.timeframe_min, unit="min")
    out["month"] = out.availability_time.dt.strftime("%Y-%m")
    out["source_event_id"] = source_event_id(out, arm)
    if out.source_event_id.duplicated().any():
        raise ValueError("duplicate source confirmation identity")
    return out


def observed_coverage(v7_signals: pd.DataFrame) -> pd.DataFrame:
    """Count observed V7-admission venues by asset/timeframe/period/month.

    The receipt collection does not expose month-level empty-stream bar clocks.
    This is intentionally named observed admission coverage rather than claiming
    continuous market-data coverage.
    """
    keys = ["asset", "timeframe_min", "period", "month"]
    return v7_signals.groupby(keys, as_index=False).agg(coverage_venues=("venue", "nunique"))


def collapse_events(source: pd.DataFrame, *, tolerance_bars: int = 1) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fold source confirmations without allowing a transitive two-bar chain.

    Every row is retained in ``members``.  `venue_count` counts each venue once
    within an event, using its earliest source confirmation.  An event boundary
    is measured from its leader, so t, t+1 and t+2 cannot collapse at tolerance
    one even though adjacent rows are one bar apart.
    """
    if tolerance_bars < 0:
        raise ValueError("tolerance_bars must be nonnegative")
    required = {"source_event_id", "asset", "timeframe_min", "side", "venue", "availability_time", "period", "month", "arm"}
    missing = required - set(source.columns)
    if missing:
        raise ValueError("event source missing columns: " + ", ".join(sorted(missing)))
    source = source.copy()
    source["availability_time"] = pd.to_datetime(source.availability_time, utc=True)
    source["timeframe_min"] = pd.to_numeric(source.timeframe_min, errors="raise").astype(int)
    if source.source_event_id.duplicated().any():
        raise ValueError("source event IDs must be unique")
    if source.empty:
        members = source.copy()
        members = members.assign(
            event_id=pd.Series(dtype=str), event_leader_source_event_id=pd.Series(dtype=str), leader_venue=pd.Series(dtype=str),
            leader_availability_time=pd.Series(dtype="datetime64[ns, UTC]"), event_deadline=pd.Series(dtype="datetime64[ns, UTC]"),
            venue_count=pd.Series(dtype=int), is_event_leader=pd.Series(dtype=bool), is_first_for_venue=pd.Series(dtype=bool),
            venue_duplicate_within_event=pd.Series(dtype=bool), multi_venue_confirmed=pd.Series(dtype=bool),
            confirmation_source_event_id=pd.Series(dtype=str), confirmation_venue=pd.Series(dtype=str), confirmation_delay_min=pd.Series(dtype=float),
        )
        return members, pd.DataFrame(columns=[
            "event_id", "arm", "asset", "timeframe_min", "side", "period", "month", "leader_source_event_id",
            "leader_stream_key", "leader_venue", "leader_availability_time", "event_deadline", "source_confirmation_count",
            "venue_count", "multi_venue_confirmed", "confirmation_source_event_id", "confirmation_venue", "confirmation_delay_min",
        ])
    # Store annotations in arrays and assign them once.  The all-source
    # population has many singleton events; appending one DataFrame per event
    # made the read-only study needlessly quadratic at final concatenation.
    members = source.sort_values(["arm", "asset", "timeframe_min", "side", "availability_time", "venue", "stream_key", "source_event_id"]).reset_index(drop=True)
    count = len(members)
    event_ids = np.empty(count, dtype=object)
    leader_ids = np.empty(count, dtype=object)
    leader_venues = np.empty(count, dtype=object)
    leader_times = np.empty(count, dtype=object)
    deadlines = np.empty(count, dtype=object)
    venue_counts = np.zeros(count, dtype=int)
    is_leader = np.zeros(count, dtype=bool)
    is_first_venue = np.zeros(count, dtype=bool)
    confirmed_flags = np.zeros(count, dtype=bool)
    confirmation_ids = np.empty(count, dtype=object); confirmation_ids[:] = pd.NA
    confirmation_venues = np.empty(count, dtype=object); confirmation_venues[:] = pd.NA
    confirmation_delays = np.full(count, math.nan, dtype=float)
    events: list[dict[str, object]] = []
    event_number = 0
    for key, group in members.groupby(["arm", "asset", "timeframe_min", "side"], sort=True):
        times = group.availability_time.astype("int64").to_numpy()
        start = 0
        # Walk each sorted group once.  Repeated boolean filtering of the
        # remaining suffix was quadratic on the all-source population.
        while start < len(group):
            leader = group.iloc[start]
            deadline = leader.availability_time + pd.Timedelta(minutes=int(leader.timeframe_min) * tolerance_bars)
            end = start + 1
            while end < len(group) and times[end] <= deadline.value:
                end += 1
            current = group.iloc[start:end]
            indices = current.index.to_numpy()
            start = end
            event_number += 1
            event_id = f"{key[0]}:{key[1]}:{key[2]}:{key[3]}:{leader.availability_time.isoformat()}:{event_number:06d}"
            first_per_venue = current.sort_values("availability_time").drop_duplicates("venue", keep="first")
            venue_count = int(first_per_venue.venue.nunique())
            follower = first_per_venue.loc[first_per_venue.venue.ne(leader.venue)]
            first_follower = follower.iloc[0] if not follower.empty else None
            confirmed = first_follower is not None
            event_ids[indices] = event_id
            leader_ids[indices] = leader.source_event_id
            leader_venues[indices] = leader.venue
            leader_times[indices] = leader.availability_time
            deadlines[indices] = deadline
            venue_counts[indices] = venue_count
            is_leader[indices] = current.source_event_id.eq(leader.source_event_id).to_numpy()
            is_first_venue[indices] = current.source_event_id.isin(first_per_venue.source_event_id).to_numpy()
            confirmed_flags[indices] = confirmed
            if confirmed:
                confirmation_ids[indices] = first_follower.source_event_id
                confirmation_venues[indices] = first_follower.venue
                confirmation_delays[indices] = (first_follower.availability_time - leader.availability_time).total_seconds() / 60
            events.append({
                "event_id": event_id, "arm": key[0], "asset": key[1], "timeframe_min": key[2], "side": key[3],
                "period": leader.period, "month": leader.month, "leader_source_event_id": leader.source_event_id,
                "leader_stream_key": leader.stream_key, "leader_venue": leader.venue,
                "leader_availability_time": leader.availability_time, "event_deadline": deadline,
                "source_confirmation_count": len(current), "venue_count": venue_count,
                "multi_venue_confirmed": confirmed,
                "confirmation_source_event_id": first_follower.source_event_id if confirmed else pd.NA,
                "confirmation_venue": first_follower.venue if confirmed else pd.NA,
                "confirmation_delay_min": (first_follower.availability_time - leader.availability_time).total_seconds() / 60 if confirmed else math.nan,
            })
    members["event_id"] = event_ids
    members["event_leader_source_event_id"] = leader_ids
    members["leader_venue"] = leader_venues
    members["leader_availability_time"] = pd.to_datetime(leader_times, utc=True)
    members["event_deadline"] = pd.to_datetime(deadlines, utc=True)
    members["venue_count"] = venue_counts
    members["is_event_leader"] = is_leader
    members["is_first_for_venue"] = is_first_venue
    members["venue_duplicate_within_event"] = ~is_first_venue
    members["multi_venue_confirmed"] = confirmed_flags
    members["confirmation_source_event_id"] = confirmation_ids
    members["confirmation_venue"] = confirmation_venues
    members["confirmation_delay_min"] = confirmation_delays
    member_frame = members
    event_frame = pd.DataFrame(events)
    if len(member_frame) != len(source):
        raise AssertionError("event folding changed source confirmation count")
    return member_frame, event_frame


def attach_outcomes(
    members: pd.DataFrame, trades: pd.DataFrame, arm: str, *, known_source: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Join exact serial-replay results, rejecting ambiguous or unknown mappings."""
    if arm not in ARMS:
        raise ValueError(f"unsupported arm: {arm}")
    required = set(TRADE_KEY) | {"trade_id", "censored", "net_r", "net_return", "mfe_r"}
    missing = required - set(trades.columns)
    if missing:
        raise ValueError("trades missing columns: " + ", ".join(sorted(missing)))
    left = members.loc[members.arm.eq(arm)].copy()
    right = trades.loc[trades.arm.eq(arm)].copy()
    for frame in (left, right):
        frame["signal_bar_open"] = pd.to_datetime(frame.signal_bar_open, utc=True)
        frame["side"] = pd.to_numeric(frame.side, errors="raise").astype(int)
    if right.duplicated(TRADE_KEY).any():
        raise ValueError("ambiguous duplicate trade mapping")
    # `members` can be a coverage-selected subset.  Validate a full arm source
    # when supplied, so natural trades excluded by the coverage rule are not
    # falsely called unknown while genuinely foreign trade rows still fail.
    known = left if known_source is None else known_source.loc[known_source.arm.eq(arm)].copy()
    known["signal_bar_open"] = pd.to_datetime(known.signal_bar_open, utc=True)
    known["side"] = pd.to_numeric(known.side, errors="raise").astype(int)
    source_keys = set(map(tuple, known[TRADE_KEY].itertuples(index=False, name=None)))
    unknown = right.loc[~right[TRADE_KEY].apply(tuple, axis=1).isin(source_keys)]
    if not unknown.empty:
        raise ValueError("trade mapping contains unknown source confirmation")
    columns = TRADE_KEY + ["trade_id", "censored", "net_r", "net_return", "mfe_r"]
    joined = left.merge(right[columns], on=TRADE_KEY, how="left", validate="one_to_one")
    joined["executed"] = joined.trade_id.notna()
    joined["occupied_or_unmapped"] = ~joined.executed
    joined["censored"] = _as_bool(joined["censored"], "censored")
    joined["closed"] = joined.executed & ~joined.censored
    return joined


def metric_summary(joined: pd.DataFrame) -> pd.DataFrame:
    """Summarize source rows by arm, sample, period, timeframe and direction."""
    rows: list[dict[str, object]] = []
    scopes = {
        "baseline": pd.Series(True, index=joined.index),
        "multi_venue_confirmed": joined.multi_venue_confirmed.fillna(False).astype(bool),
        "event_leaders": joined.is_event_leader.fillna(False).astype(bool),
        "confirmed_event_leaders": joined.is_event_leader.fillna(False).astype(bool) & joined.multi_venue_confirmed.fillna(False).astype(bool),
    }
    groups = ["arm", "period", "timeframe_min", "side"]
    for scope, mask in scopes.items():
        for key, group in joined.loc[mask].groupby(groups, dropna=False):
            closed = group.loc[group.closed]
            gains = float(closed.net_return.clip(lower=0).sum())
            losses = float(-closed.net_return.clip(upper=0).sum())
            rows.append(dict(zip(groups, key), sample=scope, source_signals=len(group), market_events=group.event_id.nunique(),
                             executed=int(group.executed.sum()), occupied_or_unmapped=int(group.occupied_or_unmapped.sum()),
                             censored=int(group.censored.sum()), closed=len(closed), wins=int(closed.net_return.gt(0).sum()),
                             win_rate=float(closed.net_return.gt(0).mean()) if len(closed) else math.nan,
                             pf=gains / losses if losses > 0 else math.nan,
                             mean_net_r=float(closed.net_r.mean()) if len(closed) else math.nan,
                             realized_10r=int(closed.net_r.ge(10).sum()),
                             realized_10r_rate=float(closed.net_r.ge(10).mean()) if len(closed) else math.nan,
                             mfe_10r=int(closed.mfe_r.ge(10).sum())))
    columns = groups + ["sample", "source_signals", "market_events", "executed", "occupied_or_unmapped", "censored", "closed", "wins", "win_rate", "pf", "mean_net_r", "realized_10r", "realized_10r_rate", "mfe_10r"]
    return pd.DataFrame(rows, columns=columns).sort_values(groups + ["sample"]).reset_index(drop=True)


def control_summary(controls: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    """Keep frozen same-stream/month/prior-vol controls as a paired null check."""
    if controls.empty:
        return pd.DataFrame(columns=["arm", "period", "timeframe_min", "side", "sample", "sampled", "matched", "mean_net_r_difference", "mean_net_return_difference"])
    keys = ["stream_key", "signal_bar_open", "side", "arm"]
    required = set(keys) | {"matched", "net_r_difference", "net_return_difference"}
    missing = required - set(controls.columns)
    if missing:
        raise ValueError("controls missing columns: " + ", ".join(sorted(missing)))
    controls = controls.copy()
    controls.signal_bar_open = pd.to_datetime(controls.signal_bar_open, utc=True)
    controls.side = pd.to_numeric(controls.side, errors="raise").astype(int)
    controls.matched = _as_bool(controls.matched, "matched")
    selected_ids = selected[["source_event_id", "arm", "period", "timeframe_min", "side", "stream_key", "signal_bar_open", "multi_venue_confirmed"]].drop_duplicates()
    selected_ids = selected_ids.loc[selected_ids.multi_venue_confirmed].copy()
    # Period is an explicit source-receipt field.  Joining it prevents a reused
    # timestamp identity from drifting across the frozen development boundary.
    paired = controls.merge(
        selected_ids.drop(columns="multi_venue_confirmed"), on=keys + ["period"], how="inner", validate="one_to_one",
    )
    rows = []
    for key, group in paired.groupby(["arm", "period", "timeframe_min", "side"], dropna=False):
        matched = group.loc[group.matched]
        rows.append(dict(arm=key[0], period=key[1], timeframe_min=key[2], side=key[3], sample="multi_venue_confirmed",
                         sampled=len(group), matched=len(matched), unmatched=len(group)-len(matched),
                         mean_net_r_difference=float(pd.to_numeric(matched.net_r_difference, errors="coerce").mean()) if len(matched) else math.nan,
                         mean_net_return_difference=float(pd.to_numeric(matched.net_return_difference, errors="coerce").mean()) if len(matched) else math.nan))
    return pd.DataFrame(rows)


def _sha256_bytes(payload: bytes) -> str:
    """Return the identity digest for an exact compressed receipt payload."""
    return hashlib.sha256(payload).hexdigest()


def _receipt_entry(path: Path, source: Path) -> dict[str, object]:
    """Describe one compressed receipt using its source-relative stable identity."""
    payload = path.read_bytes()
    return {
        "relative_path": path.relative_to(source).as_posix(),
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
    }


def _receipt_aggregate(receipts: list[dict[str, object]]) -> str:
    """Hash the ordered receipt identities so count-preserving edits cannot hide."""
    canonical = json.dumps(receipts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def input_receipt_paths(streams: Path, *, stream_limit: int | None) -> list[tuple[Path, str]]:
    """Select every parsed receipt in stable source-relative path order."""
    selected: list[tuple[Path, str]] = []
    for kind in INPUT_KINDS:
        paths = sorted(streams.glob(f"*.{kind}.csv.gz"))
        if stream_limit is not None:
            paths = paths[:stream_limit]
        selected.extend((path, kind) for path in paths)
    return sorted(selected, key=lambda item: item[0].relative_to(streams.parent).as_posix())


def inventory_input_receipts(receipts: list[tuple[Path, str]], source: Path) -> list[dict[str, object]]:
    """Read and identity-record every selected receipt before CSV parsing begins."""
    return [_receipt_entry(path, source) for path, _ in receipts]


def verify_input_receipts(
    actual: list[dict[str, object]], config: dict[str, object], *, stream_limit: int | None,
) -> dict[str, object]:
    """Fail closed unless each parsed receipt matches the pinned identity manifest.

    A limited run validates its selected subset row-for-row.  The formal full
    run additionally requires the complete ordered list and aggregate digest.
    """
    manifest_path = EXP / str(config["input_receipt_manifest"])
    manifest_payload = manifest_path.read_bytes()
    manifest_sha256 = _sha256_bytes(manifest_payload)
    if manifest_sha256 != config["input_receipt_manifest_sha256"]:
        raise ValueError("pinned input receipt manifest does not match preregistration")
    pinned = json.loads(manifest_payload)
    expected = pinned.get("receipts")
    if not isinstance(expected, list):
        raise ValueError("pinned input receipt manifest has no receipt list")
    if int(pinned.get("receipt_count", -1)) != len(expected):
        raise ValueError("pinned input receipt manifest count is invalid")
    expected_aggregate = _receipt_aggregate(expected)
    if pinned.get("aggregate_sha256") != expected_aggregate:
        raise ValueError("pinned input receipt manifest aggregate is invalid")
    if expected_aggregate != config["input_receipt_aggregate_sha256"]:
        raise ValueError("pinned input receipt aggregate does not match preregistration")
    if int(config["expected_input_receipts"]) != len(expected):
        raise ValueError("pinned input receipt count does not match preregistration")
    expected_by_path = {entry["relative_path"]: entry for entry in expected}
    actual_paths = [entry["relative_path"] for entry in actual]
    if len(actual_paths) != len(set(actual_paths)):
        raise ValueError("duplicate input receipt path selected")
    if any(expected_by_path.get(entry["relative_path"]) != entry for entry in actual):
        raise ValueError("input receipt identity differs from pinned manifest")
    actual_aggregate = _receipt_aggregate(actual)
    if stream_limit is None:
        if actual != expected or actual_aggregate != expected_aggregate:
            raise ValueError("complete input receipt inventory differs from pinned manifest")
    return {
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "receipt_count": len(actual),
        "aggregate_sha256": actual_aggregate,
    }


def _read_kind(
    receipts: list[tuple[Path, str]], kind: str, source: Path, verified: list[dict[str, object]],
) -> pd.DataFrame:
    """Parse only a payload whose bytes still match its pre-parse receipt identity."""
    verified_by_path = {entry["relative_path"]: entry for entry in verified}
    frames = []
    for path, receipt_kind in receipts:
        if receipt_kind != kind:
            continue
        # Re-read and check the exact bytes handed to pandas, closing the
        # check-then-use gap after the whole inventory was verified above.
        payload = path.read_bytes()
        entry = {
            "relative_path": path.relative_to(source).as_posix(),
            "bytes": len(payload),
            "sha256": _sha256_bytes(payload),
        }
        if entry != verified_by_path.get(entry["relative_path"]):
            raise ValueError("input receipt changed after identity verification")
        frame = pd.read_csv(io.BytesIO(payload), compression="gzip")
        if kind == "controls":
            frame["stream_key"] = path.name.removesuffix(".controls.csv.gz")
        if not frame.empty:
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def run(source: Path, output: Path, *, stream_limit: int | None = None) -> dict[str, int]:
    """Materialize a receipt-bound analysis; source replay files are never written."""
    config_path = EXP / "config.json"
    config = json.loads(config_path.read_text())
    manifest = json.loads((source / "manifest.json").read_text())
    manifest_digest = hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()
    if manifest_digest != config["source_manifest_sha256"]:
        raise ValueError("source replay manifest does not match preregistration")
    if not manifest.get("complete") or int(manifest.get("streams", -1)) != int(config["expected_streams"]):
        raise ValueError("source replay is not the complete expected stream population")
    streams = source / "streams"
    receipt_paths = input_receipt_paths(streams, stream_limit=stream_limit)
    receipt_inventory = inventory_input_receipts(receipt_paths, source)
    receipt_verification = verify_input_receipts(receipt_inventory, config, stream_limit=stream_limit)
    signals = _read_kind(receipt_paths, "signals", source, receipt_inventory)
    trades = _read_kind(receipt_paths, "trades", source, receipt_inventory)
    controls = _read_kind(receipt_paths, "controls", source, receipt_inventory)
    if stream_limit is None and int(_as_bool(signals.v7, "v7").sum()) != int(config["expected_v7_signals"]):
        raise ValueError("source V7 admission count disagrees with frozen contract")
    output.mkdir(parents=True, exist_ok=True)
    v7 = prepare_signals(signals, "v7")
    coverage = observed_coverage(v7)
    source_outputs: list[pd.DataFrame] = []
    all_event_outputs: list[pd.DataFrame] = []
    coverage_event_outputs: list[pd.DataFrame] = []
    all_joined: list[pd.DataFrame] = []
    coverage_joined_outputs: list[pd.DataFrame] = []
    for arm in ARMS:
        source_arm = prepare_signals(signals, arm)
        source_arm = source_arm.merge(coverage, on=["asset", "timeframe_min", "period", "month"], how="left", validate="many_to_one")
        source_arm["coverage_eligible"] = source_arm.coverage_venues.ge(2)
        # This full arm-level ledger preserves every frozen admission's exact
        # source ID, stream and timestamp.  Its coverage field is deliberately
        # not a main-event input: a month-level admission count can see later
        # confirmations and is therefore only a retrospective sensitivity tag.
        source_outputs.append(source_arm)
        members, events = collapse_events(source_arm, tolerance_bars=int(config["event_tolerance_bars"]))
        all_joined.append(attach_outcomes(members, trades, arm, known_source=source_arm))
        all_event_outputs.append(events)
        coverage_members, coverage_events = collapse_events(
            source_arm.loc[source_arm.coverage_eligible], tolerance_bars=int(config["event_tolerance_bars"]),
        )
        coverage_joined_outputs.append(attach_outcomes(coverage_members, trades, arm, known_source=source_arm))
        coverage_event_outputs.append(coverage_events)
    source_admissions = pd.concat(source_outputs, ignore_index=True)
    joined = pd.concat(all_joined, ignore_index=True)
    events = pd.concat(all_event_outputs, ignore_index=True)
    coverage_joined = pd.concat(coverage_joined_outputs, ignore_index=True)
    coverage_events = pd.concat(coverage_event_outputs, ignore_index=True)
    if len(joined) != len(source_admissions):
        raise AssertionError("source count changed after arm aggregation")
    metrics = metric_summary(joined)
    coverage_metrics = metric_summary(coverage_joined)
    controls_out = control_summary(controls, joined)
    delays = events.loc[events.multi_venue_confirmed].groupby(["arm", "period", "timeframe_min", "side", "confirmation_delay_min"], as_index=False).size().rename(columns={"size": "events"})
    venue_dist = events.groupby(["arm", "period", "timeframe_min", "side", "venue_count"], as_index=False).size().rename(columns={"size": "events"})
    folding = events.groupby(["arm", "period", "timeframe_min", "side"], as_index=False).agg(
        original_source_confirmations=("source_confirmation_count", "sum"), folded_market_events=("event_id", "size"),
        multi_venue_confirmed_events=("multi_venue_confirmed", "sum"))
    coverage_folding = coverage_events.groupby(["arm", "period", "timeframe_min", "side"], as_index=False).agg(
        original_source_confirmations=("source_confirmation_count", "sum"), folded_market_events=("event_id", "size"),
        multi_venue_confirmed_events=("multi_venue_confirmed", "sum"))
    for name, frame in {
        "source_admissions.csv.gz": source_admissions, "event_members.csv.gz": joined, "events.csv.gz": events, "metrics.csv": metrics,
        "venue_count_distribution.csv": venue_dist, "leader_follower_delays.csv": delays,
        "folding_summary.csv": folding, "matched_controls.csv": controls_out,
        "observed_coverage_event_members.csv.gz": coverage_joined, "observed_coverage_events.csv.gz": coverage_events,
        "observed_coverage_metrics.csv": coverage_metrics, "observed_coverage_folding_summary.csv": coverage_folding,
    }.items():
        frame.to_csv(output / name, index=False, compression="gzip" if name.endswith(".gz") else None)
    receipt = {
        "source": str(source), "source_manifest_sha256": manifest_digest,
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input_receipt_manifest": receipt_verification["manifest"],
        "input_receipt_manifest_sha256": receipt_verification["manifest_sha256"],
        "input_receipt_count": receipt_verification["receipt_count"],
        "input_receipt_aggregate_sha256": receipt_verification["aggregate_sha256"],
        "stream_limit": stream_limit, "input_signal_rows": len(signals), "input_v7_signals": int(_as_bool(signals.v7, "v7").sum()),
        "source_admission_rows": len(source_admissions), "all_source_event_rows": len(joined),
        "observed_coverage_sensitivity_rows": len(coverage_joined), "folded_events": len(events),
        "multi_venue_events": int(events.multi_venue_confirmed.sum()),
        "research_only": True, "nonblind_reused_history": True,
    }
    (output / "run_manifest.json").write_text(json.dumps(receipt, indent=2, default=str) + "\n")
    return {key: int(value) for key, value in receipt.items() if isinstance(value, (int, np.integer))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=EXP / "results")
    parser.add_argument("--stream-limit", type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output, stream_limit=args.stream_limit), indent=2))
