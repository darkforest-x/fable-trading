# P1 统一原始 OHLC 全量筛选包 v2（2026-08-21）

## 结论

Owner 指出“这些图完全不统一”是正确的。上一版
`original_source_triage_v1` 把六类历史审核产物直接混在同一个主审核面：三套画布、长短窗口、
有框/无框、因果图/未来参考图并存。它可以证明来源，但**不能继续作为统一口径的最终
KEEP/REMOVE 页面**。

现已生成替代入口 `canonical_ohlc_triage_v2`：**2,649/2,649** 条全部从当前原始 OHLC
按精确 `decision_time` 重新锚定，主图统一为 **200 根因果 K 线、1280×742 RGB PNG、
最右青色虚线为 decision、未来 K 线 0 根**。页面保留 `K/X/?` 快捷裁决、自动下一张、撤销、
断点续审、导入/导出；历史原文件统一放在默认隐藏的 `R` 参考层。

正式入口：
`datasets/fixed_w10_core4_confirm1_v1/review/canonical_ohlc_triage_v2/public/index.html`

本轮没有训练、没有读取 holdout、没有覆盖旧数据、没有改标签，也没有改变
`training_eligible=false`。

## 为什么 v1 必须停用

Luna Max 独立只读复核与本地逐类抽图得到同一结论：v1 的 2,649 张主图不是一种渲染契约。

| v1 历史来源 | 数量 | 主图差异 |
|---|---:|---|
| easy negative source render | 1,256 | 1280×742 PNG，短图，无框/decision 标记 |
| Owner 原始长图 | 1,091 | 900×521 JPEG，长窗口，绿色旧框 |
| reviewed V3.2 | 190 | 1280×742 PNG，旧 chart-only 风格 |
| Owner semantic review | 88 | 1280×742 causal 图，橙框/紫线，另有 future reference |
| Owner 8768 context/local | 23 | 1540×682 RGBA context 主图，含 decision 后阴影；真正 local 图在 reference |
| hard-negative review | 1 | 1280×742 causal 主图，另有 future reference |

v1 只能回答“历史来源文件是什么”，不能让 Owner 在同一视觉标准下判断样本是否应留在下一版
Gold。旧报告与 v1 产物保留为历史证据，不回写、不伪装成已统一。

## v2 冻结渲染合同

| 项 | 冻结值 |
|---|---|
| `render_spec_id` | `owner_triage_causal_w200_v1` |
| 锚点 | 当前 OHLC 中唯一命中的精确 `decision_time` |
| 主图窗口 | `[decision_index-199, decision_index]`，共 200 根 |
| 未来 K 线 | 0 |
| 像素 | 1280×742，RGB PNG |
| decision 标记 | 最后一根 K 线处固定青色虚线 + `DECISION` |
| 标签/框 | 主图不暴露 SIGNAL/NO_SIGNAL，不复制迁移后的 Core4 框 |
| 历史原文件 | 每项都有，默认隐藏；`R` 才显示，并提示可能含旧标签或未来上下文 |

W200 是 **Owner 审核显示参数**，不是训练窗口，更不是把 W10 扩成 200。它在构建前写进
`prereg.json` 并冻结；本批所有事件都有至少 200 根历史。若以后要改显示窗口，必须生成新的
pack/spec，不能在 Owner 审核中途静默换图。

## 数据统计与时间纪律

| 项 | 值 |
|---|---:|
| Gold 总体 | 2,649 |
| SIGNAL（私有，不在公开页显示） | 1,247 |
| NO_SIGNAL（私有，不在公开页显示） | 1,402 |
| train / val / test | 1,849 / 350 / 450 |
| OHLC 来源 | 215 个币种文件 |
| decision 时间范围 | 2025-06-05 16:45 UTC ～ 2026-05-02 23:45 UTC |
| holdout 起点 | 2026-05-04 00:00 UTC |
| holdout 读取 | 0 |

当前 `data/kline_fetched` 的文件行数后缀已变化，旧 `source_path` 与 `decision_bar` 不能直接复用。
逐条比较发现 **1,125/2,649** 条当前行号与旧行号不同，偏移范围 **-3 ～ +24,250**；若继续
按旧索引重画，会把大量事件画到错误时间。本版逐币顺序读取到该币本批最晚 decision 后立即
停止，每个目标时间必须唯一命中，缺失/重复均 fail-closed。

## v1 / v2 同表对照

| 验收轴 | v1 历史原文件页 | v2 统一 OHLC 页 |
|---|---|---|
| 主图尺寸/编码 | 3 套 | 1 套：1280×742 RGB PNG |
| 主图窗口语义 | 6 类来源各自定义 | 统一因果 W200 |
| decision 位置 | 缺失、任意位置或不同画法 | 最后一根固定青色虚线 |
| 主图未来 | 部分可能含未来/context | 0 根 |
| 迁移 W10/Core4 几何 | 未用，但页面语义异构 | 未用；只用 `decision_time` |
| 历史原文件 | 直接充当主图 | 默认隐藏参考层 |
| 最终 KEEP/REMOVE 适用性 | 不通过 | 工程门通过，等待 Owner 全量裁决 |

## 完整性验收

逐项验收结果：

