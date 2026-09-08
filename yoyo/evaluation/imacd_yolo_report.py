"""Present the frozen confirmation pilot without rerunning inference or scoring.

Reads saved decisions/proposals and the identical bounded OHLCV prefix only to
reproduce four earliest confirmed model inputs. Pixel SHA must match the saved
inference receipt. Audit annotations go on separate copies; rendered context
ends at the model's actually consumed endpoint. No later prices are shown.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from .imacd_yolo_confirmation import ROOT, EXP, DATA, END, digest, prepare_window
from .imacd_formation_research import read_prefix
from .imacd_startup_quality import build_features, MA_COLUMNS
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features


def time_bjt(value):
    return pd.Timestamp(value).tz_convert("Asia/Shanghai").strftime("%m-%d %H:%M")


def money(value):
    return f"{float(value):.8g}"


def example_images(accepted, summary):
    """Four earliest confirmations, selected without returns or future prices."""
    selected = accepted.sort_values(["signal_available_at", "event_id"]).head(4)
    images_dir = EXP/"results/examples"
    images_dir.mkdir(exist_ok=True)
    cache, records = {}, []
    for row in selected.itertuples(index=False):
        if row.symbol not in cache:
            raw = read_prefix(ROOT/summary["inputs"][row.symbol]["path"], end=END)
            sha = hashlib.sha256(raw.to_csv().encode()).hexdigest()
            if sha != summary["inputs"][row.symbol]["bounded_ohlcv_sha256"]:
                raise ValueError("source data differs from inference")
            enriched = add_candidate_features(raw.reset_index(names="open_time"))
            proposals = pd.read_csv(DATA/f"{row.symbol}_proposals.csv.gz").set_index("detection_id")
            cache[row.symbol] = raw, enriched, proposals, build_features(raw)
        raw, enriched, proposals, features = cache[row.symbol]
        proposal = proposals.loc[row.model_detection_id]
        p, e = int(row.signal_i), int(row.confirmation_i)
        image, transform, _ = prepare_window(enriched, e, int(proposal.window_len))
        pixel_sha = hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()
        if pixel_sha != proposal.input_pixel_sha256:
            raise ValueError("model input pixel mismatch")
        input_path = images_dir/f"{row.event_id}_input.png"
        audit_path = images_dir/f"{row.event_id}_audit.png"
        context_path = images_dir/f"{row.event_id}_context.png"
        cv2.imwrite(str(input_path), image)
        audit = image.copy()
        cx, cy = float(proposal.prediction_cx_norm), float(proposal.prediction_cy_norm)
        w, h = float(proposal.prediction_w_norm), float(proposal.prediction_h_norm)
        x0, x1 = int((cx-w/2)*1280), int((cx+w/2)*1280)
        y0, y1 = int((cy-h/2)*742), int((cy+h/2)*742)
        cv2.rectangle(audit, (x0, y0), (x1, y1), (130, 155, 0), 3)
        local_p = p-int(proposal.window_start_i)
        if 0 <= local_p < transform.n_bars:
            x = transform.x_at(local_p)
            cv2.line(audit, (x, 15), (x, 727), (50, 115, 230), 2)
        cv2.putText(audit, f"AUDIT COPY | {row.symbol} | IMACD orange line | YOLO core teal box",
                    (20, 30), cv2.FONT_HERSHEY_SIMPLEX, .6, (60, 60, 60), 1, cv2.LINE_AA)
        cv2.imwrite(str(audit_path), audit)

        start = max(0, min(int(row.setup_start_i)-20, p-100))
        b = raw.iloc[start:e+1]
        f = features.iloc[start:e+1]
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
            ax.plot(xs, f[name], color=col, linewidth=.8, alpha=.9)
        ax.axvspan(int(row.setup_start_i)-start-.5, p-start-.5, color="#d9a744", alpha=.10)
        ax.axvline(p-start, color="#db9846", linewidth=1.4, label="IMACD arrow")
        ax.axvline(e-start, color="#008a80", linewidth=1.2, linestyle="--", label="YOLO available")
        ax.axvspan(int(row.core_start_i)-start-.5, int(row.core_end_i)-start+.5,
                   color="#00a494", alpha=.14)
        side = "LONG" if row.side == 1 else "SHORT"
        ax.set_title(f"{row.symbol} / 15m / {side}    |    delay {int(row.delay_bars)*15} min"
                     f"    |    all visible data ends at confirmation", loc="left", fontsize=11)
        ax.legend(loc="upper left", frameon=False, fontsize=8)
        osc.plot(xs, f.md, color="#527ed0", linewidth=1.2, label="IMACD")
        osc.plot(xs, f.sb, color="#d39a3d", linewidth=1.1, label="Signal")
        osc.axhline(0, color="#8a939d", linewidth=.7)
        osc.axvspan(int(row.setup_start_i)-start-.5, p-start-.5, color="#d9a744", alpha=.10)
        ticks = np.linspace(0, len(b)-1, 6).astype(int)
        osc.set_xticks(ticks, [t.strftime("%m-%d\n%H:%M") for t in b.index[ticks]])
        osc.set_xlabel("UTC candle OPEN; last candle must close before confirmation is available")
        fig.savefig(context_path, dpi=150)
        plt.close(fig)
        records.append(dict(event_id=row.event_id, input_path=str(input_path.relative_to(ROOT)),
            audit_path=str(audit_path.relative_to(ROOT)), context_path=str(context_path.relative_to(ROOT)),
            pixel_sha256_verified=pixel_sha, model_available_at=row.confirmation_available_at))
    return records


def run():
    summary = json.loads((EXP/"results/summary.json").read_text())
    for relative, expected in summary["files"].items():
        if digest(ROOT/relative) != expected:
            raise ValueError("frozen inference ledger changed")
    d = pd.read_csv(DATA/"decisions.csv")
    kept = d.loc[d.status.eq("confirmed")].copy()
    images = example_images(kept, summary)
    rows = ["| 品种 / 15m | 原箭头 | YOLO确认 | 等待中失效 | 到期无确认 | 数据截断 | 保留率 |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for s in summary["table"]:
        rows.append(f"| {s['symbol']} | {s['arrows']} | {s['confirmed']} | {s['invalidated']} | "
                    f"{s['expired']} | {s['censored']} | {s['pass_rate_pct']:.2f}% |")
    details = ["| 品种/方向 | 原箭头确认（北京时间） | 模型确认 | 等待 | 原次开盘 | 确认次开盘 | 沿方向追价 | conf |",
               "|---|---|---|---:|---:|---:|---:|---:|"]
    for r in kept.sort_values(["signal_available_at", "event_id"]).itertuples():
        details.append(f"| {r.symbol} / {'多' if r.side == 1 else '空'} | {time_bjt(r.signal_available_at)} | "
            f"{time_bjt(r.confirmation_available_at)} | {int(r.delay_bars)*15}分 | {money(r.baseline_next_open)} | "
            f"{money(r.confirmed_next_open)} | {r.displacement_bp/100:+.4f}% | {r.model_confidence:.3f} |")
    zero = int(kept.delay_bars.eq(0).sum())
    null = summary["direction_null"]
    md = f"""# IMACD → YOLO 延迟确认试验：接线可行，盈利与去噪仍待验证

