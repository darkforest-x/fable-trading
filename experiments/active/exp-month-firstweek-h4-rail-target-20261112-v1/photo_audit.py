"""Trace the frozen V1 entry decisions for the owner's 15 annotated markets."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "exp-month-firstweek-all-replay-20261111-v1"))
from replay import HOUR, OUT, WINDOW, read_raw


def audit():
    photos = pd.read_csv(OUT.parent / "owner_reference/原图15张_OKX与币安逐张对照.csv")
    keys = pd.read_csv(OUT / "group_first_watch.csv.gz", float_precision="round_trip")
    fills = pd.read_csv(OUT / "h4_strategy/fills.csv").query("sample == 'signal'").set_index("pair_id")
    events = keys.set_index(["symbol","month","side"],verify_integrity=True)
    rows = []
    for photo in photos.to_dict("records"):
        symbol, month = photo["合约"], photo["区间月（纽约）"]
        side = 1 if photo["侧"] == "下" else -1
        key = (symbol, month, "long" if side == 1 else "short")
        row = dict(symbol=symbol, month=month, side=key[-1], image=photo["图片"])
        if key not in events.index:
            row["result"] = photo["说明"] if pd.notna(photo["说明"]) else "无币安首碰记录"
        else:
            event = events.loc[key]
            raw = read_raw(symbol)
            start = int(event.touch_ms) // (12 * HOUR) * (12 * HOUR)
            begin = raw[:,0].searchsorted(start)
            candles = raw[begin:begin+16]
            row["pair_id"] = event.id
            row["first_touch_beijing"] = pd.to_datetime(event.touch_ms, unit="ms", utc=True).tz_convert("Asia/Shanghai")
            assert row["first_touch_beijing"].strftime("%Y-%m-%d %H:%M") == photo["币安_首次触轨小时（北京）"]
            if (len(candles) < 16 or not np.array_equal(candles[:, 0], start + np.arange(16) * HOUR)
                    or start + 16*HOUR > WINDOW[month][2]):
                result = "第四小时之前区间到期或小时数据不完整"
            else:
                close = candles[11,4]
                four = candles[12:,4]
                held = side * (four - event.rail) / event.rail
                if side * (close - event.rail) <= 0:
                    result = "首碰12H未收回"
                elif side * (four[0] - close) > 0:
                    result = "第一小时已朝反转方向走"
                elif np.any(held < -1e-12) or held[-1] <= 1e-12:
                    result = "前四小时未持续守轨"
                elif side == 1 and not (four[1:] > close).any():
                    result = "下轨未重新收高，仅观察"
                else:
                    result = "主策略入选"
            row["result"] = result
            present = event.id in fills.index
            included = present and fills.loc[event.id,"group"] != "下轨未重新收高"
            assert included == (result == "主策略入选")
            if included:
                row["v1_exit_reason"], row["v1_net_pct"] = fills.loc[event.id,["exit_reason","net_pct"]]
        rows.append(row)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    table = audit()
    table.to_csv(HERE / "photo_coverage.csv", index=False)
    print(table.result.value_counts().to_string())
