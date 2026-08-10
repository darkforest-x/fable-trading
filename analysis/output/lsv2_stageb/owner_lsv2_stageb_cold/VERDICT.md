# VERDICT — owner_lsv2_stageb_cold/weights/best.pt

**状态：未通过，但未判死。禁止晋升。holdout 未动用。**

写于 2026-08-10。三个 w20/lsv2 权重里数据卫生最好的一个——但"最好"不等于"通过"。

## tip-smoke 结果（pre-holdout 窗，holdout 未触碰）

范围 `2026-04-01..2026-04-25`，311 个币种。

| 指标 | 值 | 门槛 | 判定 |
|---|---:|---|---|
| 笔数 | 10,730 | — | — |
| 胜率 | 0.346 | — | — |
| PF | 1.116 | >1 | 过 |
| 净 bp/笔 | **+10.05** | >0 | 过 |
| matched lift | **+5.4 ± 2.8 bp** | 显著为正 | 约 1.9 SE，**不足** |
| 置换 p | **0.6142** | **p < 0.01** | **不过** |

净收益为正是好消息，但置换 p=0.61 意味着**这个排序和随机排序无法区分**；
matched lift 只有 1.9 个标准误，达不到显著。按项目成功标准（top-decile 扣成本后
净收益为正 **且** 置换 p<0.01），本权重**不通过**。

## 数据卫生（相对另外两个权重的优势）

`build_local_signal_v2_stageb.py` 做对了几件事，值得保留为范例：

- **holdout 过滤生效**：`skip_reasons.holdout_or_no_time = 246`，≥2026-05-04 的事件被排除
- **时间切分 + purge**：`purge_bars=150`，train 结束点距首个 val ≥150 bar，purge 区丢弃
- **manifest 完整**：构建时即记录 `event_id` / `config_hash` / `image_sha256` /
  `renderer_version` / `decision_bar` / `future_bars`
- **可复现已验证**：4776 张图现在的 sha256 与构建时记录**逐字节一致**
  （2026-08-10 复核，见 `datasets/local_signal_v2_stageb/manifest_audit.json`）

## 下一步（需要 owner 决策的已标注）

1. 扩大 tip-smoke 窗口（当前只有 25 天），看 p 值是否随样本量下降
2. 规范 §15 的 P1 对照矩阵尚未跑，无法判断"局部窗口"假设本身是否成立
3. **holdout 评估需 owner 逐次批准并记账**——本配置消耗次数目前为 0

## 出处

- `analysis/output/lsv2_stageb_tip_smoke.json`
- 数据集：`datasets/local_signal_v2_stageb`（summary + manifest.jsonl + manifest_audit.json）
