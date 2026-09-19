# V12：主高点＋局部回踩确认

Owner 2026-09-20 明确要求识别 ONE 截图白线并写 V12。保留 V11.2 文件；新 Pine 默认开启补充结构族，本周期和上级周期同步采用。只改结构识别，不改 V9、退出、成本、生产或监控配置。

新增结构族：A/B 仍是原 left12/right8 主高点；C 为 left2/right2 局部高点，B-C 至少 max(4,right+1) 根。原 A-B 间隔24、A-C跨度72、下降与回落、贴线、全历史实体/针刺/既往突破校验保持。B 必须在 C 确认收盘前已知；只在 C 当次确认事件找线，禁止未来主点到来后补认旧C。两族容量分开，跨族几何重复仍合并。

先提交 Pine、辅助族 Python 参考、测试和本案例 builder，后读取前轮冻结 ONE 输入。该 Python 参考只验证辅助族，不宣称整个合并 Pine 候选池 parity。旧族代码契约对照和候选去重冲突另检。

验收：合成序列覆盖确认延迟、无出生根突破、历史穿线、未知B不补认、gap、双轨去重、容量、关闭开关和截断重放；ONE A1066/B1182/C1189 应20:15确认，实际突破时间由数据计算；逐截断点检查过去事件不变。无盈利参数搜索、无收益优越性结论。AUC/收益/随机入场不适用：本轮验证识别契约，以已知几何的正负合成序列及前缀不变性为对照。

图契约：ONE/OKX 15m 原始 OHLC，2026-09-15 至09-17，趋势类K线＋固定AB线；全景和局部两面板，逐根数据不少于100根；单位USDT、北京时间，标明确认使用收盘时间，横轴K线开盘时间。独立PNG（用户此前要求交易图、不需HTML），Matplotlib，蓝色上涨/空心灰色下跌、橙色确认和突破、灰色参考线，标记形状和文字同时区分；PNG实际打开检查。

复现：`.venv/bin/python -m pytest -q tests/evaluation/test_spike_v12_local_touch.py tests/evaluation/test_spike_v12_pine_contract.py`；`.venv/bin/python -W ignore -m yoyo.evaluation.spike_v12_one_case`。
