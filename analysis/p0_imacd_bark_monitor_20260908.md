# IMACD 新增 Bark 信号推送

Owner提供自己的Bark设备地址并要求“信号推送到我的bark”。现已为本机IMACD监控增加独立Bark通道，保留TG。入口仍是 [Fable 信号台](http://127.0.0.1:8766)。本轮不增加AI定时任务，原imacd-mac巡检继续PAUSED。

## 改动与上一版本对照

| 项目 | 之前1.2.0 | 当前1.3.0 |
|---|---|---|
| 推送通道 | TG | TG与Bark独立推送 |
| 信号定义 | 当前TradingView可见蓄势释放 tv_start | 完全沿用，34/9、12根、0.1ATR等均未调整 |
| 前端每条记录 | TG结果 | TG、Bark分别显示 |
| 成功含义 | TG message_id回执 | TG保持；Bark为服务器接受，不冒充手机显示或已读 |
| 历史 | 不补发 | Bark首次启用前及已有事件均不补发 |
| 故障/重试 | TG独立状态 | 两个队列、两个发送线程、独立启用时间 |
| 配置 | 现有TG配置 | 新Bark密钥仅本monitor私有配置，不改其他项目配置 |
| AI巡检 | 已暂停 | 继续暂停；后台常驻服务自行运行 |

仍只监控OKX全部在交易SWAP的1H/4H，只有当前可见focusRelease标记在收盘确认、历史就绪且新鲜度不超过30分钟时发送。Bark加入没有触碰Pine图表、六均线、副图、影线高亮、信号模式或交易执行层。

Bark通知包含币种、周期、多空方向、启动收盘价、蓄势根数、K线开盘时间、收盘确认时间，点击打开对应TradingView图表。通知分组为Fable IMACD，使用普通active级别，不发送闹钟式或紧急级别提醒。

## 通道隔离与密钥处理

Bark设备密钥保存在Mac用户运行目录的bark.json，文件权限0600；未加入git、前端、事件日志、SQL或异常文本。推送使用固定 [Bark官方API V2](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md) 的POST /push，设备密钥放请求JSON，关闭重定向。

一次新事件插入在同一SQLite事务中创建各自满足条件的TG/Bark队列项。已有事件身份不变，不能因为启用新通道就重放历史。每个通道的启用时间由校准后的交易所时钟首次记录，重启保持原值。Bark不消费TG队列，TG失败或启用时间不同也不拦截符合Bark条件的事件。

依据官方 [响应结构](https://github.com/Finb/bark-server/blob/master/router.go)，Bark只有HTTP200、整数code200和有效服务器timestamp才记为服务接受。明确4xx拒绝记录失败；429按有上限的Retry-After重试；超时、5xx、响应异常或中途退出保持unknown，不盲目重发。独立发送线程避免一边网络等待拖住另一边。

## 验证与失败记录

最终234项监控和仓库边界测试通过，21.15秒。新增场景覆盖：多通道原子插入/回滚、32路并发和一次领取、重复扫描与重启、历史不补发、独立启用时间、可见标记二次校验、未收盘/未来/过期拒绝、429/4xx/5xx、错误响应与超时脱敏、私有配置权限和显示状态。

首次测试232通过、1个合成用例失败：未来收盘的测试把队列到期时间也放到了未来，实际没有进入领取/新鲜度检查。修正该用例的检测时间，才真正覆盖负age拒绝。随后只读复核发现Bark曾继承TG启用时间门；拆成共享基础资格＋两条独立cutover，增加TG较晚而Bark较早的反例，最终234通过。没有以弱化判据换取测试通过。

已有urllib3/LibreSSL兼容提示仍在；没有安装、升级或降级任何依赖。使用 [公开ping接口](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md#ping) 实测HTTP200、code200、pong，不携带设备密钥、不发送通知。这个连通性结果**不能证明设备密钥有效或手机已收到推送**。

源码与collector先提交为d95fb44，再收集before、保存私有配置并重启本服务。用户要求只有信号才通知，因此没有启动测试、示例或历史消息；若本轮没有新的合格信号，将明确没有真实Bark接受回执。

## 真实服务与前端验收

| 检查 | 结果 |
|---|---|
| 运行版本 | 1.3.0，源码d95fb44 |
| Bark首次启用 | 2026-09-08 08:42:47.791 北京时间 |
| 原TG启用边界 | 与before逐字一致，未因新增Bark重置 |
| 首轮重启扫描 | 473合约×1H/4H，946/946，0错误，470.14秒 |
| 完成时间 / 行情健康 | 08:50:34.400，ok=true，陈旧窗口0 |
| 历史不足 | 74个窗口仍诚实预热，不强造信号 |
| 主信号 | 420条，全部当前协议tv_start，Bark状态全部history |
| Bark | configured/enabled=true，sent/pending/failed/unknown均0 |
| TG | 旧70条回执完整相同；当前协议sent/pending/failed/unknown均0 |
| 队列隔离 | Bark启用前入队0，非法信号入队0，重复事件身份0 |
| 运行源码 | 启动Python SHA全部与磁盘一致；Pine/信号引擎/标记契约与v3逐字不变 |
| 密钥与巡检 | 源码/API密钥泄露0，私有配置0600，AI巡检仍PAUSED |

真实Chrome前端8项检查通过，未发现问题。顶栏TG与Bark均启用，运行页分别显示接受/失败/未知/历史等状态；420条旧记录没有冒充已推送。ACU 1H的24根/0.14384和BCH 4H的16根/260.80保持正确，图表双线、零轴、蓄势区与tv_start箭头正常。预热早期MEGA图表404已记录为尚未初始化，后来已处理的ACU/BCH图表访问正常，没有把预热误报成成功。浏览器未读取私有配置；公开接口和诊断字段不含设备key或私有推送地址。

当前没有Bark真实推送接受记录。只有下一根符合当前条件且晚于启用点的新鲜收盘信号才会调用推送接口。本轮不宣称手机已经响铃、看到消息或已读。

## 复现与维护

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/monitor tests/boundaries/test_layer_imports.py tests/boundaries/test_yoyo_package_is_local.py tests/boundaries/test_no_cross_repository_bridges.py tests/boundaries/test_execution_bundle_only.py tests/boundaries/test_experiment_isolation.py
node --check yoyo/monitor/static/app.js
.venv/bin/python -m yoyo.monitor.manage status
curl -fsS http://127.0.0.1:8766/api/health
curl -fsS 'http://127.0.0.1:8766/api/signals?limit=5&kind=tv_start'
```

首次配置设备时用隐藏输入写入私有文件（用户设备地址不写在命令、源码或报告中）：

```bash
.venv/bin/python -c 'from getpass import getpass; from pathlib import Path; from yoyo.monitor.bark import save_configuration; save_configuration(getpass("Bark URL: "), Path.home()/"Library/Application Support/Fable/ImpulseMonitor")'
.venv/bin/python -m yoyo.monitor.manage install
```

如果服务已在运行，改配置后使用manage restart加载，而非另开进程。采集新的验证请另起文件名，不覆盖本轮：

```bash
.venv/bin/python -m yoyo.monitor.acceptance --label manual-bark-check --output experiments/active/exp-imacd-bark-monitor-20260908-v1/results/manual_check.json
python3 scripts/md_to_html.py analysis/p0_imacd_bark_monitor_20260908.md --out-dir analysis/html
```

证据目录包含before.json、after.json、verification.json、frontend_qa.json。Bark和TG由LaunchAgent托管的本机服务运行，无需Codex定时唤醒；Mac仍需保持登录、接电和联网。

## 风险与诚实声明

本轮属于通知通道交付，没有收益评估、训练、参数筛选或交易订单。候选/正类率/val样本及AUC、置换p、top-decile毛净收益、胜率、单特征基线、匹配随机交易对照均不适用；对应验收为相同信号在两个通道的独立状态对照、边界/并发/故障注入和前后真实回执审计。

这是该通知配置第1次沿用Owner所有日期授权的live-observation日期使用；重启读取固定720根行情、显示最近7天派生事件，不评价其后收益。没有原始OHLC入库、VPS写入、模型/阈值/成本切换、promote或真金操作，training_eligible及production_eligible均false。

服务接受不等于手机送达；网络超时的unknown可能意味着实际已送出，也可能未送出，因此不自动重试。Bark密钥由用户提供，本轮仅验证私有加载和官方服务连通性，没有冒充设备端确认。后续真实信号的接受/失败/未知结果会在前端分别记录。HTML由仓库转换器生成并做结构与关键数据核对；本轮真实浏览器验收对象是HTTP监控页。

下一步继续按当前固定规则运行，等待新的合格收盘信号自动推送。只有Owner决定变更通道、设备或指标条件时才调整对应配置。
