# Shared headers and dialog lifecycles prevent page-specific layout drift

- **问题**：首页设置按钮同排后，其他页面仍保留独立设置行，浪费顶部空间；关闭弹窗缺少自然过渡。
- **死胡同**：仅修改首页样式无法覆盖其他页面；等待退出动画再发交易请求会改变交易时序。
- **有效路径**：按 Stitch 设计共用单行标题组件，所有页面统一安全区间距与 44px 操作目标。弹窗仅在 dismiss 时进入退出态，阻止退出期间重复操作；交易提交立即执行。保留焦点返回、背景锁定及减少动态效果设置。
- **通用规则**：布局约束属于共享组件，业务请求生命周期独立于装饰动画。
- **牵连**：`PageHeader.tsx`、`Dialog.tsx`、`style.css`；Stitch 项目 `8978791379095391159` 的 `design/flow-0.8.2/`。
