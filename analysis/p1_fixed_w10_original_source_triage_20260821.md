# P1 fixed-W10 原始来源图全量筛选包（2026-08-21）

## 结论

Owner 指出得对：此前 448 项盲审包展示的是统一迁移后的 W10 图，不是原始视觉证据。
`W10/Core4/Confirm1` 的含义是把模型输入锁成 **10 根 K 线 = 5 根前文 + 4 根固定核心 +
1 根确认/decision bar**；其中把旧 4–7 根 Owner 核心压成固定 4 根属于迁移假设，不能靠审核
派生 W10 图自证正确。

现已沿最终 Gold 的 `source_dataset + source_record_id` 把 **2,649/2,649** 条全部回连到
迁移前的原始审核图，缺失 **0**，并生成只做 `KEEP / REMOVE / UNCERTAIN` 的全量快捷页面。
页面主图不使用 W10 分类图；当前冻结数据不覆盖、不改标签，`training_eligible=false`。

审核入口：
`datasets/fixed_w10_core4_confirm1_v1/review/original_source_triage_v1/public/index.html`

## W10 / Core4 / Confirm1 到底是什么

| 名称 | 锁定含义 | 风险 |
|---|---|---|
| W10 | 每张模型图仅显示连续 10 根 15m K 线 | 原始长图的走势上下文被删掉 |
| Core4 | 第 6–9 根固定当作 4 根核心 | 旧 Owner 框原为 4–7 根，中心压成 4 根不等于重新标注 |
| Confirm1 | 第 10 根为确认根，同时是 decision bar | 模型只允许看到该根及以前；但原图人工审核可以有单独未来参考 |

因此这套规格只能定义“未来若训练，模型看什么”，不能定义“Owner 原图中哪些框应该保留”。
本轮先回到原图做集合清理；清理完成后必须生成新版本 manifest，不能直接在旧 2,649 快照上改。

## 原图来源与覆盖

| 原始视觉证据 | 数量 | 页面展示内容 |
|---|---:|---|
| Owner 原始长图 | 1,091 | `owner_side_review/previews` 中带原框的 180 根左右长图 |
| easy-negative source render | 1,256 | 当初进入负例 manifest 的 W12–19 源图；这些没有 Owner 长图 |
| reviewed V3.2 source render | 190 | 旧 `dataset_v3_2_reviewed_core_v1` 的原始图片 |
| Owner 8768 context/local | 23 | 当时审核的 context 主图，另有 local 参考图 |
| Owner semantic review pair | 88 | 当时的 causal-review 主图，另有 future-review 参考图 |
| Owner hard-negative review pair | 1 | 当时的 causal-review 主图，另有 future-review 参考图 |
| **合计** | **2,649** | **主图缺失 0；附加参考图 112** |

必须诚实区分：1,256 个 easy negative 本来就是程序抽取的干净背景，不存在一张更早的 Owner
人工框图；这里展示的是它们进入源 negative manifest 时的原始图片，而不是伪造 Owner 原图。

私有逐条映射在
`datasets/fixed_w10_core4_confirm1_v1/review/original_source_triage_v1/admin/truth.jsonl`。
公开页面只含 opaque `review_id`、主图和可选参考图，不暴露旧标签、来源、split 或迁移状态。

## 页面操作

| 动作 | 快捷键 |
|---|---|
| 保留 | `K` 或 `1` |
| 去掉 | `X` 或 `2` |
| 待定（不得进下一版训练集） | `?` 或 `3` |
| 上一张 / 下一张 | `J` / `L`，或左右方向键；空格也可下一张 |
| 撤销上一次判断 | `U` |
| 显示/隐藏附加参考图 | `R`（仅 112 个有参考图的项目出现） |
| 原尺寸缩放 | `Z` |

默认“选择后自动下一张”，并用 `localStorage` 自动保存当前位置和答案；支持按状态过滤、编号
跳转、备注、导入旧进度和随时导出 JSON。导出文件包含完整度与已审数量；只有 2,649 条全部
完成后才能重建数据版本。

