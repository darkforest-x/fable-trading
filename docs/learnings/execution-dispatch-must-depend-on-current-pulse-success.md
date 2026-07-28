# 即时执行必须依赖当前脉冲成功

- **问题**：周期脚本没有启用 `set -e`，前向追踪命令失败后仍会到达 `executor --once`，即时执行因而可以消费未被本轮成功刷新的状态。
- **死胡同**：假设“命令失败会自动终止 shell”不可靠；全局改成 `set -e` 又会把本来允许失败的数据更新、采集和通知路径一起改变。
- **有效路径**：只捕获关键 `forward_track` 的退出状态，并用局部成功标记包住 post-pulse executor；非关键 side-step 仍按原语义运行。
- **通用规则**：在“产生状态 → 立即执行”链路中，必须让消费者的本轮调度显式依赖生产者成功，不能把 shell 的默认继续执行当成容错。
- **牵连**：`scripts/forward_pulse.sh`；`forward_track`、`real_tip_collect`、`src.execution --once`；不引入新的新鲜度阈值。
