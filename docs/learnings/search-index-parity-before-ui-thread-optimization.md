# 搜索移出主线程前先固定原有匹配语义

- **问题**：直播转写每次输入重建上下文、排序并渲染最多 300 条，输入响应与语料长度耦合。
- **死胡同**：只减少展示条数不减少扫描成本；改成全文串匹配可能让相邻句命中、中心句要求或排序发生漂移。
- **有效路径**：上下文和小写文本一次建索引，Worker 搜索、50 条分页、过时请求不回写；不支持 Worker 时分块让出执行权。真实 40,806 段语料的 8 组查询与原算法逐条核对一致，再测索引与查询耗时。
- **通用规则**：性能验收同时保留行为等价证据；结果统计与 DOM 数量分开，文本高亮只在转义后的文本片段上加标签。
- **牵连**：static/transcript-search.js、langlang.js、frontend_transcript_search.test.cjs；output/playwright/frontend-performance-20261016/search-parity.json。
