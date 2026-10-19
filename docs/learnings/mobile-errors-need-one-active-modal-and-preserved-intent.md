# Mobile errors need one active modal and preserved intent

- **问题**：手机下单失败后只在页面顶部显示提示，滚动位置和键盘使错误离开视线；系统下拉框也与应用样式不一致。
- **死胡同**：在确认抽屉外再叠一层错误弹窗会形成两个焦点陷阱；删除确认状态又会丢失网络超时请求的去重编号。
- **有效路径**：保留订单意图和编号，但每次只渲染一个可交互弹层；错误关闭后恢复确认，空字段错误返回对应输入。选择面板使用同一弹层基础组件。输入时隐藏悬浮导航并提供完成操作。
- **通用规则**：弹层可见状态与业务请求状态分开；出现网络错误不代表请求未被服务端受理。
- **牵连**：`mobile/src/App.tsx`、`Dialog.tsx`、`ticketValidation.ts`、`useKeyboard.ts`。Stitch 图和代码来源保存在 `design/porcelain-jade/polish-0.4/sources.json`。
