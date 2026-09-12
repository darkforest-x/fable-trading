"""Causal development-only SPIKE market-breadth accounting.

The study binds V1/V7 common-execution outcomes to an independently built
30-minute market panel.  Every panel value is available at the end of its
30-minute bar: close-to-close participation, SMA20/EMA20 breadth, joint
breadth, 30/60-minute changes, one-hour V1/V6 launch density, relative-volume
and TR/ATR co-expansion, and cross-asset return dispersion.  The frozen input
contains 30-minute candles only, so a 15-minute acceleration is deliberately
recorded as unavailable rather than inferred from future or synthetic bars.

No signal, execution, threshold, production setting, or source receipt is
changed.  ``--run`` only reads the frozen V1/V7 comparison artifacts through
2025-09-10T00:00:00Z and writes a new development research directory.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SOURCE_EXP = ROOT / "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1"
COMPARE_EXP = ROOT / "experiments/active/exp-spike-v7-v1-compare-20260912-v1"
DEFAULT_OUT = ROOT / "experiments/active/exp-spike-market-breadth-20260913-v1"
DEFAULT_REPORT = ROOT / "analysis/p1_spike_market_breadth_20260913.md"
DEVELOPMENT_START = pd.Timestamp("2024-09-10T00:00:00Z")
DEVELOPMENT_END = pd.Timestamp("2025-09-10T00:00:00Z")
VENUE_ORDER = {"binance": 0, "okx": 1, "gate": 2}
VARIANTS = ("v1_common_execution_long", "v7_bb_long")
METRICS = (
    "up_participation", "fast_breadth", "joint_breadth", "joint_delta_30m",
    "joint_delta_60m", "launch_density_1h", "rv_tr_atr_expansion", "return_median_30m",
    "return_iqr_30m", "btc_return_30m", "eth_return_30m",
)


def sha256(path: Path) -> str:
    """Return a small artifact's complete byte identity.

    This helper is deliberately never used for a normalized candle stream:
    those files extend beyond the development boundary. Their upstream full
    identities are receipt metadata, while this study records an independently
    streamed development-prefix digest below.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_asset(asset: str) -> str:
    """Normalize exchange denomination wrappers before breadth/count de-duplication.

    The only transformation is the documented exchange contract multiplier
    prefixes (for example ``1000BONK`` and ``1000000MOG``).  No ticker aliases
    are guessed, so an unknown symbol cannot silently merge with another coin.
    """
    value = str(asset).upper().strip()
    for prefix in ("1000000", "1000"):
        if value.startswith(prefix) and len(value) > len(prefix):
            return value[len(prefix):]
    return value


