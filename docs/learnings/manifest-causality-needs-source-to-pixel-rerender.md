# Manifest 写着因果不等于像素真的因果

- **问题**：manifest 可以同时写出正确的 `decision_at` 和 `visible_end_at`，但生成器实际切片仍可能多带未来 bar；只检查字段、文件存在和哈希自洽会把错误像素认证为正确。
- **死胡同**：对文件做 SHA 并与同一次构建写出的 SHA 比较，只能证明文件没被改，不能证明它来自声明的源区间；抽查几张也无法对全量因果性作结论。
- **有效路径**：审计器重新打开 holdout 前源 K 线，按 manifest 的 source index 和窗口边界重算 MA、重渲染每一张图，再与落盘 PNG 做逐像素比较；同时独立核对 decision/outcome-start 时间和文件标签哈希。
- **通用规则**：凡是“可见到哪里”决定结论的数据集，发布门必须包含 source-to-pixel 全量重建；manifest 是待验证的 claim，不是证据本身。
- **牵连**：renderer 版本、源路径与索引、MA warmup、图像尺寸、holdout 截断、构建器 commit/hash、审计运行时间。