本轮已实际加载固定 Grade-A 1280 模型，在 BTC、ETH 的 OKX 15m 上完成推理。2026-05-04 至 2026-07-01 UTC（右开）共 {len(d)} 个可见 IMACD 启动箭头，其中 {len(kept)} 个得到模型确认，保留 {100*len(kept)/len(d):.2f}%。**这表示减少了通知候选数，不能等同于减少了错误信号。**

{zero} 个在原箭头收盘就能确认；其余 {len(kept)-zero} 个实际等待15、30或60分钟。模型不必永远晚于箭头：它识别的核心常在蓄势区内更早形成。最大沿方向追价为 {kept.displacement_bp.max()/100:.4f}%，有一个空头等待后获得更高的开盘价。模型置信度不是行情成功概率。

## 方案与模型身份

流程为“IMACD候选 → 等待模型同向确认 → 确认/失效/过期”。最多等9根15m（135分钟），包含原箭头当根；期间md回零、反向或未知即作废。取第一次确认，价格使用那次确认后的下一根开盘。核心必须为4/5根、核心之后2–9根，同原蓄势区+箭头有真实K线交集；不要求精确框住箭头。这个9根预算是固定试验选择，未被证明最优。

使用当前研究基线 Grade-A close源 full40 native1280 YOLO11s，权重SHA `{summary['model_sha256']}`。原生15m、W18/W19、conf0.25、NMS0.70、imgsz1280、MPS float32，未训练、未叠旧语义门。旧`owner_best`别名仍指向更早模型；HL2更新版没有通过其配对联合门。因此这里的“最好”只能落实为当前有明确血缘、参数与早期命中证据的研究基线，不能宣称全版本经济最优。

