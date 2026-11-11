"""Build a causal, market-level population table for the blind first-week review.

This reads the frozen signal census, its causal daily-first-touch catalogue, and
the shared 1h/12h source candles. It emits descriptive path observations only:
there is no stop, account, position, or simulated-trade logic.

Run from the repository root with::

    python3 experiments/active/exp-month-firstweek-blind-review-20261111-v1/population.py

Outputs are written beside the source signal file under ``blind_review_v1``.
The hand-picked photo manifest is read with only its key columns, so the
builder does not pull existing outcome annotations into the blind-review set.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import sys

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE_EXPERIMENT = HERE.parent / "exp-alt-isolated-12h-firstweek-wicks-20261111-v1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SOURCE_EXPERIMENT))

from all_contract_touches import DEST as CATALOGUE_ROOT  # noqa: E402
from census12 import BASE, HOUR, OUT as CENSUS_OUT, read_raw  # noqa: E402
from scan import CUTOFF  # noqa: E402
from strategy_review import add_rules  # noqa: E402
from strategy_signals import OUT as SIGNALS_OUT  # noqa: E402


OUT = SIGNALS_OUT
DEST = OUT / "blind_review_v1"
BAR12 = 12 * HOUR
DAY45 = 45 * 24 * HOUR
EXPECTED_SIGNALS = 47_010

KEY = ["symbol", "month", "side"]
RULES = [
    ("ordinary_first_12h", "ordinary"),
    ("A_first_wick", "first_wick"),
    ("B_retest", "retest"),
    ("A_or_B_earliest", "strict_union"),
]
SIDE_MAP = {
    "下轨": "long",
    "上轨": "short",
    "下": "long",
    "上": "short",
    "lower": "long",
    "upper": "short",
    "long": "long",
    "short": "short",
}


def _local_hour_to_ms(value: object) -> int:
    """Convert a naive Beijing-local hourly label or timestamp to UTC ms."""
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("Asia/Shanghai")
    return int(stamp.tz_convert("UTC").value // 1_000_000)


def _beijing_label(ms: object) -> str | None:
    if pd.isna(ms):
        return None
    return (
        pd.to_datetime(int(ms), unit="ms", utc=True)
        .tz_convert("Asia/Shanghai")
        .strftime("%Y-%m-%d %H:%M")
    )


def _read_scope(signals: pd.DataFrame) -> tuple[pd.DataFrame, set[tuple[str, str]]]:
    """Use all symbol-month anchors in the frozen 47,010-row signal domain.

    The older coverage file is diagnostic only: it covers fewer contracts and
    must not filter the expanded signal source.
    """
    domain = signals[["symbol", "month"]].drop_duplicates().sort_values(["symbol", "month"])
    return domain, set(map(tuple, domain.itertuples(index=False, name=None)))


def _read_daily_touch_catalogue(valid_months: set[tuple[str, str]]) -> pd.DataFrame:
    """Load touch coverage only; ignore the catalogue's future-outcome columns."""
    path = CATALOGUE_ROOT / "catalogue" / "所有合约_每日首次触轨.csv.gz"
    columns = ["币安合约", "纽约区间月", "首次触价小时（北京）", "方向", "轨价"]
    catalogue = pd.read_csv(path, usecols=columns)
    catalogue = catalogue.rename(
        columns={
            "币安合约": "symbol",
            "纽约区间月": "month",
            "首次触价小时（北京）": "touch_hour_beijing",
            "方向": "source_side",
            "轨价": "catalogue_rail",
        }
    )
    catalogue["symbol"] = catalogue.symbol.astype(str)
    catalogue["month"] = catalogue.month.astype(str)
    catalogue["side"] = catalogue.source_side.map(SIDE_MAP)
    catalogue = catalogue[catalogue.side.notna()].copy()
    catalogue = catalogue[
        catalogue[["symbol", "month"]].apply(tuple, axis=1).isin(valid_months)
    ].copy()
    catalogue["touch_ms"] = catalogue.touch_hour_beijing.map(_local_hour_to_ms)
    catalogue["catalogue_rail"] = pd.to_numeric(catalogue.catalogue_rail, errors="coerce")
    catalogue = catalogue.sort_values([*KEY, "touch_ms"])
    first = catalogue.groupby(KEY, as_index=False, sort=False).first()
    counts = catalogue.groupby(KEY, sort=False).size().rename("catalogue_touch_days").reset_index()
    return first.merge(counts, on=KEY, how="left", validate="one_to_one")