def causal_asset_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute bar-close-only breadth inputs from OHLCV and preceding windows.

    Required columns are ``open, high, low, close, volume``.  SMA/EMA use the
    current close and previous 19 closes; relative volume uses the preceding
    20 completed bars; true range uses the current OHLC and prior close; ATR20
    is the preceding 20 true ranges.  Thus no field reads after its row.
    """
    needed = {"open", "high", "low", "close", "volume"}
    if missing := needed.difference(bars.columns):
        raise ValueError("missing OHLCV columns: " + ",".join(sorted(missing)))
    if bars.empty:
        return pd.DataFrame(columns=["up", "fast", "joint", "rv_tr_atr", "return_30m"], index=bars.index)
    source = bars[["open", "high", "low", "close", "volume"]].astype(float)
    boundary = source.index.to_series().diff().ne(pd.Timedelta(minutes=30)).cumsum()
    parts = []
    for _, frame in source.groupby(boundary, sort=False):
        close, high, low, volume = frame.close, frame.high, frame.low, frame.volume
        previous = close.shift(1)
        tr = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1)
        sma = close.rolling(20, min_periods=20).mean()
        ema = close.ewm(span=20, adjust=False, min_periods=20).mean()
        # RV follows the frozen signal feature convention: current volume over
        # the median of the preceding twenty completed bars, never their mean.
        rv = volume / volume.shift(1).rolling(20, min_periods=20).median()
        atr = tr.shift(1).rolling(20, min_periods=20).mean()
        out = pd.DataFrame(index=frame.index)
        out["up"] = close.gt(previous).where(previous.notna())
        out["fast"] = (close.gt(sma) & close.gt(ema)).where(sma.notna() & ema.notna())
        out["joint"] = (out["up"].eq(True) & out["fast"].eq(True)).where(out["fast"].notna())
        out["rv_tr_atr"] = (rv.gt(1.0) & (tr / atr).gt(1.0)).where(rv.notna() & atr.notna())
        out["return_30m"] = close.pct_change()
        parts.append(out)
    return pd.concat(parts).reindex(bars.index)


def _source_catalog() -> pd.DataFrame:
    """Select one evaluated 30-minute source per base asset, deterministically."""
    coverage = pd.read_csv(SOURCE_EXP / "results/coverage_limited.csv")
    # Gate direct files exist, but every 30m receipt is partial and none covers
    # development. It is excluded explicitly, never filled from a live source.
    selected = coverage.loc[(coverage.status == "evaluated") & coverage.timeframe_min.eq(30)
                            & coverage.venue.isin(["binance", "okx"])].copy()
    selected["base_asset"] = selected.asset.map(canonical_asset)
    selected["venue_rank"] = selected.venue.map(VENUE_ORDER)
    if selected.venue_rank.isna().any():
        raise ValueError("unknown venue in frozen coverage")
    selected = selected.sort_values(["base_asset", "venue_rank", "symbol"], kind="stable")
    selected = selected.drop_duplicates("base_asset", keep="first")
    selected["source_path"] = [
        str(SOURCE_EXP / "data/normalized" / row.venue / f"{row.symbol}_30m.csv.gz")
        for row in selected.itertuples(index=False)
    ]
    if not selected.source_path.map(lambda raw: Path(raw).is_file()).all():
        raise FileNotFoundError("selected frozen normalized candle source is missing")
    manifest = json.loads((COMPARE_EXP / "results/replay_two_year_20260912_v3/input_manifest.json").read_text())
    expected = {(str(Path(item["source_path"]).resolve()), int(item["minutes"])): item["source_sha256"]
                for item in manifest["streams"]}
    selected["frozen_upstream_expected_full_source_sha256"] = [
        expected.get((str(Path(raw).resolve()), 30)) for raw in selected.source_path
    ]
    if selected.frozen_upstream_expected_full_source_sha256.isna().any():
        raise ValueError("selected breadth source absent from frozen input manifest")
    return selected[[
        "venue", "symbol", "asset", "base_asset", "source_path",
        "frozen_upstream_expected_full_source_sha256",
    ]].reset_index(drop=True)


def _load_bars(path: str, *, prefix_digest: hashlib._Hash | None = None) -> pd.DataFrame:
    """Load only development OHLCV rows and optionally hash that exact prefix."""
    rows = []
    for header, values in _prefix_rows(
        Path(path), cutoff_field="time", cutoff=DEVELOPMENT_END, prefix_digest=prefix_digest,
    ):
        row = dict(zip(header, values))
        rows.append({name: row[name] for name in ("time", "open", "high", "low", "close", "volume")})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([], tz="UTC", name="time"))
    frame["time"] = pd.to_datetime(frame.time, utc=True)
    indexed = frame.set_index("time").sort_index()
    if indexed.index.duplicated().any():
        duplicates = indexed.loc[indexed.index.duplicated(keep=False)]
        for _, group in duplicates.groupby(level=0, sort=False):
            if len(group.drop_duplicates()) != 1:
                raise ValueError(f"conflicting duplicate candle in frozen source: {path}")
        indexed = indexed.loc[~indexed.index.duplicated(keep="first")]
    return indexed


def build_market_panel(catalog: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """Build an as-of-close panel and digest each exact development input prefix."""
    raw_start = DEVELOPMENT_START - pd.Timedelta(hours=12)
    raw_end = DEVELOPMENT_END - pd.Timedelta(minutes=30)
    clock = pd.date_range(raw_start, raw_end, freq="30min", tz="UTC")
    count = {name: np.zeros(len(clock), dtype=np.int32) for name in ("up", "fast", "joint", "rv_tr_atr", "returns")}
    total = {name: np.zeros(len(clock), dtype=np.int32) for name in ("up", "fast", "joint", "rv_tr_atr", "returns")}
    return_values: dict[int, list[float]] = defaultdict(list)
    prefix_digests: dict[str, str] = {}
    for number, row in enumerate(catalog.itertuples(index=False), 1):
        digest = hashlib.sha256()
        features = causal_asset_features(_load_bars(row.source_path, prefix_digest=digest))
        prefix_digests[row.source_path] = digest.hexdigest()
        features = features.reindex(clock)
        for name in ("up", "fast", "joint", "rv_tr_atr"):
            valid = features[name].notna().to_numpy()
            total[name] += valid
            count[name] += features[name].eq(True).to_numpy(bool)
        values = features.return_30m.to_numpy(float)
        valid_return = np.isfinite(values)
        total["returns"] += valid_return
        count["returns"] += valid_return
        for index in np.flatnonzero(valid_return):
            return_values[int(index)].append(float(values[index]))
        if number % 100 == 0:
            print(json.dumps({"breadth_sources_read": number, "breadth_sources_total": len(catalog)}), flush=True)
    panel = pd.DataFrame({"bar_open": clock, "asof": clock + pd.Timedelta(minutes=30)})
    for feature, label in (("up", "up_participation"), ("fast", "fast_breadth"),
                           ("joint", "joint_breadth"), ("rv_tr_atr", "rv_tr_atr_expansion")):
        denominator = total[feature].astype(float)
        panel[f"eligible_{feature}"] = total[feature]
        panel[label] = np.divide(count[feature], denominator, out=np.full(len(clock), np.nan), where=denominator > 0)
    panel["eligible_returns"] = total["returns"]
    panel["return_median_30m"] = [np.median(return_values.get(i, [np.nan])) for i in range(len(clock))]
    panel["return_iqr_30m"] = [np.subtract(*np.percentile(return_values[i], [75, 25])) if i in return_values else np.nan
                                for i in range(len(clock))]
    panel["joint_delta_15m"] = np.nan
    panel["joint_delta_30m"] = panel.joint_breadth.diff(1)
    panel["joint_delta_60m"] = panel.joint_breadth.diff(2)
    panel["joint_denominator_delta_30m"] = panel.eligible_joint.diff(1)
    panel["joint_denominator_delta_60m"] = panel.eligible_joint.diff(2)
    panel["joint_denominator_changed_30m"] = panel.joint_denominator_delta_30m.ne(0)
    panel["joint_denominator_changed_60m"] = panel.joint_denominator_delta_60m.ne(0)
    panel["joint_acceleration_descriptive"] = panel.joint_denominator_changed_30m | panel.joint_denominator_changed_60m
    return (panel.loc[panel["asof"].between(DEVELOPMENT_START, DEVELOPMENT_END, inclusive="left")]
            .reset_index(drop=True), prefix_digests)


def development_asof_clock() -> pd.Series:
    """Return the exact development as-of clock used by the final breadth panel."""
    return pd.Series(pd.date_range(
        DEVELOPMENT_START, DEVELOPMENT_END - pd.Timedelta(minutes=30), freq="30min", tz="UTC",
    ))


def _prefix_field(line: str, field_index: int) -> str:
    """Read one unquoted field without constructing later CSV outcome fields.

    Frozen stream ledgers use plain scalar fields.  The parser stops at the
    requested comma, which is the critical property for the cutoff row: no
    exit, return, R or other post-cutoff outcome field is constructed.
    """
    start = 0
    for _ in range(field_index):
        stop = line.find(",", start)
        if stop < 0:
            raise ValueError("malformed frozen CSV row before requested field")
        start = stop + 1
    stop = line.find(",", start)
    return line[start:] if stop < 0 else line[start:stop]


def _utc(value: str) -> pd.Timestamp:
    """Parse an ISO timestamp while accepting the archive's explicit UTC offset."""
    return pd.to_datetime(value, utc=True)


