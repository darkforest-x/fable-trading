# IMACD · Mac 全市场监控交付记录

这台 Mac 已完成 OKX 全市场永续合约的首轮真实扫描，并把真实指标事件送达 Telegram。前后端入口为 **[打开 Fable 信号台](http://127.0.0.1:8766)**。首轮覆盖 **473 个合约 × 1H / 4H，共 946 个监控组合**，扫描错误为 **0**；已有 **9 条 Telegram 消息取得真实回执**。页面展示信号、点位、蓄势结构、K 线、IMACD 双线与运行状态。

**最终后端已完成重启、全市场预热和连续增量扫描。** 北京时间 02:42 的全市场预热扫描耗时 456.8 秒，随后两轮增量扫描为 15.1 秒和 1.7 秒，均为 946 / 946、0 错误。02:46:26 的最终快照中 `service_alive`、`market_ready`、`ok` 全部为 true，9 条 TG 回执与重启前完全相同，重复信号身份为 0。

这是一项指标监控软件交付，不是收益回测。通知价格均为已收盘信号 K 线的收盘价；服务不读取交易所交易密钥、不下单，也不把指标趋势状态当作账户持仓。

## 已有运行证据

原始快照：[initial_acceptance.json](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-mac-monitor-20260908-v1/results/initial_acceptance.json)。采集时间为 2026-09-08 02:30:53 北京时间；其中接口时间经过交易所时钟校准，与本机采集时间可能相差数秒。

| 项目 | 首次运行实测 | 含义与范围 |
|---|---:|---|
| 在交易永续合约 | 473 个 | OKX `SWAP`、`state=live`；458 个 USDT 合约及 15 个币本位合约 |
| 监控组合 | 946 / 946 | 每个合约分别处理 1H、4H |
| 首轮完整扫描 | 447.4 秒 | 初始进程日志中的实际值，约 7 分 27 秒；不是预估值 |
| 随后一次增量扫描 | 14.98 秒 | 上述 JSON 中保存的已完成扫描 |
| 扫描错误 / 陈旧市场状态 | 0 / 0 | 首次验收快照时点 |
| 仍在预热 | 74 个组合 | 历史不足等原因尚未达到指标资格；不是 74 个合约，也不伪造信号补齐 |
| 已保存指标事件 | 3,855 条 | 包括近期回填与实时事件；不是成交笔数 |
| 相同信号身份重复组 | 0 组 | SQLite 中按交易对、周期、收盘时间、事件类型、方向检查 |
| Telegram 已送达 | 9 条 | 每条均有 Bot API `message_id`，不是仅凭发起 HTTP 请求判成功 |
| Telegram 等待 / 失败 / 不确定 | 0 / 0 / 0 | 快照时点的 outbox 状态 |
| 最近 24 小时启动与释放 | 150 条 | 按事件计数；同根释放与启动可以是两类不同事件 |

初次运行的状态分布如下。它表示指标观察状态，不表示真实仓位；`ready` 是近零蓄势已达到显示资格。

| 状态 | 组合数 |
|---|---:|
| 近零蓄势已满足资格 `ready` | 50 |
| 正在累计近零根数 `building` | 77 |
| 原系统多头趋势 `long` | 494 |
| 原系统空头趋势 `short` | 105 |
| 中性观察 `neutral` | 146 |
| 数据预热 `loading` | 74 |
| 合计 | 946 |

每条 1H、4H、UTC 日线数据流初始最多获取 720 根已确认 K 线。原始 K 线仅在内存中，不生成本地行情文件；因此本报告不把请求上限当作实际获得的 K 线总数。事件初始化回看最近 7 天，旧事件只展示，不补发过期 Telegram。

## 前后变化

| 能力 | 交付前：TradingView Pine 指标 | 交付后：本机前后端监控 |
|---|---|---|
| 市场覆盖 | 在所打开图表上查看指标 | 按公开在交易合约列表扫描全部永续合约，每小时刷新列表 |
| 周期 | 手动切换图表周期 | 同时监控 1H、4H，并获取确认过的高周期背景 |
| 信号浏览 | 图表内标记 | 集中信号表、交易对与周期筛选、方向与事件类型筛选、详情图 |
| 信号价格 | Pine 标记收盘价 | 列表、详情、TG 同时显示信号收盘价 |
| 视觉重点 | 已有近零蓄势与释放样式 | 简洁深色页面；主图 K 线与细均线；副图保留零轴和双线，不使用柱状图 |
| Telegram | 没有本次全市场后台扫描与持久消息队列 | 实时合格事件进入 SQLite outbox，带回执、重试策略与过期限制 |
| 持续运行 | 依赖图表与相应警报配置 | Mac LaunchAgent 登录后启动，异常退出后拉起；独立于当前 Codex 对话 |
| 故障呈现 | 不提供本服务的整体扫描状态 | 展示预热、陈旧行情、扫描进度、失败或不确定通知；进程存活与行情就绪分开 |
| 重启记录 | 无本次事件日志 | 保留已知事件与发送记录，避免同一身份反复推送 |

保存到 TradingView 的现有 Pine 样式未在本轮改动。此前黄金研究中的日线 F01 退出规则也没有被混入这次监控默认值。

## 四种事件的精确定义

| 事件 | 确认条件 | 默认 Telegram | 解释 |
|---|---|---|---|
| 蓄势释放 `release` | IMACD 主线与信号线连续至少 12 根处于近零区域，随后主线越过该段冻结阈值 | 发送 | 独立视觉观察，不自动等于原系统入场 |
| 系统启动 `entry` | IMACD 34/9；至少一根精确零轴后离开零轴；前 12 根满足六均线密集条件 | 发送 | 当前 Pine V2.2 的默认“密集启动”规则 |
| 趋势结束 `exit` | 原系统方向上的主线回到零轴或反向 | 发送 | 表示指标趋势结束，不是账户平仓或成交回执 |
| 蓄势影线回踩 `retest` | 已确认蓄势区内，前根收盘在线同侧；本根影线触及 SMA20，实体守在线外，收盘回到原侧 | 页面展示 | 仅高亮关键 K 线；不改变系统入场，不默认推送 TG |

六均线为 SMA / EMA 20、60、120。密集形成条件是**启动当根之前** 12 根的平均均线带宽 / ATR 不超过 3，15 对均线的交织次数至少为 2；保留 34 根密集记忆作为诊断信息。默认密集启动使用当下形成条件，高周期许可不参与强制过滤。

近零阈值在资格达成前为 `0.10 × ATR[1]`；达到第 12 根后冻结该段阈值，避免 ATR 收缩被误认为主线释放。高亮从资格达成当根开始，不事后补画前 11 根。若仅信号线离开阈值、主线没有给出方向，则结束该段，不凭空生成方向释放。释放当根不并入之前的整理区，也不当作区内回踩。

1H 读取 4H 背景，4H 读取 `1Dutc` 背景。高周期必须在本周期开盘时已经收盘并满足独立预热要求；缺失或陈旧时标记未知。“高周期许可”是主线同向、动量同向或主线精确零轴之一，因此它也不等于严格的两周期主线同向。页面和 TG 将许可作为背景信息，不能据此误读为启用了“密集＋共振”过滤。

## 时效、常驻与通知可靠性

全局公开行情请求上限为每秒 8 次，共 8 个工作线程。每轮扫描结束后等待 120 秒再启动下一轮；不是保证每 120 秒一定完成全市场扫描。页面每 15 秒刷新显示。初次预热较慢，正常增量扫描可复用未变化的已收盘观察结果。

新鲜度统一为收盘后 **30 分钟**：扫描入队检查一次，Telegram 发送前再检查一次，前端新鲜标记使用同一常量。超过时限的事件保留为历史，不以刚发生的信号通知。计划延迟预算为检查等待最多 120 秒、正常全市场增量预算 180 秒、网络重试余量 120 秒，约 7 分钟；首轮实测 447.4 秒单独记录，不能作为每次扫描的固定承诺。

交易对、周期、事件收盘时间、类型、方向和协议版本共同定义不可变事件身份。SQLite 在同一事务中插入事件及待发送记录；重复扫描、并发插入与重启不会重复入队同一身份。

Telegram 的 429 响应按 `retry_after` 延后处理。明确拒绝标记失败；超时、异常响应、5xx、或进程在发送中被打断标记 **unknown / 送达不确定**，不自动重发。这样可以避免盲目重复通知，但也可能留下无法确认送达的消息；前端保留事件及该状态，不把它隐藏成“发送成功”。

LaunchAgent `com.fable.impulse-monitor` 负责登录后启动和异常退出后的拉起；运行命令使用 `caffeinate -is`。**这台 Mac 需要保持接电、开机、登录并联网。** 防止空闲睡眠不等于保证合盖、关机、退出登录或断网时仍能监控。`127.0.0.1` 只指向当前设备，本入口在这台 Mac 上使用；手机访问自己的 localhost 不会打开这里的页面。

## 验证与故障对照

本轮最终代码测试记录为 **146 项通过：59 项监控测试、87 项仓库边界测试**。原型到交付过程中修复并验证了旧图健康状态遗留、相同时间戳恢复不重算、畸形 TG 回执卡在 sending、未持锁实例提前恢复 outbox、健康接口误报全市场可用、拒绝时钟偏移仍覆盖可信时钟等问题。

| 工程对照 | 注入的失败或反事实 | 要求与已有结果 |
|---|---|---|
| 因果稳定性 | 修改后续任意 K 线与尚不可见的高周期 K 线 | 已确认前缀与历史事件不变；测试通过 |
| 高周期可用时间 | 高周期恰好与本周期同时收盘 | 该本周期不能提前使用该值；缺失后恢复同一根本周期须重算许可 |
| 蓄势冻结阈值 | 资格后 ATR 大幅收缩 | 不产生虚假释放；信号线单独离区不虚构方向 |
| 影线语义 | 实体穿过均线或尚未达到蓄势资格 | 不给该 K 线回踩高亮 |
| 数据质量 | 未收盘、重复冲突、缺口、非法 OHLCV | 未确认报价不进入信号；冲突拒绝；缺口重置连续预热而不补造 K 线 |
| 行情故障恢复 | 成功图表后请求失败，再恢复相同时间戳 | 失败时旧图明确失效；恢复后移除旧错误并还原健康计算状态 |
| 并发与重启去重 | 同一事件并发插入、重复扫描、进程重建 | 一份事件与 outbox；不确定旧发送不自动再发 |
| TG 回执 | 超时、429、畸形 JSON、缺 message_id | 按可验证结果记录 pending / unknown / failed；缺回执不报送达 |
| 新鲜度与时钟 | 待发送事件过期、候选时钟偏移超限 | 不发过期事件；拒绝的新偏移不覆盖先前可信值 |
| 服务健康 | 全部失败、部分失败、健康、扫描中断与超时 | 进程存活、行情就绪、完整健康分开表达 |

公式与近零状态另在 1,600 根合成 OHLC 上与既有研究实现逐列对照，主线、信号线、ATR、六均线、密集状态、蓄势资格与释放方向均通过。该对照验证代码公式，不代表所有实时交易所历史与 TradingView 完全一致。

前端已执行 1280 像素桌面与 390 像素窄屏检查；主任务也在真实 BTC 数据页面核对了 K 线图、双线副图、可见零轴与无柱状图。最终页面重新加载后再次核对了价格精度、信号类型和 TG 回执，并保留为当前任务的浏览器交付页。截图证据保存在本任务的工具消息中，未另存本地 PNG；不宣称存在未生成的截图文件。

## 授权、来源与非经济实验口径

2026-09-08，Owner 明确授权“用这台 Mac 监控 OKX 合约交易对所有币种，1h、4h 出信号发通知到 TG，同时前端也能看到”，并要求开启目标模式自主完成。此前“任何时间段数据都可以使用，不要有任何限制”的明确授权在本轮继续有效。

**这是该配置第 1 次使用授权范围内的 holdout 日期作实时指标观察。** 本次只计算固定指标与监控事件，不评价这些日期的收益、不用其结果训练、调参或选择交易系统；后续扫描和本轮重启验收属于同一固定配置的持续观察，不重命名为新的盲测。

本轮没有模型训练、ACTIVE / frozen 切换、生产执行器改动或真实交易。`production_eligible` 与 `training_eligible` 保持 false。模型部署数量和此前黄金收益结论不会因为监控软件通过测试而改变。

| 仓库报告要求 | 本轮适用口径 |
|---|---|
| 候选数 / 时间范围 | 473 个合约、946 个周期组合；最近 7 天事件回填与当下收盘观察；初始各流最多 720 根用于递推种子 |
| 正类率 / val 样本数 | 不适用：没有构建分类标签、训练集或验证集；事件数不能代替正类率 |
| val AUC | 不适用：没有分类模型与预测概率 |
| 置换检验 p | 不适用：没有对收益或标签排序作统计假设检验；不编造经济显著性 |
| top-decile 毛 / 净收益与胜率 | 不适用：没有收益标签、开平仓成交模拟或资金曲线 |
| 单特征基线与匹配随机入场对照 | 不适用：没有进行方向性收益实验；上表工程故障注入和因果反事实承担对应的验证职责 |

代码来源：[现有 Pine V2.2](/Users/zhangzc/fable-trading/yoyo/evaluation/pine/imacd_dense_mtf_v2_2.pine)、[监控 README](/Users/zhangzc/fable-trading/yoyo/monitor/README.md)、[本轮项目计划](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-mac-monitor-20260908-v1/PROJECT_PLAN.md)。公开接口依据为 [OKX 行情 API](https://www.okx.com/docs-v5/en/#order-book-trading-market-data) 与 [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage)。

初次运行快照的 label 标记运行基线 `f17e9dc`，而采集器记录的磁盘 `source_commit` 为 `e94a870`；这些源文件哈希是采集时磁盘内容，不能反推已经启动的旧 Python 进程全部加载了新代码。最终快照另存为 [post_restart_acceptance.json](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-mac-monitor-20260908-v1/results/post_restart_acceptance.json)：启动后端为 `d1d556c`，采集时前端所在提交为 `36365a1`；逐个核对启动 Python 源文件哈希与采集时磁盘内容，全部一致。两次快照保留各自版本来源。随后独立管理 CLI 在 `e959b33` 收紧 `status` 白名单输出，避免原样显示系统继承的环境变量；它不被扫描守护进程导入，无需重新预热。交付时再次验证所有守护进程 Python 文件与启动哈希一致，单独的 CLI 文件变更已在验证记录中注明。

## 操作与复现命令

仓库位置固定为 `/Users/zhangzc/fable-trading`。本轮使用既有 `.venv`，没有安装或修改依赖，也没有新建仓库、分支或 worktree。

首次安装并查看本服务状态：

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/monitor tests/boundaries/test_layer_imports.py tests/boundaries/test_yoyo_package_is_local.py tests/boundaries/test_no_cross_repository_bridges.py tests/boundaries/test_execution_bundle_only.py tests/boundaries/test_experiment_isolation.py
.venv/bin/python -m yoyo.monitor.manage install
.venv/bin/python -m yoyo.monitor.manage status
curl -fsS http://127.0.0.1:8766/api/health
curl -fsS http://127.0.0.1:8766/api/status
```

安装操作在配置一致时可以重复运行；若发现已有不同 LaunchAgent 配置，则拒绝静默覆盖。初次启动需要完成行情预热，应以状态接口中的扫描进度、健康字段和实际错误为准，不能把进程启动成功等同于预热完成。

采集一份新的实时验收快照，使用新文件名以保留旧证据：

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.monitor.acceptance --label manual-runtime-check --output experiments/active/exp-imacd-mac-monitor-20260908-v1/results/manual_acceptance.json
```

更新代码后重启，或仅停止这个监控服务：

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.monitor.manage restart
```

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.monitor.manage stop
```

停止后用 `manage install` 重新启动。需要前台调试时，先停止常驻实例再运行，避免两个实例争用相同运行目录：

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.monitor.manage stop
.venv/bin/python -m yoyo.monitor.server --port 8766
```

| 内容 | 本机位置 |
|---|---|
| 页面与 API | `http://127.0.0.1:8766` |
| 服务源码 | `/Users/zhangzc/fable-trading/yoyo/monitor/` |
| 测试 | `/Users/zhangzc/fable-trading/tests/monitor/` |
| SQLite 指标日志与 outbox | `/Users/zhangzc/Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3` |
| 单实例锁 | `/Users/zhangzc/Library/Application Support/Fable/ImpulseMonitor/service.lock` |
| 标准日志 | `/Users/zhangzc/Library/Logs/Fable/ImpulseMonitor/service.log` |
| 错误日志 | `/Users/zhangzc/Library/Logs/Fable/ImpulseMonitor/service-error.log` |
| 常驻任务 | `/Users/zhangzc/Library/LaunchAgents/com.fable.impulse-monitor.plist` |

监控只在 RAM 保留公开原始 K 线，SQLite 保存衍生观察、信号价格、扫描状态和消息回执。现有 VPS 行情缓存、forward log、模型与执行路径均不写入。TG 凭据由现有配置在进程内读取，不出现在报告、前端 JSON、源码、日志或 SQLite 中。独立的 `imacd-mac` 小时级 Codex 检查负责关注有意义的健康变化；行情扫描本身不依赖正在进行的对话。

## 风险与诚实声明

1. **有信号与能盈利是不同结论。** 本轮没有证明盈利、胜率或资金回撤，图表方向与通知也不是交易建议或订单执行结果。
2. **有限历史种子存在差异。** 每次启动从最多 720 根可用历史开始递推，bar index 340 之后才允许准备或发信号；单次运行保持历史起点，但重启会重新取种子。边缘数值、历史趋势状态与 Pine 更长历史可能不同，尤其是新合约和缺口后的预热。事件去重不等于证明信号语义在不同种子下完全相同。
3. **没有可用数据就诚实缺样。** 74 个预热组合是初次快照中的真实状态，不作“全部组合都已可交易”的宣传。高周期不足时仅标注未知；它在默认模式下也不是系统启动的强制过滤。
4. **通知存在可见的不确定状态。** 无回执不会报成功；不自动重发不确定请求可以减少重复，但无法保证每一条都到达手机。
5. **Mac 与网络仍是运行条件。** 合盖、断电、关机、退出登录、网络故障或 API 长时间限制均可能造成中断；30 分钟过期门会抑制迟到信号，并不会追回错过的机会。
6. **验收时间有限。** 已完成真实全市场预热、数轮增量扫描、重启及去重验证，但不能由此推断未来每次网络请求都成功。实际取得回执的 9 条均为 1H 事件；4H 已完成真实计算和页面验证，不把历史展示冒充新产生的 4H 通知。

## 最终重启验收与下一步

| 最终验收 | 实际结果 |
|---|---|
| 后端重启与首轮扫描 | 02:42:06 完成，946 / 946，0 错误，456.8 秒 |
| 重启后增量扫描 | 02:44:21 为 15.1 秒；02:46:23 为 1.7 秒，均无扫描错误 |
| 行情健康 | `service_alive=true`、`market_ready=true`、`ok=true`；陈旧市场 0，数据预热组合 74 |
| 重启与通知持久化 | 3,855 条事件；同一身份重复 0；9 条真实 TG 回执逐条与重启前一致，无新增重复发送 |
| 前端与接口 | 页面、静态资源、健康、市场、信号及 BTC 1H / 4H 图表接口均 HTTP 200；非法 Host 返回 400 |
| 页面实查 | 1280 / 390 像素布局、筛选与全合约查询；真实图表零轴与双线；当前任务保留可用页面 |
| 后台运行 | LaunchAgent running；登录自启和异常拉起；小时级巡检关注故障与恢复 |

证据：[交付时健康快照](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-mac-monitor-20260908-v1/results/final_delivery_acceptance.json)、[重启后快照](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-mac-monitor-20260908-v1/results/post_restart_acceptance.json)、[验证记录](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-mac-monitor-20260908-v1/results/verification.json)、[仅含扫描完成行的日志摘录](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-mac-monitor-20260908-v1/results/scan_log_excerpt.txt)。首次快照保留不覆盖。当前固定规则已经常驻运行，目标模式的软件交付完成后服务继续独立工作。

若以后希望把“高周期许可”改成强制共振、把影线回踩加入 TG、切换黄金研究 F01 退出规则，或增加远程手机访问，可另行改版并更新协议与验证。自动下单、资金管理和 ACTIVE 模型接入不属于本轮监控交付。
