"""Pin the V4 release to the audited display projection, not a new detector.

This test reads Pine source only. Native TradingView compile/save evidence is
recorded separately; no OHLCV, future labels, or performance scores are read.
"""
import hashlib
from pathlib import Path


def test_v4_only_renames_the_audited_layered_display():
    root = Path(__file__).resolve().parents[1]
    parent = root / "yoyo/evaluation/pine/spike_burst_v3_layered_display.pine"
    release = root / "yoyo/evaluation/pine/spike_burst_v4.pine"
    assert hashlib.sha256(parent.read_bytes()).hexdigest() == (
        "5c54df7adfa0146c5e124578c5ad18d797f3372e218f07fadcb2bc631b73a2e7"
    )
    expected = parent.read_text()
    replacements = (
        ("// SPIKE V3: fixed long-only two-stage research hypothesis, not optimized parameters.", "// SPIKE V4: layered display of unchanged V3 events; not an optimized signal filter."),
        ('indicator("SPIKE V3 · 分层观察（研究版）", shorttitle="SPIKE V3",',
         'indicator("SPIKE V4 · 分层观察", shorttitle="SPIKE V4",'),
        ("SPIKE V3 · 最新收盘", "SPIKE V4 · 最新收盘"),
        ("SPIKE V3 · 分层观察", "SPIKE V4 · 分层观察"),
    )
    for old, new in replacements:
        assert expected.count(old) == 1
        expected = expected.replace(old, new)
    assert release.read_text() == expected
