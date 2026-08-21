# P1 Owner 旧训练正例原图精筛包（2026-08-21）

## 结论

Owner 对当前页面的质疑完全成立：目标不是核对 fixed-W10 的 2,649 行数据谱系，而是重新筛选
**旧模型实际使用过的 Owner 人工正例**，只留下现在仍认可的最佳形态，再生成新版本训练集。

此前两版页面均已停用：

- `original_source_triage_v1` 混合了六种来源和正负样本，画布、标签语义与未来可见性不一致；
- `canonical_ohlc_triage_v2` 左侧是按当前 OHLC 重新锚定的因果 W200，右侧是历史长图，两者本来
  就不是同一个裁剪窗口，因此 K 线不会一致。它适合查谱系，不适合 Owner 快速精筛形态；
- v2 的 `R` 键在焦点位于任意 `INPUT / SELECT / TEXTAREA` 时会被全局键盘处理器忽略，Owner
  在跳转框、筛选框或自动下一张控件上操作后会感觉“按 R 没反应”。但根本问题不是快捷键，
  而是本任务根本不该存在左右对照和 `R`。

正确母池已经锁定为 `datasets/owner_short_gold_center_v1/positive_manifest.jsonl` 的 **1,345 张**。
它们来自 Owner 亲自判为 `short` 的 1,361 个框；15 个历史重复别名先合并为 1,346 个独立目标，
再有 1 个因时间切分 purge，因此旧训练实际使用 1,345 个正例（train 1,143 / val 202）。新页面
逐条回到 Owner 当时看过的 900×521 原始长图预览，**一次只显示一张，只判断绿色框**。

正式入口：

`datasets/owner_short_gold_center_v1/review/owner_positive_refilter_v1/public/index.html`

## Owner 现在要做什么

- `KEEP / K / 1`：该绿色框形态进入新正例候选；
- `REMOVE / X / 2`：从新版本正例排除；
- `UNCERTAIN / ? / 3`：进入单独二次仲裁；
- `J / ←`、`L / → / 空格`：前后翻页；
- `U`：撤销；`Z`：原尺寸；
- 页面自动保存到浏览器本地，可随时导出或导入 JSON。

旧训练集不会被覆盖。Owner 完成前不训练、不改 `training_eligible`。

## 数据谱系

| 项目 | 数量 / 结果 |
|---|---:|
| `owner_side_review/review_sheet.csv` 全部 Owner 框 | 2,525 |
| Owner 亲自判为 `short` | 1,361 |
| 重复别名合并 | 15 |
| 独立 short 目标 | 1,346 |
| 时间切分 purge | 1 |
| 旧训练实际正例 | **1,345** |
| train / val | 1,143 / 202 |
| Owner 原始预览成功回连 | 1,345 / 1,345 |
| Owner side 错配 | 0 |
| 原图缺失 | 0 |
| 旧训练图片 SHA 错配 | 0 |
| 旧训练标签 SHA 错配 | 0 |
| 审核图画布 / 格式 | 900×521 / JPEG，1,345 / 1,345 |
| 审核图总字节 | 54,002,545 |

关键输入与产物 SHA-256：

| 对象 | SHA-256 |
|---|---|
| 旧训练 positive manifest | `8f4119fbf634ec976077e8eb50b36e57ae3aa0471759cad04f2eaeaeacd6d21b` |
| Owner review sheet | `bb7081e7e1821c5f791486fae0f29caf18307b104bbb07156c35883781071c9a` |
| 新 public manifest | `b079376be80ae04dc9e5eca57e7c33871bc34cdc3ad1d90938943a5417db9865` |
| 私有 truth | `0dc4609c7b627518a30b48d0d1f0f84794b5943bafe517df273642dc5c39c296` |
| 页面 | `2bf8ad252f57a054dc2b4a35aca462c297cf082c898bae329086afcfc960907b` |
| 审核图集合 | `6c671c261eefebc06917f5c1fdbf1d42bbfbce4280fb0fade07663a7eaf04882` |
| prereg | `48fc27a15724825c361b1fcec79d906a88ab3320442655d67fb90fddec0bfa18` |

## 与错误页面的对照

