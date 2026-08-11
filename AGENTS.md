# AGENTS.md — fable-trading 工作规范

一句话：两层架构验证"双均线密集启动"信号——YOLO 检测层（2a）+ LightGBM 判断层（2b），
2026-07 起进入 **VPS 实盘阶段**（执行层 + 前向 100 笔新鲜裁决）。
当前进度与下一步看 `HANDOFF.md` 顶部"当前真相"；各阶段结论看 `analysis/p*_report.md`；
本周执行计划看 `analysis/week_plan_20260720.md`；路线图（历史）看 `PROJECT_PLAN.md`。

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
12. **检测任务分流（owner 2026-08-11 最新口径，覆盖 Local Signal V2 的 07-23 旧口径）**：
    实盘执行路径仍只扫 tip/tip-1/tip-2 因果窗；任何使用核心形态之后 K 线的模型都不得冒充
    新鲜盘口信号，不得直接进入 tip-smoke、forward、ACTIVE 或部署。若未来接入实盘，输出时间
    必须记为完整检测窗右端，并由 owner 另行批准延迟预算和执行架构。

    **Local Signal V2 研究主目标已改为短延迟事后形态检测**：标签是“完美平台/启动形态”语义，
    不是固定裁剪模板。Owner 的 ETH 参考中，核心约 4–7 根，边界是两条竖线之间的平台/转折段；
    红框不得包入右侧快速下跌。输入窗口不固定为 20–30 根，必须从最短充分上下文开始动态变化；
    当前首轮只试约 14–22 根，并继续按 precision 向更短收缩。核心结束后只允许 **3–5 根**确认：
    3 根优先，5 根为硬上限，6–10 根撤出。红框位置随最短充分上下文自然变化，不得固定最右或
    正中。验收分别报告 delay 3/4/5 的首次命中，精确度优先。不得因为后面已经上涨/下跌就自动
    把核心框判成正例。旧 W20–30 派生框只能作复核来源；但能够逐框追溯到Label Studio坐标、
    又经Owner亲自确认short方向的原始金标，是新合同的几何源。外层可重裁，内框只能按Owner批准
    的中心截取规则从原坐标派生，禁止Codex或模型二次目测重画；未经Owner确认裁切合同不得训练。
    Stage A 数据和权重继续保留作表征底座。该研究不再以严格因果 Stage B + 真 tip 作为离线检测器唯一验收；但
    `production_eligible=false`，直到 owner 单独批准生产用途。今天 ETH 图是语义参考尺，不是
    坐标模板，不得据此删除或替代既有 Stage A 数据、权重、日志和候选池。当前ETH参考只冻结
    空头语义；Owner未明确多头镜像策略前，一律标为`mirror_unconfirmed`，既不进正例也不进负例。
    Owner确认类别协议/代表板只设置`owner_protocol_confirmed=true`，不得批量推导
    `sample_owner_confirmed=true`；协议级确认与逐样本金标必须分层记录。

    ~~pre-v16 检测器权重已三机清除（仅存 COCO yolo11 底座）~~
    **事实更正 2026-08-05：只清除了两机。
    Mac 与 VPS 已删，Windows 3060（`C:\fable`）上 59 个权重完好，含 v8_chain / v9 四版 /
    v10_chain / v14 / v15 / v16 / short_star 全系。唯 v11、v12、v13 不在 3060——那三版是
    Mac 上 MPS 训的；v12/v13 的 Mac 副本尚存，v11 两头皆空，是唯一真正不可恢复的模型。
    “不用事后模型冒充新鲜实盘信号”的纪律不变，但“权重已不存在”不能再当前提，
    见 `docs/learnings/purge-records-are-claims-not-facts.md`。**
    实盘检测器仍只认真 tip 金标 + tip-smoke，自家 val/mAP/旧 frozen-F1 不得作生产裁决；
    无验证过的实盘检测器时管道诚实空转（detector=none）。
13. **单分支纪律**（owner 2026-07-30）：**只有 `main`，不开新分支、不建 worktree。**
    直接在 main 上提交、`git push origin HEAD:main`。**每次提交前先 `git branch --show-current`
    确认自己在 main**——曾有并行会话把 HEAD 切到别的分支，后续 6 个提交落在那里，
    只因用的是显式 `HEAD:main` 才没丢。需要隔离环境（并行 agent / 跨模型实现）时，
    先问 owner；owner 点头才开，且用完当轮就删。

14. **层间契约**（owner 2026-08-03 重构）：`yoyo/layers/` 的四层**禁止互相 import**，
    只能经 `yoyo/contracts/`（protocol / outcomes / costs / schema）和 `yoyo/data/`。
    由 `tests/test_layer_boundaries.py` 用 AST 强制，不是口头约定。
    病因：2026-08-03 的 side/feature-semantics 故障横跨 forward_scan + frozen + executor
    三个文件——L2 的事实（模型用什么坐标系训的）被 L1 的事实（这单做多还是做空）决定了，
    而代码里没有任何东西反对。**旧 `src/` 是转发壳，迁移期并存；新代码一律写进 `yoyo/`。**