## Artifact 与完整性

| 项 | 值 |
|---|---|
| Gold 快照 SHA | `344212f8e5ef1fac3616b2026d19d6e721ce29984b3bbda194d4071c9fc327c4` |
| public manifest SHA | `bd42b8509592f5e2c8fba4f6e0dc039923cc9c3783ebe15fd14ec4005a42249c` |
| private truth SHA | `c5f576daaa1f1fb39410e753fabfdc3c82580b7f2ba80b9d8b67b250dda347fe` |
| 页面 SHA | `78ce524c80e1c85fd58ee08ae68654e460f0301492bc79a70c59f607ade2b2ac` |
| 复制图片 | 2,649 主图 + 112 参考图；146,569,810 bytes |
| builder commit | `b6a48a02986a75b6a7dfa6c7ec12856794151bec` |
| holdout 读取 / 训练 | 0 / 0 |

构建器对有历史 SHA 的来源逐张重算并严格比较；Owner 长图 preview 没有旧 SHA 时记录本次实际
SHA，复制后再次核对。零假设/负控制不是收益随机对照，而是给真实图片故意传入错误预期 SHA：
`test_wrong_original_sha_is_rejected_as_a_negative_control` 必须 fail-closed。真实构建 2,649 条全部
通过，说明页面不是靠路径猜测或 W10 fallback 拼出来的。旧 V3.2 与 8768 的原始像素从本仓
`archive/consolidated` 按数据集名定位；构建器不读取任何外部兄弟仓。

## 浏览器验收

使用真实 Chromium 页面完成：`K/1` 保留并自动跳转、`X/2` 去掉、`U` 撤销、刷新后进度持久、
跳到第一个双图项目、`R` 展示参考图、JSON 下载。页面控制台 **0 error / 0 warning**。

## 非方向性实验说明

本轮是数据谱系与人工审核界面，不产生交易收益，因此 val AUC、置换收益 p、top-decile
毛/净收益、胜率、单特征基线和匹配随机交易对照均不适用，也不得编造。对应的严格对照是：
原图路径逐条联结、历史 SHA/复制 SHA 双门、错误 SHA 负控制、公开/私有字段隔离和真实浏览器验收。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

.venv/bin/python -m pytest -q tests/test_fixed_w10_original_review.py

.venv/bin/python tools/datasets/fixed_w10_original_review.py build

# Owner 导出答案后只做摘要，不修改数据：
.venv/bin/python tools/datasets/fixed_w10_original_review.py summarize \
  --answers /path/to/fixed_w10_core4_confirm1_v1_original_source_triage_v1_answers.json

python3 scripts/md_to_html.py \
  analysis/p1_fixed_w10_original_source_triage_20260821.md \
  --out-dir analysis/html
```

## 风险与诚实声明

- 原图来源异构：Owner 长图、自动 easy negative、旧 V3.2 和后续语义审核图不能冒充同一种标注质量。
- 112 个项目的附加图可能包含 decision 后的人类审核上下文；它们只用于人工保留裁决，严禁进入训练输入。
- KEEP 只表示保留为下一版候选，不自动确认旧 `SIGNAL/NO_SIGNAL` 标签正确；标签语义和边界仍需在新集合上另验。
- 本轮没有覆盖旧数据、没有更改 registry eligibility、没有训练、没有读 holdout、没有 promote 或部署。
- 此全量原图筛选取代“现在先做 448 张 W10 盲审”的执行顺序；旧包保留为历史，但不得先继续使用。

## 下一步

1. Owner 完成 2,649 张原图筛选并导出 JSON。
2. 先校验导出完整度，`REMOVE` 永久进入新版本 exclusions，`UNCERTAIN` 进入独立仲裁队列。
3. 只从 `KEEP` 构建新的、不覆盖旧数据的 Gold/图片 manifest，并重新做 split、依赖、图片/标签 SHA 门。
4. 在新集合上重新生成随机重复盲审包；数字通过后仍需 Owner 明确批准
   `training_eligible=true`，才允许 3060 启动第一轮。
