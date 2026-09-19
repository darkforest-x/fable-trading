"""Guard unchanged V11.2 signal/risk and line-validation logic in new V12."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / "yoyo/evaluation/pine/spike_burst_v11_2.pine").read_text()
NEW = (ROOT / "yoyo/evaluation/pine/spike_burst_v12.pine").read_text()


def function(source, name):
    start = source.index(name + "(")
    tail = source[start:]
    end = re.search(r"\n(?=[a-zA-Z]\w*\(|// ─)", tail)
    return tail[:end.start() if end else len(tail)].strip()


def test_original_search_validation_geometry_unchanged():
    for name in ("f_v10_search", "f_v10_validate", "f_v10_same", "f_v10_price", "f_v10_rank"):
        assert function(OLD, name) == function(NEW, name), name


def test_v9_logic_and_risk_parameters_unchanged():
    start = OLD.index("const int maLen")
    end = OLD.index("string v10LineGroup")
    def executable(source):
        return [line for line in source.splitlines() if line.strip() and not line.lstrip().startswith("//")]
    assert executable(OLD[start:end]) == executable(NEW[NEW.index("const int maLen"):NEW.index("string v10LineGroup")])


def test_htf_closed_bar_contract_unchanged():
    assert function(OLD, "f_v11_htfOutputs") == function(NEW, "f_v11_htfOutputs")
    old_request = next(x for x in OLD.splitlines() if "= request.security(" in x)
    assert old_request in NEW


def test_alert_and_event_codes_follow_selected_box_rule():
    assert 'alertcondition(v10Enabled and v112HtfShow,' in NEW
    event = next(x for x in NEW.splitlines() if '"V12 事件码' in x)
    assert "v112ChartShow ? 4" in event and "v112HtfShow ? 16" in event


def test_auxiliary_family_capacity_does_not_evict_legacy_by_score():
    assert "known.kind == item.kind and known.source == item.source" in NEW
    assert "bool v12LocalEnabled = input.bool(true" in NEW
    assert "bx + v10Right <= bar_index" in NEW
    assert "touchBar - v12LocalLeft >= segStart" in NEW
    assert "touchBar - v12LocalLeft >= v10SegmentStart" in NEW
