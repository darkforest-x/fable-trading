"""Audit every fixed selected-pool entry against the original contemporaneous bars.

Source: owner 2026-09-20 requests per-trade diagnosis and charts like the prior
50-chart book. Signal features use OHLCV/ATR/MA/MD through signal_i only:
five-bar stop anchors, twenty-bar lows, two-bar confirmation price change,
and the causally known A/B line. Parent comparisons and post-entry/exit paths
are separately marked hindsight outcomes, never used to select entry rules.
No threshold, trading policy, price source, or live setting changes.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation import spike_v112_trade_book as book
from yoyo.evaluation import spike_v1_v8_be05 as fixed
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v112_sample_direction import path_metrics
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-v112-trade-review-20260920-v1")
SOURCE = Path("experiments/active/exp-spike-v112-selected-returns-20260919-v1")
LEDGER = SOURCE / "trades_with_controls.csv"
PAIRS = {"15m": (15, "1h", 60), "1h": (60, "4h", 240), "4h": (240, "1d", 1440)}


def time_x(index, stamp, minutes):
    """Map timestamps without clamping old higher-timeframe anchors to x=0."""
    times = index.asi8 / 60e9
    t = pd.Timestamp(stamp).value / 60e9
    if t < times[0]: return float((t - times[0]) / minutes)
    if t > times[-1]: return float(len(index) - 1 + (t - times[-1]) / minutes)
    return float(np.interp(t, times, np.arange(len(index))))


def context(symbol, tf, base, meta):
    # The old chart builder has no 4h option; extend its process-local adapter.
    book.PAIRS.update(PAIRS)
    ctx = book.rebuild(symbol, tf, base, meta)
    ctx["htf_frame"] = features(v11.bars_for(base, PAIRS[tf][2]))
    return ctx


def lines_for(ctx, i, source):
    """Frozen lines at this event; higher-bar closes precede their visibility."""
    frame, htf, H = ctx["frame"], ctx["htf_frame"], ctx["H"]
    lines = []
    for kind in (["chart", "htf"] if source == "both" else [source]):
        if kind == "chart":
            ln = ctx["lines"][int(ctx["trace"]["break_winner_uid"][i])]
            a, b, c, born = (int(ln[key]) for key in ("ax", "bx", "cx", "born_i"))
            k = i
            src, minutes = frame, ctx["minutes"]
            ap, bp, cp = ln["ap"], ln["bp"], ln["cp"]
            y = ap + (bp - ap) / (b - a) * (np.arange(len(frame)) - a)
        else:
            assert bool(H["known"][i])
            src, minutes = htf, PAIRS[{15: "15m", 60: "1h", 240: "4h"}[ctx["minutes"]]][2]
            stamp = lambda key: pd.Timestamp(H[key][i] * 60, unit="s", tz="UTC")
            a, b, c, k = [int(src.index.get_loc(stamp(key))) for key in ("ax_t", "bx_t", "cx_t", "break_t")]
            born_stamp = stamp("born_t")
            born = int(src.index.get_loc(born_stamp - pd.Timedelta(minutes=minutes)))
            assert src.index[k] + pd.Timedelta(minutes=minutes) <= frame.index[i]
            ap, bp, cp = H["ap"][i], H["bp"][i], H["cp"][i]
            idx = np.searchsorted(src.index.asi8, frame.index.asi8, side="right") - 1
            idx = np.maximum(idx, 0)
            fractional = idx + (frame.index.asi8 - src.index.asi8[idx]) / (minutes * 60e9)
            y = ap + (bp - ap) / (b - a) * (fractional - a)
        born_time = src.index[born] + pd.Timedelta(minutes=minutes)
        assert born_time <= frame.index[i] + pd.Timedelta(minutes=ctx["minutes"])
        lines.append({"kind": kind, "source_tf": ctx["htf"] if kind == "htf" else {15:"15m",60:"1h",240:"4h"}[ctx["minutes"]],
                      "a_time": str(src.index[a]), "b_time": str(src.index[b]), "c_time": str(src.index[c]),
                      "ap": float(ap), "bp": float(bp), "cp": float(cp), "born_time": str(born_time),
                      "break_time": str(src.index[k]), "line_at_signal": float(y[i]),
                      "confirm_two_bar_change_pct": float((src.close.iloc[k] / src.close.iloc[k-2] - 1) * 100),
                      "confirm_last_bar_change_pct": float((src.close.iloc[k] / src.close.iloc[k-1] - 1) * 100),
                      "values": y})
    return lines


def protection_trace(prepared, jt):
    """Observe the frozen baseline update function; validate each exit phase."""
    frame, spec = prepared.frame, prepared.spec
    e, x = int(jt["entry_i"]), int(jt["exit_i"])
    pos = {k: jt[k] for k in ("entry_price", "initial_risk", "initial_stop")}
    pos.update(side=1, protection=jt["initial_stop"], mfe_r=0., trail_armed=False)
    records, arm_i = [], None
    for k in range(e, x + 1):
        before = float(pos["protection"])
        exiting = k == x and not jt["censored"]
        row = {"bar_i": k, "bar_open": str(frame.index[k]), "active_stop": before,
               "armed_before": bool(pos["trail_armed"]), "exit_bar": exiting}
        if exiting:
            if "stop" in jt["exit_reason"]:
                assert frame.low.iloc[k] <= before
                np.testing.assert_allclose(jt["exit_price"], min(frame.open.iloc[k], before), rtol=1e-10)
            else:
                assert jt["exit_reason"] == "opposite_v6_next_open" and prepared.raw_side[k-1] == -1
                np.testing.assert_allclose(jt["exit_price"], frame.open.iloc[k], rtol=1e-10)
        else:
            assert not prepared.gap[k]
            assert frame.low.iloc[k] > before
            fixed._close_updates(pos, high=frame.high.iloc[k], low=frame.low.iloc[k], close=frame.close.iloc[k],
                                 atr=frame.atr.iloc[k], spec=spec, enable_be=False, events=[],
                                 context=prepared.context, arm="v8", policy="baseline", i=k)
            if arm_i is None and pos["trail_armed"]: arm_i = k
        row.update(next_stop=float(pos["protection"]), armed_after=bool(pos["trail_armed"]))
        records.append(row)
    np.testing.assert_allclose(pos["mfe_r"], jt["mfe_r"], atol=1e-8)
    np.testing.assert_allclose(pos["protection"], jt["protection"], atol=1e-8)
    return pd.DataFrame(records), arm_i


def stop_bar_bounds(raw, entry, risk, stop, prior_mfe):
    """Bound MFE before an active stop using ordered 5m bars, never its high blindly.

    Caller provides one complete exit chart bar. The high of the first 5m bar
    touching the stop is ambiguous unless its open already gaps below it.
    """
    lower = max(0., float(prior_mfe))
    for row in raw.itertuples():
        if row.open <= stop:
            return lower, lower, str(row.Index), False
        if row.low <= stop:
            upper = max(lower, (row.high - entry) / risk)
            return lower, upper, str(row.Index), upper > lower + 1e-10
        lower = max(lower, (row.high - entry) / risk)
    raise AssertionError("No raw 5m stop touch in a chart stop bar")


def describe(r):
    """Chinese factual narrative and explicitly untested improvement questions."""
    reason = r["exit_reason"]
    closed = r["status"] == "closed"
    if not closed:
        category = "数据结束仍持仓"
        why = f"截至数据结束仍持仓，不能按已平仓盈利或亏损归类。末端保护价{r['active_stop_at_exit']:.6g}。"
    elif "stop" in reason:
        category = "盈利保护退出" if r["net_r"] > 0 else (
            "收盘激活追踪后仍亏" if r["trail_ever_armed"] else
            "盘中到2R但收盘未激活" if r["mfe_lower_r"] >= 2 else
            "到过1R后回吐" if r["mfe_lower_r"] >= 1 else
            "前三根止损" if r["held_bars"] <= 2 else "推进不足后止损")
        why = f"持仓第{r['held_bars']+1}根K，最低价{r['exit_bar_low']:.6g}触及当时已生效保护价{r['active_stop_at_exit']:.6g}；按{book.REASON.get(reason,reason)}在{r['exit_price']:.6g}退出，扣费{r['net_r']:+.2f}R。"
    else:
        category = "反向确认盈利退出" if r["net_r"] > 0 else "反向确认亏损退出"
        why = f"上一根出现原始反向信号，下一根开盘{r['exit_price']:.6g}退出；这笔不是价格触及止损退出，扣费{r['net_r']:+.2f}R。"
    observations = [f"距V9 {r['bars_after_v9']}根，较父V9模拟入场价{r['entry_premium_pct']:+.2f}%；初始风险{r['initial_risk_frac']*100:.2f}%（{r['risk_atr']:.2f}个信号ATR）。"]
    observations.append(f"入场信号收盘{'在' if r['above_all_ma'] else '不在'}六均线上方，IMACD{'向上且增强' if r['momentum_ok'] else '未同时满足向上和增强'}，相对成交量{r['signal_rv']:.2f}倍。")
    if "stop" in reason and r["exit_5m_complete"]:
        observations.append(f"原账本最大浮盈{r['mfe_r']:.2f}R；5分钟审计得到实际止损前浮盈范围{r['mfe_lower_r']:.2f}–{r['mfe_upper_r']:.2f}R（若上下界不同，触及止损的5分钟内先后未知）。")
    elif "stop" in reason:
        observations.append(f"原账本最大浮盈{r['mfe_r']:.2f}R；退出根5分钟数据不完整，不能确定根内浮盈上界。")
    else:
        observations.append(f"截至退出前/数据末端，原账本最大浮盈{r['mfe_r']:.2f}R。")
    if r["trail_ever_armed"]:
        observations.append(f"已收盘触及2R并激活追踪；{'退出时' if closed else '数据末端'}保护在入场价{r['exit_stop_r']:+.2f}R，激活不保证保本。")
    elif not np.isfinite(r["max_close_r"]):
        observations.append("入场当根退出，无存活持仓收盘；追踪未激活。")
    else:
        observations.append(f"{'退出前' if closed else '截至数据末端'}最高收盘{r['max_close_r']:.2f}R，未收盘达到2R，原追踪未激活。")
    questions = []
    if r["bars_after_v9"] > 48: questions.append("等待超过48根，需检验突破时原启动结构是否已失效")
    if r["entry_premium_pct"] > 0: questions.append("已比V9入场更贵，需评估确认收益能否覆盖追价代价")
    if not r["above_all_ma"] or not r["momentum_ok"]: questions.append("突破当时支撑或动能不完整，但此前单加六均线门未改善全池，不能直接套门")
    if r["confirm_not_rising"]: questions.append("突破来源的两根确认期价格未上涨，需检验下降线下移与有效价格推进的区别")
    if r["below_break_line"]: questions.append("识别突破时本周期收盘已低于至少一条冻结投影线，需核查多周期确认时差")
    if r["mfe_lower_r"] >= 2 and not r["trail_ever_armed"]: questions.append("盘中高点与收盘触发存在差异；此前全池最高价激活试验未通过，不能凭此单直接修改")
    if r["trail_ever_armed"] and r["exit_stop_r"] < 0: questions.append("4ATR追踪在本单仍低于成本线；统一提保本可能损失其他赢家，需另做完整对照")
    if r.get("post_exit48_complete"):
        observations.append(f"退出下一根起48根内，最高回到原入场{r['post_exit48_max_r']:+.2f}R，最低到{r['post_exit48_min_r']:+.2f}R。")
        if closed and r["net_r"] < 0 and r["post_exit48_max_r"] >= 1:
            questions.append("退出后48根内曾回到+1R；属事后重启机会，不能证明放宽原止损会赚钱")
    elif closed:
        observations.append("退出后48根窗口不足或断档，不评价止损后是否重新启动。")
    if closed and r["net_r"] > 0: questions.append("作为盈利对照保留：任何更早止盈/更严过滤都需计算会损失多少这类赢家")
    return category, why, " ".join(observations), "；".join(questions) or "当前事实不足以提出特定改法；先保留原规则作为对照。"


def review_one(trade, ctx, base):
    """Reproduce the entry/exit, then attach causal context and outcome paths."""
    f, p = ctx["frame"], ctx["prepared"]
    i, s, e, x = [int(trade[k]) for k in ("signal_i", "box_entry_i", "entry_i", "exit_i")]
    status, jt = inc.attempt(p, i)
    assert status == trade["status"] and jt is not None
    for col in ("entry_i", "exit_i", "entry_price", "initial_stop", "initial_risk", "exit_price", "mfe_r", "net_r"):
        np.testing.assert_allclose(jt[col], trade[col], rtol=1e-9, atol=1e-10, equal_nan=True)
    assert jt["exit_reason"] == trade["exit_reason"]
    assert f.index[i] == pd.Timestamp(trade["signal_bar_open"])
    ps, parent = inc.attempt(p, s)
    assert parent is not None
    linepack = lines_for(ctx, i, trade["source"])
    trace, arm_i = protection_trace(p, jt)
    entry, risk = float(jt["entry_price"]), float(jt["initial_risk"])
    lower, upper, stop_t, ambiguous, five_complete = float(jt["mfe_r"]), float(jt["mfe_r"]), None, False, False
    if "stop" in jt["exit_reason"]:
        raw = base.loc[(base.index >= f.index[x]) & (base.index < f.index[x]+pd.Timedelta(minutes=ctx["minutes"]))]
        five_complete = len(raw) == ctx["minutes"]//5 and bool((raw.index.to_series().diff().dropna() == pd.Timedelta(minutes=5)).all())
        if five_complete:
            lower, upper, stop_t, ambiguous = stop_bar_bounds(raw, entry, risk, jt["protection"], jt["mfe_r"])
        else:
            upper = float("nan")
    paths = path_metrics(f, e, x, entry, jt["initial_stop"], "joint", ctx["minutes"])
    if status == "closed": np.testing.assert_allclose(paths["joint_mfe_rebuilt"], jt["mfe_r"], atol=1e-8)
    held = f.iloc[e:x if status == "closed" else x+1]
    extrema_stop = float(f.low.iloc[i-4:i+1].min() - .2*f.atr.iloc[i])
    atr_stop = float(f.close.iloc[i] - 2*f.atr.iloc[i])
    r = dict(trade)
    r.update(v9_status=ps, v9_entry=parent["entry_price"], v9_stop=parent["initial_stop"],
             v9_exit_time=str(parent["exit_time"]), v9_net_r=parent["net_r"], v9_mfe_r=parent["mfe_r"],
             parent_exit_before_joint=int(parent["exit_i"]) <= i and not parent["censored"],
             entry_premium_pct=(entry/parent["entry_price"]-1)*100,
             risk_atr=risk/float(f.atr.iloc[i]), stop_anchor="近5根低点减0.2ATR" if extrema_stop <= atr_stop else "信号收盘减2ATR",
             stop_above_prior20_low=jt["initial_stop"] > f.low.iloc[max(0,i-19):i+1].min(),
             signal_atr=float(f.atr.iloc[i]), signal_rv=float(f.rv.iloc[i]),
             above_all_ma=bool(f.close.iloc[i]>f.ropeHigh.iloc[i]),
             momentum_ok=bool(f.md.iloc[i]>f.sb.iloc[i] and f.md.iloc[i]>f.md.iloc[i-1]),
             signal_body_atr=float((f.close.iloc[i]-f.open.iloc[i])/f.atr.iloc[i]),
             signal_ma_distance_atr=float((f.close.iloc[i]-f.ropeHigh.iloc[i])/f.atr.iloc[i]),
             held_bars=x-e, max_close_r=float((held.close.max()-entry)/risk),
             trail_ever_armed=arm_i is not None, trail_arm_i=arm_i,
             active_stop_at_exit=float(jt["protection"]), exit_stop_r=(jt["protection"]-entry)/risk,
             exit_bar_open=float(f.open.iloc[x]), exit_bar_high=float(f.high.iloc[x]), exit_bar_low=float(f.low.iloc[x]),
             mfe_lower_r=lower, mfe_upper_r=upper, exit_stop_5m_time=stop_t,
             exit_5m_complete=five_complete, stop_5m_order_ambiguous=ambiguous,
             below_break_line=any(f.close.iloc[i]<ln["line_at_signal"] for ln in linepack),
             confirm_not_rising=any(ln["confirm_two_bar_change_pct"]<=0 for ln in linepack),
             lines_json=json.dumps([{k:v for k,v in ln.items() if k!="values"} for ln in linepack],ensure_ascii=False))
    r.update({k.removeprefix("joint_"):v for k,v in paths.items() if k.startswith("joint_post_exit") or k.startswith("joint_h")})
    r["category"],r["exit_explanation"],r["observations"],r["research_questions"] = describe(r)
    trace.insert(0,"trade_key",trade["trade_key"])
    return r, trace, parent, jt, linepack


def worker(args):
    symbol, path, meta, records = args
    base = inc.guarded_5m(Path(path), study.START-pd.Timedelta(days=study.WARMUP_BARS))
    cache, rows, traces = {}, [], []
    for trade in records:
        tf = trade["timeframe"]
        if tf not in cache: cache[tf] = context(symbol,tf,base,meta)
        row, trace, _, _, _ = review_one(trade, cache[tf], base)
        rows.append(row); traces.append(trace)
    out = EXP/"streams"/symbol; out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(rows).to_csv(out/"reviews.csv",index=False)
    pd.concat(traces,ignore_index=True).to_csv(out/"protection_trace.csv.gz",index=False,compression={"method":"gzip","mtime":0})
    return {"symbol":symbol,"rows":len(rows),"source_path":str(path),"source_sha256":study.digest(Path(path))}


def main():
    declared=(*_local_transitive_python((Path(__file__),)),EXP/"PROJECT_PLAN.md")
    assert _committed(declared), "Commit builder and plan before rebuilding paths"
    receipt=json.loads((SOURCE/"receipt.json").read_text())
    assert study.digest(LEDGER)==receipt["files"][str(LEDGER)]
    ledger=pd.read_csv(LEDGER).sort_values(["timeframe","signal_bar_open","symbol"])
    ledger["review_id"]=[f"T{i:03d}" for i in range(1,len(ledger)+1)]
    assert len(ledger)==570
    files,meta=study.series_files(),study.symbol_meta()
    identity={"source_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
              "declared":{str(p):study.digest(p) for p in declared},"ledger_sha256":study.digest(LEDGER),
              "metadata_sha256":study.digest(study.EXCHANGE_INFO)}
    (EXP/"identity.json").write_text(json.dumps(identity,indent=2)+"\n")
    jobs=[(s,str(files[s]),meta[s],q.to_dict("records")) for s,q in ledger.groupby("symbol")]
    done=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        tasks=[pool.submit(worker,j) for j in jobs]
        for future in as_completed(tasks):
            done.append(future.result());print(json.dumps({"completed":len(done),"total":29,"symbol":done[-1]["symbol"]}),flush=True)
    reviews=pd.concat([pd.read_csv(EXP/"streams"/s/"reviews.csv") for s in sorted(ledger.symbol.unique())],ignore_index=True).sort_values("review_id")
    assert len(reviews)==570 and reviews.trade_key.nunique()==570
    reviews.to_csv(EXP/"reviews.csv",index=False)
    chinese={"review_id":"编号","symbol":"币种","timeframe":"周期","entry_time":"入场时间UTC","exit_time":"退出或边界时间UTC",
             "entry_price":"入场价","initial_stop":"初始止损","exit_price":"退出价","net_r":"净R","category":"路径分类",
             "exit_explanation":"为何退出","observations":"当时与后续事实","research_questions":"待验证不足","trade_key":"交易唯一键"}
    reviews[list(chinese)].rename(columns=chinese).to_csv(EXP/"逐笔分析570笔.csv",index=False,encoding="utf-8-sig")
    docs=EXP/"by_symbol";docs.mkdir(exist_ok=True)
    for symbol,group in reviews.groupby("symbol"):
        texts=[f"# {symbol}：{len(group)}笔逐笔复盘\n\n数据是原Binance回放，不是TV截图。所有改法仅为待验证问题；信号时事实与事后路径分开。\n"]
        for r in group.to_dict("records"):
            texts.append(f"## {r['review_id']} · {r['timeframe']} · {book.bj(r['entry_time'])} 北京\n\n**{r['category']}**\n\n{r['exit_explanation']}\n\n{r['observations']}\n\n待验证：{r['research_questions']}\n\n唯一键：`{r['trade_key']}`\n")
        (docs/f"{symbol}.md").write_text("\n".join(texts))
    summary=reviews.groupby(["timeframe","category"]).agg(n=("trade_key","size"),sum_r=("net_r","sum")).reset_index()
    summary.to_csv(EXP/"categories.csv",index=False)
    completion={"rows":570,"symbols":29,"joint_trade_parity":570,"protection_trace_parity":570,
                "inputs":done,"outputs":{str(p):study.digest(p) for p in sorted(EXP.rglob("*")) if p.is_file() and p.name!="completion.json"},
                "training_eligible":False,"production_eligible":False}
    (EXP/"completion.json").write_text(json.dumps(completion,ensure_ascii=False,indent=2)+"\n")
    print(summary.to_string(index=False))


if __name__=="__main__": main()
