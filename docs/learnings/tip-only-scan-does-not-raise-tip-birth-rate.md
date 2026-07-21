# tip-only 调度救不了 tip 出生率——先采真实 tip 成败图

- **问题**：实盘 tip_fresh≈0；计划问「主线改 tip-only / 降 tip conf / 右缘偏置」能否救急。
- **死胡同**：
  1. 以为少扫窗就能新鲜——VPS tip-smoke 27 币 tip 与 live 均为 **0/27** 开火。
  2. 把 TIP_CONF 降到 0.22——同一 0/27 与账本 tip_fire **1/32**，阈值不是瓶颈。
  3. 用离线 tip_hit / accept PF 推断实盘 tip——与墙钟 log_lag（0/32≤30）脱钩。
- **有效路径**：诊断对照（live vs tip lag-walk + 强制 tip 扫描）证明调度正确、模型
  贴边框空；开关保留默认 live；分叉到 v13 **真实 tip 窗成败图**小样（带框预览）。
- **通用规则**：改扫描调度前先强制 tip 扫描看盘口框是否存在；零框则训练/标注优先，
  不改新鲜度门凑信号。
- **牵连**：`FABLE_YOLO_MODE` / `TIP_CONF` / `FABLE_YOLO_RIGHT_BIAS`；
  `analysis/p_tip_only_smoke.md`；`scripts/collect_v13_tip_previews.py`；
  `TIP_EDGE_BARS=2`；`docs/learnings/box-right-edge-maps-launch-bar-not-tip.md`。
