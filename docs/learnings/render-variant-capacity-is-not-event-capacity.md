# 裁剪变体容量不能冒充独立事件容量

- **问题**：冻结 A 级门后，扩展 4916 万根 15m K 线只得到 1043 个几何合格独立事件，但交付目标是 8000 张像素不同的训练正样本。
- **死胡同**：把 1043 直接说成 8000 个事件是虚报有效样本量；重复同一 PNG 没有新增信息；为了凑 8000 放宽 A 级门又会改变标签语义。最初限制每事件 5–6 个位置也只有最多 6294 张，程序正确地 fail closed。
- **有效路径**：保持 A 级门、4/5 根核心、18/19 根总窗和时间切分不变，只把真实连续 K 线的固定上下文位置扩为 8 个；1043 个事件产生 8000 张像素唯一 PNG。所有变体用同一 `sample_id` 分组并强制进入同一时间 split，报告同时列“8000 张图”和“1043 个独立事件”。
- **通用规则**：先在最终 NMS、去重、几何门和切分后量独立事件容量；裁剪变体可以增加位置鲁棒性的训练输入，但永远不能增加统计独立事件数。训练、验证和报告都必须按 event group 管理，禁止把变体拆到不同 split。
- **牵连**：`yoyo/datasets/ma_launch_owner_grade_a8000.py`、`experiments/active/exp-15m-ma-launch-owner-grade-a8000-v1/capacity_attempts.json`、`datasets/ma_launch_owner_grade_a8000_v1/manifest.jsonl`；另见 [严格稀有形态不能靠重复渲染扩容](rare-strict-patterns-cannot-be-expanded-by-rendering-duplicates.md) 和 [容量必须在最终事件 NMS 后测量](dataset-capacity-must-be-measured-after-final-event-nms.md)。
