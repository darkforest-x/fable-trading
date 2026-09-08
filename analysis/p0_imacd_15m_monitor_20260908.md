# IMACD 新增 15 分钟监听：三周期已运行

本机 [Fable 信号台](http://127.0.0.1:8766) 已加入 **15m**，与原 1H/4H 一起扫描 OKX 全部在交易永续合约。仍然只在收盘确认出现当前可见 `focusRelease/tv_start` 启动标记时推送到 TG 和 Bark。前端支持三周期筛选、真实分钟坐标以及正确的 TradingView 周期跳转。

## 与上一版本对照

| 项目 | 之前 1.3.0 | 当前 1.4.0 |
|---|---|---|
| 监听周期 | 1H / 4H | 15m / 1H / 4H |
| 全市场覆盖 | 473 合约 × 2 = 946 窗口 | 473 合约 × 3 = 1,419 窗口 |
| 可见启动规则 | IMACD 34/9、近零 12 根、0.10 ATR | 沿用，15m 的 12 根对应 3 小时 |
| 高周期背景 | 1H→4H，4H→日线 | 新增 15m→1H；不作为启动过滤 |
| 新周期历史 | 不适用 | 独立启用时间，启用前和恰好启用时的收盘不补发 |
| TG / Bark | 原启用时间与独立回执 | 全部保留，新周期同时支持两通道 |
| 视图 | 六条细均线、双线副图 | 样式保留，分钟轴不再强写整点 |

遵守 [OKX 原生 K 线接口](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks) 的 `15m` 粒度和确认标识。未收盘数据不参与信号。高周期只使用本地 K 线开盘时已经收盘的数据；例如 10:45–11:00 的 15m，不使用刚在 11:00 收盘的 1H。

## 真实运行验收

部署源码 `137c672b0a054fac971cd56dd80592f4a6e5a113`，先提交再采集。验收脚本随后提交为 `bb4f781`，只读取派生收据，不读取原始行情或通知密钥。

| 检查 | 实际结果 |
|---|---|
| 首次 15m 启用时间 | 2026-09-08 09:14:55.850 北京时间 |
| 冷启动全扫描 | 1,419/1,419，627.8 秒 |
| 冷启动边界情况 | 8 个先加载的 15m 窗口跨过 09:15 收盘而暂时陈旧，保留真实异常记录 |
| 后续增量扫描 | 1,419/1,419，16.3 秒，09:27:36.459 完成 |
| 恢复后行情 | 错误 0、陈旧窗口 0、health.ok=true |
| 历史不足 | 78 个窗口仍在暖机，扫描覆盖不等于已经有足够历史产生信号 |
| 当前规则事件 | 1,126 条：15m 706、1H 282、4H 138；全部通过可见启动契约审计 |
| 历史与重复 | 重复身份 0，15m 启用前入队 0，原 70 条 TG 历史回执逐字段保持 |
| 当前通道 | TG 成功 1、Bark 服务接受 1；两者 pending/failed/unknown 均 0 |
| 首条真实新信号 | ROK-USDT-SWAP，15m，09:15 向上释放，收盘 433.99，近零 13 根 |

这条真实信号分别取得 TG 的 message_id 和 Bark 的服务器接受回执，没有发送启动、演示或链路测试消息。Bark 回执证明推送服务接受，不能证明手机已经展示或已读。1H/4H 两个渠道的旧 cutover 没有被重置。

## 验证方法与故障恢复

169 项 monitor 测试及 87 项相关仓库边界测试通过，共 **256 项**；JS 语法检查通过。17 项 before/after 收据检查全部通过，包括各周期全市场覆盖、源码哈希、旧回执完整、两个渠道启用时间、新周期历史拦截和重复身份。真实 Chrome 首轮 9 项界面检查通过，预热的 8 个陈旧窗口如实显示；后续恢复另记收据，7 项检查通过，包括 8 个窗口恢复、BTC 15m 图表和 ROK 双通道回执；不覆盖初始异常证据。

复核发现并修复一个漏发场景：行情暂时落后一根时，不能把仍新鲜、且晚于启用时间的新信号永久写为 history。现在仅暂缓这种候选的首次入库；恢复后仍新鲜才原子加入独立 TG/Bark 队列。合成反例验证 17 分钟恢复各通知一次、32 分钟恢复不通知，已有历史身份仍然不能升级补发。

## 数据与延迟范围

四条行情流为 15m、1H、4H、1Dutc，每条初始最多 720 根，仅驻留 RAM。15m 的前 340 根用于暖机，所以首次约有 4 天可产信号历史；7 天是事件保留上限，不是假称有完整 7 天的初始化信号。维持原 8 请求/秒、8 工作线程、扫描后等待 120 秒，scanner、TG/Bark 与前端的新鲜度门仍为 30 分钟。

473 合约完整冷启动理论请求预算约 12N/8=710 秒；实际 627.8 秒受新上市/历史不足合约减少分页影响。正常单个 15m 边界约 N/8=59 秒请求预算，日界四流约 237 秒，另加解析、网络、扫描等待和发送队列。本次增量仅补齐少数陈旧流，16.3 秒不是每个整点/日界的保证。进程内保留原始递推起点，RAM 和重算成本会随运行历史增长。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/monitor
.venv/bin/python -m pytest -q tests/boundaries/test_layer_imports.py tests/boundaries/test_yoyo_package_is_local.py tests/boundaries/test_no_cross_repository_bridges.py tests/boundaries/test_execution_bundle_only.py tests/boundaries/test_experiment_isolation.py
node --check yoyo/monitor/static/app.js
.venv/bin/python -m yoyo.monitor.manage status
curl -fsS http://127.0.0.1:8766/api/health
.venv/bin/python experiments/active/exp-imacd-15m-monitor-20260908-v1/verify.py
```

首次加载源码使用 `.venv/bin/python -m yoyo.monitor.manage restart`，不要因正常预热重复重启。重新采集用 `python -m yoyo.monitor.acceptance --label <新标签> --output <新路径>`，不得覆盖本轮 before/cold_start/after 收据。

## 风险与诚实声明

这是通知工程验收，不是盈利实验。沿用 Owner“任何时间段数据都可以使用”的授权，这是该配置第 1 次 live-observation 日期使用；没有训练、收益评分、参数搜索、下单、模型提升或 VPS 行情写入。AUC、置换 p、top-decile 收益、胜率和随机入场经济对照不适用；采用同样信号在不同启用/恢复时点的拒绝与成功对照、未来扰动和失败注入。

固定参数快照没有自动同步 TradingView 未来设置变化；有限暖机历史也不保证所有边界信号逐点一致。现有 LibreSSL 依赖提示保留，没有改动依赖。Mac 常驻服务持续工作，额外 Codex AI 巡检继续暂停。

## 后续

按现有三周期运行，只有新鲜、收盘确认的可见启动才通知。今后若要修改信号门槛或新增其他周期，再由 Owner 决定；无需为本次改动配置新的定时 AI 任务。
