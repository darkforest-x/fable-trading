"""Exact Decimal unit bounds, no actual market inputs."""
from yoyo.evaluation.binance_flow_unit_audit import classify


def test_vwap_inside_ohlc():
    row=['1704067200000','101','102','99','100','10','1704067499999','1000','7','7','700','0']
    assert classify(row)==[]


def test_genuine_source_quote_units_mismatch_is_not_rounding():
    row=['1704067200000','101','102','99','100','10','1704067499999','1000','7','7','720','0']
    flags=classify(row)
    assert {r['side'] for r in flags}=={'buy','sell'}
    assert all(r['exact_outside'] for r in flags)
    assert {r['side']:r['excess_quote'] for r in flags}=={'buy':'6','sell':'17'}


def test_decimal_boundary_is_not_reclassified_by_float_division():
    row=['1704067200000','0.1','0.1','0.1','0.1','0.3','1704067499999','0.03','3','0.2','0.02','0']
    flags=classify(row)
    assert flags and not any(r['exact_outside'] for r in flags)
    assert all(r['classification']=='float_boundary_only' for r in flags)


def test_zero_volume_produces_no_fake_vwap():
    row=['1704067200000','101','102','99','100','0','1704067499999','0','0','0','0','0']
    assert classify(row)==[]
