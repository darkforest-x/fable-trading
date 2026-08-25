# 15m 六均线 9000 候选 P1 数据集 release 门

## 技术结论

P1 前置 release planner 已完成并通过回归：它把完整 Owner 审核、精确 SHORT KEEP preview 和
Owner 显式 release 固定为三个不可替代的事实源；只有三者哈希一致，才会生成**时间切分与保护区
计划**。即使规划成功，训练图、YOLO label、负样本、epoch 和 weights 仍全部为 0，
`training_eligible=false / production_eligible=false`。

当前真实状态仍是阻断：仓库内没有 9,000 / 9,000 完整审核汇总，也没有绑定精确 preview hash
的 Owner release，因此 planner 没有生成正例计划或保护区，更没有连接 3060。阻断不是工具缺失，
而是 P0/P1 所需的两项 Owner 事实尚未出现。

逐样本审核入口：
[`exp-15m-ma-launch-boundary-review9000-v1/results/public/index.html`](http://127.0.0.1:8769/exp-15m-ma-launch-boundary-review9000-v1/results/public/index.html)

## 当前闸门只缺两项外部事实

以下数字来自提交后的 `--status-only` 正式回执。它重新核对四个上游 SHA，并明确把“请求训练”与
“释放精确数据集成员”分开；前者不能代替后者。

| 闸门 | 当前证据 | 是否满足 | 失败时动作 |
|---|---:|---:|---|
| 完整逐样本审核 | 正式答案 0 / 9,000 | 否 | 不生成 release plan |
| 精确 SHORT release | release receipt 不存在 | 否 | 不推导数据集成员 |
| 上游四项 SHA | 4 / 4 一致 | 是 | 任一漂移立即拒绝 |
| P1 planner 代码 | 已提交 | 是 | 非 main / dirty builder 拒绝运行 |
| 正例 split plan | 0 行 | 未到此门 | 不读取 OHLCV |
| 新 KEEP guard ledger | 0 行 | 未到此门 | 不收集负例 |
| 训练图片 / label / negative | 0 / 0 / 0 | 未授权 | 不连接 3060 写入 |
| epoch / weights | 0 / 0 | 未授权 | 不训练、不 promote |

状态回执固定的两个缺口是
`complete_owner_review_summary` 与 `explicit_hash_bound_owner_short_release`。第二次用同一路径运行
`--status-only` 得到 exit 1 的 `refusing to overwrite`，证明状态回执不会被静默覆盖。

## 小窗口、小框与位置变化现在进入可执行合同

本轮没有改 Owner 参数，只把上一轮恢复的训练方法推进到 fail-closed 规划层。

| 合同 | 冻结值 | planner 如何使用 |
|---|---:|---|
| 时间粒度 | 15 分钟 | 仅用 `anchor_time` 做整数 bar 时间算术 |
| 输入窗口 | W14–22 | 从 Owner 每张选择推导 input start/end |
| 核心框 | 4–7 根 | 从 Owner 每张选择推导 core start/end |
| 确认延迟 | 3 / 4 / 5 根 | 3 优先、5 封顶；不读核心之后收益 |
| 框位置 | 自然变化 | 重算 W/core/delay 与 center ratio；退化审计失败即停 |
| 依赖块 | 同币输入窗重叠或相接 | 整块只能落在一个 split |
| validation | 时间上最新 15% 依赖块 | 禁止随机切分 |
| purge | 150 根 15m bar | train 最晚输入端到 val 最早输入端必须达标 |
| 新正框保护 | 核心两侧各 12 根 | SHORT 与 LONG KEEP 都进入保护 ledger |
| easy negative | train 正例 1× | 仅输出软目标，不选样本 |
| hard negative | train 正例 2× | 仅输出软目标，不用模型分数选样本 |

SHORT KEEP 才能进入正例 split plan；LONG KEEP 仍是 `mirror_unconfirmed`，不进正例也不进负例，
但其 Owner 确认核心会进入**保护区**，防止后续背景采样踩到一张尚未获镜像协议批准的形态。

## release planner 重算事实而不相信派生布尔值

planner 先逐行从原子事实重算：`direction=SHORT AND decision=KEEP` 才是 SHORT release preview
成员；KEEP 必须有 Owner 几何，非 KEEP 必须无几何；SHORT/LONG 的协议状态也重新推导。上游的
`eligible_for_later_owner_release_preview`、`geometry_owner_confirmed` 等字段只作为一致性断言，
不能决定成员。

这样做修复了一个审计中发现的风险：若上游布尔值错误，一条 SHORT KEEP 可能静默漏出，或一条
LONG/非 KEEP 可能混入。现在任一不一致都 fail closed。解决思路记录在
`docs/learnings/release-preview-membership-must-be-recomputed-from-owner-facts.md`。

通过成员门后，planner 才执行以下纯时间步骤：

1. 从 `anchor_time`、W、core、confirmation 推导 input/core/guard 时间；
2. 拒绝任一源时间、input/core 时间或 guard 右端触及 `2026-05-04 00:00 UTC`；
3. 将同币重叠或相接的输入窗合成不可拆依赖块；
4. 按全局时间排序，把最新 15% 依赖块给 val；
5. val 最早输入之前不足 150 根的中间块全部 drop；
6. 输出 planning-only 正例、全 KEEP 保护区与按 W 分层的 1×/2×负样本软目标。

全过程不打开 OHLCV，因此它只能证明 split/guard 计划的逻辑完整，不能证明未来市场数据可物化、
图片可渲染或安全负例一定足额。

## 故障注入证明错误输入不会越过门

本轮是非方向性的数据谱系与切分守门，没有模型、入场、退出、成本或收益，因此 val AUC、置换
`p`、top-decile 毛/净收益、胜率、单特征基线、匹配随机交易对照与 YOLO mAP 均不适用。对应的
严格零假设是：不完整、漂移或越权的 release 输入不得产生任何训练资产。

| 故障 / 对照 | 预期 | 结果 |
|---|---|---|
| review summary 未完成 | 拒绝 | 通过 |
| 位置去捷径审计失败 | 拒绝 | 通过 |
| release 未绑定 exact summary SHA | 拒绝 | 通过 |
| release 把 `training_authorized` 设 true | 拒绝 | 通过 |
| LONG 行混进 SHORT preview | 拒绝 | 通过 |
| 上游 preview eligibility 布尔值与原子事实不符 | 重算后拒绝 | 通过 |
| core + 12 根 guard 触及 holdout | 拒绝 | 通过 |
| 同币相接输入窗 | 合并为同一 dependency | 通过 |
| train/val 依赖块交叉 | 拒绝 | 通过 |
| 第二次写同一路径状态回执 | 拒绝覆盖 | exit 1 |

10 个新定向测试通过；连同层间边界测试为 68 passed。全仓为 **1,598 passed、4 skipped**，仅有
既有 matplotlib/pyparsing 的 14 条弃用 warning。

最终 HTML 在 headed Chromium 的 1440×1000 与 390×844 两种视口均可读；标题、三张表、列表与
审核入口链接存在，HTTP 200，console error 0。

测试里的 20 行合成 review 只用于机械故障注入：16 个 SHORT KEEP、2 个 LONG KEEP 与 2 个 DROP
验证了 split、touch dependency 和 LONG 保护逻辑；它们没有保存在数据集目录，不计入候选、金标
或训练样本。

## 当前没有可诚实绘制的数据集分布图

本报告不新增 W/core/delay、split 或负样本分布图，因为正式 release plan 仍为 0 行。用合成测试
分布代替真实 Owner 审核结果会把 QA fixture 冒充数据证据；因此使用精确表格呈现当前门状态，
等真实完整审核与 release 后再绘制实际位置、split 和负样本短缺分布。

## 完整复现命令

```bash
cd /Users/zhangzc/fable-trading

# 定向合同与边界
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_candidate_dataset_release.py \
  tests/boundaries/test_layer_imports.py

# 当前真实门状态；正式回执已存在时会拒绝覆盖
PYTHONPATH=. .venv/bin/python \
  scripts/plan_15m_candidate_dataset_release.py --status-only

# 完整审核与 Owner release 到位后才允许规划；仍不读 OHLCV、不产图
PYTHONPATH=. .venv/bin/python \
  scripts/plan_15m_candidate_dataset_release.py \
  --review-dir /absolute/path/to/complete_review_summary \
  --release /absolute/path/to/owner_dataset_release.json \
  --out /absolute/path/to/new_release_plan

# 全仓回归
PYTHONPATH=. .venv/bin/pytest -q tests

# 项目规定的 HTML 交付
python3 scripts/md_to_html.py \
  analysis/p1_15m_ma_launch_dataset_release_gate9000_20260826.md \
  --out-dir analysis/html
```

## 限制、风险与诚实声明

- P1 planner 不是 Gold Dataset builder；它不检查 OHLC 连续性、不渲染图、不生成纵向 YOLO 坐标。
- 新 KEEP guard ledger 只是必要集合。正式负样本 builder 必须再 union 全部历史 Owner 原框保护区；
  在该 union 逐字节验收前，`historical_owner_guard_union_complete=false`，负样本选择继续阻断。
- 3:1 是软目标。若同币、同 split、同 W 且避开所有保护区的背景不足，必须诚实缺样，不能缩保护区、
  跨币、跨 split 或复用窗口凑比例。
- latest-15%-blocks 与 150-bar purge 是继承的冻结合同，不代表当前 9,000 样本已经得到可用 train/val；
  实际计数只有完整审核和 release 后才可计算。
- 本轮未读取 holdout OHLCV，未写 raw kline，未生成训练资产，未连接 3060 写入，未训练、promote、
  改 ACTIVE、部署或改变 forward/order 状态。

## 下一步只有审核与精确 release

Owner 先在审核页完成 9,000 张并导出 JSON；summarizer 生成完整 review summary 与精确 SHORT KEEP
preview。Codex 交付真实类别、W/core/delay、位置退化和方向分布后，Owner 再明确释放该 preview
hash 用于 **P1 planning only**。

planner 随后才会计算真实 split 与新保护区。再下一轮必须单独预注册：合并全部历史 Owner guard、
读取 bounded pre-holdout OHLC、物化正例、收集安全 easy/hard negative、逐字节验收训练包。只有该
Gold Dataset 质量门通过，才准备并在 3060 启动训练；训练完成也不自动 promote、部署或交易。

仍需 Owner 回答的唯一当前问题是：完成逐样本审核后，是否释放**精确 hash 绑定的 SHORT KEEP
preview**进入 P1 规划。现有“之后训练”的总体请求不被解释为这项样本级 release。
