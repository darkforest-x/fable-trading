# YOLO 追加通知的新鲜度属于确认收盘时刻

- **问题**：V1 原始启动通过 cutover 后，1H/4H 的 YOLO 可能在数根确认 bar 后才成立；若追加 Bark 仍以原始 bar 的 30 分钟新鲜度判定，会丢掉刚刚收盘的补充确认。
- **死胡同**：把当前时刻代入 raw eligibility 虽然能挡冷启动历史行，却也会把合法的延迟候选误判过期；只看原始 bar 又使后续确认无法单独通知。
- **有效路径**：候选资格固定在原始 bar 的 cutover 和新鲜度时刻判断，持久候选恢复时也在该原始时刻复核。YOLO 入库和追加 Bark 则用确认事件自己的收盘时刻判断新鲜度，同时要求原始与确认都晚于所属 cutover。
- **通用规则**：多阶段事件必须分别保存并使用各自的因果时钟；第一阶段的准入时钟不能替代第二阶段的新信息时钟。
- **牵连**：`yoyo/monitor/v1_worker.py`、`yoyo/monitor/model_gate.py`、`yoyo/monitor/notification_policy.py`，以及 `tests/monitor/test_spike_v1_yolo_extra.py` 的合法延迟/过期确认边界。
