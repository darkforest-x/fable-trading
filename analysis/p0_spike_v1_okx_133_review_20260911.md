# SPIKE Burst V1 · OKX 133 笔逐笔图册（2026-09-11）

## 范围与结论

本交付是历史图形复盘数据，不训练、不调参、不重跑收益，也不触及监控、通知、订单或
ACTIVE。名单固定为北京时间 `[2026-08-27 00:00, 2026-09-11 00:00)` 内，OKX、
`spike_burst_v1`、多头、30m/1H/4H 的 133 个唯一信号：30m 75、1H 42、4H 16。

`data/manifest.json` 现有 133 条可用图，没有占位图。127 条来自不可变 covered ledger；
6 条来自预先枚举的只读 live journal（ISRG-USDT-SWAP、SOL-USD-SWAP、UNI-USD-SWAP、
ON-USDT-SWAP、SOXS-USDT-SWAP 的 30m，及 STABLE-USDT-SWAP 的 1H）。live 六条没有
covered-trade 成交账本，因此 `entry` 与 `exit` 均为 `null`，而不是未知成交被伪造为零。

## 冻结与数据契约

账本为
`exp-spike-v1-twoyear-allmarkets-20260911-v1/results/immutable_replay_ledger/covered_trade_ledger.b15b69…3578.csv.gz`，
其文件 SHA-256 已按文件名验证为
`b15b69b8864e5eb651f2417fc6681ea688b5fbb95dacd15af1284b42744e3578`。每一条账本图还要求
既有 replay event 的 `covered_ledger.evidence.frozen_ohlc_sha256` 与实际冻结 OHLC 文件相同；
无证据或不符会拒绝出图。

每张 JSON 保留真实 UTC 毫秒时钟并提供 BJT 展示时间。`signal.time_ms` 是收盘信号时刻，
`signal.bar_open_ms` 是 K 线左端；`entry.time_ms` / `exit.time_ms` 是账本已知的 UTC 毫秒，
不会随图窗裁剪改变。蜡烛为真实 OHLCV 与 SMA/EMA 20、60、120、MD、SB。指标总是在完整连续
段上先计算，再切图；后续 K 线都标记为 `historical_review_only_not_model_input`。

初始止损是静态展示：live 直接采用事件 payload 的 `initial_stop`；账本多头为冻结
`signal_close - reference_signal_risk`，并记录来源。没有输出追踪止损，因此不会把 SL 后的峰值
伪装成可实现收益。JSON 的全局图窗从信号前最多 100 根延至实际退出后 72 根，若无退出结论则至冻结 OHLC
截止，上限 1500 根。浏览器默认“信号近景”为信号前 100 / 后 144 根；若图总长超过 245 根，截图使用
查看器的“全局”视图，避免裁掉已知账本位置或冻结截止。

## 数据统计与覆盖

| 周期 | 信号/图数 | 蜡烛数 | 每图蜡烛范围 |
| --- | ---: | ---: | ---: |
| 30m | 75 | 18,112 | 105–656 |
| 1H | 42 | 8,668 | 111–330 |
| 4H | 16 | 1,989 | 102–175 |
| 合计 | 133 | 28,769 | — |

44 张图在冻结 OHLC 的右端不足默认 144 根后续复盘上下文；这是明确的 `coverage.insufficient_after`
状态，不补造 K 线。所有图均有完整信号前 100 根。最终 manifest SHA-256 为
`254832d132b9e180c44d4abcd0b3ea52af8045d9c46b6204145bcb8108cc9e31`。

0G-USDT-SWAP 4H 是唯一恢复点：冻结文件的 `time` 列全空，但 `Unnamed: 0` 是 16,923 个完整、
严格递增、无重复的 30 分钟 UTC 时间。读取器只允许这一种无歧义形式回退到该现有索引列，且不改写
CSV、不换数据源、冻结 SHA 不变。初次构建的其余 132 张 JSON 与补建后逐字节相同，见
`data/repair_0g_receipt.json`。

## 质量检查与零假设

本次是非方向性数据/渲染交付，AUC、置换检验、top-decile 毛/净收益、胜率、单特征基线和匹配随机
对照均不适用：没有生成任何预测、交易选择或收益重算。等价的零假设是“图册可以把错币种、错周期、
错时区、错收盘价、重复事件、非连续 OHLC，或裁剪后不存在的账本退出伪装成可审图”。实际检查为：

- 133 个 `(venue, symbol, timeframe_min, signal_close_ms, side)` 身份全唯一，且精确为 75/42/16。
- 每条 signal bar 与 `bar_open + timeframe == signal.time_ms` 一致；信号收盘价和该 bar 的真实 close 一致。
- 每图 OHLC 时间严格递增且步长等于周期，几何有效；初始 SL 正且低于多头信号价。
- 102 条 covered ledger 已有结束结论；25 条 `exit.reason=censored` 仅表示冻结数据在期末截止，
  不是退出或成交结论；6 条 live 没有交易账本。所有 133 图不含动态 `active_stop` 字段。
