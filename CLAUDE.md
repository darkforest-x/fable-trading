# CLAUDE.md — fable-trading 工作规范

一句话：两层架构验证"双均线密集启动"信号——YOLO 检测层（L1）+ LightGBM 判断层（L2）。

**当前阶段：P0（形态定义与重复标注稳定性）→ P1（Gold Dataset）。**
执行层代码在 `yoyo/layers/l4_execution/`，但 `models/active_bundle.json` 不存在，
`require_active_bundle()` fail-closed，**生产上跑着 0 个模型**。
P0/P1 通过前禁止新训练与 promote。

- **东西放哪 / 一轮怎么走** → `docs/PROJECT_CHARTER.md`
- **当前真相、在等什么、下一条允许的动作** → `HANDOFF.md` 顶部
- **阶段与门** → `ROADMAP.md`
- 各阶段结论 → `analysis/p*_report.md`（索引在 `analysis/INDEX.md`）

## 铁律（违反 = 返工，没有例外）

1. **holdout 纪律**：holdout（≥2026-05-04）只在最终验收时评估，每次动用必须先获得项目
   所有者在对话中的明确批准，并在报告里记录"这是该配置第 N 次消耗 holdout"。
   训练/调参/特征选择的任何环节不得读取 holdout；看板、缓存与图表同样不得给未授权配置
   评分 holdout。`train.py` 不加 `--eval-holdout` 只是训练命令安全，不代表其他读取路径安全。
2. **时间切分**：所有评估按时间切分，禁止随机切分，禁止跨切点的样本进入训练。
3. **无前视**：特征只能使用信号 bar 及之前的数据；只有标签允许看未来。
   新增特征必须在 docstring 写明用到的列与窗口。
4. **单变量纪律**：一次实验只改一个变量；结果无论成败都写入报告。
   多变量打包改动需项目所有者批准并在 PROJECT_PLAN 记录（先例：2b-v2 三项打包，2026-07-07）。
5. **YOLO 增强禁用**：fliplr/flipud/mosaic/mixup/hsv 全关——它们破坏时间方向和红绿 K 线语义
   （旧项目 180 版失败的病因之一，见 README）。
6. **数据**：`data/` 不入 git；`data/kline_cache` 是旧项目缓存的只读软链接；
   新数据用 `python3 -m src.data.fetch_okx`（可断点续传，需本机网络）。
   **版本是契约不是依赖**：`torch`/`ultralytics`/`numpy` 三处必须同版本——
   Mac venv、3060、CI（`constraints-ci.txt`）。`scripts/train_on_3060.sh` 不一致就拒绝开训，
   理由是「结果无法与历史曲线对照」。装新库前先 `pip install --dry-run --report -` 看会不会降级。

## 实盘纪律（2026-07-20 起，与铁律同级）

7. **新鲜度三门同值**：执行器 max_signal_age_min / TG 过滤 / 看板 FRESH_DETECT_MIN
   当前 30min，由管道时序推导（15 bar + 7 脉冲/扫描 + 余量）；改动必须附延迟预算表
   且三处同改（见 `docs/learnings/freshness-gates-must-be-derived-from-pipeline-arithmetic.md`）。
8. **脉冲预算 <15min**：禁止往 forward 脉冲加扫描窗或新任务；阶段耗时看
   discover_wall / phase2_wall 日志，>600s 要查因。
9. **VPS 是唯一写者**：K 线与 forward_log.csv 只在 VPS 写；deploy 不推 data/kline_fetched。
10. **不自动 promote**：models/ACTIVE 与 frozen 默认配置的切换需 owner 点头；
    forward_log 不清空（清账 = owner 决策）。
11. **真金操作**（下单/撤单/kill 开关/改仓位/改 API key）只有 owner 亲手做或明确逐次授权。
12. **检测任务分流**（owner 2026-08-11 口径，覆盖 Local Signal V2 的 07-23 旧口径）：
    实盘执行路径**只扫 tip / tip-1 / tip-2 因果窗**；任何使用核心形态之后 K 线的模型
    都不得冒充新鲜盘口信号，不得直接进入 tip-smoke、forward、ACTIVE 或部署。
    若未来接入实盘，输出时间必须记为**完整检测窗右端**，并由 owner 另行批准延迟预算与执行架构。
    实盘检测器只认真 tip 金标 + tip-smoke，**自家 val / mAP / 旧 frozen-F1 不得作生产裁决**；
    无验证过的实盘检测器时管道诚实空转（`detector=none`）。

    - Local Signal V2 的**研究规格**（核心根数、确认窗、镜像与确认层级）→
      `docs/protocol/local_signal_v2.md`（owner 口径逐字保留）。本条只管纪律，那里管怎么做。
    - 「pre-v16 权重已三机清除」是**错的**，只清了两机；哪些权重还在见
      `analysis/p_model_inventory_20260820.md`，为什么记录不能当事实见
      `docs/learnings/purge-records-are-claims-not-facts.md`。
