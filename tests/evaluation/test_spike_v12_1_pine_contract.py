"""Guard the V12.1 presentation-only boundary against the preserved V12 source."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / "yoyo/evaluation/pine/spike_burst_v12.pine").read_text()
NEW = (ROOT / "yoyo/evaluation/pine/spike_burst_v12_1.pine").read_text()


def section(source, start, end=None):
    body = source[source.index(start):]
    return body[:body.index(end)] if end else body


def test_display_revision_preserves_signal_and_risk_calculations():
    for start, end in (
        ("const int maLen", "// ───────────── 自动下降线参数"),
        ("// ───────────── 双轨高点", "// 本周期线的A/B/C"),
        ("// ───────────── 结构监测与联合判断", "// ───────────── 当前候选主线绘制"),
        ("// 事件码：", None),
    ):
        assert section(OLD, start, end) == section(NEW, start, end).replace("SPIKE V12.1", "SPIKE V12").replace('"V12.1 事件码', '"V12 事件码')
