# VERDICT — cycle_0_owner_w20_midbox_cold/weights/best.pt

**状态：判死。仅作归档，禁止晋升，禁止用于任何实盘或 paper 路径。**

写于 2026-08-10。放在这里是因为同目录的 `results.csv` 只有 val 指标，数字好看，
而真正的裁决在别处——按铁律 12，**自家 val / mAP 永不作裁决**。

## 裁决依据：全市场 tip-replay 回测

协议：`tip_replay W=24 conf>=0.15 edge>=22`；entry t+1 open；TP5.0/SL2.0/H72；
cost=0.0006；MIN_GAP=18；matched control = symbol × month × atr_q；week sign-flip
置换 n=2000。311 个币种。

| | pre-holdout `2026-03-01..05-03` | holdout `2026-05-04..07-01` |
|---|---:|---:|
| 笔数 | 10,713 | 12,141 |
| 胜率 | 0.3255 | 0.2973 |
| PF | 1.006 | **0.836** |
| 净 bp/笔 | +0.59 | **−18.6** |
| matched lift | +4.88 ± 2.64 bp | +2.6 ± 2.58 bp |
| 置换 p | 0.5202 | **0.7806** |

两段的置换 p 都远大于项目门槛 p<0.01；matched lift 都在 1–2 个标准误内，
即**相对同币 × 同月 × 同波动桶的随机入场没有可测边缘**。holdout 段净亏 18.6 bp/笔。

## holdout 记账

- 该配置**第 1 次消耗 holdout**，owner 2026-08-07 对话中明确批准。
- 同配置再读 holdout 必须重新获批并记为第 2 次。

## 已知污染

`build_w20_midbox_dataset.py` 没有 holdout 过滤。实测（2026-08-10 复核）：
2635 个正样本里 **246 个（9.3%）窗口右端落在 ≥2026-05-04 的 holdout 期**，
其中 **209 个在 train、37 个在 val**。

对照 `datasets/local_signal_v2_stageb`：同源事件，最晚样本 2026-05-03 10:45 UTC，
落在 holdout 期的样本数 **0**——其 `skip_reasons.holdout_or_no_time` 正好也是 246。
同一批事件，一个漏进去了，一个挡住了。

因此上表 holdout 列同时受"无边缘"和"训练集污染"两个因素影响，
不能当作干净的样本外证据——**它只能证伪，不能证实**。

## 出处

- 报告：`analysis/p_w20_midbox_tip_backtest_20260807.md`（HTML 同名在 `analysis/html/`）
- 原始结果：`analysis/output/w20_tip_holdout.json` / `w20_tip_preholdout.json`