## 弱模型在本仓库最容易犯的错（每条都真实发生过或差点发生）

- **把 AUC 当成功标准** → 本项目成功标准是 top-decile 扣 0.2% 往返成本后的净收益为正
  且置换检验 p<0.01；v1 的教训就是 AUC 0.59 照样亏钱。AUC 只是参考量。
- **在 holdout 上"看一眼"** → 看一眼就是消耗一次，见铁律 1。
- **重跑 build_dataset 覆盖别的池的数据集** → 输出文件名必须带池名
  （`data/ma206/judgment_dataset_strict.csv` / `..._expanded.csv`），tag 必须带池名。
- **顺手调 strict/expanded 阈值预设** → 阈值是项目所有者决策，改动需批准。
- **只汇报好消息** → 报告必须含"风险与诚实声明"节；隐瞒失败的实验记录等于污染实验日志。
- **默认拉全部币种重新 fetch** → 先检查 `data/kline_fetched/` 已有 `okx_*_15m_*.csv`，
  fetcher 会自动跳过已完成币种。
- **把 val/accept PF 当实盘** → 确认级只有前向新鲜 100 笔；v11 accept PF 高仍要前向终审。
- **报池子的绝对收益，不带对照组** → 2026-07-28：100×6m 池 +16.9bp 里 +7.2bp 是做空 beta，
  检测器自己只值 +9.0bp 而往返成本 10bp。见 `docs/learnings/pool-internal-metrics-cannot-see-beta.md`。
- **拿人工标注当天然可学习的目标** → 先量「标注时可见多少未来」：499 个 ⭐标杆里
  只有 2 个画在盘口，中位可见 97 根。见 `docs/learnings/zero-live-edge-labels-means-the-target-is-unverified.md`。
- **把窗口缩短当成因果化** → 决定看得见多少未来的是**窗口右端落在哪根**，不是窗口有多长。
  w20_midbox 从 200 根缩到 20–30 根，95.3% 的样本窗口右端仍晚于 decision bar（中位 9 根未来 K）。
  见 `docs/learnings/window-length-does-not-control-future-visibility.md`。
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
- **哈希对上就宣布数据集可复现** → 数据集有多个自由度：像素内容、split 落点、样本集合。
  w20_midbox 重建时 2635/2635 图片逐字节一致，**但 405 个样本的 split 落点全错**。
  见 `docs/learnings/reproducibility-is-per-axis-not-a-boolean.md`。
- **默认「代码没变所以结果没变」** → 先比时间戳：产物 `generated_at` 早于
  `git log --diff-filter=A` 的 builder 首次入库时间 = 跑出它的代码不在 git 里，
  一切复现声明未经验证。**先提交 builder，再跑构建。**
  见 `docs/learnings/artifacts-built-before-their-builder-landed.md`。
- **改一道新鲜度门忘了另两道** → 三门必须同值，见实盘纪律 7。
- **往脉冲里塞实验扫描** → 超 15min 节拍 = 结构性挡 tip；见实盘纪律 8。
- **自动 promote / 清 forward_log** → 禁止；owner 点头。
- **改历史报告里的路径** → 禁止。`analysis/` 207 份与 `docs/learnings/` 234 条记录的是
  「当时发生了什么」，路径就是当时的真实路径。重构只改活文档，旧文档靠 `docs/RESTRUCTURE_MAP.md`
  查新旧对应。2026-08-03 一次改名正则把历史报告里真实跑过的命令改了，已回退。
- **开新分支 / 建 worktree** → 禁止；见铁律 13。提交前先确认 `git branch --show-current` 是 main。
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
- [ ] 解读（每个数字变化的归因）
- [ ] 风险与诚实声明
- [ ] 下一步选项（标注哪些需要项目所有者决策）

代码标准：python3 + pandas/lightgbm/ultralytics，无新增重型依赖；模块级 docstring
说明来源与决策依据（现有代码都是这个风格，照着写）。

## 不确定时的升级规则

- 涉及 **holdout、阈值预设、障碍参数（TP/SL 倍数、atr 下限）、成本假设（0.2%）** 的任何
  改动 → 停下来问项目所有者，不要"先试试"。
- 涉及 **新鲜度门、脉冲预算、ACTIVE/frozen 切换、清空 forward_log、promote owner_best、
  真下单/改仓** → 同上，见实盘纪律 7–11。
- 数据源不可用或返回结构变化 → 如实报告现象，不要静默换数据源或造数据。
- 结果好得反常（AUC 突然 >0.7、净收益突然翻倍、accept PF 夸张）→ 第一假设是泄漏或 bug，
  写最小复现验证后再汇报；确认级只认前向新鲜样本。
- 项目所有者用中文交流，汇报用中文；代码与注释用英文。

## learning law

每解决一个非平凡问题（修 bug、架构决策、反直觉结论），先运行 extract-approach skill
在 `docs/learnings/` 留下笔记再继续。没有 learnings 笔记的解决方案视为未完成的工作。