def _read_signals() -> pd.DataFrame:
    path = SIGNALS_OUT / "signals.csv.gz"
    signals = pd.read_csv(path)
    if len(signals) != EXPECTED_SIGNALS:
        raise ValueError(
            f"expected the frozen {EXPECTED_SIGNALS:,}-row signal file, found {len(signals):,}: {path}"
        )
    signals = add_rules(signals)
    signals["symbol"] = signals.symbol.astype(str)
    signals["month"] = signals.month.astype(str)
    signals["side"] = signals.side.map(SIDE_MAP)
    if signals.side.isna().any():
        raise ValueError("the frozen signal source contains an unrecognized side")
    for col in ["entry_ms", "touch_ms", "rank_12h"]:
        signals[col] = pd.to_numeric(signals[col], errors="raise").astype("int64")
    for col in ["entry_price", "rail"]:
        signals[col] = pd.to_numeric(signals[col], errors="coerce")
    return signals.sort_values([*KEY, "entry_ms", "touch_ms", "rank_12h"])


def _read_photo_keys(valid_months: set[tuple[str, str]]) -> pd.DataFrame:
    """Read only the 15 source photo keys, never their reviewed outcome fields."""
    path = SOURCE_EXPERIMENT / "原图15张逐项核对.csv"
    selected = pd.read_csv(
        path,
        usecols=["图片", "合约", "区间月（纽约）", "侧"],
    ).rename(
        columns={
            "图片": "photo_id",
            "合约": "symbol",
            "区间月（纽约）": "month",
            "侧": "source_side",
        }
    )
    if len(selected) != 15:
        raise ValueError(f"expected 15 hand-picked photo keys, found {len(selected)}: {path}")
    selected["symbol"] = selected.symbol.astype(str)
    selected["month"] = selected.month.astype(str)
    selected["side"] = selected.source_side.map(SIDE_MAP)
    if selected.side.isna().any() or selected.duplicated(KEY).any():
        raise ValueError("the 15 hand-picked photo keys must have known sides and unique symbol-month-side keys")
    selected["selection_source"] = "handpicked_photo15"

    extra = pd.DataFrame(
        [
            {
                "photo_id": "extra_1000PEPE_2025-06_long",
                "symbol": "1000PEPEUSDT",
                "month": "2025-06",
                "source_side": "long",
                "side": "long",
                "selection_source": "explicit_extra",
            }
        ]
    )
    result = pd.concat([selected, extra], ignore_index=True, sort=False)
    result["in_valid_scope"] = result[["symbol", "month"]].apply(tuple, axis=1).isin(valid_months)
    return result


