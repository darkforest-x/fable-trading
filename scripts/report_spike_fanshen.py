"""Render the frozen Fanshen experiment with complete tables and owner CSVs.

Reads only precomputed results, never OHLCV or new parameter variants. Per-trade
tables are natural exits plus separately marked boundary positions; UTC-to-
Shanghai conversions change presentation only.
"""
import json
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/"experiments/active/exp-spike-fanshen-exit-multitf-20260914-v1"
OUT=EXP/"results"
REPORT=ROOT/"analysis/p1_spike_fanshen_exit_multitf_20260914.md"
LABELS={"original":"原V8"}
for signal in ["arrows","alerts"]:
    for mode in ["augment","replace_trail"]:
        for condition in ["profit","any"]:
            LABELS[f"{signal}_{mode}_{condition}"]=("箭头" if signal=="arrows" else "警报")+("＋原跟踪" if mode=="augment" else "替换跟踪")+("／盈利才平" if condition=="profit" else "／见信号就平")


def fmt(value,percent=False):
    if pd.isna(value):return "—"
    return f"{100*value:.1f}%" if percent else f"{value:.2f}"


def table(header,rows):
    return "\n".join(["|"+"|".join(header)+"|","|"+"|".join(["---"]*len(header))+"|"]+
                     ["|"+"|".join(map(str,row))+"|" for row in rows])+"\n"


