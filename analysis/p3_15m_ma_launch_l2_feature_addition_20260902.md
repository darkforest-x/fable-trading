# 15m L2 因果特征增量实验（2026-09-02）

## 结论先行

最终裁决：**REJECT**。LONG tune 入选 **full_110**，SHORT tune 入选 **plus_ma_family**。本轮只允许在旧 28 列上增加特征；候选、标签、时间切分、模型参数、成本和匹配对照均保持不变。

![增量特征诊断](output/ma_launch_l2_feature_addition_v1/feature_addition_diagnostics.png)

## 数据与因果复原

| split | LONG 独立事件 | SHORT 独立事件 | 合计 |
|---|---:|---:|---:|
| train | 190 | 227 | 417 |
| tune | 118 | 111 | 229 |
| final_validation | 157 | 85 | 242 |

数据范围：2026-01-01 至 2026-05-03；holdout 读取 0。原 28 列复算最大误差 3.553e-15；新增 82 列，去重后总计 110 列；未来特征行 0。

语义重复列被剔除：`fast_spread`、`fast_spread_rank96`、`dense_run_len_fast`、`roc_12`、`roc_48`、`vol_ratio_20`。LONG/SHORT 独立训练；新增带符号市场坐标不会跨方向混训。

## 新增特征组

| 组 | 新增列数 | 主要内容 |
|---|---:|---|
| ma_family | 26 | 多周期 SMA/EMA 相对位置、间距、斜率、交叉、带宽 |
| dense_dynamics | 8 | 密集带收缩/扩张速度、历史位置、连续状态 |
| momentum_structure | 22 | 多窗收益、距高低点、突破距离、区间位置 |
| candle_volatility | 11 | ATR7/28、实现波动、实体/影线、波动变化 |
| volume_flow | 5 | 96 根量比、量 z、价量相关、突破量、上涨量占比 |
| market_structure | 5 | HH/HL/LH/LL 与结构偏向 |
| time_context | 5 | UTC 小时与星期周期编码 |

## March tune 预注册选择

| 方向 | 方案 | 总列 | 新增 | iter | top-10% 净收益 | q90 n | q90 净收益 | Spearman | 健康 | 入选 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| LONG | baseline_28 | 28 | 0 | 17 | -0.930% | 13 | -0.931% | 0.0649 | True | False |
| LONG | plus_ma_family | 54 | 26 | 17 | -1.699% | 12 | -1.644% | -0.0569 | True | False |
| LONG | plus_dense_dynamics | 36 | 8 | 15 | -0.585% | 12 | -0.592% | 0.0789 | True | False |
| LONG | plus_ma_dense | 62 | 34 | 15 | -0.594% | 12 | -0.599% | 0.0152 | True | False |
| LONG | plus_momentum_structure | 50 | 22 | 15 | -0.980% | 12 | -0.997% | 0.0088 | True | False |
| LONG | plus_candle_volatility | 39 | 11 | 40 | -0.622% | 12 | -0.648% | 0.0867 | True | False |
| LONG | plus_volume_flow | 33 | 5 | 15 | -0.890% | 12 | -0.891% | 0.0819 | True | False |
| LONG | plus_market_structure | 33 | 5 | 51 | -0.630% | 12 | -0.585% | 0.1121 | True | False |
| LONG | plus_time_context | 33 | 5 | 62 | +0.288% | 12 | +0.258% | 0.1245 | True | False |
| LONG | full_110 | 110 | 82 | 60 | +0.354% | 12 | +0.318% | 0.1905 | True | True |
| SHORT | baseline_28 | 28 | 0 | 17 | -0.650% | 12 | -0.681% | 0.2969 | True | False |
| SHORT | plus_ma_family | 54 | 26 | 18 | -0.171% | 12 | -0.043% | 0.3791 | True | True |
| SHORT | plus_dense_dynamics | 36 | 8 | 12 | -0.392% | 12 | -0.269% | 0.3841 | True | False |
| SHORT | plus_ma_dense | 62 | 34 | 18 | -0.597% | 12 | -0.687% | 0.3123 | True | False |
| SHORT | plus_momentum_structure | 50 | 22 | 14 | -0.426% | 12 | -0.106% | 0.3375 | True | False |
| SHORT | plus_candle_volatility | 39 | 11 | 13 | -0.357% | 12 | -0.212% | 0.3800 | True | False |
| SHORT | plus_volume_flow | 33 | 5 | 7 | -0.424% | 12 | -0.278% | 0.4080 | True | False |
| SHORT | plus_market_structure | 33 | 5 | 10 | -0.269% | 12 | -0.342% | 0.2821 | True | False |
| SHORT | plus_time_context | 33 | 5 | 14 | -0.680% | 12 | -0.729% | 0.2027 | True | False |
| SHORT | full_110 | 110 | 82 | 6 | -0.353% | 12 | -0.234% | 0.3668 | True | False |

选择与 early stopping 只看 March tune；April final 在 selection receipt 提交后才打开。

## April final：基线、单特征与冻结入选组合

| 配置 | LONG / SHORT | top-10% 净收益 | q90 n | q90 净收益 | TP 标签率 | p | 事件减匹配对照 | 8/8 均跑赢 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 单特征基线 | ma_spread_only / ma_spread_only | +0.578% | 48 | +0.578% | 35.42% | 0.075392 | +0.744% | False |
| 旧 28 列基线 | baseline_28 / baseline_28 | +0.898% | 20 | +1.231% | 40.00% | 0.072093 | +1.134% | True |
| tune 冻结入选 | full_110 / plus_ma_family | +1.108% | 31 | +0.987% | 41.94% | 0.046395 | +0.922% | True |

