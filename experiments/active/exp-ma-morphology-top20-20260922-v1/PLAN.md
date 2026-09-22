# A组模型：今日涨幅前20的模型检出检查

Owner 2026-09-22请求使用刚训练完的A模型跑今日涨幅榜，并明确选择OKX USDT永续、北京时间今日涨幅前20。三个周期为15m/30m/1H；不是训练或实盘准入。

- 冻结public tickers + live instruments + exchange time。排名为`last/sodUtc8-1`，保留24h涨幅作区别说明；不足20个正涨幅币种直接失败，不换交易所或榜单。
- 逐币每周期拉取北京时间当日零点前1224根及到快照时刻的原生确认K线；OHLCV仅写独立实验目录，不触碰VPS生产缓存。
- 使用A原best.pt，SHA `17aee5679b73dfd9183339c580c2caac323fc2b6c0759da38827834d72f2742b`。B仍在3060训练，当前推理在Mac进行。
- 从今日第一根收盘到快照前最后收盘，逐根扫描18/19根可见短窗；与A预9/core4或5/后5渲染一致，HL2 SMA/EMA20/60/120、1200预热、1280×742、visible_range_v1。只使用每窗右端已知数据，无数值形态预筛，无未来收益筛选，无GT框限制预测。
- 原生predict最小矩形补边、rect=True、imgsz1280、conf0.25、iou0.5，实际张量高768宽1280；不使用造成冲突的val额外16px补边。
- 每个窗保留零/非零预测，图中显示模型实际预测框；按预测核心时间去重仅用于阅览，同一行情多个窗不冒充独立事件。分别报告日内曾检出、最新收盘窗检出、覆盖不足与未检出。
- 这是一项当前榜单回看，不能说当时能事先选中这些币；core+5确认模型不能冒充盘口tip信号。置信度不等于盈利概率。
- A所用训练正图主要来自1/3/5分钟，15m较少、30m/1H极少；高周期输出缺乏充分训练覆盖，均待人工审图。本轮不调整阈值追求好看结果。
- 保留全部失败与负检出，交付Markdown报告、CSV/JSON及必要图集。训练资格、生产资格默认false；不改ACTIVE、监控或订单。

复现入口：先提交fetcher、scan实现及本plan；随后运行`python -m yoyo.data.ma_gainers_inputs`，再运行`python -m yoyo.evaluation.ma_gainers_model_scan --inputs .../inputs --out .../results --model .../arm_A_best.pt --device cpu`。
