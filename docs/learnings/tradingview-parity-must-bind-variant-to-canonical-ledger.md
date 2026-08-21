# TradingView parity 必须把策略版本绑定到唯一 canonical 账本

- **问题**：同一 Pine 研究同时存在 V9 与 V12F 后，对账器若只比较调用方传入的两张表，可能把错误版本、错误时期或截短后的账本误判为逐笔一致；两版本共用输出文件还会让后一次结果覆盖前一次身份。
- **死胡同**：把预期笔数写成 `len(canonical)`，或只新增一个可选 CSV 路径。这样虽然代码更“通用”，但 canonical 本身选错时仍会自洽通过，无法证明 TradingView 导出对应的是被冻结策略。
- **有效路径**：为每个版本集中冻结 source、variant selector、period selector、expected trade count 和独立 output；命令行只接受已注册版本，对账同时要求笔数、入场时间与方向、退出时间和成交价全部一致，未知版本与任何 holdout 行 fail closed。
- **通用规则**：外部执行引擎 parity 的第一步不是比较数值，而是先锁定被比较产物的身份合同；版本、时期、账本来源、预期基数和输出命名必须作为同一个不可拆的配置注册。
- **牵连**：`scripts/reconcile_pine_eth_15m_tradingview.py`、`tests/test_reconcile_pine_eth_15m_tradingview.py`、V9 110 笔与 V12F 97 笔 pre-holdout canonical ledgers、TradingView Strategy Tester 导出、2026-03-01 安全结束门与 2026-05-04 repository holdout 门。
