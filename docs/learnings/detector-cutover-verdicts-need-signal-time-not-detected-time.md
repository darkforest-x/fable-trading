# 检测器切流后的"事后行"多是补账，不能用 detected_at 评判新检测器

- **问题**：v12 H-TIP 于 2026-07-20 14:01 UTC 切主线后，看板 forward 行仍全是大 lag（272–2000 min），
  owner 疑心 v12 实盘仍是事后识别。
- **死胡同**：直接按 `detected_at ≥ 切流点` 划分并统计 lag——切流后 5 行全部事后，看似"v12 也不行"。
  但这 5 行的 `signal_time` 全部早于切流点（最晚 07-20 09:30），是切流后前 3 个脉冲对旧信号的补账：
  live 模式 box 右缘可映射到 200-bar 窗口内任何 bar（`right_edge_to_bar`），旧启动带着右侧上下文
  重新被"发现"，天然是大 lag。这类行对"新检测器是否盘口开火"零证据。
- **有效路径**：评判检测器切流效果只看 `signal_time ≥ 切流点` 的行——切流后 3h 内一行都没有
  （无新信号开火，也无新信号被事后补认），即"尚无裁决"，不是"仍然事后"。本地 tip-only 冒烟
  （6 币 2.5s）复现出与 VPS 日志相同的旧信号命中（BONK 08:45 / RECALL 07-19 11:00），
  侧面证明 VPS 真在跑 v12 权重。
- **通用规则**：任何检测器/模型切流后的前向评估，第一步把行按 `signal_time`（不是 detected_at）
  相对切流点切开；signal_time 在切流前的行一律归为补账，单列不计入新配置的成绩。
- **牵连**：`src/judgment/forward_scan.py`（live 窗口调度）、`src/judgment/yolo_candidates.py`
  （right_edge_to_bar）、`src/webapp/forward_payloads.py`（FRESH_DETECT_MIN=30 裁决门）、
  `models/owner_best.json`（promoted_at 即切流点）。
