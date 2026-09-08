"""Guard V2.5's display-only contract against changing existing release signals.

The on-platform companion probe executes the actual Pine helper functions.
These checks separately compare frozen calculation/visual blocks with V2.4;
they do not claim to compile Pine or to estimate strategy profitability.
"""

from pathlib import Path
import re

from yoyo.evaluation.build_imacd_v25_contract_probe import build


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "yoyo/evaluation/pine/imacd_dense_mtf_v2_4.pine"
NEW = ROOT / "yoyo/evaluation/pine/imacd_dense_mtf_v2_5.pine"


def without_v25(text: str) -> str:
    return re.sub(r"^[ \t]*// BEGIN V2\.5[^\n]*\n.*?^[ \t]*// END V2\.5[^\n]*\n", "", text, flags=re.M | re.S)


def statements(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("//")]


def test_release_and_original_system_calculation_are_identical():
    old = OLD.read_text().split("f_smma(float source", 1)[1].split("// BEGIN V2.4 DISPLAY RISK/REWARD", 1)[0]
    new = NEW.read_text().split("f_smma(float source", 1)[1].split("// BEGIN V2.5 LIFECYCLE AND R REFERENCES", 1)[0]
    assert statements(without_v25(new)) == statements(old)


def test_existing_plot_lines_and_all_five_alerts_are_unchanged():
    def protected_lines(text):
        return [line for line in text.splitlines() if re.match(r"(?:hline\(|plot\(|plotshape\(|alertcondition\(|glowMain =|glowSignal =|fill\(|bgcolor\()", line)]
    assert protected_lines(NEW.read_text()) == protected_lines(OLD.read_text())
    assert len([line for line in NEW.read_text().splitlines() if line.startswith("alertcondition(")]) == 5


def test_signal_inputs_and_prelaunch_retest_are_unchanged():
    def inputs(text):
        return statements(without_v25(text.split('string gSignal =', 1)[1].split("// BEGIN V2.4 DISPLAY INPUTS", 1)[0]))
    assert inputs(NEW.read_text()) == inputs(OLD.read_text())
    def retests(text):
        return statements(text.split("float retestMa =", 1)[1].split("color retestColor =", 1)[0])
    assert retests(NEW.read_text()) == retests(OLD.read_text())


def test_probe_contains_actual_helpers_without_reimplementation(tmp_path):
    path = tmp_path / "probe.pine"
    build(NEW, path)
    source = NEW.read_text()
    helpers = source.split("// BEGIN V2.5 PURE HELPERS\n", 1)[1].split("// END V2.5 PURE HELPERS", 1)[0]
    generated = path.read_text()
    assert helpers.strip() in generated
    assert len(re.findall(r'^    f_check\(', generated, flags=re.M)) == 24
    assert 'runtime.error(' in generated
    assert 'request.' not in generated
