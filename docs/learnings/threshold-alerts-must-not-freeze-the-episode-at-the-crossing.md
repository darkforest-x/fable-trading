# 阈值报警不能把整段行情冻结在越线那一刻

- **问题**：全市场插针报警（5 分钟内同方向 ≥25 个币）每条真实报警都显示正好 25 个。2026-11-09 05:55 那条：第 25 个币触及就写库，同一根 05:55 K 线里随后到的第 26 个、05:59 的第 27 个都没进来；冷却 30 分钟又不允许新报警，所以这些币哪条报警都不属于。补算后 8 条历史报警实际是 27–117 个（10-30 04:42 那条 117 个）。
- **死胡同**：只调显示（去掉推送文字前 12 个的截断、App 里 12 个一折叠）没用，因为数据本身停在 25。在 `on_touch` 回调里边收边改报警行也不够：重启时会漏掉，旧报警也没法补。另外，响应台原来的“新增”只数 `candle_ms > last_ms`，触发那根 K 线里晚到的币既不算在 25 里，也不算新增。
- **有效路径**：分成两份数据。触发时那一刻（`trigger_n`/`trigger_symbols`）只用于推送文字和记录。完整成员在读取时从触价簿查：同方向、live 首触、时间在 `[first_ms, last_ms+冷却)` 内，再并上触发集合。窗口关闭后再过 2 分钟（等最后几条延迟到达），冻结写入 `all_symbols`。API 的 `n`/`symbols` 直接改成完整值，下游的转发层（App 的 monitor_feed）不用重启就能拿到。关键判断是：冷却期就是这条报警的归属窗口。冷却期间触及的币不会再有别的报警认领。
- **通用规则**：带冷却的阈值报警，先问清楚“越线之后、冷却之内”的事件归谁。显示的数量要等于整段行情的成员数，越线时的数量单独保留。补全时从持久化的事件簿重算，不要依赖内存回调。这样重启和历史回填都能对上。
- **牵连**：`yoyo/monitor/spike_alert.py`（`_members`、`snapshot`、`SETTLE_MS`），`yoyo/monitor/static/spike-alarm.js`（`deskFacts` 的“新增”改成按触发集合判断），`yoyo/discipline_app/monitor_feed.py`（`MAX_ALARM_SYMBOLS`），原生 `CoinChips`。另见 [derive-alert-event-before-mutating-cooldown-state.md](derive-alert-event-before-mutating-cooldown-state.md)、[sqlite-wal-read-only-open-needs-the-shm-file.md](sqlite-wal-read-only-open-needs-the-shm-file.md)。
