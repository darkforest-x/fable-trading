# w20 / lsv2 数据集可追溯性与可复现性审计 — 2026-08-10

> 起因：提交 `4b5f48b`（2026-08-10 19:16，已推 origin/main）把 441MB 产物入库，
> 但把约 800MB 的 `datasets/*/images` 排除在外，理由是
> "regenerable from manifests + builders"。本轮把这句声明**验证或证伪**，
> 并补齐规范 §12 / §16.1 要求的 manifest 字段。
>
> 本轮**未动用 holdout**。全局 holdout 消耗次数不变（w20_midbox tip-replay 配置仍为 1 次，
> 2026-08-07 owner 批准那次）。

## 一句话

**像素可复现，切分不可复现。** 2635 张正样本图与标签重建后**逐字节一致**（2635/2635），
但 train/val 切分**无法用 git 里任何代码重现**——重建把 405 个 val 样本全划进了 train。
manifest 现已把切分逐样本钉死，缺口从"不可知"变成"已记录"。

## 复现命令

```bash
# 1. 回填 manifest（生成 manifest.jsonl + manifest_audit.json，不改 builder 原输出）
python3 scripts/backfill_dataset_manifests.py

# 2. 防漂移测试（9 项）
.venv/bin/python -m pytest tests/test_manifest_backfill.py -q

# 3. 同 seed 全量重建到临时目录
PYTHONPATH=.:$HOME/yoyo-trading .venv/bin/python scripts/build_w20_midbox_dataset.py \
    --out /tmp/w20_repro_full --seed 20260807 --limit 0 --augs 1

# 4. 逐文件比对 sha256（脚本见本报告"产物"节）
```

## 数据统计

| | dense_owner_w20_midbox | local_signal_v2_stageb |
|---|---:|---:|
| 磁盘图片 / 标签 | 7570 / 7570 | 4776 / 4776 |
| 正样本 | 2635 | 2388 |
| 易负样本 | 2635 | 2388 |
| 硬负样本 | 2300 | 0 |
| 样本时间范围 | 2025-06-05 .. **2026-07-10** | 2025-06-05 .. **2026-05-03** |
| 窗口右端落在 holdout 期（≥2026-05-04） | **246（9.3%）** | **0** |
| 其中在 train / val | 209 / 37 | — / — |
| 符号数（正样本） | 179（val 32，重叠 0） | — |

## 结果表：审计前 → 审计后

| §16.1 硬门槛 | 审计前 | 审计后 |
|---|---|---|
| manifest 与图片数量守恒 | ❌ 5270 / 7570（69.6%） | ✅ 7570 / 7570 |
| 100% 样本可追溯到原始 market bar | ❌ 2300 张硬负无任何 manifest | ✅ 全部 7570 行 |
| 图片-标签配对守恒 | 未检查 | ✅ 两数据集均 PASS |
| 无重复 sample_id | 未检查 | ✅ PASS |
| 同 event 不跨 split | 未检查 | ✅ PASS |
| 全样本有 image_sha256 | ❌ w20_midbox 一个都没有 | ✅ 全有 |
| 固定 seed 重跑可复现 | **声明，未验证** | **⚠️ 像素 PASS，切分 FAIL** |

## 复现测试结果（本轮核心）

同 seed（20260807）、同源数据集、`--limit 0` 全量重建 2635 个正样本，0 skip：

| 比对项 | 结果 |
|---|---|
| 图片 sha256 一致 | **2635 / 2635** |
| 标签 sha256 一致 | **2635 / 2635** |
| 缺失或多余样本 | 0 |
| **split 落点与原版不同** | **405** |

重建产出 `train 2635 / val 0`，原数据集是 `train 2230 / val 405`。

作为对照，`local_signal_v2_stageb` 构建时就记录了 `image_sha256`；
把 4776 张图现在的哈希与当初记录的比对：**4776 / 4776 一致**，
即盘上的图确证就是当初训练用的那批。

## 解读

**1. "可从 manifest + builder 重建"这句话，一半为真。**
渲染路径（`yoyo.l1_detection.render.render_chart`）+ 每样本 RNG
（`sha1(seed|stem|aug_i)`）是确定性的，2635/2635 逐字节一致证实了这点。
所以 800MB 图像不入库，在**像素层面**是安全的。

**2. 切分不是。** `split_of()` = `sha1(symbol) % VAL_MOD == 0`，当前 VAL_MOD=5，
对全部 32 个 val 符号都判 train。我穷举验证过：

- VAL_MOD ∈ {3,4,5,6,7,8,9,10}：无一吻合（mod=5 时 val 符号命中 0/32）
- 哈希输入换成 base_stem / 整 stem / `okx_`+symbol / 去 `_SWAP` / md5：均不吻合
- 种子化随机符号划分（seed × frac 共 8 组）：不吻合

**根因**：数据集建于 **2026-08-06 16:57**，而 `scripts/build_w20_midbox_dataset.py`
**直到 2026-08-07 13:48（`bed5e64`）才首次入库**。跑出这批数据的脚本不是 git 里这一版。
这正是本项目 `docs/learnings/purge-records-are-claims-not-facts.md` 那条教训的同构情形：
**关于产物来历的陈述，不等于产物的来历。**