输入由训练同源渲染器生成白底K线和六根close均线，未把TV界面截图输入模型。NumPy图像使用BGR，预测参数含conf/iou/imgsz，遵循[Ultralytics官方推理接口](https://docs.ultralytics.com/modes/predict/)。现装8.4.89对`half=False`打印弃用提醒；调用成功，无静默设备回退。实际包版本在JSON收据中。

## 主表：同一批箭头前后对照

{chr(10).join(rows)}

原始基线放行全部箭头；确认组只放行表中YOLO确认数。失效和过期是状态机终态，不是人工判断的错误行情。数据末端完整随访要求属于离线研究队列规则；在线实现应保留已知即时确认，不能照搬“缺未来9根就整体截断”的队列筛选。本次截断为0，对本次结果没有影响。

## 全部确认明细

{chr(10).join(details)}

追价定义 `side × (确认后次开盘 / 原箭头后次开盘 - 1)`，正值表示沿信号方向更贵。两个价格均来自可执行的下一开盘时钟；不把它称真实成交滑点，也没有按原价格回填新信号。这里没有固定3R退出或任何新止损合同。

## 数据与统计口径

候选数{len(d)}，模型确认率{100*len(kept)/len(d):.2f}%，不是有标签正类率；没有新金标或监督训练，val样本数不适用。两个源文件共解析{sum(v['parsed_rows'] for v in summary['inputs'].values())}根15m，最早2022-01-03，用于复现递归指标播种；评分候选仅在上述固定时段。完整检测窗口{sum(v['windows_scored'] for v in summary['inputs'].values())}张，原始框{sum(v['raw_boxes'] for v in summary['inputs'].values())}个，结构合格框{sum(v['structural_boxes'] for v in summary['inputs'].values())}个。滑窗框不是独立事件。

原始模型训练可见窗最晚2025-11-29，验证最晚2026-05-03。本轮继承Owner“任何时间段数据都可以使用不要有任何限制”的明确授权，**这是本融合配置第1次消耗holdout**。其他模型/研究已有历史接触，不能称全新盲验；本轮之后未搜索权重、等待上限、阈值或品种。

本轮是工程可行性评价，不是收益回测。未设置退出/仓位合同，因此AUC、胜率、净收益、最大回撤、top-decile毛净收益以及经济匹配随机入场均不适用，不能编造。没有人工真假金标，也不能报形态precision/recall。独立指标基线为全部IMACD箭头，实验只追加一个冻结的模型确认规则。

对应工程零假设：固定候选的原始有效窗口与模型几何池，在同币同月内打乱方向{null['permutations']}次。真实同向确认{null['observed']}，随机均值{null['null_mean']:.4f}，单侧p={null['p_one_sided']:.6f}。这只支持两个条件化信号的方向关联，**不是盈利检验，也不是去噪成功检验**。两种方法本就使用相同价格和均线，有机械相关的可能；没有用翻转后立刻被md取消的伪对照。

## 逐图查看：按时间选最早四个确认，不按涨跌挑例子

图中只显示截至模型实际检测端点的数据。橙色线为原箭头、青色虚线为模型确认，浅橙底为已冻结蓄势区。第二张是独立审核副本，矩形为原始YOLO框；真正模型输入另存无注释PNG，其像素SHA逐张核对一致。
"""
    for item in images:
        md += f"\n### {item['event_id']}\n\n![截至确认的上下文](../{item['context_path']})\n\n![模型输入审核副本](../{item['audit_path']})\n"
    md += f"""
## 验证与复现

源冻结commit `{summary['source_commit']}`；台账验证全部{len(kept)}个确认的身份、方向、core/post、真实交集、等待、时钟与价格位移。20个合成测试（含未来扰动后像素一致和模拟批量模型接口）及68个层间守门测试通过。独立复核结果见实验目录results/independent_review.json。台账未保存逐根md或首个失效位置，因此9条失效时钟不能只凭台账完整独立重放；这部分由冻结代码与合成测试支持，没有冒充独立复证。

从源码与相同本地权重、缓存复现首次运行：

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/test_imacd_yolo_confirmation.py tests/boundaries/test_layer_imports.py
.venv/bin/python -m yoyo.evaluation.imacd_yolo_confirmation
.venv/bin/python -m yoyo.evaluation.imacd_yolo_verify
.venv/bin/python -m yoyo.evaluation.imacd_yolo_report
```

推理命令只供没有run_started收据的首次环境；当前工作区会拒绝覆盖该次评价。复核现有结果只运行报告命令，它校验已保存SHA并复现渲染，不再次调用模型。无需安装依赖；权重、原始缓存不入git，路径与摘要记录在source_manifest/summary。渲染报告源码先提交再生成图片。

## 风险与诚实声明

- 这次只检验BTC、ETH原生15m，共12个确认；不代表OKX全币种或1H/4H效果，更不能据此宣布能赚大钱。
- 确认框和蓄势区存在时间交集，不代表已经人工确认同一个形态；已保存交集比例、核心相对箭头位置。重复使用同一精确核心的确认数为{summary['reused_core_confirmations']}，邻近但不完全相同的框仍可能相关。
- 没有评估被删掉的37个箭头中有多少大趋势；保留率低可以是强筛选，也可以是漏报。
- 无经济退出合同，未测资金费、真实滑点、止损存活或趋势利润留存；本轮无法回答收益与最大回撤。
- 离线计算理论可用时间，不包含Mac扫描、推理、网络和通知的实际延迟。实际推理总耗时约{sum(v['elapsed_seconds'] for v in summary['inputs'].values()):.1f}秒（980张条件化输入），不能线性冒充全市场服务容量测试。
- 没有更改现有TradingView、spike、Mac监控、TG/Bark信号口径、ACTIVE或执行配置。training_eligible=false，production_eligible=false。

## 下一步

技术上已经可以实现候选与双确认两种状态；真实通知应发在双确认的当前时间，并同时保留原箭头时间和确认价格。接入前应按真实在线输入长度/递归播种与延迟预算做影子验证，尤其不要把离线完整随访筛选直接塞进实时循环。

经济上需要对同一批已保存候选预先固定入场、退出和成本口径，比较确认组与原指标，检查删掉的赢家和尾部趋势收益。当前不根据这12个确认调参数；需要新评价合同才能回答是否更赚钱。1H/4H跨周期使用也要单独注册检验，不能自动推广。
"""
    report = ROOT/"analysis/p1_imacd_yolo_confirmation_20260908.md"
    report.write_text(md)
    subprocess.run([str(ROOT/".venv/bin/python"), "scripts/md_to_html.py", str(report),
                    "--out-dir", "analysis/html"], cwd=ROOT, check=True)
    (EXP/"results/example_manifest.json").write_text(json.dumps(images, indent=2))
    print(ROOT/"analysis/html"/report.with_suffix(".html").name)


if __name__ == "__main__":
    run()
