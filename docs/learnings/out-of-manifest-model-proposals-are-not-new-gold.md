# 不在训练 manifest 里，不等于新的 Gold 正例

- **问题**：用现有模型扫描事后涨跌榜时，255 个去重事件中有 254 个不与训练输入重合；如果直接把“新事件”当“新正例”，会把 216 个语义拒绝误检和 38 个未经 Owner 确认的模型提议一起回灌数据集。
- **死胡同**：只按币种/时间查 manifest，或只报告 `novel_events` 总数。前者会漏掉动态裁窗的输入身份差异，后者把“没见过”和“标签为正”混成同一轴；再用模型自己的语义门自动定标签，会形成无法独立证伪的自训练闭环。
- **有效路径**：先用交易所、合约、窗口起止和长度找可能重合，再重渲染并比较解码像素 SHA；随后把两个轴正交记录：`novelty_status` 只回答是否被训练集表示，`review_bucket` 只回答语义门建议审正例还是 hard negative。最终明确拆成 38 个新正候选、216 个新 hard-negative 候选和 1 个训练输入重复事件，三类都保持 `training_eligible=false` 等待 Owner 逐样本裁决。
- **通用规则**：任何主动学习扫描先问三个不同问题：模型是否见过这个精确输入、它是否接近既有正事件、谁有资格决定标签。只有前两项全量可复验且第三项由独立人工裁决，才能把候选写入下一版数据计划；“out-of-manifest”永远只是一条血缘事实。
- **牵连**：`scripts/scan_15m_ma_launch_grade_a_daily_movers.py`、`scripts/verify_15m_ma_launch_grade_a_daily_movers.py`、`datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1/manifest.jsonl`、`experiments/active/exp-15m-ma-launch-grade-a-daily-movers-202510-v1/`；另见 [事后涨跌榜审图必须把选池前视与因果检测分开](posthoc-mover-gallery-must-separate-ranking-lookahead-from-causal-detection.md) 和 [人工审核的未来上下文必须与训练输入物理隔离](human-review-future-context-must-be-physically-separated-from-training-input.md)。
