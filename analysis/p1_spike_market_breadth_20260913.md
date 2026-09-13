# SPIKE 市场广度：冻结候选的匹配随机对照整合报告

## 结论

冻结的 `joint_delta_60m > 0` **应拒绝作为统一硬过滤**：6 个 cohort 的 matched 配对差值净R跨组合方向翻转，全部 6 个 paired p 均不显著（最小 p=0.2964），且至少一个 cohort 丢失了原有 ≥10R 候选。此结论只拒绝这条冻结硬过滤；不构成任何上线、通知、训练或仓位调整建议。

## 范围与证据边界

- 阶段一开发窗口：`2024-09-10T00:00:00+00:00` 至 `2025-09-10T00:00:00+00:00`（右端排除）。本整合器没有读取 holdout、市场面板、候选明细或原始逐笔大表；`candidate_context.csv.gz` 仅以字节 SHA-256 核验，未解析行内容。
- matched manifest 的候选与来源 SHA 分别与阶段一的 `candidate_context.csv.gz`、`source_manifest.csv` 一致；阶段一 manifest 也再次固定了两者。报告器还逐字节核验 `matched_control_pairs.csv.gz`、`matched_control_summary.csv`、`control_receipts.csv` 和当前 matched-controls 生成器代码身份；pairs 与 receipts 不解析。随机化种子为 `0`，所有展示行的配对显著性单位均已核验为日历月。
- 阶段一 manifest 记录 `holdout_consumed=True`；本报告不把这轮开发期读作新的 holdout 消耗，也不据此声称独立样本外验证。

## 各周期基线（共同执行的已实现结果）

| 变体 | 周期(分) | 候选 | 已平仓 | 均值净R | 中位净R | 胜率 | ≥10R |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1_common_execution_long | 30 | 653 | 653 | -0.040 | -1.010 | 29.7% | 0.3% |
| v1_common_execution_long | 60 | 427 | 427 | -0.378 | -1.019 | 18.5% | 0.5% |
| v1_common_execution_long | 240 | 74 | 74 | 0.196 | -1.008 | 33.8% | 2.7% |
| v7_bb_long | 30 | 8,513 | 8,513 | 0.082 | -1.037 | 30.1% | 0.9% |
| v7_bb_long | 60 | 4,534 | 4,534 | -0.078 | -1.027 | 29.4% | 0.7% |
| v7_bb_long | 240 | 958 | 958 | 0.438 | -1.011 | 36.6% | 1.1% |

## 冻结单变量候选：`joint_delta_60m > 0`

| 变体 | 周期(分) | 基线候选 | 保留候选 | 保留率 | 均值净R | 胜率 | ≥10R保留率 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1_common_execution_long | 30 | 653 | 475 | 72.7% | 0.071 | 31.6% | 100.0% |
| v1_common_execution_long | 60 | 427 | 317 | 74.2% | -0.337 | 19.6% | 100.0% |
| v1_common_execution_long | 240 | 74 | 33 | 44.6% | -0.567 | 24.2% | 0.0% |
| v7_bb_long | 30 | 8,513 | 4,778 | 56.1% | 0.044 | 29.7% | 53.3% |
| v7_bb_long | 60 | 4,534 | 2,127 | 46.9% | -0.118 | 30.2% | 43.3% |
| v7_bb_long | 240 | 958 | 452 | 47.2% | 0.150 | 29.6% | 18.2% |

阶段一只冻结这一条单变量候选；没有将广度水平、密度、量价扩张、BTC/ETH 背景或其他切片叠加成新规则。

## 匹配随机对照：基线与冻结规则

基线 summary 合计目标 `15,159`、匹配 `15,056`，动态匹配率 `99.3%`。