def _find_pre_signal_moves(
    raw: np.ndarray,
    touch_ms: int | None,
    confirmation_ms: int,
    side: str,
    rail: float,
) -> dict[str, object]:
    """Measure favorable 5%/20% moves from first touch hour to confirmation.

    The first touch hour is included. A wick-only threshold in that H1 candle
    remains ambiguous; a close already beyond the threshold or any later H1
    reaching it confirms that the move occurred before signal confirmation.
    """
    out: dict[str, object] = {
        "pre_signal_5_status": "unknown_no_touch_hour",
        "pre_signal_5_hour_ms": None,
        "pre_signal_20_status": "unknown_no_touch_hour",
        "pre_signal_20_hour_ms": None,
        "pre_signal_path_hours": 0,
        "pre_signal_path_gap": False,
    }
    if touch_ms is None or not len(raw) or not np.isfinite(rail) or rail <= 0:
        return out
    times = raw[:, 0].astype("int64")
    start = int(np.searchsorted(times, touch_ms))
    if start >= len(raw) or times[start] != touch_ms:
        out["pre_signal_5_status"] = "unknown_missing_touch_hour"
        out["pre_signal_20_status"] = "unknown_missing_touch_hour"
        return out
    end = int(np.searchsorted(times, confirmation_ms, side="left"))
    if end <= start:
        out["pre_signal_5_status"] = "not_reached_before_signal"
        out["pre_signal_20_status"] = "not_reached_before_signal"
        return out

    sign = 1 if side == "long" else -1
    first_5: int | None = None
    first_20: int | None = None
    later_hour_hits: dict[str, int | None] = {"5": None, "20": None}
    touch_hour_hits = {"5": False, "20": False}
    touch_hour_close_hits = {"5": False, "20": False}
    gap = False
    expected = touch_ms
    visited = 0
    for idx in range(start, end):
        if int(times[idx]) != expected:
            gap = True
            break
        row = raw[idx]
        favorable_extreme = ((row[2] if side == "long" else row[3]) / rail - 1) * sign * 100
        favorable_close = (row[4] / rail - 1) * sign * 100
        if idx == start:
            touch_hour_hits["5"] = favorable_extreme >= 5
            touch_hour_hits["20"] = favorable_extreme >= 20
            touch_hour_close_hits["5"] = favorable_close >= 5
            touch_hour_close_hits["20"] = favorable_close >= 20
        if first_5 is None and favorable_extreme >= 5:
            first_5 = idx
        if idx > start and favorable_extreme >= 5 and later_hour_hits["5"] is None:
            later_hour_hits["5"] = idx
        if first_20 is None and favorable_extreme >= 20:
            first_20 = idx
        if idx > start and favorable_extreme >= 20 and later_hour_hits["20"] is None:
            later_hour_hits["20"] = idx
        visited += 1
        expected += HOUR
    out["pre_signal_path_hours"] = visited
    out["pre_signal_path_gap"] = gap
    for label, hit in [("5", first_5), ("20", first_20)]:
        if touch_hour_close_hits[label]:
            out[f"pre_signal_{label}_status"] = "reached_before_signal"
            out[f"pre_signal_{label}_hour_ms"] = int(times[start])
        elif later_hour_hits[label] is not None:
            out[f"pre_signal_{label}_status"] = "reached_before_signal"
            out[f"pre_signal_{label}_hour_ms"] = int(times[later_hour_hits[label]])
        elif hit == start:
            out[f"pre_signal_{label}_status"] = "ambiguous_same_touch_hour"
            out[f"pre_signal_{label}_hour_ms"] = int(times[start])
        elif gap:
            out[f"pre_signal_{label}_status"] = "unknown_gap_before_signal"
        else:
            out[f"pre_signal_{label}_status"] = "not_reached_before_signal"
    return out


