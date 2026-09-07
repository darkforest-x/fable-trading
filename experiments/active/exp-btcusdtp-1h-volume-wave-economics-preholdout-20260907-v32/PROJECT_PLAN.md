# V32：冻结 V31 前置方向量波门的经济检验

## 用户目标与本轮边界

Owner 本轮“继续下一步”。先回答 V31 留下的100个入口是否具有扣20bp成本后的方向优势。
这是历史2023—2024开发集的重复使用，第1次对该冻结V31配置评估保存的经济标签；不是独立样本外验收。
2025+、holdout（>=2026-05-04）本轮消耗0次；不改 TradingView、退出逻辑、ACTIVE、任何实盘状态。

## 冻结假设（读取本轮分组收益前）

唯一对比变量是 V31 的二值门，数字量波 EMA20/EMA5，不调整其周期、符号、热身100或原SMA40入口。
K1开盘T，决策E=T+1h；只用前两小时完成的量波，direction*(wave[T-1h]-wave[T-2h])>0。
原251母事件 / 744原随机对照全保留。case accepted/abstain/unknown=100/148/3；
control=401/343/0。100 accepted 对应 ALL300 原对照，不得仅拿其中158 accepted来评价入口优势。

固定观察端点 E+4h 为唯一主检验，1/12/24h仅描述，不择优当主结果。
gross=direction*(open[E+H]-open[E])/open[E]，net=gross-0.002。
不是带止损、资金费、仓位重叠或成交路径的实盘PnL；不存在本轮TP/SL优化。

## 四个主对比与零假设

1. accepted_cost：100 accepted 的4h净变化均值，零假设<=0。
2. accepted_excess：上述100逐母减 ALL3 原对照的净变化均值，零假设<=0。
3. policy_excess：原母门策略收益减原三对照在各自决策时应用同门后的策略均值，零假设<=0。
4. policy_delta：原母门策略收益减该母未过滤净变化，零假设<=0。

已知弃权恰为0，即便弃用的未来标签缺失；未知门仍NaN，不当作弃权；入选但未来标签未知也为NaN。
所有原身份、原配对、全部H保留，完整三个对照缺任一不可偷换成两个对照。
月块为原母month，保持24个月，不随机切分、不重抽对照；四个半年均独立描述。

## 统计、分解与停止规则

PCG64 seed20260907，先9999次24月有放回整月抽样，再9999次24月符号翻转。
CI95为逐笔加权均值的月块bootstrap百分位区间，NumPy2 linear；
单侧greater符号检验用月sum和plus-one修正，四项Holm校正。
继续门：四项均值>0、四项CI下界>0、四项Holm p<0.01且四半年accepted均值都>0。
否则记录 not_supported，不改阈值、不试反向、不挑时段、不读新年代“救回”。

保留所有异常值；Shapiro-Wilk仅分布诊断，不据此换检验。
报告必须说明少交易的改进拆为避免手续费与避免毛亏，分母为相同已知机会。
失败分层固定为毛亏、毛盈不足成本、净盈，gross=0独列；半年/月/方向只描述，不拟合规则。
报告V24未过滤基线与V30旧共振对照，所有已完成负面结果不得隐去。
AUC/top-decile不适用于固定二值门（无训练/排名分数）；用原匹配随机对照、四项效应CI/p作为对应检验。
同币×母月份×原波动桶控制保持V24冻结身份，禁止重采样。
多轮开发集选择偏差未被本轮Holm4覆盖，不能将p值当独立发现或盈利保证。

## 运行与验证

先提交 core、runner、独立audit、测试、本计划、配置，再运行一次研究。
runner先验证哈希、源提交时间、V31盲态与完整时钟，依赖smoke后才读取标签。
独立audit不调用经济core：保存端点重算3980行、1004个母×H配对、四项推断与描述统计，并独立检查wave时钟/分组。
主环境数值契约 NumPy2.0.2 / pandas2.3.3；现有系统Python可载入统计技能依赖，不新增依赖。
Freqtrade2026.8本机可用，但本轮是固定时钟入口诊断；通过后才值得另做真实退出/撮合假设交叉验证。

```bash
git branch --show-current
.venv/bin/python -m pytest -q tests/test_hourly_impulse_volume_wave_economics.py tests/test_hourly_impulse_volume_wave_economic_research.py tests/test_audit_hourly_impulse_volume_wave_economics_v32.py
python3 -m yoyo.evaluation.hourly_impulse_volume_wave_economic_research
python3 scripts/audit_hourly_impulse_volume_wave_economics_v32.py
```

已存在results时拒绝覆盖。复算审计用 --check-only。新材料/新读取授权前不扩范围。
MD报告完成立即运行scripts/md_to_html.py；交付HTML；图表使用有实际查询来源的canonical artifact封装。
