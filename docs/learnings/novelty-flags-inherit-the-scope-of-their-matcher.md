# “新事件”只对原排重器检查过的范围成立

- **问题**：Grade-A 5,053 个候选被标作 new_event_review，下一轮准备把它们用于人工校准、
  难负例替换及评估设计；这一名字容易被理解成跨来源无重合、甚至未见验证。
- **死胡同**：仅凭旧 novelty 标记排除原 30 个训练重合项。旧索引只收 Binance，近邻比较
  只看同方向正核心末端 5 根内，未证明 OKX、异方向、负例和完整依赖区间的隔离。
- **有效路径**：保留历史队列语义，在本轮另从完整正负 manifest 合并字面同币跨 venue
  依赖区间，加现行 150-bar 时间缓冲，再做成员排重。即使通过，也把历史决策曝光保留为
  not_established；人工校准集不能因哈希或成员不重合就升级成独立验收集。
- **通用规则**：复用 novelty / clean / unseen 之类标记前，先读它的比较总体、匹配键、
  时间半径和遗漏项；新用途超出证明范围时另做审计，不扩大旧标记的含义。
- **牵连**：`scripts/scan_15m_ma_launch_grade_a_daily_movers.py` 的训练索引与重合检查；
  `yoyo/datasets/grade_a_calibration.py`；`experiments/active/exp-15m-grade-a-owner-calibration-20260907-v1/PLAN.md`。
