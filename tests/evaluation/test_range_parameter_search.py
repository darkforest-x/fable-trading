"""Selection chronology and parameterized screening kernel invariants."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.pepe_range_breakeven import replay, scalar, assert_equal
from yoyo.evaluation.range_parameter_search import DEFAULT, validate_grid, choose_development


def test_grid_has_one_change_per_treatment():
    cfg = json.loads(DEFAULT.read_text())
    validate_grid(cfg)
    cfg["arms"][1]["trail_arm_r"] = 99
    with pytest.raises(ValueError, match="exactly one"):
        validate_grid(cfg)


def test_future_segment_cannot_change_selected_parameter():
    rows = pd.DataFrame([
        dict(arm="a", segment=0, n=100, net_r=1, realized_r_drawdown=2),
        dict(arm="b", segment=0, n=100, net_r=2, realized_r_drawdown=2),
        dict(arm="a", segment=1, n=100, net_r=100000, realized_r_drawdown=2),
        dict(arm="b", segment=1, n=100, net_r=-100000, realized_r_drawdown=2),
    ])
    assert choose_development(rows, 100) == "b"


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("arm_r,multiple,expected_armed", [(3, 2, 1), (5, 4, -1), (3, 6, 1)])
def test_parameterized_trailing_preserves_completed_management_clock(side, arm_r, multiple, expected_armed):
    bars = np.array([[100, 200, 1, 100], [100, 104, 99.5, 103],
                     [103, 110, 102.5, 109], [109, 110, 98, 100]], dtype=float)
    if side == -1:
        bars = 200-bars
        bars[:, [1, 2]] = bars[:, [2, 1]]
    ctx = dict(bars=np.ascontiguousarray(bars),
               atr=np.array([np.nan, 1, np.nan, 1]), hour=np.zeros(4))
    event = [0, side, 100, 100-side, 3]
    result = replay(ctx, [event], "baseline", tick=.01, arm_r=arm_r,
                    atr_multiple=multiple)[0]
    expected = scalar(ctx, event, "baseline", tick=.01, arm_r=arm_r, atr_multiple=multiple)
    assert_equal(result, expected, .01)
    assert result[4] == expected_armed
    assert result[0] == 3
    assert result[1] == pytest.approx(100+side if multiple == 2 else 100-side)
