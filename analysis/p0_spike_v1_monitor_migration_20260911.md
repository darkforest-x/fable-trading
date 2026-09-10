# SPIKE V1 Mac monitor migration：信号浏览、冻结回放图与服务验收记录

## 结论

本轮把本机 `127.0.0.1:8766` 的监控协议收敛到冻结的 **SPIKE V1 长多 30m / 1H / 4H**：收盘后的原始 V1 启动和额外 YOLO 确认是独立事件、独立 Bark 阶段；历史回放只用于浏览，既不补发 Bark，也不展示未经修复的回测收益。已导入的 1,019 条 V1 回放信号能够按 event id 读取同源冻结 OHLC，并在前端明确标为历史复盘上下文。

本机服务可监听且关键 `/api/status` 超时根因已经通过 trace 确认并修复；当前首轮扫描仍在进行，不能把进程存活或部分完成误称为市场/模型就绪。原生浏览器交互验收仍被 CUA 的 `-10005 timeoutReached` 阻塞。没有任何盈利、模型泛化或 Bark 手机送达声明。

## 协议与数据边界

| 项目 | 当前行为 |
| --- | --- |
| 实时信号 | 原版 SPIKE V1、仅 long、30m / 1H / 4H 的已收盘 bar |
| 通知 | `source=live, confirmation=raw` 与随后 `source=live, confirmation=yolo` 分别去重、分别允许 Bark；Telegram 关闭 |
| 回放 | `source=replay, confirmation=raw`；无候选、无 outbox、无通知 |
| 回放图 | 只由已保存 event id 定位 `venue/symbol/timeframe/bar_open_ms`；按需读取冻结 gzip OHLC；后续 K 线标为历史复盘，未输入扫描器或 YOLO |
| 回测指标 | 已知 actual-fill、风险、censoring 和 drawdown 账本问题未在本轮改动；回放浏览不显示或推断 PnL |

回放导入命令已在此配置下执行一次：

```bash
python3 -m yoyo.monitor.replay_import \
  experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/covered_trade_ledger.csv.gz \
  --database "$HOME/Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3" \
  --import-id spike-v1-covered-ledger-20260911-unverified-partial
```

结果为 783 条新导入、236 条既有相同记录、11 条不属于 V1 30m/1H/4H 的记录跳过；运行库最终有 1,019 条 `replay/raw`。对这 1,019 条已导入记录按文件存在性检查，冻结 OHLC 覆盖为 1,019/1,019；这里的覆盖只说明可画出对应源 K 线，不说明交易完整性或策略表现。

历史遗留处理限定为源为空、协议为旧 IMACD 的 9,160 条错误 journal 行；它们已在有完整数据库备份后移除。迁移期间另发现 5 个可由 canonical V1 raw identity 替换的旧重复 id，已幂等清理；11 个无 canonical 替代的旧 id 未伪造替代记录。没有再次清库，也没有触及本次 live/replay 行。

## 回放图实际接口核验

清理 trace 后，对本机 API 读取一条已导入记录：

| 字段 | 实测 |
| --- | --- |
| event id | `120295e8085b51f7fc21ca36` |
| 来源 | Binance `ARCUSDT`，30m，`bar_open_ms=1788971400000` |
| `/api/replay/chart` | HTTP 200，105 根实际可用上下文 K 线 |
| 定位 | 返回 target K 线时间与该 event 的 `bar_open_ms` 完全相同 |
| target OHLC | 0.07349 / 0.07850 / 0.07297 / 0.07774 |
| 未来上下文 | `historical_review_only_not_model_input` |

该端点拒绝客户端提供 venue、symbol 或时间；只从数据库中的 event 读取。缺冻结文件会显式返回 `frozen_ohlc_missing`，不会改画当前 OKX 行情。

## HTTP 与扫描真实状态

诊断 trace 先排除了 `/api/status` 自身的数据库读取：它的 handler 可在约 16ms 到达。真正阻塞来自 `/api/markets`：旧实现会同步 JSON 解码 214 行完整 market payload，合计约 11.5MB（每行含 chart/events），并在共享解释器路径序列化响应。trace 记录的两次旧请求内耗时为 25.792s 与 19.595s。