**3. 切分本身是合法的，只是没被记录。** 32 个 val 符号与 147 个 train 符号**重叠为 0**，
没有符号跨界，不构成泄漏。它是一次**未被追踪的决策**，不是 bug。

**4. 现在它被钉住了。** `manifest.jsonl` 逐样本记录了真实 split + image_sha256，
且 `all_rows_have_image` 通过（说明记录的 split 与磁盘位置一致）。
重建后按 manifest 重新分目录即可还原原始切分——规则找不回来，但**数据找得回来**。

**5. 与 holdout 污染叠加。** w20_midbox 的 val 里有 37 个样本窗口右端落在 holdout 期。
所以该数据集的 val 既不可复现、又含 holdout 期数据。它训出的权重
（`cycle_0_owner_w20_midbox_cold` / `cycle_hardneg_c1`）的 val 指标不能作任何裁决依据——
这与铁律 12 的既有规定一致，本轮只是给出了量化理由。

## 必报指标说明

本轮是**数据审计**，不是模型实验，因此以下项目标准指标不适用，如实声明而非留空：

- **val AUC / 置换检验 p / top-decile 净收益 / 胜率 / 单特征基线**：本轮未训练、未评估任何模型，
  不产生这些量。三个既有权重的对应数字见各自新增的 `VERDICT.md`。
- **匹配随机对照组**：本轮无方向性策略结果表，无从对照。
  （既有 tip 回测的 matched control 结果已在 `analysis/p_w20_midbox_tip_backtest_20260807.md`。）

本轮的可裁决指标是上面两张表里的**通过/未通过**，全部可由复现命令重跑验证。

## 风险与诚实声明

1. **重建只覆盖正样本。** 2635 个正样本逐字节验证；2635 个易负样本和 2300 个硬负样本
   **未做重建比对**——它们由 `add_w20_midbox_negatives.py` / `add_w20_hardneg_pack.py` 生成，
   各自的 RNG 路径未验证。硬负的 manifest 是**从磁盘文件名反解重建的**
   （`manifest_source: reconstructed_from_disk_20260810`），不是构建时记录的，
   因此它证明"这些文件存在且哈希是这些"，**不证明**"重跑能得到同样的文件"。
2. **切分规则未找回。** 我只能证明 git 里的代码复现不出它，不能证明当时用的是什么。
   若原始 builder 版本还在某处（3060 上？），比对它才能定案。
3. `data/kline_cache` 当前不存在（CLAUDE.md 记它是旧项目缓存的只读软链接）。
   本次重建只用了 `data/kline_fetched`（602 文件）却得到逐字节一致的结果，
   说明这批样本没有用到 cache 里的币种——但**不能推广**到其他数据集。
4. 本报告新增的三份 `VERDICT.md` 里，w20 两份的 holdout 污染数字（246 / 209 / 37）
   是本轮实测；tip 回测数字引自 08-07 报告，未重跑。
5. `manifest.jsonl` 里 `decision_bar_index` / `confirm_delay` / `visible_end_timestamp`
   对 w20_midbox **全部为 null**，并列在每行的 `missing_fields` 里。这是刻意的：
   该数据集是 Stage-A midbox 协议，没有因果语义，**编一个 decision bar 出来
   正是 `window-length-does-not-control-future-visibility.md` 警告的那种伪因果**。

## 下一步选项（标注需项目所有者决策的）

1. **让 builder 从 manifest 读 split**，而不是重算——把"规则可复现"降级为"数据可复现"，
   代价小、立刻生效。（不需决策，建议直接做）
2. **补跑负样本 / 硬负样本的重建比对**，把剩余 4935 个样本也验一遍。（不需决策）
3. **给 `add_w20_hardneg_pack.py` 补写 manifest**，避免下次再靠反解。（不需决策）
4. **w20_midbox 是否重建为带 holdout 过滤的版本** —— 现版本 246 个样本污染，
   任何基于它的 val 数字都不可用。**需要 owner 点头**（涉及是否放弃现有两个权重）。
5. **规范 §15 的 P1 六臂对照矩阵尚未开跑**，且其中 Stage A 臂与铁律 12 字面冲突。
   **需要 owner 决策**（此问题自 08-08 起挂着，未答）。

## 产物

- `scripts/backfill_dataset_manifests.py` — 回填 + 审计，不改 builder 原输出
- `tests/test_manifest_backfill.py` — 9 项防漂移测试（AST 提取，无重依赖）
- `datasets/dense_owner_w20_midbox/manifest.jsonl` + `manifest_audit.json`
- `datasets/local_signal_v2_stageb/manifest.jsonl` + `manifest_audit.json`
- `analysis/output/w20_overnight/cycle_0_owner_w20_midbox_cold/VERDICT.md` — 判死
- `analysis/output/w20_overnight/cycle_hardneg_c1/VERDICT.md` — 未裁决
- `analysis/output/lsv2_stageb/owner_lsv2_stageb_cold/VERDICT.md` — 未通过，holdout 未动
- `analysis/output/forward_log_w20_midbox_shadow.csv` — 自 `data/` 移出（铁律 6）
