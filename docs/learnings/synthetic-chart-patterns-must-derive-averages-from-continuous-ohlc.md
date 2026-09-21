# 合成形态图的均线必须从连续 OHLC 派生

- **问题**：为了让 Owner 确认“均线收拢后启动”的图形样式，需要用程序合成 K 线与 SMA/EMA，而不能将视觉上好看的线条直接画到无关蜡烛上。
- **死胡同**：把启动后的路径从独立基准重新开始，虽然终点涨幅合理，却会在 T0 形成不属于形态的 close 跳变；预画均线也会让图像无法证明线条来自同一价格序列。
- **有效路径**：先生成一条包含充分 warmup、收拢段和释放段的连续 OHLC；令后段从前一根实际合成 close 接续，再从这条 `close` 重算 SMA/EMA20/60/120。早期图截到 T+3，完整图与 v1 同时保留，避免用后文掩盖启动时刻。
- **通用规则**：任何合成图若要讨论指标外观，先审查时间序列在分段接点的连续性，再由唯一的原始序列计算所有派生线；seed、参数和像素/OHLC 回执必须可追溯。
- **牵连**：`yoyo/datasets/ma_launch_synthetic_series.py`、`yoyo/datasets/ma_launch_synthetic_preview20.py`、`experiments/active/exp-ma-launch-synthetic-preview20-20260921-v1/plan_v2.json` 与 `preview_v2/`。该规则只保护样式谱系，不构成真实行情、标签、收益或泛化证据。
