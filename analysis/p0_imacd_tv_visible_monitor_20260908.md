# IMACD 监控纠正：以 TradingView 实际可见的启动标记为准

Owner 明确选择：“TradingView 主图真正出现启动箭头，才发 TG”。本轮读取真实图表的输入与显示开关，把前端和通知统一到当前可见的 **“蓄势释放”价格标签**。本机入口：[Fable 信号台](http://127.0.0.1:8766)。

## 此前为什么仍然不对

第二版根据“刚突破零轴”这句话，把信号解释成前根 md 精确为 0、本根非零。它通过了自己的测试，但测试的定义与用户图上实际可见标记不一致。继续验证该定义，不能证明通知符合用户意图。

本次通过 TradingView 原生 UI 读取 OKX ETHUSDT.P 4H：指标“IMACD · 蓄势释放”，34/9，showFocus=true，focusMinBars=12，focusAtrBand=0.10，showMarks=false，showPriceText=true。普通系统启动/退出被隐藏，显示的是本地 Pine V2.2 的 focusRelease 价格标签分支。设置对话框用取消关闭，没有改参数、样式或保存脚本。Pine 编辑器曾显示另一个旧脚本，其内容未冒充当前图表源码。

| 版本 | 判定 | 与当前图表的关系 |
|---|---|---|
| v1 | entry / release / exit 混合通知 | 把隐藏普通启动、退出和可见释放混在一起 |
| v2 | md 从精确 0 首次变成非零 | 可能早于真正可见的蓄势释放 |
| v3 | 当前设置下已确认的 focusRelease → tv_start | 对应实际可见的“蓄势释放”标签 |

v1 已送达的70条历史消息包含25条entry、32条exit、13条release。上一轮“44条不符合精确离轴”的分类是当时错误定义下的结果，**不能继续作为本轮可见标记的错误数**。旧报告与回执保留，v2 注册状态改为 superseded。

## 用一根真实图表标记确定含义

实际图表标签为 **“蓄势释放 ↑ ·44根 / 收盘1922.23”**。先提交 builder（a8974b7），再从 OKX 公开 history-candles 获取固定720根4H窗口，在RAM内运行实际监控算法。9次请求、0缺口；没有缓存原始K线。

| 对照 | 原始离轴事件 | 图上可见启动 |
|---|---:|---:|
| ETH-USDT-SWAP · 4H | zero_breakout / 隐藏entry | 蓄势释放 / tv_start |
| K线开盘，北京时间 | 2026-08-19 08:00 | 2026-08-19 16:00 |
| 收盘确认，北京时间 | 2026-08-19 12:00 | 2026-08-19 20:00 |
| 收盘点位 | 1911.20 | **1922.23** |
| 启动前近零蓄势 | 42根 | **44根** |
| 当前通知资格 | 不通知 | 与图表目标标记对应 |

真正启动前的 md 已经是1.6782385620、sb为0.2580567249，仍在冻结阈值±1.9863322974内；启动根md变为3.2607141914，才出带。要求 previous_md==0 会选中提前8小时的另一根K线，并排除真正启动根。

复现输入哈希、Pine文件哈希、事件和指标上下文在 eth4h_tv_reference.json，verified=true。固定窗口结束于2026-09-07 20:00UTC，起始于2026-05-10 20:00UTC；这是单个实际可见标签的正反例匹配，不是全历史自动等价证明。

## 统一的通知条件

- 保留当前图表参数：至少12根近零蓄势合格后冻结0.10 ATR阈值；前一根双线仍在带内，本根主线严格出带，收盘确认并完成历史预热。
- showFocus开启、showMarks关闭对应的可见释放成为唯一 tv_start。均线密集与高周期保留背景信息，不改变这条绘图分支。
- 单独离开精确零轴、隐藏普通启动/退出、未达资格、只有信号线出带、后续光晕及回踩观察都不通知。
- 生成、入队、发送端、主API、列表、主箭头和24小时数量使用同一协议 imacd-tv-visible-start-monitor-v3。
- TG写出方向、标记收盘价、蓄势根数、K线开盘与收盘确认时间。相同事件只发送一次。
- 同步交易所时钟后记录新协议生效点；升级前历史可以展示但不补发。旧回执完整保留，当前发送数量单独统计。

配置标识 imacd-v2.2-focus12-band0.10-marks-off 是**实际设置快照**。后续手动修改 TradingView 参数不会自动同步到监控，页面已明确说明。

## 验证方法

190项监控及仓库边界测试通过，只有既有urllib3/LibreSSL兼容提示。独立只读复核通过：已非零的前md、44根蓄势正例被接受；15种不合格/隐藏/错误协议等反例被拒绝。测试包括冻结带、双方向、未来价格扰动、高周期可见时间、合成行情经过真实扫描入队函数、重启去重、校准时钟切换、旧历史抑制和发送故障。

前端额外修正 notification_status=history 的显示，避免升级前的近期回算数据被含糊显示为“已记录”；JS语法检查和真实页面检查通过。后端启动源码为a8974b7，静态历史标签修正为45ec14f；不需要为这次静态文本修正重启扫描进程。

这是通知语义的软件修复，没有收益评价。AUC、置换p、top-decile毛/净收益、胜率、单特征经济基线及匹配随机入场均不适用；这里的严格对照是同一实际图表标记与提前离轴反例、因果扰动和队列故障注入，不以通知送达冒充盈利证据。

## 最终全市场验收

| 项目 | 实测 |
|---|---|
| 服务/协议 | 1.2.0 / imacd-tv-visible-start-monitor-v3 |
| 生效时间 | 2026-09-08 08:06:52 北京时间（校准OKX时钟） |
| 覆盖范围 | 473个在交易SWAP：458个USDT、15个币本位；1H/4H共946个窗口 |
| 首轮扫描 | 946/946完成，0错误，456.1秒；08:14:24完成 |
| 行情健康 | ok=true，陈旧窗口0；74个历史不足窗口仍不发信号 |
| 当前协议历史标记 | 420条，契约违规0；默认API返回420条，全部tv_start |
| 24小时标记数 | 44条，只统计当前协议的可见启动 |
| 原始离轴API请求 | kind=zero_breakout返回400 |
| 通知审计 | 当前sent/pending/failed/unknown均0；升级前通知队列0，非法事件0 |
| 历史/身份 | 旧70条回执逐条相同；重复事件身份0 |
| 运行代码 | 全部启动Python文件SHA与磁盘一致；静态前端独立记录版本 |

Chrome真实页面9项检查通过，无浏览器error/warn。CHZ 4H显示20根/0.01418且精确零轴0；ACU 1H显示24根/0.14384；BCH-USDT 4H显示16根/260.80。1H/4H、搜索、多空筛选、金色蓄势区、明确零轴、仅tv_start箭头均通过。历史70是API隔离核对，未声称页面展示该数字。近期回算数据正确显示“历史记录”，不会冒充TG送达。

新版启用后尚未产生需要发送的合格收盘标记，因此没有新的真实TG回执；没有发送启动测试消息。通知发送端的隔离与故障场景由测试验证，旧70条送达不能冒充v3实际送达证明。

## 复现与操作

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/monitor tests/boundaries/test_layer_imports.py tests/boundaries/test_yoyo_package_is_local.py tests/boundaries/test_no_cross_repository_bridges.py tests/boundaries/test_execution_bundle_only.py tests/boundaries/test_experiment_isolation.py
node --check yoyo/monitor/static/app.js
.venv/bin/python experiments/active/exp-imacd-tv-visible-monitor-20260908-v3/verify_reference.py
.venv/bin/python -m yoyo.monitor.manage status
curl -fsS http://127.0.0.1:8766/api/health
curl -fsS 'http://127.0.0.1:8766/api/signals?limit=5&kind=tv_start'
```

固定参考脚本会把派生JSON打印到标准输出，退出码0表示真实参考匹配。原始OHLC仅在RAM。手动收集新的实时验收请用新文件名，不覆盖本轮证据：

```bash
.venv/bin/python -m yoyo.monitor.acceptance --label manual-tv-visible-check --output experiments/active/exp-imacd-tv-visible-monitor-20260908-v3/results/manual_check.json
python3 scripts/md_to_html.py analysis/p0_imacd_tv_visible_monitor_20260908.md --out-dir analysis/html
```

服务已通过manage install恢复，LaunchAgent为com.fable.impulse-monitor。本轮只维护该服务；主机登录后自动启动，崩溃自动恢复。必要时使用manage restart；stop卸载后用install恢复，不开第二个进程。扫描完成后等待120秒，前端15秒刷新，队列/发送/UI均保持30分钟新鲜度。小时巡检已更新成v3设置快照与队列核对，正常时安静，无信号不发TG。Mac需保持登录、接电联网。

## 风险与诚实声明

单个图表参考精确匹配、合成反例与全市场契约审计不能证明所有历史点都与TradingView逐字节一致。实际平台加载历史长度与本地720根种子可能产生边缘差异，新合约历史不足会明确预热；没有为凑覆盖伪造K线。此次未修改TradingView副图、主图均线、亮色K线或指标参数。

这是该固定配置第1次在Owner既有“任何时间段数据都可以使用”明确授权下使用holdout日期作实时行情观察与参考重建；不读取后续收益用于调参、训练或筛选。没有收益主张、下单、改仓、凭据修改、模型切换、promote或VPS数据写入，training_eligible和production_eligible保持false。

通知包含的是信号K线收盘价，不是成交价；只有收盘确认后才通知，盘中暂现不能冒充已确认箭头。若以后更改TradingView脚本或设置，需要同步监控配置；当前版本不会自动读取远端变化。

## 证据与后续

证据目录：experiments/active/exp-imacd-tv-visible-monitor-20260908-v3/results/。包含tradingview_settings_observation.json、eth4h_tv_reference.json、after.json、verification.json、frontend_qa.json。前两份报告作为当时记录保留，由本轮明确取代其通知语义。

下一步按当前配置持续运行，等待下一根真实、收盘确认且新鲜的可见启动自动通知；无需再次授权。未来若Owner改变脚本/显示设置，需按新实际标记重新核对。任何收益研究、下单或模型切换都不属于本轮交付。
