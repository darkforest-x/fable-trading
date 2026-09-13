"""Read-only portfolio-risk accounting over frozen SPIKE cross-venue events.

This study deliberately separates event construction from risk admission.  It
reads the completed all-source V7/V8 event membership ledger and exact frozen
serial-replay trades.  Event folding uses the upstream causal availability
clock.  The two portfolio rules then use only an already-open position's entry
and exit clocks at a candidate's frozen entry time: one open position per
underlying asset, or a fixed five-position concurrency limit.  Neither rule
uses a trade's later return to decide admission.  Frozen ``net_r`` is joined
only after admission for descriptive, nonblind historical metrics.

The input receipts do not include a frozen multi-asset OHLC return panel, so a
30-day correlation gate is intentionally not estimated.  Estimating it from
these later trade results would be forward-looking and is prohibited.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd


EXP = Path("experiments/active/exp-spike-portfolio-risk-20260913-v1")
CROSS_RESULTS = Path("experiments/active/exp-spike-cross-venue-events-20260913-v1/results")
REPLAY = Path("experiments/active/exp-spike-v8-noise-filter-20260913-v1/replay_v1")
ARMS = ("v7", "v8")
CONCURRENCY_CAP = 5
POLICIES = (
    "source_confirmations",
    "event_leaders",
    "event_leaders_asset_cap",
    "event_leaders_concurrency_cap_5",
)
JOIN_KEY = ["stream_key", "signal_bar_open", "side", "arm"]
MEMBER_COLUMNS = [
    "source_event_id", "event_id", "arm", "period", "asset", "side",
    "timeframe_min", "stream_key", "signal_bar_open", "availability_time",
    "is_event_leader", "venue", "leader_venue",
]
TRADE_COLUMNS = [
    "trade_id", "stream_key", "signal_bar_open", "side", "arm", "period",
    "entry_time", "exit_time", "entry_price", "initial_risk", "net_return",
    "net_r", "censored", "exit_reason",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_trade_receipts(streams: Path, config: dict[str, object]) -> list[tuple[Path, bytes]]:
    """Verify every frozen trade payload against cross-study's pinned receipts before parsing."""
    ledger_path = CROSS_RESULTS.parent / "input_receipts.json"
    if _sha256(ledger_path) != config["cross_input_receipts_sha256"]:
        raise ValueError("cross input receipt manifest changed")
    ledger = json.loads(ledger_path.read_text())
    expected = [item for item in ledger["receipts"] if str(item["relative_path"]).endswith(".trades.csv.gz")]
    expected.sort(key=lambda item: item["relative_path"])
    aggregate = hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if aggregate != config["trade_receipt_aggregate_sha256"] or len(expected) != int(config["expected_trade_receipts"]):
        raise ValueError("pinned trade receipt contract changed")
    actual: list[tuple[Path, bytes]] = []
    for path in sorted(streams.glob("*.trades.csv.gz")):
        payload = path.read_bytes()
        actual.append((path, payload))
    actual_entries = [{"relative_path": path.relative_to(streams.parent).as_posix(), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()} for path, payload in actual]
    if actual_entries != expected:
        raise ValueError("frozen trade receipt identity differs from pinned manifest")
    return actual


def _bool(values: pd.Series, column: str) -> pd.Series:
    """Parse receipt booleans strictly so malformed evidence fails closed."""
    if values.dtype == bool:
        return values.astype(bool)
    normalized = values.astype(str).str.lower()
    valid = values.isna() | normalized.isin({"true", "false"})
    if not valid.all():
        raise ValueError(f"nonboolean values in {column}")
    return normalized.eq("true")


def _time(values: pd.Series, column: str, *, nullable: bool = False) -> pd.Series:
    parsed = pd.to_datetime(values, utc=True, errors="coerce")
    if not nullable and parsed.isna().any():
        raise ValueError(f"{column} must contain finite UTC timestamps")
    return parsed


