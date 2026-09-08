# 高波动山寨跨币迁移诊断预注册 v1

2026-09-09。Owner 已授权广泛离线山寨研究和任意历史时段；本轮继承主实验锁定配置，只检验跨币迁移。无新参数搜索、训练、网络抓取、实盘操作或部署。

## 输入与身份

- 加密品种取 `exp-1h-okx-model-first-standing-top10-20260904-v1/results/universe.json`，2026-09-03 17:23:19 UTC 冻结的277个 `instCategory=1` 品种，未按涨幅或量排名；SHA256 `e9bf6421dc2fddb61d34a6fed2204f2a04c191d81c9157b52c70213b74f58682`。
- 主54取 `exp-imacd-yolo-expanded-20260908-v1/universe.json`，SHA256 `023e1e031c64c5d693ada72fa063d30392355e2790861fc30403989f941d66d7`。全部排除；SOPH、USELESS仍是事后展示币，也排除。
- 外部池是上述分类清单与本地 `data/kline_fetched/okx_*_USDT_SWAP_15m_*.csv` 文件名交集。预计215币；先按身份冻结，再验证数据，禁止按当前涨幅、历史长度或结果筛选。原分类清单中的6个无本地文件品种另列，不冒充已获得完整全市场覆盖。
- 多文件歧义、OHLCV错误、时间缺口或冻结右端不完整均拒绝该币，完整记录原因，并保留该币现金份额。不得静默删币、挑一个看起来好的文件、连接断裂片段或前填。
- 右端固定2026-08-14 04:00 UTC，兼容1H/4H完整收盘。只解析此前15m行；完整源字节SHA与有界前缀SHA分别记录。不写canonical。准备产物仅保留只读源引用及审计，不复制215份历史；加载时重验源SHA并只解析有界前缀。压缩特征缓存是可重建派生品，每币评价完成后清理，不属于冻结价格身份。

## 冻结配置与时段

配置只来自主实验 `results/development/selection_lock.json`，SHA256 `3ccadfd45eaa7a8ce0bc0a1e031b4450b3bfc3ba2752615271f077dea2326e08`，生成于2026-09-08 17:00:54 UTC。同时验证锁文件引用的主开发summary哈希；不根据外部结果重新选配置。

- 1H：base、exit_fixed3r、exit_chandelier、exit_sma60、gate_box_break。
- 4H：base、exit_fixed3r、exit_chandelier、exit_sma60、signal5。
- 三个独立折：2026-01-01至05-04；05-04至07-12；07-12至08-14 04:00 UTC。每折独立从现金1起步，边界强制退出单列。
- 周期、参数、背景条件、初始止损、退出和成本完全继承主实验，不引入衍生品、不组合多个胜出条件、不拟合新阈值。
- 1H/4H同源完整15m聚合，统一至少550根warmup；每周一00:00 UTC使用此前完整7日ATR14/close均值选外部池可用币top25%，成员整周固定。只知道当时已有的价格；新上市/不足warmup时留现金，不要求当前已有一年历史。

## 执行和比较合同

下一open进场，初始止损2倍信号ATR，最多30个自然日；stop-first，止损跳空按更差open，移动保护次根才生效。往返固定20bp、每侧entry-notional10bp，不含资金费率。固定数量仅在入场时1x，不宣称实时名义/权益维持1x。

外部全部币与每周高波动子组都使用同一冻结外部池大小作为等资本分母，闲置现金不再分配。价格验证失败、无信号和未进入高波动组均保留现金。报告收盘盯市MDD，并将合约不利极值相加的压力MDD明确标作保守边界，不声称各币极值实际同步。

随机对照沿用主引擎：同币、同周期、同月、因果波动五桶、同方向；排除释放事件，至多3个不重复随机时点。继承固定seed20260909的稳定映射、相同风险/退出/20bp成本。超额只用有效共同覆盖，未匹配数单列。推断沿用月份聚类置换和bootstrap；每个周期每组每折固定5臂Holm；主要high_vol跨两个周期固定10臂Holm，缺失臂不缩小家族。不由p值或收益产生新的候选。

