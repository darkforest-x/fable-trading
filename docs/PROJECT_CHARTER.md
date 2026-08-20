# 项目章程 — fable-trading

> **这份文件管「项目是什么、东西放哪、一轮怎么走」。变化最慢。**
> 当前进度看 `HANDOFF.md` 顶部；铁律看 `CLAUDE.md`；阶段门看 `ROADMAP.md`。
> 2026-08-20 单仓收敛后确立。

---

## 0. 一句话

验证一个交易假设：**K 线多均线「密集后启动」形态，在启动初期可被识别，
且其中一小部分在扣除成本后可交易。**

两层：YOLO 检测「长得像的」（L1）→ LightGBM 排序「值得进的」（L2）→ 回测（L3）→ 执行（L4）。
外加一套防自欺的实验纪律——**那套纪律是这个项目真正的资产**，模型换了好几版，纪律没换。

当前阶段：**P0（形态定义与重复标注稳定性）→ P1（Gold Dataset）**。
P0/P1 通过前禁止任何新训练与 promote。

---

## 1. 四份权威文档，各管一段，不重叠

单仓收敛之前，五个仓各有一份 `HANDOFF.md`，谁都自称当前真相。
现在只有一套，并且**刻意切分职责**——同一件事只在一处写：

| 文件 | 管什么 | 变化频率 | 不该出现什么 |
|---|---|---|---|
| `HANDOFF.md` 顶部 | **当前真相**：进行到哪、在等什么、下一条允许的动作 | 每轮 | 架构说明、纪律条文 |
| `CLAUDE.md` = `AGENTS.md` | **铁律与实盘纪律**（约束） | 很少 | 进度、实验结果 |
| `ROADMAP.md` | **阶段与门**（P0–P5 各自的通过条件） | 阶段推进时 | 单次实验细节 |
| `docs/PROJECT_CHARTER.md`（本文） | **结构与流程**：东西放哪、一轮怎么走 | 几乎不变 | 当前进度、具体数字 |

`docs/DOC_MAP.md` 是索引，不是内容。

**写东西之前先问：这属于上面哪一段？** 答不上来就是它不该被写成新文档，
而应该进 `analysis/pXX_report.md`（单次实验）或 `docs/learnings/`（非平凡教训）。

---

## 2. 仓库规范

### 只有一个仓

`darkforest-x/fable-trading` 是**唯一 ACTIVE 交易研究仓**。
`darkforest-one` / `yolo-xx` / `yoyo-trading` / `yoyo-eth` 已回迁并只读归档。

**不得再开新仓。** 不建 `yoyo-eth-v2`、`yolo-new`、`fable-next`，
也不为了「只是试一下」开临时仓。

为什么：收敛前 63 个文件 import 的 `yoyo` 解析到另一个仓，
本仓跑不起来除非那个仓在磁盘上；35 个脚本还一直在用另一个仓的 `render.py`——
而检测器绑死在那些像素上。**分仓的代价不是麻烦，是你不知道自己在跑哪份代码。**

### 只有一个分支

只有 `main`。不开新分支、不建 worktree（需要隔离环境先问 owner，用完当轮删）。

```bash
git branch --show-current      # 每次 commit 前先确认是 main
git push origin HEAD:main      # 显式目标，别用裸 push
```

### 新研究去哪

```
experiments/active/<experiment_id>/
```
并注册进 `experiments/registry.yaml`。**不开新仓、不开新分支、不建新目录树。**

---

## 3. 东西放哪（决策表）

| 我要做的事 | 放哪 |
|---|---|
| 跨层共享的语义（成本、障碍、holdout、形态、候选） | `yoyo/contracts/` |
| 读 K 线、算指标、连续性/可用性检查 | `yoyo/data/` |
| 检测层：渲染、候选发现、causal onset、数值基线、teacher | `yoyo/layers/l1_detection/` |
| 判断层：特征、标签、训练、冻结工件 | `yoyo/layers/l2_judgment/` |
| 回测 | `yoyo/layers/l3_backtest/` |
| 执行 | `yoyo/layers/l4_execution/` |
| 切分、匹配对照、置换检验、经济门 | `yoyo/evaluation/` |
| 产物登记与血统 | `yoyo/artifacts/` |
| 一次性实验脚本 | `scripts/`（可复用的提进 `yoyo/`） |
| owner 审核工具 | `tools/review/` |
| 金标行、审核裁决 | `datasets/annotations/` |
| 数据集身份文件 | `datasets/manifests/` |
| 单次实验报告 | `analysis/pXX_*.md` → 转 HTML 交付 |
| 非平凡教训 | `docs/learnings/`（只增不改） |
| 归档仓的历史结论 | `experiments/historical/` |
| 大体积产物（图片/权重/runs） | `archive/consolidated/`，**不入 git** |

