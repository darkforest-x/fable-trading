# P1：ETH 15m Pine V12F × Grade-A YOLO 延迟融合回测（2026-09-03）

## 结论

**主方案失败。** 在 `2025-01-01` 至 `2026-03-01`（右开）的 ETH-USDT-SWAP 15m
安全前缀上，预先指定的 `Pine + YOLO 语义门 + 因果延迟成交` 只有 5 个候选、4 笔成交，
账户收益 **-1.31%**，15m 最大回撤 **6.05%**，每笔扣项目统一 0.2% 往返成本后平均
**-47.63bp**，胜率 **0/4**，profit factor **0**。top-decile 只有 1 笔且净亏 **10bp**，
Holm 校正后的排序置换 `p=1.0`；相对匹配随机入场的周块检验同样不显著
（Holm `p=0.875`）。四项经济门全部未通过，结论为 `fail_research_economic_gate`。

原始 YOLO 相交组表面收益 **+56.14%**，但不能解释为模型有效：它只成交 6 笔，其中唯一赢家
持有 13,251 根（约 138 天），单位净收益 +124.66%，贡献该组总单位净收益的 107.3%；其余
5 笔全部亏损，合计 -845.21bp。最高置信度交易也亏损 320.01bp，匹配随机对照本身平均赚
1436.60bp/笔，候选相对对照的周块 `p=0.4375`。这是单次长趋势与状态型反转路径放大的结果，
不是稳定去噪证据。

同合同的 Pine-only 基线为 97 笔、+31.41%、15m 最大回撤 18.22%。因此本轮不能把当前
completed-history YOLO 设为 Pine 的硬过滤器，更不能进入 tip、forward、ACTIVE 或部署。

## 本轮问题与冻结合同

本轮只回答一个问题：在不要求两个信号精确同一根、只要求同币同向的闭区间有交叉时，当前
Grade-A YOLO 能否在**真实可用时间之后**改善 Pine V12F 的经济结果。

回测前冻结三组，运行后没有改阈值或增删组：

| 组别 | 事件来源 | 成交时间 | 分数 |
|---|---|---|---|
| `pine_v12f` | 冻结 Pine V12F | Pine 信号后下一根开盘 | Pine oscillator 绝对值 |
| `pine_yolo_raw_delayed` | Pine 与 raw YOLO 同向闭区间相交 | `combined_available_at` 当根开盘 | YOLO confidence |
| `pine_yolo_semantic_delayed`（主方案） | 上组再通过 core4/5、post2--9 与冻结因果语义门 | 同左 | YOLO confidence |

融合组的 `combined_available_at=max(pine.available_at, model.available_at)`。决策 bar 是该时刻前
一根已经完整收盘的 15m K，入场是 `combined_available_at` 对应 K 线开盘；ATR14、close、
日历/波动资格、止损距离和仓位全部从这个延迟后的已完成决策 bar 重算，没有回填到原 Pine 时点。

执行合同沿用 V12F：ATR×4 且止损距离最多为决策 close 的 3%，每笔风险 1%，最大杠杆 13x，
单边佣金 0.1%，确认 bar break-even、反转和 skip 逻辑启用，不启用 trailing，**没有固定 TP**，
同 bar 冲突按 stop-first，区间末尾强平。本轮没有训练、拟合、超参搜索或事后选最优门。

## 数据、模型与样本统计

| 项目 | 数值 |
|---|---:|
| 品种 / 周期 | ETH-USDT-SWAP / 15m |
| 输入 | `data/kline_deep/okx_ETH_USDT_SWAP_15m_158499.csv` |
| 安全加载行数 | 145,666 |
| 安全加载范围 | 2022-01-03 15:30 -- 2026-02-28 23:45 UTC |
| 截断后内存 OHLCV SHA256 | `17f091b5e9b88f35feb5560c5a774a9277a21b64444d6ee2ea963cfacfc09159` |
| 回测范围 | 2025-01-01 00:00 -- 2026-03-01 00:00 UTC（右开） |
| holdout 起点 | 2026-05-04 00:00 UTC |
| 回测解析 / 评分 holdout 行 | 0 / 0 |
| Pine 候选 | 152（LONG 81 / SHORT 71） |
| raw YOLO 相交候选 | 20 |
| semantic YOLO 相交候选 | 5 |
| 模型 | Grade-A 8,000 正例 + 24,000 匹配负例，YOLO11s full40，native 1280 |
| 模型权重 SHA256 | `862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838` |
| 模型窗 / 核心 / 确认 | W18/W19；core 4/5；post 2--9 |
| 推理阈值 | conf 0.25；NMS IoU 0.70 |

