# 回放账本联结必须同时保存身份和来源证据

- **问题**：monitor 的 replay event 使用短 journal id，而 covered ledger 使用独立的 source-event SHA；把任一 id 单独当作关联键会漏掉同一信号或把不同来源混为一条记录。
- **死胡同**：最初 signal-only importer 有意不复制 outcome，因而“直接比较 event id”既不成立，也无法说明前端后来显示的退出字段来自哪一份冻结 OHLC 与 coverage receipt。
- **有效路径**：先用 `venue/symbol/timeframe_min/signal_bar_open_ms` 做一对一四元组联结，再同时保存两种 id、Pine/source、ledger、coverage receipt 和冻结 OHLC SHA。已实现与 censored 分开写入，并把内部 exit enum 翻译为用户可读文本；联结命令只更新已有 journal payload，不经 event insertion/outbox/candidate 路径。
- **通用规则**：任何把离线结果挂到运行时卡片的操作，先证明键唯一并记录双方身份与每个输入的 hash；身份相同不等于结果已独立验证。
- **牵连**：`yoyo/monitor/replay_ledger.py`、`yoyo/monitor/store.py`、`yoyo/monitor/static/app.js`、`tests/monitor/test_replay_ledger.py`、`experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/replay_ledger_link_receipt.csv.gz`。
