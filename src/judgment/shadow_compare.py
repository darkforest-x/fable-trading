"""Compact multi-book forward comparison for digests (read-only).

Labels all numbers as short forward observations. Never mutates ACTIVE or
promotes a challenger from two-day PnL.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.judgment.forward_records import read_forward_log
from src.judgment.shadow_registry import ShadowBook, list_shadow_books


# Digest fee note: matches scripts/daily_digest.py (0.06% round-trip proxy).
_DIGEST_FEE = 0.0006


def _summarize_log(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "total_rows": 0,
            "open_rows": 0,
            "closed_rows": 0,
            "closed_net_sum": None,
            "closed_win_rate": None,
            "duplicate_keys": 0,
            "signal_time_min": None,
            "signal_time_max": None,
        }
    frame = read_forward_log(path)
    if frame.empty:
        return {
            "exists": True,
            "total_rows": 0,
            "open_rows": 0,
            "closed_rows": 0,
            "closed_net_sum": None,
            "closed_win_rate": None,
            "duplicate_keys": 0,
            "signal_time_min": None,
            "signal_time_max": None,
        }
    keys = list(zip(frame["source"].astype(str), frame["symbol"].astype(str), frame["signal_time"].astype(str)))
    dup = len(keys) - len(set(keys))
    open_n = int((frame["status"].astype(str) != "closed").sum())
    closed = frame[frame["status"].astype(str) == "closed"]
    closed_n = int(len(closed))
    net_sum = None
    win_rate = None
    if closed_n and "realized_ret" in closed.columns:
        rets = pd.to_numeric(closed["realized_ret"], errors="coerce").dropna()
        if len(rets):
            net = rets - _DIGEST_FEE
            net_sum = float(net.sum())
            win_rate = float((net > 0).mean())
    st = pd.to_datetime(frame["signal_time"], utc=True, errors="coerce")
    return {
        "exists": True,
        "total_rows": int(len(frame)),
        "open_rows": open_n,
        "closed_rows": closed_n,
        "closed_net_sum": net_sum,
        "closed_win_rate": win_rate,
        "duplicate_keys": int(dup),
        "signal_time_min": str(st.min()) if st.notna().any() else None,
        "signal_time_max": str(st.max()) if st.notna().any() else None,
    }


def compare_shadow_books(books: tuple[ShadowBook, ...] | None = None) -> dict[str, Any]:
    """Build a compact champion/challenger comparison (no writes)."""
    books = books or list_shadow_books()
    rows: list[dict[str, Any]] = []
    for book in books:
        summary = _summarize_log(book.log_path)
        rows.append(
            {
                "name": book.name,
                "role": book.role,
                "status": book.status,
                "bar": book.bar,
                "side": book.side,
                "exit_family": book.exit_family,
                "entry_model": book.entry_model,
                "log_path": str(book.log_path),
                "unsupported_reason": book.unsupported_reason or None,
                "promotes_active": False,
                "evidence_class": "prospective_forward_observation",
                **summary,
            }
        )
    return {
        "evidence_class": "prospective_forward_observation",
        "note": (
            "Short forward sample only; historical stability is separate; "
            "future profit is unproven. No book promotes ACTIVE from this table."
        ),
        "fee_adjustment": _DIGEST_FEE,
        "books": rows,
    }


def format_comparison_text(comparison: dict[str, Any] | None = None) -> str:
    """Human-readable multi-line board for digest / CLI."""
    comparison = comparison or compare_shadow_books()
    lines = ["<b>影子对比</b>（前向观察·非终局）"]
    for row in comparison["books"]:
        if row["status"] == "unsupported":
            lines.append(f"· {row['name']}: unsupported — {row['unsupported_reason']}")
            continue
        if not row["exists"] or row["total_rows"] == 0:
            lines.append(f"· {row['name']} ({row['role']}): 尚无前向行")
            continue
        net = row["closed_net_sum"]
        win = row["closed_win_rate"]
        net_s = f"{100 * net:+.2f}%" if net is not None else "n/a"
        win_s = f"{100 * win:.0f}%" if win is not None else "n/a"
        lines.append(
            f"· {row['name']} ({row['role']}): n={row['total_rows']} "
            f"open={row['open_rows']} closed={row['closed_rows']} "
            f"净{net_s} 胜{win_s} 裁决 {row['closed_rows']}/100"
        )
    lines.append(comparison["note"])
    return "\n".join(lines)
