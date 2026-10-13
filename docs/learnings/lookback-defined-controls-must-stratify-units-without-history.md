# 按回看特征定义的对照，必须单独处理没有回看历史的样本

- **问题**：检验 Bit浪浪 直播点名的 42 个币是否只是“本来就在涨”（exp-bitlanglang-picks-20261013-v1）。对照取同场“过去 30 天涨幅前 20%”的币随机抽。其中 8 次点名（ORDI ×4、TIA ×2、BIGTIME、BB）上线不满 30 天，算不出过去 30 天涨幅。
- **死胡同**：v1b 让这 8 次点名留在“点名组”的均值里，对照池却只能从有 30 天历史的老币里抽——新币永远进不了对照组。结果 p=0.0000、30 天点名 +38.5pp 对对照 +10.1pp，看起来扣掉动量后优势依然很大；同时中位数也只按老币算，和主评分口径不一致。这个“显著”其实是在拿新币的暴涨去比老币。
- **有效路径**：v1c 把样本按“有没有回看历史”拆开：老币点名（34 次）对同场涨幅前 20% 的老币，30 天 +22.8pp 对 +10.4pp，p 0.027（事后对照，不到 p<0.01）；新币点名（8 次、只有 4 个币）对同场其他 47 个新币，均值 +105pp 对 +17.7pp、中位数 −5.9pp，币太少不做检验。只去掉 ORDI，30 天平均就从 +38.5pp 降到 +26.3pp。头条数字主要来自动量和少数几个新币。
- **通用规则**：只要对照组是用回看特征（过去 N 天涨幅、波动率、排名、均线位置）定义的，先问“算不出这个特征的单位去哪了”。不能让它们只出现在处理组：要么单独成层、拿同类单位（同场其他新币）做对照，要么两组都剔除并报告剔除了多少。中位数、超额基线的口径也要和主评分一致。
- **牵连**：`yoyo/evaluation/bitlanglang_picks_v1.py` 的 `momentum()`（v1c）、`trailing_return()` 在不足 30 天时返回 None；结果 `experiments/active/exp-bitlanglang-picks-20261013-v1/results_v1/momentum_v1b.json`（保留作记录）与 `momentum_v1c.json`。相关：[精确对照分层为空时应保留候选](empty-exact-control-strata-must-stay-unmatched.md)、[池内指标看不见 beta](pool-internal-metrics-cannot-see-beta.md)、[排名窗必须在交易窗前闭合](symbol-ranking-window-must-end-before-the-trading-window.md)。
