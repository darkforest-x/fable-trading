"""Preserve the independently executed IMACD-YOLO saved-ledger audit.

Source: the read-only review executed on 2026-09-08 after the frozen V1 run.
This module preserves those same 25 checks; it does not rerun the detector,
read OHLCV, calculate trade outcomes, or import the pilot selection code.
Inputs are candidates.csv, decisions.csv, the BTC/ETH proposal and decision
ledgers, and results/summary.json. Hash validation reads only saved ledgers.

The bar-clock check derives timestamps from the saved parsed_first and 15m
index offsets; it does not independently check raw source continuity. Saved
ledgers do not contain per-bar md or first_invalid_i. Therefore the nine
reported invalidations and direction-pool clipping at invalidation cannot be
independently replayed here. Accepted events' first same-direction match is
checked without md: any earlier invalidation would also forbid that accepted
event under the reviewed permanent-invalidation rule.

The shuffled-direction null is independently recomputed from saved can_long
and can_short flags. It tests conditional direction agreement only, not
profitability, genuine launch accuracy, or a reduction in false alerts. Price
displacement is recomputed from saved prices; their OHLCV origin is not read.

Run only after this audit source has been committed. The command writes
results/independent_review.json for this experiment and no other artifact.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/imacd_yolo_confirmation_20260908_v1"
RESULTS = ROOT / (
    "experiments/active/exp-imacd-yolo-confirmation-20260908-v1/results"
)


def verify_saved(data_dir: Path = DATA, results_dir: Path = RESULTS) -> dict:
    """Recompute the 25 previously executed checks from saved pilot ledgers."""
    dat = Path(data_dir)
    results = Path(results_dir)
    summary = json.loads((results / "summary.json").read_text())
    decisions = pd.read_csv(dat / "decisions.csv")
    candidates = pd.read_csv(dat / "candidates.csv")
    proposals = pd.concat(
        [pd.read_csv(dat / f"{symbol}_proposals.csv.gz")
         for symbol in ("BTC", "ETH")],
        ignore_index=True,
    )
    checks = {}
    checks["saved_sha"] = all(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
        for name, expected in summary["files"].items()
    )
    checks["unique_ids"] = (
        not decisions.event_id.duplicated().any()
        and not proposals.detection_id.duplicated().any()
    )
    checks["candidate_ledger_identical"] = candidates.equals(
        decisions[candidates.columns]
    )
    checks["per_symbol_decisions_same"] = all(
        decisions[decisions.symbol.eq(symbol)].reset_index(drop=True).equals(
            pd.read_csv(dat / f"{symbol}_decisions.csv")
        )
        for symbol in ("BTC", "ETH")
    )
    checks["all_complete"] = decisions.complete_followup.all()
    checks["candidate_calendar"] = pd.to_datetime(
        decisions.signal_available_at, utc=True
    ).between(
        pd.Timestamp(summary["start"]),
        pd.Timestamp(summary["end_exclusive"]),
        inclusive="left",
    ).all()
    checks["setup_length"] = (
        (decisions.signal_i - decisions.setup_start_i).eq(decisions.setup_bars).all()
        and decisions.setup_bars.ge(12).all()
    )
    checks["signal_close_clock"] = (
        pd.to_datetime(decisions.signal_available_at, utc=True)
        - pd.to_datetime(decisions.signal_open_at, utc=True)
    ).eq(pd.Timedelta(minutes=15)).all()
    checks["proposal_geometry"] = (
        (proposals.core_end_i - proposals.core_start_i + 1)
        .eq(proposals.core_length_bars).all()
        and (proposals.window_end_i - proposals.core_end_i)
        .eq(proposals.confirmation_bars).all()
        and proposals.window_len.isin([18, 19]).all()
        and (proposals.window_end_i - proposals.window_start_i + 1)
        .eq(proposals.window_len).all()
        and proposals.core_start_i.ge(proposals.window_start_i).all()
        and proposals.core_end_i.le(proposals.window_end_i).all()
    )
    checks["structural_flag"] = (
        proposals.structural_pass
        == (proposals.core_length_bars.isin([4, 5])
            & proposals.confirmation_bars.between(2, 9))
    ).all()
    checks["model_values"] = (
        proposals.confidence.between(.25, 1).all()
        and proposals.direction.isin(["long", "short"]).all()
    )
    for symbol in ("BTC", "ETH"):
        boxes = proposals[proposals.symbol.eq(symbol)]
        events = decisions[decisions.symbol.eq(symbol)]
        metadata = summary["inputs"][symbol]
        first = pd.Timestamp(metadata["parsed_first"])
        checks[symbol + "_proposal_clock"] = (
            pd.to_datetime(boxes.available_at, utc=True)
            == first + pd.to_timedelta((boxes.window_end_i + 1) * 15, unit="min")
        ).all()
        checks[symbol + "_candidate_clock"] = (
            pd.to_datetime(events.signal_open_at, utc=True)
            == first + pd.to_timedelta(events.signal_i * 15, unit="min")
        ).all()
        endpoints = {int(i) + k for i in events.signal_i for k in range(10)}
        checks[symbol + "_endpoints_stats"] = (
            metadata["candidate_endpoints"] == len(endpoints)
            and metadata["windows_scored"] == 2 * len(endpoints)
            and boxes.window_end_i.isin(endpoints).all()
            and metadata["raw_boxes"] == len(boxes)
            and metadata["structural_boxes"] == int(boxes.structural_pass.sum())
            and metadata["windows_with_boxes"]
            == len(boxes[["window_end_i", "window_len"]].drop_duplicates())
        )

    accepted = decisions[decisions.status.eq("confirmed")]
    first_fail, other_fail, expiry_fail = [], [], []
    for event in decisions.itertuples():
        pool = proposals[
            proposals.symbol.eq(event.symbol)
            & proposals.structural_pass
            & proposals.window_end_i.between(event.signal_i, event.signal_i + 9)
            & proposals.core_start_i.le(event.signal_i)
            & proposals.core_end_i.ge(event.setup_start_i)
        ].copy()
        pool = pool.sort_values(
            ["window_end_i", "confidence", "window_len", "detection_id"],
            ascending=[True, False, True, True],
        )
        if event.status == "expired" and (
            bool(pool.direction.eq("long").any()) != event.can_long
            or bool(pool.direction.eq("short").any()) != event.can_short
        ):
            expiry_fail.append(event.event_id)
        if event.status != "confirmed":
            continue
        same = pool[pool.direction.eq("long" if event.side == 1 else "short")]
        if same.empty or same.iloc[0].detection_id != event.model_detection_id:
            first_fail.append(event.event_id)
        box = proposals[
            proposals.detection_id.eq(event.model_detection_id)
        ].iloc[0]
        overlap = (
            min(int(box.core_end_i), event.signal_i)
            - max(int(box.core_start_i), event.setup_start_i) + 1
        )
        good = (
            event.confirmation_i == box.window_end_i
            == event.signal_i + event.delay_bars
            and 0 <= event.delay_bars <= 9
            and pd.Timestamp(box.available_at)
            == pd.Timestamp(event.confirmation_available_at)
            == pd.Timestamp(event.signal_available_at)
            + pd.Timedelta(minutes=15 * event.delay_bars)
            and overlap >= 1
            and overlap == event.overlap_bars
            and np.isclose(overlap / box.core_length_bars, event.core_overlap_fraction)
            and event.core_end_from_arrow_bars == box.core_end_i - event.signal_i
            and event.core_start_i == box.core_start_i
            and event.core_end_i == box.core_end_i
            and np.isclose(event.model_confidence, box.confidence)
            and np.isclose(
                event.displacement_bp,
                event.side * (
                    event.confirmed_next_open / event.baseline_next_open - 1
                ) * 10000,
            )
        )
        if not good:
            other_fail.append(event.event_id)
    checks["first_direction_match"] = not first_fail
    checks["confirmed_clock_overlap_displacement"] = not other_fail
    checks["expired_pool_flags"] = not expiry_fail
    checks["same_close_prices_equal"] = np.allclose(
        accepted.loc[accepted.delay_bars.eq(0), "baseline_next_open"],
        accepted.loc[accepted.delay_bars.eq(0), "confirmed_next_open"],
    )
    for row in summary["table"]:
        events = decisions[decisions.symbol.eq(row["symbol"])]
        kept = events[events.status.eq("confirmed")]
        checks[row["symbol"] + "_table"] = (
            row["arrows"] == len(events)
            and row["confirmed"] == len(kept)
            and row["complete"] == int(events.complete_followup.sum())
            and row["invalidated"] == int(events.status.eq("invalidated").sum())
            and row["expired"] == int(events.status.eq("expired").sum())
            and row["censored"] == int(events.status.eq("censored_end").sum())
            and np.isclose(row["pass_rate_pct"], 100 * len(kept) / len(events))
            and np.isclose(row["delay_median_minutes"], kept.delay_bars.median() * 15)
            and np.isclose(row["displacement_median_bp"], kept.displacement_bp.median())
            and np.isclose(row["displacement_max_bp"], kept.displacement_bp.max())
        )

    # Reimplement the specified conditional shuffle from saved pool flags.
    full = decisions[decisions.complete_followup].copy().reset_index(drop=True)
    side = full.side.to_numpy()
    has_long = full.can_long.to_numpy()
    has_short = full.can_short.to_numpy()
    permutations = summary["direction_null"]["permutations"]
    groups = full.groupby([
        full.symbol,
        pd.to_datetime(full.signal_available_at, utc=True).dt.strftime("%Y-%m"),
    ]).indices
    rng = np.random.default_rng(summary["direction_null"]["seed"])
    counts = []
    for _ in range(permutations):
        shuffled = side.copy()
        for indexes in groups.values():
            shuffled[indexes] = rng.permutation(side[indexes])
        counts.append(int((
            ((shuffled == 1) & has_long) | ((shuffled == -1) & has_short)
        ).sum()))
    counts = np.array(counts)
    observed = int((((side == 1) & has_long) | ((side == -1) & has_short)).sum())
    null = dict(
        observed=observed,
        null_mean=float(counts.mean()),
        null_sd=float(counts.std()),
        p_one_sided=float((1 + (counts >= observed).sum()) / (permutations + 1)),
    )
    checks["null_arithmetic"] = (
        all(np.isclose(value, summary["direction_null"][key])
            for key, value in null.items())
        and observed == len(accepted)
    )
    checks["core_reuse"] = (
        int(accepted.core_identity.duplicated().sum())
        == summary["reused_core_confirmations"] == 0
    )
    return dict(
        checks={key: bool(value) for key, value in checks.items()},
        n_checks=len(checks),
        all_passed=bool(all(checks.values())),
        n_events=len(decisions),
        n_proposals=len(proposals),
        statuses=decisions.status.value_counts().to_dict(),
        delays=accepted.delay_bars.value_counts().sort_index().to_dict(),
        confirmed_overlap=dict(
            min_bars=float(accepted.overlap_bars.min()),
            min_fraction=float(accepted.core_overlap_fraction.min()),
            offset_min=float(accepted.core_end_from_arrow_bars.min()),
            offset_max=float(accepted.core_end_from_arrow_bars.max()),
        ),
        null_recomputed=null,
        first_fail=first_fail,
        other_fail=other_fail,
        expiry_fail=expiry_fail,
        confirmed=accepted[[
            "event_id", "side", "signal_available_at", "delay_bars",
            "displacement_bp", "overlap_bars", "core_overlap_fraction",
        ]].to_dict("records"),
        limitations=[
            "No raw OHLCV, model inference, or independent indicator replay. "
            "Prices are checked arithmetically from saved next-open values only.",
            "Bar clocks use saved parsed_first plus 15m offsets; raw continuity "
            "is not independently re-read.",
            "No per-bar md or first_invalid_i is persisted. The nine reported "
            "invalidations and direction-pool clipping cannot be independently "
            "replayed from these ledgers; code and synthetic tests support them.",
            "Direction permutation is conditional on saved original md-valid "
            "pools. It is an engineering-only association check, not a test of "
            "profitability, launch accuracy, or reduced false alerts.",
            "All 49 candidates in this fixed run have complete follow-up. "
            "The incomplete-follow-up censoring policy is not exercised here.",
        ],
    )


def main() -> None:
    """Write the saved-ledger review after this source has been committed."""
    review = verify_saved()
    path = RESULTS / "independent_review.json"
    path.write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({
        "path": str(path),
        "n_checks": review["n_checks"],
        "all_passed": review["all_passed"],
    }))


if __name__ == "__main__":
    main()
