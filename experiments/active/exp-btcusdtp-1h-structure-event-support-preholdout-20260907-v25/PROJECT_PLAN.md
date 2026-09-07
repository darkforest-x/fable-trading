# V25 — K1结构首次建立/反转事件：保存特征支持审计

## 问题、单位与唯一变化

V24已否定当前固定251入口具有已验证的4h持续性优势；盈利目标仍未完成。
V20持久同向结构状态也已失败。本轮只检查新问题是否有足够样本继续检验：
同一根K1是否首次建立或反转已确认结构，而非仅处于过去建立的同向状态。
这仍属于旧结构家族，不是新独立因子，也不是任意同向BOS/趋势延续突破。

原251case与V24随机744control、原3或0成员组全部保留。单位为own request，
不是标签时钟数；没有新的随机分配，不读取任何收益标签。源数据2023–2024已反复
开发使用。以各请求自己的signal_time/decision_time/direction判断，不把case门复制给control。

## 事件公式与可见时钟

沿用已冻结的10左/10右、21完整小时、含等高等低的Python pivot近似。
pivot在右侧10根全部收盘后才可用，不能按中心bar回画时间使用。
相邻小时缺失会清空结构状态与pivot；同价pivot替换仍按原稳定价格保护处理。
state已经同向时再次突破不产生事件；本根必须首次建立或反转方向。

只有精确own signal_time所在完整小时可用于请求，decision_time=signal_time+1h，
structure_available_at必须等于decision_time，所有非空确认时钟不得晚于该时刻。
缺该小时或structure_known=false（包括no_confirmed_break）保持unknown；
known时仅structure_break_on_k1=true且structure_break_direction=own direction为accepted，
其他known为abstain。禁止asof、邻近时钟、前填充与将unknown改成abstain凑覆盖。

## 输入与来源门

config/runner的INPUTS冻结V24 case/control requests、random allocation/assignments、
started/sampling_frozen与V20 hourly_trace/context_frozen/started/失败与恢复元数据SHA。
不使用V24 label summary、labels、OPEN prefix或V20经济结果/episodes。
V20旧失败发生于特征冻结之后；必须核旧首次与恢复检查点指向同一个冻结特征SHA，
不能只看到旧failure就丢弃，也不能忽略它假称无失败。只验其特征来源链，不读取旧收益。

先验证本轮源码/config/本计划已提交，输出results不存在，再写started。
核输入SHA与原来源commit bytes、来源时序和冻结哈希；逐份核V24抽样文件归属。
完整小时trace先只读时钟检查UTC、唯一、排序、hour网格与pre2025范围，再materialize
保存的OHLC/structure字段。不得读原始5m归档、下载数据或选择2025+价格。
按冻结原函数重建保存小时结构逐字段parity；这是保存trace一致性检查，不是独立raw重建。

当前已知输入规模：251case、744controls；case每半年55/66/55/75；
V24支持248组三控，3无对照保留。继承E严格位于原fold起点至end−72h之前。
所有请求ID、方向、mother/slot、时钟、原组不变，不按门结果重抽或删除。

## 计数、分母与冻结的继续条件

保存case/control完整上下文、原251母配对支持、case/control×全体/四半年/
双方向/24个月计数，零事件月份和半年度也必须存在。主支持门仅用全部251case：
accepted>=80；每半年>=12；至少12个活跃月；每半年至少3个活跃月。
活跃月是有至少1个accepted CASE的月，control的事件不能贡献case门。
这是事前实务样本屏障，不是统计功效保证，更不是盈利条件。

完整known组要求原assigned3、case known和三个control全部known；known包含
accepted与abstain，不能要求控制也交易才算有对照。case accepted但一control unknown
保留自身accepted，组比较unknown；原无对照也保留自身case支持。
分别报完整known/原251、完整known/原matched248、accepted且完整known/全部accepted；
分母0写null而非0%。门内实际控制accepted/abstain/unknown另列，不混同配对完整性。

写support_frozen，再生成summary，记录输出哈希；异常保存failure与已有证据，
不覆盖、不自动重启。本CLI永远不进入经济计算，即使support_pass也只结束本阶段。
若任一支持门失败，结果为support_insufficient，不读该条件新收益、不改长度/阈值/
最少样本/月份、不把“未评估盈利”说成“已证明亏损”。若全通过，另预注册经济阶段。

## 验证、交付与限制

合成测试覆盖exact join、重复/null时钟、future pivot、旧状态非事件、初次建立/反转、
gap、unknown、零月、原三控/无重复/配对未知和所有边界。支持结果另做独立保存小时重算；
若主函数重放成功不等于独立算法已验证，报告需区分。无分类器，AUC/top-decile/PF/
收益p值不适用；严格实现负对照是破坏上述因果/身份契约时必须失败的合成输入。

技术HTML报告：先结论与定义，再全体/时间/背景支持表、因果语义与来源、验证限制、
风险及下一步。表用于精确审计，若简单计数画图不能增加可解释性则不额外画图；
不新增notebook副本，因为可复现CLI/纯函数/测试/CSV是本仓要求的可复核路径。
报告源码写完立即md_to_html，再用canonical build-report HTML包装和官方验证。

使用experimental-design固定单变量/分母和继续屏障，analyze-data-quality检查时钟/
来源/未知，source-driven-development核pandas2.3.3（NumPy2.0.2、Python3.9.6）API。
原随机分配已在V24冻结，本轮不调用DOE安装或重排时间数据，不安装新依赖。
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_csv.html

无holdout消耗、实盘/TV/ACTIVE/frozen/训练/依赖/仓库分支变动。
全目标仍要求扣成本稳定正收益、匹配对照优势和独立时间/前向验证，不能用支持通过替代。
