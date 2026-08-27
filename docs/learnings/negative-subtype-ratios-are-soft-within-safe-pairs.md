# hard / easy 子类型比例也不能凌驾于安全配对

- **问题**：10,000 个正例各配一个同币同源同时间块负例时，预先按奇偶固定 5,000 hard / 5,000 easy；INJ 与 ENJ 的个别半年在金标禁入、no-launch、同 split 和不重叠之后没有足够 hard 容量。
- **死胡同**：继续扩大时间块、降低 no-launch 门、缩小正例保护区，或者因为 hard 不够就跨币抽样，都能凑出漂亮的 50/50，但会把安全约束变成比例的附属品。它与[负例比例不能凌驾于金标禁入区](negative-ratio-must-not-weaken-gold-exclusion.md)是同一种错误，只是这次发生在负例内部子类型。
- **有效路径**：保留奇偶分配为不看行情结果的首选；首选类型在同币、同源、同半年、同 split 内耗尽时，只允许切换到另一种已经冻结且同样安全的负例类型，并对 train、val、全体分别设 hard 占比下限。最终只有 1 个 hard→easy 回退，实际为 hard 4,999 / easy 5,001。
- **通用规则**：总正负配对数可以是硬交付，但 hard/easy、波动桶等负例内部构成是软目标；第一步先冻结标签纯度、时间隔离、金标禁入和不复用，再用占比下限防止某一层完全消失。
- **牵连**：`yoyo/datasets/ma_launch_owner_yolo_dataset.py`、`exp-15m-ma-launch-owner-yolo-dataset10000-v1/preregistration.json`、150-bar purge、同源同半年匹配、train/val hard-negative 覆盖。
