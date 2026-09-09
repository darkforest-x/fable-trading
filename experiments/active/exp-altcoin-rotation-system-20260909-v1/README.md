# 山寨轮动观察台

这是可运行的研究观察系统。先看市场与板块，再查4h结构、15m完整性、事件审核与风险参考。
观察分不是胜率；尚无收益有效性结论，也不产生可执行订单。

## 启动

页面右上角“外观模式”支持浅色、深色和跟随系统，选择保存在当前浏览器，刷新后保留。
主题覆盖状态标签、详情弹窗与SVG图表；切换外观不会扫描或改变研究配置。

2026-09-09修复：原临时进程退出会使18766无法访问。现由本机LaunchAgent
`com.fable.rotation-observer`托管网页服务，登录时启动，异常退出后拉起；不会自动扫描。
配置保存在本目录`com.fable.rotation-observer.plist`，安装于当前用户的
`~/Library/LaunchAgents/`。日志在`runtime/server.stdout.log`与`server.stderr.log`。
以下命令只控制本观察台，其他监控服务使用各自独立的label。

```bash
launchctl list | rg 'com.fable.rotation-observer'
curl --max-time 5 http://127.0.0.1:18766/healthz
# Restart this web service after observer source changes:
launchctl kickstart -k gui/$(id -u)/com.fable.rotation-observer
# Stop this web service for the current login session:
launchctl bootout gui/$(id -u)/com.fable.rotation-observer
# Start it again after bootout has finished:
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.fable.rotation-observer.plist"
```

托管服务已运行时，无需再手动执行下面的`serve`命令。观察台仅限这台Mac本机访问。

修复验收：已安装配置与本目录文件逐字节一致，`plutil -lint`通过；独立调用确认
服务进程由PID 1托管，`/healthz`与`/api/snapshot`均返回200。浏览器新标签页实际
加载“本地服务已连接”和6个候选，仍为2026-04-30截止的原历史快照。本轮没有重新扫描。
登录启动已配置，未通过实际注销或重启电脑测试。

从fable-trading根目录使用已存在的.venv，无新增依赖。默认端口18766，仅本机可访问。

```bash
.venv/bin/python -m yoyo.rotation status
.venv/bin/python -m yoyo.rotation scan
.venv/bin/python -m yoyo.rotation serve --port 18766
```

打开 http://127.0.0.1:18766 。GET不会拉行情；点击“扫描一次”才读取所选配置的数据。
默认cutoff为2026-04-30 00:00 UTC，8个明确样本，属于历史工程验收，不能当成今天的推荐。
真实采集前源码、配置、事件目录和计划必须已提交。源码改变不会读取旧版本缓存。
失败显示错误并保留上次快照；旧快照不会改成当前时间。

```bash
.venv/bin/python -m yoyo.rotation scan --synthetic --runtime experiments/active/exp-altcoin-rotation-system-20260909-v1/demo-runtime
.venv/bin/python -m yoyo.rotation export > /tmp/rotation-observations.csv
.venv/bin/python -m yoyo.rotation approval-request
```

合成模式始终有标识，不自动替换失败行情。SQLite位于本实验runtime，仅保存派生指标、来源URL/哈希、状态转移；不存原始K线。CSV含配置/源码/cutoff身份，空字段保持空。

## 观察顺序与状态

1. 日线：BTC趋势、ETH/BTC30日相对强度、样本广度描述环境；广度只代表当前覆盖样本。
2. 候选：7/30日相对BTC收益、SMA20/50、30日日成交额中位数、板块强弱。RS公式为(1+币收益)/(1+BTC收益)-1。
3. 4h：前20根高低价框；收盘越过上沿且成交量达到前20根中位数的2倍才形成raw breakout。过去12根无raw breakout才建立独立锚点。
4. 锚点最多覆盖后12根；之后首个低点接近上沿且收盘守住、量低于突破的bar记回踩。穿过原下沿则该锚点持续失效，过度延伸单独标记。默认至少45根历史保证尾窗起点不改变结果。
5. 15m同类结构仅辅助观察；缺失或失效不能显示条件齐备。
6. 事件审核：未知不等于安全。任何新公告使旧审核退回待复核。
7. 风险：每1万资金、0.5%示例风险预算、10%名义上限；沿用SPOT_TAKER现货成本0.3%，与项目旧报告0.2%基准分别标明。无既有持仓资料，不合成为组合建议。

“待突破”表示当前无有效突破锚点，不证明低波动压缩；平台算法没有验证箱体触碰次数或积累强度。
事件资料覆盖、供给/解锁、筹码集中度、合约深度、相关持仓仍需逐个研究。
OI单快照不能推出增减；资金费率周期缺失时不得默认8小时。

## 数据源与配置

config.json显式设置venue为binance或okx，symbols统一BASEUSDT，OKX适配为BASE-USDT。
历史不读取今日成员/涨幅榜倒选；live获批后可选exchange_spot，先冻结24h成交额池再计算。
明确股票、ETF、稳定币、杠杆产品排除；未知资产保留但风险阻断，JUP不会因UP后缀误删。
OKX日线1Dutc、4H、15m；history-candles最多5页、每页100；confirm必须为1。
衍生品只在Binance获批live模式补前6名，不自动跨交易所映射。

## 人工事件目录

assets：symbol、asset_type(crypto/non_crypto/unknown)、sector、published_at。
events：symbol、title、severity(info/warning/block)、source_url、published_at、effective_at，可选expires_at。
reviews：symbol、reviewed_at、valid_until、reviewer、sources数组、status(reviewed或revoked)。
时间必须带时区；先按当时可知资料筛选，再选最新审核；同时间冲突报错。
reviews默认空，不能为了出现绿色标签伪造已完成审核。公布时间不是人工核查时间。

## 实时观察启用

approval-request只输出NOT APPROVED配置方案，不生成批准。Owner需明确本配置/源码、有效期、holdout观察次数；实际回复保存receipt后才可执行。修改配置或源码会使旧receipt失效。
完整配置须入本仓并提交，再用--config和--receipt传入。

```bash
.venv/bin/python -m yoyo.rotation watch --config <committed-live-config.json> --receipt <owner-receipt.json> --cycles 4
```

watch为明确次数的独立进程，每15分钟一次；本轮不安装后台调度或通知。不接入forward脉冲，不改模型/ACTIVE/仓位/API key，不训练、不promote。

## 验证

```bash
.venv/bin/python -m pytest tests/test_rotation_system.py tests/test_rotation_logic.py tests/test_rotation_derivatives.py tests/test_rotation_okx_provider.py tests/test_rotation_web_static.py tests/boundaries/test_layer_imports.py tests/causality/test_holdout_boundary_is_single_valued.py -q
```

无前视、缺口、事件时序、到期授权、去重、错误保留、显示单位与图表坐标均有负对照。
AUC、收益、胜率、置换p、匹配随机入场对照均未计算；此轮不是收益评估。
