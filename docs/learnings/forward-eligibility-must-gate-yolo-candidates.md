# 原始启动的前向资格必须在 YOLO 候选入口执行

- **问题**：冷启动重放会在九根确认窗内重新识别旧 4H V1 raw bar；旧代码仅按九根窗口注册 YOLO 候选，因此 pre-cutover 历史行可触发后续模型推理。
- **死胡同**：只在 Bark sender 复核 cutover 能防止发送，却仍允许历史候选加载模型并写入 YOLO 确认 journal，混淆 live 记录。
- **有效路径**：scanner 复用原始 Bark 的 cutover 与 freshness eligibility 作为候选注册门；模型处理已持久化候选时，按原始 bar close 重新核验静态 cutover，失败则标记 `disabled` 并保留审计。
- **通用规则**：附加确认的候选身份必须继承原始事件的前向资格。发送边界不是唯一防线；不得用当前时间重验原始的新鲜度，否则会错误取消合法的延迟确认窗。
- **牵连**：`yoyo/monitor/v1_worker.py`、`yoyo/monitor/model_gate.py`、`tests/monitor/test_v1_cutover_candidate.py`；当前 IBM 4H pre-cutover candidate 仅被标记 disabled，未删除事件或发送通知。
