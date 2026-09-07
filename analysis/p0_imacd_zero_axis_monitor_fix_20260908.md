# IMACD 监控纠正：只推送刚离开零轴的第一根

Owner 纠正：“监控不对啊，出信号了是指刚突破零轴啊”。此次将前后端与 Telegram 统一为 **前根 IMACD 主线等于 0，本根首次转正或转负**。现有页面仍为 [Fable 信号台](http://127.0.0.1:8766)。均线密集和高周期保留为背景提示，信号在 K 线收盘后确认。

## 错误在哪里

原监控把三种事件一起通知，错误地把“指标上有一个事件”当成用户要的“刚突破零轴”。软件测试证明了扫描与送达，却没有证明通知事件符合用户定义。

| 旧通知类别 | 已发送数量 | 与首次离轴的关系 |
|---|---:|---|
| 原密集入场 entry | 25 | 要求前根为零，但额外密集条件可能漏掉其他离轴首根 |
| 原趋势结束 exit | 32 | 回到零轴或反向，不是用户所指的横盘后离轴启动 |
| 原近零范围释放 release | 13 | 12 条前根已非零；仅 1 条恰好与首次离轴重合 |
| 合计 | 70 | 44 条不符合首次离轴形态，不能把送达率当信号正确率 |

旧记录逐条审计见本轮 `results/old_notification_audit.json`。原70条回执保留，不删除，不重新命名为新版本信号。

## 统一后的判断

```text
多头启动：已收盘且预热完成，md[1] == 0 且 md > 0
空头启动：已收盘且预热完成，md[1] == 0 且 md < 0
```

md 继续保持正数或负数时不重复发；回到零轴不发；单纯与信号线交叉不发；本根直接由正变负或由负变正、前根没有停留在零轴，也不归入此“零轴横盘后启动”。最短允许前一根在零轴，不擅自加入12根等待条件。通知显示实际连续零轴根数，便于观察长横盘。

任何微小非零值都按原 IMACD 算式处理，不等它越过 `0.1 ATR`。此次未修改34/9参数、六均线计算、高周期因果时间边界或30分钟新鲜度。高周期背景仍是 1H→4H、4H→UTC日线，在本周期开盘时已经可知的已收盘值。没有强制共振或密集过滤。

| 路径 | 旧版 | 修正版 |
|---|---|---|
| 监控事件 | entry / release / exit 混合 | 仅 zero_breakout |
| TG入队与发送 | 按事件类型及新鲜度 | 新鲜度＋首次离轴契约＋协议首次生效点 |
| 默认API与主列表 | 四种事件混排 | 仅新协议的零轴启动 |
| 24小时数量 | entry＋release | 仅 zero_breakout |
| 主图箭头 | 多种事件箭头 | 仅零轴启动箭头，带原收盘点位 |
| 均线、高周期 | 密集是原entry门槛，HTF标注 | 都是背景标注 |
| 回执统计 | 旧所有事件消息累计 | 当前协议消息与 historical_sent 分开 |

副图保留零轴与双线，不画柱状图。旧密集入场、近零释放、趋势结束及影线回踩保留为内部图表观察，不进入主信号通知。

## 升级与因果边界

代码版本 `7786f77`；运行协议 `imacd-zero-axis-monitor-v2`，软件版本1.1.0。首次启用先同步OKX时钟，再持久化生效时间；本机慢几秒不能把升级前的整点收盘错当成升级后的新信号。发送线程在时钟与策略就绪前等待，单独发送器缺少激活元数据也不发送。

切换前已发生的零轴启动可以重算并在页面展示，但不补发TG。旧pending消息标记skipped，旧sent回执保持原样；不确定的发送仍保留unknown，避免盲目重发。协议生效点不因重启变动，既有事件身份去重仍生效。

## 验证与对照

**171 项测试通过：84 项监控测试、87 项仓库边界测试。** 只有既有 urllib3 / LibreSSL 兼容提示，未安装或改变依赖。

- 正负方向、`±1e-14` 微小离轴均在首根触发；不等待ATR。
- 连续非零、回零、无零根的直接反向、仅信号线交叉均不误触发。
- 密集为假、高周期不允许时，首次离轴仍保留，并标注背景。
- 修改后续OHLC和不可见高周期，不改变已确认历史事件。
- 实际扫描函数处理合成“长期零轴后跳离”行情，只有zero_breakout入队；并发/重复/重启不重复发送。
- 旧kind/旧协议/错误前值/回零/未收盘/方向不符均不能经发送端进入TG；激活前历史不发送。
- 旧回执保留，主API及24h计数不把旧数据换标签后冒充新信号。
- 本机时钟慢于交易所的整点反例，校准后的生效点阻止升级前历史通知。

真实前端抽查158行全部为零轴启动。BTC 1H样本显示连续零轴9根、空头点位78,794.70；ACT 1H样本只有零轴1根、均线未密集仍显示启动，价格0.011103完整。1H/4H、搜索、多空筛选与零轴双线检查通过，无控制台错误。截图保存在当前任务工具消息中，未宣称另有本地PNG。

## 操作与复现

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/monitor tests/boundaries/test_layer_imports.py tests/boundaries/test_yoyo_package_is_local.py tests/boundaries/test_no_cross_repository_bridges.py tests/boundaries/test_execution_bundle_only.py tests/boundaries/test_experiment_isolation.py
.venv/bin/python -m yoyo.monitor.manage status
curl -fsS http://127.0.0.1:8766/api/health
curl -fsS 'http://127.0.0.1:8766/api/signals?limit=5&kind=zero_breakout'
```

需要重启时使用 `.venv/bin/python -m yoyo.monitor.manage restart`，不要开第二个进程。记录新的手动快照时使用新文件名，保留本轮前后证据：

```bash
.venv/bin/python -m yoyo.monitor.acceptance --label manual-zero-axis-check --output experiments/active/exp-imacd-zero-axis-monitor-20260908-v2/results/manual_check.json
python3 scripts/md_to_html.py analysis/p0_imacd_zero_axis_monitor_fix_20260908.md --out-dir analysis/html
```

小时巡检已更新为核对新协议、zero_breakout主列表和当前协议TG队列。扫描仍是完成一轮后等待120秒，Mac需保持开机登录、接电联网；初次全市场预热需数分钟。

## 风险与诚实声明

本次是通知定义纠正，无收益回测、训练、调参、模型切换或下单。AUC、置换p值、top-decile净收益、胜率及匹配随机入场均不适用；上述否定反例、未来扰动、真实API和队列故障注入是对应的软件验证，不冒充盈利证据。

原始K线只在RAM；有限720根历史种子、至少bar index340预热的约束不变，新合约或有缺口的窗口可能继续预热。显示价格为信号收盘价，不是成交价。通知在收盘后扫描确认，不能把盘中尚未收盘的触碰称为已确认信号。

本修正配置是第1次在既有全日期明确授权内作实时holdout日期观察，不评价后续收益。生产与训练资格保持false。旧版本70条回执不能证明新协议已发送新消息；新回执与真实全市场扫描情况以下面的最终验收为准。

## 最终全市场验收

| 项目 | 实测结果 |
|---|---|
| 新协议生效 | 2026-09-08 07:37:09 北京时间，使用校准后的OKX时钟 |
| 全市场扫描 | 473个合约 × 1H/4H，946/946完成，0错误；耗时447.56秒 |
| 扫描完成时间 | 2026-09-08 07:44:33 北京时间 |
| 行情健康 | ok=true；陈旧窗口0；历史不足仍预热74窗口 |
| 新口径历史启动 | 1,413条；逐条核对previous_md==0、md非零、方向相符、前零根数≥1，违规0 |
| 默认API与24h统计 | 仅新协议zero_breakout；旧exit类别请求返回400 |
| 新协议TG | sent=0、pending=0、failed=0、unknown=0；升级后尚未出现下一根合格收盘信号 |
| 历史回执 | 旧70条逐条保持相同；未把历史重算补发 |
| 运行版本 | 7786f77；所有启动Python源码哈希与交付磁盘一致 |

新规则已常驻运行，下一条符合条件的收盘信号会按零轴启动口径处理。没有为了验收制造交易信号或额外发送测试消息。当前全部新协议历史事件都早于生效点；不把这些历史记录当作新协议实时推送成功证据。

[修正前快照](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-zero-axis-monitor-20260908-v2/results/before.json) · [旧通知语义审计](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-zero-axis-monitor-20260908-v2/results/old_notification_audit.json) · [全市场修正后快照](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-zero-axis-monitor-20260908-v2/results/after.json) · [验证记录](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-zero-axis-monitor-20260908-v2/results/verification.json)。旧交付记录与本轮记录分开保留。
