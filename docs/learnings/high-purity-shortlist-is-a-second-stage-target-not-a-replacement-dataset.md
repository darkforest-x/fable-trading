# 高纯 shortlist 应是二层目标而不是替换整个检测数据集

- **问题**：260 个完美候选不足以从头训练稳定的视觉检测器，若直接替换原 10,000 正例，会同时损失定位覆盖和币种/时间多样性。
- **死胡同**：把“更纯”误解成“只用这一小批训练”，或把 9,740 个非完美 weak-positive 全部丢弃；前者易记忆少数图，后者浪费已经验证过的宽召回定位信息。
- **有效路径**：保留 10,000 正例 + 30,000 背景作为 L1 宽召回检测层，把 260 个高纯事件与其余 9,740 个近形态组成 L2 的 perfect-vs-ordinary 判别目标；若未来获准训练，先做时间切分和 Gold/DIRECT 门，不把自动 shortlist 冒充 Gold。
- **通用规则**：小而纯的集合优先用于校准、二层判别、主动学习或精调，不应在没有独立覆盖审计时替换大而宽的检测集合。
- **牵连**：`ROADMAP.md` P0/P1 门、`datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2/`、`yoyo/layers/l1_detection/`、`yoyo/layers/l2_judgment/`、`training_eligible=false`。
