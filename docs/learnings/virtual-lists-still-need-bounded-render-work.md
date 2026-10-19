# Virtual lists still need bounded render work

- **问题**：币种列表已虚拟化，但每像素滚动仍更新 React 状态；每次刷新又重算全部排名，即使展示顺序刻意冻结。
- **死胡同**：少渲染 DOM 并不自动减少渲染前的 O(n) 计算；对计算结果做 useMemo 后丢弃它，也仍然支付了全量扫描成本。
- **有效路径**：滚动状态量化为行边界，额外保留一行覆盖非整行视口；排序结果按排序方式和显式刷新版本保存，首个有效指标到达后复用；搜索输入归一化只做一次。测试用指标 getter 计数，1000 次实时刷新只读取一次排名，显式更新才重新计算。
- **通用规则**：虚拟化验收同时检查 DOM 数量、状态更新粒度和每次渲染前的计算量，不以存在虚拟列表组件作为性能证据。
- **牵连**：`yoyo/discipline_app/mobile/src/ContractOptions.tsx`、`contractRanking.ts`、`tests/discipline_app/contract_ranking.test.ts`。
