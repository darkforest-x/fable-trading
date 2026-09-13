"""Causal acceptance tests for the read-only SPIKE portfolio-risk study."""
from __future__ import annotations

import gzip
import hashlib
import json

import pandas as pd
import pytest

from yoyo.evaluation import spike_portfolio_risk_study as study

from yoyo.evaluation.spike_portfolio_risk_study import (
    MEMBER_COLUMNS,
    TRADE_COLUMNS,
    apply_policy,
    prepare_candidates,
    summarize_policy,
)


def _members(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=MEMBER_COLUMNS)
    for column in ("signal_bar_open", "availability_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame


def _trades(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    for column in ("signal_bar_open", "entry_time", "exit_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame


def _member(source: str, event: str, asset: str, at: str, *, leader: bool = True) -> dict:
    return {
        "source_event_id": source, "event_id": event, "arm": "v7", "period": "development", "asset": asset,
        "side": 1, "timeframe_min": 60, "stream_key": source, "signal_bar_open": at,
        "availability_time": at, "is_event_leader": leader, "venue": "venue", "leader_venue": "venue",
    }


def _trade(source: str, at: str, exit_at: str, net_r: float = 1.0) -> dict:
    return {
        "trade_id": f"trade-{source}", "stream_key": source, "signal_bar_open": at, "side": 1, "arm": "v7",
        "period": "development", "entry_time": at, "exit_time": exit_at, "entry_price": 100.0,
        "initial_risk": 10.0, "net_return": net_r / 10.0, "net_r": net_r, "censored": False,
        "exit_reason": "frozen",
    }


def test_event_leader_policy_folds_followers_without_using_outcomes():
    members = _members([
        _member("leader", "event-1", "BTC", "2024-01-01T00:00Z"),
        _member("follower", "event-1", "BTC", "2024-01-01T00:01Z", leader=False),
    ])
    trades = _trades([
        _trade("leader", "2024-01-01T00:00Z", "2024-01-01T02:00Z", 2.0),
        _trade("follower", "2024-01-01T00:01Z", "2024-01-01T02:00Z", -9.0),
    ])
    candidates = prepare_candidates(members, trades)
    source = apply_policy(candidates, "source_confirmations")
    leaders = apply_policy(candidates, "event_leaders")
    assert source.accepted.sum() == 2
    assert leaders.accepted.sum() == 1
    assert leaders.source_event_id.tolist() == ["leader"]
    metrics = summarize_policy(leaders, candidates, "event_leaders")
    row = metrics.loc[(metrics.arm == "v7") & (metrics.period == "development")].iloc[0]
    assert row.source_confirmations == 2
    assert row.market_events == 1
    assert row.folded_source_confirmations == 1
    assert row.accepted == 1
    assert row.cumulative_net_r == 2.0


def test_asset_cap_blocks_only_same_asset_until_its_frozen_exit_clock():
    members = _members([
        _member("btc-open", "event-btc-a", "BTC", "2024-01-01T00:00Z"),
        _member("btc-repeat", "event-btc-b", "BTC", "2024-01-01T01:00Z"),
        _member("eth-open", "event-eth", "ETH", "2024-01-01T01:00Z"),
        _member("btc-after", "event-btc-c", "BTC", "2024-01-01T02:00Z"),
    ])
    trades = _trades([
        _trade("btc-open", "2024-01-01T00:00Z", "2024-01-01T02:00Z"),
        _trade("btc-repeat", "2024-01-01T01:00Z", "2024-01-01T03:00Z"),
        _trade("eth-open", "2024-01-01T01:00Z", "2024-01-01T03:00Z"),
        _trade("btc-after", "2024-01-01T02:00Z", "2024-01-01T04:00Z"),
    ])
    audit = apply_policy(prepare_candidates(members, trades), "event_leaders_asset_cap").set_index("source_event_id")
    assert audit.loc["btc-open", "accepted"]
    assert audit.loc["btc-repeat", "blocked_reason"] == "asset_already_open"
    assert audit.loc["eth-open", "accepted"]
    assert audit.loc["btc-after", "accepted"]  # exits release capacity before same-clock entries.


def test_concurrency_cap_is_independent_of_asset_gate_and_tie_order_is_stable():
    members = _members([
        _member(f"source-{n}", f"event-{n}", f"asset-{n}", "2024-01-01T00:00Z") for n in range(6)
    ])
    trades = _trades([
        _trade(f"source-{n}", "2024-01-01T00:00Z", "2024-01-01T02:00Z", float(n)) for n in range(6)
    ])
    candidates = prepare_candidates(members, trades)
    first = apply_policy(candidates, "event_leaders_concurrency_cap_5")
    second = apply_policy(candidates.sample(frac=1, random_state=4), "event_leaders_concurrency_cap_5")
    assert first.accepted.sum() == 5
    assert first.blocked_reason.eq("concurrency_cap_5").sum() == 1
    assert first.set_index("source_event_id").accepted.to_dict() == second.set_index("source_event_id").accepted.to_dict()
    # With distinct assets, asset-cap permits all six; the policies differ by only their stated rule.
    assert apply_policy(candidates, "event_leaders_asset_cap").accepted.sum() == 6


def test_v7_and_v8_are_independent_alternative_portfolios():
    member_v7 = _member("source-v7", "event-v7", "BTC", "2024-01-01T00:00Z")
    member_v8 = _member("source-v8", "event-v8", "BTC", "2024-01-01T00:00Z")
    member_v8["arm"] = "v8"
    trade_v7 = _trade("source-v7", "2024-01-01T00:00Z", "2024-01-01T02:00Z")
    trade_v8 = _trade("source-v8", "2024-01-01T00:00Z", "2024-01-01T02:00Z")
    trade_v8["arm"] = "v8"
    audit = apply_policy(prepare_candidates(_members([member_v7, member_v8]), _trades([trade_v7, trade_v8])), "event_leaders_asset_cap")
    assert audit.accepted.sum() == 2


def test_unmapped_receipt_is_audited_as_blocked_and_never_scored_as_a_loss():
    members = _members([_member("missing", "event-missing", "BTC", "2024-01-01T00:00Z")])
    trades = _trades([])
    candidates = prepare_candidates(members, trades)
    audit = apply_policy(candidates, "source_confirmations")
    assert not audit.iloc[0].accepted
    assert audit.iloc[0].blocked_reason == "frozen_outcome_unavailable"
    row = summarize_policy(audit, candidates, "source_confirmations").query("arm == 'v7' and period == 'development'").iloc[0]
    assert row.blocked == 1
    assert row.closed == 0
    assert row.cumulative_net_r == 0.0


def test_changed_trade_receipt_fails_before_portfolio_parse(tmp_path, monkeypatch):
    cross = tmp_path / "cross"
    (cross / "results").mkdir(parents=True)
    monkeypatch.setattr(study, "CROSS_RESULTS", cross / "results")
    streams = tmp_path / "replay" / "streams"
    streams.mkdir(parents=True)
    path = streams / "one.trades.csv.gz"
    payload = gzip.compress(b"trade_id\n", mtime=0)
    path.write_bytes(payload)
    entry = {"relative_path": "streams/one.trades.csv.gz", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    ledger = {"receipts": [entry]}
    ledger_path = cross / "input_receipts.json"
    ledger_path.write_text(json.dumps(ledger))
    config = {
        "cross_input_receipts_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
        "trade_receipt_aggregate_sha256": hashlib.sha256(json.dumps([entry], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "expected_trade_receipts": 1,
    }
    assert study.verify_trade_receipts(streams, config)
    path.write_bytes(gzip.compress(b"trade_id\nchanged\n", mtime=0))
    with pytest.raises(ValueError, match="receipt identity"):
        study.verify_trade_receipts(streams, config)
