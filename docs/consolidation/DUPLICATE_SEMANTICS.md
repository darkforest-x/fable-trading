# 语义去重盘点（C5）

> 逐条测试：`tests/parity/test_duplicate_semantics.py`
> 原则：**能证明一致的就钉住一致；不一致的就量化并登记，等 owner 裁决。**
> 不做的事：在"不许影响运行"的任务里大面积改写活代码。

任务书 §5 要求"每种语义只能保留一个 canonical implementation"。五仓合并后逐项实测结果如下。

## 汇总

| 语义 | 实现数 | 状态 | 处置 |
|---|---|---|---|
| holdout 边界 | **11** | 全部一致 | 已建 canonical（`yoyo/contracts/holdout.py`）+ 漂移测试 |
| 文件 SHA-256 | **7**（另有 5 处工具内副本） | 实测全部一致 | 测试钉住一致；合并留待专门一轮 |
| SMA / EMA | **3** | 实测**逐位一致** | 测试钉住；不改列名（见下） |
| ATR | **2** | **不一致**（warmup 播种） | 已量化并钉住，**需 owner 裁决** |
| 成本常量 | 177 处引用 | 有意为之 | 不动（CLAUDE.md 明文） |
| 障碍/出场模拟 | 1 | 已唯一 | 加测试防止长出第二个 |
| closed-bar 判定 | 1 | 已唯一 | — |

---

## 1. holdout 边界 —— 11 处，6 个名字

`HOLDOUT_START` / `HOLDOUT_CUTOFF` / `HOLD_DEFAULT` / `ACCEPT_START` /
`holdout_start_exclusive` / `holdout_start` / `data_end_boundary`。

全部指向 `2026-05-04T00:00:00Z`。**今天一致，但没有任何东西让它们一致**——分散在
不同文件、不同名字，没有任何两个被同一个测试读过。

处置：`yoyo/contracts/holdout.py` 是唯一定义；其余 11 处**不改写**（那是在动
judgment 活路径），改由 `tests/causality/test_holdout_boundary_is_single_valued.py`
逐个读出来比对，任何一处漂移当场红。

## 2. 文件 SHA-256 —— 7 个实现

`yoyo/artifacts/lineage.py::digest_file`、`yoyo/contracts/protocol.py::file_sha256`、
`yoyo/datasets/gold_render.py::sha256_file`、
`yoyo/datasets/legacy_gold_migration/io.py::sha256_file`、
`yoyo/layers/l1_detection/onset/common/hashing.py::file_sha256`、
`yoyo/layers/l2_judgment/frozen.py::file_sha256`、
`tools/consolidation/port_asset.py::sha256_file`。

在 3 MB 随机文件上实测：**7 个摘要完全相同**。分块大小和返回类型不同——正是那种在
其中一个被人改动之前一直看不见的差异。

处置：测试钉住一致。合并成一个需要改 7 处调用点，其中包括 `protocol.py`
（`yoyo` 从本仓搬出时特意把 `file_sha256` 内联进去，就是为了不向 judgment 层借），
属于独立一轮的工作，不塞进本次收敛。

## 3. SMA / EMA —— 3 个实现，逐位一致

| 模块 | 列名 | 角色 |
|---|---|---|
| `yoyo/layers/l1_detection/data.py::add_mas` | `sma20` / `ema20` … | 喂给 renderer，检测器绑死在这些像素上 |
| `yoyo/layers/l1_detection/numeric_baseline/indicators.py::add_indicators` | `sma_20` / `ema_20` … | yoyo-eth 字节一致的研究代码 |
| `yoyo/data/indicators.py::add_indicators` | `ema8`…`ema200` | 另一套 EMA 周期集，用途不同 |

前两者独立写成，实测六条均线**最大绝对差 0.000e+00**。
**列名里的那个下划线就是全部差异**——也正是没人发现这是同一个函数写了两遍的原因。

处置：不合并。合并意味着在检测路径上改列名，而那条路径的像素不许动一位。
测试钉住数值一致，任何一方漂移当场红。

## 4. ATR —— 2 个实现，warmup 不一致 ⚠️ **需 owner 裁决**

```
yoyo/layers/l1_detection/numeric_baseline/indicators.py
    tr.iloc[0] = NaN                      # bar 0 没有前收，TR 无定义
    atr = tr.ewm(alpha=1/14, adjust=False, ignore_na=True).mean()
    atr.iloc[:14] = NaN                   # 攒够 14 个 TR 之前不出数

yoyo/data/indicators.py
    atr14 = tr.ewm(alpha=1/14, adjust=False).mean()   # 从 bar 0 就出数，
                                                       # 且用 bar 0 的 high-low 播种
```

实测差异（同一批 300 根合成 K 线）：

| bar | 绝对差 |
|---|---|
| 0–13 | 严格版为 NaN，宽松版有值 |
| 14 | **0.1094** |
| 40 | 0.0171 |
| 100+ | < 1.9e-4 |
| 200+ | < 1.1e-7 |

即：**播种差异，指数衰减，200 根后耗尽**。

**为什么这条不能只当 warmup 细节**：ATR 定义 TP/SL 的障碍距离（−5 / +2 ATR）。
序列开头 ATR 偏了，障碍距离就偏了。凡是从序列前 100 根取信号的路径都受影响。

处置：**不修**。改任何一边都会移动所有用过它的已发布数字，而"哪一个才对"是 owner
决策（CLAUDE.md 把障碍参数列为 owner 保留项）。当前已量化并由
`test_the_two_atrs_diverge_only_in_warmup_and_the_gap_decays` 钉住，
数值一变当场红。

**给 owner 的选项**：
- A. 保持现状（两套并存，差异已钉住）——不动任何已发布数字
- B. 统一到严格版（前 14 根 NaN）——更正确，但 `yoyo/data/indicators.py` 的下游
  全部要重算，含看板与因子库
- C. 只在新代码里用严格版，旧路径冻结——新旧混用，需要在报告里标注用的是哪一个

## 5. 成本常量 —— 177 处引用，有意为之

`yoyo/contracts/costs.py` 已是 single source of truth，且其 docstring 明确写着：
`scripts/` 下支撑已发布报告的实验脚本**故意保留内联副本**，改它们会破坏那些报告的复现。

处置：不动。这不是债，是记录。

## 6. 已唯一的两项

- **障碍/出场模拟**：`yoyo/contracts/outcomes.py::resolve_barrier_outcome`。
  加了测试断言 `SAME_BAR_POLICIES == ("conservative_sl",)`——同 bar TP/SL 抢先的
  裁决口径是 owner 决策，冒出第二个策略就等于一个问题两个答案。
- **closed-bar 判定**：`yoyo/data/continuity.py::latest_closed_boundary`（C3 新建，
  本仓此前没有）。新鲜度三门的算术应当从它推导，而不是各自写一遍
  （见 `docs/learnings/freshness-gates-must-be-derived-from-pipeline-arithmetic.md`）。
