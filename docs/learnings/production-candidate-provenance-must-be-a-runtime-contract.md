# 生产候选来源必须是运行时契约

- **问题**：生产前向脉冲在 YOLO 依赖不可用时自动切换到 rules，使未经盘口验证的候选源可以悄悄进入同一执行链。
- **死胡同**：只在 shell 里写“优先 YOLO”或记一条 fallback 日志不是安全边界；调用者仍能通过环境变量把 rules 送入生产扫描。
- **有效路径**：把 runtime mode 和 candidate source 组合变成 Python 层的显式校验；production 只接受 yolo，research 才可接受 rules。检测器缺失是 `detector=none` 且零发现，不是来源迁移。
- **通用规则**：凡是会改变产生交易候选集的 provenance，第一步应在业务入口校验“运行模式 × 来源”，而不是依赖启动脚本的默认值。
- **牵连**：`scripts/forward_pulse.sh`、`src/judgment/forward_types.py`、`src/judgment/forward.py`、`src/judgment/forward_scan.py`；实盘纪律 12；rules 离线研究能力仍需保留。
