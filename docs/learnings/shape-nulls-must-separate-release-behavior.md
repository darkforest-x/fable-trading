# 形态空值对照必须与启动后走势分开

- **问题**：只排除框高但仍保留 post1..post5 release 门，却把结果命名为 `shape_only`，会把核心形态和未来释放混成一个数字。
- **死胡同**：用一次 pass rate 同时回答“框是否落对”与“后面是否走出来”；当边界平移时，post 窗也随之变化，无法判断下降来自形态还是后续走势。
- **有效路径**：同一批固定样本并列报告两组：morphology-only（排除 release 与 box）和 full-no-box（保留 release、排除 box）；方向翻转对照也使用相同双口径。
- **通用规则**：凡是指标名含 `shape_only`，调用链必须显式关闭所有未来/结果轴；需要完整过滤结果时另设字段，不能靠默认参数暗中混入。
- **牵连**：boundary-shift null、direction-flip null、`hard_gate_failures(include_release=...)`、报告解释与后续阈值比较。
