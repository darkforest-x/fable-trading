"""Predeclared champion/challenger forward books (prospective shadow tracking).

Each entry is frozen configuration only — no parameter search, no ACTIVE
promotion. Unsupported challengers stay explicit rather than approximated
with the mainline model or bar.

Evidence class for all prospective logs: short forward observation, not
historical backtest and not profitability proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final, Literal, Optional

from src.judgment.forward import (
    FORWARD_LOG_H1_SCALED_PATH,
    FORWARD_LOG_PATH,
    FORWARD_START,
    run_forward_tracking,
    run_forward_tracking_h1_shadow,
)
from src.judgment.forward_types import PROJECT_DIR
from src.judgment.frozen import DEFAULT_CONFIG_NAME

BookRole = Literal["champion", "challenger"]
BookStatus = Literal["supported", "unsupported"]

Runner = Callable[..., object]


@dataclass(frozen=True)
class ShadowBook:
    """One append-only paper book with fixed, predeclared settings."""

    __slots__ = (
        "name",
        "role",
        "status",
        "log_path",
        "bar",
        "side",
        "exit_family",
        "entry_model",
        "description",
        "unsupported_reason",
        "runner_key",
    )

    name: str
    role: BookRole
    status: BookStatus
    log_path: Path
    bar: str
    side: str
    exit_family: str
    entry_model: str
    description: str
    unsupported_reason: str
    runner_key: Optional[str]


# Fixed registry — order is display order. Do not retune from forward PnL.
SHADOW_BOOKS: Final[tuple[ShadowBook, ...]] = (
    ShadowBook(
        name="tp5_sl2_long_swap",
        role="champion",
        status="supported",
        log_path=FORWARD_LOG_PATH,
        bar="15m",
        side="long",
        exit_family="tp5_sl2",
        entry_model=DEFAULT_CONFIG_NAME,
        description=(
            "Frozen ACTIVE long TP5/SL2 SWAP mainline. Prospective paper book only; "
            "not a final performance claim."
        ),
        unsupported_reason="",
        runner_key="mainline_tp5_sl2",
    ),
    ShadowBook(
        name="h1_scaled_25_t3",
        role="challenger",
        status="supported",
        log_path=FORWARD_LOG_H1_SCALED_PATH,
        bar="15m",
        side="long",
        exit_family="scaled_25_t3",
        entry_model=DEFAULT_CONFIG_NAME,
        description=(
            "H1 scaled exits (half@2.5 ATR + trail 3) on mainline freeze entries. "
            "Discovery-tier; never auto-promotes ACTIVE."
        ),
        unsupported_reason="",
        runner_key="h1_scaled_shadow",
    ),
    ShadowBook(
        name="h8_30m_h48",
        role="challenger",
        status="unsupported",
        log_path=PROJECT_DIR / "data" / "forward_log_h8_30m.csv",
        bar="30m",
        side="long",
        exit_family="tp5_sl2",
        entry_model="none_frozen_30m",
        description=(
            "H8 30m TP5/SL2 discovery pool (h48). Predeclared for prospective "
            "tracking once a frozen 30m artifact exists."
        ),
        unsupported_reason=(
            "No frozen 30m LightGBM artifact under models/; forward scan is 15m-only "
            "today. Approximating with the 15m mainline model would break "
            "single-variable discipline — mark unsupported until a proper freeze."
        ),
        runner_key=None,
    ),
    ShadowBook(
        name="h10_short_tp5_sl2",
        role="challenger",
        status="unsupported",
        log_path=PROJECT_DIR / "data" / "forward_log_h10_short.csv",
        bar="15m",
        side="short",
        exit_family="tp5_sl2_short",
        entry_model="none_frozen_short",
        description=(
            "H10 short-side mirror TP5/SL2. Predeclared; requires short freeze + "
            "short candidate scan + inverted maker fill."
        ),
        unsupported_reason=(
            "No frozen short-side model; mainline booster is long-trained. "
            "Scoring shorts with the long freeze would be an approximation — refused."
        ),
        runner_key=None,
    ),
)

_RUNNERS: Final[dict[str, Runner]] = {
    "mainline_tp5_sl2": run_forward_tracking,
    "h1_scaled_shadow": run_forward_tracking_h1_shadow,
}


def list_shadow_books() -> tuple[ShadowBook, ...]:
    return SHADOW_BOOKS


def get_shadow_book(name: str) -> ShadowBook:
    for book in SHADOW_BOOKS:
        if book.name == name:
            return book
    raise KeyError(f"unknown shadow book: {name!r}")


def supported_books() -> tuple[ShadowBook, ...]:
    return tuple(b for b in SHADOW_BOOKS if b.status == "supported")


def unsupported_books() -> tuple[ShadowBook, ...]:
    return tuple(b for b in SHADOW_BOOKS if b.status == "unsupported")


def champion_book() -> ShadowBook:
    for book in SHADOW_BOOKS:
        if book.role == "champion":
            return book
    raise RuntimeError("registry missing champion")


def book_to_dict(book: ShadowBook) -> dict:
    return {
        "name": book.name,
        "role": book.role,
        "status": book.status,
        "log_path": str(book.log_path),
        "bar": book.bar,
        "side": book.side,
        "exit_family": book.exit_family,
        "entry_model": book.entry_model,
        "description": book.description,
        "unsupported_reason": book.unsupported_reason or None,
        "forward_start": str(FORWARD_START),
        "promotes_active": False,
    }


def registry_snapshot() -> dict:
    """Machine-readable registry for digests and evidence bundles."""
    return {
        "evidence_class": "prospective_forward_observation",
        "active_promotion": "disabled",
        "forward_start": str(FORWARD_START),
        "books": [book_to_dict(b) for b in SHADOW_BOOKS],
        "supported": [b.name for b in supported_books()],
        "unsupported": [b.name for b in unsupported_books()],
    }


def resolve_runner(book: ShadowBook) -> Runner:
    if book.status != "supported" or not book.runner_key:
        raise ValueError(
            f"book {book.name!r} is {book.status}: {book.unsupported_reason or 'no runner'}"
        )
    try:
        return _RUNNERS[book.runner_key]
    except KeyError as exc:
        raise ValueError(f"no runner registered for key {book.runner_key!r}") from exc