def main():
    summary=pd.read_csv(OUT/"summary.csv")
    cash=pd.read_csv(OUT/"cash_summary.csv")
    receipt=json.loads((OUT/"run_receipt.json").read_text())
    inputs=json.loads((OUT/"input_receipts.json").read_text())
    parity=json.loads((OUT/"baseline_parity.json").read_text())
    primary="arrows_augment_profit"
    common=summary.loc[summary.window=="common"]
    parts=["# V8 × 翻身 Stoch：六周期退出回测\n",
           "日期：2026-09-14。市场为OKX ETH-USDT-SWAP。主方案在看数据前指定为：保留原V8保护，持仓扣费后盈利时，遇到同周期反向箭头于下一根开盘平仓。\n",
           "本轮比较9套冻结退出，共同窗口2026-01-01至2026-05-01（UTC，右端不含）；另报各周期可用历史。未使用holdout，没有调K/D或WVF参数，没有实盘操作。\n",
           "## 共同窗口：原V8与主方案\n"]
    rows=[]
    for m in [3,5,15,30,60,240]:
        old=common.loc[(common.minutes==m)&(common.policy=="original")].iloc[0]
        new=common.loc[(common.minutes==m)&(common.policy==primary)].iloc[0]
        rows.append([f"{m}m",f"{int(old.natural)}→{int(new.natural)}",f"{fmt(old.win_rate,True)}→{fmt(new.win_rate,True)}",
                     f"{fmt(old.sum_net_r)}→{fmt(new.sum_net_r)}",f"{int(old.max_net_loss_streak)}→{int(new.max_net_loss_streak)}",
                     f"{int(old.max_initial_stop_streak)}→{int(new.max_initial_stop_streak)}",fmt(new.paired_delta_net_r)])
    parts.append(table(["周期","自然平仓数","净胜率","总净R","最长净亏连单","最长初始止损连单","同入场净R变化"],rows))
    parts.append("总净R来自各方案各自完整串行交易；最后一列固定原V8入场，仅改退出，所以不应与两个串行总R直接相减混用。胜率只认扣费后净R>0；初始止损与所有净亏损分别统计。\n")
    improved=common.loc[(common.policy==primary)&(common.paired_delta_net_r>0),"minutes"].astype(int).tolist()
    parts.append(f"同入场主方案改善的周期：{improved}；空列表表示没有。这个描述不等于样本外有效性。共同窗口仅4个月，大周期交易少时必须结合逐笔和长期栏判断。\n")
    parts.append("## 提前止盈救回了什么，又少赚了什么\n")
    rows=[]
    for m in [3,5,15,30,60,240]:
        r=common.loc[(common.minutes==m)&(common.policy==primary)].iloc[0]
        rows.append([f"{m}m",int(r.paired_n),int(r.paired_improved),int(r.paired_worsened),
                     int(r.saved_losers),int(r.lost_3r_winners),int(r.fanshen_exits)])
    parts.append(table(["周期","同入场自然配对","改善笔数","变差笔数","原净亏变净赚","原毛3R变不足3R","实际翻身退出"],rows))
    parts.append("MFE是沿用原引擎的审慎记录：先判断止损，止损根的新有利极值不计入。因此MFE≥1R后净亏只用于同口径诊断，不能证明盘中先赚1R后才止损，更不能把MFE≥3R当固定3R止盈成交。\n")
    parts.append("## 1000U账户：固定1U与亏损翻倍\n")
    rows=[]
    for m in [3,5,15,30,60,240]:
        for p in ["original",primary]:
            sub=cash.loc[(cash.window=="common")&(cash.minutes==m)&(cash.policy==p)]
            f=sub.loc[sub.schedule=="fixed"].iloc[0]
            d=sub.loc[sub.schedule=="double"].iloc[0]
            rows.append([f"{m}m",LABELS[p],fmt(f.final_balance),fmt(d.final_balance),
                         fmt(d.max_realized_drawdown,True),int(d.n_natural),str(d.halt_reason) if pd.notna(d.halt_reason) else "未触发容量终止"])
    parts.append(table(["周期","退出","固定1U期末U","翻倍期末U","翻倍已实现最大回撤","翻倍自然笔数","资金约束"],rows))
    parts.append("资金口径：本金1000U；基础1U是价格止损风险，另计费用；成本固定为名义金额0.2%往返，保留既有10倍保证金容量假设。亏损后风险翻倍，只有累计净回本才重置，保本不会抹去前亏。容量不足时保持欠账，风险已超过现金则终止。未建模交易所标记价格强平、资金费率、额外滑点及盘中浮动权益；此回撤是已实现现金回撤，不能叫实盘强平概率。边界单按最后完整收盘估值，未纳入自然胜率。\n")
    parts.append("## 所有冻结方案与匹配随机对照\n")
    for window,title in [("common","共同2026年1—4月"),("available","各周期可用历史")]:
        parts.append(f"### {title}\n")
        for m in [3,5,15,30,60,240]:
            sub=summary.loc[(summary.window==window)&(summary.minutes==m)]
            parts.append(f"#### {m}分钟｜{sub.iloc[0].start} 至 2026-05-01 UTC\n")
            rows=[]
            for r in sub.itertuples():
                rows.append([LABELS[r.policy],int(r.natural),fmt(r.win_rate,True),fmt(r.sum_gross_r),
                             fmt(r.sum_net_r),fmt(r.profit_factor),int(r.max_net_loss_streak),
                             fmt(r.random_mean_net_r),fmt(r.excess_net_r),fmt(r.p_holm),int(r.matched_n)])
            parts.append(table(["退出","笔数","净胜率","毛R","净R","PF","净亏连单","随机均净R","超额均净R","校正p","匹配笔数"],rows))
    parts.append("随机对照按同ETH、周期、UTC月、方向、信号时点ATR/价格相对前120根的三分位桶匹配；每笔5个随机入场，采用同退出和成本。它是独立交易对照，不能拼成可执行随机账户。边界截尾样本重采，缺失数保存在summary与逐笔controls文件。月块符号置换9999次；每个窗口54项作Holm校正。四个月检验分辨率有限，未设研究期内参数选择。\n")
    parts.append("## 源码、时间与交易语义\n")
    parts.append("- K/D=Stochastic5／SMA3／SMA3；绿箭头为当前K、D均<20的金叉，红箭头为均>80的死叉。买入警报额外要求WVF绿色，卖出警报与红箭头相同。多仓看红信号，空仓看绿信号。\n- 九组仅比较反向箭头／警报、保留／移除原跟踪、盈利才平／所有反向信号平。全部保留初始5bar极值＋0.2ATR缓冲及2ATR风险下限，也保留原V6反向退出。替换跟踪不是无止损持有。\n- 盈利门按信号收盘、扣固定费用后判断。下一开盘可能跳空变亏，不能把它称为稳赚止盈。\n- 已触发止损优先于同根收盘箭头；下一开盘穿越保护则记止损；原V6反向与翻身同时出现时保留原优先级。\n")
    parts.append("Pine指标会在实时未收盘K线上重算；本实验只使用最终收盘值，避免把盘中暂现箭头当成稳定历史信号。参见[TradingView执行模型](https://www.tradingview.com/pine-script-docs/language/execution-model/)。\n")
    parts.append("## 数据与复核证据\n")
    rows=[]
    for r in inputs:
        rows.append([r["minutes"],r.get("rows"),r.get("first_open"),r.get("last_close"),
                     r.get("aggregation",{}).get("rows",r.get("rows"))])
    parts.append(table(["周期","源前缀根数","源首根UTC","最后完整收盘UTC","聚合根数"],rows))
    parts.append(f"Builder提交：`{receipt['builder_commit']}`。原V8逐笔parity：{len(parity)}组，共{sum(r['rows'] for r in parity)}行通过（共同窗与可用历史有重叠，不代表独立交易总数）。29项公式、缺失处理、时间边界和执行测试通过；哈希及读取边界见results/run_receipt.json、input_receipts.json、manifest.json。\n")
    parts.append("没有预测模型或事前交易评分排序，val AUC、top-decile排序毛／净收益及单特征排序基线不适用；不能用事后盈利挑top10%。零假设对照为原V8同入场配对与同成本匹配随机入场。样本数、胜率和时间范围均见完整表；正类率即净胜率。\n")
    parts.append("## 逐笔文件\n")
    delivery=EXP/"delivery"
    delivery.mkdir(exist_ok=True)
    for m in [3,5,15,30,60,240]:
        df=pd.read_csv(OUT/f"{m}m_common_{primary}_trades.csv.gz")
        if not df.empty:
            df["方向"]=df.side.map({1:"多",-1:"空"})
            df["入场北京时间"]=pd.to_datetime(df.entry_time,utc=True).dt.tz_convert("Asia/Shanghai").astype(str)
            df["退出根北京时间"]=pd.to_datetime(df.exit_time,utc=True).dt.tz_convert("Asia/Shanghai").astype(str)
            df["退出时间说明"]=df.apply(lambda x:"边界收盘估值" if x.censored else ("开盘成交" if x.exit_at_open else "该K线内止损，精确秒未知"),axis=1)
            cols=["方向","入场北京时间","entry_price","initial_stop","退出根北京时间","exit_price","退出时间说明","exit_reason","gross_r","net_r","mfe_r","censored"]
            df=df[cols].rename(columns={"entry_price":"入场价","initial_stop":"初始止损","exit_price":"退出价","exit_reason":"退出原因","gross_r":"毛R","net_r":"净R","mfe_r":"审慎MFE_R","censored":"是否边界未平"})
        path=delivery/f"ETH_{m}m_primary_2026JanApr.csv"
        df.to_csv(path,index=False,encoding="utf-8-sig")
        parts.append(f"- [{m}分钟主方案逐笔CSV](../../experiments/active/{EXP.name}/delivery/{path.name})\n")
    parts.append(f"所有方案逐笔、原入场配对、匹配控制和资金账本在`{OUT.relative_to(ROOT)}`，summary.csv为108组完整统计；原源码在source/owner_fanshen_v1_20250706.pine。\n")
    parts.append("## 风险与诚实声明\n")
    parts.append("本配置消耗holdout次数为0；截至2026-05-01的历史已被多轮研究观察，不能冒充完全未见样本。共同窗口短；大周期少数交易和单一行情可能主导结果；不同可用历史长度不能直接跨周期比总收益。Python公式和执行已做定向验证，尚未完成TradingView原生逐信号一一对齐；源自Owner粘贴代码，不代表TV保存版本逐字节身份已认证。历史较好的方案仍是待验证候选，不能保证最多6连损或倍投必胜。\n")
    parts.append("## 复现命令\n")
    parts.append("```bash\ncd /Users/zhangzc/fable-trading\n.venv/bin/python -m pytest -q tests/test_spike_fanshen_exit.py tests/test_spike_fanshen_study.py tests/evaluation/test_spike_recovery_exit.py\n# 使用上述冻结builder；结果目录必须不存在，已有结果应保留并改用独立重跑目录\n.venv/bin/python -m yoyo.evaluation.spike_fanshen_study\n.venv/bin/python scripts/report_spike_fanshen.py\n.venv/bin/python scripts/md_to_html.py analysis/p1_spike_fanshen_exit_multitf_20260914.md --out-dir analysis/html\n```\n")
    parts.append("## 下一步\n")
    parts.append("先逐笔核对主要改善与受损交易，再决定是否单独冻结最有希望的周期与退出版本作独立验收。新增holdout评估须按配置记录已有或新取得的Owner明确授权；本次没有推进任何实盘配置。\n")
    REPORT.write_text("\n".join(parts))
    print(REPORT)


if __name__=="__main__":main()

