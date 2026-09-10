# 对账先保留CSV浮点值，不先放宽风险等式容差

- **问题**：SPIKE V3独立审计校验`initial_risk == entry_price - initial_stop`，250条实际事件、889条随机控制在1e-12相对容差下失败，最大相对误差3.7034e-11。
- **死胡同**：直接放宽容差会把序列化输入问题当成策略或风险公式的可接受误差；小价格山寨的相减尤其容易放大解析误差。
- **有效路径**：对同一冻结CSV分别用默认解析器与`float_precision="round_trip"`读取。后者全部等式精确一致（最大相对差0）；原交易代码、源数据、配置和结果文件未改动。审计与报告改为保留写出时的浮点身份。
- **通用规则**：从文本产物重建浮点对账前，先确认往返解析语义，再判断公式或放宽容差。精度验证应使用相对尺度，不能拿对大币合适的绝对epsilon掩盖小币误差。
- **牵连**：`yoyo/evaluation/spike_burst_early_warning_audit.py`；`experiments/active/exp-spike-burst-early-warning-20260910-v3/results/trade_events.csv.gz`与`trade_controls.csv.gz`。这是同一冻结结果的I/O审计修复，不是重跑策略或调参。
