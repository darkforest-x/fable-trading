# Short-only 链路待办

**日期**：2026-07-24  
**为何**：v1 short 训因「框非 tip + 非时间切分」被 Owner 叫停；作战计划见 `analysis/p_short_only_pipeline.md`。本文件只记待办；**不** promote / **不**动 holdout。

状态：`pending` = 未做 · `in_progress` = 进行中 · `done` = 已完成

---

## Owner 明确事项

- [ ] **pending** — 做空模型训练好之后：用该模型在**实际 K 线数据**上跑检测，框出约 **1000** 张；**勿与既有训练集重复**（排除已用于 `dense_owner_side_short*` / owner short 金标的样本）。  
  **命令草稿（未跑；需 Owner 点头或另起会话）**：

  ```bash
  # A) 判断层候选池（全宇宙扫，偏重；产出 CSV 非 1000 目视包）
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
    scripts/yolo_candidate_source.py --side short \
    --weights runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt \
    --out data/judgment_yolo_owner_side_short.csv --workers 4

  # B) Owner 目视 ~1000 框包（建议薄脚本；排除 tip 训练集 stem）
  # 伪命令 — 脚本尚未落地，勿当已存在：
  # OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  #   scripts/dump_short_tip_detect_sample.py \
  #   --weights runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt \
  #   --exclude-dataset datasets/dense_owner_side_short_tip \
  #   --also-exclude datasets/dense_owner_side_short \
  #   --mode tip --conf 0.3 --target 1000 \
  #   --out analysis/output/owner_side_short_tip_v1b_detect1000/
  #
  # 过渡：可先用 tip 预览收集器小样冒烟（非 1000、非严格 exclude）：
  # OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  #   scripts/collect_v13_tip_previews.py \
  #   --weights runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt \
  #   --log analysis/output/forward_log_vps_20260721.csv --limit 32 --conf 0.30 \
  #   --out analysis/output/owner_side_short_tip_v1b_tip_preview32
  ```

---

## 检测层（阶段 A）

- [x] **done** — tip 集完成：选项 1 tip **重裁窗 + 重写框** + **时间切分** → `datasets/dense_owner_side_short_tip/`（train 1037 / val 324；holdout 0；`box_right_frac` 中位 0.997）。勿在旧 `dense_owner_side_short/` 上续训。
- [x] **done** — Owner 口头批准开训；`owner_side_short_tip_v1b` 训完（≈57 ep early-stop；权重见下）。
- [x] **done** — tip-smoke 诚实评估（**不** promote）：tip **19/27**、live 对照 **4/27**（同口径快照；历史 v12–v15 为 0/27）。报告 `analysis/p_owner_side_short_tip_v1b.md`；日志 `analysis/output/owner_side_short_tip_v1b_tip_smoke.log`；JSON `analysis/output/diag_tip_smoke_owner_side_short_tip_v1b.json`。  
  权重：`runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt`。  
  **诚实**：val mAP≈0.99 **不作**晋升裁决。
- [ ] **pending** — 上节 Owner 1000 框（排除训练集）；**未**自动开跑。

---

## 判断层与回测（阶段 B / C）

- [x] **done** — **纠偏：short 回归对齐 v11**（Owner 2026-07-24）：停止 binary/feat_mirror 优化叙事；主线 = tip_v1b YOLO → `--objective regression` 预测空头 `realized_ret` → 分位筛单。  
  - 30×6m 扫完成：`data/judgment_yolo_owner_side_short_30_6m.csv` n=**7519**（镜像主路径；墙钟≈16min）。  
  - 训：`train --tag p2b_yolo_short_30_6m_reg --side short --objective regression`（**无** holdout）。  
  - 发现级：top-decile 净 **+0.371%**（n=150）/ Spearman **0.149** / val-q90=**0.00362**。  
  - 报告：`analysis/p_short_judgment_reg_align_v11.md`。CLI 已补 `--objective`。
- [x] **done** — **binary 扩币支线收口**（同 30×6m）：镜像基线 `p2b_yolo_short_30_6m_mirror` 净 **−0.181%** / p=0.125；top-K10 净 **−0.237%**。报告 `analysis/p_short_judgment_refactor_v2.md`。**关闭** binary 特征优化。
- [x] **done** — **5 币 × 6m 首表**（历史 binary 发现级）：`…_5_6m` AUC0.599 / 净+0.062% / n=24；报告 `analysis/p_short_only_backtest_tip_v1b_5_6m.md`。**不再作为主叙事**。
- [x] **done** — **方向特征镜像进主路径**（修债）：`align_short_feature_rows`；`train --side` 拒混边。feat_mirror 旁路实验**归档**，不当优化旋钮。
- [ ] **in_progress** — 同构回归下扩样本（逼近 v11 ~2.6 万候选哲学）或补 walkforward；**不** promote。
- [ ] **optional** — 全宇宙 YOLO short 扫；Owner 点头再开。
- [ ] **pending** — 换障碍（trend/MA/trail）**需 Owner 批**；禁止默认 binary。

---

## 待 Owner 确认（未决定，勿擅自执行）

- [ ] **pending** — tip-smoke 已发现级过线后是否申请检测器晋升门（默认建议：先 1000 目视 / 接判断层，**不** promote）。
- [ ] **pending** — 判断层主池：YOLO short 唯一主链 vs 规则 short 对照（作战计划默认建议：YOLO 主链）。
- [ ] **pending** — 阶段 C 障碍参数是否另批扫参，或先沿用现网默认（改 TP/SL/成本/阈值须另批）。
- [ ] **pending** — Long YOLO / 双链路空边以外事项：本待办不展开；与 §7-2 dump 并行、互不续命（HANDOFF）。

---

## 仍禁止（提醒，非待办）

promote · ACTIVE · 清 `forward_log` · holdout#8 · 真下单 · 改新鲜度三门 · 在未 tip 对齐的旧 short 集上续训