| 变体 | 周期(分) | 队列 | 目标 | 匹配 | 匹配率 | 目标均值净R | 对照均值净R | 配对差值净R | 月块 sign-flip p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1_common_execution_long | 30 | 基线 | 653 | 652 | 99.8% | -0.039 | -0.112 | 0.073 | 0.7614 |
| v1_common_execution_long | 30 | 冻结规则：delta_60m > 0 | 475 | 475 | 100.0% | 0.071 | 0.013 | 0.057 | 0.7161 |
| v1_common_execution_long | 60 | 基线 | 427 | 427 | 100.0% | -0.378 | -0.489 | 0.112 | 0.9775 |
| v1_common_execution_long | 60 | 冻结规则：delta_60m > 0 | 317 | 317 | 100.0% | -0.337 | -0.676 | 0.339 | 0.7212 |
| v1_common_execution_long | 240 | 基线 | 74 | 71 | 95.9% | 0.247 | 0.790 | -0.543 | 0.0808 |
| v1_common_execution_long | 240 | 冻结规则：delta_60m > 0 | 33 | 31 | 93.9% | -0.538 | -0.213 | -0.325 | 0.2964 |
| v7_bb_long | 30 | 基线 | 8,513 | 8,469 | 99.5% | 0.078 | -0.112 | 0.190 | 0.234 |
| v7_bb_long | 30 | 冻结规则：delta_60m > 0 | 4,778 | 4,750 | 99.4% | 0.034 | -0.133 | 0.167 | 0.3202 |
| v7_bb_long | 60 | 基线 | 4,534 | 4,493 | 99.1% | -0.073 | -0.162 | 0.089 | 0.5155 |
| v7_bb_long | 60 | 冻结规则：delta_60m > 0 | 2,127 | 2,103 | 98.9% | -0.114 | -0.223 | 0.110 | 0.4733 |
| v7_bb_long | 240 | 基线 | 958 | 944 | 98.5% | 0.459 | 0.515 | -0.056 | 0.7909 |
| v7_bb_long | 240 | 冻结规则：delta_60m > 0 | 452 | 444 | 98.2% | 0.172 | 0.279 | -0.107 | 0.4806 |

未匹配原因仍保留在 `matched_control_summary.csv`；低于 100% 的匹配率不得被解释成对照组支持。

## 其他单变量：matched top/bottom 对比（描述性）

