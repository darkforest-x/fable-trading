# SPIKE V1 回放记录与 v2 覆盖账本逐笔关联

## 结论

2026-09-11（北京时间）已将 monitor 中全部 **1,019** 条 `source=replay, confirmation=raw` 的 V1 信号，与冻结 `covered-v2-next-open-risk-realized-event-sequence` 账本按四元组 `venue / symbol / timeframe_min / signal_bar_open_ms` 一对一关联。所有记录同时保存 monitor journal id、账本 source-event id、Pine/replay source SHA、ledger SHA、coverage receipt SHA 和对应冻结 OHLC 文件 SHA。

关联只证明这条回放卡与哪一条覆盖账本、哪一份冻结输入相对应。它不构成独立经济审计、完整市场覆盖、账户收益、策略有效性或盈利结论。前端对已实现行只显示该账本的退出与净 R，并标为“未独立收益审核”；18 条右端 censored 行不显示净 R、胜率、PF 或净收益。

## 冻结输入与复现

| 项目 | 值 |
| --- | --- |
| V1 Pine / replay source SHA256 | `18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2` |
| 覆盖账本方法 | `covered-v2-next-open-risk-realized-event-sequence` |
| covered ledger SHA256 | `d35483bcfad3a567376f6009aaac58f41981e2c522a6478a745d3481e1730064` |
| coverage receipt SHA256 | `014be0a70370527e6e0dba89ff24b55208d666fa1b1f85b6f857bd05442e2fff` |
| 信号时间范围（UTC） | 2024-09-10 16:00 至 2026-09-09 16:30 |

```bash
python3 -m pytest -q tests/monitor/test_replay_ledger.py tests/monitor/test_spike_v1_api_replay.py
node --test tests/monitor/frontend_cards.test.cjs
python3 -m yoyo.monitor.replay_ledger \
  --database "$HOME/Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3" \
  --receipt experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/replay_ledger_link_receipt.csv.gz
python3 scripts/md_to_html.py analysis/p1_spike_v1_replay_ledger_link_20260911.md --out-dir analysis/html
```

上述实际运行返回：`linked=1019`、`matched_realized=1001`、`matched_censored=18`、`unmatched=0`、`source_mismatch=0`、`ohlc_missing=0`。receipt 中的 1,019 行没有重复四元组、monitor id 或 ledger id。运行后 Bark pending/failed/unknown 均为 0；模型 candidate 计数没有由回放联结创建或改变。

## 覆盖分布

| 维度 | 30m | 1H | 4H | 合计 |
| --- | ---: | ---: | ---: | ---: |
| 已关联回放卡 | 592 | 301 | 126 | 1,019 |
| 已实现账本行 | — | — | — | 1,001 |
| censored | — | — | — | 18 |

| 交易所 | 已关联回放卡 |
| --- | ---: |
| Binance | 733 |
| OKX | 233 |
| Gate | 53 |

逐笔证据在 [replay_ledger_link_receipt.csv.gz](../experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/replay_ledger_link_receipt.csv.gz)：每一行包含双方 id、四元组、状态、四种 SHA 及冻结 OHLC 文件。运行库的同名 `covered_ledger` payload 还保留已实现行的 entry/exit/净 R，或仅保留 censored 状态。

## 实现边界

`replay_import` 仍只导入信号字段。`replay_ledger` 是单独命令，先验证 manifest 的方法、Pine 和 source SHA，再完整准备所有更新，最后只用 `Store.update_event_payload()` 修改既有 replay journal payload；它不走 `upsert_event()`，因此不会新建 event、Bark outbox、Telegram outbox 或 YOLO candidate。无匹配、source 不符或 OHLC 文件缺失的行保持 `performance_status=unverified`。

前端将三类状态分开：已关联已实现、已关联 censored、未关联。页面上的“覆盖净 R · 非账户”是 frozen covered ledger 的单笔字段，不是账户回撤、组合收益、实时成交或独立验证后的推荐。

## 风险与诚实声明

这 1,019 条是已有 partial covered inputs 的回放子集，不是完整两年三所全市场评估。covered ledger 虽已修复 next-open 风险分母、censoring 与事件序列描述问题，仍缺完整覆盖、匹配随机对照、资金组合模型和独立经济审计。不得从此关联推导 V1、YOLO、通知或实盘仓位调整。

历史图的信号后 K 线仍只用于回看，未输入 live scanner 或 YOLO；回放继续不通知。运行中的 live scanner、Bark cutover 和模型候选不因该数据库元数据联结而改变。
