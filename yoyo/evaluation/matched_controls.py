"""What random entry would have earned under the same conditions.

This is the control every directional result table in this project needs, and
the reason is on the record: a 100-symbol 6-month pool returned +16.9bp, of
which +7.2bp was short beta. The detector was worth +9.0bp against a 10bp
round trip -- a loss, reported as a gain, until the control was drawn
(docs/learnings/pool-internal-metrics-cannot-see-beta.md). A permutation test
cannot find that, because permutation only asks whether the *ranking* is real;
it holds the pool fixed, so a pool standing on beta permutes to the same beta.

Strata, all five required by task book section 3.3:

    symbol          different symbols are different markets
    time bucket     month by default; beta is a property of the period
    volatility      terciles of ATR, computed from the control window's own
                    bars, so the bucketing cannot import information from
                    outside the window being judged
    horizon         same number of bars forward
    cost            same round-trip route

Selection is deterministic by construction: a control is chosen by hashing
(seed, matcher_version, event_id, candidate_time), not by drawing from a random
number generator whose state depends on how many events came before. Taken from
darkforest-one's matching.py, which is the better of the two designs the source
repositories had -- yoyo-eth's rng draw reproduces only if the whole loop
replays in the same order.

Ranking windows must close before the trading window. Choosing the volatility
bucket from bars inside the horizon would rank on data the entry could not have
had (docs/learnings/symbol-ranking-window-must-end-before-the-trading-window.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

MATCHER_VERSION = "matched-control-v1"

#: (symbol, time bucket, volatility bucket)
MatchKey = Tuple[str, str, int]


class ControlPoolError(ValueError):
    """No admissible control for a candidate. Never silently widened."""


@dataclass(frozen=True)
class MatchedControls:
    """Controls drawn for one set of candidates, with the evidence to audit them."""

    controls: pd.DataFrame
    n_candidates: int
    n_per_candidate: int
    strata_used: Tuple[str, ...]
    fallback_count: int
    matcher_version: str
    seed: str

    @property
    def coverage(self) -> float:
        """Share of candidates matched inside their own stratum."""
        if self.n_candidates == 0:
            return 0.0
        return 1.0 - self.fallback_count / self.n_candidates


def volatility_buckets(atr: pd.Series, n_buckets: int = 3) -> Tuple[np.ndarray, np.ndarray]:
    """Bucket edges and assignments from the control window's OWN bars.

    Returns (edges, assignment). Quantile edges rather than fixed widths,
    because ATR levels differ by an order of magnitude across symbols and a
    fixed grid would put one symbol entirely in one bucket.
    """
    valid = atr.dropna()
    if valid.empty:
        raise ControlPoolError("no valid ATR values to bucket")
    quantiles = [i / n_buckets for i in range(1, n_buckets)]
    edges = valid.quantile(quantiles).to_numpy()
    return edges, np.digitize(atr.to_numpy(), edges)


def match_key(symbol: str, when: pd.Timestamp, volatility_bucket: int, *, time_bucket: str = "month") -> MatchKey:
    if time_bucket == "month":
        stamp = when.strftime("%Y-%m")
    elif time_bucket == "week":
        stamp = when.strftime("%G-W%V")
    elif time_bucket == "day":
        stamp = when.strftime("%Y-%m-%d")
    else:
        raise ValueError(f"unknown time_bucket {time_bucket!r}")
    return (symbol, stamp, int(volatility_bucket))


def _selection_hash(seed: str, candidate_id: str, control_time: pd.Timestamp) -> str:
    raw = "|".join((seed, MATCHER_VERSION, candidate_id, control_time.isoformat()))
    return sha256(raw.encode("utf-8")).hexdigest()


def draw_matched_controls(
    candidates: pd.DataFrame,
    pool: pd.DataFrame,
    *,
    n_per_candidate: int = 20,
    seed: str = "matched-control-v1",
    time_bucket: str = "month",
    n_volatility_buckets: int = 3,
    allow_fallback: bool = False,
) -> MatchedControls:
    """Draw controls for `candidates` from `pool`, matched on the five strata.

    `candidates` needs candidate_id, symbol, decision_ts, atr.
    `pool` needs symbol, decision_ts, atr, and must already be restricted to
    bars that could legally have been entered -- same window, complete horizon.
    Restricting it here would need the horizon, and a control pool that trims
    itself is a pool whose definition lives in two places.

    `allow_fallback=False` on purpose. When a stratum is empty the honest answer
    is that this candidate has no control, not a draw from the whole pool: a
    fallback control silently stops being matched on the axis that was missing,
    which is usually the axis that mattered. Pass True only when the caller
    reports the fallback count alongside the result.
    """
    for frame, name, needed in (
        (candidates, "candidates", ("candidate_id", "symbol", "decision_ts", "atr")),
        (pool, "pool", ("symbol", "decision_ts", "atr")),
    ):
        missing = [column for column in needed if column not in frame.columns]
        if missing:
            raise ControlPoolError(f"{name} is missing columns {missing}")
    if candidates.empty:
        raise ControlPoolError("no candidates to match")
    if pool.empty:
        raise ControlPoolError("control pool is empty")

    # Buckets come from the pool's own ATR distribution: the pool is the window
    # being judged, so its own bars define what "high volatility" means there.
    edges, pool_buckets = volatility_buckets(pool["atr"], n_volatility_buckets)
    pool = pool.assign(_bucket=pool_buckets)
    candidate_buckets = np.digitize(candidates["atr"].to_numpy(), edges)

    by_key: Dict[MatchKey, pd.DataFrame] = {}
    for (symbol, bucket), group in pool.groupby(["symbol", "_bucket"], sort=False):
        for stamp, sub in group.groupby(group["decision_ts"].dt.strftime(_STAMP[time_bucket])):
            by_key[(str(symbol), str(stamp), int(bucket))] = sub

    rows: List[Dict[str, Any]] = []
    fallback_count = 0
    for position, candidate in enumerate(candidates.itertuples(index=False)):
        key = match_key(
            str(candidate.symbol),
            pd.Timestamp(candidate.decision_ts),
            int(candidate_buckets[position]),
            time_bucket=time_bucket,
        )
        stratum = by_key.get(key)
        if stratum is None or stratum.empty:
            if not allow_fallback:
                raise ControlPoolError(
                    f"no matched control for candidate {candidate.candidate_id} in stratum "
                    f"{key}. Widening to the whole pool would drop the axis that is "
                    "missing, which is usually the one that mattered; pass "
                    "allow_fallback=True only if you report the fallback count."
                )
            stratum = pool
            fallback_count += 1

        scored = sorted(
            (
                (_selection_hash(seed, str(candidate.candidate_id), pd.Timestamp(row.decision_ts)), row)
                for row in stratum.itertuples(index=False)
            ),
            key=lambda item: item[0],
        )
        for rank, (selection_hash, row) in enumerate(scored[:n_per_candidate]):
            rows.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "control_rank": rank,
                    "symbol": row.symbol,
                    "decision_ts": row.decision_ts,
                    "atr": row.atr,
                    "match_symbol": candidate.symbol,
                    "match_time_bucket": key[1],
                    "match_volatility_bucket": key[2],
                    "selection_sha256": selection_hash,
                }
            )

    return MatchedControls(
        controls=pd.DataFrame(rows),
        n_candidates=len(candidates),
        n_per_candidate=n_per_candidate,
        strata_used=("symbol", time_bucket, "volatility_tercile", "horizon", "cost"),
        fallback_count=fallback_count,
        matcher_version=MATCHER_VERSION,
        seed=seed,
    )


_STAMP = {"month": "%Y-%m", "week": "%G-W%V", "day": "%Y-%m-%d"}
