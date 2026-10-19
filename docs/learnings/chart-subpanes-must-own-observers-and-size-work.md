# Chart subpanes must own observers and size work

- **问题**：信号表现图即使数据未变，每次刷新仍生成完整图表配置并调用 resize；离开表现分析后观察器和图表实例继续存活。
- **死胡同**：只在 setOption 前比较数据签名，挡不住此前已经做完的格式化与布局工作；只检查顶层路由也覆盖不到账户和记录子页。
- **有效路径**：由可见图表子页持有观察器；先比较尺寸和数据，再做 resize / 构造配置；离开取消帧，闲置释放实例，返回重新挂载。
- **通用规则**：先判定工作是否必要，再构造昂贵参数；资源所有权要细到真正显示它的子页。
- **牵连**：`signal-workspace.js`、共享 `idleRelease` 与 `frontend_signal_workspace.test.cjs`；曲线仍使用完整退出时间序列，不抽样或改统计口径。
