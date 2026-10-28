# 期末余额必须连同补款、结算原因和失效状态一起解释

- **问题**：allin3 页面显示 1000U 起步、期末 1,754,717U，容易被理解为一次本金在可执行策略下滚成175万。
- **死胡同**：只引用页面终值或原月度达标比例，既看不到中途补款，也无法识别最后一笔究竟是目标成交还是数据末结算；曲线存在不代表旧评分仍有效。本次未重跑交易来重复生成已经存在的证据。
- **有效路径**：联结原资金事件、原生逐笔账本和最新因果修正摘要。336笔路径包含5次净补款共4,527.237128U；最大已实现余额回撤99.9744%。最后ON单贡献522,744.517397U，reason=0表示样本末结算，尚未到达目标；最后两笔贡献期末余额57.8%。旧触价柱评分已被确认前视，图中必须明确标记失效。
- **通用规则**：解释高倍数终值时先追溯具体路径、实际现金流、版本有效性和主要贡献订单的退出原因。净补款、整份本金预算与交易利润保持三个不同口径；描述性样本不能充当随机对照或策略验证。
- **牵连**：`experiments/active/exp-strategy-booklet-paths-20261027-v1/allin3-ledger.csv.gz`；`yoyo/evaluation/strategy_booklet_paths.py`；`yoyo/evaluation/monday_limit_langlang.py`；`experiments/active/exp-monday-limit-paper-accounts-20261028-v1/summary.json`。见[补款不是交易利润](refilled-paper-equity-is-not-trading-profit.md)与[触价柱评分前视](the-touch-bar-score-leak-turned-4x-into-109x.md)。
