"""Deterministic observation ranking of supplied point-in-time snapshots.

Inputs are daily completed-close 7/30d returns and SMA20/50 flags, a 4h L1 setup
plain dictionary, a 15m auxiliary trigger dictionary, externally supplied
sector/event metadata, and explicit liquidity eligibility. The auxiliary
trigger contributes no score; its available/non-invalidated state is required
before a candidate can be ready for observation. It need not match the 4h
setup shape. This layer imports no detector or execution layer.
Ranking uses only the same available snapshot as the benchmark; no future
return, current leaderboard backfill, labels, training or probability is used.

The score is an unvalidated 0..1 heuristic: 0.25 each for clipped 7/30d relative
returns, 0.10 each for the two trend flags, 0.20 for setup quality and 0.10 for
liquidity. Unknown event risk multiplies score by 0.9. It is not win probability
or expected return. All inputs remain in output, including blocked candidates.
"""
from __future__ import annotations

from copy import deepcopy

from yoyo.data.rotation_features import finite_number


SETUP_STATES = {"watch", "breakout", "pullback", "extended", "invalidated", "insufficient_data"}
OBSERVABLE_TRIGGER_STATES = {"watch", "breakout", "pullback", "extended"}


def _relative(asset, benchmark):
    asset, benchmark = finite_number(asset), finite_number(benchmark)
    if asset is None or benchmark is None or asset <= -1 or benchmark <= -1:
        return None
    return finite_number((1 + asset) / (1 + benchmark) - 1)


def _component(value, span):
    return 0.0 if value is None else min(1.0, max(0.0, 0.5 + value / span))


def rank_candidates(candidates: list[dict], *, regime: dict) -> list[dict]:
    """Retain every symbol with an explicit research/blocked status and reason.

    ``status`` preserves its setup, except hard blockers become ``avoid``.
    ``review_status=ready`` means eligible for human observation only. Unknown
    events/regime, missing relative-strength data and non-trigger setups stay
    review/watch; event blocks, liquidity failures, insufficient/invalidated
    data, a defensive regime or mismatched observation times cannot be ready.
    Missing, insufficient or invalidated 15m auxiliary observations require
    review while preserving the 4h status and score; a valid 15m watch is enough
    for this auxiliary completeness check and need not itself be a breakout.
    """
    output = []
    symbols = [str(item.get("symbol", "")) for item in candidates]
    if any(not symbol for symbol in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError("candidate symbols must be nonempty and unique")
    for candidate in candidates:
        item = deepcopy(candidate)
        daily, setup = item.get("daily") or {}, item.get("setup") or {}
        trigger = item.get("trigger")
        trigger_state = trigger.get("state") if isinstance(trigger, dict) else None
        trigger_observable = isinstance(trigger_state, str) and trigger_state in OBSERVABLE_TRIGGER_STATES
        state = setup.get("state", "insufficient_data")
        if state not in SETUP_STATES:
            state = "insufficient_data"
        rs7 = _relative(daily.get("return_7d"), regime.get("btc_return_7d"))
        rs30 = _relative(daily.get("return_30d"), regime.get("btc_return_30d"))
        liquidity = item.get("liquidity_eligible") is True
        event = item.get("event_risk", "unknown")
        reasons = list(daily.get("reasons", [])) + list(setup.get("reasons", []))
        if not isinstance(trigger, dict) or not trigger or trigger_state is None:
            reasons.append("trigger_15m_missing")
        elif isinstance(trigger_state, str) and trigger_state in {"insufficient_data", "invalidated"}:
            reasons.append(f"trigger_15m_{trigger_state}")
        elif not trigger_observable:
            reasons.append("trigger_15m_unknown_state")
        blocked = []
        if not liquidity:
            blocked.append("liquidity_not_eligible")
        if event == "block":
            blocked.append("blocking_event_risk")
        if daily.get("state") != "ok":
            blocked.append("daily_data_insufficient")
        if state in {"invalidated", "insufficient_data"}:
            blocked.append(f"setup_{state}")
        if regime.get("state") == "defensive":
            blocked.append("market_regime_defensive")
        observed = regime.get("observed_at")
        if observed is not None and daily.get("last_bar_at") != observed:
            blocked.append("daily_and_benchmark_observation_times_differ")
        if not item.get("sector") or item.get("sector") == "unknown":
            reasons.append("sector_metadata_unknown_not_invented")
        if event != "clear" and event != "block":
            reasons.append("event_risk_unknown_needs_manual_review")
        if rs7 is None or rs30 is None:
            reasons.append("relative_strength_incomplete")
        if regime.get("state") not in {"supportive", "mixed", "defensive"}:
            reasons.append("market_regime_unknown")
        score = (
            0.25 * _component(rs7, 0.20) + 0.25 * _component(rs30, 0.40)
            + 0.10 * (daily.get("above_sma20") is True)
            + 0.10 * (daily.get("above_sma50") is True)
            + {"breakout": 0.20, "pullback": 0.20, "watch": 0.06, "extended": 0.02}.get(state, 0)
            + 0.10 * liquidity
        )
        if event not in {"clear", "block"}:
            score *= 0.9
        review_status = "review"
        if blocked:
            review_status = "blocked"
        elif (event == "clear" and regime.get("state") in {"supportive", "mixed"}
              and rs7 is not None and rs30 is not None and state in {"breakout", "pullback"}
              and trigger_observable):
            review_status = "ready"
        item.update({
            "score": finite_number(score), "rs_7d": rs7, "rs_30d": rs30,
            "status": "avoid" if blocked else state, "review_status": review_status,
            "reasons": list(dict.fromkeys(reasons + blocked + ["heuristic_score_not_win_probability"])),
        })
        output.append(item)
    output.sort(key=lambda row: (row["review_status"] == "blocked", -row["score"], row["symbol"]))
    for rank, item in enumerate(output, 1):
        item["rank"] = rank
    return output
