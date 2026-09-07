# V24：固定 K1 入口的 OPEN 时钟标签与事前随机背景对照

## 回答的问题

本轮首次独立观察原始 BTCUSDT.P 1h K1 入口之后固定时钟的方向持续性，不借助原退出时刻、K1 止损、颜色退出或未来盈利筛选样本。主时钟固定为 **4h**；1h、12h、24h 只描述，不选择最好的时钟。标签是 open-to-open markout 及扣 20bp 的成本阈值 markout，**不是可执行策略净收益**。固定时钟不模拟止损、仓位、资金费、滑点或成交可行性。

全 251 母样本保留，四半年数量 55/66/55/75。V23 新背景供给支持 248/251，另外三母仍计算自己的四个标签，只在无三控制的配对超额中为 unknown/unmatched，不能将其自身标签设为空或删除。V23 的最大容量 witness 不被直接当作随机对照；V10 的旧 154 对照及其结论不被改写。

## 输入冻结与来源

同目录 config 与 builder 的 INPUTS 列出全部 13 个 V23 保存结果的逐字节 SHA，包括原母、已知 admissible edges、母支持、匹配 frame、容量见证和三个 JSON 收据。容量见证仅验哈希，不参与新随机选择。另锁 V4 原母表、V1 base config、原 archive audit 的 SHA。先验所有 bytes/metadata；再检查 V23 started→support_frozen→summary 的 UTC 时序、支持门与 248/251、源文件在旧 builder commit 的 bytes、commit 先于运行、输出 hash 与原 V4 身份链。母表两次 CSV 解码允许既有 1e-12 浮点契约，ID/字段/时钟/行序不允许改变。

归档仍为原 base source：`data/kline_preholdout_okx_5m/okx_BTC_USDT_SWAP_5m_341567.csv`，SHA `767f67c2b0ae5a8c83369a7cb950334e61de09edbb82a0158122c41794eed5ac`。audit SHA `c14aae71600d7f91b27068976656b9d4a8dc1bfb462df182a2e2a78ac85d5228`。不下载、不换源。物理档案可以包含 2025 至 2026 年初，但先只读时间列检查物理末端严格早于 source.end_exclusive 与 repository holdout。价格 materialization 只能读取严格早于 `2025-01-01T00:00:00Z` 的 OPEN 前缀。

## 唯一抽样合同（先于任何 raw 时间/价格读取）

以 V23 13192 admissible edges 构造二部连通组件，按每组件最小 mother ID 排序；母 ID、candidate UTC ISO ID 分别排序。每个非空组件必须是完整二部图，各母共享完全相同的候选集合，且 `candidate_count >= 3*mother_count`；否则直接失败，不换 seed、不重配、不回退 MILP。孤立母必须有原缺支持 reason，不伪装可抽样母。

只初始化一次 `Generator(PCG64(20260907))`。沿 canonical component 顺序，对每个 sorted candidate list 调用一次 permutation，按 sorted mother 顺序连续分三个 candidate；三 slot 为 0/1/2。空组件不消耗随机流。控制方向沿对应母，控制 signal/decision 时钟用自身候选时刻，母 decision month 必须相同。控制 timestamp 全局不复用（不按方向拆开复用），不含实际母时刻。每母三控制或零控制；新控制总数固定 744。一次抽样同时用于四个 H，不随 OPEN 缺失或标签正负补抽。

这是符合事前安全候选资格的**随机背景抽样**，不是随机分派“是否产生 K1”，不能据此宣称无混杂的 K1 因果效应。原三键为同币 × month × UTC 六小时块 × 因果 vol bucket；V23 所有额外支持有效性、当前/前一穿线、实际母排除、风险转移合法性由冻结 edges 继承，不放松。颜色/斜率不再精确配对，故比较对象是整体 K1 入口，不是纯形态优势。

## 原始 OPEN 路径与完整标签

`sampling_frozen.json` 先保存 seed、初末 RNG state、allocation、case/control requests、组件和全母 assignments 的 hash；此检查点之后才能调用 raw loader。

Loader 的顺序固定：audit SHA/元数据 → 整个物理归档仅 `open_time` 列 → UTC 5m 网格/排序/唯一/首尾与 audit 一致、物理末端不越界 → 整档 bytes hash → `nrows=时间戳<2025的行数`、仅 `open_time,open`、字符串保留原报价。不经 resample_complete，不按 HLC/OPEN 有效性预先删行，绝不读取 high/low/close/volume。坏 OPEN 留给标签显式 unknown。保存实际送入标签模块的两列 `open_prefix.csv.gz`，供独立 saved-evidence 核验，不宣称 raw 聚合被独立重跑。

每个 request 的 E 是 own decision_time；对 H∈{1,4,12,24}，使用 E 与 E+H 两端 OPEN，并要求 inclusive `[E,E+H]` 每个 5m timestamp 和 finite positive OPEN 全部存在（`12H+1`）。缺任何一根即该 H unknown；不因为两端价格恰好都有就假装完整。H 标签窗口必须 `E+H < fold.end`，等号拒绝；请求本身仍继承严格 `fold.start <= E < fold.end−72h`。全部 source price 前缀严格 pre2025，四 fold 不变。

