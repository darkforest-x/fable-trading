"""Present saved 1H/4H transfer receipts without rerunning YOLO inference.

The only source-data read reproduces the earliest two confirmed inputs per
timeframe. A timestamp-bounded 15m prefix is aggregated with the frozen UTC
helper, checked against its saved aggregated CSV SHA, then rendered from the
same causal MA features. The exact unannotated input pixels must match the
inference SHA before separate audit/context copies are written. Context ends
at the actually consumed detection endpoint; returns never select examples.
run() may regenerate presentation artifacts from immutable saved receipts.
"""
from __future__ import annotations

import hashlib
import json
import subprocess

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from .imacd_yolo_timeframes import ROOT, EXP, DATA, END
from .imacd_yolo_confirmation import digest, prepare_window
from .imacd_formation_research import read_prefix, aggregate
from .imacd_startup_quality import build_features, MA_COLUMNS
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features


def fmt(value, digits=3):
    return f"{float(value):.{digits}f}" if value is not None and pd.notna(value) else "N/A"


def bjt(value):
    return pd.Timestamp(value).tz_convert("Asia/Shanghai").strftime("%m-%d %H:%M")


def timeframe(value):
    return {15: "15m", 60: "1H", 240: "4H"}[int(value)]


def example_images(accepted, summary):
    """Choose first two per timeframe by signal time/event id, never outcomes."""
    selected = accepted.sort_values(["signal_available_at", "event_id"])
    selected = selected.groupby("timeframe_min", sort=True).head(2)
    images_dir = EXP / "results/examples"
    images_dir.mkdir(parents=True, exist_ok=True)
    cache, records = {}, []
    for row in selected.itertuples(index=False):
        minutes = int(row.timeframe_min)
        key = f"{row.symbol}_{minutes}"
        if key not in cache:
            info = summary["inputs"][key]
            raw = aggregate(read_prefix(ROOT / info["raw_path"], end=END), minutes)
            if hashlib.sha256(raw.to_csv().encode()).hexdigest() != info["bounded_ohlcv_sha256"]:
                raise ValueError(f"aggregated source differs from inference: {key}")
            enriched = add_candidate_features(raw.reset_index(names="open_time"))
            proposals = pd.read_csv(DATA / f"{key}_proposals.csv.gz").set_index("detection_id")
            if not proposals.index.is_unique:
                raise ValueError(f"duplicate model detection ids: {key}")
            cache[key] = raw, enriched, proposals, build_features(raw)
        raw, enriched, proposals, features = cache[key]
        proposal = proposals.loc[row.model_detection_id]
        p, e = int(row.signal_i), int(row.confirmation_i)
        if int(proposal.window_end_i) != e:
            raise ValueError("example endpoint differs from detection receipt")
        available = raw.index[e] + pd.Timedelta(minutes=minutes)
        if available != pd.Timestamp(row.confirmation_available_at):
            raise ValueError("example endpoint does not reproduce confirmation clock")
        image, transform, _ = prepare_window(enriched, e, int(proposal.window_len))
        pixel_sha = hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()
        if pixel_sha != proposal.input_pixel_sha256:
            raise ValueError(f"model input pixel mismatch: {row.event_id}")
        paths = {kind: images_dir / f"{row.event_id}_{kind}.png" for kind in ("input", "audit", "context")}
        if not cv2.imwrite(str(paths["input"]), image):
            raise OSError("failed to write verified model input")
        audit = image.copy()
        cx, cy = float(proposal.prediction_cx_norm), float(proposal.prediction_cy_norm)
        w, h = float(proposal.prediction_w_norm), float(proposal.prediction_h_norm)
        height, width = image.shape[:2]
        cv2.rectangle(audit, (int((cx-w/2)*width), int((cy-h/2)*height)),
                      (int((cx+w/2)*width), int((cy+h/2)*height)), (130, 155, 0), 3)
        local_p = p - int(proposal.window_start_i)
        if 0 <= local_p < transform.n_bars:
            x = int(transform.x_at(local_p))
            cv2.line(audit, (x, 15), (x, height-15), (50, 115, 230), 2)
        cv2.putText(audit, f"AUDIT COPY | {key} | IMACD orange | YOLO core teal",
                    (20, 30), cv2.FONT_HERSHEY_SIMPLEX, .6, (60, 60, 60), 1, cv2.LINE_AA)
        if not cv2.imwrite(str(paths["audit"]), audit):
            raise OSError("failed to write audit copy")
        start = max(0, min(int(row.setup_start_i)-20, p-100))
        b, f = raw.iloc[start:e+1], features.iloc[start:e+1]
        xs = np.arange(len(b))
        fig, (ax, osc) = plt.subplots(2, 1, figsize=(12, 6.5), sharex=True,
                                     gridspec_kw={"height_ratios": [3, 1]}, layout="constrained")
        fig.set_facecolor("#fafbfc")
        for axis in (ax, osc):
            axis.set_facecolor("#fafbfc")
            axis.spines[["top", "right"]].set_visible(False)
            axis.grid(axis="y", color="#dfe5eb", alpha=.5)
        for k, candle in enumerate(b.itertuples()):
            col = "#159b8d" if candle.close >= candle.open else "#d75768"
            ax.vlines(k, candle.low, candle.high, color=col, linewidth=.7)
            ax.add_patch(Rectangle((k-.32, min(candle.open, candle.close)), .64,
                max(abs(candle.close-candle.open), candle.open*.00001), color=col, linewidth=0))
        colors = ["#63a8a3", "#438d87", "#83a6cd", "#6888ba", "#9299a1", "#606a74"]
        for name, col in zip(MA_COLUMNS, colors):
            ax.plot(xs, f[name], color=col, linewidth=.8)
        ax.axvline(p-start, color="#db9846", linewidth=1.4, label="IMACD arrow")
        ax.axvline(e-start, color="#008a80", linewidth=1.2, linestyle="--", label="YOLO available")
        ax.axvspan(int(row.core_start_i)-start-.5, int(row.core_end_i)-start+.5,
                   color="#00a494", alpha=.14)
        for axis in (ax, osc):
            axis.axvspan(int(row.setup_start_i)-start-.5, p-start-.5, color="#d9a744", alpha=.10)
        side = "LONG" if row.side == 1 else "SHORT"
        ax.set_title(f"{row.symbol} / {timeframe(minutes)} / {side} | delay {row.delay_bars*minutes/60:g}h"
                     " | visible data ends at confirmation", loc="left", fontsize=11)
        ax.legend(loc="upper left", frameon=False, fontsize=8)
        osc.plot(xs, f.md, color="#527ed0", linewidth=1.2)
        osc.plot(xs, f.sb, color="#d39a3d", linewidth=1.1)
        osc.axhline(0, color="#8a939d", linewidth=.7)
        ticks = np.linspace(0, len(b)-1, 6).astype(int)
        osc.set_xticks(ticks, [t.strftime("%m-%d\n%H:%M") for t in b.index[ticks]])
        osc.set_xlabel("UTC candle OPEN; final candle closes before confirmation is available")
        fig.savefig(paths["context"], dpi=150)
        plt.close(fig)
        records.append(dict(event_id=row.event_id, timeframe_min=minutes,
            selection="first two confirmations per timeframe by signal_available_at, event_id",
            **{f"{kind}_path": str(path.relative_to(ROOT)) for kind, path in paths.items()},
            aggregate_sha256_verified=summary["inputs"][key]["bounded_ohlcv_sha256"],
            pixel_sha256_verified=pixel_sha, model_available_at=str(available)))
    return records


