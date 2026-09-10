"""Display-only acceptance: frozen strategy bytes, overlay scope and lifecycle.

These are source contracts, not a Pine compiler or a backtest. TradingView's
native compilation and visual acceptance remain explicit separate checks.
The strongest contract removes ONLY the declared display layer,
then requires the entire file to equal the SHA-pinned original Pine source.
"""
import hashlib
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "yoyo/evaluation/pine/spike_burst_v1.pine"
DISPLAY = ROOT / "yoyo/evaluation/pine/spike_burst_v1_display.pine"
SOURCE_SHA = "18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2"
BEGIN, END = "// BEGIN BURST DISPLAY ONLY\n", "// END BURST DISPLAY ONLY\n"


def display_layer():
    source = DISPLAY.read_text()
    assert source.count(BEGIN) == source.count(END) == 1
    return source.split(BEGIN, 1)[1].split(END, 1)[0]


def test_entire_original_is_preserved_after_removing_only_display():
    original = BASE.read_bytes()
    assert hashlib.sha256(original).hexdigest() == SOURCE_SHA
    candidate = DISPLAY.read_text()
    start, end = candidate.index(BEGIN), candidate.index(END) + len(END)
    candidate = candidate[:start] + candidate[end:]
    assert candidate.encode() == original


def test_display_is_after_state_machine_and_cannot_write_strategy_variables():
    source = DISPLAY.read_text()
    assert source.index(BEGIN) > source.index("// END BURST STATE MACHINE")
    body = display_layer()
    assigned = re.findall(r"\b([A-Za-z_]\w*)\s*:=", body)
    assert assigned and all(name.startswith("rr") for name in assigned)
    assert not re.search(r"\b(?:plot|fill|barcolor|bgcolor|hline|alertcondition|request\.security)\s*\(", body)
    # Only confirmed state-derived peak is consumed, never a current bar high.
    code = "\n".join(line.split("//", 1)[0] for line in body.splitlines())
    assert not re.search(r"\b(?:high|low|open|close)\b", code)
    assert "if barstate.isconfirmed and showRisk and rrShow" in code