20 个 raw 候选中，延迟时点资格门允许 19 个；状态型回放最终成交 6 笔。5 个 semantic 候选中，
资格门允许 4 个并成交 4 笔。被拒绝的 semantic 事件是 2025-03-27 SHORT：模型于 13:45 UTC
才可用，延迟决策落入冻结 V12F 禁止时段，因此没有强行成交。

## 与 Pine-only 同表结果

所有收益都使用同一执行器；`project net` 固定按每笔毛收益减 20bp，以符合项目经济门口径。

| 指标 | Pine-only | + raw YOLO 延迟 | + semantic YOLO 延迟（主） |
|---|---:|---:|---:|
| 候选 / 资格允许 / 成交 | 152 / 152 / 97 | 20 / 19 / 6 | 5 / 4 / 4 |
| LONG / SHORT 成交 | 49 / 48 | 3 / 3 | 3 / 1 |
| 账户收益 | +31.41% | +56.14% | **-1.31%** |
| 15m 最大回撤 | 18.22% | 15.53% | **6.05%** |
| 日线最大回撤 | 17.10% | 12.53% | 5.35% |
| 毛收益 / 笔 | +77.63bp | +1956.72bp | -27.63bp |
| 项目净收益 / 笔 | +57.63bp | +1936.72bp | **-47.63bp** |
| 胜率 | 11.34% | 16.67% | **0.00%** |
| monetary profit factor | 1.589 | 13.119 | **0.000** |
| 中位持仓 bars | 37.0 | 26.5 | 30.0 |

semantic 四笔中，三笔毛收益恰好约 +10bp，是 break-even stop；扣 20bp 项目成本后各为 -10bp。
剩余 SHORT 净亏 -160.51bp，所以该门不仅样本稀疏，而且目前没有一笔覆盖统一成本。

## 分数排序、top-decile 与单特征基线

正标签定义为该笔 `project_net_return > 0`。AUC 只作参考；项目成功门是 top-decile 扣成本后为正、
排序置换显著、且对匹配随机对照的超额为正并显著。

| 指标 | Pine oscillator（单特征基线） | raw YOLO confidence | semantic YOLO confidence（主） |
|---|---:|---:|---:|
| AUC | 0.415 | 0.800 | 不可计算（0 个正类） |
| top-decile 笔数 | 10 | 1 | 1 |
| top-decile 毛收益 / 笔 | +244.27bp | -300.01bp | +10.00bp |
| top-decile 净收益 / 笔 | +224.27bp | **-320.01bp** | **-10.00bp** |
| top-decile 胜率 | 20.00% | 0.00% | 0.00% |
| 单侧排序置换 `p` | 0.1862 | 1.0000 | 0.7544 |
| 模型两组 Holm `p` | 不适用 | 1.0000 | 1.0000 |

raw AUC 0.8 只有 1 个正类、5 个负类；与此同时最高置信度样本亏损，top-decile 净收益为负。
因此不能把 AUC 当作该组成功证据。

## 匹配随机入场与统计检验

每笔候选匹配 3 个不复用控制：同品种、同 UTC 月份、同香港 6 小时时段、同前月 ATR 五分位、
同方向，并复制候选持仓跨度、障碍和成本。统计单元是完整 UTC 周，不把 15m bar 当独立样本。

稀疏模型组使用所有周符号分配的 exact sign-flip；Pine-only 有 49 个周，使用固定 seed 的 50,000
次 Monte Carlo sign-flip 并做加一修正。置信区间通过 20,000 次周块 bootstrap 得到。两个模型组的
排序与匹配检验分别做 Holm 校正，项目显著性阈值为 0.01。由于模型组只有 4/6 个周，非参数检验的
离散分辨率本身已不可能达到 0.01；这正是样本不足的结论，不应用正态近似“制造显著”。

| 指标 | Pine-only | raw YOLO 延迟 | semantic YOLO 延迟（主） |
|---|---:|---:|---:|
| 匹配控制行 | 291 | 18 | 12 |
| 控制净收益 / 笔 | +82.53bp | +1436.60bp | -61.48bp |
| 候选 - 控制 / 笔 | -24.90bp | +500.12bp | +13.85bp |
| UTC 周数 | 49 | 6 | 4 |
| 周块 sign-flip `p` | 0.4175 | 0.4375 | 0.4375 |
| 模型两组 Holm `p` | 不适用 | 0.8750 | 0.8750 |
| 超额周 bootstrap 95% CI | [-88.98, +166.28]bp | [-288.95, +1831.32]bp | [-114.64, +166.06]bp |
| 绝对净收益周 bootstrap 95% CI | [-40.02, +230.97]bp | [-245.87, +6176.09]bp | **[-122.88, -10.00]bp** |