计算由纯 `hourly_impulse_fixed_clock.build_fixed_clock_labels` 完成：`direction*(OPEN[E+H]−OPEN[E])/OPEN[E]`，固定 Decimal 报价运算后减 `.002` 再转 float，exact 20bp 为零，不用 epsilon 清洗负值。只投影 event_id/decision_time/direction/fold，旧 stop/exit/MFE/颜色与结果 schema 无关。

## 输出、配对与分母

case labels 固定 1004 行（251×4），control labels 固定 2976 行（744×4），paired labels 固定 1004 行（251×4）。这些行不是 1004/2976 个独立样本。保留 pure label 的 role=primary/descriptive；case/control 用 request_kind，另保留 mother_id、control_slot、mother_month、matched_support。

每母-H 配对保留 case_known、matched_support、n_controls_expected=3、n_controls_assigned、n_controls_known、pair_complete、pair_reason、母值、三控制 mean 和 excess。必须母已知且三个控制均已知才有 excess；三控制均已知但母未知可保留控制 mean 作诊断，不能成为有效配对。缺支持、母 label 未知、控制 label 未知分别记录，不当零收益。自身 labels 和完整配对母均值并列，显露三无对照母的选择差异。

本阶段 summary 只给四个 H 的 all/known/unknown、全母 gross/成本阈值均值、完整配对母/控制/超额均值及来源/抽样收据。不调用推断，不设策略 profitable flag，不进行账户复利或单仓模拟。完整配对不等于独立样本，固定窗口可能重叠。

## 标签前冻结的后续统计合同（本 runner 不执行）

仅 4h 的 all-case 成本阈值 markout 与完整三控制配对 excess 是两项必要主指标。母 decision month 定义 2023–2024 的 24 个 calendar-month cluster；控制同月，重采样必须整组携带母、三个控制和所有 H，H 间共享月索引与符号。不把各 H 或各 control 当独立 n。

预定独立于抽样 RNG 的新 PCG64(seed20260907) 统计 stream，9999 draws，事件加权 sum/n 的月 bootstrap 95% percentile CI；one-sided monthly-sum signflip +1 校正。主线在另一纯统计模块实现并独立审查；当前 runner 只冻结合同不调用它。

探索性继续入口研究必须：known case≥226、complete pairs≥226，两主指标 mean>0、CI 下界>0、p<.01，以及四半年 case 成本阈值均值均>0。1/12/24h 无替代主指标资格。继续 flag 不是盈利验收、不是部署授权；若不满足，不从四个时钟挑赢家重命名主结果。

## 安全状态机与合成验收

程序先检查当前源码、纯标签模块、测试、计划、配置已提交；拒绝任何已有 results。started 在输入读取前，sampling_frozen 在任何 raw 读取前，source_receipt 和 OPEN prefix 在标签前；任何异常保存 failure 与已有证据，不覆盖旧结果，不重新抽样。所有 JSON 非有限值写 null，禁止 NaN/Infinity 字面量。

合成测试重点：单 PCG64 流及 canonical 顺序、完整二部/供给不足失败、不得按未来结局或输入行序抽样、三母无支持保留、全组已知才配对、同月同方向控制、1e-12 原母 parity、72h/fold端点、时间预检→hash→nrows OPEN 顺序、不读 HLC、不按坏报价删行、真实价格读取前 sampling freeze、SHA/source-commit失败不读 raw、错误收据与拒绝覆盖。

```bash
.venv/bin/python -m pytest -q tests/test_hourly_impulse_fixed_clock_research.py tests/test_hourly_impulse_fixed_clock.py
```

主代理审查、提交 builder 后才允许首次真实运行（本实现轮不运行）：

```bash
.venv/bin/python -m yoyo.evaluation.hourly_impulse_fixed_clock_research
```

## 技术来源与诚实边界

后续保存标签统计入口亦在标签读取前提交，并纳入 runner 的 source receipts：
`python3 -m yoyo.evaluation.hourly_impulse_fixed_clock_analysis`。它只读已冻结 label
表及来源收据，不再读 raw；先使用 SHA 锁定的 statistical-analysis
`check_normality(plot=False)` 检查4h全母和配对超额的分布，再执行上述固定推断。
所有 IQR 尾部保留，正态性建议不触发选测试、变换收益或换主要时钟。
系统 Python 与研究环境均为 NumPy2.0.2/pandas2.3.3/SciPy1.13.1，系统已有绘图库用于
加载技能工具，不安装新依赖。保存诊断先于推断，拒绝覆盖 analysis_results 和失败收据。

本轮是固定入口标签诊断，没有训练/评分 L2 或优化排序；val AUC 与 top-decile 排序收益
不是本次目标，不冒充执行净收益。报告必须给全母与完整配对背景的毛/成本阈值 markout、
样本数、未知原因、离散程度、月簇区间及两项预定检验，保留所有非显著结果。

NumPy 2.0.2 / pandas 2.3.3 与仓库约束一致，不装依赖。官方 PCG64 及 permutation 定义支持冻结一次随机流；read_csv usecols/nrows/dtype 保证只 materialize 指定列和行数：

- https://numpy.org/doc/2.0/reference/random/bit_generators/pcg64.html
- https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.permutation.html
- https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_csv.html

使用实验设计技能区分背景抽样与处理随机化、母与重复标签；使用来源驱动开发核对版本 API。现有 V1–V23 已反复使用开发期，本次不是新 holdout 或确认级验收。没有改变 holdout 批准、成本、生产资格和训练资格。
