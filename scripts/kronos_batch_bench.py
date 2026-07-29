"""Is predict_batch fast enough to score a pool? One forecast at a time was not.

Measured yesterday: Kronos-small takes 13.8s for a 72-bar forecast on MPS, so
scoring 25,602 candidates one at a time costs 98 hours. The official
predict_batch runs several series through the GPU together and requires equal
lookback and pred_len across the batch -- ours are equal by construction, since
every candidate uses the same window.

This measures the real speedup and the batch size where it stops helping, so the
backlog item carries a cost rather than a hope. Also checks a shorter horizon: we
need "how far does it move", not 72 individual bars, and generation cost is
linear in pred_len.
"""
import sys, time
from pathlib import Path
P = Path("/Users/zhangzc/fable-trading")
sys.path.insert(0, str(P)); sys.path.insert(0, str(P/"external/Kronos"))
import pandas as pd, numpy as np, torch
from src.data.loader import list_series, load_series
from model import Kronos, KronosTokenizer, KronosPredictor

tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
mdl = Kronos.from_pretrained("NeoQuasar/Kronos-small")
dev = "mps" if torch.backends.mps.is_available() else "cpu"
pred = KronosPredictor(mdl, tok, device=dev, max_context=512)
print(f"设备 {dev}\n")

series = list_series(bar="15m")
fr = load_series(series[("okx","BTC_USDT_SWAP")])
fr["timestamps"] = pd.to_datetime(fr["open_time"], utc=True).dt.tz_localize(None)
LOOK = 400

def make(n, plen):
    xs, xts, yts = [], [], []
    for k in range(n):
        cut = len(fr) - plen - k*10
        x = fr.iloc[cut-LOOK:cut]
        xs.append(x[["open","high","low","close","volume"]].reset_index(drop=True))
        xts.append(x["timestamps"].reset_index(drop=True))
        yts.append(fr.iloc[cut:cut+plen]["timestamps"].reset_index(drop=True))
    return xs, xts, yts

print(f"{'批大小':>7}{'预测长度':>9}{'用时':>9}{'每条':>9}{'25602条需要':>13}")
for plen in (72, 24, 12):
    for bs in (1, 8, 32):
        xs, xts, yts = make(bs, plen)
        t0=time.perf_counter()
        try:
            pred.predict_batch(df_list=xs, x_timestamp_list=xts, y_timestamp_list=yts,
                               pred_len=plen, T=1.0, top_p=0.9, sample_count=1, verbose=False)
        except Exception as e:
            print(f"{bs:>7}{plen:>9}   失败: {str(e)[:50]}"); continue
        dt=time.perf_counter()-t0
        per=dt/bs
        print(f"{bs:>7}{plen:>9}{dt:>8.1f}s{per:>8.2f}s{25602*per/3600:>12.1f}h")
