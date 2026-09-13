# 空头展示回放不能与冻结多头状态机共用状态

- **问题**：信号中心需要展示当前 SPIKE V1 Pine 的空头设置，但监控中的冻结 replay 只实现多头。直接把空头分支加入该 replay 会让同一 quiet/release/持仓状态被反向事件消费，历史多头事件和 R 路径不再可比。
- **死胡同**：把 Pine 中的 `burstDown` 当作现有 `burstUp` 的 UI 镜像，或在冻结多头状态机中打开双向分支，都没有记录空头的 recentHigh 止损、上方保护触发和 Pine 的方向输入，且会改变多头生命周期。
- **有效路径**：以当前 Pine 源文件及 `方向 = 空头` 的状态顺序建立独立 short-only replay；它只读闭合 OHLCV，空头以五根高点计算初始止损并用上方保护计算 R。短事件用独立 protocol 和 Pine 设置 provenance 持久化，缓存历史按独立 display cutover 补齐，通知和 YOLO 候选入口明确拒绝。
- **通用规则**：当新增方向会共享已有状态机的 quiet、pending 或持仓状态时，先判断原方向的历史序列是否是契约；若是，新增方向必须独立 replay，并在事件中写明源哈希和非默认 Pine 设置。
- **牵连**：`yoyo/evaluation/pine/spike_burst_v1.pine`、`yoyo/evaluation/spike_burst_replay.py`、`yoyo/monitor/v1_short_replay.py`、`yoyo/monitor/signals.py`、`yoyo/monitor/v1_worker.py`、`yoyo/monitor/notification_policy.py`。
