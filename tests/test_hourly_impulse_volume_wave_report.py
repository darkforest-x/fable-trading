"""Synthetic canonical monthly projection contract."""
import pytest
from yoyo.evaluation.hourly_impulse_volume_wave_report import monthly_dataset


def fixture():
    return [dict(population=p,dimension="month",key=f"{y}-{m:02d}",total="10",accepted="4",
        abstain="5",unknown="1",known="9",accepted_rate="0.4")
        for p in ("case","control") for y in (2023,2024) for m in range(1,13)]


def test_full_grid_and_context_kept():
    out=monthly_dataset(fixture())
    assert len(out)==48
    assert out[0]["accepted_rate"]==.4 and out[0]["unknown"]==1
    assert out[24]["series"]=="原随机控制"


@pytest.mark.parametrize("change",["missing","duplicate","ratio","unknown"])
def test_bad_projection_fails(change):
    rows=fixture()
    if change=="missing": rows.pop()
    elif change=="duplicate": rows[-1]=rows[0]
    elif change=="ratio": rows[0]["accepted_rate"]="0.444"
    else: rows[0]["unknown"]="0"
    with pytest.raises(AssertionError):
        monthly_dataset(rows)
