"""Protect the existing signal and pane contract during the V2.7 risk redesign.

Static parity is separate from compilation. Numeric cases execute the actual
Pine helpers via the companion TradingView probe, not a Python translation.
"""

from pathlib import Path
import re

from yoyo.evaluation.build_imacd_v27_contract_probe import build


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "yoyo/evaluation/pine/imacd_dense_mtf_v2_6.pine"
NEW = ROOT / "yoyo/evaluation/pine/imacd_dense_mtf_v2_7.pine"


def block(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


def test_all_signal_calculation_and_focus_drawing_are_identical():
    old, new = OLD.read_text(), NEW.read_text()
    assert block(old, "f_smma(float source", "// BEGIN V2.5 LIFECYCLE AND R REFERENCES") == block(new, "f_smma(float source", "// BEGIN V2.5 LIFECYCLE AND R REFERENCES")
    assert block(old, "string gSignal =", "// BEGIN V2.4 DISPLAY INPUTS") == block(new, "string gSignal =", "// BEGIN V2.4 DISPLAY INPUTS")


def test_all_plots_alerts_and_colors_are_identical():
    def protected(text):
        return [line for line in text.splitlines() if re.match(r"(?:hline\(|plot\(|plotshape\(|alertcondition\(|glowMain =|glowSignal =|fill\(|bgcolor\(|barcolor\()", line)]
    assert protected(NEW.read_text()) == protected(OLD.read_text())
    assert len([line for line in NEW.read_text().splitlines() if line.startswith("alertcondition(")]) == 5


def test_stop_precedence_gap_semantics_and_monotone_trail_unchanged():
    assert block(NEW.read_text(), "// BEGIN V2.5 PURE HELPERS", "// END V2.5 PURE HELPERS") == block(OLD.read_text(), "// BEGIN V2.5 PURE HELPERS", "// END V2.5 PURE HELPERS")


def test_probe_executes_actual_helpers_and_preserves_prior_cases(tmp_path):
    output = tmp_path / "probe.pine"
    assert build(NEW, output) == 48
    generated = output.read_text()
    for version in ("2.5", "2.7"):
        assert block(NEW.read_text(), f"// BEGIN V{version} PURE HELPERS", f"// END V{version} PURE HELPERS").strip() in generated
    assert "request." not in generated
    assert "runtime.error(" in generated
    assert "48 Pine contracts PASS" in generated
