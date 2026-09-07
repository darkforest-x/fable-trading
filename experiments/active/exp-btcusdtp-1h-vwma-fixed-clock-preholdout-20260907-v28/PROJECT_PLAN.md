# V28 — VWMA288固定时钟方向检验（标签前冻结）

## 单一问题

V26只把入口SMA40/HL2换成VWMA40/HL2，288母单冻结；V27已获得284完整三控支持。
现在检验该新入口群的4h方向延续是否同时覆盖20bp成本门、优于同币同月/UTC6h/ATR桶
随机背景。沿用V24标签、抽样和统计流程，不改形态/长度/成本/止损/小级别退出参数。
这是预先指定的开发区探索，不是独立验证、交易PnL、保证赚钱或授权生产。

## 先验分母与抽样

全部288母，四半年68/74/66/80；284有完整资格、4缺因果支持；母单不因缺对照删除。
读取V27之前先核SHA、builder来源、graph冻结、独立audit通过。原母全字段与V26一致。
只使用V27 eligible_edges15493和mother_support，禁止直接借用MILP容量见证当随机组。
用V24原生采样纯函数：组件以最小母ID排序、母ID排序、UTC候选ID先排序，
PCG64(20260907)单流对每组件候选仅置换一次，连续三控，全球时间不复用。
组件必须完全二部图且供给>=3倍母数，否则失败，不换seed/匹配键/贪心回退。
要求恰284母/852控制；抽样输出和RNG状态在读取任何raw前写sampling_frozen。

## 标签与价格界限

原5m archive只准先读全物理时钟，再核archiveSHA，然后仅物化pre2025 open_time/open。
不读取HLCV、不删除无效open行、不重采样，不读取2025+/holdout价格，不读旧退出路径。
E是K1完成后的真实下一开盘；H=(1,4,12,24)，只有4h主检验。E..E+H每根5m原生OPEN
完整且合法才有已知标签；缺失窗口保留unknown。标签是direction*(O[E+H]/O[E]-1)，
成本门减0.002，故四个H不是四个独立母。共1152case/3408control/1152paired行。
配对差必须case已知且三控全已知，不能补抽控制/删原母/把unknown填零。
固定时钟不执行K1止损，不能解释成实际净收益、胜率或可部署策略。

## 诊断与统计冻结

statistical-analysis流程：先保存4h全case和paired excess的n/未知/SD/分位数/IQR离群ID、
实际调用固定技能utility的Shapiro诊断；不按正态性切检验，不删除尾部。
主推断沿用V24：24个自然月整块bootstrap9999次，事件sum/count加权；95%percentile
linear CI；月总和单侧signflip9999次带+1。统计独立PCG64(20260907)流，先抽indices再signs；
两个主序列共享数组。依赖月间足够弱相关及零假设符号可交换；不是随机治疗精确检验。
使用已验证数值契约相同的本机python3（3.9.6/numpy2.0.2/pandas2.3.3/scipy1.13.1），
其已有seaborn可加载技能utility；项目venv缺该诊断导入依赖，不装库、不静默换统计法。

统计模块静态特化自V24，唯一代码变化是251→288、248→284、226→260和四fold分母；
测试逐字归一化比对旧源码，不运行时改旧模块全局量。旧模块与旧实验不改。
继续条件：两个主序列各至少260已知、均值>0、CI下界>0、p<.01，且四半年case成本门
均值均>0；它只决定是否值得继续，不接受盈利。1/12/24h只描述，无p/CI可挑最好窗口。
开发区在多轮研究中复用，未做跨所有实验家族多重选择校正；即便通过仍需独立验证。
无事后power。288母是冻结全样本支持门，不等同有80%检验力。

## 对照与诚实边界

同时报告V24旧SMA251和本次VWMA288全母成本门、各自配对case/背景/超额、四半年及未知。
这是不同入口群及不同控制组的描述对比，不把均值相减当配对改进或因果MA效应。
毛/成本门正比例仅称4h标签正比例；AUC/top-decile/排名置换不适用：没有模型排名。
单参考旧SMA是单特征基线；背景控制用相同方向、持有时钟、成本，无TP/SL障碍。
本轮不做新增115/删除78分组选择，不凭盈利结果改门；不自动继续退出微调。
若主门失败，记录“该参考替换未证实解决入口弱势”，而非换窗口称成功。

## 运行与产物

源码/配置/计划/测试注册并提交之后才跑；结果目录拒绝覆盖，故障单独保留。
独立saved audit应复核三控抽样、OPEN标签、月统计和来源链；未审不能称验收。
报告MD立即转HTML，含逐行表、分布/月份图、风险与源路径。源码/报告是CLI可复现
审计链，不另建重复notebook。holdout消耗0；TV、ACTIVE、forward及实盘都不动。

```bash
.venv/bin/python -m pytest -q tests/test_hourly_impulse_vwma_clock.py tests/test_hourly_impulse_fixed_clock.py tests/test_hourly_impulse_fixed_clock_research.py tests/test_hourly_impulse_fixed_clock_statistics.py tests/test_hourly_impulse_fixed_clock_analysis.py
git branch --show-current
python3 -m yoyo.evaluation.hourly_impulse_vwma_clock_research
```
