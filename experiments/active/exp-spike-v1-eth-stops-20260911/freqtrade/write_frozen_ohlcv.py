"""Write only the already-frozen OKX ETH OHLCV into this experiment's FT data dir."""
from pathlib import Path
import sys
import pandas as pd
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from yoyo.evaluation.spike_v1_twoyear_allmarkets import aggregate
from freqtrade.data.history.datahandlers import get_datahandler
from freqtrade.enums import CandleType
EXP=Path(__file__).resolve().parents[1]
src=ROOT/'experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/data/normalized/okx/ETH-USDT-SWAP_30m.csv.gz'
raw=pd.read_csv(src,index_col=0,parse_dates=True); raw.index=pd.to_datetime(raw.index,utc=True)
handler=get_datahandler(EXP/'data','feather')
for minutes,tf in ((30,'30m'),(60,'1h'),(240,'4h')):
    df=aggregate(raw,minutes).reset_index(names='date')
    # The source remains the documented OKX linear SWAP.  This spot-mode
    # container only supplies Freqtrade's offline bar executor and avoids it
    # inventing unavailable funding rows; it never represents a spot result.
    handler.ohlcv_store('ETH/USDT',tf,df[['date','open','high','low','close','volume']],CandleType.SPOT)
