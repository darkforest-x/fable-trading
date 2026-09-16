"""Owner-authorized holdout read #1: why did one bar open a trade?

Scope is fixed by docs/HOLDOUT_LEDGER.md entry 1: one month of OKX
ETH-USDT-SWAP 5m, one question, no profit or loss computed and no version
comparison scored. The frozen pre-holdout reader is deliberately NOT used or
relaxed; this script reads its own bounded copy.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from yoyo.evaluation.parabolic_rsi_sar import diamonds, pine_sar  # noqa: E402
from yoyo.evaluation.spike_fanshen_exit import compute_signals  # noqa: E402
from yoyo.evaluation.spike_v6_bb_squeeze import _rsi_wilder  # noqa: E402

SRC = Path(sys.argv[1])
TARGETS = {"19:15 北京时间 (UTC+8)": "2026-08-19T11:15:00Z",
           "19:15 UTC": "2026-08-19T19:15:00Z"}

raw = pd.read_csv(SRC)
raw["open_time"] = pd.to_datetime(raw.open_time, utc=True)
f = raw.set_index("open_time").loc[:, ["open", "high", "low", "close"]].astype(float)
basis = f.close.rolling(200, min_periods=200).mean()
sd = f.close.rolling(200, min_periods=200).std(ddof=0)
f["upper"], f["lower"] = basis + 2 * sd, basis - 2 * sd
arrows = compute_signals(f.loc[:, ["high", "low", "close"]])
f["k"], f["d"] = arrows.k, arrows.d
f["arrow"] = np.where(arrows.arrow_long, 1, np.where(arrows.arrow_short, -1, 0))
f["v2_signal"] = np.where(f.high.lt(f.lower) & (f.arrow == 1), 1,
                          np.where(f.low.gt(f.upper) & (f.arrow == -1), -1, 0))
f["rsi"] = _rsi_wilder(f.close, 14)
sar, below = pine_sar(f.rsi.to_numpy(float))
d = diamonds(sar, below)
f["sar"] = sar
f["strong_diamond"] = np.where(d["strong_up"], 1, np.where(d["strong_dn"], -1, 0))
f["v3_gate_passes"] = np.where((f.v2_signal == 1) & (f.rsi < 30), 1,
                               np.where((f.v2_signal == -1) & (f.rsi > 70), -1, 0))

print("=== 2026-08-19 全天 v2 组合信号 ===")
day = f.loc["2026-08-19"]
hits = day[day.v2_signal != 0]
if hits.empty:
    print("这一天没有任何 v2 组合信号（整根含影线出 BB + Stoch 极区箭头）。")
else:
    for t, r in hits.iterrows():
        print(f"{t}  {'多' if r.v2_signal==1 else '空'}  close={r.close:.2f} high={r.high:.2f} low={r.low:.2f} "
              f"BB[{r.lower:.2f},{r.upper:.2f}] K={r.k:.1f} D={r.d:.1f} RSI={r.rsi:.2f} "
              f"→ v3门{'放行' if r.v3_gate_passes else '拦截'}")
print()
for label, stamp in TARGETS.items():
    ts = pd.Timestamp(stamp)
    if ts not in f.index:
        print(f"--- {label} ({ts}) 不在数据里 ---"); continue
    r = f.loc[ts]
    print(f"--- {label} → UTC {ts} ---")
    print(f"  O/H/L/C      {r.open:.2f} / {r.high:.2f} / {r.low:.2f} / {r.close:.2f}")
    print(f"  BB200±2σ     下轨 {r.lower:.2f}   上轨 {r.upper:.2f}")
    print(f"  整根出带?     高<下轨={bool(r.high < r.lower)}  低>上轨={bool(r.low > r.upper)}")
    print(f"  Stoch 5/3/3  K={r.k:.2f} D={r.d:.2f}  箭头={'多' if r.arrow==1 else '空' if r.arrow==-1 else '无'}")
    print(f"  v2 组合信号   {'多' if r.v2_signal==1 else '空' if r.v2_signal==-1 else '无'}")
    print(f"  RSI14        {r.rsi:.2f}   （<30 才允许做多 / >70 才允许做空）")
    print(f"  RSI 的 SAR    {r.sar:.2f}   大菱形◈={'多' if r.strong_diamond==1 else '空' if r.strong_diamond==-1 else '无'}")
    print(f"  v3 入场门     {'放行' if r.v3_gate_passes else '拦截'}")
    print()
window = f.loc["2026-08-19 09:00":"2026-08-19 21:00"]
print("=== 附近 12 小时内所有 v2 信号与 RSI ===")
sub = window[window.v2_signal != 0]
print("无" if sub.empty else sub.loc[:, ["close", "lower", "upper", "k", "d", "v2_signal", "rsi", "v3_gate_passes"]].round(2).to_string())
out = Path(__file__).resolve().parent / "bar_diagnosis.json"
rows = {}
for label, stamp in TARGETS.items():
    ts = pd.Timestamp(stamp)
    if ts in f.index:
        rows[label] = json.loads(f.loc[ts].to_json())
out.write_text(json.dumps(dict(source=str(SRC), holdout_read_index=1, scored=False,
                               day_signals=len(hits), bars=rows), ensure_ascii=False, indent=2) + "\n")
