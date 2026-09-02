# P1：4h YOLO 半月语义门 Owner 终审否决（2026-09-02）

## 结论先行

Owner 看完第 7 次 4h holdout 交付的 34 张完整未来 K 线图后，给出批次裁决：**“都不太行”**。
因此 `exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1` 最终状态由技术门阶段的
`accepted as offline research filter` 改为 **`rejected`**。

这不是说程序没按预注册执行：1,764 个原结构框确实只剩 256 个，221 个事件确实只剩 34 个，
因果与哈希复算也全部通过。失败发生在更上层：**15m 自动生成器定义的数值语义，不等于 Owner
认可的 4h 形态语义**。技术谓词通过不能冒充视觉 Gold 通过。

本轮不把 Owner 的一句批次否决伪装成 34 份逐样本框边界标注，也不据此计算 4h precision；它只
足以否决“这 34 张可作为合格 4h 信号面”的主张。

完整冻结图仍保留为失败证据：
[34 张全局未来 K 线](../experiments/active/exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1/results/semantic_gate/all_global_future_charts.html)。

## 已有产物中的失败特征

以下只汇总第 7 次 holdout 已经落盘的特征，不新增谓词、不重算新配置、不调阈值：

| 已落盘诊断 | 34 个事件代表框 | 为什么会视觉不合格 |
|---|---:|---|
| 核心 K 线总跨度 > 2 ATR | **13 / 34** | `candle_envelope_atr` 被计算但从未作为门 |
| 核心 close 离 MA 外包络 > 1 ATR | **10 / 34** | 冻结上限宽到 1.9 ATR |
| 至少一次贴线 ≤0.25 ATR，但另有 close 离包络 >1 ATR | **6 / 34** | “有一根碰线”被误当成“整段贴线” |
| 末端六均线间距 >0.8 ATR | **12 / 34** | 冻结上限仍允许到 1.1 ATR |
| 核心六均线总包络 >1 ATR | **8 / 34** | 冻结上限仍允许到 1.5 ATR |
| 原预测框高度 >整图 30% | **13 / 34** | `prediction_cy_norm / h_norm` 不参与放行 |
| MA 斜率离散度 >0.20 ATR | **3 / 34** | `ma_slope_std_atr` 被计算但不参与放行 |

这些数字解释了为何第一层过滤能剔除 USELESS、LA 等明显错误，却仍留下整批 Owner 不认可的图。
它们不是给下一版选阈值用的调参表；第 7 次 holdout 已消费，不能在看完结果后把 1.9 改成 1.0、
把 1.5 改成 0.8，再用同一批图宣布成功。

## 根因

1. **训练目标不是 4h Gold。** 权重学习的是 15m 自动生成图；W18/W19 在 15m 约覆盖 4.5～4.75
   小时，在 4h 覆盖 72～76 小时，MA20/60/120 的实际时间尺度也整体改变。
2. **自动生成器语义不是 Owner 语义。** 现有正框纵向包住核心 K 线完整影线和六均线，而不是只定位
   最密的均线结；此前 10,000 正例中 95.78% 的 K 线跨度大于 MA 跨度。
3. **语义门只复现弱协议。** parent 15m 验证证明的是“减少同生成体系负例开火并保留同生成体系
   正例”，不是对真实 4h 图的 Owner precision。
4. **门的逻辑不等于‘整段贴线且六线拧成绳’。** 当前只要求全核心里至少存在一次 close-to-MA
   最小距离合格；不要求每根 K 都贴线，也不检查 K 总跨度、MA 斜率一致性、Owner-50 距离或预测框
   纵向覆盖。

所以不能继续把 YOLO 当 4h 主检测器，再靠增加几个后置布尔条件补洞。模型并非在原 15m 任务上
“白训练”，但这份权重对 4h 目标没有通过资格证明。

## 数据、对照与不适用项

| 项目 | 结果 |
|---|---:|
| 4h holdout 消费 | checkpoint **#7**，不重用调门 |
| 原结构框 / 事件 | 1,764 / 221 |
| 技术语义门通过框 / 事件 | 256 / 34 |
| Owner 批次视觉裁决 | **REJECT**（“都不太行”） |
| 新模型推理 / 网络读取 | 0 / 0 |
| 新阈值、网格或训练 | 0 |
| promote / ACTIVE / forward / deploy / order | 0 |

这是形态语义终审，不存在入场、出场、TP/SL 或收益序列，因此 val AUC、top-decile 毛/净收益、
胜率、收益置换、单特征收益基线和同币 × 同时间块 × 同波动桶随机入场对照均不适用。

严格对照是同一批 34 张图的两种裁决面：冻结数值协议为 34/34 技术通过，而 Owner 对完整全局图的
批次裁决为不接受该信号面。由于没有逐图结构化理由，本报告不把它写成可外推的 0/34 precision，
也不对 Owner 反馈做显著性检验。

## 最终裁决与下一条允许动作

- 冻结本轮为 **失败基线**，保留图片、框、特征、报告和 Owner 回执；不删除、不覆盖。
- 停止用这份 15m 权重继续扫 4h，也停止在第 7 次 holdout 上补阈值。
- 当前 ROADMAP 只允许 P0/P1。若继续 4h，下一条正确路径是在 **pre-holdout 4h** 上建立小规模
  Owner 目标协议：先确认横向核心、是否只包 MA、K/MA 允许距离、多个密集结取舍与重复标注稳定性；
  之后才能形成可追溯 4h Gold。P0/P1 门通过前不训练新的 4h YOLO。
- 若不愿建立 4h Gold，就应诚实放弃 YOLO 的 4h 路径，而不是继续用 15m 权重筛图。

## 风险与诚实声明

- “都不太行”是清晰的产品/视觉面否决，但不是逐图可学习标签；不能直接拿 34 张全标负例重训。
- 本报告是在 holdout 结果揭晓后的失败归因，只能指导研究设计，不能作为下一配置的独立验证。
- 任何新 4h 标签协议、阈值或模型都必须只在 pre-holdout 开发，并用新的未见数据验收。
- 本轮没有训练、改权重、改标签、promote、部署、写 forward、发 Telegram 或下单。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

# 只汇总既有 signals.csv；不读取 candle、不产生新评分
PYTHONPATH=. .venv/bin/python - <<'PY'
from pathlib import Path
import pandas as pd

p = Path("experiments/active/exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1/results/semantic_gate/signals.csv")
d = pd.read_csv(p)
assert len(d) == 34
print("candle_envelope_gt_2", int(d.semantic_candle_envelope_atr.gt(2.0).sum()))
print("close_outside_gt_1", int(d.semantic_max_close_to_ma_envelope_atr.gt(1.0).sum()))
print("one_touch_but_far", int((d.semantic_minimum_close_to_ma_atr.le(.25) & d.semantic_max_close_to_ma_envelope_atr.gt(1.0)).sum()))
print("end_ma_spread_gt_point8", int(d.semantic_ma_spread_end_atr.gt(.8).sum()))
print("ma_envelope_gt_1", int(d.semantic_ma_envelope_atr.gt(1.0).sum()))
print("box_height_gt_30pct", int(d.prediction_h_norm.gt(.3).sum()))
print("ma_slope_std_gt_point2", int(d.semantic_ma_slope_std_atr.gt(.2).sum()))
PY

python3 scripts/md_to_html.py \
  analysis/p1_4h_ma_launch_yolo_halfmonth_owner_rejection_20260902.md \
  --out-dir analysis/html
```