def _observe_post_signal(
    raw: np.ndarray,
    entry_ms: int,
    entry_price: float,
    confirmation_ms: int,
    side: str,
    rail: float,
) -> dict[str, object]:
    """Observe up to 45 continuous H1 days for rail-20% target or 12h invalidation."""
    result: dict[str, object] = {
        "path_status": "unknown_missing_entry_hour",
        "path_observed_hours": 0,
        "path_complete_45d": False,
        "path_end_ms": None,
        "target20_hour_ms": None,
        "invalidation_12h_close_ms": None,
        "same_12h_close_invalidated_after_target": False,
        "same_12h_close_timing_note": "not_applicable",
        "mae_before_terminal_pct": np.nan,
        "mae_includes_terminal_hour": False,
        "mae_observation_end_hour_ms": None,
        "entry_adverse_5_status": "unknown_missing_entry_hour",
        "entry_adverse_5_hour_ms": None,
        "entry_5_vs_target20_order": "unknown_missing_entry_hour",
    }
    if (
        not len(raw)
        or not np.isfinite(entry_price)
        or entry_price <= 0
        or not np.isfinite(rail)
        or rail <= 0
    ):
        return result
    times = raw[:, 0].astype("int64")
    start = int(np.searchsorted(times, entry_ms))
    if start >= len(raw) or times[start] != entry_ms:
        return result
    if entry_ms != confirmation_ms or confirmation_ms % BAR12:
        result["path_status"] = "unknown_unaligned_signal_time"
        result["entry_adverse_5_status"] = "unknown_unaligned_signal_time"
        result["entry_5_vs_target20_order"] = "unknown_unaligned_signal_time"
        return result

    target_price = rail * (1.2 if side == "long" else 0.8)
    expected = entry_ms
    max_bars = DAY45 // HOUR
    target_idx: int | None = None
    adverse_idx: int | None = None
    invalidation_ms: int | None = None
    invalidation_idx: int | None = None
    same_block_close_invalidated = False
    target_block_close_observed = False
    visited: list[int] = []
    gap = False

    for offset in range(max_bars):
        idx = start + offset
        if idx >= len(raw) or int(times[idx]) != expected:
            gap = True
            break
        row = raw[idx]
        visited.append(idx)
        hit_target = row[2] >= target_price if side == "long" else row[3] <= target_price
        hit_adverse = row[3] <= entry_price * 0.95 if side == "long" else row[2] >= entry_price * 1.05
        if hit_target and target_idx is None:
            target_idx = idx
        if hit_adverse and adverse_idx is None:
            adverse_idx = idx

        close_time = int(times[idx]) + HOUR
        complete_post_signal_12h_close = (close_time % BAR12 == 0) and (offset + 1 >= 12)
        close_outside_rail = (float(row[4]) - rail) * (1 if side == "long" else -1) < 0
        if (
            complete_post_signal_12h_close
            and target_idx is not None
            and times[target_idx] // BAR12 == times[idx] // BAR12
        ):
            target_block_close_observed = True
        if complete_post_signal_12h_close and close_outside_rail:
            invalidation_ms = close_time
            invalidation_idx = idx
            if target_idx is not None and times[target_idx] // BAR12 == times[idx] // BAR12:
                same_block_close_invalidated = True
            break
        # After the target, keep only the remainder of its own UTC 12h candle
        # so a later invalidating close in that same candle is still observed.
        if target_idx is not None and close_time % BAR12 == 0:
            break
        expected += HOUR

    complete_45d = len(visited) == max_bars and not gap
    result["path_observed_hours"] = len(visited)
    result["path_complete_45d"] = bool(complete_45d)
    result["path_end_ms"] = int(times[visited[-1]] + HOUR) if visited else None

    # A target inside the H1 candle that closes an invalidating 12h bar is
    # ordered before that 12h close, as required by the review protocol.
    target_before_invalidation = target_idx is not None and (
        invalidation_ms is None or int(times[target_idx]) + HOUR <= invalidation_ms
    )
    if target_before_invalidation:
        result["path_status"] = "success_target20_before_invalidation"
        result["target20_hour_ms"] = int(times[target_idx])
        if same_block_close_invalidated:
            result["same_12h_close_invalidated_after_target"] = True
            result["invalidation_12h_close_ms"] = invalidation_ms
            result["same_12h_close_timing_note"] = "target_hour_precedes_invalidating_12h_close"
        elif target_block_close_observed:
            result["same_12h_close_timing_note"] = "target_hour_precedes_noninvalidating_12h_close"
        else:
            result["same_12h_close_timing_note"] = "target_seen_but_same_12h_close_incomplete_or_missing"
    elif invalidation_ms is not None:
        result["path_status"] = "invalidated_before_target20"
        result["invalidation_12h_close_ms"] = invalidation_ms
    elif complete_45d:
        result["path_status"] = "complete_45d_no_target_or_invalidation"
    else:
        result["path_status"] = "unknown_cutoff_or_gap"

    event_terminal_idx = target_idx if target_before_invalidation else invalidation_idx
    mae_end_idx = event_terminal_idx if event_terminal_idx is not None else (visited[-1] if visited else None)
    mae_indices = [idx for idx in visited if mae_end_idx is not None and idx <= mae_end_idx]
    if mae_indices:
        if side == "long":
            worst = min(float(raw[idx, 3]) for idx in mae_indices)
            mae = max(0.0, (entry_price - worst) / entry_price * 100)
        else:
            worst = max(float(raw[idx, 2]) for idx in mae_indices)
            mae = max(0.0, (worst - entry_price) / entry_price * 100)
        result["mae_before_terminal_pct"] = mae
        result["mae_includes_terminal_hour"] = event_terminal_idx is not None
        result["mae_observation_end_hour_ms"] = int(times[mae_end_idx])

    if adverse_idx is not None and (event_terminal_idx is None or adverse_idx <= event_terminal_idx):
        result["entry_adverse_5_status"] = (
            "reached_by_terminal" if event_terminal_idx is not None else "reached_in_observed_path"
        )
        result["entry_adverse_5_hour_ms"] = int(times[adverse_idx])
    elif event_terminal_idx is not None:
        result["entry_adverse_5_status"] = "not_reached_by_terminal"
    elif complete_45d:
        result["entry_adverse_5_status"] = "not_reached_in_complete_45d"
    else:
        result["entry_adverse_5_status"] = "unknown_cutoff_or_gap"

    if target_before_invalidation and target_idx is not None:
        if adverse_idx is None or adverse_idx > target_idx:
            result["entry_5_vs_target20_order"] = "target20_before_entry_adverse5"
        elif adverse_idx < target_idx:
            result["entry_5_vs_target20_order"] = "entry_adverse5_before_target20"
        else:
            result["entry_5_vs_target20_order"] = "same_H1_both_touched_order_unknown"
    elif invalidation_ms is not None:
        if adverse_idx is not None and adverse_idx <= invalidation_idx:
            result["entry_5_vs_target20_order"] = "entry_adverse5_before_invalidation_target_not_reached"
        else:
            result["entry_5_vs_target20_order"] = "target20_not_reached_before_invalidation"
    elif adverse_idx is not None:
        result["entry_5_vs_target20_order"] = "entry_adverse5_observed_target20_not_observed"
    elif complete_45d:
        result["entry_5_vs_target20_order"] = "neither_threshold_observed_in_complete_45d"
    else:
        result["entry_5_vs_target20_order"] = "unknown_cutoff_or_gap"
    return result