| 变体 | 周期(分) | 变量 | 分位 | 候选 | 匹配 | 匹配率 | 目标均值净R | 对照均值净R | 配对差值净R | 月块 sign-flip p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1_common_execution_long | 30 | btc_return_30m | bottom_quartile | 164 | 164 | 100.0% | -0.405 | -0.586 | 0.181 | 0.7578 |
| v1_common_execution_long | 30 | btc_return_30m | top_quartile | 164 | 164 | 100.0% | -0.003 | -0.202 | 0.199 | 0.3274 |
| v1_common_execution_long | 30 | eth_return_30m | bottom_quartile | 164 | 164 | 100.0% | -0.257 | -0.516 | 0.259 | 0.7783 |
| v1_common_execution_long | 30 | eth_return_30m | top_quartile | 164 | 164 | 100.0% | 0.159 | -0.114 | 0.273 | 0.3712 |
| v1_common_execution_long | 30 | fast_breadth | bottom_quartile | 164 | 163 | 99.4% | -0.427 | -0.534 | 0.107 | 0.878 |
| v1_common_execution_long | 30 | fast_breadth | top_quartile | 164 | 164 | 100.0% | 0.037 | -0.158 | 0.195 | 0.4564 |
| v1_common_execution_long | 30 | joint_breadth | bottom_quartile | 164 | 163 | 99.4% | -0.420 | -0.492 | 0.072 | 0.9199 |
| v1_common_execution_long | 30 | joint_breadth | top_quartile | 164 | 164 | 100.0% | -0.060 | -0.220 | 0.160 | 0.5852 |
| v1_common_execution_long | 30 | joint_delta_30m | bottom_quartile | 164 | 164 | 100.0% | -0.227 | 0.004 | -0.232 | 0.3849 |
| v1_common_execution_long | 30 | joint_delta_30m | top_quartile | 164 | 164 | 100.0% | -0.005 | 0.300 | -0.305 | 0.7274 |
| v1_common_execution_long | 30 | launch_density_1h | bottom_quartile | 165 | 165 | 100.0% | -0.265 | 0.169 | -0.434 | 0.5172 |
| v1_common_execution_long | 30 | launch_density_1h | top_quartile | 164 | 164 | 100.0% | -0.019 | -0.254 | 0.235 | 0.4838 |
| v1_common_execution_long | 30 | return_iqr_30m | bottom_quartile | 164 | 163 | 99.4% | -0.212 | 0.214 | -0.427 | 0.7235 |
| v1_common_execution_long | 30 | return_iqr_30m | top_quartile | 164 | 164 | 100.0% | -0.069 | -0.104 | 0.035 | 0.9021 |
| v1_common_execution_long | 30 | return_median_30m | bottom_quartile | 164 | 164 | 100.0% | -0.388 | -0.565 | 0.177 | 0.8132 |
| v1_common_execution_long | 30 | return_median_30m | top_quartile | 164 | 164 | 100.0% | 0.110 | 0.001 | 0.110 | 0.7005 |
| v1_common_execution_long | 30 | rv_tr_atr_expansion | bottom_quartile | 164 | 163 | 99.4% | -0.226 | -0.124 | -0.102 | 0.6082 |
| v1_common_execution_long | 30 | rv_tr_atr_expansion | top_quartile | 166 | 166 | 100.0% | -0.226 | -0.617 | 0.390 | 0.6436 |
| v1_common_execution_long | 30 | up_participation | bottom_quartile | 164 | 164 | 100.0% | -0.390 | -0.553 | 0.163 | 0.8287 |
| v1_common_execution_long | 30 | up_participation | top_quartile | 166 | 166 | 100.0% | 0.040 | -0.113 | 0.153 | 0.5709 |
| v1_common_execution_long | 60 | btc_return_30m | bottom_quartile | 107 | 107 | 100.0% | -0.440 | 0.050 | -0.490 | 0.0115 |
| v1_common_execution_long | 60 | btc_return_30m | top_quartile | 134 | 134 | 100.0% | -0.648 | -0.281 | -0.367 | 0.2812 |
| v1_common_execution_long | 60 | eth_return_30m | bottom_quartile | 107 | 107 | 100.0% | -0.174 | 0.008 | -0.182 | 0.3006 |
| v1_common_execution_long | 60 | eth_return_30m | top_quartile | 107 | 107 | 100.0% | -0.516 | -0.254 | -0.261 | 0.7495 |
| v1_common_execution_long | 60 | fast_breadth | bottom_quartile | 107 | 107 | 100.0% | -0.307 | 0.005 | -0.311 | 0.145 |
| v1_common_execution_long | 60 | fast_breadth | top_quartile | 135 | 135 | 100.0% | -0.580 | -0.297 | -0.283 | 0.5644 |
| v1_common_execution_long | 60 | joint_breadth | bottom_quartile | 107 | 107 | 100.0% | -0.215 | 0.136 | -0.351 | 0.1862 |
| v1_common_execution_long | 60 | joint_breadth | top_quartile | 118 | 118 | 100.0% | -0.620 | -0.325 | -0.295 | 0.7773 |
| v1_common_execution_long | 60 | joint_delta_30m | bottom_quartile | 107 | 107 | 100.0% | -0.326 | -1.739 | 1.413 | 0.3603 |
| v1_common_execution_long | 60 | joint_delta_30m | top_quartile | 107 | 107 | 100.0% | -0.476 | 0.098 | -0.574 | 0.0016 |
| v1_common_execution_long | 60 | launch_density_1h | bottom_quartile | 109 | 109 | 100.0% | -0.414 | 0.011 | -0.424 | 0.0046 |
| v1_common_execution_long | 60 | launch_density_1h | top_quartile | 115 | 115 | 100.0% | -0.489 | -0.303 | -0.186 | 0.8836 |
| v1_common_execution_long | 60 | return_iqr_30m | bottom_quartile | 107 | 107 | 100.0% | -0.196 | -1.395 | 1.200 | 0.6587 |
| v1_common_execution_long | 60 | return_iqr_30m | top_quartile | 123 | 123 | 100.0% | -0.605 | -0.270 | -0.335 | 0.5668 |
| v1_common_execution_long | 60 | return_median_30m | bottom_quartile | 107 | 107 | 100.0% | -0.237 | -1.375 | 1.138 | 0.6544 |
| v1_common_execution_long | 60 | return_median_30m | top_quartile | 120 | 120 | 100.0% | -0.563 | -0.260 | -0.303 | 0.6541 |
| v1_common_execution_long | 60 | rv_tr_atr_expansion | bottom_quartile | 108 | 108 | 100.0% | -0.167 | -0.143 | -0.023 | 0.9292 |
| v1_common_execution_long | 60 | rv_tr_atr_expansion | top_quartile | 126 | 126 | 100.0% | -0.599 | -0.285 | -0.315 | 0.635 |
| v1_common_execution_long | 60 | up_participation | bottom_quartile | 107 | 107 | 100.0% | -0.237 | -1.378 | 1.141 | 0.6542 |
| v1_common_execution_long | 60 | up_participation | top_quartile | 107 | 107 | 100.0% | -0.581 | -0.237 | -0.344 | 0.6804 |
| v1_common_execution_long | 240 | btc_return_30m | bottom_quartile | 19 | 19 | 100.0% | -0.094 | 0.256 | -0.350 | 0.5136 |
| v1_common_execution_long | 240 | btc_return_30m | top_quartile | 19 | 16 | 84.2% | -0.534 | -0.711 | 0.178 | 0.5412 |
| v1_common_execution_long | 240 | eth_return_30m | bottom_quartile | 20 | 20 | 100.0% | -0.218 | 0.435 | -0.654 | 0.2716 |
| v1_common_execution_long | 240 | eth_return_30m | top_quartile | 19 | 17 | 89.5% | -0.408 | -0.409 | 0.001 | 1 |
| v1_common_execution_long | 240 | fast_breadth | bottom_quartile | 19 | 18 | 94.7% | 0.055 | -0.023 | 0.078 | 0.874 |
| v1_common_execution_long | 240 | fast_breadth | top_quartile | 19 | 18 | 94.7% | -0.089 | 0.395 | -0.484 | 0.3058 |
| v1_common_execution_long | 240 | joint_breadth | bottom_quartile | 21 | 20 | 95.2% | 0.353 | 1.477 | -1.123 | 0.1565 |
| v1_common_execution_long | 240 | joint_breadth | top_quartile | 19 | 17 | 89.5% | -0.265 | -0.051 | -0.214 | 0.6894 |
| v1_common_execution_long | 240 | joint_delta_30m | bottom_quartile | 19 | 19 | 100.0% | 0.326 | 1.725 | -1.399 | 0.048 |
| v1_common_execution_long | 240 | joint_delta_30m | top_quartile | 19 | 18 | 94.7% | -0.248 | 0.234 | -0.482 | 0.3057 |
| v1_common_execution_long | 240 | launch_density_1h | bottom_quartile | 21 | 20 | 95.2% | 0.006 | 0.029 | -0.023 | 0.9729 |
| v1_common_execution_long | 240 | launch_density_1h | top_quartile | 21 | 20 | 95.2% | 0.013 | 0.129 | -0.115 | 0.88 |
| v1_common_execution_long | 240 | return_iqr_30m | bottom_quartile | 19 | 19 | 100.0% | 0.621 | 1.737 | -1.116 | 0.0907 |
| v1_common_execution_long | 240 | return_iqr_30m | top_quartile | 19 | 17 | 89.5% | -0.334 | -0.366 | 0.032 | 0.9121 |
| v1_common_execution_long | 240 | return_median_30m | bottom_quartile | 21 | 21 | 100.0% | 0.484 | 1.519 | -1.035 | 0.1863 |
| v1_common_execution_long | 240 | return_median_30m | top_quartile | 19 | 17 | 89.5% | -0.543 | -0.847 | 0.304 | 0.3807 |
| v1_common_execution_long | 240 | rv_tr_atr_expansion | bottom_quartile | 19 | 18 | 94.7% | 0.762 | 1.955 | -1.193 | 0.1949 |
| v1_common_execution_long | 240 | rv_tr_atr_expansion | top_quartile | 19 | 18 | 94.7% | 0.936 | -0.020 | 0.956 | 0.3926 |
| v1_common_execution_long | 240 | up_participation | bottom_quartile | 19 | 19 | 100.0% | 0.800 | 1.787 | -0.987 | 0.2601 |
| v1_common_execution_long | 240 | up_participation | top_quartile | 19 | 17 | 89.5% | -0.123 | -0.283 | 0.161 | 0.4975 |
| v7_bb_long | 30 | btc_return_30m | bottom_quartile | 2,131 | 2,117 | 99.3% | 0.093 | 0.011 | 0.081 | 0.7121 |
| v7_bb_long | 30 | btc_return_30m | top_quartile | 2,129 | 2,125 | 99.8% | -0.041 | -0.185 | 0.144 | 0.2518 |
| v7_bb_long | 30 | eth_return_30m | bottom_quartile | 2,136 | 2,124 | 99.4% | 0.091 | -0.139 | 0.231 | 0.3785 |
| v7_bb_long | 30 | eth_return_30m | top_quartile | 2,153 | 2,150 | 99.9% | -0.115 | -0.079 | -0.036 | 0.8439 |
| v7_bb_long | 30 | fast_breadth | bottom_quartile | 2,129 | 2,118 | 99.5% | -0.011 | -0.209 | 0.198 | 0.4848 |
| v7_bb_long | 30 | fast_breadth | top_quartile | 2,133 | 2,132 | 100.0% | -0.216 | -0.067 | -0.149 | 0.2356 |
| v7_bb_long | 30 | joint_breadth | bottom_quartile | 2,132 | 2,120 | 99.4% | -0.006 | -0.209 | 0.203 | 0.4118 |
| v7_bb_long | 30 | joint_breadth | top_quartile | 2,138 | 2,134 | 99.8% | -0.142 | -0.105 | -0.038 | 0.6643 |
| v7_bb_long | 30 | joint_delta_30m | bottom_quartile | 2,129 | 2,117 | 99.4% | 0.255 | -0.084 | 0.339 | 0.2163 |
| v7_bb_long | 30 | joint_delta_30m | top_quartile | 2,132 | 2,119 | 99.3% | 0.225 | -0.076 | 0.301 | 0.1676 |
| v7_bb_long | 30 | launch_density_1h | bottom_quartile | 2,221 | 2,210 | 99.5% | -0.074 | -0.146 | 0.072 | 0.6196 |
| v7_bb_long | 30 | launch_density_1h | top_quartile | 2,186 | 2,175 | 99.5% | 0.120 | -0.100 | 0.221 | 0.662 |
| v7_bb_long | 30 | return_iqr_30m | bottom_quartile | 2,130 | 2,107 | 98.9% | 0.304 | -0.163 | 0.467 | 0.0775 |
| v7_bb_long | 30 | return_iqr_30m | top_quartile | 2,132 | 2,130 | 99.9% | -0.235 | -0.066 | -0.170 | 0.0986 |
| v7_bb_long | 30 | return_median_30m | bottom_quartile | 2,129 | 2,117 | 99.4% | 0.023 | -0.128 | 0.151 | 0.4838 |
| v7_bb_long | 30 | return_median_30m | top_quartile | 2,129 | 2,126 | 99.9% | -0.118 | -0.067 | -0.051 | 0.5462 |
| v7_bb_long | 30 | rv_tr_atr_expansion | bottom_quartile | 2,129 | 2,115 | 99.3% | 0.187 | -0.101 | 0.287 | 0.2544 |
| v7_bb_long | 30 | rv_tr_atr_expansion | top_quartile | 2,131 | 2,129 | 99.9% | -0.141 | -0.161 | 0.019 | 0.8923 |
| v7_bb_long | 30 | up_participation | bottom_quartile | 2,129 | 2,117 | 99.4% | 0.036 | -0.129 | 0.165 | 0.4576 |
| v7_bb_long | 30 | up_participation | top_quartile | 2,131 | 2,128 | 99.9% | -0.153 | -0.073 | -0.080 | 0.4406 |
| v7_bb_long | 60 | btc_return_30m | bottom_quartile | 1,134 | 1,124 | 99.1% | -0.214 | -0.070 | -0.144 | 0.2453 |
| v7_bb_long | 60 | btc_return_30m | top_quartile | 1,134 | 1,125 | 99.2% | 0.045 | -0.104 | 0.149 | 0.3381 |
| v7_bb_long | 60 | eth_return_30m | bottom_quartile | 1,134 | 1,124 | 99.1% | -0.259 | -0.027 | -0.232 | 0.131 |
| v7_bb_long | 60 | eth_return_30m | top_quartile | 1,137 | 1,129 | 99.3% | -0.038 | -0.092 | 0.054 | 0.6476 |
| v7_bb_long | 60 | fast_breadth | bottom_quartile | 1,134 | 1,124 | 99.1% | -0.154 | -0.191 | 0.037 | 0.8852 |
| v7_bb_long | 60 | fast_breadth | top_quartile | 1,144 | 1,144 | 100.0% | -0.288 | -0.156 | -0.132 | 0.3788 |
| v7_bb_long | 60 | joint_breadth | bottom_quartile | 1,134 | 1,126 | 99.3% | -0.227 | -0.173 | -0.054 | 0.7692 |
| v7_bb_long | 60 | joint_breadth | top_quartile | 1,134 | 1,122 | 98.9% | 0.039 | -0.139 | 0.178 | 0.4136 |
| v7_bb_long | 60 | joint_delta_30m | bottom_quartile | 1,135 | 1,129 | 99.5% | -0.163 | -0.088 | -0.075 | 0.5879 |
| v7_bb_long | 60 | joint_delta_30m | top_quartile | 1,135 | 1,120 | 98.5% | -0.008 | -0.091 | 0.083 | 0.555 |
| v7_bb_long | 60 | launch_density_1h | bottom_quartile | 1,212 | 1,204 | 99.3% | -0.244 | -0.271 | 0.027 | 0.8383 |
| v7_bb_long | 60 | launch_density_1h | top_quartile | 1,170 | 1,158 | 99.0% | -0.016 | -0.088 | 0.072 | 0.5535 |
| v7_bb_long | 60 | return_iqr_30m | bottom_quartile | 1,135 | 1,123 | 98.9% | -0.098 | -0.248 | 0.151 | 0.3431 |
| v7_bb_long | 60 | return_iqr_30m | top_quartile | 1,134 | 1,128 | 99.5% | 0.134 | 0.026 | 0.107 | 0.5856 |
| v7_bb_long | 60 | return_median_30m | bottom_quartile | 1,139 | 1,131 | 99.3% | -0.227 | -0.097 | -0.130 | 0.4337 |
| v7_bb_long | 60 | return_median_30m | top_quartile | 1,136 | 1,124 | 98.9% | 0.002 | -0.184 | 0.186 | 0.4203 |
| v7_bb_long | 60 | rv_tr_atr_expansion | bottom_quartile | 1,136 | 1,129 | 99.4% | -0.227 | -0.082 | -0.144 | 0.2729 |
| v7_bb_long | 60 | rv_tr_atr_expansion | top_quartile | 1,135 | 1,127 | 99.3% | 0.014 | -0.077 | 0.091 | 0.5647 |
| v7_bb_long | 60 | up_participation | bottom_quartile | 1,134 | 1,123 | 99.0% | -0.126 | -0.034 | -0.092 | 0.5563 |
| v7_bb_long | 60 | up_participation | top_quartile | 1,136 | 1,124 | 98.9% | 0.022 | -0.264 | 0.286 | 0.248 |
| v7_bb_long | 240 | btc_return_30m | bottom_quartile | 246 | 242 | 98.4% | 0.976 | 0.684 | 0.291 | 0.8286 |
| v7_bb_long | 240 | btc_return_30m | top_quartile | 240 | 237 | 98.8% | 0.181 | 0.335 | -0.155 | 0.3451 |
| v7_bb_long | 240 | eth_return_30m | bottom_quartile | 246 | 242 | 98.4% | 1.146 | 0.849 | 0.297 | 0.8053 |
| v7_bb_long | 240 | eth_return_30m | top_quartile | 251 | 248 | 98.8% | 0.328 | 0.445 | -0.117 | 0.6029 |
| v7_bb_long | 240 | fast_breadth | bottom_quartile | 241 | 234 | 97.1% | 0.120 | 0.289 | -0.169 | 0.7138 |
| v7_bb_long | 240 | fast_breadth | top_quartile | 247 | 246 | 99.6% | 1.072 | 0.765 | 0.306 | 0.735 |
| v7_bb_long | 240 | joint_breadth | bottom_quartile | 248 | 242 | 97.6% | 0.777 | 0.475 | 0.302 | 0.6449 |
| v7_bb_long | 240 | joint_breadth | top_quartile | 240 | 237 | 98.8% | 0.491 | 0.589 | -0.098 | 0.6155 |
| v7_bb_long | 240 | joint_delta_30m | bottom_quartile | 245 | 242 | 98.8% | 0.887 | 0.552 | 0.336 | 0.7757 |
| v7_bb_long | 240 | joint_delta_30m | top_quartile | 246 | 242 | 98.4% | 0.541 | 0.837 | -0.296 | 0.4754 |
| v7_bb_long | 240 | launch_density_1h | bottom_quartile | 249 | 246 | 98.8% | 0.256 | 0.170 | 0.085 | 0.6219 |
| v7_bb_long | 240 | launch_density_1h | top_quartile | 246 | 243 | 98.8% | 0.226 | 0.290 | -0.064 | 0.8714 |
| v7_bb_long | 240 | return_iqr_30m | bottom_quartile | 272 | 265 | 97.4% | -0.049 | 0.255 | -0.304 | 0.0236 |
| v7_bb_long | 240 | return_iqr_30m | top_quartile | 249 | 249 | 100.0% | 0.972 | 0.801 | 0.172 | 0.6412 |
| v7_bb_long | 240 | return_median_30m | bottom_quartile | 241 | 237 | 98.3% | 0.987 | 0.689 | 0.299 | 0.7409 |
| v7_bb_long | 240 | return_median_30m | top_quartile | 240 | 236 | 98.3% | 0.660 | 0.555 | 0.105 | 0.648 |
| v7_bb_long | 240 | rv_tr_atr_expansion | bottom_quartile | 240 | 234 | 97.5% | -0.052 | 0.194 | -0.247 | 0.2122 |
| v7_bb_long | 240 | rv_tr_atr_expansion | top_quartile | 240 | 240 | 100.0% | 1.067 | 0.638 | 0.429 | 0.4163 |
| v7_bb_long | 240 | up_participation | bottom_quartile | 244 | 240 | 98.4% | 0.984 | 0.540 | 0.443 | 0.5599 |
| v7_bb_long | 240 | up_participation | top_quartile | 248 | 243 | 98.0% | 0.411 | 0.461 | -0.050 | 0.727 |

