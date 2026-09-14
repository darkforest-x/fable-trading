# ICT时段作为V8开仓过滤
Owner要求关注ICTKillzones[28Trades]，只在第二张图London/Londonlunch/NewYork时段开仓，并授权研究。
仅改变开仓时段；固定V8信号、次开成交、原初始SL/退出及20bp名义往返成本。本轮不读取2026-05-04起holdout，不重新使用9月价格评分本配置。历史上下文固定至2026-04-30。
当前脚本UI在2026-09-13纽约夏令时证据：北京时间13:57London无值、14:00London有值；17:00切lunch、20:00切NY、23:00NY无值。三者对应纽约[02,05)、[05,08)、[08,11)。
建模使用IANA America/New_York跨年DST，冬季同钟面为明确假设，受保护代码冬季实现未直接验证。周末包括，界限左闭右开；按计划nextopen入场时间判定；不时段外延迟排队、不每日重置债务、时段结束不强平。所有rawV6反向仍可触发退出。
六组all/london/lunch/new_york/union/london_new_york。原始V8及此前冻结net1R费用保本next_bar/ohlc/olhc，后两为路径假设并非真实tick。每个退出组只改变时段这一变量。
首要假设union；开发2023-08至2024末，选择netnextbar各时段自然笔数>=50且平均净R最高者，哪怕负值也诚实记录；selection先落地再评估2025、2026前4月。固定全部组后期表只是稳定性比较，不依据后期更换冠军。2023-08至2026-04连续账户另列，用于资金路径不作为第四个独立证据。
每组全量单仓串行R账本不设容量过滤，同时单独运行1000U/价格风险1U/10倍容量固定与净亏翻倍整轮回本重置两种资金账户，必须标实际运行终点，不能把停机后低样本胜率当全期。
方向性表附同币/UTC月/纽约入场小时/周末状态/此前120bar相对波动桶9随机入场；独立自然出场并非可执行随机账户。缺配对/删失拒样逐笔保留。按月超额净R符号9999次置换，未经多重比较校正的p只作描述，不据此promote。
AUC、topdecile、val标签正类率无预测模型不适用，替代为实际胜率、每笔净R、毛净3R兑现、BE、完整SL与净亏连续最长、同窗口全天及匹配随机对照。不能用少量交易最长连亏下降承诺<=6。
先提交builder/tests/config再运行：PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m yoyo.evaluation.spike_ict_session_study
生成MD立即HTML交付，记录全部结果/限制/sourcehash、Notion、learning。此前Pine图18笔与Python账本20笔尚未逐项映射，当前研究沿用明确的Python次开成交定义，不宣称图表成交parity。