def _first_candidate(group: pd.DataFrame, kind: str) -> pd.Series | None:
    if group.empty:
        return None
    if kind == "ordinary":
        candidates = group[group.rank_12h.eq(1)]
    else:
        candidates = group[group[kind].eq(1)]
    if candidates.empty:
        return None
    return candidates.sort_values(["entry_ms", "touch_ms", "rank_12h"]).iloc[0]


def _build_events(
    valid_months: set[tuple[str, str]],
    touched: pd.DataFrame,
    signals: pd.DataFrame,
    photo_keys: pd.DataFrame,
) -> pd.DataFrame:
    touch_by_key = {tuple(row[k] for k in KEY): row for row in touched.to_dict("records")}
    signals_by_key = {
        key: frame
        for key, frame in signals.groupby(KEY, sort=False)
    }
    all_keys = set(touch_by_key) | set(signals_by_key)
    all_keys |= {
        tuple(row[k] for k in KEY)
        for row in photo_keys.to_dict("records")
        if bool(row["in_valid_scope"])
    }
    by_symbol: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for key in sorted(all_keys):
        by_symbol[key[0]].append(key)

    rows: list[dict[str, object]] = []
    raw_by_symbol: dict[str, np.ndarray] = {}
    path_cache: dict[tuple[str, int, int, float, float, str], dict[str, object]] = {}
    pre_cache: dict[tuple[str, int, int, float, str], dict[str, object]] = {}

    for symbol, symbol_keys in by_symbol.items():
        raw = read_raw(symbol)
        if len(raw):
            raw = raw[raw[:, 0] + HOUR <= CUTOFF]
            raw_by_symbol[symbol] = raw
        else:
            raw_by_symbol[symbol] = raw

        for key in symbol_keys:
            key_signals = signals_by_key.get(key, pd.DataFrame())
            touch = touch_by_key.get(key)
            touch_ms = int(touch["touch_ms"]) if touch is not None else None
            rail_candidates = []
            if touch is not None and pd.notna(touch.get("catalogue_rail")):
                rail_candidates.append(float(touch["catalogue_rail"]))
            if not key_signals.empty:
                rail_candidates.extend(pd.to_numeric(key_signals.rail, errors="coerce").dropna().astype(float).tolist())
            rail = rail_candidates[0] if rail_candidates else np.nan
            raw = raw_by_symbol[symbol]

            for rule_name, kind in RULES:
                signal = _first_candidate(key_signals, kind)
                base: dict[str, object] = {
                    "symbol": key[0],
                    "month": key[1],
                    "side": key[2],
                    "in_valid_scope": (key[0], key[1]) in valid_months,
                    "catalogue_touch_found": touch is not None,
                    "catalogue_touch_days": int(touch["catalogue_touch_days"]) if touch is not None else 0,
                    "attention_start_ms": touch_ms,
                    "attention_start_hour_beijing": _beijing_label(touch_ms),
                    "rail": rail,
                    "rule": rule_name,
                    "signal_present": signal is not None,
                    "signal_touch_ms": None,
                    "signal_touch_hour_beijing": None,
                    "confirmation_ms": None,
                    "confirmation_hour_beijing": None,
                    "reference_entry_ms": None,
                    "reference_entry_hour_beijing": None,
                    "reference_entry_price": np.nan,
                    "rank_12h": None,
                    "first_wick": None,
                    "retest": None,
                    "ordinary_baseline": None,
                    "candidate_rule_reason": "no_matching_signal",
                    "pre_signal_5_status": "not_applicable_no_signal",
                    "pre_signal_5_hour_ms": None,
                    "pre_signal_20_status": "not_applicable_no_signal",
                    "pre_signal_20_hour_ms": None,
                    "pre_signal_path_hours": 0,
                    "pre_signal_path_gap": False,
                    "path_status": "no_signal",
                }
                if signal is not None:
                    confirmation_ms = int(signal.entry_ms)
                    entry_ms = int(signal.entry_ms)
                    entry_price = float(signal.entry_price)
                    signal_touch_ms = int(signal.touch_ms)
                    signal_rail = float(signal.rail)
                    base.update(
                        {
                            "signal_touch_ms": signal_touch_ms,
                            "signal_touch_hour_beijing": _beijing_label(signal_touch_ms),
                            "confirmation_ms": confirmation_ms,
                            "confirmation_hour_beijing": _beijing_label(confirmation_ms),
                            "reference_entry_ms": entry_ms,
                            "reference_entry_hour_beijing": _beijing_label(entry_ms),
                            "reference_entry_price": entry_price,
                            "rail": signal_rail,
                            "rank_12h": int(signal.rank_12h),
                            "first_wick": int(signal.first_wick),
                            "retest": int(signal.retest),
                            "ordinary_baseline": int(signal.baseline),
                            "candidate_rule_reason": "ordinary_rank1" if kind == "ordinary" else kind,
                        }
                    )
                    pre_key = (symbol, int(touch_ms or -1), confirmation_ms, signal_rail, key[2])
                    if pre_key not in pre_cache:
                        pre_cache[pre_key] = _find_pre_signal_moves(
                            raw, touch_ms, confirmation_ms, key[2], signal_rail
                        )
                    base.update(pre_cache[pre_key])

                    path_key = (symbol, signal_touch_ms, confirmation_ms, entry_price, signal_rail, key[2])
                    if path_key not in path_cache:
                        path_cache[path_key] = _observe_post_signal(
                            raw, entry_ms, entry_price, confirmation_ms, key[2], signal_rail
                        )
                    base.update(path_cache[path_key])
                else:
                    base.update(
                        {
                            "path_observed_hours": 0,
                            "path_complete_45d": False,
                            "path_end_ms": None,
                            "target20_hour_ms": None,
                            "invalidation_12h_close_ms": None,
                            "same_12h_close_invalidated_after_target": False,
                            "mae_before_terminal_pct": np.nan,
                            "mae_includes_terminal_hour": False,
                            "entry_adverse_5_status": "not_applicable_no_signal",
                            "entry_adverse_5_hour_ms": None,
                            "entry_5_vs_target20_order": "not_applicable_no_signal",
                        }
                    )
                rows.append(base)
    return pd.DataFrame(rows)


