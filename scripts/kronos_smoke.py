"""Smoke test: does Kronos load and forecast on our own OKX 15m data?

Kronos (shiyu-coder/Kronos, AAAI 2026, MIT) is an open K-line foundation model --
45 exchanges, 12B bars, a tokenizer that quantises OHLCV into hierarchical
discrete tokens and a decoder-only transformer over them. The backlog item is
whether its representation beats our 28 hand features plus 19 alphas, which
today measured +17.82bp with a permutation p of 0.32.

Clone with `gh repo clone shiyu-coder/Kronos external/Kronos` (gitignored) and
install einops / huggingface_hub / safetensors -- a few MB, torch is already
here, so this does not violate the no-heavy-dependency rule.

Nothing is wired into the pipeline. This only answers whether the model runs
here, on our bars, at our window size, and how long one forecast costs -- the
facts needed before deciding whether the backlog item is worth a day.

Measured 2026-07-29 on the M4 via MPS, Kronos-small (24.7M): 39.8s to load, 13.8s
for one 72-bar forecast. At that rate the 25,602-candidate pool costs 98 hours on
a single sampled path, so generating per-candidate forecasts naively is not
viable. Three ways out, in order of preference: predict_batch (official, needs
equal lengths -- ours are), a shorter pred_len (we need "how far does it move",
not 72 individual bars), or the encoder representation without autoregressive
sampling.
"""
import sys, time
from pathlib import Path
P = Path("/Users/zhangzc/fable-trading")
sys.path.insert(0, str(P))
sys.path.insert(0, str(P/"external/Kronos"))
import pandas as pd, numpy as np
from src.data.loader import list_series, load_series

from model import Kronos, KronosTokenizer, KronosPredictor

t0 = time.perf_counter()
tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
mdl = Kronos.from_pretrained("NeoQuasar/Kronos-small")
print(f"加载 {time.perf_counter()-t0:.1f}s   参数 {sum(p.numel() for p in mdl.parameters())/1e6:.1f}M")

dev = "mps" if __import__("torch").backends.mps.is_available() else "cpu"
pred = KronosPredictor(mdl, tok, device=dev, max_context=512)
print(f"设备 {dev}")

series = list_series(bar="15m")
fr = load_series(series[("okx", "BTC_USDT_SWAP")])
fr["timestamps"] = pd.to_datetime(fr["open_time"], utc=True).dt.tz_localize(None)
LOOK, PRED = 400, 72                      # 72 = our label horizon
cut = len(fr) - PRED
x = fr.iloc[cut-LOOK:cut]
y_ts = fr.iloc[cut:cut+PRED]["timestamps"]
truth = fr.iloc[cut:cut+PRED]

t0 = time.perf_counter()
out = pred.predict(df=x[["open","high","low","close","volume"]].reset_index(drop=True),
                   x_timestamp=x["timestamps"].reset_index(drop=True),
                   y_timestamp=y_ts.reset_index(drop=True),
                   pred_len=PRED, T=1.0, top_p=0.9, sample_count=1)
dt = time.perf_counter()-t0
print(f"\n预测 {PRED} 根用时 {dt:.1f}s")
print(out.head(3)[["open","high","low","close"]])

entry = float(x["close"].iloc[-1])
p_ret = float(out["close"].iloc[-1])/entry - 1
a_ret = float(truth["close"].iloc[-1])/entry - 1
print(f"\n参照最后一根收盘 {entry:.1f}")
print(f"  预测 {PRED} 根后涨跌 {p_ret*100:+.2f}%")
print(f"  实际 {PRED} 根后涨跌 {a_ret*100:+.2f}%")
print(f"\n单次预测 {dt:.1f}s → 25,602 个候选需 {25602*dt/3600:.1f} 小时(单条路径)")
