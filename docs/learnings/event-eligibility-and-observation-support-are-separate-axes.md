# Event资格与可观察支持必须分开计数

- **问题**：将持久结构状态收紧为K1本根首次建立/反转事件时，如何保持原母单与随机三控的可比性，而不把缺数据变成被过滤交易。
- **死胡同**：把所有非事件都标abstain会吞掉no_confirmed_break等unknown；只留下三个control也accepted的组，会把“有可观察对照”偷偷改成“对照也满足新策略”，分母和研究问题都变了。
- **有效路径**：先沿用原结构known/unknown，再仅在known内部判断当前方向事件。原251母与744控制身份不动。完整组要求母及原三个control都known，known可为accepted或abstain。合成反例验证：case accepted、三control全abstain仍是完整组；case unknown、三control known仍不完整；零分母为null。
- **通用规则**：增加事件过滤时，先独立列出可观察性、资格、分组完整性三条轴，再写聚合；不能靠删unknown或重抽对照提高样本覆盖。
- **牵连**：`yoyo/evaluation/hourly_impulse_structure_event_support.py`、`tests/test_hourly_impulse_structure_event_support.py`、V25事前支持门；该实现验证不代表事件足够多，更不代表盈利或Pine运行时一致性。
