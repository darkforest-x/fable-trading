# ETH3m 九月逐笔核对
Owner要求北京时间2026-09-01 00:00至2026-09-14 21:19所有交易，确认OKX。
本次是冻结规则的描述性账本核对，每个列出配置第1次专项消耗holdout；并非新的盲验收，不调参。
最后完整3m bar open UTC13:15，close13:18（北京时间21:18）；截止时未完整13:18bar不读OHLC。
保留原始全量prefix，使用仓库fetcher公共GET传输有界补齐；只写隔离审计压缩证据，不改行情缓存/VPS。
原始V8全历史串行状态连续进入窗口，输出窗口内新开及跨入窗口持仓，明确carry-in；
费用保本三种冻结时序从期初空仓单仓模拟，全信号列未开仓状态；不使用1000U容量过滤截短账本。
净1R目标、20bp成本、保护价多entry*1.002/空entry*.998、有利1tick才触发；原初始SL/raw反向/2R+4ATR跟踪不改。
3m OHLC无法给真实分钟内轨迹，两个路径是情景。原始有利R上下范围不冒充最高浮盈已知时间。
交付全部候选、原始和各时序交易CSV、逐K原始证据及HTML可选交易图。
先提交builder/config，再登记holdout读取回执并获取数据；源完整性、截止、时区、原始独立回放和串行账本一致性验证。
本轮无排序/预测模型或优势检验，AUC/top-decile/随机入场对照不适用；以全量匹配与价格现金重算零误差为审计零假设，不据此声称策略有效。
复现：.venv/bin/python -m yoyo.evaluation.spike_september_trade_audit fetch
然后 .venv/bin/python -m yoyo.evaluation.spike_september_trade_audit run
然后 .venv/bin/python scripts/report_spike_september_trade_audit.py
已有证据禁止覆盖；复现run读取固定SHA快照，无需再次下载或另挑参数。
