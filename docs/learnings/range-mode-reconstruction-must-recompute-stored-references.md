# Range-mode history must recompute references from candle inputs

- **问题**：A history endpoint can expose the same confirmed signal under different range policies, but stored rows hold only the default plan. Reusing that row makes alternate-mode IDs, entries, and targets disagree.
- **死胡同**：Treating a stored fib plan as the alternate plan's `original_reference` implied a fib wrapper and obscured which boundary actually produced the reconstruction.
- **有效路径**：Reuse stored EMA checkpoints as recurrence anchors, then apply the selected wick/reclaim boundaries to confirmed OHLC and project entry, target, stop, policy, and ID for that mode. Keep mode-specific cache keys and leave scanner persistence on its default policy.
- **通用规则**：When a read path supports multiple reference policies over one signal stream, cache and identity must include the policy, and each projection must be recomputed from shared causal inputs rather than another policy's projected result.
- **牵连**：`yoyo/monitor/cloud_signal_history.py`, `yoyo/monitor/cloud_signal_plans.py`, `yoyo/monitor/cloud_signals.py`; `/api/cloud-signals/history?range_mode=fib|monday`.
