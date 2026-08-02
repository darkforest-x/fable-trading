# P0.0 基线审计 —— 仓库现状 vs Grok Build 接管计划

**日期**:2026-08-03
**依据**:Notion《Fable Trading｜Grok Build 接管计划(2026-07-31)》00/02/04/05 页
**动作性质**:只读审计。未碰 holdout、未改 ACTIVE、未 promote、未 deploy、未下单、未清 forward_log。

计划书 00 页要求接管方"先给可核验的基线事实,不许只回已了解"。本文是那份基线,
并逐条核对计划书列出的 P0-01…P0-08 风险在**全量仓库**里是否仍然成立 ——
计划书写在 07-31 的 lite 快照上,自己也声明不得假设快照等于线上。**结论是快照确实已过时,
而过时的方向不是"问题变少了"。**

---

## 一、最重要的发现:live 路径上存在 train/serve 特征错配

**计划书 P0-03 明文禁止的那件事,已经发生了。** 原文:

> 不能只修改 forward 为 short-aligned 后继续加载旧 v10 模型,否则会制造新的
> train/serve 分布错配。

端到端实测,四步都可复现:

```
1. load_runtime_artifact()
   → frozen_tp5_sl2_swap_yolo_v10_reg_20260731.json   (side=short)
2. _artifact_trade_side(artifact) → "short"
3. forward_scan.py:201 → extract_feature_rows_for_side(..., "short")
   → align_short_feature_rows() 重写 10 列,其中 6 列取负
4. 该模型的训练集 data/judgment_yolo_swap_v10.csv 的特征
   = 未对齐的 extract_feature_rows()
```

第 4 步不是推断,是逐值比对。取 14 行,两种提取各算一遍,与池中存值比:

```
                     池中存值      plain 提取     short 对齐
ext_up             -0.027392     -0.027392      +0.016265
close_vs_ema55     -0.026739     -0.026739      +0.027473
close_vs_ema200    -0.059420     -0.059420      +0.063174
order_score        +1.000000     +1.000000      +3.000000
slow_slope_12      -0.004687     -0.004687      +0.004687
ret_24             -0.031411     -0.031411      +0.031411

14 行 → 更像 plain 的 14 行,更像 short 对齐的 0 行(且为逐位精确相等)
```

**模型学的是 `slow_slope_12 = -0.0047` 这类输入,现在被喂进 `+0.0047`。**
六个方向性特征的符号全反,`order_score` 走的是另一套取值。这不是分布漂移,是坐标系倒置。

**病因是好意**:commit `32e556b`(fix: short protocol P0 — side-aware forward)
按计划书 P0-01 把 forward 从硬编码 `side="long"` 改成了 side-aware,这一步本身正确;
但 `models/ACTIVE` 仍指着用 legacy 语义训练的 v10。计划书早就写明这两步必须同时做,
或者干脆把旧模型标成 execution-ineligible —— **修了推理端而没动模型端,正是它警告的那半步。**

### 为什么这没有变成真金损失

`src/execution/executor.py:235` 对非 long 一律拒单:

```python
if trade_side != "long":
    ... "current executor is long-only; refused signal side=..."
```

所以 short 信号到不了下单。**计划书验收矩阵 A-02/A-07(short 不得产生 buy)在当前代码上成立。**
受污染的是 paper / forward 记账的分数,不是仓位。

---

## 二、六项基线事实(计划书 00 页第 4 节要求的格式)

**1. 分支与工作树**

```
分支 main   工作树 0 项改动   与远程 落后 0 · 领先 0
其他分支 0 个   worktree 0 个
```

满足验收矩阵 I-07(全部提交在 main,无新 branch/worktree)。

**2. models/ACTIVE 与 artifact 哈希**

```
ACTIVE = models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.txt
  .txt   sha256 4ab5ab98af492e4b…
  .json  sha256 31170d758678d1ab…
  created_at 2026-07-30T16:27:35Z   28 特征
  threshold_val_q90 = -0.0004397139085409754
  holdout_policy: excluded from training and threshold selection; not evaluated

检测器 models/owner_short_star_v10.pt  sha256 86d969c830189b2d…
  注:models/owner_best.pt 的 sha256 与之完全相同 —— 同一份权重两个名字
```

**dataset_sha256 与实际文件一致** (`9bca6802…`),artifact 身份没有漂移。

**3. 关键数据文件**

```
data/judgment_v10_wide.csv        22M   18,379 行   有
data/judgment_yolo_swap_v10.csv   12M   18,379 行   有
data/kronos_feats_v10.csv        2.8M   18,255 行   有
data/forward_log.csv             4.0K        0 行   ← 只有表头
```

**forward_log 是空的。** 计划书 D-06 说"P0 不清 forward log,旧记录保留并标记 legacy",
但这里已经没有旧记录可标 —— 仅存的 35 行在 `vps_rescue/`(VPS 退租时抢救下来的)。
**这与计划书的前提不符,需要 Owner 知道。**

**4. VPS 证据**