def _make_summary(events: pd.DataFrame, valid: pd.DataFrame, touched: pd.DataFrame) -> pd.DataFrame:
    """Count rules against both the full valid month-side scope and touch scope."""
    valid_months = valid[["symbol", "month"]].drop_duplicates()
    all_pairs = [
        (str(row.symbol), str(row.month), side)
        for row in valid_months.itertuples(index=False)
        for side in ("long", "short")
    ]
    touched_keys = set(map(tuple, touched[KEY].drop_duplicates().itertuples(index=False, name=None)))
    scopes: list[tuple[str, str, str, set[tuple[str, str, str]], int]] = []
    all_key_set = set(all_pairs)
    scopes.append(("overall", "ALL", "ALL", all_key_set, len(all_key_set)))
    for side in ("long", "short"):
        key_set = {key for key in all_key_set if key[2] == side}
        scopes.append(("side", "ALL", side, key_set, len(key_set)))
    for month in sorted(valid_months.month.astype(str).unique()):
        month_set = {key for key in all_key_set if key[1] == month}
        scopes.append(("month", month, "ALL", month_set, len(month_set)))
        for side in ("long", "short"):
            side_month = {key for key in month_set if key[2] == side}
            scopes.append(("month_side", month, side, side_month, len(side_month)))

    rows: list[dict[str, object]] = []
    for grouping, month, side, key_set, denominator in scopes:
        if grouping == "overall":
            mask = pd.Series(True, index=events.index)
        else:
            mask = pd.Series(True, index=events.index)
            if month != "ALL":
                mask &= events.month.astype(str).eq(month)
            if side != "ALL":
                mask &= events.side.eq(side)
        subset = events[mask]
        for rule_name, _ in RULES:
            current = subset[subset.rule.eq(rule_name)]
            present = current[current.signal_present.eq(True)]
            statuses = present.path_status.value_counts().to_dict()
            pre5 = present.pre_signal_5_status.value_counts().to_dict()
            pre20 = present.pre_signal_20_status.value_counts().to_dict()
            candidate_keys = set(
                map(
                    tuple,
                    current.loc[current.signal_present.eq(True), KEY].drop_duplicates().itertuples(
                        index=False, name=None
                    ),
                )
            )
            touched_in_scope = sum(key in key_set for key in touched_keys)
            signal_count = len(candidate_keys)
            mae = pd.to_numeric(present.mae_before_terminal_pct, errors="coerce").dropna()
            rows.append(
                {
                    "grouping": grouping,
                    "month": month,
                    "side": side,
                    "rule": rule_name,
                    "valid_month_side_denominator": denominator,
                    "catalogue_touched_month_side_count": touched_in_scope,
                    "signal_month_side_count": signal_count,
                    "no_signal_among_touched_count": max(0, touched_in_scope - signal_count),
                    "no_signal_in_valid_scope_count": max(0, denominator - signal_count),
                    "signal_coverage_of_valid_scope_pct": signal_count / denominator * 100 if denominator else np.nan,
                    "signal_coverage_of_touched_scope_pct": signal_count / touched_in_scope * 100 if touched_in_scope else np.nan,
                    "target20_success_count": int(statuses.get("success_target20_before_invalidation", 0)),
                    "invalidated_before_target_count": int(statuses.get("invalidated_before_target20", 0)),
                    "complete_45d_no_target_or_invalidation_count": int(
                        statuses.get("complete_45d_no_target_or_invalidation", 0)
                    ),
                    "unknown_cutoff_or_gap_count": int(statuses.get("unknown_cutoff_or_gap", 0)),
                    "pre_signal_5_reached_count": int(pre5.get("reached_before_signal", 0)),
                    "pre_signal_5_ambiguous_touch_hour_count": int(pre5.get("ambiguous_same_touch_hour", 0)),
                    "pre_signal_5_unknown_count": int(
                        pre5.get("unknown_gap_before_signal", 0)
                        + pre5.get("unknown_missing_touch_hour", 0)
                        + pre5.get("unknown_no_touch_hour", 0)
                    ),
                    "pre_signal_5_not_reached_count": int(pre5.get("not_reached_before_signal", 0)),
                    "pre_signal_20_reached_count": int(pre20.get("reached_before_signal", 0)),
                    "pre_signal_20_ambiguous_touch_hour_count": int(pre20.get("ambiguous_same_touch_hour", 0)),
                    "pre_signal_20_unknown_count": int(
                        pre20.get("unknown_gap_before_signal", 0)
                        + pre20.get("unknown_missing_touch_hour", 0)
                        + pre20.get("unknown_no_touch_hour", 0)
                    ),
                    "pre_signal_20_not_reached_count": int(pre20.get("not_reached_before_signal", 0)),
                    "mae_observed_count": int(len(mae)),
                    "mae_before_terminal_median_pct": float(mae.median()) if len(mae) else np.nan,
                    "mae_before_terminal_p90_pct": float(mae.quantile(0.9)) if len(mae) else np.nan,
                }
            )
    return pd.DataFrame(rows)


