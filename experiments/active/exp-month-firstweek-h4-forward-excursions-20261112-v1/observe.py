"""Forward price excursions from the fourth H1 close after a first-touch 12h reclaim.

Membership uses only the frozen first recorded contract/month/rail, the original
12h reclaim close, the original rail expiry, and the first four completed H1s.
Keep non-advancing hour one (ties allowed) and a held rail through hour four.
For the lower rail flag any strict close above the original 12h close on H2..4.
No later barrier, volatility, or outcome filters membership. The main group
includes paths already at an old rail-relative threshold. A separate prefix
flag permits comparison with still-open watches. After qualification, use
calendar hours irrespective of month end and require continuity per horizon.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "exp-month-firstweek-all-replay-20261111-v1"))
from replay import CUTOFF, HOUR, OUT, ROUNDTRIP, WINDOW, read_raw

HORIZONS = (24, 72, 168)
GROUPS = ("上轨守住", "下轨重新收高", "下轨未重新收高")
ORDERS = ("浮盈先", "浮亏先", "同根顺序不明", "仅有浮盈", "仅有浮亏", "全程持平")
INPUT_FIELDS = ["id", "symbol", "month", "side", "rail", "underlying", "touch_ms",
                "scheduled_close_ms", "first_close_state", "close_pct"]
PATHS = OUT / "h4_forward_excursions"


def hours(raw, start, count):
    index = np.searchsorted(raw[:, 0], start)
    bars = raw[index:index + count]
    expected = start + np.arange(count, dtype=np.int64) * HOUR
    if not np.array_equal(bars[:, 0], expected):
        return None
    assert np.isfinite(bars[:, 1:5]).all() and (bars[:, 1:5] > 0).all()
    return bars


def prior_barrier(bars, rail, sign):
    """Flag rail-relative +/- barriers touched by any H1 from the original touch to H4.

    If only the touch hour crossed a barrier on the same side on which it opened,
    and that hour did not close beyond it, the sequence relative to touch is unknown.
    Order between the two old barriers is immaterial to the 'neither reached' flag.
    """
    adverse_price = bars[:, 3] if sign == 1 else bars[:, 2]
    favorable_price = bars[:, 2] if sign == 1 else bars[:, 3]
    hit_good = sign * (favorable_price / rail - 1) >= .20 - 1e-12
    hit_bad = sign * (adverse_price / rail - 1) <= -.05 + 1e-12
    if (hit_good[1:] | hit_bad[1:]).any():
        return "已到旧阈值"
    if not (hit_good[0] or hit_bad[0]):
        return "未到旧阈值"
    opening, closing = sign * (bars[0, [1, 4]] / rail - 1)
    good_certain = hit_good[0] and (opening <= 0 or closing >= .20 - 1e-12)
    bad_certain = hit_bad[0] and (opening >= 0 or closing <= -.05 + 1e-12)
    if good_certain or bad_certain or (hit_good[0] and hit_bad[0]):
        return "已到旧阈值"
    return "触价小时内顺序不明"


def qualify(raw, event, funnel):
    sign = 1 if event.side == "long" else -1
    t0 = event.scheduled_close_ms
    start = t0 + 4 * HOUR
    if start > min(CUTOFF, WINDOW[event.month][2]):
        funnel["第四根未在有效期内收盘"] += 1
        return None
    bars = hours(raw, int(event.touch_ms), (start - int(event.touch_ms)) // HOUR)
    if bars is None:
        funnel["前缀小时线不完整"] += 1
        return None
    assert bars[0, 3] <= event.rail <= bars[0, 2]
    reclaim_close = bars[-5, 4]
    closes = bars[-4:, 4]
    assert np.isclose(sign * (reclaim_close / event.rail - 1) * 100, event.close_pct, atol=1e-8)
    if sign * (closes[0] - reclaim_close) > 0:
        funnel["第一根已推进"] += 1
        return None
    # Match the previous close-state comparison. A final equality is not an inside close.
    signed_distance = sign * (closes - event.rail) / event.rail
    if (signed_distance < -1e-12).any() or signed_distance[-1] <= 1e-12:
        funnel["截至第四根未持续守住"] += 1
        return None
    group = GROUPS[0]
    if sign == 1:
        group = GROUPS[1] if (closes[1:] > reclaim_close).any() else GROUPS[2]
    funnel["符合条件"] += 1
    return dict(id=event.id, symbol=event.symbol, month=event.month, underlying=event.underlying,
                group=group, rail=event.rail, decision_ms=start, decision_close=closes[-1],
                reclaim_close=reclaim_close, prior=prior_barrier(bars, event.rail, sign))


def excursion(future, reference, sign):
    """Use future H1 highs/lows only. Record first occurrences of both window extremes.

    A zero excursion has no event time. With both positive extrema in distinct
    hours, their order is known. With both first reached in one hour report
    unresolved intrabar order, irrespective of that hour's close.
    """
    high_i = int(np.argmax(future[:, 2]))
    low_i = int(np.argmin(future[:, 3]))
    favorable_i, adverse_i = (high_i, low_i) if sign == 1 else (low_i, high_i)
    high, low = future[high_i, 2], future[low_i, 3]
    favorable_price, adverse_price = (high, low) if sign == 1 else (low, high)
    mfe = max(0., 100 * sign * (favorable_price - reference) / reference)
    mae = max(0., -100 * sign * (adverse_price - reference) / reference)
    if not mfe or not mae:
        order = "仅有浮盈" if mfe else ("仅有浮亏" if mae else "全程持平")
    elif favorable_i == adverse_i:
        order = "同根顺序不明"
    else:
        order = "浮盈先" if favorable_i < adverse_i else "浮亏先"
    return dict(mfe_pct=mfe, mae_pct=mae, order=order,
                mfe_hour=favorable_i + 1 if mfe else None,
                mae_hour=adverse_i + 1 if mae else None)


def build():
    cards = pd.read_csv(OUT / "group_first_watch.csv.gz", usecols=INPUT_FIELDS, **ROUNDTRIP)
    assert cards.id.is_unique and not cards.duplicated(["symbol", "month", "side"]).any()
    selected = cards[cards.first_close_state.isin(["收回", "实体在线内且长影"])]
    funnel = Counter({"全部首碰": len(cards), "首碰12H收回": len(selected)})
    records = []
    for symbol, batch in selected.groupby("symbol"):
        raw = read_raw(symbol)
        raw = raw[raw[:, 0] + HOUR <= CUTOFF]
        for event in batch.itertuples(index=False):
            case = qualify(raw, event, funnel)
            if case is None:
                continue
            for horizon in HORIZONS:
                future = hours(raw, case["decision_ms"], horizon)
                status = "完整" if future is not None else "窗口行情不足"
                metrics = excursion(future, case["decision_close"], 1 if event.side == "long" else -1) if future is not None else {}
                records.append({**case, "horizon_hours": horizon, "status": status, **metrics})
    frame = pd.DataFrame(records)
    assert len(frame) == funnel["符合条件"] * len(HORIZONS)
    assert not frame.duplicated(["id", "horizon_hours"]).any()
    return frame, dict(funnel)


def summarize(frame):
    rows = []
    samples = {"全部形态": frame, "截至第四根未到旧阈值": frame[frame.prior.eq("未到旧阈值")]}
    for name, sample in samples.items():
        for (group, horizon), candidates in sample.groupby(["group", "horizon_hours"]):
            obs = candidates[candidates.status.eq("完整")]
            row = dict(范围=name, 形态=group, 窗口小时=horizon, 符合=len(candidates), 完整=len(obs),
                       缺行情=len(candidates)-len(obs))
            for metric, title in [("mfe_pct", "最大浮盈"), ("mae_pct", "最大浮亏幅度")]:
                for quantile, stat in [(.25, "P25"), (.5, "中位数"), (.75, "P75"), (1, "样本最大")]:
                    row[title+stat] = obs[metric].quantile(quantile)
            row.update(obs.order.value_counts().reindex(ORDERS, fill_value=0).to_dict())
            assert sum(row[label] for label in ORDERS) == len(obs)
            rows.append(row)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    frame, funnel = build()
    PATHS.mkdir(parents=True, exist_ok=True)
    frame.to_csv(PATHS / "excursions.csv", index=False, encoding="utf-8-sig")
    summary = summarize(frame)
    summary.to_csv(HERE / "summary.csv", index=False, encoding="utf-8-sig")
    (HERE / "population.json").write_text(json.dumps(funnel, ensure_ascii=False, indent=2) + "\n")
    print(funnel)
    print(summary.round(2).to_string(index=False))