13. **单仓 + 单分支纪律**（owner 2026-07-30；单仓部分 2026-08-19 收敛后生效）：
    **fable-trading 是唯一 ACTIVE 交易研究仓**——`darkforest-one` / `yolo-xx` /
    `yoyo-trading` / `yoyo-eth` 已回迁并只读归档，**不得再开新仓**（`yoyo-eth-v2` /
    `yolo-new` / `fable-next` 一律不建）；新研究进 `experiments/active/<experiment_id>/`
    并注册到 `experiments/registry.yaml`。
    **只有 `main`，不开新分支、不建 worktree。**
    直接在 main 上提交、`git push origin HEAD:main`。**每次提交前先 `git branch --show-current`
    确认自己在 main**——曾有并行会话把 HEAD 切到别的分支，后续 6 个提交落在那里，
    只因用的是显式 `HEAD:main` 才没丢。需要隔离环境（并行 agent / 跨模型实现）时，
    先问 owner；owner 点头才开，且用完当轮就删。

14. **层间契约**（owner 2026-08-03 重构）：`yoyo/layers/` 的四层**禁止互相 import**，
    只能经 `yoyo/contracts/`（protocol / outcomes / costs / schema）和 `yoyo/data/`。
    由 `tests/boundaries/test_layer_imports.py` 用 AST 强制，不是口头约定。
    病因：2026-08-03 的 side/feature-semantics 故障横跨 forward_scan + frozen + executor
    三个文件——L2 的事实（模型用什么坐标系训的）被 L1 的事实（这单做多还是做空）决定了，
    而代码里没有任何东西反对。**旧 `src/` 是转发壳，迁移期并存；新代码一律写进 `yoyo/`。**

## 弱模型在本仓库最容易犯的错（每条都真实发生过或差点发生）

### 最贵的四条 —— 每一条都让这个项目损失过数月

读不完整份清单也要记住这四条。它们的共同点是：**把不是证据的东西当成了证据。**

- **把 AUC 当成功标准** → 本项目成功标准是 top-decile 扣 0.2% 往返成本后的净收益为正
  且置换检验 p<0.01；v1 的教训就是 AUC 0.59 照样亏钱。AUC 只是参考量。
- **报池子的绝对收益，不带对照组** → 2026-07-28：100×6m 池 +16.9bp 里 +7.2bp 是做空 beta，
  检测器自己只值 +9.0bp 而往返成本 10bp。见 `docs/learnings/pool-internal-metrics-cannot-see-beta.md`。
- **拿人工标注当天然可学习的目标** → 先量「标注时可见多少未来」：499 个 ⭐标杆里
  只有 2 个画在盘口，中位可见 97 根。见 `docs/learnings/zero-live-edge-labels-means-the-target-is-unverified.md`。
- **把窗口缩短当成因果化** → 决定看得见多少未来的是**窗口右端落在哪根**，不是窗口有多长。
  w20_midbox 从 200 根缩到 20–30 根，95.3% 的样本窗口右端仍晚于 decision bar（中位 9 根未来 K）。
  见 `docs/learnings/window-length-does-not-control-future-visibility.md`。

### 验收口径 —— 什么才算数

- **在 holdout 上"看一眼"** → 看一眼就是消耗一次，见铁律 1。
- **把 val/accept PF 当实盘** → 确认级只有前向新鲜 100 笔；v11 accept PF 高仍要前向终审。
- **只汇报好消息** → 报告必须含"风险与诚实声明"节；隐瞒失败的实验记录等于污染实验日志。
- **哈希对上就宣布数据集可复现** → 数据集有多个自由度：像素内容、split 落点、样本集合。
  w20_midbox 重建时 2635/2635 图片逐字节一致，**但 405 个样本的 split 落点全错**。
  见 `docs/learnings/reproducibility-is-per-axis-not-a-boolean.md`。
- **把"币波动高"和"这根波动在扩张"当成一个变量** → 这是两条方向相反的轴：币层波动水平
  在因果排名下是倒 U（榜单前 10% 超额胜率 t=1.32≈0），而 bar 层相对自身扩张 +9.21pp（p=5e-5）。
  用 bp 判永远判不清（TP/SL 精确 ±5/2 ATR），必须换 ATR 单位或胜率；见
  `docs/learnings/volatility-level-and-volatility-expansion-are-opposite-axes.md`。
