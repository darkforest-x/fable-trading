# VERDICT — owner_lsv2_stageb_cold/weights/best.pt

**状态：已失效（invalidated），禁止作为 Local Signal V2 候选。holdout 未动用。**

写于 2026-08-10；同日补充根因复核。该权重绑定的 `local_signal_v2_stageb` 有负样本跨时间块，且训练时 `hsv_s/v=0.05`，因此其 P1 结果不能转移到修复后的 strict-negative V2。

## 失效原因（2026-08-10 补充）

- 317 条 train negatives 晚于正样本 train 截止；296 条 val negatives 早于正样本 val 起点。
- 当时的 P0 auditor 只把 positives 传给 split audit，导致错误全绿。
- 历史训练配置使用 `hsv_s=0.05 / hsv_v=0.05`，违反铁律 5。
- 修复版数据集是 `datasets/local_signal_v2_stageb_strictneg_v2`；本权重没有在该数据集训练，不能继承其 P0 PASS。

以下历史 tip-smoke 数字保留用于追溯，但不再承担候选裁决。

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

## 当时记录的数据卫生声明（已被新审计推翻）

下面只有因果 positive 与 holdout 过滤仍成立；“时间切分 + purge”只对 positives 成立，不能代表完整训练集：

- **holdout 过滤生效**：`skip_reasons.holdout_or_no_time = 246`，≥2026-05-04 的事件被排除
- **时间切分 + purge（仅 positives）**：负样本未受同一时间边界约束，完整数据集 P0 FAIL
- **manifest 完整**：构建时即记录 `event_id` / `config_hash` / `image_sha256` /
  `renderer_version` / `decision_bar` / `future_bars`
- **可复现已验证**：4776 张图现在的 sha256 与构建时记录**逐字节一致**
  （2026-08-10 复核，见 `datasets/local_signal_v2_stageb/manifest_audit.json`）

## 下一步（需要 owner 决策的已标注）

1. 不再扩大本权重的 tip-smoke；先由 owner 决定是否以 strict-negative V2 重启 P1。
2. 规范 §15 的 P1 对照矩阵尚未跑，无法判断“局部窗口”假设本身是否成立。
3. **holdout 评估需 owner 逐次批准并记账**——本配置消耗次数目前为 0。

## 出处

- `analysis/output/lsv2_stageb_tip_smoke.json`
- 数据集：`datasets/local_signal_v2_stageb`（summary + manifest.jsonl + manifest_audit.json）