def _prefix_rows(path: Path, *, cutoff_field: str, cutoff: pd.Timestamp,
                 prefix_digest: hashlib._Hash | None = None) -> Iterable[tuple[list[str], list[str]]]:
    """Yield only chronological CSV rows strictly before cutoff.

    At the first boundary row this reads and decodes only ``cutoff_field`` then
    terminates. It does not materialize or parse the later payload columns of
    that row. Gzip may still read ahead internally, so this establishes a
    logical-record boundary rather than a physical compressed-byte boundary.

    If supplied, ``prefix_digest`` receives the uncompressed header and every
    complete pre-cutoff CSV record actually yielded to the caller. It excludes
    the boundary row and all later records.
    """
    with gzip.open(path, "rb") as stream:
        header_bytes = stream.readline()
        header = next(csv.reader([header_bytes.decode("utf-8").rstrip("\r\n")]))
        if cutoff_field not in header:
            raise ValueError(f"frozen stream lacks cutoff field {cutoff_field}: {path}")
        if prefix_digest is not None:
            prefix_digest.update(header_bytes)
        index = header.index(cutoff_field)
        previous: pd.Timestamp | None = None
        while True:
            line = bytearray()
            field = bytearray()
            field_number = 0
            value: pd.Timestamp | None = None
            while True:
                character = stream.read(1)
                if not character:
                    if not line:
                        return
                    delimiter = b"\n"
                else:
                    delimiter = character
                    line.extend(character)
                if field_number == index and delimiter not in (b",", b"\n"):
                    field.extend(delimiter)
                if field_number == index and delimiter in (b",", b"\n"):
                    value = _utc(field.decode("utf-8").rstrip("\r"))
                    if previous is not None and value < previous:
                        raise ValueError(f"non-monotonic {cutoff_field} prevents safe prefix read: {path}")
                    previous = value
                    if value >= cutoff:
                        return
                if delimiter == b",":
                    field_number += 1
                if delimiter == b"\n" or not character:
                    break
            if value is None:
                raise ValueError(f"malformed frozen CSV row before requested field: {path}")
            raw_line = bytes(line)
            if prefix_digest is not None:
                prefix_digest.update(raw_line)
            yield header, next(csv.reader([raw_line.decode("utf-8").rstrip("\r\n")]))


