# Telegram 简讯与信号图验收 · 2026-09-08

已在这台 Mac 上启用监控服务 1.5.0：TG 使用三行摘要、信号图和 TradingView 按钮。升级后 7 条真实新信号均取得图片发送成功回执；无渲染降级、失败、未知发送或历史重放。473 个合约的 15m / 1H / 4H 扫描全部恢复，1419 个组合，错误和过期状态均为 0。

## 用户看到的内容

```text
RE-USDT · 1H · 🟢 向上启动
收盘 0.46567 · 蓄势 34 根
09-08 10:00 北京时间 · 已确认
```

图片保留真实 K 线、六条细均线、目标启动箭头和价格、IMACD 蓝橙双线、零轴及已确认蓄势区。长链接放进“打开 TradingView”按钮。没有修改 Pine、信号阈值、高周期许可口径或 Bark 格式。

[实际发送的 RE 1H 图片](../../experiments/active/exp-imacd-telegram-chart-20260908-v1/results/re-1h-sent.png)；[PNUT 本地历史预览](../../experiments/active/exp-imacd-telegram-chart-20260908-v1/results/pnut-preview.png)。PNUT 预览没有作为新消息发送。

## 前后对照

| 项目 | 升级前 1.4.0 | 升级后 1.5.0 |
|---|---|---|
| TG 内容 | 多项背景说明与长链接 | 三行摘要与按钮 |
| 行情图片 | 无 | 1080×1080，最多 120 根，截止信号当根 |
| 当前协议 TG 成功数 | 4 | 11，其中新增 7 张图片 |
| 旧协议 TG 成功数 | 70 | 70，旧回执逐项不变 |
| Bark 当前成功数 | 4 | 11，独立发送 |
| 渲染失败降级数 | 不适用 | 0 |
| TG/Bark 待发送、失败、未知 | 全为 0 | 全为 0 |
| 完整扫描组合数 | 1419 | 1419 |
| 规则、启用时间、新鲜度 | v3 可见释放 / 30 分钟 | 全部不变 |

300 项软件检查通过，包括 213 项监控测试和 87 项仓库边界检查。另有 35 项真实服务收据检查全部通过。独立复核对配图相关测试得到 42 passed；该集合已包含在 300 项中，不重复相加。一条既有 LibreSSL 环境警告不影响本次检查。

## 数据与时序

范围为 OKX 473 个 live SWAP，每周期 473 个组合；本次验收时 1138 个当前协议历史信号，其中 15m 714、1H 286、4H 138。78 个新合约周期因历史不足继续显式预热，不把它们算成已达到指标计算要求。

09:54:37 左右启动新版。首次全市场加载耗时 630.45 秒，跨 10:00 收盘造成 16 个组合暂缺最新确认 K 线；这是记录在 cold_start.json 中的真实退化，未反复重启。10:07:07 启动的增量扫描耗时 77.14 秒，1419/1419 完成，错误与 stale 均为 0。验收收据包含完整时间戳及全部旧、新回执。

RE 1H 例子的信号开盘为 09-08 09:00、确认收盘为 10:00，价位 0.46567，蓄势 34 根。发送图片 193968 字节，从私有运行库复制原始 PNG 并重新核对 SHA-256，TG message_id 1860。RKLB 1H 紧随其后获得回执 1861；最终验收共有 7 张新图。回执说明 Telegram 接受了图片消息，不等于手机展示或阅读。

## 验证方法与限制

渲染器不重新抓行情、不插值：扫描器先截断，渲染器再校验目标时间、连续 K 线、精确收盘价与 md/sb。向未来追加或扰动行情必须得到逐字节相同 PNG；目标缺失、重复、价格或指标不一致必须拒绝。图片与首次事件和两条渠道队列同事务提交；并发重复、事务失败回滚、重启及旧数据库迁移均有测试。

渲染在 HTTP 前失败才使用简短文字。429 重试同一持久化 PNG；上传超时、5xx、缺少图片回执均记 unknown，不另外发送文字，以免重复。真实验收核对所有图片哈希、旧回执、三个周期与两渠道启用时刻、运行代码哈希，未发现漂移。

这是通知工程验收，没有训练、调参、收益评估或下单。沿用 Owner“任何时间段数据都可以使用”的授权，这是该展示配置第 1 次 live-observation 使用。val AUC、收益、胜率、置换 p 和随机入场基线不适用；同等严格的对照为未来扰动不变性、错误目标拒绝、故障注入及旧回执不变性。图片和成功消息不证明交易系统盈利。有限初始化长度与 TradingView 设置快照的既有限制不变。原始 OHLC 数组仅在 RAM，派生图片随新通知保存在私有 SQLite，磁盘占用随数量增长。

## 复现与交付

实现先提交于 041d2a3；预览生成器先提交于 84c8636；收据检查器先提交于 26d9daa，并于 59f572e 补充无发送中队列的验收条件。运行源提交为 84c86365f438e5c8b0a773d5a502141d2281f540，所有运行模块与验收时磁盘源码哈希相同。只读 API 的行情随时间变化，因此原始预览必须在其事件仍处于缓存内、价格和指标精确匹配时才能重建，不能宣称任意未来时间逐字节再现；本次 PNG 与元数据已保存。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/monitor tests/boundaries/test_layer_imports.py tests/boundaries/test_yoyo_package_is_local.py tests/boundaries/test_no_cross_repository_bridges.py tests/boundaries/test_execution_bundle_only.py tests/boundaries/test_experiment_isolation.py
.venv/bin/python experiments/active/exp-imacd-telegram-chart-20260908-v1/preview.py --event-id 10f8f3c5fd138819d2050d32 --output /tmp/fable-pnut-new-preview.png
.venv/bin/python -m yoyo.monitor.acceptance --label recheck --output /tmp/fable-telegram-chart-recheck.json
.venv/bin/python experiments/active/exp-imacd-telegram-chart-20260908-v1/verify.py --before experiments/active/exp-imacd-telegram-chart-20260908-v1/results/before.json --after experiments/active/exp-imacd-telegram-chart-20260908-v1/results/after.json --output /tmp/fable-telegram-chart-verification.json
python3 scripts/md_to_html.py analysis/p0_imacd_telegram_chart_20260908.md --out-dir analysis/html
```

部署时执行了一次 `.venv/bin/python -m yoyo.monitor.manage restart`。复核已有结果不需要重启或发送测试消息。无需 Owner 再次操作；下一条符合条件的新信号会继续采用该格式。

接口来源：[Telegram sendPhoto 官方契约](https://core.telegram.org/bots/api#sendphoto)。内部证据：[35 项收据检查](../../experiments/active/exp-imacd-telegram-chart-20260908-v1/results/verification.json)、[升级后状态](../../experiments/active/exp-imacd-telegram-chart-20260908-v1/results/after.json)。
