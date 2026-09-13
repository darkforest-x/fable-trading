# ZigZag 通道的历史坐标不是它成为可用特征的时间

- **问题**：Owner 提出 V8 持仓从未达到 +1R、长期困在 ChartPrime ZigZag Volume Profile 通道内时提前退出。历史图能看到完整通道，但这不说明入场时已经存在同一条通道。
- **死胡同**：直接在最终历史通道内数 K 线，或把沿斜通道运行等同于横盘。前者可能引用后来才生成的几何，后者可能把正常趋势误判为失败；没有执行这类有偏回测。
- **有效路径**：2026-09-14 在 TradingView 公开 Source code 页读取 186 行。方向切换才创建 profile，端点在历史，生成时 ATR 用于通道宽度；同向延伸可移动 ZigZag 线。成交量循环从当前 bar 回看，而几何端点更早，必须核对窗口对齐。将图形创建时间、端点时间与交易决策时间分别记录。原通道只有有限历史段，任何向右投影都是显式派生规则。
- **通用规则**：复刻结构指标前先审计每个对象何时生成、是否后移或回画、使用哪一时刻的波动和成交量。决策只能使用当时已经生成的对象；无对象记缺失。Never +1R 与 hit +1R 后回撤分开；退出按下一可成交价，不能回填入场价作为保本成交。
- **牵连**：[ChartPrime 公开指标](https://www.tradingview.com/script/TA5Q8m53-ZigZag-Volume-Profile-ChartPrime/)；`yoyo/evaluation/spike_v8_early_exit_study.py`；`analysis/p1_spike_v8_entry_process_early_exit_20260913.md`。本轮只审查机制和既有证据，未评估新收益、修改 Pine、通知或执行路径。候选设计与未知项存于 [Notion 待验证记录](https://app.notion.com/p/3da8856479af819ca295dd55f9e278c0)。
