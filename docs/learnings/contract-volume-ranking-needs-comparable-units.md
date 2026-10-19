# 跨币种成交量排名必须先统一计量单位

- **问题**：合约选择器需要按成交活跃度排序，但不同币种的币数与合约张数不能直接比较。
- **死胡同**：沿用 BTC、ETH、SOL 固定置顶无法反映成交量；直接比较 `vol24h` 或 `volCcy24h` 会把合约面值、币价差异混进排名。本次在字段核对阶段排除了这两种口径，未上线错误排序。
- **有效路径**：按 OKX 官方字段语义，用永续合约的基准币成交量 `volCcy24h × last` 估算 USDT 成交额，明确显示估算符号；缺失值不补成零、不回退到张数。打开选择器时保存排名快照，报价仍更新，只有用户切换排序、更新排名或首次获得有效数据时重排。
- **通用规则**：比较跨标的活跃度前，先核实交易所字段的资产单位；现价折算的估计值不能冒充逐笔累计成交额。展示列表的实时数字与交互期间稳定的行序应分别处理。
- **牵连**：`yoyo/discipline_app/market.py`、`mobile/src/contractRanking.ts`、`ContractOptions.tsx`；[OKX Get tickers](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-tickers)。金额/张数、缺失值/零、稳定快照与手动更新均有针对性测试。
