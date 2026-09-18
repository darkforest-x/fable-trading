# 移植 Pine 拐点时，TV 没公开的相等高点规则要用「单侧多出的拐点数」衡量，不要看宽松平局计数

- **问题**：2026-09-18 移植 Owner 的 SPIKE V10.4（双轨三点下降线）做 6 周期回测。
  `ta.pivothigh` 遇到相等高点怎么判，TradingView 文档没写，网上的第三方说法也互相矛盾。
  仓库已有的移植（`trendline_break._confirmed_pivots`）选了两侧严格大于，并附带一个
  「平局计数」＝ 宽松比较（两侧 ≥）成立、严格比较不成立的 K 线数。

- **死胡同**：主跑的覆盖表里，这个平局计数在 5m 上是 **17,878,164**（8,262 万根里占 22%），
  看上去像「移植有两成拐点是错的」。其实它把**平台**也算进去了：一段高点完全相同的横盘里，
  每一根在两侧 ≥ 下都是「拐点」，TV 不可能这样判。这个数既不是 TV 的任何一种规则，
  也不能说明换规则后信号会变多少——**拿它当敏感性指标是空的。**
  另一个死胡同是「挑一个看起来对的规则、不提」：粗 tick 币在低周期上，这个选择真的会改信号。

- **有效路径**：
  1. 只数有意义的替代规则：**左侧严格、右侧 ≥**（平台只取最左一根，任何合理实现都会这样收口）
     比严格规则**多出**多少拐点。实测：BTC 5m +2.6%、DOGE 5m +4.6%、TRX 5m +11.2%、
     ACX 5m +21.8%；到 1h 全部 ≤6.3%。差别由「tick 相对波动的粗细」决定，周期越短越严重。
  2. 在读任何收益之前把它写进 PROJECT_PLAN，作为**只改这一个变量**的敏感性复跑（`pivot_ties="right_inclusive"`），
     并写明判定：两种规则判定不同的周期标为「依赖拐点口径，不可下结论」。
  3. 结果：联合信号 5m +24%、15m +13%、1h +8%、4h +4%，但**每一格正负号、每个周期的判定都不变**，
     每笔净 R 差 ≤0.03R。结论不依赖这个未公开细节——这句话现在有证据，而不是假设。
  4. 把新的 `pivots()` 严格路径用测试钉在旧 `_confirmed_pivots` 上，保证加开关没改动主跑用的那条路径。

- **通用规则**：移植依赖一个**文档没写的边界规则**（平局、含/不含端点、na 处理）时，
  第一步不是选一个，而是**按周期 × tick 粗细量出「另一种合理规则会多/少出多少事件」**；
  超过几个百分点就事前登记一次单变量复跑。宽松/全不等式那类计数会把退化情形（平台、常数段）
  一起数进去，只能说明「数据里有平台」，不能说明「规则会改结论」。

- **牵连**：`yoyo/evaluation/spike_v10_4.py`（`pivots`、`V104Params.pivot_ties`）、
  `yoyo/evaluation/trendline_break.py::_confirmed_pivots`（同一个未公开规则，两侧严格）、
  `tests/evaluation/test_spike_v10_4.py::test_strict_pivots_equal_the_repo_convention_and_one_sided_breaks_plateaus_once`、
  `experiments/active/exp-spike-v10-4-joint-multitf-20260918-v1/PROJECT_PLAN.md`「追加」节、
  报告 `analysis/p1_spike_v10_4_joint_multitf_20260918.md` 第 7 节。
  相关：[同一指标家族的端点问题](a-descending-trendline-break-is-a-crossing-not-a-breakout.md)。
