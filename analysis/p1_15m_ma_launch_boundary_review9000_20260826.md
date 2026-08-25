# 15m 六均线启动 9000 候选逐样本类别与边界审核入口

## 结论先行

9,000 个新增候选的逐样本审核入口已经完成并通过机械与真实浏览器验收。页面一次只显示一张，
每张必须先裁决 `KEEP / DROP / UNCERTAIN`；`KEEP` 还必须明确选择完整输入 **W14–22**、
核心 **4–7 根**、确认 **3–5 根**。页面不预填几何、没有批量认可，也不会把蓝色 `t-3`
审核线当作框边界。

本轮只完成了**审核工具与回执合同**，没有完成 9,000 张人工审核：正式 Owner 答案仍为 0，
Owner 金框仍为 0，训练图、YOLO label、负例和模型仍全部为 0。因此 Gold Dataset 门和 3060
训练门继续关闭；这不是训练失败，而是没有拿候选冒充标签。

审核页：
[`experiments/active/exp-15m-ma-launch-boundary-review9000-v1/results/public/index.html`](http://127.0.0.1:8769/exp-15m-ma-launch-boundary-review9000-v1/results/public/index.html)

![新增 9000 候选 Top-40 总览](../experiments/active/exp-15m-ma-launch-candidate9000-v1/results/overview_top40.png)

## 本轮恢复的训练几何合同

| 项目 | 冻结值 | 审核页如何保证 |
|---|---:|---|
| 审核图 | 48 根，候选 `t` 在本地 index 30 | 复用原候选 PNG 和 SHA，不重新画行情 |
| 用户要求的竖线 | `t-3`，index 27 | 只作视觉参考，不提供默认答案 |
| 训练输入 | W14–22，右端为 `t` | Owner 每张单独选择；起点由闭区间算术导出 |
| 核心 | 4–7 根 | Owner 每张单独选择；结束点由确认根数导出 |
| 确认 | 3 / 4 / 5 根 | 3 优先、5 硬上限；6–10 不可选 |
| 框纵向 | 核心 full wicks + 六均线 | 下游仅在 Owner 释放后机械派生，本轮不画 YOLO 标签 |
| 框位置 | 随 W / core / confirmation 自然变化 | 不允许统一 delta、固定中心、固定右缘或机械 jitter |
| SHORT | 已冻结类别方向，仍需逐样本确认 | SHORT KEEP 只进入“待 Owner 释放预览” |
| LONG | `mirror_unconfirmed` | 即使逐样本 KEEP，也不进正例或负例 |

例如审核页真实浏览器 QA 选择 `W18 / core5 / confirmation3` 后，闭区间几何是：训练输入
`i13–30`、核心 `i23–27`、框中心比例 `70.5882%`。这是一次 QA 场景，不是任何正式样本答案；
QA 结束已撤销到 `0/9000`。

## 数据与血缘

| 项 | LONG | SHORT | 合计 |
|---|---:|---:|---:|
| 源候选 | 4,500 | 4,500 | 9,000 |
| 唯一 event_id | 4,500 | 4,500 | 9,000 |
| 源 `owner_verdict=PENDING` | 4,500 | 4,500 | 9,000 |
| 正式审核答案 | 0 | 0 | 0 |
| `training_eligible=true` | 0 | 0 | 0 |
| `production_eligible=true` | 0 | 0 | 0 |

源 manifest SHA-256 为
`3b4faa197ebd08bfd24ea662c30b8360200f29e172dbc5d68639eb880a71d731`，仍是上一轮冻结的
9,000 候选；本轮没有改候选成员、排序、图片或指标。源候选实际时间覆盖
`2022-01-05 17:30 UTC` 至 `2026-05-03 10:30 UTC`，全部早于 holdout 起点
`2026-05-04 00:00 UTC`。

审核 builder 重新读取并核对 9,000 个图片 SHA，共 `1,393,089,371` bytes，错配 0。
审核 HTML 内嵌 9,000 个最小身份记录，文件约 3.23 MB；图片仍只读复用原来的
`review_charts/`，没有复制 1.4 GB sidecar。

## 与上一状态同表对照

| 闸门 | 候选扩容完成时 | 本轮完成后 | 解释 |
|---|---:|---:|---|
| 新候选 | 9,000 | 9,000 | 成员与源 hash 不变 |
| 逐样本类别答案 | 0 | 0 | 审核入口不是 Owner 答案 |
| 逐样本 W/core/confirm | 0 | 0 | 必须由 Owner 在页面逐张选择 |
| 可恢复进度的审核页 | 无 | 1 | localStorage + JSON 导入/导出 |
| 预选答案 / 批量认可 | 0 / 无 | 0 / 无 | 防止整批误标 |
| 训练图片 / labels | 0 / 0 | 0 / 0 | 仍未物化 Gold Dataset |
| easy / hard negative | 0 / 0 | 0 / 0 | 尚无 Owner 正框禁入区 |
| 3060 epoch / weights | 0 / 0 | 0 / 0 | 没有远端写入或训练 |

## 回执校验与零假设对照

本轮是非方向性的审核基础设施，没有入场、退出、成本、收益或模型分数，所以 val AUC、置换
`p`、top-decile 毛/净收益、胜率、单特征基线、匹配随机交易对照和 YOLO mAP 均不适用。
对应的严格零假设是：错误、缺失或批量退化的审核回执必须 fail closed。

| 对照 / 故障注入 | 期望 | 结果 |
|---|---|---|
| 未知 event_id | 拒绝 | 通过 |
| 重复 event_id | 拒绝 | 通过 |
| symbol / direction / time / PNG SHA 身份漂移 | 拒绝 | 通过 |
| W 不在 14–22、core 不在 4–7、确认不在 3–5 | 拒绝 | 通过 |
| KEEP 的起止算术与三项选择不一致 | 拒绝 | 通过 |
| DROP / UNCERTAIN 携带几何 | 拒绝 | 通过 |
| 5 / 9000 的不完整导出走默认严格模式 | 拒绝 | exit 1，`review is incomplete: 5/9000` |
| 同一不完整导出显式 `--allow-incomplete` | 只生成进度回执 | `incomplete_validated` |
| 合成 5 KEEP 的 W/core/confirm 全部有变化 | 位置反退化检查通过 | 5 triples、5 W、4 core widths、3 delays |
| 合成回执的训练图 / label / 负例 / 模型 | 全为 0 | 0 / 0 / 0 / 0 |

14 个定向测试全部通过；全仓为 **1,588 passed、4 skipped**。完整测试只有既有 matplotlib
依赖的 14 条弃用 warning，没有新增失败。

最终报告 HTML 另在 headed Chromium 的 1440×1000 与 390×844 两种视口验收：标题、四张表、
内嵌总览图和审核入口链接均可读，HTTP 200，console error 0。

## 真实浏览器验收

用 headed Chromium 从 README 的真实 localhost 命令打开页面，结果如下：

- 页面和候选图片请求均为 HTTP 200，浏览器 console error 为 0；
- 初始进度 `0/9000`，没有预选几何；
- 选择 `confirmation3 + core5 + W18` 后，蓝色训练窗与橙色核心时间带正常显示；
- 默认“只看未审”下，第4501张 KEEP 后准确进入第4502张，没有跳到4503；
- Undo 后回到第4501张和 `0/9000`；
- QA 没有在仓库生成答案文件。

第一次真实浏览器检查曾抓到两个只靠静态检查发现不了的问题并已修复：HTTP 服务根目录太深会
阻止访问兄弟实验图片；当前项裁决后离开 PENDING 集合时，普通 `+1` 会跳过一张。最终回执只记录
修复后的全绿结果，解决思路分别留在：

- `docs/learnings/review-http-root-must-cover-external-sidecars.md`
- `docs/learnings/filtered-review-auto-next-must-use-pre-mutation-index.md`
- `docs/learnings/python39-long-unicode-template-needs-cli-parse-check.md`

## 使用方法

直接打开审核 HTML 即可。若浏览器限制本地文件，从仓库根目录运行：

```bash
python3 -m http.server 8769 --directory experiments/active
```

再打开：

```text
http://127.0.0.1:8769/exp-15m-ma-launch-boundary-review9000-v1/results/public/index.html
```

页面默认先审 SHORT、只看未审。每张 KEEP 必须点三组几何；进度自动保存在当前浏览器。
可随时“导出审核 JSON”备份，再用“导入进度”恢复。完整答案返回仓库后，先运行：

```bash
PYTHONPATH=. .venv/bin/python scripts/summarize_15m_candidate_boundary_review.py \
  --answers /absolute/path/to/exported_answers.json \
  --out /absolute/path/to/new_validation_output
```

默认模式要求 9,000 / 9,000 完整覆盖；`--allow-incomplete` 只用于进度检查，不能释放数据集。

## 完整复现命令

```bash
cd /Users/zhangzc/fable-trading

# 定向合同与真实 CLI 入口
PYTHONPATH=. .venv/bin/python scripts/build_15m_candidate_boundary_review.py --help
PYTHONPATH=. .venv/bin/pytest -q tests/test_candidate_boundary_review.py

# 从已提交 builder 生成审核包；输出已存在时会拒绝覆盖
PYTHONPATH=. .venv/bin/python scripts/build_15m_candidate_boundary_review.py

# 全仓回归
PYTHONPATH=. .venv/bin/pytest -q tests

# 报告 HTML
python3 scripts/md_to_html.py \
  analysis/p1_15m_ma_launch_boundary_review9000_20260826.md \
  --out-dir analysis/html
```

## 风险与诚实声明

- 9,000 是机器检索候选，不是 9,000 个金标；候选排序使用完成后的 12 根路径，不能冒充实时因果
  信号。人工审核可以利用未来理解语义，但真正训练输入只允许到 `t`。
- 页面约束的是横向 bar 几何；纵向 YOLO 框要在 Owner 释放后按核心 full-wicks + 六均线机械派生，
  本轮没有生成任何标签。
- LONG 仍是 `mirror_unconfirmed`。当前请求允许收集多空候选，不等于已批准多头镜像训练协议。
- “框不能都在一个位置”通过回执后的退化审计守门；若 Owner 的选择退化，系统会停训并返回审核，
  不会人为抖动位置凑分布。
- 旧训练配方的 train 负例目标是 easy 1× + hard 2×，总负正比 3:1，但比例是软目标。没有完整
  Owner 正框禁入区前，不能安全收集负例；之后也不得为凑比例削弱排斥区或跨 split 取样。
- 本轮未读取 holdout、未写 raw kline、未连接 3060 写入、未训练、未 promote、未改 ACTIVE、
  未部署、未改 forward/order 状态。

## 下一步与 Owner 决策门

下一条允许动作是 Owner 在审核页提交逐样本答案。收到完整导出后，Codex 先跑 summarizer 并交付
类别、方向、W/core/confirm 和位置分布审计；Owner 再明确决定是否释放 SHORT KEEP 集合作为
Gold Dataset 输入。只有释放后，才另行预注册时间/依赖 split、安全负样本、字节验收和 3060
训练；训练结束也只停在模型与日志，不自动 promote 或部署。
