"""V12.2 must change display history without touching the signal/risk engine."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / "yoyo/evaluation/pine/spike_burst_v12.pine").read_text()
NEW = (ROOT / "yoyo/evaluation/pine/spike_burst_v12_2.pine").read_text()


def section(source, start, end=None):
    body = source[source.index(start):]
    return body[:body.index(end)] if end else body


def test_engine_and_risk_unchanged():
    for start, end in (
        ("const int maLen", "// ───────────── 自动下降线参数"),
        ("// ───────────── 双轨高点", "// 本周期线的A/B/C"),
        ("// ───────────── 结构监测与联合判断", "// ───────────── 当前候选主线绘制"),
        ("// 事件码：", None),
    ):
        assert section(OLD, start, end) == section(NEW, start, end).replace("SPIKE V12.2", "SPIKE V12").replace('"V12.2 事件码', '"V12 事件码')


def test_only_display_inputs_changed():
    old = dict(re.findall(r'^(?:bool|int|float|string|color) (\w+) = (input\..*)$', OLD, re.M))
    new = dict(re.findall(r'^(?:bool|int|float|string|color) (\w+) = (input\..*)$', NEW, re.M))
    changed = {key for key in old if old[key] != new.get(key)}
    assert changed == {"v10JointKeep", "v11ExtendBars"}
    assert set(new) - set(old) == {"v122LocalTidy", "v122RegionKeep", "v122RegionGap", "v122HistoryFade", "v122JointFade"}