该表排除已经冻结的 `joint_delta_60m`。`matched_control_summary.csv` 动态给出 `120` 个其余变量×top/bottom×cohort 描述性 paired p：最小未校正 p=`0.0016`，Bonferroni 上界=`min(1, 120 × 0.0016) = 0.192`。不得事后挑选其中任何一行来重选变量、阈值或组合规则。

## 跨组合证据与研究建议

本研究**没有预注册**降噪阈值、≥10R 保留阈值或统一验收门；下表只并列拒绝这条冻结规则的证据，且不会改动信号、通知、仓位或注册表。

| 变体 | 周期(分) | 匹配率 | 配对差值净R | 月块 p | 基线≥10R | 保留≥10R | ≥10R保留率 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1_common_execution_long | 30 | 100.0% | 0.057 | 0.7161 | 2 | 2 | 100.0% |
| v1_common_execution_long | 60 | 100.0% | 0.339 | 0.7212 | 2 | 2 | 100.0% |
| v1_common_execution_long | 240 | 93.9% | -0.325 | 0.2964 | 2 | 0 | 0.0% |
| v7_bb_long | 30 | 99.4% | 0.167 | 0.3202 | 75 | 40 | 53.3% |
| v7_bb_long | 60 | 98.9% | 0.110 | 0.4733 | 30 | 13 | 43.3% |
| v7_bb_long | 240 | 98.2% | -0.107 | 0.4806 | 11 | 2 | 18.2% |

