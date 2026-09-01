# 跨周期 YOLO 只能提案，必须重过因果数值语义

- **问题**：15m 训练的 full40 YOLO 直接扫 4h 时，在冻结的半个月全币种快照上产生 1,764 个结构合法框、221 个事件；人工图审发现不少框内六均线不密集，K 线也没有贴住均线。
- **死胡同**：只靠 `confidence + NMS + core4/5 + post2-9` 放行，能证明检测框横向位置可映射，却完全没有证明同样根数在新周期仍满足训练正例的均线密度、贴线和方向释放语义。提高 confidence 也不能直接修复这种语义错配，只会混合改变模型确信度与形态定义。
- **有效路径**：冻结原 1,764 个框，不重跑 YOLO、不调阈值；按每张保存的 W18/W19 右端物理截断数值输入，复算 ATR14、SMA/EMA 20/60/120，并套用训练正例生成时已冻结的全部可见谓词。最终只有 256 个框、34/221 个源事件存活；主要拒绝原因正是末端均线间距（970）和均线总包络（872）。实际方向通过 256 框，方向翻转空对照仅 4 框，配对 `p=2.04e-70`。
- **通用规则**：检测器换周期时，第一步不是重新解释 confidence，也不是凭图调一个新阈值；先把原预测冻结为 proposal，对同一候选逐一重放目标周期的因果数值语义，并同时报告框级与去重事件级结果。通过语义门仍只代表形态一致，不代表目标周期精度或交易收益。
- **牵连**：`yoyo/layers/l1_detection/semantic_gate.py`、`scripts/apply_4h_ma_launch_yolo_semantic_gate.py`、checkpoint holdout 使用 #7、`max_ma_envelope_atr=1.5`、`max_ma_spread_end_atr=1.1`；另见 [completed-history-yolo-needs-causal-prefix-semantic-gate.md](completed-history-yolo-needs-causal-prefix-semantic-gate.md) 与 [window-length-does-not-control-future-visibility.md](window-length-does-not-control-future-visibility.md)。