def _variant_block_rows(path: Path, *, monotonic_field: str, bounded_fields: tuple[str, ...],
                        cutoff: pd.Timestamp) -> Iterable[tuple[list[str], list[str]]]:
    """Yield rows whose bounded fields precede ``cutoff`` in variant blocks.

    The comparison ledgers are concatenated by variant: their timestamps are
    increasing *within* a block, but a later variant starts again in the
    development period.  This reader therefore scans through EOF, resets the
    monotonic check when ``variant`` changes, and rejects a variant that
    reappears after its block ended.  It decodes only ``variant`` and the
    requested timestamp scalars before deciding whether a row is eligible.

    A row with any bounded timestamp at or after the cutoff is discarded
    without buffering its remaining payload or giving it to ``csv.reader``.
    Gzip may physically prefetch compressed bytes; this is a logical-record
    boundary only.
    """
    with gzip.open(path, "rb") as stream:
        header_bytes = stream.readline()
        header = next(csv.reader([header_bytes.decode("utf-8").rstrip("\r\n")]))
        required = {"variant", monotonic_field, *bounded_fields}
        if missing := required.difference(header):
            raise ValueError(f"frozen stream lacks required fields {sorted(missing)}: {path}")
        indices = {name: header.index(name) for name in required}
        scalar_indices = set(indices.values())
        current_variant: str | None = None
        closed_variants: set[str] = set()
        previous: pd.Timestamp | None = None
        while True:
            raw_line = bytearray()
            scalars: dict[str, bytes] = {}
            field = bytearray()
            field_number = 0
            include: bool | None = None
            has_char = False
            while True:
                character = stream.read(1)
                if not character:
                    if not has_char:
                        return
                    delimiter = b"\n"
                else:
                    has_char = True
                    delimiter = character
                    if include is not False:
                        raw_line.extend(character)
                if field_number in scalar_indices and delimiter not in (b",", b"\n"):
                    field.extend(delimiter)
                if field_number in scalar_indices and delimiter in (b",", b"\n"):
                    for name, index in indices.items():
                        if index == field_number:
                            scalars[name] = bytes(field)
                            break
                if delimiter == b",":
                    field_number += 1
                    field.clear()
                if include is None and len(scalars) == len(indices):
                    variant = scalars["variant"].decode("utf-8")
                    if not variant:
                        raise ValueError(f"empty variant prevents safe block read: {path}")
                    if variant != current_variant:
                        if current_variant is not None:
                            closed_variants.add(current_variant)
                        if variant in closed_variants:
                            raise ValueError(f"variant block reappears after ending: {variant} in {path}")
                        current_variant, previous = variant, None
                    stamp = _utc(scalars[monotonic_field].decode("utf-8").rstrip("\r"))
                    if pd.isna(stamp):
                        raise ValueError(f"missing {monotonic_field} prevents safe block read: {path}")
                    if previous is not None and stamp < previous:
                        raise ValueError(
                            f"non-monotonic {monotonic_field} within variant {variant} prevents safe read: {path}"
                        )
                    previous = stamp
                    bounded = [_utc(scalars[name].decode("utf-8").rstrip("\r")) for name in bounded_fields]
                    if any(pd.isna(value) for value in bounded):
                        raise ValueError(f"missing bounded timestamp prevents safe block read: {path}")
                    include = all(value < cutoff for value in bounded)
                    if not include:
                        raw_line.clear()
                if delimiter == b"\n" or not character:
                    break
            if include is None:
                raise ValueError(f"malformed frozen CSV row before required scalars: {path}")
            if include:
                yield header, next(csv.reader([bytes(raw_line).decode("utf-8").rstrip("\r\n")]))


def _variant_block_safe_record_numbers(path: Path, *, monotonic_field: str, bounded_fields: tuple[str, ...],
                                       start: pd.Timestamp, cutoff: pd.Timestamp) -> tuple[list[str], set[int]]:
    """Return development-safe record numbers without retaining non-scalar bytes.

    This is the first pass for trade ledgers whose outcome fields can precede
    ``exit_time``.  It retains only the variant and bounded timestamp scalars,
    validates contiguous variant blocks through EOF, and records rows with all
    bounded timestamps in ``[start, cutoff)``.  No other payload bytes are
    buffered, decoded, or passed to a CSV parser.
    """
    with gzip.open(path, "rb") as stream:
        header_bytes = stream.readline()
        header = next(csv.reader([header_bytes.decode("utf-8").rstrip("\r\n")]))
        required = {"variant", monotonic_field, *bounded_fields}
        if missing := required.difference(header):
            raise ValueError(f"frozen stream lacks required fields {sorted(missing)}: {path}")
        indices = {name: header.index(name) for name in required}
        fields_by_index = {index: name for name, index in indices.items()}
        current_variant: str | None = None
        closed_variants: set[str] = set()
        previous: pd.Timestamp | None = None
        selected: set[int] = set()
        record_number = 0
        while True:
            scalars: dict[str, bytes] = {}
            field = bytearray()
            field_number = 0
            has_char = False
            while True:
                character = stream.read(1)
                if not character:
                    if not has_char:
                        return header, selected
                    delimiter = b"\n"
                else:
                    has_char = True
                    delimiter = character
                if field_number in fields_by_index and delimiter not in (b",", b"\n"):
                    field.extend(delimiter)
                if field_number in fields_by_index and delimiter in (b",", b"\n"):
                    scalars[fields_by_index[field_number]] = bytes(field)
                if delimiter == b",":
                    field_number += 1
                    field.clear()
                if delimiter == b"\n" or not character:
                    break
            if len(scalars) != len(indices):
                raise ValueError(f"malformed frozen CSV row before required scalars: {path}")
            variant = scalars["variant"].decode("utf-8")
            if not variant:
                raise ValueError(f"empty variant prevents safe block read: {path}")
            if variant != current_variant:
                if current_variant is not None:
                    closed_variants.add(current_variant)
                if variant in closed_variants:
                    raise ValueError(f"variant block reappears after ending: {variant} in {path}")
                current_variant, previous = variant, None
            stamp = _utc(scalars[monotonic_field].decode("utf-8").rstrip("\r"))
            if pd.isna(stamp):
                raise ValueError(f"missing {monotonic_field} prevents safe block read: {path}")
            if previous is not None and stamp < previous:
                raise ValueError(
                    f"non-monotonic {monotonic_field} within variant {variant} prevents safe read: {path}"
                )
            previous = stamp
            bounded = [_utc(scalars[name].decode("utf-8").rstrip("\r")) for name in bounded_fields]
            if any(pd.isna(value) for value in bounded):
                raise ValueError(f"missing bounded timestamp prevents safe block read: {path}")
            if all(start <= value < cutoff for value in bounded):
                selected.add(record_number)
            record_number += 1