raw 组的匹配控制也异常赚钱，说明这批稀疏时点本身踩中了趋势 beta。候选虽比控制均值高
500.12bp，但区间跨零、exact `p=0.4375`；semantic 组候选和控制都亏，+13.85bp 的相对差不能
抵消其绝对亏损，也完全不显著。

## raw 组的单笔集中度诊断

| 项目 | 结果 |
|---|---:|
| 唯一赢家入场 | 2025-05-08 02:30 UTC，LONG |
| 退出 | 2025-09-23 03:15 UTC，反转 |
| 持仓 | 13,251 根 15m bar，约 138 天 |
| 该笔毛 / 项目净单位收益 | +12485.52bp / +12465.52bp |
| 占组内总单位净收益 | 107.27% |
| 占组内总货币 PnL | 108.25% |
| 其余 5 笔 | 5/5 亏损；合计 -845.21bp；均值 -169.04bp |

V12F 没有固定止盈，且信号过滤会改变下一次反转出现的时间。因此一笔被过滤器留下的长趋势可以改变
整个状态路径并主导复利曲线。总收益、回撤和 PF 在这里不是六份独立证据，只是同一笔长尾交易的不同投影。

## 经济门判定

| 预注册门 | raw YOLO 延迟 | semantic YOLO 延迟（主） |
|---|---:|---:|
| top-decile 净收益 > 0 | 失败 | 失败 |
| top-decile Holm 置换 `p < 0.01` | 失败 | 失败 |
| 候选 - 匹配控制 > 0 | 通过 | 通过 |
| 匹配周块 Holm `p < 0.01` | 失败 | 失败 |
| 总判定 | **FAIL** | **FAIL** |

不能用第三项单独通过来挑选 raw 组；预注册的是四门同时通过。

## 检测器样本外边界

当前 YOLO 数据集的全局时间切点是 `2025-12-01 00:00 UTC`，train 最晚依赖到
2025-11-29 08:00，val 最早输入为 2025-12-02 16:15。本回测从 2025-01 开始，因此**不是检测器
全程样本外评估**：raw 的 20 个候选中 16 个在切点前、4 个在切点后；最终 6 笔交易为切点前 5 笔、
切点后 1 笔。semantic 的 5 个候选为切点前 3 个、切点后 2 个；成交分别 2 笔和 2 笔。

事后只作风险拆分：raw 切点前 5 笔均值 +2388.06bp，被上述长趋势主导；切点后唯一一笔亏
320.01bp。semantic 切点前两笔均值 -10bp，切点后两笔均值 -85.25bp，四笔全亏。该拆分不是
预注册子组，不能用于挽救或重新选择方案；它只进一步限制 raw 表面收益的证据资格。

## 完整性验证

独立账本检查通过：

- 三组候选数精确为 152 / 20 / 5，Pine-only 精确复现 97 笔与 +31.40545%；
- 每笔 `entry_i=decision_i+1`，信号、模型可用时间与成交均落在严格 15m 网格；
- 融合止损距离逐笔等于延迟决策 bar 的 `round(min(ATR14*4, close*3%), tick)`；
- 执行事件全部通过延迟后的日历/波动资格门，所有入场和退出都早于 `2026-03-01`；
- 项目净收益逐笔等于毛收益减 0.002；实际佣金逐笔为
  `0.001 * (1 + exit_price / entry_price)`；
- 每笔恰有 3 个匹配控制，组内控制起点不重复，matched-pair 数与交易数一致；
- 回测启动前验证融合四份源产物 SHA、模型/渲染/语义门身份和截断 OHLCV 哈希，漂移即失败。

自动测试覆盖：延迟可用时点映射、延迟 ATR/close、非 15m 网格拒绝、同 bar 反向冲突拒绝、
Holm 校正、完整周 exact sign-flip，以及 Pine / YOLO 融合账本的既有因果与身份测试。

## 风险与诚实声明

- 本轮是历史安全前缀回放，不是未触碰市场的最终确认；Pine 参数此前也已在该前缀研究过。
- YOLO 是围绕 Pine 候选做条件化扫描，不是全市场独立发现器；结果只回答硬过滤问题。
- 当前模型看 core 后 2--9 根，输出时间已按完整窗右端记账；它按纪律不得冒充 tip 信号。
- semantic `n=4`、raw `n=6`，统计功效极低；报告 exact 检验而没有用不合适的正态假设。
- raw 结果被一笔 138 天长仓完全支配，且检测器训练期与回测前段重叠，不具备稳健样本外资格。
- 回测自身没有解析、展示或评分 `>=2026-05-04` 的 holdout 行，也没有使用整文件哈希。但此前整理
  融合报告时曾误对包含 holdout 尾部的源 CSV 执行一次整文件 `shasum`，因此读取过受保护字节。
  该哈希已撤销证据资格并替换为截断后内存 OHLCV 哈希；当时没有解析行值或给配置评分，但仍属于
  未授权 holdout 字节访问纪律事件。是否计为该配置一次正式 holdout 消耗，留待 Owner 裁决。
