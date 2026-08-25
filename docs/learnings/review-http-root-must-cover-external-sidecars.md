# 审核页引用兄弟产物时 HTTP 根必须覆盖共同祖先

- **问题**：审核 HTML 位于一个实验目录，9,000 张只读图片复用另一个兄弟实验的 sidecar。磁盘上的相对路径逐个解析都存在，但按 README 从 HTML 自己的 `results/` 启 HTTP 服务后，浏览器把 `../../../兄弟实验/...` 规范化到服务根之外并返回 404。
- **死胡同**：只做 `Path.resolve().is_file()` 和 HTML/JavaScript 静态检查会误判成功；相对路径在文件系统语义下成立，不代表选定 HTTP document root 允许它被请求。
- **有效路径**：保留从 HTML 到 sidecar 的正确相对路径，把 HTTP 根提升到两个实验目录的共同父目录 `experiments/active/`，并用真实浏览器检查网络请求为 200。不要为了迁就过深的服务根复制 1.4GB 图片或改写已冻结候选产物。
- **通用规则**：任何 HTML 复用目录外 sidecar 时，交付前同时验证两件事：文件系统解析存在，以及按文档中的真实 HTTP root 发出的 URL 可访问；HTTP root 至少要覆盖所有引用文件的共同祖先。
- **牵连**：`scripts/build_15m_candidate_boundary_review.py`、审核包 README、候选 `review_charts/` 只读复用合同、避免重复大体积图片。