## 必交产物与解释

准备阶段输出身份/数据审计manifest和已验证的只读价格引用。评价阶段输出每币每周期事件、随机对照、收益曲线、无效/缺数据诊断；合并summary、逐月含现金组合、实际可用性表、周成员表及复现身份。必报事件/自然退出/边界/未匹配数、币覆盖、毛净均值中位数、胜率、PF、对照超额/CI/p、MFE/R/捕获、前3赢家贡献及剔除敏感性、组合收益/MDD。图表由哈希锁定的有界价格和事件时间生成。AUC不用于本迁移选择，未新增连续评分器，故不适用。

## 风险与诚实声明

这是近期存续品种中的历史跨币诊断，不是2025当时完整交易所名单，也不是从未见过的holdout。部分币、行情时段在其他研究或人工展示中已观察。2026-05-04仍为正式holdout边界；Owner已授权本实验使用，实际评价启动和重跑由主任务的暴露记录分别登记，不把固定字段伪装成累计次数。首轮库存搜索误匹配少量主池2023–25开发结果已披露，不用于外部币池或配置选择。

冻结清单分类来自2026-09-03；本轮不拉新metadata，也无法证明历史首次可见时间或已退市品种完整性。CSV丢失confirm标记的限制继承历史builder，关闭时间检查不等同于HTTP原始确认凭证。所有净收益称静态交易成本后净收益。

## 执行顺序与验证

先提交builder、测试和本预注册，再执行：

```bash
.venv/bin/python -m yoyo.evaluation.altcoin_transfer_research prepare --out-dir data/altcoin_transfer_20260909_v1
.venv/bin/python -m yoyo.evaluation.altcoin_transfer_research evaluate --prepared-dir data/altcoin_transfer_20260909_v1 --out-dir experiments/active/exp-imacd-altcoin-transfer-20260909-v1/results
```

两阶段均核对实际导入的所有本仓yoyo源码与同一个已提交HEAD。合成测试覆盖身份排除/缺文件、无历史长度筛选、价格错误/缺口/尾部拒绝并保留分母、有界前缀、已锁配置、缺失现金、月末归属、空事件和端到端复现。最终中文报告与HTML由主任务整合；任何结果都不自动升级线上。

## 运行前更正附记：周成员的决策时钟（待新锁）

主实验复核发现周成员应以信号收盘/下一open所属周判断，而非信号open所属周。主任务正在新目录重跑开发与审计，原结果不覆盖。上述旧selection SHA仅保留为本预注册草稿的来源记录，目前不得用于外部运行。外部builder显式 `SELECTION_CORRECTION_PENDING=True` 拒绝真实prepare/evaluate；待主任务提供纠正后的锁、精确允许臂和SHA后，在本附记记录替代身份，再提交代码并解除此门。外部尚未运行真实价格准备或收益计算。

### 更正完成：启用 clock_v2 开发锁

主任务已完成以信号收盘/下一open确定周成员的开发期重跑，并在任何外部真实准备或评价之前提供新锁。有效来源改为 `exp-imacd-altcoin-trends-20260909-v1/results_clock_v2/development/selection_lock.json`：旧SHA `3ccadfd45eaa7a8ce0bc0a1e031b4450b3bfc3ba2752615271f077dea2326e08` → 新SHA `c306749e038da29c0468d44407f378cada9079cc3d8305f5ddcc0b50f94ba951`。旧路径、旧结果及前述等待状态作为更正历史保留，不再是运行来源。

允许臂保持不变：1H为base、exit_chandelier、exit_fixed3r、exit_sma60、gate_box_break；4H为base、exit_chandelier、exit_fixed3r、exit_sma60、signal5。builder默认路径和SHA已替换，`SELECTION_CORRECTION_PENDING=False`；提交本更正后，才可执行原两阶段命令。运行时仍重新核对锁及其开发summary哈希。本更正未读取外部真实价格或收益，也未根据外部结果改选配置。