def load_members(path: Path) -> pd.DataFrame:
    """Load the upstream all-source event ledger without changing its grouping."""
    members = pd.read_csv(path, usecols=MEMBER_COLUMNS)
    missing = set(MEMBER_COLUMNS) - set(members.columns)
    if missing:
        raise ValueError("event members missing columns: " + ", ".join(sorted(missing)))
    members["signal_bar_open"] = _time(members.signal_bar_open, "signal_bar_open")
    members["availability_time"] = _time(members.availability_time, "availability_time")
    members["side"] = pd.to_numeric(members.side, errors="raise").astype(int)
    members["timeframe_min"] = pd.to_numeric(members.timeframe_min, errors="raise").astype(int)
    members["is_event_leader"] = _bool(members.is_event_leader, "is_event_leader")
    if not members.arm.isin(ARMS).all() or not members.side.isin((-1, 1)).all():
        raise ValueError("event members contain unsupported arm or side")
    if members.source_event_id.duplicated().any():
        raise ValueError("event member source identities must be unique")
    return members


def load_frozen_trades(receipts: list[tuple[Path, bytes]]) -> pd.DataFrame:
    """Read only frozen serial-replay outcomes and their already-recorded clocks."""
    if not receipts:
        raise ValueError("no frozen trade receipts found")
    frames = [frame for _, payload in receipts if not (frame := pd.read_csv(io.BytesIO(payload), compression="gzip", usecols=TRADE_COLUMNS)).empty]
    trades = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=TRADE_COLUMNS)
    trades["signal_bar_open"] = _time(trades.signal_bar_open, "trade signal_bar_open")
    trades["entry_time"] = _time(trades.entry_time, "trade entry_time")
    trades["exit_time"] = _time(trades.exit_time, "trade exit_time")
    trades["side"] = pd.to_numeric(trades.side, errors="raise").astype(int)
    trades["censored"] = _bool(trades.censored, "trade censored")
    for column in ("entry_price", "initial_risk", "net_return", "net_r"):
        trades[column] = pd.to_numeric(trades[column], errors="coerce")
    if trades.duplicated(JOIN_KEY).any():
        raise ValueError("frozen trade receipts have ambiguous source mapping")
    if not trades.arm.isin(ARMS).all() or not trades.side.isin((-1, 1)).all():
        raise ValueError("frozen trades contain unsupported arm or side")
    if (trades.exit_time < trades.entry_time).any():
        raise ValueError("frozen trade exit precedes entry")
    return trades


