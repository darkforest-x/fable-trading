# Short-only 链路待办

**日期**：2026-07-24  
**为何**：v1 short 训因「框非 tip + 非时间切分」被 Owner 叫停；作战计划见 `analysis/p_short_only_pipeline.md`。本文件只记待办；**不** promote / **不**动 holdout。

状态：`pending` = 未做 · `in_progress` = 进行中 · `done` = 已完成

---

## Owner 明确事项

- [ ] **pending** — 做空模型训练好之后：用该模型在**实际 K 线数据**上跑检测，框出约 **1000** 张；**勿与既有训练集重复**（排除已用于 `dense_owner_side_short*` / owner short 金标的样本）。

---

## 检测层（阶段 A）

- [x] **done** — tip 集完成：选项 1 tip **重裁窗 + 重写框** + **时间切分** → `datasets/dense_owner_side_short_tip/`（train 1037 / val 324；holdout 0；`box_right_frac` 中位 0.997）。勿在旧 `dense_owner_side_short/` 上续训。
- [x] **done** — Owner 口头批准开训（「可以开始训练吧」）；训练已按批准重启（非再等看图）。样本仍可看 `analysis/output/owner_side_short_tip_sample30/`。
- [ ] **in_progress** — 本机 MPS 训 `owner_side_short_tip_v1b`（Owner 已批准并已重启；launchd `com.fable.owner_side_short_tip_v1b`；`--data datasets/dense_owner_side_short_tip/data.yaml`；日志 `analysis/output/owner_side_short_tip_v1b_train.log`；pid `analysis/output/owner_side_short_tip_v1b_train.pid`；坏集权重不晋升；**勿** pkill tip 训练）。
- [ ] **pending** — 训练落盘后做 tip-smoke / 真 tip 金标**诚实评估**（研究口径；**不** promote / **不**改 ACTIVE）。

---

## 判断层与回测（阶段 B / C）

- [ ] **in_progress** — **同步重构做空（short-only）判断层逻辑**（与检测 tip 重建/开训并行可准备；2026-07-24 更新）。  
  - 可先做（不依赖权重）：`--side short` 规则池（`build_dataset --side short`）、代码骨架/标签口径对齐、报告与主表分边。  
  - 必须等检测权重：`yolo_candidate_source --side short` 扫 YOLO short 池 → 判断层训练（标签带 `short`；**不加** `--eval-holdout`）。  
  - 现状：`--side short` **骨架已有**，待 tip 权重后接主链；勿在权重未就绪时开训判断层主链。
- [ ] **pending** — short-only 发现级回测/优化（主表禁止混边；净收益+置换为裁决，AUC 仅参考）。

---

## 待 Owner 确认（未决定，勿擅自执行）

- [ ] **pending** — tip-smoke 通过后是否申请检测器晋升门（默认建议：先诚实报，**不** promote）。
- [ ] **pending** — 判断层主池：YOLO short 唯一主链 vs 规则 short 对照（作战计划默认建议：YOLO 主链）。
- [ ] **pending** — 阶段 C 障碍参数是否另批扫参，或先沿用现网默认（改 TP/SL/成本/阈值须另批）。
- [ ] **pending** — Long YOLO / 双链路空边以外事项：本待办不展开；与 §7-2 dump 并行、互不续命（HANDOFF）。

---

## 仍禁止（提醒，非待办）

promote · ACTIVE · 清 `forward_log` · holdout#8 · 真下单 · 改新鲜度三门 · 在未 tip 对齐的旧 short 集上续训