def _selected_csv_rows(path: Path, *, expected_header: list[str], selected: set[int]) -> Iterable[list[str]]:
    """Parse only complete records selected by a prior scalar-only pass."""
    with gzip.open(path, "rb") as stream:
        header_bytes = stream.readline()
        header = next(csv.reader([header_bytes.decode("utf-8").rstrip("\r\n")]))
        if header != expected_header:
            raise ValueError(f"frozen stream header changed between bounded passes: {path}")
        record_number = 0
        while True:
            capture = record_number in selected
            raw_line = bytearray() if capture else None
            has_char = False
            while True:
                character = stream.read(1)
                if not character:
                    if not has_char:
                        return
                    delimiter = b"\n"
                else:
                    has_char = True
                    delimiter = character
                    if raw_line is not None:
                        raw_line.extend(character)
                if delimiter == b"\n" or not character:
                    break
            if raw_line is not None:
                yield next(csv.reader([bytes(raw_line).decode("utf-8").rstrip("\r\n")]))
            record_number += 1


def _signal_density(catalog: pd.DataFrame, clock: pd.Series) -> pd.Series:
    """Count distinct V1/V6 base assets with a raw launch in the preceding hour."""
    raw = COMPARE_EXP / "results/replay_two_year_20260912_v3/streams"
    events: list[tuple[pd.Timestamp, str]] = []
    asset_by_stream = {(row.venue, row.symbol): row.base_asset for row in catalog.itertuples(index=False)}
    source_to_base = {str(Path(row.source_path).resolve()): row.base_asset for row in catalog.itertuples(index=False)}
    run_manifest = json.loads((raw.parent / "input_manifest.json").read_text(encoding="utf-8"))
    folder_base = {item["key"]: source_to_base.get(str(Path(item["source_path"]).resolve())) for item in run_manifest["streams"]}
    for folder in raw.iterdir():
        path = folder / "signals.csv.gz"
        if not path.is_file():
            continue
        for header, values in _variant_block_rows(
            path, monotonic_field="signal_confirm_time", bounded_fields=("signal_confirm_time",), cutoff=DEVELOPMENT_END,
        ):
            row = dict(zip(header, values))
            stamp = _utc(row["signal_confirm_time"])
            if (row.get("variant") not in {"v1_common_execution_long", "v6_unfiltered_long", "v6_unfiltered_both"}
                    or row.get("signal_role") != "entry_candidate" or row.get("admitted_for_entry") != "True"
                    or stamp < DEVELOPMENT_START - pd.Timedelta(hours=1)):
                continue
            base = asset_by_stream.get((row.get("venue"), row.get("symbol"))) or folder_base.get(folder.name)
            if base is not None:
                events.append((stamp, base))
    return launch_density_from_events(events, clock)


def launch_density_from_events(events: Iterable[tuple[pd.Timestamp, str]], clock: pd.Series) -> pd.Series:
    """Count unique bases in strict (T-60m, T], including a signal at T itself."""
    by_time: dict[pd.Timestamp, set[str]] = defaultdict(set)
    for stamp, base in events:
        by_time[stamp].add(base)
    density = []
    for stamp in clock:
        members: set[str] = set()
        # With a 30m source clock, strict (T-60m, T] is exactly T-30m and T.
        for event_time in (stamp - pd.Timedelta(minutes=30), stamp):
            members.update(by_time.get(event_time, set()))
        density.append(len(members))
    return pd.Series(density, index=clock.index, dtype="int32")


