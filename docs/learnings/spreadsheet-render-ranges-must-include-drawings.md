# Spreadsheet visual verification must include drawing anchors

- **问题**：回测工作簿的累计 R/回撤图已写进 XLSX 媒体包，却在默认 `A1:H24` 渲染里不可见；日期也以 Excel serial 显示。
- **死胡同**：只检查 `xl/media` 或默认左侧表格截图，不能证明读者能看到嵌入图，也不能确认日期样式。
- **有效路径**：保留 `Date` 为数值日期并设置明确的日期格式；将曲线改为绑定源数据的原生图表，并按图表锚点渲染 `A1:P24`。保存后检查 XLSX 压缩包、图表 XML 锚点和实际渲染。
- **通用规则**：含图表的工作簿先查 drawing anchor；渲染范围必须覆盖该 anchor，且日期应以数值和 number format 验收，不能靠格式化字符串掩盖问题。
- **牵连**：`outputs/01a086ad-0b77-7380-b6a6-2b62f505d12b/spike_v1_excel_20260911/build.mjs` 与同次 XLSX 交付物；使用 `@oai/artifact-tool`。
