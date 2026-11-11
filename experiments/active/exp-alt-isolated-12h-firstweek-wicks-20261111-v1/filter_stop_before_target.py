"""Filter broad daily rail touches to confirmed +20% before -5% paths.

The event ledger is the existing Binance daily-first-touch output from
level_reversals.py. Entry is at the recorded rail. Use H1 OHLC, require a
continuous 45-day window starting at the touch hour, and exclude any bar whose
target/stop order cannot be established from OHLC. In the entry hour, a lone
threshold is accepted only when the open is on the post-entry side of the rail.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
from census12 import BASE, read_raw  # noqa: E402

OUT = BASE.parent / "results12"
EVENTS = OUT / "逐日首次碰轨_7天14天45天.csv.gz"
HOUR_MS = 3_600_000
HORIZON_HOURS = 45 * 24 + 1  # entry hour plus the original 1,080 following H1 bars
GROUP = ["币安合约", "纽约区间月", "方向"]
TIME_COL = "首次触价小时（北京）"
RETURN_COL = "45天最大反向幅度%"


def to_ms(value: str) -> int:
    return int(pd.Timestamp(value).tz_localize("Asia/Shanghai").tz_convert("UTC").value // 1_000_000)


def beijing_time(ms: int | None) -> str | None:
    if ms is None or pd.isna(ms):
        return None
    return pd.to_datetime(ms, unit="ms", utc=True).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M")


def classify(raw: np.ndarray, touch_ms: int, side: str, level: float) -> dict:
    """Return event status and first H1 threshold bars; times identify the H1 candle, not an exact tick."""
    clocks = raw[:, 0].astype(np.int64)
    pos = int(np.searchsorted(clocks, touch_ms))
    if pos >= len(raw) or clocks[pos] != touch_ms:
        return {"状态": "缺少触价小时", "完整45天": False, "TP阈值小时_ms": None, "SL阈值小时_ms": None,
                "结果小时_ms": None, "结果方向": None}
    end = pos + HORIZON_HOURS
    if end > len(raw):
        return {"状态": "45天不完整", "完整45天": False, "TP阈值小时_ms": None, "SL阈值小时_ms": None,
                "结果小时_ms": None, "结果方向": None}
    bars = raw[pos:end]
    expected = touch_ms + np.arange(HORIZON_HOURS, dtype=np.int64) * HOUR_MS
    if not np.array_equal(bars[:, 0].astype(np.int64), expected):
        return {"状态": "45天含缺口", "完整45天": False, "TP阈值小时_ms": None, "SL阈值小时_ms": None,
                "结果小时_ms": None, "结果方向": None}

    long = side == "下轨"
    target = level * (1.20 if long else 0.80)
    stop = level * (0.95 if long else 1.05)
    eps = abs(level) * 1e-12
    o, h, l = bars[:, 1], bars[:, 2], bars[:, 3]
    tp = h >= target - eps if long else l <= target + eps
    sl = l <= stop + eps if long else h >= stop - eps
    tp_ix = np.flatnonzero(tp)
    sl_ix = np.flatnonzero(sl)
    tp_ms = int(expected[tp_ix[0]]) if len(tp_ix) else None
    sl_ms = int(expected[sl_ix[0]]) if len(sl_ix) else None

    result = {"完整45天": True, "TP阈值小时_ms": tp_ms, "SL阈值小时_ms": sl_ms,
              "结果小时_ms": None, "结果方向": None}
    for i in range(HORIZON_HOURS):
        if not tp[i] and not sl[i]:
            continue
        if i == 0:
            # The touch is intrabar. A sole favorable hit is post-entry only if
            # the open began at/beyond the rail on the adverse side, and vice versa.
            if tp[i] and sl[i]:
                result["状态"] = "入场小时双阈值顺序不明"
                return result
            if tp[i]:
                known = o[i] <= level + eps if long else o[i] >= level - eps
                if not known:
                    result["状态"] = "入场小时TP可能先于入场"
                    return result
                result.update(状态="确认TP先于SL", 结果小时_ms=int(expected[i]), 结果方向="TP")
                return result
            known = o[i] >= level - eps if long else o[i] <= level + eps
            if not known:
                result["状态"] = "入场小时SL可能先于入场"
                return result
            result.update(状态="确认SL先于TP", 结果小时_ms=int(expected[i]), 结果方向="SL")
            return result

        if tp[i] and sl[i]:
            # A gap open through one barrier fixes the first threshold; otherwise
            # hourly extremes do not reveal which barrier traded first.
            if long and o[i] >= target - eps:
                first = "TP"
            elif long and o[i] <= stop + eps:
                first = "SL"
            elif not long and o[i] <= target + eps:
                first = "TP"
            elif not long and o[i] >= stop - eps:
                first = "SL"
            else:
                result["状态"] = "同小时双阈值顺序不明"
                return result
        else:
            first = "TP" if tp[i] else "SL"
        result.update(状态="确认TP先于SL" if first == "TP" else "确认SL先于TP",
                      结果小时_ms=int(expected[i]), 结果方向=first)
        return result
    result["状态"] = "45天内未触及阈值"
    return result


def main() -> None:
    daily = pd.read_csv(EVENTS)
    daily["触价小时_ms"] = daily[TIME_COL].map(to_ms).astype("int64")
    daily = daily.sort_values(GROUP + ["触价小时_ms"], kind="stable").reset_index(drop=True)
    # Keep only the earliest recorded touch per contract/month/side. This is
    # the exact denominator for the prior 6,912 first-touch target-reaching groups.
    first = daily.drop_duplicates(GROUP, keep="first").copy().reset_index(drop=True)
    first["触价日（北京）"] = pd.to_datetime(first["触价小时_ms"], unit="ms", utc=True).dt.tz_convert(
        "Asia/Shanghai").dt.strftime("%Y-%m-%d")
    old_positive = first[RETURN_COL].ge(20)
    if int(old_positive.sum()) != 6912:
        raise ValueError(f"Expected prior first-touch denominator 6,912; got {int(old_positive.sum())}")

    classified: list[dict] = []
    for symbol, indices in first.groupby("币安合约", sort=False).groups.items():
        raw = read_raw(symbol)
        if not len(raw):
            outcomes = [{"状态": "缺少H1数据", "完整45天": False, "TP阈值小时_ms": None,
                         "SL阈值小时_ms": None, "结果小时_ms": None, "结果方向": None} for _ in indices]
        else:
            outcomes = [classify(raw, int(first.at[i, "触价小时_ms"]), str(first.at[i, "方向"]),
                                 float(first.at[i, "轨价"])) for i in indices]
        for i, outcome in zip(indices, outcomes):
            classified.append({"row": int(i), **outcome})
    scored = pd.DataFrame(classified).set_index("row").sort_index()
    first = pd.concat([first, scored], axis=1)
    first["是否合约月度方向首触"] = True
    first["旧口径首触45天达20%"] = first[RETURN_COL].ge(20)
    first["TP阈值小时（北京）"] = first["TP阈值小时_ms"].map(beijing_time)
    first["SL阈值小时（北京）"] = first["SL阈值小时_ms"].map(beijing_time)
    first["结果小时（北京）"] = first["结果小时_ms"].map(beijing_time)
    classified_first = first.copy()
    old_pool = classified_first[classified_first["旧口径首触45天达20%"]].copy()
    confirmed = classified_first[classified_first["状态"].eq("确认TP先于SL")].copy()
    first = first.rename(columns={
        "币安合约": "币安合约", "纽约区间月": "纽约区间月", "方向": "触价轨道（下轨做多／上轨做空）",
        TIME_COL: "触价小时（北京）", "轨价": "入场轨价", "45天最大反向幅度%": "旧口径45天最大反向幅度%",
        "第几小时达到20%": "旧口径达20%小时数", "达20%前已知最大不利幅度%": "旧口径达20%前最大不利幅度%",
        "是否合约月度方向首触": "是否该合约月份方向首次记录触价", "触价日（北京）": "触价日期（北京）",
        "TP阈值小时（北京）": "TP阈值所在小时（北京）", "SL阈值小时（北京）": "SL阈值所在小时（北京）",
        "结果小时（北京）": "确认结果所在小时（北京）", "状态": "判定", "完整45天": "45天OHLC连续完整",
    })
    keep = ["币安合约", "纽约区间月", "触价轨道（下轨做多／上轨做空）", "触价日期（北京）",
            "触价小时（北京）", "入场轨价", "45天OHLC连续完整", "判定", "确认结果所在小时（北京）",
            "TP阈值所在小时（北京）", "SL阈值所在小时（北京）", "是否该合约月份方向首次记录触价",
            "旧口径首触45天达20%",
            "旧口径45天最大反向幅度%", "旧口径达20%小时数", "旧口径达20%前最大不利幅度%"]
    first[keep].sort_values(["触价小时（北京）", "币安合约"]).to_csv(
        OUT / "首次触价池_45天内20pct先于5pct止损.csv", index=False, encoding="utf-8-sig")
    old_pool = old_pool.rename(columns={
        "币安合约": "币安合约", "纽约区间月": "纽约区间月", "方向": "触价轨道（下轨做多／上轨做空）",
        TIME_COL: "触价小时（北京）", "轨价": "入场轨价", RETURN_COL: "旧口径45天最大反向幅度%",
        "第几小时达到20%": "旧口径达20%小时数", "达20%前已知最大不利幅度%": "旧口径达20%前最大不利幅度%",
        "是否合约月度方向首触": "是否该合约月份方向首次记录触价", "触价日（北京）": "触价日期（北京）",
        "TP阈值小时（北京）": "TP阈值所在小时（北京）", "SL阈值小时（北京）": "SL阈值所在小时（北京）",
        "结果小时（北京）": "确认结果所在小时（北京）", "状态": "判定", "完整45天": "45天OHLC连续完整",
    })
    old_pool[keep].sort_values(["触价小时（北京）", "币安合约"]).to_csv(
        OUT / "原口径6912首触达20pct组合交叉核对.csv", index=False, encoding="utf-8-sig")
    confirmed = confirmed.rename(columns={
        "币安合约": "币安合约", "纽约区间月": "纽约区间月", "方向": "触价轨道（下轨做多／上轨做空）",
        TIME_COL: "触价小时（北京）", "轨价": "入场轨价", RETURN_COL: "旧口径45天最大反向幅度%",
        "第几小时达到20%": "旧口径达20%小时数", "达20%前已知最大不利幅度%": "旧口径达20%前最大不利幅度%",
        "是否合约月度方向首触": "是否该合约月份方向首次记录触价", "触价日（北京）": "触价日期（北京）",
        "TP阈值小时（北京）": "TP阈值所在小时（北京）", "SL阈值小时（北京）": "SL阈值所在小时（北京）",
        "结果小时（北京）": "确认结果所在小时（北京）", "状态": "判定", "完整45天": "45天OHLC连续完整",
    })
    confirmed[keep].sort_values(["触价小时（北京）", "币安合约"]).to_csv(
        OUT / "首触确认机会_45天内20pct先于5pct止损.csv", index=False, encoding="utf-8-sig")
    cross = classified_first.loc[classified_first["旧口径首触45天达20%"], "状态"].value_counts().to_dict()
    summary = {
        "input_daily_touch_rows": int(len(daily)),
        "first_recorded_groups_symbol_month_side": int(len(first)),
        "first_recorded_groups_with_original_45d_path": int(classified_first[RETURN_COL].notna().sum()),
        "original_first_touch_45d_return_ge_20_groups": int(old_positive.sum()),
        "original_any_daily_45d_return_ge_20_events": int(daily[RETURN_COL].ge(20).sum()),
        "original_any_daily_45d_return_ge_20_groups": int(daily[daily[RETURN_COL].ge(20)].groupby(GROUP).ngroups),
        "first_recorded_status_counts": {str(k): int(v) for k, v in classified_first["状态"].value_counts(dropna=False).items()},
        "cross_tab_original_first_touch_ge_20_groups_denominator": int(classified_first["旧口径首触45天达20%"].sum()),
        "cross_tab_original_first_touch_ge_20_by_new_status": {str(k): int(v) for k, v in cross.items()},
        "first_recorded_groups_confirmed_tp_before_sl": int((classified_first["状态"] == "确认TP先于SL").sum()),
        "original_6912_groups_confirmed_tp_before_sl": int((
            classified_first["旧口径首触45天达20%"] & classified_first["状态"].eq("确认TP先于SL")
        ).sum()),
        "first_recorded_confirmed_opportunities": int(len(confirmed)),
        "time_resolution": "小时K线只给阈值所在小时；入场小时单阈值按开盘价相对入场轨保证发生在入场后，否则排除；双阈值同小时排除。后续小时双阈值仅在开盘已越过一侧阈值时定序，否则排除。",
        "horizon": "首次触价小时及其后1,080根连续H1 OHLC（原口径45天窗口）；阈值所在小时不是精确成交时间。只回放每个合约/月/方向的最早触价。",
        "input": str(EVENTS),
    }
    (OUT / "stop_before_target_filter_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