**新代码一律写进 `yoyo/`。** `src/` 下 23 个模块是转发壳，旧 import 仍可用，但别往里加东西。

---

## 4. 架构：四层 + 契约

```
                    yoyo/contracts/        ← 所有层都可以 import
                    yoyo/data/             ← 所有研究层都可以 import
                          ↑
   L1 detection  →  L2 judgment  →  L3 backtest  →  L4 execution
        （层与层之间禁止互相 import，只能经 contracts / data）
```

**这不是整洁问题。** 2026-08-03 有一个故障横跨 forward_scan、frozen 和 executor，
因为 L2 的事实（模型用什么坐标系训的）被 L1 的事实（这单做多还是做空）决定了，
而代码里没有任何东西反对。现在由 `tests/boundaries/test_layer_imports.py` 用 AST 强制。

### 契约模块

| 模块 | 管的语义 |
|---|---|
| `costs.py` | 成本路由表（owner 决策值，禁止调） |
| `outcomes.py` | 障碍/出场解算（**唯一实现**，同 bar TP/SL 抢先口径是 owner 决策） |
| `holdout.py` | 冻结边界 `2026-05-04T00:00:00Z`（**唯一定义**，另有 11 处旧定义由测试比对） |
| `pattern.py` | PatternEvent：时间语义 + 五条规则（见下） |
| `candidates.py` | CandidateProposal：proposal 不是 signal |
| `protocol.py` | ACTIVE bundle 的加载与 fail-closed 闸门 |
| `artifacts.py` | 注册表 schema |

### PatternEvent 的五条规则

1. `visible_end_at <= decision_at` —— 标注者不许看过判断的那根之后
2. 锚点必须落在自己的窗口内
3. `pattern_valid` 不许用「后来涨/跌了」来论证
4. `causal_onset_i` 必须有人给的 warrant，**框的右边界不算**
5. proposal 不是 gold —— `training_eligible` 需要人的来源

---

## 5. 一轮实验的完整流程

```
① 注册        写进 experiments/registry.yaml（question / single_variable / status: active）
② 建目录      experiments/active/<experiment_id>/
③ 先落 builder  提交生成器与测试，再跑生成器，再提交小型产物
              （产物 generated_at 早于 builder 入库时间 = 复现声明未经验证）
④ 跑          单变量：一次只改一个东西
⑤ 报告        analysis/pXX_*.md，必备节见下
⑥ 转 HTML     python3 scripts/md_to_html.py analysis/pXX_*.md --out-dir analysis/html
⑦ 交付        给 owner 的是 HTML 的可打开链接，不是 .md 路径
⑧ 回填注册表  status → accepted / rejected / inconclusive / superseded + result
⑨ learnings   非平凡问题跑 extract-approach，写 docs/learnings/
```

### 报告必备节（缺一不可）

- [ ] 复现命令（从零跑通的完整序列）
- [ ] 数据统计（候选数 / 正类率 / 时间范围 / val 样本数）
- [ ] 结果表，且与上一版本同表对照
- [ ] 必报指标：val AUC、置换检验 p、top-decile 毛/净收益、胜率、单特征基线对照
- [ ] **匹配随机对照组**（同币 × 同时间块 × 同波动桶 × 同 horizon × 同成本）
- [ ] 解读（每个数字变化的归因）
- [ ] **风险与诚实声明**
- [ ] 下一步选项（标注哪些需要 owner 决策）

---

## 6. 验收：三重经济门，缺一即否

由 `yoyo/evaluation/economic_gates.py` 执行：

```
top-decile 扣 0.2% 往返成本后净收益 > 0
置换检验 p < 0.01
跑赢匹配随机对照
```

**AUC 是参考量，不是成功标准。** v1 的 AUC 0.59 照样亏钱。