提交 `181193f` 后，概览改读排除 `chart/events` 的摘要，选中图才按主键读取完整记录。受当前机器资源竞争影响，摘要仍需 SQLite 解析旧 JSON 输入，trace 内为 5.569s，实际响应约 6.323s，输出仅 85,842 bytes；但紧跟其后的 `/api/status` 成功返回，handler 约 488ms、请求总计 556ms，没有再次零字节超时。若未来必须进一步压低市场概览延迟，正确范围是写入时保存紧凑 summary 列或表，不能把 chart/events 再塞回概览。

最终 cleanup reload 后的单次实际 API 检查：

| 检查 | 结果 |
| --- | --- |
| `/api/health` | HTTP 200，`service_alive=true`，扫描写入当时为 `30 / 1434`、errors `0`；但 `market_ready=false`、`model_ready=false`、`ok=false` |
| `/api/status` | HTTP 200，非 stale 快照；紧邻读取的已缓存扫描快照为 `27 / 1434`、errors `0`，模型仍 `loading`。扫描写入与状态快照不同步，故不把两者混为一个精确完成计数 |
| `/api/markets` | HTTP 200，214 条摘要：30m 72、1H 71、4H 71，三周期这些已记录行均为 ready；没有 `chart/events` 字段 |
| Bark 状态 | `v1_bark_arm` 已设置；当前 V1 bark outbox 为 0，未发送测试消息 |

因此三周期已开始扫描但远未完成，当前自然 live raw 为 22 条、live YOLO 为 0 条。没有把待预热、扫描中或模型加载中表示为可通知状态。

## 实现与验证

本轮 monitor 提交链：`bf08b3c`、`8129087`、`603ea45`、`07e2db7`、`468fc5a`、`229b654`、`6b11464`、`a481311`、`1e5a286`、`7ce4971`、`df6c8f6`、`63e0b29`、`f35caf4`、`181193f`。其中 `f35caf4` 使 replay 图依赖在选卡时才导入，避免 pandas/NumPy/PyArrow 在 FastAPI 监听前阻塞；`181193f` 是上述 overview blocker 的最小修复。

本轮实际执行的定向代码验证：

```bash
python3 -m pytest -q \
  tests/monitor/test_market_summaries.py \
  tests/monitor/test_api_startup_imports.py \
  tests/monitor/test_replay_chart.py \
  tests/monitor/test_spike_v1_api_replay.py \
  tests/monitor/test_v1_status_snapshot.py
python3 -m py_compile yoyo/monitor/store.py yoyo/monitor/service.py yoyo/monitor/server.py
node --check yoyo/monitor/static/app.js
node --test tests/monitor/frontend_cards.test.cjs tests/monitor/frontend_theme.test.cjs
```

这组 Python 测试最后一次为 11 passed；此前回放/API 紧邻测试为 10 passed。它们不是两个独立测试集，不应相加。前端 Node 合同测试此前为 10 passed；本轮没有在 CUA 中重新获得浏览器页面：`cua.getApp("Google Chrome")` 返回 `-10005 timeoutReached`，按约束没有反复连接或伪造截图。静态/API 合同检查不替代桌面与窄屏交互验收。

## 运行环境与未完成项

受控 trace 已从 LaunchAgent 移除并以无 trace 的 cleanup reload 启动。启动仍受本机其它 ccxt-mcp/ChatGPT 作业竞争影响：诊断时系统 load 约 100、CPU idle 0%、swap 约 6.4GB；本任务无权停止其它会话。监听器可在约一分钟后建立，因而不能以冷启动瞬时健康响应设定性能承诺。

此前本任务暂停的 `yoyo.evaluation.spike_v1_twoyear_allmarkets fetch --venue binance`（PID 51814）已核对命令身份并 `SIGCONT` 恢复，进程从 `T+` 变为 `S+`。它是独立两年采集，不是本轮 monitor 结果；恢复不表示其评估账本问题已解决。

后续仍需在资源可用时完成：全 1,434 单元扫描、模型 ready、真实浏览器桌面/窄屏卡片选择与图表缩放验收。若浏览器自动化恢复，应验证 live/raw、live/yolo 与 replay 的分栏和标签，以及 replay 图中历史后续 K 线的说明。更广泛 V1 或 YOLO 有效性、收益和手机实际送达均不在本次功能验收范围。
