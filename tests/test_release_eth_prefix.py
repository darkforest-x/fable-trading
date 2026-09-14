"""Restricted-value tripwires and aggregation semantics for ETH prefix reads."""
import pandas as pd
import pytest

from yoyo.data.release_eth_prefix import aggregate, read_prefix


def write_source(path, start="2024-01-01", count=32):
    t = pd.date_range(start, periods=count, freq="15min", tz="UTC")
    frame = pd.DataFrame({"ts": t.as_unit("ms").astype("int64"), "open": 100., "high": 102.,
                          "low": 98., "close": 101., "volume": 1., "open_time": t})
    frame.to_csv(path, index=False)
    return frame


def test_boundary_timestamp_is_read_before_forbidden_values(tmp_path):
    p = tmp_path / "bars.csv"
    write_source(p, "2026-04-30T23:45Z", 1)
    with p.open("a") as f:
        f.write(f"{pd.Timestamp('2026-05-01T00:00Z').value // 10**6},THIS_IS_NOT_A_VALID_PRICE\n")
    frame, receipt = read_prefix(p, "2026-05-01T00:00Z")
    assert len(frame) == 1 and receipt["restricted_price_rows_parsed"] == 0
    assert receipt["first_excluded_timestamp_only"] == "2026-05-01T00:00:00+00:00"


def test_no_endpoint_can_authorize_holdout_or_naive_time(tmp_path):
    for end in ("2026-05-05T00:00Z", "2024-01-01"):
        with pytest.raises(ValueError, match="endpoint"):
            read_prefix(tmp_path / "missing", end)


def test_complete_aggregation_is_causal_and_rejects_internal_gaps(tmp_path):
    p = tmp_path / "bars.csv"
    write_source(p)
    frame, _ = read_prefix(p, "2024-01-02T00:00Z")
    for minutes in (15, 60, 240):
        result, receipt = aggregate(frame, minutes)
        assert len(result) * (minutes // 15) == len(frame)
        assert result.volume.iloc[0] == minutes // 15
        assert receipt["internal_incomplete_groups"] == 0
        prefix, _ = aggregate(frame.iloc[:16], minutes)
        pd.testing.assert_frame_equal(result.iloc[:len(prefix)], prefix)
    with pytest.raises(ValueError, match="missing"):
        aggregate(frame.drop(index=4), 60)


def test_prefix_hash_excludes_suffix_and_geometry_is_not_repaired(tmp_path):
    p = tmp_path / "bars.csv"
    write_source(p)
    _, before = read_prefix(p, "2024-01-01T04:00Z")
    with p.open("a") as f:
        f.write("9999999999999,no prices should be parsed\n")
    _, after = read_prefix(p, "2024-01-01T04:00Z")
    assert before["consumed_prefix_sha256"] == after["consumed_prefix_sha256"]
    frame = write_source(p)
    frame.loc[0, "high"] = 90
    frame.to_csv(p, index=False)
    with pytest.raises(ValueError, match="high"):
        read_prefix(p, "2024-01-01T04:00Z")
