# 突破+spike 双通道通知接入（2026-09-22）

## 结论

旧版联合信号只进入 `spike-lines-v1.sqlite3` 展示账本，没有通知队列，主服务也未启动 TG 发送线程。
本次按 Owner 要求接入联合信号 Bark 和既有 Telegram 入口“Yolo均线密集交易系统”（API 类型为 channel）。
监控服务升级至 1.16.0 并通过既有 LaunchAgent 重启。Bark 与 TG 各一条明确标注的测试通知均获得 HTTP 200 和有效回执；TG message_id 为 2094。

## 行为对照

| 项目 | 改动前 | 改动后 |
| --- | --- | --- |
| 突破+spike | 只显示 | Bark + TG 独立通知 |
| 周期 | 15m/30m/1H/4H 展示 | 同范围联合通知 |
| 原始 V9 / YOLO | Bark；TG 关闭 | 保留 |
| 普通趋势线突破 | 只显示 | 保留 |
| 历史与重复事件 | 不推送 | 不补发，首次插入与入队同事务 |
| 联合新鲜度 | UI 高周期可亮至一个周期 | UI 与发送均沿用30分钟门 |
| Bark 顶栏 | 任意累计未知即“异常” | 明示“2 条待核实”，不清历史回执 |

两通道启用时间为北京时间 **2026-09-22 12:55:49.816**，来自同步后的 OKX 时钟。
后续重启保留切点与原事件身份。发送读取同一时钟偏移；429 最多5次尝试，超时/不确定结果不自动重发。
消息包括联合信号名、合约/周期、突破来源、收盘价与时间、原 V9 时间、参考止损和 TradingView 入口。

## 数据与验证

- 改动前 lines 账本共有1686条：1576条普通突破、110条联合信号；保留事件ID及首次发现时间。
- 重启后首轮486合约/2430格、139.5秒、0错误、0新增；1686条身份列摘要与修改前相同，联合双通道队列均空，确认未补发历史。
- 原 Bark 有1036条已接受和2条未知，未知分别为 ANTHROPIC 15m 与 AT 30m 的历史请求；非联合信号队列。
- `.venv` 下127项定向 Python 检查通过，涵盖双通道首次入队、原子性/去重边界、切点、迟到/未来拦截、独立失败、限流、重启未知恢复、API回执、服务线程、V9回归、联合计算与层间导入门。
- 24项前端检查通过；原生浏览器核对“Bark 已启用 / TG 已启用”、旧事件“未推送”和顶栏累计未知数量。
- 扩大到旧 `test_service.py` 时9项失败；用 HEAD 的 service/server 模块复跑得到相同9项（旧V1预热、3周期、拒绝15m与缺V9 reset假设），不宣称全仓测试通过。
- 辅助代理还用系统 `python3` 执行旧监控集合得到124通过/57失败，包含旧fixture协议不匹配和该解释器缺httpx；未安装依赖或修改旧测试，验收使用仓库 `.venv`。
- 本次是通知功能验收，无交易收益实验；AUC、收益、胜率、置换检验和匹配随机入场不适用。等价反例控制为普通break、历史/切点前、重复、未来、过期和损坏事件均不得产生推送。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/monitor/test_joint_notifications.py tests/monitor/test_joint_notification_integration.py tests/monitor/test_v9_rollout.py tests/monitor/test_spike_lines.py tests/monitor/test_spike_lines_rsi.py tests/monitor/test_spike_lines_versions.py tests/monitor/test_api_startup_imports.py tests/boundaries/test_layer_imports.py -q
node --test tests/monitor/frontend_lines.test.cjs tests/monitor/frontend_cards.test.cjs
.venv/bin/python -m yoyo.monitor.manage status
curl -fsS http://127.0.0.1:8766/api/lines/status
```

部署使用 `.venv/bin/python -m yoyo.monitor.manage restart`。独立账本在线备份位于运行目录 `backups/spike-lines-before-notifications-1790052917.sqlite3`。
验收证据在 `output/qa/joint_notifications_20260922/`，包含修改前事件身份摘要、测试日志、旧源码失败对照、双通道真实测试回执与运行状态。

## 风险与诚实声明

服务端接受测试不等于手机已显示/已读。测试回执单独保留，不冒充真实行情信号或增加行情通知计数。
截至首次重启验收，联合队列尚无启用后新信号；未来实际信号的端到端延迟须看对应回执。
已有2条未知 Bark 历史回执保留；不为清除黄标而盲目重发。
保留工作区原有前端及其他任务修改；无训练、Pine、策略阈值、成本、ACTIVE、VPS写入或真实订单操作。

## 下一步

新联合事件自动进入两个通道。若 Owner 指定另一个 TG 群，再核对明确目标；不改变当前已配置入口。
