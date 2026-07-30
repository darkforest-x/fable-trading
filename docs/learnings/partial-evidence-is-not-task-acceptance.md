# 部分证据不等于任务验收完成

- **问题**：VPS 已有 API、页面和截图后，运行状态把 Todo 6 写成 prior green，但原计划的公开脱敏、当前配置和桌面/移动端完整验收仍未关闭。
- **死胡同**：看到若干通过的子检查或提交记录，就跳过原工单的完整 acceptance criteria。
- **有效路径**：完成状态只由原工单逐条验收决定；部分实现单列为 partial evidence，不能替代任务勾选。
- **通用规则**：汇报完成前同时核对计划 checkbox、完整验收条件、最新配置下的真实表面证据，三者缺一不可。
- **牵连**：Grok Todo 6、Todo 7、Todo 8、`.omo/runtime/GROK_2DAY_STATUS.md`、`.omo/runtime/NEXT_TASK.md`。
