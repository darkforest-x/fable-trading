"""Read a window that crosses the holdout boundary, only under a recorded grant.

`spike_fanshen_prefix.read_prefix` refuses any end past 2026-05-01 and stays
that way: research code must not be able to reach holdout bars by accident.
Acceptance runs are the documented exception, and they are gated here instead:
the caller must pass the authorization block that was committed with the
experiment, and this module checks that `docs/HOLDOUT_LEDGER.md` already
carries that numbered entry and this experiment id before a byte is parsed.

A read through this function consumes the holdout. There is no "just looking".
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.data.release_eth_prefix import validate_ohlcv

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs/HOLDOUT_LEDGER.md"


def read_authorized_window(path, minutes: int, authorization: dict) -> tuple[pd.DataFrame, dict]:
    """Return bars inside an authorized window plus a consumption receipt.

    ``authorization`` must carry ``ledger_index``, ``experiment_id``,
    ``window_start``, ``window_end`` and ``evaluation_start``. Warmup bars
    before ``evaluation_start`` are allowed so bands can form; the caller is
    responsible for scoring only from ``evaluation_start`` onwards.
    """
    required = {"ledger_index", "experiment_id", "window_start", "window_end", "evaluation_start"}
    if not required.issubset(authorization):
        raise ValueError(f"authorization needs {', '.join(sorted(required))}")
    if minutes not in (1, 3, 5, 15):
        raise ValueError("unsupported native source duration")
    ledger = LEDGER.read_text()
    index = int(authorization["ledger_index"])
    if f"| {index} |" not in ledger:
        raise ValueError(f"holdout ledger has no entry #{index}; record it before reading")
    if authorization["experiment_id"] not in ledger:
        raise ValueError("holdout ledger entry does not name this experiment")
    start = pd.Timestamp(authorization["window_start"])
    end = pd.Timestamp(authorization["window_end"])
    evaluation = pd.Timestamp(authorization["evaluation_start"])
    if start.tzinfo is None or end.tzinfo is None or evaluation.tzinfo is None:
        raise ValueError("authorized window must be timezone aware")
    if not start <= evaluation <= end:
        raise ValueError("evaluation start must sit inside the authorized window")
    if evaluation < pd.Timestamp(HOLDOUT_START):
        raise ValueError("this reader is for acceptance runs; use read_prefix before the boundary")
    frame = pd.read_csv(path)
    if frame.columns[0] != "ts":
        raise ValueError("timestamp must be first field")
    frame["open_time"] = pd.to_datetime(frame.open_time, utc=True)
    frame = frame.loc[(frame.open_time >= start)
                      & (frame.open_time + pd.Timedelta(minutes=minutes) <= end)].copy()
    validate_ohlcv(frame, minutes)
    frame = frame.set_index("open_time").loc[:, ["open", "high", "low", "close", "volume"]].astype(float)
    holdout_rows = int((frame.index >= pd.Timestamp(HOLDOUT_START)).sum())
    return frame, dict(path=str(path), sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                       rows=int(len(frame)), first_open=str(frame.index[0]),
                       last_close=str(frame.index[-1] + pd.Timedelta(minutes=minutes)),
                       window_start=str(start), window_end=str(end),
                       evaluation_start=str(evaluation), holdout_rows_read=holdout_rows,
                       holdout_consumed=True, ledger_index=index,
                       experiment_id=authorization["experiment_id"])