VPS 已到期不再续费,服务证据只能从仓库判断,取不到 live service/ledger。
计划书"技术停止条件"第 5 条(forward/executor service 实际代码不在快照中)在这里
表现为服务本身已不存在。

**5. 计划书引用的 `docs/grok_build/` 在仓库里不存在**

00 页要求先读 `docs/grok_build/00_MASTER_PROJECT_PLAN.md` 等 5 个文件,**仓库里没有这个目录**。
这些内容目前只存在于 Notion。若要按计划执行,需先把它们落盘。

**6. 测试基线**

```
排除 tests/test_eth3m_v2_classification.py:  328 passed, 2 skipped, 9.2s
包含它:                                       无限挂起,整套卡在 21%
```

挂起的是 `test_full_frame_transform_is_deterministic_and_uncropped`,由 `f8c7178` 引入。
**一个挂起的测试会让"跑全套"变成不可执行的验收动作**,应在 P0 前修掉。

---

## 三、P0-01…P0-08 逐条现状

| 计划书条目 | lite 快照说 | 全量仓库实测 |
|---|---|---|
| **P0-01** short artifact 写成 long | 存在 | **已修**(`32e556b`);side/barrier/feature 都跟 artifact 走 |
| **P0-02** 缺失 side 默认 long | 存在 | **仍在**:`executor.py:142` `return side or "long"` |
| **P0-03** feature semantics 分叉 | 风险 | **已实际发生**,见第一节 |
| **P0-04** barrier/return 分叉 | 存在 | 未验(labeling.py 默认 TP4 vs forward TP5 未逐一核对) |
| **P0-05** signal/decision/fill 混一体 | 存在 | 未验 |
| **P0-06** artifact 非单一权威 | 存在 | **仍在**:`frozen.py:258` 仍 glob+sorted,332 行仍有 "runtime fallback latest default" |
| **P0-07** tip age 缺全局断言 | 存在 | 未验 |
| **P0-08** signal_key 含 score | 存在 | **已修**:`executor.py:31` docstring 明写 "Intentionally excludes score" |

**三条已修、两条仍在、一条已恶化成实际故障、三条未验。**

另注:`_artifact_trade_side()` 在拿不到 side 时 `return "long"` —— 与 P0-02 同一类 fail-open,
计划书没单列,但属于同一条"缺失即默认 long"的病。

---

## 四、与我 07-30 结论的交叉核对

计划书 00 页称"当前 q90 阈值并不等于运行时 top-decile;固定门在 val 放行约 91.2%"。
这条若同样适用于我 07-30 报的顶档提升,那些数就得重新解释。**实测否定了这种波及**:

```
15 折,每折选中 613/6126 = 10.0%,分值唯一率 100%,边界并列 0 个
```

生产是**固定阈值 + 可能饱和的分类器输出**,我的诊断是**每折分位数 + 连续回归输出**,
不是同一机制。计划书那条对 live 路径依然成立且严重,只是够不到研究侧的数字。
脚本:`scripts/diag_topdecile_is_really_a_decile.py`,commit `5d5c120`。

---

## 五、复现命令

```bash
# 特征语义比对(第一节的 14 行逐值比对)
PYTHONPATH=. .venv/bin/python -c "
from src.judgment.frozen import load_runtime_artifact
from src.judgment.forward_scan import _artifact_trade_side
a = load_runtime_artifact(); print(a.metadata_path.name, _artifact_trade_side(a))"

# 顶档是不是真十分位
PYTHONPATH=. .venv/bin/python scripts/diag_topdecile_is_really_a_decile.py

# 测试基线
.venv/bin/python -m pytest tests/ -q --deselect tests/test_eth3m_v2_classification.py
```

---

## 六、风险与诚实声明

- 本文只做只读审计,**未修任何代码**。第一节的错配尚未修复,现在仍在。
- **未消耗 holdout**(计数仍为 9),未改 ACTIVE,未 promote,未 deploy,未下单,未动 forward_log。
- P0-04/05/07 标为"未验"就是没验,不是验过没问题。
- 特征比对取 14 行、覆盖少数币种。逐位精确相等的证据很强,但不等于全池 18,379 行都已核对。
- 我不是计划书的作者,对其中经济假设(return convention、成本路由)不做评价,那些是 Owner 门。

---

## 七、需要 Owner 决策的事项

1. **第一节的错配怎么修**:两条路 —— (a) 把 ACTIVE 标成 execution-ineligible / 回退到
   legacy 语义提取,(b) 按计划书 P1/P2 用 side-aligned 特征重建数据并重训。
   **(a) 是止血,(b) 是计划书的正路。P0 阶段不许重训,所以现在只能选 (a)。**
2. **forward_log 已空**,与计划书"保留旧记录标 legacy"的前提不符;`vps_rescue/` 的 35 行是否并回。
3. **是否把 Notion 的 7 页落盘成 `docs/grok_build/`**,让计划书的必读顺序在仓库里成立。
4. 挂起的测试是否在 P0 之前修(它让"全套测试通过"这个验收项当前无法执行)。
