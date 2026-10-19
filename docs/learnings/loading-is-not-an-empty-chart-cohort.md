# Loading is not an empty chart cohort

- **问题**：信号表现页面在请求尚未返回时显示“0 笔”和“暂无已结束记录”，把未知状态呈现成确定统计。
- **死胡同**：只在筛选栏显示加载提示不够，图表自己的空态、辅助标签和说明仍在声称零记录。
- **有效路径**：把加载状态单独传给图表配置，同时更新曲线、周期图、辅助标签和说明；只有成功返回的空集合才展示无记录。
- **通用规则**：统计组件的 loading、empty 和 unavailable 是不同语义，不能依靠空数组统一处理。
- **牵连**：`signal-workspace.js` 和 `frontend_signal_workspace.test.cjs`；不改变信号统计或账户收益口径。