| 项目 | fixed-W10 v2（停用） | Owner positive refilter v1（当前） |
|---|---|---|
| 母池 | 2,649 行混合 Gold | 旧训练实际 1,345 个正例 |
| 正负构成 | 1,247 SIGNAL + 1,402 NO_SIGNAL | 1,345 Owner-confirmed positive |
| 主要问题 | 数据谱系 / 全量 Gold 迁移 | 训练正例语义纯度 |
| 主图 | 当前 OHLC 重绘 W200 | Owner 当时看过的原始长图预览 |
| 第二张图 | 历史来源图 | **没有** |
| 绿色框 | 只在部分历史图出现 | 每张都指向本轮唯一目标 |
| `R` | 显示/隐藏历史图 | **已删除** |
| 结果用途 | mixed-Gold 来源审核 | 新训练正例候选筛选 |

## 审核图和训练图的边界

Owner 原始长图会显示绿色框之后的走势，这是本轮人工复核形态语义所需的历史证据，不能冒充
decision-time 因果输入。新包将 1,345 张审核图放在独立 `public/images/`，没有复制任何 YOLO
标签；旧训练短图和标签继续留在原数据集，逐 SHA 锁定。本轮导出的 KEEP 只代表“新版本正例
候选”，后续训练仍必须从冻结的短窗训练输入重建/筛选，不能把长图或未来走势直接喂给模型。

## 工程验收与零假设对照

这是数据筛选工具，不是方向性收益实验，因此 val AUC、置换收益 p、top-decile 收益、胜率、
单特征基线和匹配随机交易对照均不适用，也没有编造这些数字。

同等严格的工程零假设对照是 fail-closed lineage test：

- 把任一映射目标从 `owner_side=short` 改成 `long`，构建必须失败；
- 修改任一已物化审核图的一个字节，验收必须因 SHA 不一致失败；
- public manifest 每项只允许 `review_id + image`，不得泄漏 train/val、样本 ID 或预选答案；
- 页面必须恰好一个审核 `<img>`，出现 `historical_image`、`toggleReference`、`R ·` 或“左右”
  任一旧双图契约即失败。

已通过：新模块 6 个针对性测试；1,345 张正式图片的尺寸、格式、SHA 与私有 truth 一一联结；
`PYTHONPATH=. .venv/bin/pytest -q tests` 为 **1,402 passed、4 skipped、14 warnings**。

## 复现命令

构建器已先以 commit `b7ad3569527b0a460c4c91dd9fd010860f059ec1` 入库，再生成正式产物：

```bash
cd /Users/zhangzc/fable-trading

PYTHONPATH=. .venv/bin/python tools/datasets/owner_positive_refilter.py build \
  --generator-commit b7ad3569527b0a460c4c91dd9fd010860f059ec1

PYTHONPATH=. .venv/bin/python tools/datasets/owner_positive_refilter.py verify

PYTHONPATH=. .venv/bin/pytest -q tests/test_owner_positive_refilter.py

python3 scripts/md_to_html.py \
  analysis/p1_owner_short_positive_refilter_20260821.md \
  --out-dir analysis/html
```

Owner 导出后汇总：

```bash
PYTHONPATH=. .venv/bin/python tools/datasets/owner_positive_refilter.py summarize \
  /path/to/owner_short_gold_center_v1_positive_refilter_v1_answers.json \
  --joined-out datasets/owner_short_gold_center_v1/review/owner_positive_refilter_v1/review_results/joined.jsonl
```

## 风险与诚实声明

- 这 1,345 张是旧训练实际正例，不是 fixed-W10 的 2,649 行，也不是全部 2,525 个多空框；
- 绿色框来自 Owner 当年的原始裁决，但旧训练框曾按“原框中心一半、限制 4–7 根”机械派生，
  完成 KEEP/REMOVE 仍不等于每个新短窗几何已经重新逐框确认；
- 只保留事后走势最漂亮的样本可能制造“结果已知才成立”的不可学习目标。筛完后必须另测
  decision-time 可辨识性，不能把 Owner 纯度直接当模型能学会的证据；
- 当前页面结果尚为空，不能提前声称数据已经优化成功；
- 未读 holdout、未训练、未 promote、未部署，`training_eligible=false`。

## 下一步

1. Owner 完成 1,345 张精筛并导出 JSON；
2. KEEP / REMOVE / UNCERTAIN 精确回连旧 positive manifest；
3. 生成不覆盖旧数据的新版本，重新做时间 split、依赖隔离、图片/标签 SHA 和匹配负例；
4. 对新版本做盲重复与 decision-time 可学习性门检；
5. 门通过后由 Owner 明确批准 `training_eligible=true`，3060 第一轮只改变“正例集合”这一变量。