def _make_photos(events: pd.DataFrame, keys: pd.DataFrame, touched: pd.DataFrame) -> pd.DataFrame:
    """Emit key/presence status only; do not expose future path labels here."""
    touch_keys = set(map(tuple, touched[KEY].drop_duplicates().itertuples(index=False, name=None)))
    rows = []
    for key_row in keys.to_dict("records"):
        key = tuple(key_row[k] for k in KEY)
        candidates = events[
            events.symbol.eq(key[0]) & events.month.astype(str).eq(key[1]) & events.side.eq(key[2])
        ]
        row: dict[str, object] = {
            "photo_id": key_row["photo_id"],
            "selection_source": key_row["selection_source"],
            **{k: key_row[k] for k in KEY},
            "in_valid_scope": bool(key_row["in_valid_scope"]),
            "catalogue_touch_found": key in touch_keys,
        }
        if key in touch_keys:
            touch = touched[
                touched.symbol.eq(key[0]) & touched.month.astype(str).eq(key[1]) & touched.side.eq(key[2])
            ].iloc[0]
            row["attention_start_hour_beijing"] = _beijing_label(int(touch.touch_ms))
            row["catalogue_touch_days"] = int(touch.catalogue_touch_days)
        else:
            row["attention_start_hour_beijing"] = None
            row["catalogue_touch_days"] = 0
        for rule_name, _ in RULES:
            one = candidates[candidates.rule.eq(rule_name)]
            signal = one.iloc[0] if len(one) else None
            row[f"{rule_name}_signal_present"] = bool(signal is not None and signal.signal_present)
            row[f"{rule_name}_confirmation_hour_beijing"] = (
                signal.confirmation_hour_beijing if signal is not None and signal.signal_present else None
            )
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    signals = _read_signals()
    valid, valid_months = _read_scope(signals)
    touched = _read_daily_touch_catalogue(valid_months)
    photo_keys = _read_photo_keys(valid_months)

    events = _build_events(valid_months, touched, signals, photo_keys)
    summary = _make_summary(events, valid, touched)
    photos = _make_photos(events, photo_keys, touched)

    # The older census is diagnostic only. Quantify the loss it would cause,
    # while retaining every row from the expanded signal source above.
    legacy_coverage = pd.read_csv(
        CENSUS_OUT / "coverage_months.csv",
        usecols=["symbol", "month", "status"],
    )
    legacy_coverage["symbol"] = legacy_coverage.symbol.astype(str)
    legacy_coverage["month"] = legacy_coverage.month.astype(str)
    legacy_valid = set(
        map(
            tuple,
            legacy_coverage.loc[
                legacy_coverage.status.eq("valid"), ["symbol", "month"]
            ].drop_duplicates().itertuples(index=False, name=None),
        )
    )
    legacy_kept_rows = int(
        signals[["symbol", "month"]].apply(tuple, axis=1).isin(legacy_valid).sum()
    )
    summary["signal_source_rows"] = len(signals)
    summary["rows_covered_by_legacy_valid_census"] = legacy_kept_rows
    summary["rows_legacy_census_would_drop"] = len(signals) - legacy_kept_rows
    summary["rows_retained_in_primary_domain"] = len(signals)

    DEST.mkdir(parents=True, exist_ok=True)
    events.to_csv(DEST / "population_events.csv.gz", index=False, compression="gzip")
    summary.to_csv(DEST / "population_summary.csv", index=False)
    photos.to_csv(DEST / "population_photos.csv", index=False)
    print(
        "wrote",
        DEST,
        "event rows",
        len(events),
        "summary rows",
        len(summary),
        "photo keys",
        len(photos),
        flush=True,
    )


if __name__ == "__main__":
    main()