- 132 张原图的前后 SHA 全相同；0G 的日期来源、哈希与无歧义检查均被单独记录。

实际命令：

```bash
git show e09ca2d:yoyo/evaluation/spike_v1_review_data.py >/dev/null
git show 1aade37:yoyo/evaluation/spike_v1_review_data.py >/dev/null
python3 -m pytest -q tests/test_spike_v1_review_data.py tests/monitor/test_replay_chart.py
python3 -m yoyo.evaluation.spike_v1_review_data
python3 -m yoyo.evaluation.spike_v1_review_data --only-missing
python3 yoyo/evaluation/static/spike_v1_review/build_site.py \
  --data-dir experiments/active/exp-spike-v1-okx-133-review-20260911/data \
  --out-dir experiments/active/exp-spike-v1-okx-133-review-20260911/site
# In a separate terminal; keep this process running while using the viewer.
python3 -m http.server 8767 \
  --directory experiments/active/exp-spike-v1-okx-133-review-20260911/site
# In the first terminal after the local site is serving:
python3 -m yoyo.evaluation.spike_v1_review_render --clean-output
python3 -m yoyo.evaluation.spike_v1_review_render --repair-global-crops
python3 scripts/md_to_html.py analysis/p0_spike_v1_okx_133_review_20260911.md --out-dir analysis/html
```

构建器初版提交为 `e09ca2d`；冻结 CSV 无歧义索引时间读取及只补缺失机制提交为
`1aade37`。最终 `data/manifest.json` SHA-256 为
`254832d132b9e180c44d4abcd0b3ea52af8045d9c46b6204145bcb8108cc9e31`。

浏览器逐笔渲染使用最终静态站点 `http://localhost:8767/`、官方 Lightweight Charts 和独立
headless Playwright 会话。`rendered/render_receipt.json` 记录 133/133 PNG、0 失败、每条请求 ID
与 `#charts.dataset.renderedRecordId` 的相等性、三块主画布非空、浏览器错误及 PNG SHA。最终收据
SHA-256 为 `9d66263cd9f1912dd411693cae2a5a536ddf850daf5a88250b4464749c6d4418`；图包
`rendered/spike-v1-okx-133-rendered.zip` 的 SHA-256 为
`81175b9212c85706337df21f488be4d78dd92208aff106524f2126b740c07eda`。其中 34 张
`candles > 245` 使用 `view=global` 重截，99 张 `view=signal` 已覆盖它们的全部冻结 K 线；三张
时间框架 contact sheet 与 README 均在 ZIP 内。#44 USELESS 是全局重截样例，已显示 09/06 18:00 BJT
的历史账本 protective_stop；#105 SOPH 是通过相同硬门禁的近景样例。

渲染器初版提交为 `69a75a0`，rendered-record-ID 门禁、进度收据、归档 README 与会话关闭提交为
`6829164`，全局重截功能提交为 `d5a7aa8`。最终 UI 收据为
`qa/ui_acceptance.json`（提交 `4c5bb37`）；root 的独立 PNG SHA、渲染 ID、34 张全局覆盖和 ZIP
CRC 审计记录在 `qa/root_acceptance.json` 的 `final_render_audit`。

数据相关测试为 `10 passed`；图册构建契约为 `4 passed`，Node UI 语义契约为 `5 passed`。
其中 `urllib3` 对本机 LibreSSL 的兼容性 warning 不影响测试结论。

## 风险与诚实声明

图册的未来 K 线只供事后视觉复盘，不能作为实时信号、执行依据或收益证据。账本 entry（次开盘）与
exit 都是已有历史回测账本字段，不是实盘成交，也不是本次重新模拟；102 条有已结束结论，25 条期末
`censored` 没有退出结论，live 六条没有 entry/exit。此次读取覆盖既有 holdout 时间是 owner 明确授权的
同一冻结图册配置**第 1 次**展示性历史复盘读取；其后的 JSON 校验、静态服务和 PNG 渲染只消费这份
冻结 JSON，不用于参数选择、训练、策略评分或收益结论。OHLC 上下文可以晚于信号名单的 09/11 00:00 BJT
筛选截止，但不重取或延长冻结数据。

## 下一步

静态 viewer 可消费 `experiments/active/exp-spike-v1-okx-133-review-20260911/data/manifest.json` 的
`records[].chart_path`，逐笔显示初始 SL、已知 entry/exit 和复盘 K 线。任何把图册用于新阈值、训练、
实盘或收益比较的工作都需要新的 owner 决策。
