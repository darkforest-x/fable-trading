"""Build a Pine-native synthetic probe from the delivered V12.8 state block.

Only the clock/OHLC adapter and inherited V12.6 frame inputs are replaced.
The candidate state machine itself is copied, not reimplemented in Python.
Run the resulting QA indicator in TradingView; runtime.error is a failed case.
This is an engineering fixture, never market data or economic evidence.
"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/active/exp-spike-v128-roll-hints-20260923-v1/native_probe.pine"


def build(source: str) -> str:
    block = source.split("// BEGIN V128 ROLL_HINTS\n", 1)[1].split("// END V128 ROLL_HINTS", 1)[0]
    state = "var int v128FrameEntryBar" + block.split("var int v128FrameEntryBar", 1)[1]
    for name in ("open", "high", "low", "close", "time_close", "time"):
        state = re.sub(r"\b" + name + r"\b", "qa_" + name, state)
    prelude = '''//@version=6
indicator("SPIKE V12.8 QA · synthetic only", overlay=true, max_labels_count=100)
// THIS IS A TEST HARNESS. Prices below are artificial; no market interpretation.
const int V128_H1_MS = 3600000
int qa_step = bar_index % 16
int qa_cycle = int(math.floor(bar_index / 16))
int qa_scenario = qa_cycle % 3
int qa_time = 1704067200000 + (qa_cycle * 16 + qa_step) * V128_H1_MS
int qa_time_close = qa_time + V128_H1_MS
var array<float> qa_closes = array.from(100.0,110.0,105.0,121.0,130.0,125.0,141.0,150.0,145.0,161.0,150.0,150.0,100.0,100.0,100.0,100.0)
var array<float> qa_highs = array.from(101.0,111.0,111.0,122.0,131.0,131.0,142.0,151.0,151.0,162.0,163.0,151.0,101.0,101.0,101.0,101.0)
var array<float> qa_lows = array.from(99.0,99.0,104.0,105.0,120.0,124.0,125.0,140.0,144.0,145.0,142.0,149.0,99.0,99.0,99.0,99.0)
float qa_close = array.get(qa_closes,qa_step)
float qa_high = array.get(qa_highs,qa_step)
float qa_low = array.get(qa_lows,qa_step)
float qa_open = qa_close
bool v128Enabled = true
bool v128Supported = true
bool v128ChartIsH1 = true
bool v126Minimal = true
bool dataGap = false
bool pricesValid = true
bool referenceStarted = qa_step == 0
int trendSide = qa_step < 12 ? 1 : 0
int entryBar = bar_index - qa_step
float entry = 100.0
float initialStop = 90.0
float risk = qa_scenario == 1 ? 100.0 : 10.0
float atr = 1.0
color bull = color.teal
color muted = color.gray
color gold = color.orange
color ink = chart.fg_color
float v128H1Open = qa_open
float v128H1High = qa_high
float v128H1Low = qa_low
float v128H1Close = qa_close
int v128H1Time = qa_time + (qa_scenario == 2 and qa_step >= 4 ? V128_H1_MS : 0)
int v128H1CloseTime = v128H1Time + V128_H1_MS
bool v128H1Valid = true
'''
    checks = '''
var int qa_checks = 0
if barstate.isconfirmed
    if qa_scenario == 0
        int expectedCount = qa_step >= 12 ? 0 : qa_step >= 6 ? 2 : qa_step >= 3 ? 1 : 0
        if v128CandidateCount != expectedCount
            runtime.error("QA normal candidate count, step=" + str.tostring(qa_step))
        if qa_step == 9 and math.abs(v128StructuralStop - (144.0 - syminfo.mintick)) > syminfo.mintick * 0.1
            runtime.error("QA reference stopped updating after candidate two")
        if qa_step == 10 and not v128EpisodePaused
            runtime.error("QA prior reference touch did not pause hints")
        if qa_step == 9 and v128CandidateEvent
            runtime.error("QA third candidate emitted")
    if qa_scenario == 1 and v128CandidateCount != 0
        runtime.error("QA below-2R candidate emitted")
    if qa_scenario == 2 and qa_step >= 4 and qa_step < 12
        if not v128EpisodeInvalid or v128CandidateCount != 1
            runtime.error("QA hour gap failed closed")
    if qa_step >= 12 and (v128CandidateCount != 0 or not na(v128FrameEntryBar))
        runtime.error("QA exit failed to reset frame")
    qa_checks += 1
var table qa_result = table.new(position.top_left,1,1)
if barstate.islast
    table.cell(qa_result,0,0,"V12.8 native state QA PASS · checked bars " + str.tostring(qa_checks),text_color=color.teal)
plot(qa_checks,"Synthetic bars checked",display=display.data_window)
'''
    return prelude + state + checks


if __name__ == "__main__":
    OUT.write_text(build((ROOT / "yoyo/evaluation/pine/spike_burst_v12_8.pine").read_text()))
    print(OUT)