def complete_aggregate_30m(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate only buckets containing every constituent 30m candle."""
    grouped = bars.resample(f"{minutes}min", origin="epoch", closed="left", label="left")
    return grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).loc[
        grouped.size().eq(minutes // 30)
    ].dropna()


def _background_returns(panel: pd.DataFrame, catalog: pd.DataFrame) -> pd.DataFrame:
    """Attach BTC/ETH close-to-close returns using the same preferred-venue rule."""
    result = panel.copy()
    for base in ("BTC", "ETH"):
        match = catalog.loc[catalog.base_asset.eq(base)]
        if len(match) != 1:
            result[f"{base.lower()}_return_30m"] = np.nan
            continue
        features = causal_asset_features(_load_bars(match.iloc[0].source_path))
        series = features.return_30m.reindex(pd.DatetimeIndex(result.bar_open)).to_numpy(float)
        result[f"{base.lower()}_return_30m"] = series
    return result


def _base_deduplicated_trades(catalog: pd.DataFrame) -> pd.DataFrame:
    """Read only development-closed trades from variant-block ledgers.

    The first pass reads only variant, entry, and exit timestamp scalars.  The
    second pass materializes CSV records only for rows whose entry and exit are
    both in development, so a cross-boundary row cannot materialize outcome
    fields that appear before ``exit_time`` in the frozen schema.
    """
    root = COMPARE_EXP / "results/replay_two_year_20260912_v3/streams"
    available = {(row.venue, row.symbol) for row in catalog.itertuples(index=False)}
    rows: list[dict[str, object]] = []
    required = {"signal_bar_open", "entry_time", "entry_price", "side", "initial_risk", "mfe_r", "exit_time", "net_r",
                "censored", "variant", "venue", "symbol", "asset", "timeframe_min", "segment"}
    for folder in root.iterdir():
        path = folder / "trades.csv.gz"
        if not path.is_file():
            continue
        header, selected = _variant_block_safe_record_numbers(
            path, monotonic_field="entry_time", bounded_fields=("entry_time", "exit_time"),
            start=DEVELOPMENT_START, cutoff=DEVELOPMENT_END,
        )
        if not required.issubset(header):
            raise ValueError(f"stream schema changed: {path}")
        for values in _selected_csv_rows(path, expected_header=header, selected=selected):
            item = dict(zip(header, values))
            entry, exit_time = _utc(item["entry_time"]), _utc(item["exit_time"])
            if item["variant"] not in VARIANTS or (item["venue"], item["symbol"]) not in available:
                continue
            item["entry_time"] = entry
            item["exit_time"] = exit_time
            item["signal_bar_open"] = _utc(item["signal_bar_open"])
            item["base_asset"] = canonical_asset(item["asset"])
            item["venue_rank"] = VENUE_ORDER[item["venue"]]
            rows.append(item)
    trade = pd.DataFrame(rows)
    if trade.empty:
        raise ValueError("no safely bounded development trades")
    for name in ("entry_price", "side", "initial_risk", "mfe_r", "net_r", "timeframe_min"):
        trade[name] = pd.to_numeric(trade[name], errors="raise")
    trade["censored"] = trade.censored.eq("True")
    trade = trade.sort_values(["variant", "timeframe_min", "signal_bar_open", "base_asset", "venue_rank", "symbol"], kind="stable")
    return trade.drop_duplicates(["variant", "timeframe_min", "signal_bar_open", "base_asset"], keep="first").reset_index(drop=True)


def _mae_r(trades: pd.DataFrame, catalog: pd.DataFrame) -> pd.Series:
    """Approximate MAE from exact-complete timeframe bars through the exit bar."""
    paths = {(row.venue, row.symbol): row.source_path for row in catalog.itertuples(index=False)}
    out = pd.Series(np.nan, index=trades.index, dtype=float)
    closed = trades.loc[~trades.censored.astype(bool)]
    for (venue, symbol, minutes), subset in closed.groupby(["venue", "symbol", "timeframe_min"], sort=False):
        path = paths.get((venue, symbol))
        if path is None:
            continue
        bars = complete_aggregate_30m(_load_bars(path), int(minutes))
        for row in subset.itertuples():
            span = bars.loc[(bars.index >= row.entry_time) & (bars.index <= row.exit_time)]
            if span.empty or not np.isfinite(row.initial_risk) or row.initial_risk <= 0:
                continue
            adverse = (float(span.low.min()) - row.entry_price) if row.side > 0 else (row.entry_price - float(span.high.max()))
            out.at[row.Index] = adverse / float(row.initial_risk)
    return out


def attach_context(trades: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """Join breadth only at the next-open decision time, which equals signal-bar close."""
    merged = trades.merge(panel.drop(columns=["bar_open"]), left_on="entry_time", right_on="asof", how="left", validate="many_to_one")
    if merged["asof"].isna().any():
        raise ValueError("frozen trade has no same-close breadth row in development window")
    return merged


def summarize_slice(part: pd.DataFrame, *, label: str, metric: str) -> dict[str, object]:
    """Report realized outcomes; the corrected run has no random-entry control yet."""
    closed = part.loc[~part.censored.astype(bool)].copy()
    return {
        "slice": label, "metric": metric, "candidates": len(part), "closed": len(closed),
        "censored": int(part.censored.astype(bool).sum()), "mean_net_r": closed.net_r.mean(),
        "median_net_r": closed.net_r.median(), "win_rate": closed.net_r.gt(0).mean(),
        "realized_ge_10r": closed.net_r.ge(10).mean(),
        "mae_r_exit_bar_window_approx_median": closed.mae_r_exit_bar_window_approx.median(),
        "mae_r_exit_bar_window_approx_p05": closed.mae_r_exit_bar_window_approx.quantile(.05), "matched": 0,
        "matched_delta_net_r": np.nan, "matched_signflip_p": np.nan,
    }


def month_block_permutation(part: pd.DataFrame, *, draws: int = 10_000) -> float:
    """Test the one breadth-label split while preserving calendar-month outcome blocks.

    This is an incremental null for breadth labels, not the repository-required
    same-asset/month/volatility random-entry benchmark.  It cannot authorize a
    production filter.
    """
    closed = part.loc[~part.censored.astype(bool) & part.joint_delta_60m.notna()].copy()
    closed["month"] = closed.entry_time.dt.to_period("M").astype(str)
    observed = []
    groups = []
    for _, block in closed.groupby("month", sort=False):
        labels = block.joint_delta_60m.gt(0).to_numpy(bool)
        if labels.any() and (~labels).any():
            values = block.net_r.to_numpy(float)
            observed.append(values[labels].mean() - values[~labels].mean())
            groups.append((values, labels.sum()))
    if not groups:
        return np.nan
    actual = abs(float(np.mean(observed)))
    rng = np.random.default_rng(20260913)
    simulated = np.empty(draws)
    for draw in range(draws):
        deltas = []
        for values, count in groups:
            index = rng.permutation(len(values))
            deltas.append(values[index[:count]].mean() - values[index[count:]].mean())
        simulated[draw] = abs(np.mean(deltas))
    return float((1 + np.count_nonzero(simulated >= actual)) / (draws + 1))


def outcome_tables(context: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Create baseline, isolated quartile descriptions, and one frozen single-variable rule."""
    summary, slices = [], []
    for (variant, minutes), part in context.groupby(["variant", "timeframe_min"], sort=True):
        summary.append({"variant": variant, "timeframe_min": int(minutes), **summarize_slice(part, label="all", metric="all")})
        closed = part.loc[~part.censored.astype(bool)]
        for metric in METRICS:
            values = closed[metric].dropna()
            if len(values) < 8:
                continue
            lo, hi = values.quantile(.25), values.quantile(.75)
            for name, subset in (("bottom_quartile", part.loc[part[metric].le(lo)]),
                                 ("top_quartile", part.loc[part[metric].ge(hi)])):
                slices.append({"variant": variant, "timeframe_min": int(minutes),
                               "threshold": lo if name == "bottom_quartile" else hi,
                               **summarize_slice(subset, label=name, metric=metric)})
    # The only proposed rule is directional breadth acceleration; it is one variable,
    # no level, volatility, BTC/ETH, or density condition is conjoined with it.
    rule = context.loc[context.joint_delta_60m.gt(0)].copy()
    rule_rows = []
    for (variant, minutes), part in rule.groupby(["variant", "timeframe_min"], sort=True):
        rule_rows.append({"variant": variant, "timeframe_min": int(minutes), "rule": "joint_delta_60m > 0",
                          "month_block_label_permutation_p": month_block_permutation(context.loc[
                              (context.variant == variant) & context.timeframe_min.eq(minutes)]),
                          **summarize_slice(part, label="candidate", metric="joint_delta_60m")})
    return pd.DataFrame(summary), pd.DataFrame(slices), pd.DataFrame(rule_rows)


def _markdown_table(frame: pd.DataFrame, columns: Iterable[str]) -> str:
    view = frame.loc[:, list(columns)].copy()
    for column in view.columns:
        if pd.api.types.is_float_dtype(view[column]):
            view[column] = view[column].map(lambda x: "—" if pd.isna(x) else f"{x:.3f}")
    return view.to_markdown(index=False)


def write_report(report: Path, output: Path, catalog: pd.DataFrame, summary: pd.DataFrame,
                 rule: pd.DataFrame, manifest: dict) -> None:
    """Render a Chinese source report whose claims are linked to frozen artifacts."""
    report.parent.mkdir(parents=True, exist_ok=True)
    tables = _markdown_table(summary, ["variant", "timeframe_min", "candidates", "closed", "mean_net_r", "median_net_r", "win_rate", "realized_ge_10r", "mae_r_exit_bar_window_approx_median"])
    proposed = _markdown_table(rule, ["variant", "timeframe_min", "candidates", "closed", "mean_net_r", "median_net_r", "win_rate", "month_block_label_permutation_p"]) if len(rule) else "无足够样本。"
    report.write_text(f"""# SPIKE 市场广度开发期研究（2026-09-13）

## 结论

这是一次开发期描述，不是生产准入、模型训练或收益验证。`joint_delta_60m > 0` 是单变量研究候选，未与 BTC/ETH、量价扩散、密度或任一水平阈值打包；由于同币×同月×同波动桶随机入场对照尚未完成，它不得冻结为生产准入规则。

## 范围与复现

- 开发窗口：`2024-09-10T00:00:00Z` 至 `2025-09-10T00:00:00Z`（右端排除）。OHLCV 按全局时间前缀读取；按 variant 连续块拼接的信号与交易账本则扫描至 EOF、逐块检查单调性。交易账本首遍只读取 variant/entry/exit 标量与安全行号，第二遍仅物化 entry 和 exit 均在开发期的行；边界后 OHLCV、信号或结果 payload 均未进入数据表。
- 交易结果：逐流读取冻结 common-execution `trades.csv.gz` 的 V1 common execution long 与 V7 BB long；只保留入场与退出均在开发期的已实现行，不把跨开发边界的最终 P&L 作为开发结果。
- 市场横截面：{len(catalog)} 个有冻结 30m OHLCV 的已评估底层资产，按 Binance、OKX 的固定优先级再按底层币种去重；1000/1000000 面额前缀在去重时还原。Gate direct 文件存在，但 30m 收据为 71 个 partial、0 个 complete，且无开发期覆盖，故 Gate 的广度上下文与对应候选均明确排除，未用网络或其他交易所补齐。
- 指标：上涨参与率、SMA20 与 EMA20 双站上、联合广度、30/60m 联合广度变化、过去 1h 的 V1/V6 不同币启动密度、RV>1 且 TR/前20根 ATR>1、横截面 30m 收益中位/IQR、BTC/ETH 30m 背景。密度是严格 `(T-60m,T]`，包含信号自身；联合广度加速度同时附带分母变化标记，分母变化时只作描述。所有指标只用收盘 K 线及之前数据。
- 15m：冻结源只有 30m 基础 K 线，因此 `joint_delta_15m` 全部为缺失；没有将 30m 变化伪装为 15m。
- 复现：`python3 -m yoyo.evaluation.spike_market_breadth_study --run`。产物及 SHA 见 `{output}/results/manifest.json`。

## 各周期基线（已实现结果）

{tables}

此轮没有加载合并控制账本，也没有生成随机入场保护性对照。表中候选规则的 `month_block_label_permutation_p` 仅是保持日历月块的广度标签置换零假设，不能代替同币×同月×同波动桶随机入场对照。

## 冻结的单变量候选：60m 联合广度上升

{proposed}

这个候选来自已经消费的开发期，不能据此声称胜率或净 R 可泛化。完成随机入场对照后才可预注册：仅测试 `joint_delta_60m > 0`，沿用同一共同执行、成本、币种去重，匹配同币×同月×信号前波动桶；不叠加任何其他指标、不重选阈值，报告每周期的候选数、已实现净 R 均值/中位、胜率、>=10R、MAE 分布与随机对照差。后续窗口在 owner 明确批准前不得被读取。

## 必报口径与诚实声明

本研究不是分类器，未产生概率分数，因此 AUC、单特征分类基线和 top-decile 模型排序不适用；这里的单变量分位表在 `single_variable_slices.csv` 中作为探索性描述，不能替代验证。V1/V7 的 R 及退出来自逐流、开发前缀的共同执行账本；MAE 从同一开发前缀 OHLC 缓存重算。此前另一个初步密度切片曾完整物化合并两年账本的边界后行，虽未打印或评分那些行，仍构成意外 holdout-access 污染；本报告不使用其数字，不能声称 holdout 未消耗。广度与高广度、广度跃迁是不同变量；报告未把任一结论接到线上 V1 过滤、通知或仓位。

## 下一步

首先需要完成同币×同月×同波动桶随机入场保护性对照；在此之前不应批准验证窗口或生产准入。`production_eligible=false`、`training_eligible=false`（本研究未修改任何注册表）。
""", encoding="utf-8")


def run(output: Path, report: Path) -> None:
    """Execute the immutable development analysis and write bounded artifacts."""
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite existing output: {output}")
    results = output / "results"
    results.mkdir(parents=True)
    catalog = _source_catalog()
    # Validate every variant-block ledger before spending time on the frozen
    # market panel.  The clock must match the final panel exactly before its
    # preflight density can be attached.
    trades = _base_deduplicated_trades(catalog)
    asof_clock = development_asof_clock()
    density = _signal_density(catalog, asof_clock)
    panel, prefix_digests = build_market_panel(catalog)
    catalog["actual_development_prefix_sha256"] = catalog.source_path.map(prefix_digests)
    if catalog.actual_development_prefix_sha256.isna().any():
        raise ValueError("development-prefix digest missing from breadth panel input")
    if not pd.DatetimeIndex(panel.asof).equals(pd.DatetimeIndex(asof_clock)):
        raise ValueError("preflight signal-density clock differs from breadth panel as-of clock")
    panel = _background_returns(panel, catalog)
    panel["launch_density_1h"] = density.to_numpy()
    context = attach_context(trades, panel)
    context["mae_r_exit_bar_window_approx"] = _mae_r(context, catalog)
    summary, slices, rule = outcome_tables(context)
    catalog.to_csv(results / "source_manifest.csv", index=False)
    panel.to_csv(results / "market_breadth_30m.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    context.to_csv(results / "candidate_context.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    summary.to_csv(results / "outcome_summary.csv", index=False)
    slices.to_csv(results / "single_variable_slices.csv", index=False)
    rule.to_csv(results / "frozen_candidate_rule.csv", index=False)
    manifest = {
        "development_start": DEVELOPMENT_START.isoformat(), "development_end_exclusive": DEVELOPMENT_END.isoformat(),
        "holdout_consumed": True,
        "holdout_access_note": "A preliminary combined-ledger read materialized post-cutoff rows; this corrected run does not reuse it.",
        "post_development_data_handling": {
            "post_cutoff_payload_materialized": False,
            "post_cutoff_payload_parsed": False,
            "cutoff_check": (
                "Variant-block ledgers scan only variant plus bounded timestamp scalars through EOF; "
                "trades use a scalar-only first pass and materialize CSV records only for development-safe row numbers."
            ),
            "compressed_byte_read_boundary": "Not asserted: gzip may buffer or read ahead internally.",
        },
        "base_assets": len(catalog), "base_dedup_order": ["binance", "okx"],
        "gate": (
            "unavailable: direct 30m receipts are 71 partial and 0 complete, with no development-period coverage; "
            "no normalized frozen OHLCV; excluded from breadth context and candidate scoring"
        ),
        "source_integrity": {
            "frozen_upstream_expected_full_source_sha256": (
                "Copied from the pre-existing comparison input manifest; not recomputed from market files by this run."
            ),
            "actual_development_prefix_sha256": (
                "SHA-256 of each uncompressed CSV header plus complete rows strictly before development_end, "
                "streamed while building the market panel."
            ),
        },
        "frozen_rule": "joint_delta_60m > 0", "joint_delta_15m": "unavailable: source cadence is 30m",
        "inputs": {str(path.relative_to(ROOT)): sha256(path) for path in (
            SOURCE_EXP / "results/coverage_limited.csv",
            COMPARE_EXP / "results/replay_two_year_20260912_v3/input_manifest.json",
        )},
        "outputs": {path.name: sha256(path) for path in results.iterdir() if path.is_file()},
        "study_code_sha256": sha256(Path(__file__)),
    }
    (results / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_report(report, output, catalog, summary, rule, manifest)
    subprocess.run(["python3", "scripts/md_to_html.py", str(report), "--out-dir", "analysis/html"], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="write the bounded development study")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required; this runner never overwrites an existing study")
    run(args.output, args.report)


if __name__ == "__main__":
    main()