def prepare_candidates(members: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Join exact frozen outcomes while retaining all unmapped source receipts.

    Entry admission uses ``entry_time``, ``asset``, ``side`` and a stable
    receipt identifier only.  ``net_r`` and ``net_return`` are deliberately
    absent from every admission condition and are read only for post-hoc
    metrics after a candidate is accepted.
    """
    required_members = set(MEMBER_COLUMNS)
    required_trades = set(TRADE_COLUMNS)
    if required_members - set(members.columns):
        raise ValueError("members do not satisfy the event-ledger contract")
    if required_trades - set(trades.columns):
        raise ValueError("trades do not satisfy the frozen-outcome contract")
    result = members.merge(
        trades, on=JOIN_KEY, how="left", validate="one_to_one", suffixes=("", "_trade"), indicator=True,
    )
    if (result["period_trade"].notna() & ~result.period.eq(result.period_trade)).any():
        raise ValueError("event member and frozen trade disagree on period")
    result["frozen_outcome_available"] = result._merge.eq("both")
    result.drop(columns=["_merge", "period_trade"], inplace=True)
    result["censored"] = pd.Series(np.where(result["censored"].isna(), False, result["censored"]), index=result.index).astype(bool)
    available = result.frozen_outcome_available
    if available.any():
        # A censored boundary has a valid exposure/risk clock but deliberately
        # no realized outcome.  Requiring its later return would wrongly turn
        # it into an unavailable candidate and understate reserved capacity.
        realized = available & ~result.censored
        valid = np.isfinite(result.loc[available, ["entry_price", "initial_risk"]]).all(axis=1)
        valid_realized = np.isfinite(result.loc[realized, ["net_return", "net_r"]]).all(axis=1)
        if not valid.all() or not valid_realized.all():
            raise ValueError("frozen mapped trade has non-finite outcome or risk fields")
        if not result.loc[available, "entry_price"].gt(0).all() or not result.loc[available, "initial_risk"].gt(0).all():
            raise ValueError("frozen mapped trade has nonpositive entry price or initial risk")
        if (result.loc[available, "entry_time"] < result.loc[available, "availability_time"]).any():
            raise ValueError("frozen trade enters before its source receipt is available")
    # The source identity is a stable causal tie-breaker; it contains no price
    # path, exit, return, or later-confirmation field.
    result["candidate_key"] = result.arm.astype(str) + ":" + result.source_event_id.astype(str)
    return result


def _policy_population(candidates: pd.DataFrame, policy: str) -> pd.DataFrame:
    """Select only the fixed structural population for one isolated policy."""
    if policy not in POLICIES:
        raise ValueError(f"unsupported policy: {policy}")
    if policy == "source_confirmations":
        return candidates.copy()
    return candidates.loc[candidates.is_event_leader].copy()


def apply_policy(candidates: pd.DataFrame, policy: str) -> pd.DataFrame:
    """Apply a structural fold plus at most one causal portfolio risk rule.

    Exits at a timestamp release capacity before candidates at that timestamp.
    Same-timestamp candidates are ordered by ``candidate_key`` alone.  This is
    deterministic and outcome-independent; it prevents simultaneous duplicate
    venue receipts from silently bypassing a one-asset or concurrency rule.
    """
    selected = _policy_population(candidates, policy).reset_index(drop=True)
    selected["policy"] = policy
    count = len(selected)
    accepted_values = np.zeros(count, dtype=bool)
    decision_values = np.full(count, "blocked", dtype=object)
    reason_values = np.full(count, "frozen_outcome_unavailable", dtype=object)
    before_values = np.full(count, np.nan, dtype=float)
    after_values = np.full(count, np.nan, dtype=float)
    asset_before_values = np.full(count, np.nan, dtype=float)
    tradable = selected.frozen_outcome_available
    reason_values[tradable.to_numpy()] = None
    # Heap records (exit clock, causal identity, asset).  Its membership is the
    # complete entry-known state; returns are never consulted during admission.
    import heapq

    # V7 and V8 are alternative frozen admissions, not simultaneous book
    # members.  Each arm therefore receives an independent portfolio clock.
    for arm in ARMS:
        ordered = selected.loc[tradable & selected.arm.eq(arm)].sort_values(
            ["entry_time", "candidate_key"], kind="mergesort",
        )
        open_heap: list[tuple[int, str, str]] = []
        active_assets: Counter[str] = Counter()
        active_count = 0
        for row in ordered.itertuples():
            index = int(row.Index)
            entry = pd.Timestamp(row.entry_time)
            while open_heap and open_heap[0][0] <= entry.value:
                _, _, asset = heapq.heappop(open_heap)
                active_assets[asset] -= 1
                if active_assets[asset] == 0:
                    del active_assets[asset]
                active_count -= 1
            asset = str(row.asset)
            before_values[index] = active_count
            asset_before_values[index] = active_assets.get(asset, 0)
            reason: str | None = None
            if policy == "event_leaders_asset_cap" and active_assets.get(asset, 0) > 0:
                reason = "asset_already_open"
            elif policy == "event_leaders_concurrency_cap_5" and active_count >= CONCURRENCY_CAP:
                reason = "concurrency_cap_5"
            if reason is not None:
                reason_values[index] = reason
                after_values[index] = active_count
                continue
            accepted_values[index] = True
            decision_values[index] = "accepted"
            reason_values[index] = None
            heapq.heappush(open_heap, (pd.Timestamp(row.exit_time).value, str(row.candidate_key), asset))
            active_assets[asset] += 1
            active_count += 1
            after_values[index] = active_count
    selected["accepted"] = accepted_values
    selected["decision"] = decision_values
    selected["blocked_reason"] = reason_values
    selected["open_count_before"] = before_values
    selected["open_count_after"] = after_values
    selected["asset_open_before"] = asset_before_values
    return selected.sort_values(["entry_time", "candidate_key"], kind="mergesort", na_position="last").reset_index(drop=True)


def _drawdown_r(closed: pd.DataFrame) -> float:
    """Return realized-exit-time maximum drawdown in frozen net R units."""
    if closed.empty:
        return 0.0
    returns = closed.sort_values(["exit_time", "candidate_key"], kind="mergesort").net_r.to_numpy(float)
    equity = np.concatenate(([0.0], np.cumsum(returns)))
    return float(np.max(np.maximum.accumulate(equity) - equity))


def summarize_policy(audit: pd.DataFrame, all_candidates: pd.DataFrame, policy: str) -> pd.DataFrame:
    """Report source/event/admission and frozen R metrics by arm and time split."""
    rows: list[dict[str, object]] = []
    for arm in ARMS:
        for period in ("development", "validation"):
            source = all_candidates.loc[all_candidates.arm.eq(arm) & all_candidates.period.eq(period)]
            part = audit.loc[audit.arm.eq(arm) & audit.period.eq(period)]
            accepted = part.loc[part.accepted]
            closed = accepted.loc[~accepted.censored]
            gains = float(closed.net_r.clip(lower=0).sum())
            losses = float(-closed.net_r.clip(upper=0).sum())
            rows.append({
                "policy": policy,
                "arm": arm,
                "period": period,
                "source_confirmations": int(len(source)),
                "market_events": int(source.event_id.nunique()),
                "policy_candidates": int(len(part)),
                "folded_source_confirmations": int(len(source) - len(part)),
                "frozen_outcome_available": int(part.frozen_outcome_available.sum()),
                "accepted": int(len(accepted)),
                "blocked": int(len(source) - len(accepted)),
                "blocked_frozen_outcome_unavailable": int((~part.frozen_outcome_available).sum()),
                "blocked_asset_already_open": int(part.blocked_reason.eq("asset_already_open").sum()),
                "blocked_concurrency_cap_5": int(part.blocked_reason.eq("concurrency_cap_5").sum()),
                "censored_accepted": int(accepted.censored.sum()),
                "closed": int(len(closed)),
                "max_concurrency_after_entry": int(accepted.open_count_after.max()) if len(accepted) else 0,
                "cumulative_net_r": float(closed.net_r.sum()),
                "max_drawdown_r": _drawdown_r(closed),
                "pf_net_r": gains / losses if losses > 0 else math.nan,
                "realized_10r_count": int(closed.net_r.ge(10).sum()),
            })
    return pd.DataFrame(rows)


def blocked_reason_summary(audit: pd.DataFrame, all_candidates: pd.DataFrame, policy: str) -> pd.DataFrame:
    """Keep a compact audit count that reconciles every structural exclusion."""
    rows = []
    for arm in ARMS:
        for period in ("development", "validation"):
            source = all_candidates.loc[all_candidates.arm.eq(arm) & all_candidates.period.eq(period)]
            part = audit.loc[audit.arm.eq(arm) & audit.period.eq(period)]
            rows.append({"policy": policy, "arm": arm, "period": period, "reason": "event_folded_follower",
                         "count": int(len(source) - len(part))})
            for reason, count in part.loc[~part.accepted, "blocked_reason"].value_counts(dropna=False).items():
                rows.append({"policy": policy, "arm": arm, "period": period,
                             "reason": str(reason), "count": int(count)})
    return pd.DataFrame(rows)


def run(
    output: Path,
    *,
    members_path: Path = CROSS_RESULTS / "event_members.csv.gz",
    replay: Path = REPLAY,
) -> dict[str, int]:
    """Materialize the fixed nonblind portfolio-risk comparison and audit CSVs."""
    config_path = EXP / "config.json"
    config = json.loads(config_path.read_text())
    for path, key in ((members_path, "event_members_sha256"), (CROSS_RESULTS / "run_manifest.json", "cross_venue_manifest_sha256"),
                      (replay / "manifest.json", "replay_manifest_sha256")):
        if _sha256(path) != config[key]:
            raise ValueError(f"frozen input changed: {path}")
    members = load_members(members_path)
    trade_receipts = verify_trade_receipts(replay / "streams", config)
    trades = load_frozen_trades(trade_receipts)
    candidates = prepare_candidates(members, trades)
    output.mkdir(parents=True, exist_ok=True)
    audits = []
    metrics = []
    blocked = []
    for policy in POLICIES:
        audit = apply_policy(candidates, policy)
        audits.append(audit)
        metrics.append(summarize_policy(audit, candidates, policy))
        blocked.append(blocked_reason_summary(audit, candidates, policy))
    audit_frame = pd.concat(audits, ignore_index=True)
    metrics_frame = pd.concat(metrics, ignore_index=True)
    blocked_frame = pd.concat(blocked, ignore_index=True)
    output_columns = [
        "policy", "source_event_id", "event_id", "arm", "period", "asset", "side", "timeframe_min", "venue", "leader_venue",
        "availability_time", "entry_time", "exit_time", "is_event_leader", "frozen_outcome_available", "trade_id", "censored",
        "accepted", "decision", "blocked_reason", "open_count_before", "open_count_after", "asset_open_before",
        "initial_risk", "entry_price", "net_return", "net_r", "exit_reason",
    ]
    audit_frame.loc[:, output_columns].to_csv(output / "risk_gate_event_audit.csv.gz", index=False, compression="gzip")
    metrics_frame.to_csv(output / "metrics.csv", index=False)
    blocked_frame.to_csv(output / "blocked_reason_summary.csv", index=False)
    correlation = {
        "implemented": False,
        "rule": "past_30d_same_direction_return_correlation_ge_0.8",
        "reason": "The frozen cross-venue event and replay trade receipts contain outcomes and clocks, but no frozen multi-asset OHLC return panel. Using later realized trade outcomes to estimate correlation would violate the causal rule.",
    }
    (output / "correlation_gate_status.json").write_text(json.dumps(correlation, indent=2) + "\n")
    receipt = {
        "config_sha256": _sha256(config_path),
        "code_sha256": _sha256(Path(__file__)),
        "event_members_sha256": _sha256(members_path),
        "cross_venue_manifest_sha256": _sha256(CROSS_RESULTS / "run_manifest.json"),
        "replay_manifest_sha256": _sha256(replay / "manifest.json"),
        "cross_input_receipts_sha256": config["cross_input_receipts_sha256"],
        "trade_receipt_aggregate_sha256": config["trade_receipt_aggregate_sha256"],
        "trade_receipt_count": len(trade_receipts),
        "source_confirmations": int(len(candidates)),
        "market_events": int(candidates.event_id.nunique()),
        "frozen_trade_rows": int(len(trades)),
        "audit_rows": int(len(audit_frame)),
        "concurrency_cap": CONCURRENCY_CAP,
        "research_only": True,
        "nonblind_reused_history": True,
        "correlation_gate_implemented": False,
    }
    (output / "run_manifest.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return {key: value for key, value in receipt.items() if isinstance(value, int)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=EXP / "results")
    parser.add_argument("--members", type=Path, default=CROSS_RESULTS / "event_members.csv.gz")
    parser.add_argument("--replay", type=Path, default=REPLAY)
    args = parser.parse_args()
    print(json.dumps(run(args.output, members_path=args.members, replay=args.replay), indent=2))
