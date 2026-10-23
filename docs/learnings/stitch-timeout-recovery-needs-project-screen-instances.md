# Stitch 超时后，列表没有新页面不等于生成失败

- **问题**：2026-10-23 周一 1.618 HTML 报告的 Stitch 生成调用 300 秒超时；多次 list_screens 没有新页面，Owner 要求继续等待，不能跳过设计。
- **死胡同**：只比较 list_screens 返回的 screen name。该接口未列出已生成的报告页，重复检查同一索引不能证明任务失败；再次生成还可能造成重复。
- **有效路径**：读取 get_project 的 screenInstances，发现报告标题和 screen ID，再调用 get_screen，成功取得设计图与 HTML 文件资源，并用 download_file 读取两者。无需重复生成。
- **通用规则**：异步写入超时后，先恢复已有任务；列表查询阴性时，核对项目直接引用与单资源读取，不把单一索引缺失当作不存在。
- **牵连**：项目 8978791379095391159；报告页 5230353fc774421d9260d60091ad8a89；设计出处保存在 artifacts/monday-cases-report-20261023/design/provenance.json。遵守 GEMINI_3_8_FLASH 和 UI 先设计要求。
