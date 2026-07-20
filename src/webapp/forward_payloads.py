"""Forward-validation dashboard payloads from append-only forward_log.csv.

The forward tab reports only maker-filled closed rows for the 100-trade
decision counter, while keeping all logged threshold signals visible in the
table so open or missed-maker rows are not silently dropped.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest.run import MAX_CONCURRENT
from src.judgment.forward_types import FORWARD_COLUMNS, FORWARD_LOG_PATH
from src.webapp.dashboard_cache import relative_path

from src.costs import FORWARD_COST  # mainline swap-maker route
FORWARD_DECISION_TRADES = 100


# A verdict trade must have been KNOWABLE at the same 15m pulse as the signal.
# signal_time = bar open; bar closes +15m; pulse ~+16m → honest lag ≈ 15–20m.
# Owner 2026-07-19: multi-hour hindsight must not count; target tip-fire same
# pulse. Align with executor max_signal_age_min (20). Structural 15m bar delay
# remains; this kills post-hoc recognition hours later.
FRESH_DETECT_MIN = 20.0


def forward_payload(cost: float = FORWARD_COST) -> dict:
    frame = _read_forward_log()
    closed = frame[(frame["status"] == "closed") & frame["realized_ret"].notna()].copy()
    decision = closed[closed["maker_filled"].fillna(False)].copy()
    # Hindsight exclusion (2026-07-19): the live detector often recognises a
    # signal only HOURS after its bar, once the launch has printed -- KAITO
    # 03:00 was detected 07:17, EDEN 04:30 at 14:17, and all such rows closed
    # as TPs precisely because detection was conditioned on the move having
    # happened. Counting them would let hindsight buy the verdict. Only rows
    # detected within FRESH_DETECT_MIN of their signal bar are decidable.
    if "detected_at" in decision.columns and not decision.empty:
        det = pd.to_datetime(decision["detected_at"], errors="coerce", utc=True)
        sig = pd.to_datetime(decision["signal_time"], errors="coerce", utc=True)
        lag_min = (det - sig).dt.total_seconds() / 60.0
        hindsight = decision[(lag_min > FRESH_DETECT_MIN) | lag_min.isna()]
        decision = decision[lag_min <= FRESH_DETECT_MIN]
    else:
        hindsight = decision.iloc[0:0]
    decision["net_ret"] = decision["realized_ret"] - cost
    equity, drawdown = _forward_equity(decision)
    decision_count = int(len(decision))
    hindsight_count = int(len(hindsight))
    rows = frame.sort_values("signal_time", ascending=False).head(200).copy()
    if not rows.empty:
        rows["net_ret"] = rows["realized_ret"] - cost
        # Detection lag (minutes): how late the live scan recognized the bar.
        # Fresh ⇔ lag <= FRESH_DETECT_MIN; larger = hindsight (not tradeable live).
        det = pd.to_datetime(rows["detected_at"], errors="coerce", utc=True)
        sig = pd.to_datetime(rows["signal_time"], errors="coerce", utc=True)
        lag = (det - sig).dt.total_seconds() / 60.0
        rows["lag_min"] = lag.round(1)
        rows["fresh"] = lag.notna() & (lag <= FRESH_DETECT_MIN)
        for column in ("score", "threshold", "entry_price", "realized_ret", "atr_pct", "net_ret"):
            rows[column] = rows[column].round(5)
        for column in ("signal_time", "detected_at", "entry_time", "exit_time"):
            rows[column] = rows[column].astype(str).replace("NaT", "")
    return {
        "cost": cost,
        "cost_label": "maker 0.06% round-trip",
        "log_path": relative_path(FORWARD_LOG_PATH),
        "total_rows": int(len(frame)),
        "closed_rows": int(len(closed)),
        "open_rows": int((frame["status"] != "closed").sum()) if not frame.empty else 0,
        "decision_trades": decision_count,
        "decision_target": FORWARD_DECISION_TRADES,
        "decision_remaining": max(FORWARD_DECISION_TRADES - decision_count, 0),
        "progress": round(min(decision_count / FORWARD_DECISION_TRADES, 1.0), 4),
        "hindsight_excluded": hindsight_count,
        "fresh_detect_min": FRESH_DETECT_MIN,
        "latest_detected_at": _latest_timestamp(frame, "detected_at"),
        "metrics": forward_metrics(decision),
        "outcomes": _outcome_rows(decision),
        "equity": equity,
        "drawdown": drawdown,
        "rows": _json_rows(rows),
    }


def forward_metrics(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {
            "n_trades": 0,
            "profit_factor": None,
            "win_rate": None,
            "mean_net_per_trade": None,
            "net_return_on_capital": None,
            "total_net_units": 0.0,
        }
    net = frame["net_ret"].to_numpy()
    wins, losses = net[net > 0].sum(), net[net < 0].sum()
    return {
        "n_trades": int(len(frame)),
        "profit_factor": round(float(wins / -losses), 3) if losses < 0 else None,
        "win_rate": round(float((net > 0).mean()), 4),
        "mean_net_per_trade": round(float(net.mean()), 5),
        "net_return_on_capital": round(float(net.sum() / MAX_CONCURRENT), 4),
        "total_net_units": round(float(net.sum()), 5),
    }


def _read_forward_log() -> pd.DataFrame:
    if not FORWARD_LOG_PATH.exists():
        return pd.DataFrame(columns=FORWARD_COLUMNS)
    frame = pd.read_csv(FORWARD_LOG_PATH)
    for column in FORWARD_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    frame = frame[list(FORWARD_COLUMNS)]
    for column in ("signal_time", "detected_at", "entry_time", "exit_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    for column in ("score", "threshold", "entry_price", "realized_ret", "atr_pct"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["maker_filled"] = frame["maker_filled"].fillna(False).astype(bool)
    return frame


def _forward_equity(frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    if frame.empty:
        return [], []
    ordered = frame.dropna(subset=["exit_time"]).sort_values("exit_time")
    points = []
    cumulative = 0.0
    for row in ordered.itertuples():
        cumulative += float(row.net_ret)
        points.append({"time": int(row.exit_time.timestamp()), "value": round(100 * cumulative / MAX_CONCURRENT, 4)})
    peak, drawdown = 0.0, []
    for point in points:
        peak = max(peak, point["value"])
        drawdown.append({"time": point["time"], "value": round(point["value"] - peak, 4)})
    return points, drawdown


def _outcome_rows(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    rows = []
    for outcome, group in frame.groupby("outcome"):
        rows.append({"label": str(outcome) or "open", "value": round(float(group["net_ret"].sum() * 100), 4),
                     "text": f"{len(group)}笔"})
    return sorted(rows, key=lambda row: row["value"])


def _latest_timestamp(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame:
        return ""
    value = frame[column].max()
    if pd.isna(value):
        return ""
    return str(value)


def _json_rows(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    return frame.replace({np.nan: None, pd.NaT: None}).to_dict("records")