- 本轮没有训练、调参、读取 holdout 评分、promote、修改 ACTIVE/frozen/forward、部署或下单。

## 下一步选项（需 Owner 决策）

1. **推荐停止“YOLO 作为硬门”这条方案。** 语义门直接亏损；raw 组只剩一笔长尾赢家，继续调 conf、
   overlap 或 post 范围会变成在 4--6 笔上追结果。
2. 若继续研究模型融合，只把 `raw/semantic、boundary/positive-duration、confidence、delay bars` 作为
   预注册特征，在检测器 `2025-12-02` 之后收集更多真正时间外事件；不得用本轮挑阈值。
3. 若目标是实盘去噪，另走 tip / tip-1 / tip-2 Gold 检测器路线。当前 completed-history 权重不能进入
   tip-smoke、forward 或 ACTIVE，延迟预算和执行架构仍需 Owner 单独批准。

## 产物与哈希

目录：`analysis/output/p1_pine_v12f_grade_a_yolo_backtest_20260903/`

| 文件 | SHA256 |
|---|---|
| `candidate_events.csv` | `1d4dea251279b0983b0512b0ecb29a7e6d49e52a193ffa118b13030d7b2ea611` |
| `trades.csv` | `4f15a811d6d3247a093dce1c536b3b1643915f1fddea57915995c0684dd7ac71` |
| `equity.csv` | `692ac41f5b22c7d2b847a69895c5e8e5e306be08dd3a062b3e793ba821810f52` |
| `matched_controls.csv` | `ca36b75bb8f8457fb3140a3b674ddd12ffa522057f4caea5cc7a37123a9976be` |
| `matched_pairs.csv` | `0d3db67b77b5a2b24d18b49ebb3de196928c44975a1c6f46dbbc70b51bfc07fd` |
| `metrics.csv` | `36bf0b02d3fad72ab9df868fc7f0892b0fefc4f76b840d23c9fccebdfeee783f` |
| `summary.json` | `1ccf1be9ad410fd04dbcccde228091be7638cda74246d10b63c7f8f3ba66e7f1` |

运行环境：Python 3，numpy 2.0.2，pandas 2.3.3，scipy 1.13.1，torch 2.8.0，
ultralytics 8.4.89。

## 复现命令

回测器拒绝覆盖已有产物，复现时应写入一个新目录：

```bash
cd /Users/zhangzc/fable-trading

PYTHONPATH=. .venv/bin/python \
  scripts/backtest_pine_eth_15m_v12f_yolo_fusion.py \
  --input data/kline_deep/okx_ETH_USDT_SWAP_15m_158499.csv \
  --fusion-dir analysis/output/p1_pine_v12f_grade_a_yolo_fusion_20260903 \
  --output-dir /tmp/p1_pine_v12f_grade_a_yolo_backtest_reproduction \
  --symbol ETH_USDT_SWAP \
  --start 2025-01-01 \
  --end-exclusive 2026-03-01

PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_backtest_pine_eth_15m_v12f_yolo_fusion.py \
  tests/test_run_pine_eth_15m_v12f_yolo_fusion.py \
  tests/test_run_pine_eth_15m_v12f.py \
  tests/test_ma_launch_yolo_semantic_gate.py \
  tests/test_pine_allin_v7_backtest.py \
  tests/test_backtest_pine_eth_15m_v12_preholdout.py

/Users/zhangzc/.local/bin/ruff check \
  scripts/backtest_pine_eth_15m_v12f_yolo_fusion.py \
  scripts/run_pine_eth_15m_v12f_yolo_fusion.py \
  tests/test_backtest_pine_eth_15m_v12f_yolo_fusion.py \
  tests/test_run_pine_eth_15m_v12f_yolo_fusion.py

python3 scripts/md_to_html.py \
  analysis/p1_pine_v12f_grade_a_yolo_backtest_20260903.md \
  --out-dir analysis/html
```

随机过程统一使用 seed `20260903`：模型分数排序置换 10,000 次，周 bootstrap 20,000 次；
超过 20 周时 sign-flip 使用 50,000 次 Monte Carlo 加一修正，否则穷举所有 `2^W` 个周符号分配。
