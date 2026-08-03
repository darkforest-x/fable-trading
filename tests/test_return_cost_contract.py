"""P0 H4: gross/taker/maker routing is unique and cannot double deduct."""
from __future__ import annotations

import pytest

from src.costs import (
    SWAP_MAKER,
    SWAP_TAKER,
    convert_return,
    deduct_round_trip_cost_once,
)


def test_gross_to_taker() -> None:
    assert convert_return(0.01, source_semantics="gross", target_semantics="net_taker") \
        == pytest.approx(0.01 - SWAP_TAKER)


def test_gross_to_maker() -> None:
    assert convert_return(0.01, source_semantics="gross", target_semantics="net_maker") \
        == pytest.approx(0.01 - SWAP_MAKER)


def test_taker_to_maker_restores_gross_before_applying_maker() -> None:
    net_taker = 0.01 - SWAP_TAKER
    expected = 0.01 - SWAP_MAKER
    assert convert_return(
        net_taker, source_semantics="net_taker", target_semantics="net_maker"
    ) == pytest.approx(expected)


def test_deduct_api_refuses_already_net_input() -> None:
    with pytest.raises(ValueError, match="cost already included"):
        deduct_round_trip_cost_once(
            0.009, input_semantics="net_taker", target_semantics="net_maker"
        )


@pytest.mark.parametrize("bad", ["net", "taker", "unknown"])
def test_unknown_return_semantics_fail_closed(bad: str) -> None:
    with pytest.raises(ValueError, match="unknown"):
        convert_return(0.01, source_semantics=bad, target_semantics="net_taker")