def report_table(rows, legacy=False):
    lines = ["| 品种 | 周期 | 原箭头 | 模型确认 | 等待失效 | 到期 | 数据截断 | 保留率 | 最长等待预算 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        minutes = 15 if legacy else int(row["timeframe_min"])
        wait = 2.25 if legacy else row.get("max_wait_hours", 9*minutes/60)
        retention = "N/A" if row['pass_rate_pct'] is None else fmt(row['pass_rate_pct'], 2)+"%"
        lines.append(f"| {row['symbol']} | {timeframe(minutes)} | {row['arrows']} | {row['confirmed']} | "
            f"{row['invalidated']} | {row['expired']} | {row['censored']} | {retention} | {wait:g}h |")
    return "\n".join(lines)


def run():
    """Idempotently create MD, HTML and examples from a committed report source."""
    source = "yoyo/evaluation/imacd_yolo_timeframes_report.py"
    subprocess.run(["git", "ls-files", "--error-unmatch", source], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL)
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", source], cwd=ROOT, text=True)
    if dirty.strip():
        raise ValueError("commit report source before rendering artifacts")
    summary = json.loads((EXP / "results/summary.json").read_text())
    for relative, expected in summary["files"].items():
        if digest(ROOT / relative) != expected:
            raise ValueError(f"frozen inference ledger changed: {relative}")
    d = pd.read_csv(DATA / "decisions.csv")
    kept = d.loc[d.status.eq("confirmed")].copy()
    if d.event_id.duplicated().any():
        raise ValueError("duplicate decision identities")
    if len(d) != sum(r["arrows"] for r in summary["table"]) or len(kept) != sum(r["confirmed"] for r in summary["table"]):
        raise ValueError("summary counts differ from saved decisions")
    images = example_images(kept, summary)
    prior_path = ROOT / "experiments/active/exp-imacd-yolo-confirmation-20260908-v1/results/summary.json"
    if prior_path.exists():
        if digest(prior_path) != summary['prior_15m_summary_sha256']:
            raise ValueError('prior15m comparison receipt changed')
        prior = json.loads(prior_path.read_text())
        comparison = report_table(prior["table"], legacy=True)
        prior_note = f"已保存15m summary SHA：`{digest(prior_path)}`。这里只读取旧汇总，未重跑15m推理。"
    else:
        comparison, prior_note = "旧15m汇总不可用，本报告不填补比较数字。", ""
    details = ["| 品种/周期/方向 | 原箭头收盘（北京） | 模型确认（北京） | 等待 | 原次开盘 | 确认次开盘 | 沿方向位移 | conf |",
               "|---|---|---|---:|---:|---:|---:|---:|"]
    for row in kept.sort_values(["timeframe_min", "signal_available_at", "event_id"]).itertuples(index=False):
        details.append(f"| {row.symbol}/{timeframe(row.timeframe_min)}/{'多' if row.side == 1 else '空'} | "
            f"{bjt(row.signal_available_at)} | {bjt(row.confirmation_available_at)} | "
            f"{row.delay_bars*row.timeframe_min/60:g}h | {row.baseline_next_open:.8g} | "
            f"{row.confirmed_next_open:.8g} | {row.displacement_bp/100:+.4f}% | {row.model_confidence:.3f} |")
    if kept.empty:
        details.append("| 本轮没有模型确认事件；不生成价格位移统计或案例图 | — | — | — | — | — | — | — |")
    null_lines = ["| 周期 | 有效候选的真实同向确认 | 方向打乱次数 | 随机均值 | 单侧p | Holm p |",
                  "|---|---:|---:|---:|---:|---:|"]
    for minutes in (60, 240):
        null = summary["direction_null"].get(str(minutes), {})
        null_lines.append(f"| {timeframe(minutes)} | {null.get('observed', 'N/A')} | {null.get('permutations', 'N/A')} | "
            f"{fmt(null.get('null_mean'), 3)} | {fmt(null.get('p_one_sided'), 6)} | {fmt(null.get('p_holm'), 6)} |")
    coverage = ["| 输入 | 聚合周期 | 已解析完整K线 | 首根UTC | 末根UTC | 推理窗口 | 源15m缓存 |",
                "|---|---|---:|---|---|---:|---|"]
    for key, info in summary["inputs"].items():
        coverage.append(f"| {key} | {timeframe(info['minutes'])} | {info['parsed_rows']} | {info['parsed_first']} | "
            f"{info['parsed_last']} | {info.get('windows_scored', 'N/A')} | `{info['raw_path']}` |")
    review_path = EXP / "results/independent_review.json"
    review = ("独立复核文件尚未生成；不宣称独立复核通过。" if not review_path.exists() else
              "已保存独立复核收据（以下为文件原文，不新增通过数量）：\n\n```json\n"
              + json.dumps(json.loads(review_path.read_text()), ensure_ascii=False, indent=2) + "\n```")
    retained = 100*len(kept)/len(d) if len(d) else None
    immediate = int(kept.delay_bars.eq(0).sum())
    censored = int(d.status.eq("censored_end").sum())
    late = kept.loc[kept.delay_bars.gt(0)]
    delay_hours = ', '.join(f'{v:g}小时' for v in sorted(
        (late.delay_bars*late.timeframe_min/60).unique())) or '无'
    long_count, short_count = int(kept.side.eq(1).sum()), int(kept.side.eq(-1).sum())
    worst_move = 'N/A' if kept.empty else fmt(kept.displacement_bp.max()/100,4)+'%'
    md = f"""# IMACD → YOLO 的1H/4H迁移：先核对确认与等待时钟

本轮读取冻结1H/4H推理台账，BTC、ETH合计{len(d)}个原始箭头，模型确认{len(kept)}个，保留率{fmt(retained, 2)}%。其中{immediate}个在原箭头收盘即可确认，{len(kept)-immediate}个需要等待，{censored}个因数据末端缺少完整随访而截断。**这些数字描述候选筛选与确认延迟，不能称为真正噪音减少、预测准确率或盈利改善。**

确认方向为多头{long_count}、空头{short_count}；实际非零等待为{delay_hours}，最不利的沿方向追价为{worst_move}。本次确认集中在空头时，不能据此声称能抓住Owner截图中的多头大启动。4H没有足够候选时，应将“没有原箭头”与“有箭头但模型没有确认”分开解释。

这次把原生15m研究模型直接用于完整聚合的1H/4H图像，没有重新训练。评价区间固定为2026-05-04至2026-07-01 UTC（右开），只用BTC和ETH；三个周期的箭头集合不同，15m旧表仅作工程参照，不能把保留率差异归因为哪个周期更赚钱。

## 新周期逐项结果

{report_table(summary['table'])}

基线放行所有原始IMACD可见focusRelease；试验追加同一个冻结YOLO确认规则。失效、到期和截断是程序状态，均不是人工标注的错误行情。存在截断时，完整随访子集的结果不能冒充所有实时可用候选的结论。

## 与已保存15m试验并列

{comparison}

{prior_note} 旧说明见[15m工程报告]({ROOT / 'analysis/html/p1_imacd_yolo_confirmation_20260908.html'})。本报告不改写其历史数字或结论。

## 时间预算与跨周期含义

箭头p收盘后，可检查p至p+9共10个收盘端点，最多实际等待9根本周期K线：1H为9小时，4H为36小时。按第一次满足条件的确认端点记录，不能挑后面更好看的框或价格。等待中md回零、反向或未知就永久作废；对应核心必须为4/5根，核心后2–9根，与原冻结蓄势区加箭头存在真实K线交集。这种交集不等于人工确认了同一形态。

| 时间结构 | 15m旧试验 | 1H迁移 | 4H迁移 |
|---|---:|---:|---:|
| W18/W19覆盖时长 | 4.5/4.75小时 | 18/19小时 | 72/76小时 |
| 核心4/5根 | 1/1.25小时 | 4/5小时 | 16/20小时 |
| 核心后2–9根 | 0.5–2.25小时 | 2–9小时 | 8–36小时 |
| 箭头后最多等待 | 2.25小时 | 9小时 | 36小时 |

相同根数不代表相同市场时间尺度。模型原生训练域为15m，跨周期可能有形态、波动和事件频率分布变化；本次只评价直接迁移的工程行为，没有证明该权重已经适配1H/4H。9根等待预算事前固定，未搜索最优值。

模型权重SHA：`{summary['model_sha256']}`；推理源码冻结commit：`{summary['source_commit']}`。图像由训练同源白底K线与close源六均线渲染，不是TradingView界面截图。沿用W18/W19、conf0.25、NMS0.70、imgsz1280及冻结推理配置；conf不是行情成功概率。接口背景见[Ultralytics官方predict文档](https://docs.ultralytics.com/modes/predict/)。本报告没有加载模型或调用predict。

## 确认明细：价格位移不是盈亏

{chr(10).join(details)}

沿方向位移为 `side × (确认后下一开盘 / 原箭头后下一开盘 − 1)`。正值表示等待后沿信号方向更贵，负值表示更便宜；不是已实现利润或盘口滑点。新确认的可用时间是实际检测窗右端K线的收盘，不回填到原箭头，也不沿用原箭头价格。离线完整随访筛选不能直接复制到实时循环：在线应在当时已知数据上确认或继续等待。

## 条件化方向零假设

{chr(10).join(null_lines)}

对每个周期，固定原箭头方向下的md有效时间池与模型几何池，在同币同月内打乱箭头方向；两个周期的p由冻结程序进行Holm校正。这检验的是条件化方向关联，**不检验真假形态、未来趋势、盈利或去噪成功**。两套信号都由价格与均线生成，存在机械相关的可能。空候选、空确认或缺少方向可交换性时，应按台账记录解释；不能用小p替代独立人工金标。

## 数据、授权与指标适用范围

{chr(10).join(coverage)}

源15m缓存按时间戳读取到固定结束点，UTC仅聚合完整1H/4H组；较早历史用于递归指标播种，候选评价仅在固定区间内。模型训练血缘、聚合摘要、模型输入像素SHA及逐端点轨迹保留在实验台账，供后续独立审核。各周期是独立配置；依据Owner既有“任何时间段数据都可以使用不要有任何限制”的明确授权，**这是1H迁移配置第1次消耗holdout，也是4H迁移配置第1次消耗holdout**。这些市场时间此前已被研究接触，不能称全新盲测；本轮没有依据结果换权重、阈值、等待预算或品种。

BTC4H本轮无原始箭头，因此按条件化流程未触发模型；ETH4H两条候选共检查40张W18/W19输入，阈值0.25以上的原始模型框为0，未确认不是被后续方向或失效门删掉。推理期间现装Ultralytics对half参数、pandas对空组拼接给出弃用提示，均正常完成，未更换版本、设备或数据源。

确认率不是有标签正类率；没有新增监督训练，val样本数不适用。没有真假金标，AUC、accuracy、precision、recall均不适用。没有预注册经济退出/仓位合同，因此成本、TP/SL、胜率、净收益、最大回撤、top-decile毛净收益、经济匹配随机入场均为N/A。上述方向打乱是本工程任务的零假设对照，不代替经济对照。单规则基线为原始IMACD全部箭头。

## 可复现案例：每周期按时间取前两条确认

只显示截至模型实际消耗端点的K线；浅橙区是冻结蓄势段，橙线为原箭头、青色虚线为模型确认，青色区域为检测核心。模型原始输入与审核注释副本分开保存，前者逐张验证像素SHA。没有确认的周期不挑选替代成功案例。
"""
    for minutes in (60, 240):
        examples = [item for item in images if item["timeframe_min"] == minutes]
        md += f"\n### {timeframe(minutes)}\n\n"
        if not examples:
            md += "本周期没有确认事件，不生成示例图。\n"
        for item in examples:
            md += (f"\n**{item['event_id']}**\n\n![截至确认端点的上下文](../{item['context_path']})\n\n"
                   f"![独立审核副本](../{item['audit_path']})\n\n"
                   f"[无注释模型原始输入]({ROOT / item['input_path']})；像素SHA `{item['pixel_sha256_verified']}`。\n")
    md += f"""
## 验证回执与复现

冻结运行的内置验证回执原文如下；没有将其冒称独立审核，也不从代码存在推断测试通过数量。

```json
{json.dumps(summary.get('validation', {'status': 'not recorded'}), ensure_ascii=False, indent=2)}
```

{review}

首次推理仅供尚无运行收据的相同环境：

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.evaluation.imacd_yolo_timeframes
.venv/bin/python -m yoyo.evaluation.imacd_yolo_timeframes_verify
.venv/bin/python -m yoyo.evaluation.imacd_yolo_timeframes_report
```

当前结果复核与重新生成报告只运行后两条Python命令。校验器读取保存轨迹，报告先校验台账SHA，案例再校验聚合OHLCV摘要和输入像素SHA，不再次推理。报告源码必须先提交；生成MD后立即用仓库scripts/md_to_html.py转换HTML。原始缓存、权重和大型轨迹数据不入git，由来源路径与摘要定位。

## 风险与诚实声明

- 只覆盖两个品种和已暴露历史中的固定区间，不代表全OKX或未来分布；1H/4H确认数少时尤其不能推广。
- 未评估删掉的箭头是否包含大行情，保留率更低既可能过滤无用候选，也可能漏掉机会。
- 更长周期把同样9根等待放大为9或36小时；理论可用时钟不含Mac扫描、推理、网络与通知延迟，尚未做全市场容量或影子在线验收。
- 模型与IMACD共有价格/均线输入；条件化方向关联不足以证明独立增益，框交集不足以证明语义同一。
- 后续如评估经济价值，需要先固定独立入场、退出、成本与样本规则。本轮不设新TP/SL，也不宣称优于原系统。
- TradingView、spike、Mac监控、TG/Bark、ACTIVE和执行配置均不在本报告写入范围；training_eligible=false，production_eligible=false。

## 下一步

工程轨迹复核见上方实际收据；后续应人工审核保留与删除的候选，区分语义一致性与仅有几何交集。4H样本覆盖不足需要独立冻结的新样本才能扩展结论；不能在本次固定试验里临时改阈值追求出框。上线或经济验证需要先明确合同，不能因为确认数下降就改通知门。
"""
    report = ROOT / "analysis/p1_imacd_yolo_timeframes_20260908.md"
    report.write_text(md)
    subprocess.run([str(ROOT / ".venv/bin/python"), "scripts/md_to_html.py", str(report),
                    "--out-dir", "analysis/html"], cwd=ROOT, check=True)
    (EXP / "results/example_manifest.json").write_text(json.dumps(images, indent=2))
    print(ROOT / "analysis/html" / report.with_suffix(".html").name)


if __name__ == "__main__":
    run()