**研究结论：跨 cohort 方向翻转、全部 paired p 不显著及 ≥10R 丢失共同拒绝 `joint_delta_60m > 0` 作为统一硬过滤。它不是其他规则的上线验收，也不可上线。**

## 如何优化（下一轮研究，而非上线）

- 先把市场广度作为市场状态标签或排序维度，保留现有规则的单变量身份，不把它直接变成交易过滤。
- 下一轮只测试一个变量：按信号周期归一化广度窗口，避免把 30m 面板的固定 60m 变化直接当作所有信号周期的同义特征。
- 重算 `launch_density` 时排除目标币，避免候选自身的启动进入它自己的市场环境指标。
- 这些项目需要新的预注册与 owner 决策；在完成独立验证前，不得上线、训练、通知或改仓位。

## 风险与诚实声明

- 这是已消费开发期上的单变量研究及其匹配随机对照，不是新的样本外、前向或实盘证据。
- 匹配对照衡量的是同一候选池与同币/同月/同波动桶随机入场的差异；它不能证明交易成本、流动性、滑点、资金费或实际执行会与历史回放一致。
- 单变量 top/bottom 表包含多个描述性比较，不能把其中看似较好的行当作发现后确认，亦不能绕开冻结规则追加条件。

## 复现命令

```bash
python3 -m yoyo.evaluation.spike_market_breadth_report \
  --stage-one experiments/active/exp-spike-market-breadth-20260913-v2/results \
  --matched experiments/active/exp-spike-market-breadth-matched-controls-20260913-v2/results \
  --report analysis/p1_spike_market_breadth_20260913.md
```
