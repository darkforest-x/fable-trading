# Label Studio 导入器必须扫描所有本地文件字段

- **问题**：ETH 3m 双视图任务已成功导入 200 条，但因果图和未来图都返回 404。任务数据使用 `causal_image` 与 `review_image`，旧导入器却只从 `data.image` 推断本地存储根目录。
- **死胡同**：只看 API 的 `201 imported` 会误判成功；Label Studio 允许任务入库，即使对应 local-files storage 没有绑定。旧逻辑在找不到 `data.image` 时还静默回退到 `dense_15m_full`，让错误表面上像目录缺失，而不是字段发现失败。
- **有效路径**：遍历每条任务 `data` 中的全部字符串值，解析所有 `/data/local-files/?d=...` 查询参数并去重首级目录；找不到路径时立即失败，不再猜默认目录。导入后必须抽查首、中、尾任务的每个图片字段返回 HTTP 200，而不能只核对 task count。
- **通用规则**：Label Studio 多模态或多视图任务的导入验收至少包含三层：任务唯一数、配置字段存在、每个资源字段可读取。`POST import` 成功只证明任务入库，不证明标注页面可用。
- **牵连**：`scripts/ls_auto_import.py`、`tests/test_ls_auto_import.py`、`datasets/eth_3m_v10_prebox200/label_studio/tasks.json`、Label Studio project 53 / local storage 63。数据集挂载保持只读；未读取 holdout。