入选组合诊断：AUC=0.4503，PR-AUC=0.3124，Spearman=-0.0320。AUC 不作成功裁决；裁决看扣成本收益、p、样本量及匹配对照。

## 31 个 q90 独立信号的真实结果与高清图

`31` 不是 YOLO 原始框数。April final 共有 1,021 个滑窗命中，依照同币重叠暴露块只保留 242 个独立代表事件，L2 冻结 q90 门再保留 31 个：LONG 21、SHORT 10。信号时间为 2026-04-01 00:15 至 2026-05-03 01:30 UTC，约 32 天。

必须区分两种口径：结果表里的 `TP 标签率=41.94%` 是 13/31 个先到 TP，不等于“只有 13 个赚钱”。真实结果是 TP 13、SL 14、TIMEOUT 4；4 个 TIMEOUT 中 3 个扣 0.2% 成本后仍为正、1 个为负。因此实际扣成本净盈利 **16/31（51.61%）**、净亏损 **15/31（48.39%）**，平均净收益仍是 **+0.987%**。

![31 个 q90 信号总览第一页](output/ma_launch_l2_feature_addition_v1/selected_q90_signal_gallery/overview_page_01.png)

[逐张查看 31 张 1920×1320 高清原图](output/ma_launch_l2_feature_addition_v1/selected_q90_signal_gallery/gallery.html)。每张图左侧 168 根是模型当时可见的因果输入；蓝色虚线右侧浅色区域是审计用未来 72 根，只用于展示已冻结结果，不是模型输入。红框逐张复原原 L1 检测坐标；橙点是下一根开盘进场，绿/红点是实际退出。8 张 overview 覆盖全部 31 图，未人工删图或只挑盈利图。

## 分方向 final

| 方向 | 入选方案 | 总列 | 新增 | final n | q90 n | q90 净收益 | TP 标签率 | p |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| LONG | full_110 | 110 | 82 | 157 | 21 | +0.682% | 33.33% | 0.089591 |
| SHORT | plus_ma_family | 54 | 26 | 85 | 10 | +1.627% | 60.00% | 0.046395 |

## 入选模型的 gain 前列

| 方向 | 特征 | 组 | gain |
|---|---|---|---:|
| LONG | dow_cos | time_context | 0.03 |
| LONG | dist_high_48 | momentum_structure | 0.02 |
| LONG | spread_chg8 | legacy_28 | 0.02 |
| LONG | spread_pos96 | legacy_28 | 0.01 |
| LONG | full_ratio_min48 | legacy_28 | 0.01 |
| LONG | ret_96 | momentum_structure | 0.01 |
| LONG | spread_mean24 | legacy_28 | 0.01 |
| LONG | rvol_96 | candle_volatility | 0.01 |
| LONG | ret_48 | legacy_28 | 0.01 |
| LONG | close_vs_ema200 | legacy_28 | 0.01 |
| SHORT | atr_pct_ratio96 | legacy_28 | 0.03 |
| SHORT | ema8_slope8 | ma_family | 0.03 |
| SHORT | close_vs_ema120 | ma_family | 0.02 |
| SHORT | atr_pct | legacy_28 | 0.01 |
| SHORT | slow_slope_12 | legacy_28 | 0.01 |
| SHORT | close_vs_ema200 | legacy_28 | 0.01 |
| SHORT | fast_slow_gap | legacy_28 | 0.01 |
| SHORT | spread_chg24 | legacy_28 | 0.01 |
| SHORT | spread_chg8 | legacy_28 | 0.01 |
| SHORT | volume_ratio | legacy_28 | 0.01 |

## 匹配对照与验收门

匹配条件保持同币、同月、同 UTC 8 小时时段、同 ATR 桶、同方向及同障碍/成本。入选事件完整控制覆盖 31/31；平均事件减对照 +0.922%。

| 预注册门 | 通过 |
|---|---|
| aggregate_beats_matched_controls_every_assignment | True |
| aggregate_minimum_30_selected_dependency_blocks | True |
| aggregate_outcome_permutation_p_lt_0_01 | False |
| aggregate_selected_q90_net_strictly_better_than_baseline_28 | False |
| aggregate_selected_top_decile_net_positive | True |
| at_least_one_side_selected_an_addition | True |
| baseline_reproduction_required | True |
| each_side_minimum_10_selected_dependency_blocks | True |
| neither_side_selected_q90_net_negative | True |
| 全部门 | **False** |

## 基线复现

旧 28 列基线复现：True；final 最大分数误差 9.975e-17；KEEP 完全一致：True。

## 风险与诚实声明

- train 417、tune 229、final 242 个独立事件；110 列完整模型相对样本量偏大，必须以时间外结果而非 tune 增益裁决。
- 十个 add-only 方案共享同一 tune，存在多重比较；没有用 April final 反向重选。
- 新增方向性列保留原始市场符号，但 LONG/SHORT 完全分开训练；若未来任何方案通过，还需另做坐标语义消融。
- 当前 L1 使用完成态窗口，本实验不能冒充 tip/tip-1/tip-2 实盘信号。
- 未读取 2026-05-04 后 holdout，未 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。

## 复现命令

    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_addition --build-features
    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_addition --select
    # 提交 selection receipt 后：
    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_addition --evaluate-final
    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_addition --render --verify --report
    PYTHONPATH=. .venv/bin/python scripts/render_15m_ma_launch_l2_feature_addition_signals.py --render
    PYTHONPATH=. .venv/bin/python scripts/render_15m_ma_launch_l2_feature_addition_signals.py --verify
    python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l2_feature_addition_20260902.md --out-dir analysis/html

## 下一步

只有全部预注册门通过，才值得在新的未见时间段复验；本报告不授权读取 holdout、promote 或部署。