- **按"当前涨跌幅榜单"挑币回测** → 排名窗必须在交易窗之前闭合。同窗排名下"高波动更赚"是单调的，
  换成上月排名单调性当场消失；见
  `docs/learnings/symbol-ranking-window-must-end-before-the-trading-window.md`。

### 标签与金标 —— 谁有资格说这是正例

- **把动态重裁剪当成重标注** → 动态短窗只能修复位置/上下文分布，不能把旧核心proposal变成
  新语义金标。先同时审类别与核心边界，再生成训练图；见
  `docs/learnings/dynamic-recrop-does-not-repair-label-semantics.md`。
- **有原始金标还让Codex二次手画** → 二次视觉重框会引入新的主观边界和宽度锚定。先逐框联结
  原Label Studio坐标与Owner方向裁决，外层只负责重裁，内层从原框中心派生；见
  `docs/learnings/original-gold-geometry-beats-secondary-manual-reboxing.md`。
- **为了正负1:1缩小金标禁入区** → 配对比例是软目标，Owner框保护、同币同时间块和时间隔离是
  硬约束；找不到安全背景就诚实缺样，禁止跨币、复用或靠近金标凑数；见
  `docs/learnings/negative-ratio-must-not-weaken-gold-exclusion.md`。
- **把一批偏右框统一左移** → 每张图的启动首根不同，统一delta只是换一个位置shortcut。先给K线
  编号，逐图把核心右端落在启动前一根，再重渲染新旧框对照；见
  `docs/learnings/per-image-reboxing-needs-indexed-boundaries-not-global-offsets.md`。
- **把人工审核的未来K线混进训练图** → Owner审核可以看更远未来，但训练短窗必须保持逐字节
  不变。审核未来单独目录/manifest，生成前后核对训练图SHA，未来目录禁止labels；见
  `docs/learnings/human-review-future-context-must-be-physically-separated-from-training-input.md`。
- **把未确认的方向镜像强塞进二分类** → 当前只确认空头参考时，多头镜像既不能混作同类正例，
  也不能当空头负例；先隔离为`mirror_unconfirmed`。见
  `docs/learnings/unconfirmed-mirror-is-neither-positive-nor-negative.md`。
- **把协议确认冒充逐样本确认** → Owner认可类别方向不等于确认扩展后的每张图/每个框；确认层级
  必须拆开记录。见`docs/learnings/protocol-confirmation-is-not-sample-confirmation.md`。

### 数据与复现

- **重跑 build_dataset 覆盖别的池的数据集** → 输出文件名必须带池名
  （`data/ma206/judgment_dataset_strict.csv` / `..._expanded.csv`），tag 必须带池名。
- **默认拉全部币种重新 fetch** → 先检查 `data/kline_fetched/` 已有 `okx_*_15m_*.csv`，
  fetcher 会自动跳过已完成币种。
- **默认「代码没变所以结果没变」** → 先比时间戳：产物 `generated_at` 早于
  `git log --diff-filter=A` 的 builder 首次入库时间 = 跑出它的代码不在 git 里，
  一切复现声明未经验证。**先提交 builder，再跑构建。**
  见 `docs/learnings/artifacts-built-before-their-builder-landed.md`。
- **改历史报告里的路径** → 禁止。`analysis/` 与 `docs/learnings/` 记录的是
  「当时发生了什么」，路径就是当时的真实路径。重构只改活文档，旧文档靠 `docs/RESTRUCTURE_MAP.md`
  查新旧对应。2026-08-03 一次改名正则把历史报告里真实跑过的命令改了，已回退。

### 运行安全 —— 碰到就可能动到真金

- **顺手调 strict/expanded 阈值预设** → 阈值是项目所有者决策，改动需批准。
- **改一道新鲜度门忘了另两道** → 三门必须同值，见实盘纪律 7。
- **往脉冲里塞实验扫描** → 超 15min 节拍 = 结构性挡 tip；见实盘纪律 8。
- **自动 promote / 清 forward_log** → 禁止；owner 点头。

### git 与环境

- **开新分支 / 建 worktree / 开新仓** → 默认禁止，**owner 点头才可以且用完当轮删**；见铁律 13。
  提交前先确认 `git branch --show-current` 是 main。
- **用 `git status --short` 验收 .gitignore 改动** → 它把未跟踪目录折叠成一行，
  9 万个文件缩成 14 行，泄漏根本看不见。一律用 `-uall` 展开，再加
  `git add --dry-run -A <dir> | wc -l` 看真实会 stage 多少。
  另：目录级排除（`datasets/`）之下所有 `!` 否定规则都是死的，git 不会下降进去；
  忽略规则按体积/扩展名写，别按目录名写。见
  `docs/learnings/directory-level-gitignore-kills-every-negation-below-it.md`。

