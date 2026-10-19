# Period shortcuts must own both result identity and focus

- **问题**：币种行汇总多个周期后，用户需要从周期标签直接打开对应的 K 线与识别框；手机还需要关闭选币弹窗并回到图表。
- **死胡同**：旧标签只是币种按钮里的展示元素，点击会沿用上次周期。不能直接在原按钮里嵌套周期按钮；也不能先选择币种再切周期，这会先请求错误周期。已有方向筛选或图表视野还可能把目标框藏起来。
- **有效路径**：币种按钮与周期按钮并列，周期操作一次指定 symbol/timeframe，复用版本号防止迟到响应覆盖。显式周期点击恢复完整形态口径并定位最新框。列表更新保留实际按钮焦点；手机关闭弹窗后将焦点交给图表标题并恢复滚动。无形态与历史不足分别展示，不沿用旧框。
- **通用规则**：复合列表的快捷入口必须携带完整资源标识。除请求结果外，还要验证焦点、筛选和可视范围是否把用户带到目标；嵌套交互应改成并列操作，触摸目标至少 44px。
- **牵连**：`yoyo/monitor/static/yolo-detect.js`、`yoyo/monitor/static/yolo-detect.css`、`tests/monitor/frontend_yolo_detect.test.cjs`。DOS 5m/15m 真实框 ID 对应、390px/320px 布局与弹窗恢复验证保存在 `output/detection-period-tags-20261017/`；不修改后台巡检或执行服务。
