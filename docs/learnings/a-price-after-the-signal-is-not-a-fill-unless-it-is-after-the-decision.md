## 问题

实时 tip 行在模型尚未完成裁决时，就用 `signal_time + 15m` 和 signal close 生成代理 entry；下一脉冲再把历史 next-open 回填为成交。若裁决发生在该 open 之后，后续障碍路径包含决策前行情，却会被看板当作实际成交收益。

## 失败尝试

用 `maker_filled` 的空值充当“稍后确认”的哨兵，并在未来扫描中回填 entry。它只能证明某根 K 线后来存在，不能证明当时已做出决策、发出请求或获得成交；字段补全反而把时间倒置隐藏成一条完整交易。

## 有效做法

拆开 signal close、candidate detection、decision、entry request 和 fill。signal close 只写 `reference_px`；无 fill 时 entry、fill 和 actual PnL 全为空。paper 只选择严格晚于 `decision_at` 的第一根 future open；broker 只接受 ledger 的显式 `fill_at`/`fill_px`。障碍从该 fill 的安全 OHLC 起点开始，统计只计显式 filled 且 actual PnL 完整的行。

## 可推广原则

“价格在信号之后出现”不等于“价格在决策之后可成交”。任何实际收益证据都必须能串起 `decision_at <= entry_requested_at <= fill_at`；链条缺一环时，只能记录观察或研究结果，不能推进成交计数。

## 本次涉及

- `src/judgment/execution_timeline.py`
- `src/judgment/forward_scan.py`
- `src/judgment/forward_records.py`
- `src/execution/executor.py`
- `src/webapp/forward_payloads.py`
- `src/webapp/data_hub.py`
- `src/webapp/status_strip.py`
- `tests/test_execution_timeline.py`
- `tests/test_tip_realtime_path.py`

