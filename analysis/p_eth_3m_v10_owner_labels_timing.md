# ETH 3m v10 owner 标注后的迟到诊断

日期：2026-07-29
范围：Label Studio 项目 53，200 张 ETH 3m v10 预标图
结论级别：开发期诊断；未训练、未调参、未消耗 holdout

## 结论

人工标注确认 v10 确实能找到一部分目标形态：93/200 张为“是”（46.5%）。但这 93 张不能直接当入场可用正例：框的横向中位跨度为 36 分钟，到开火时从框内最高收盘到信号收盘已经下跌中位 4.47 个 3m ATR；93/93 在信号端都位于六条均线下方。

用“信号前已走跌幅是否超过随后 3h 最大剩余跌幅”作为未来感知的敏感性代理，41/93（44.1%）落入迟到风险组。这 41 张未来 3h 最终收跌率只有 41.5%，其余 52 张为 86.5%。这个代理只证明需要独立时机标签，不能作为实盘特征或冻结阈值。

## 数据与完整性

| 检查 | 结果 |
|---|---:|
| Label Studio 任务 | 200 |
| 已完成且未跳过 | 200 |
| 每任务唯一 `is_target` | 200 |
| owner “是” | 93 |
| owner “不是” | 107 |
| 1h 分隔事件 | 104 |
| 框右缘与 manifest 一致 | 200/200 |
| 框右缘映射到最后一根（bar 199） | 200/200 |
| 未来最大下探复算最大绝对误差 | 9.85e-17 |
| 最晚 future_end | 2026-05-01 23:54 UTC |
| holdout（>=2026-05-04） | 未读取、未消耗 |

## 结果对照

| owner 标签 | n | 框跨度中位 | 信号前已走中位 | 未来 3h 最大剩余中位 | 未来 3h 收盘收益中位 |
|---|---:|---:|---:|---:|---:|
| 是 | 93 | 36 min | 4.47 ATR | 5.28 ATR | -0.481% |
| 不是 | 107 | 36 min | 4.17 ATR | 1.55 ATR | +0.480% |

owner 的形态判断有结果区分力，但不等于时机判断。框跨度在“是/不是”两组相同，且框跨度与已走 ATR 的 Spearman 仅 0.008，因此不能靠删最宽框修复。

| owner “是”的时序代理 | n | 已走中位 | 剩余中位 | 未来 3h 收跌率 | 未来反抽中位 |
|---|---:|---:|---:|---:|---:|
| 剩余不少于已走 | 52 | 4.38 ATR | 8.17 ATR | 86.5% | 0.264% |
| 已走多于剩余 | 41 | 4.79 ATR | 2.72 ATR | 41.5% | 0.623% |

## 解释

1. 所有框的右缘都落在因果窗口最后一根，排除了 HTML 坐标偏移。
2. owner 判“是”的样本第一次收盘跌到六条均线下方后，到 v10 开火又经过中位 33 分钟；v10 识别的是确认后的下破段，而非最早可行动点。
3. v10 conf 与 owner 判是的 Spearman 只有 0.174；调高 conf 可以过滤一部分误框，但不能产生入场时机。
4. 迟到但形态正确的样本必须保留为 detector 正例；时机需要独立监督。

## 推荐下一步

先只处理 93 张 owner “是”：自动提出一条更早的因果入场竖线，先生成 30 张校准图。界面仍只保留一个二元问题：“这个入场点来得及吗？是 / 不是”。口径确认后再扩到剩余 63 张。

最终训练结构：原生 ETH 3m detector 负责形态核心，独立因果 timing layer 负责当前是否可行动；人工可以看未来审核，训练图和特征必须截止到候选竖线。

## 风险与诚实声明

- 当前没有人工“来得及/来不及”标签，因此不能把 41 张代理样本宣称为人工确认的迟到框。
- 未运行 PnL、TP/SL 或成本验收；障碍和成本参数不在本轮授权范围。
- 200 张只有 104 个 1h 分隔事件，任务级比例不是 200 个独立事件的统计置信度。
- 所有未来值只用于结果诊断，不能进入模型输入。

## 复现命令

```bash
.venv/bin/python scripts/analyze_eth3m_v10_yes_no_labels.py
.venv/bin/python scripts/build_eth3m_v10_label_timing_report.py
sqlite3 -header -csv :memory: < analysis/output/eth3m_v10_label_timing/source.sql
node skills/build-report/scripts/deliver_portable_artifact.mjs \
  --input /Users/zhangzc/fable-trading/analysis/output/eth3m_v10_label_timing/artifact.json \
  --output /Users/zhangzc/fable-trading/analysis/output/eth3m_v10_label_timing/report.html
```

最后一条命令在 Data Analytics 插件根目录执行。
