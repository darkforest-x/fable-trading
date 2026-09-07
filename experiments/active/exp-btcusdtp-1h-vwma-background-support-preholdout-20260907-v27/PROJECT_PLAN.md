# V27 — 为VWMA288建立新的背景支持

## 问题与不变项

V26已冻结288入口、四半年68/74/66/80，仅通过样本支持。现在只建立这批入口的
背景资格与最大完整三控容量，不改变入口参数，不读收益或原始5m。40/HL2、
大实体/吞没形态、K1极值止损、72h期限和20bp成本不动。本阶段不随机分配，
容量见证不能冒充随机经济样本，也不能作为获利证明。

新实验保留全部288母的字段、时钟和顺序；不是沿用旧251母的248组三控。
同币BTC×决策UTC月×UTC6h块×因果ATR比例三分桶，与V23同三键；不随机切时间。
母自身进入支持须真实entry OPEN连续、风险/ATR合法、因果支持已知。
控制继承母方向和风险/ATR，不继承母OHLC、颜色或价格。

## 源码先行、仅保存数据

来源为V10 matching_frame与3份来源元数据/V4原母，加V26六份配置/收据/audit
和全部七表输出：共18项SHA锁定。V10来源函数核原14源码与pre2025截止；V26
核12源码、原SMA先复现再VWMA的收据及独立audit链接。本轮不重算raw5聚合。
源码/config/计划/测试先提交再读完整表；先检查三张表的UTC小时列、唯一/顺序/
pre2025，再读保存特征。运行前后再次核输入/源码SHA，results拒绝覆盖。
不读2025+价格、holdout、旧退出、收益、MFE、MAE，失败保留failure文件。

## 新参考与旧退出上下文分开

按V26保存参考完整首尾信号小时截取V10，允许V10有更长尾；区间内小时集合必须
精确相等，不能inner join把缺失藏起来。小时切片包含预热历史，不按折切点重置。
先核V10在共同域的SMA全部特征/OHLCV/ATR及派生小时段，再核SMA与VWMA共同字段。
ATR/close三分桶来自前720小时、最少168、排除当前根；逐字段复核而非看桶数相近。

原真实entry OPEN、原始源/entry段、known_entry_open、entry_source_continuous、
known_5m_*及management_source_segment_id逐字段保持。这些仍是SMA小周期退出
可观察性，不把5m退出改成VWMA。旧SMA1h参考快照单独前缀保存，供审计对照。

新背景的ma/ma_side/ma_slope_atr/cross_count24使用VWMA；known_hourly_colour、
unsigned_hourly_slope_sign随之重算。known_hourly_valid要求继承旧已知性且新VWMA
侧别/斜率/MA已知；不因换参考放行旧缺失源。由这些门重建matching_support。
raw_strict_body_cross及其本根/前一小时排除用VWMA；actual_mother_decision_excluded
使用全部新288母时钟。未知不得补零量/颜色/价格。保存资格改变诊断，不从中挑参数。

## 容量与停止

复用冻结V23 build_support_graph和allocate_support的实际图/求解/控制行，禁改旧文件。
每连通组件采用原MILP30s、完整三或零、控制时间全局不复用，状态/对偶/gap必须
验证最优；超时不回退贪心，不减少组数。先保存graph与support_frozen再开始求解。
旧allocate的251/226固定摘要不得裁决新母群；新wrapper明确舍弃旧摘要门，并用
288母且matched>=260（ceil90%）判断。核所有四半年与缺配原因，不删母。

支持不足终止，不读取该配置标签、不偷换匹配键。通过后才允许另行注册随机抽样
与经济研究；不把容量见证拿去假装随机样本。本阶段不执行那一后续标签步骤。

## 验证与报告

纯函数合成测试覆盖：精确UTC/一对一/内部缺口、旧SMA全字段、均匀量不变、新参考
颜色与贯穿、新母排除、桶因果与源可用性保留。旧图/容量测试继续跑；wrapper门
合成覆盖259/260边界、288分母、源码先行和故障留存。实际图/分配另独立复核。
报告含旧251与新288分母比较、分段支持、完整三控缺失原因、来源、风险与命令。
本阶段无收益/分类模型，AUC、p、top-decile、胜率、PF不适用，不能编造或写成零；
合成等参考还原旧表及损坏输入拒绝作为严格实现零假设，不代替经济对照。

使用experimental-design固定三键/分母，source-driven-development核pandas2.3.3
空键连接与SciPy1.13.1最优证书，data-quality审保存表和来源链。Python3.9.6/
NumPy2.0.2与项目环境不变，不安装DOE工具。保存CLI/CSV/tests可复核路径而非
重复notebook，最终MD立即转HTML并用canonical报告包装。盈利未达成，无生产变动。

```bash
.venv/bin/python -m pytest -q tests/test_hourly_impulse_vwma_background.py tests/test_hourly_impulse_vwma_background_research.py tests/test_hourly_impulse_background_support.py tests/test_hourly_impulse_matching_capacity.py
git branch --show-current
.venv/bin/python -m yoyo.evaluation.hourly_impulse_vwma_background_research
```
