"""Reproduce the frozen writer bug and constrain its output-only repair."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.imacd_yolo_expanded import grouped_stats
from yoyo.evaluation.imacd_yolo_expanded_finalize import native_scalar


def test_multi_column_grouping_requires_explicit_scalar_conversion():
    d=pd.DataFrame(dict(timeframe_min=[60,240],side=[1,-1],complete_followup=[True,True],
        status=["confirmed","confirmed"],symbol=["A","B"],delay_bars=[0.,2.],displacement_bp=[0.,3.]))
    grouped=grouped_stats(d,["side","timeframe_min"])
    with pytest.raises(TypeError, match="int64"):
        json.dumps(grouped)
    restored=json.loads(json.dumps(grouped,default=native_scalar,allow_nan=False))
    assert [x["confirmed"] for x in restored]==[1,1]
    assert {x["timeframe_min"] for x in restored}=={60,240}
    assert sum(x["displacement_median_bp"] for x in restored)==3.


def test_converter_does_not_mask_unknown_objects_or_nonfinite_metrics():
    with pytest.raises(TypeError,match="Unsupported"):
        json.dumps({"unknown":object()},default=native_scalar)
    with pytest.raises(ValueError):
        json.dumps({"bad":np.float64(np.nan)},allow_nan=False,default=native_scalar)