三条各自防一种错：净收益防「毛边小于手续费」；置换防「top-decile 只有 5 个样本」；
匹配对照防「整池踩在 beta 上」——置换检验看不见后者，因为它固定池只打乱排序，
一个站在做空 beta 上的池打乱之后还是那个 beta。

`evaluate_economic_gates()` 的对照组参数是**必填**的：设成可选就等于给「忘了带对照」
留了个看起来正常的短签名。

---

## 7. 守门测试：别绕过，绕过就是把已经付过的学费再付一次

```bash
python3 -m pytest tests -q        # 从仓库根跑，1248 个用例
```

| 测试 | 防的是什么 | 代价已付过 |
|---|---|---|
| `tests/boundaries/test_layer_imports.py` | 层间越界 import | 2026-08-03 三文件横跨故障 |
| `tests/boundaries/test_yoyo_package_is_local.py` | `yoyo` 解析到仓外 | 迁一半和迁完了指标完全相同 |
| `tests/boundaries/test_no_cross_repository_bridges.py` | sys.path 出现兄弟仓 | 35 个脚本跑的是另一个仓的 render.py |
| `tests/boundaries/test_execution_bundle_only.py` | 未 promote 的模型进下单路径 | — |
| `tests/boundaries/test_experiment_isolation.py` | experiments/ 改动生产状态 | — |
| `tests/boundaries/test_bulk_archive_stays_out_of_git.py` | 20 GB 进 git / README 被一起忽略 | 目录级 ignore 杀死否定规则 |
| `tests/causality/test_future_mutation.py` | 特征偷看未来 | — |
| `tests/causality/test_holdout_boundary_is_single_valued.py` | 11 处 holdout 定义漂移 | — |
| `tests/parity/test_migration_ledger_parity.py` | 迁入文件被改而没记账 | — |
| `tests/parity/test_duplicate_semantics.py` | 同语义多实现悄悄分叉 | 两个 ATR 至今不一致 |
| `tests/contracts/test_known_conclusions.py` | 历史负面结论被改写 | 任务书里有四条没发生的「结论」 |

**测试红了先看它在说什么，不要先想怎么让它绿。** 上面每一条都是有人已经踩过的坑。

---

## 8. 什么必须停下来问 owner

```
holdout（读取 / 记账 / 清零）
阈值预设、障碍参数（TP/SL 倍数、atr 下限）、成本假设
新鲜度三门、脉冲预算
ACTIVE / frozen 切换、promote、清空 forward log
真金操作：下单 / 撤单 / kill 开关 / 改仓位 / 改 API key
开新仓、开新分支、建 worktree
多变量打包改动
```

另外三种情况也要停：

- **数据源不可用或结构变化** → 如实报告，不静默换源、不造数
- **结果好得反常**（AUC 突然 >0.7、净收益翻倍、accept PF 夸张）→ 第一假设是泄漏或 bug
- **发现两个实现算同一个东西但结果不同** → 量化并登记，不要自己选一个（如两个 ATR）

---

## 9. 环境

```
Python      ~/fable-trading/.venv（3.9）
训练        Windows 3060 zzc@192.168.1.2，C:/fable/.venv/Scripts/python.exe（7 倍速）
            Mac 只做数据 / 评估 / 决策
K 线        VPS 是唯一写者；data/kline_fetched 不从 deploy 推
看板        http://103.214.174.58:8642，更新用 scripts/deploy_vps.sh
大体积产物   archive/consolidated/（写时复制克隆，不入 git）
```

跑测试从仓库根：`python3 -m pytest tests -q`。
从别处跑会让 `yoyo` 解析到 venv 里 yoyo-trading 的 editable 安装——
守门测试会当场红，不会静默跑错。

---

## 10. 这个项目最容易犯的错

完整清单在 `CLAUDE.md`「弱模型在本仓库最容易犯的错」一节（每条都真实发生过）。
四条最贵的：

1. **把 AUC 当成功标准** —— 成功标准是三重经济门
2. **报池子的绝对收益不带对照组** —— +16.9bp 里 +7.2bp 是做空 beta
3. **拿人工标注当天然可学习的目标** —— 499 个标杆里只有 2 个画在盘口，中位可见 97 根未来
4. **把窗口缩短当成因果化** —— 决定看见多少未来的是窗口**右端落在哪根**，不是窗口多长