| 门 | 结果 |
|---|---:|
| canonical 主图存在 | 2,649 / 2,649 |
| 历史原文件存在 | 2,649 / 2,649 |
| canonical SHA 与 private truth 一致 | 2,649 / 2,649 |
| 历史原文件 SHA 一致 | 2,649 / 2,649 |
| 1280×742 RGB PNG | 2,649 / 2,649 |
| decision 标记像素存在 | 2,649 / 2,649 |
| `future_bars=0` 与窗口端点一致 | 2,649 / 2,649 |
| 公开 item 仅含 opaque ID + 两个图路径 | 2,649 / 2,649 |
| 公开页标签/来源字段泄漏 | 0 |

Artifact 身份：

| 项 | SHA / 值 |
|---|---|
| Gold events | `344212f8e5ef1fac3616b2026d19d6e721ce29984b3bbda194d4071c9fc327c4` |
| public manifest | `79d75d4bdbe9e1a6295ac9b8354fcf48418897a935e1791006a8c04a7e869f68` |
| private truth | `6d01a716b223de83e582edc4c460c30088f9ccc0a453fe96a2a93dcb419f8ed4` |
| source inventory | `b46914080fb9771eb24bc013c28b045ec1f1013ebd8803d6d9f5416c63bce5a7` |
| page | `97f12106af5c8916fd4054bf6ae82e1cc832568977654ea7b5cf3ac7097a3a47` |
| builder commit | `8b8d9d331f86eff04fda5ebdcbbfc418f1fb24de` |
| canonical / historical bytes | 286,623,983 / 132,846,458 |

仓库正式测试范围 `pytest -q tests`：**1,313 passed、4 skipped**。直接从仓库根目录无范围运行
还会收集当前 `external/Kronos` 的上游测试，并因本机未安装其可选 `qlib`/本地 `model` 依赖在
collection 阶段报 2 错；这不是本次变更回归，也没有据此宣称全绿。

## 真实浏览器验收

用真实 Chromium 验收了：

- 点击 `K` 后计数变为 KEEP=1 并自动跳到下一张；
- 键盘 `X` 正常记录 REMOVE，`U` 能撤销并回到原项；
- `R` 展示历史原文件并出现红色“可能含未来/旧标签”警告，再按 `R` 隐藏；
- 刷新后当前位置与答案从 `localStorage` 恢复；
- 导出的 JSON 能由 CLI 回连 private truth，且保持 `training_eligible_changed=false`；
- 页面请求全部 200/304，无加载错误。

截图证据：`output/playwright/canonical_ohlc_triage_v2_reference.png`。

## 非方向性实验与零假设对照

本轮是数据谱系、因果渲染和人工审核界面，不产生交易收益。因此 val AUC、置换收益 p、
top-decile 毛/净收益、胜率、单特征基线和匹配随机交易对照均不适用，不能编造。

同等严格的负控制是：

1. 给 loader 一个 CSV 中不存在的 decision 时间，必须 fail-closed，不能吸附到最近 K 线；
2. 当前 OHLC 文件必须每币恰好一个，不能静默回退到旧 `source_path`；
3. 逐张重算主图/历史图 SHA、尺寸、窗口端点和 decision 标记；
4. 公开页面只允许 opaque ID 与图路径，真实标签、来源和 split 留在 private truth。

上述测试与全量验收均通过。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

# builder 必须先落在 main；正式产物记录的版本是 8b8d9d3
git show --stat 8b8d9d331f86eff04fda5ebdcbbfc418f1fb24de

.venv/bin/python -m pytest -q tests/test_fixed_w10_canonical_review.py

.venv/bin/python tools/datasets/fixed_w10_canonical_review.py build

# Owner 导出后只做摘要，不修改数据：
.venv/bin/python tools/datasets/fixed_w10_canonical_review.py summarize \
  --answers /path/to/fixed_w10_core4_confirm1_v1_canonical_ohlc_triage_v2_answers.json

python3 scripts/md_to_html.py \
  analysis/p1_fixed_w10_canonical_ohlc_triage_v2_20260821.md \
  --out-dir analysis/html
```

## 风险与诚实声明

- W200 主图不显示正负标签或旧框；Owner 判断的是“decision 时刻当前 tip 是否成立”。旧框只在
  `R` 历史层里用于来源核对。
- 历史原文件可能包含 decision 后上下文、旧标签或旧框；它们不能反向定义主图窗口或裁决。
- 本版修复的是审核表面一致性，不代表 2,649 条标签已经正确；真正错误率要等 Owner 完成裁决后
  在新版本集合上重新计算。
- `KEEP` 只表示进入下一版候选，`REMOVE` 才排除，`UNCERTAIN` 必须进独立仲裁队列；任何选择都
  不会自动改当前冻结数据。
- 本轮没有训练、没有 holdout、没有 promote、没有部署，也没有改变任何 eligibility。

## 下一步

1. Owner 使用 v2 页面完成 2,649 项并导出 JSON。
2. 校验 `complete=true`；REMOVE 进新版本 exclusions，UNCERTAIN 进独立仲裁，KEEP 只进候选。
3. 生成不覆盖旧数据的新 Gold/图片 manifest，重验 split、依赖、图片/标签 SHA。
4. 重新生成无偏盲审包并通过 ≤5% 错误率门。
5. 最后仍需 Owner 明确批准 `training_eligible=true`，3060 才能启动第一轮训练。