## 质量标准（可检查，不是形容词）

每轮实验的交付物是 `analysis/pXX_report.md` （源文件，用于 git 历史与复现）。

**必须步骤** (铁律，无例外)：
1. 完成 md 报告后，立即转换为 HTML：
   ```bash
   python3 scripts/md_to_html.py analysis/pXX_xxx.md --out-dir analysis/html
   ```
2. **交付给 owner 的是 HTML** (`analysis/html/pXX_xxx.html`)，浏览器直接打开。

md 源文件必须包含：

- [ ] 复现命令（从零跑通的完整命令序列）
- [ ] 数据统计（候选数 / 正类率 / 时间范围 / val 样本数）
- [ ] 结果表，且与上一版本同表对照
- [ ] 必报指标：val AUC、置换检验 p、top-decile 毛/净收益、胜率、单特征基线对照
- [ ] **匹配随机对照组**（同币 × 同时间块 × 同波动桶的随机入场，同障碍同成本）——
      方向性策略的每张结果表都要带。置换检验只验排序，抓不到整池踩在 beta 上
- [ ] **非方向性实验**（标签质量、渲染 parity、数据审计等没有收益可言的）：
      上面两条按字面不适用。**不许编造，也不许留空**——写一句说明为什么不适用，
      并给出**同等严格的零假设对照**（例：标签质量审计把标签打乱重跑同一方法，
      看真实标签的疑似错标率是噪声的几分之一）。
      先例：`analysis/p1_gold_label_quality_20260820.md`
- [ ] 解读（每个数字变化的归因）
- [ ] 风险与诚实声明
- [ ] 下一步选项（标注哪些需要项目所有者决策）

代码标准：python3 + pandas/lightgbm/ultralytics，模块级 docstring 说明来源与决策依据
（现有代码都是这个风格，照着写）。

**依赖有两类，别混：**
- **契约**（`torch` / `ultralytics` / `numpy` / `pandas`）——Mac、3060、CI 三处同版本，
  改动 = 改跨机契约，改完历史曲线不再可比。锁在 `constraints-ci.txt`。
- **评估用**（cleanlab、aeon 之类）——**装进独立 venv**，见 `requirements-eval.txt`。
  cleanlab 会把 numpy 降到 1.26，直接装进主 venv 就打断了上面那条契约。
  装之前先 `pip install --dry-run --report -` 看会动哪些版本。

## 不确定时的升级规则

- 涉及 **holdout、阈值预设、障碍参数（TP/SL 倍数、atr 下限）、成本假设（0.2%）** 的任何
  改动 → 停下来问项目所有者，不要"先试试"。
- 涉及 **新鲜度门、脉冲预算、ACTIVE/frozen 切换、清空 forward_log、promote owner_best、
  真下单/改仓** → 同上，见实盘纪律 7–11。
- 数据源不可用或返回结构变化 → 如实报告现象，不要静默换数据源或造数据。
- 结果好得反常（AUC 突然 >0.7、净收益突然翻倍、accept PF 夸张）→ 第一假设是泄漏或 bug，
  写最小复现验证后再汇报；确认级只认前向新鲜样本。
- 项目所有者用中文交流，汇报用中文；代码与注释用英文。

## 收敛后的入口（2026-08-19）

- **注册表是入口**：实验进 `experiments/registry.yaml`，产物进 `artifacts/registry.yaml`。
  `production_eligible` / `training_eligible` 默认 false，改动需 owner。
- **守门测试别绕**（`tests/boundaries/` + `tests/causality/` + `tests/parity/`）：
  `yoyo` 必须解析在本仓内、sys.path 不许出现兄弟仓、holdout 定义不许漂移、
  迁移资产逐个重算哈希、CI 必须从 `requirements.txt` 装且锁版本。
  **红了先看它在说什么，别先想怎么让它绿**——每一条都是有人踩过的坑。
- **两个 ATR 实现不一致**（warmup 播种，bar 14 差 0.109），已钉住未修，
  等 owner 裁决：`docs/consolidation/DUPLICATE_SEMANTICS.md` §4。
- 四仓历史结论（含全部负面结果）在 `experiments/historical/`，
  验收在 `reports/consolidation/FINAL_ACCEPTANCE.md`。

## learning law

每解决一个非平凡问题（修 bug、架构决策、反直觉结论），先运行 extract-approach skill
在 `docs/learnings/` 留下笔记再继续。没有 learnings 笔记的解决方案视为未完成的工作。
