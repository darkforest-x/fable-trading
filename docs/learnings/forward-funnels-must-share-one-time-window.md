# 前向漏斗的每一级必须使用同一时间窗

- **问题**：前向摘要显示两万多个候选却只有几个信号，表面上像 LightGBM 几乎全部
  拦截，实际候选分母包含全部历史，信号分子只包含正式前向窗口。
- **死胡同**：直接用现有 `candidates_seen / threshold_signals_seen` 推断过滤率；两个计数
  的时间边界不同，算出的通过率没有统计意义。
- **有效路径**：先在候选层应用 `FORWARD_START`，把同一批 post-start 特征分数同时送入
  q90 和 q80，再比较候选数、有限分数数和两档通过数。
- **通用规则**：任何扫描漏斗都先固定数据宇宙、时间窗和去重口径；只有三者相同的相邻
  阶段才能相除。另报数据最新时间，避免把数据停更误判成策略无信号。
- **牵连**：`src/judgment/forward_scan.py`、`src/judgment/forward_threshold_shadow.py`、
  `data/forward_log_ma206_q80_shadow.csv`；q80 只作 owner 批准的诊断，不改变 ACTIVE。
