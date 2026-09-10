# SPIKE V1 回放记录与 v2 覆盖账本逐笔关联

## 结论

2026-09-11（北京时间）已将不可变 snapshot 中全部 **6,185** 条 30m / 1H / 4H `source=replay, confirmation=raw` 的 V1 信号导入 monitor，并按四元组 `venue / symbol / timeframe_min / signal_bar_open_ms` 与内容寻址的 `covered-v2-next-open-risk-realized-event-sequence` 账本核验。全部记录保存 monitor journal id、账本 source-event id、Pine/replay source SHA、ledger SHA、coverage receipt SHA 和对应冻结 OHLC 文件 SHA。1D 的 68 条账本行明确不属于当前三周期 UI/通知协议，没有导入。

此前 1,019 条链接指向会被 `evaluate-covered` 覆盖的可变汇总文件；旧文件字节不可再读取，故先全部降为 `stale_evidence_unverified` 并隐藏 outcome，审计中保留旧 status、id 和 SHA。随后才复制当前字节为 SHA 命名副本并重链，再 signal-only 导入剩余三周期行。核验结果为 6,151 条可联结（6,082 realized、69 censored），另有 34 条在 frozen OHLC 根目录确实缺同源文件，保留 `unverified/ohlc_missing`、没有 outcome 或历史图替代。关联只证明回放卡与该特定快照和冻结输入相对应；它不构成独立经济审计、完整市场覆盖、账户收益、策略有效性或盈利结论。

## 冻结输入与复现

| 项目 | 值 |
| --- | --- |
| V1 Pine / replay source SHA256 | `18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2` |
| 覆盖账本方法 | `covered-v2-next-open-risk-realized-event-sequence` |
| 不可变 covered ledger SHA256 | `b15b69b8864e5eb651f2417fc6681ea688b5fbb95dacd15af1284b42744e3578` |
| 不可变 coverage receipt SHA256 | `3e4c08972a2680fae48cc7b7862e960d8a6c969ab387531aca996ffe526a69a2` |
| 不可变副本 | `results/immutable_replay_ledger/covered_trade_ledger.b15b69…e3578.csv.gz` 与 `coverage_progress.3e4c08…69a2.json` |
| 信号时间范围（UTC） | 2024-09-10 16:00 至 2026-09-09 16:30 |

```bash
python3 -m pytest -q tests/monitor/test_replay_ledger.py tests/monitor/test_spike_v1_api_replay.py
node --test tests/monitor/frontend_cards.test.cjs
python3 -m yoyo.monitor.replay_ledger \
  --database "$HOME/Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3" \
  --snapshot-dir experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/immutable_replay_ledger \
  --receipt experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/replay_ledger_link_receipt.csv.gz
python3 scripts/md_to_html.py analysis/p1_spike_v1_replay_ledger_link_20260911.md --out-dir analysis/html
```

首次旧证据处理返回 `invalidated=1019`；随后全量操作的 import 返回 `inserted=5166`、`already_present=1019`、`outside_v1_contract=68`。修正审计分页后，最终重链返回 `replay_events=6185`、`reconciled=6185`、`linked=6151`、`matched_realized=6082`、`matched_censored=69`、`ohlc_missing=34`、`unmatched=0`、`source_mismatch=0`。receipt 的 6,185 行没有重复四元组、monitor id 或 ledger id；每行都指向同一对当前不可变 SHA。运行后 Bark pending/failed/unknown 均为 0；模型 candidate 计数没有由回放导入或联结创建或改变。

## 覆盖分布

| 维度 | 30m | 1H | 4H | 合计 |
| --- | ---: | ---: | ---: | ---: |
| 已导入回放卡 | 3,594 | 2,039 | 552 | 6,185 |
| 可联结已实现 | 3,563 | 2,009 | 510 | 6,082 |
| 可联结 censored | 15 | 15 | 39 | 69 |
| 缺同源冻结 OHLC | 16 | 15 | 3 | 34 |

| 缺文件交易所 | 未联结行 |
| --- | ---: |
| Binance | 24 |
| Gate | 10 |
| OKX | 0 |

逐笔证据在 [replay_ledger_link_receipt.csv.gz](../experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/replay_ledger_link_receipt.csv.gz)：6,185 行全部包含双方 id、四元组、状态、四种 SHA 和逐条 `link_reason`。可联结行记录冻结 OHLC 文件；34 条缺文件行的 `link_status=ohlc_missing`、`link_reason=frozen_ohlc_missing`，不携带 outcome。运行库对已实现行保留 entry/exit/净 R，对 censored 行只保留 censored 状态；原 1,019 行还保留 `superseded_stale_evidence`，其中包含不可读旧可变证据的原 status/id/hash 审计。

## 实现边界

`replay_import` 仍只导入信号字段。`replay_ledger` 是单独命令：先把既有链接降级为不显示 outcome 的 stale audit，再验证 manifest 的方法、Pine 和 source SHA，把 ledger/receipt 原样复制并逐字哈希为不可变副本，最后才用 `Store.update_event_payload()` 修改既有 replay journal payload；它不走 `upsert_event()`，因此不会新建 event、Bark outbox、Telegram outbox 或 YOLO candidate。无匹配、source 不符或 OHLC 文件缺失的行保持 `performance_status=unverified`。

前端区分已关联已实现、已关联样本结束时尚未退出、缺少同源冻结 OHLC、证据已过期和未关联。缺 OHLC 不用当前行情、其他交易所或推断 K 线替代；证据过期时也不显示旧 outcome。页面上的“单笔净 R · 非账户收益”只在当前不可变 frozen covered ledger 的逐笔字段存在时展示，绝不是账户回撤、组合收益、实时成交或独立验证后的推荐。列表以服务器 cursor 每页最多读取 2,000 条更早 raw 记录，避免“最近 2,000”冒充全量浏览。

## 风险与诚实声明

这 1,019 条是已有 partial covered inputs 的回放子集，不是完整两年三所全市场评估。covered ledger 虽已修复 next-open 风险分母、censoring 与事件序列描述问题，仍缺完整覆盖、匹配随机对照、资金组合模型和独立经济审计。不得从此关联推导 V1、YOLO、通知或实盘仓位调整。

历史图的信号后 K 线仍只用于回看，未输入 live scanner 或 YOLO；回放继续不通知。运行中的 live scanner、Bark cutover 和模型候选不因该数据库元数据联结而改变。
