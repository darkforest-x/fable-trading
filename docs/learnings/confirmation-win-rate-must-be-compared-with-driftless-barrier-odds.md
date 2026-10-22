# 确认后胜率升高可能只是离目标更近，要和无漂移障碍概率比

- **问题**：周一 1.618 首次触及后，“1h/2h/4h 后已收回 1.618 以内”“从未收在外面”等确认状态，把回到周一边界的比例从 39% 抬到 56–64%，看上去确认能判断 1.618 有效。
- **死胡同**：直接比较原始有效率（确认 64% 对未确认 44%），或和同周同方向随机入场比（+0.24R 超额，p=0.0002）。前者忽略了确认时价格已经朝目标走了一段，止损/目标距离变了；后者的对照本身有偏，见 [same-week-random-controls-inherit-the-weeks-trend](same-week-random-controls-inherit-the-weeks-trend.md)。
- **有效路径**：对每笔入场计算固定价格止损/目标下的无漂移命中概率 `risk/(risk+reward)`，和实际有效率并列。后一年 inside_clean 63.6% 对理论 63.1%，k4 unresolved 44.2% 对 45.2%，首次触及 37.6% 对 38.2%：全部等于随机游走，平均 R≈0。确认只改变了几何，没有增加信息。
- **通用规则**：凡“等确认后胜率更高”的结论，第一步算每笔入场的 `risk/(risk+reward)` 均值，只有实际命中率显著高于它（或平均 R 显著 >0）才算确认有信息；胜率上升而盈亏比同步下降，不是优势。
- **牵连**：`yoyo/evaluation/monday_1618_post_touch.py`（`driftless` 列）、`experiments/active/exp-monday-1618-post-touch-20261023-v1/summary.json`、`exp-monday-1618-validity-20261023-v1`；15m 保守同K先止损会让实际值略偏低。
