# P0 修复 — Local Signal V2 Stage B strict-negative V2

**日期**：2026-08-10

**Builder HEAD**：`471f854`（先提交代码，再从该提交全量重建）
**范围**：修复负样本时间切分与 P0 审计盲区；重建版本化数据集；未训练、未读 holdout、未改 ACTIVE、未部署、未下单。

## 一句话结论

旧 `datasets/local_signal_v2_stageb` 的正样本按时间切分，但负样本只是继承 split 名称，实际从整个 pre-holdout 历史随机抽取；原审计又只检查正样本，因此产生了错误的 P0 全绿。

修复后新建 `datasets/local_signal_v2_stageb_strictneg_v2`：每个负窗口完整落在所属时间块内，统一 manifest 补齐规范 §12 的时间字段，P0 八道机器门全部通过。旧 P1 权重因绑定旧数据集且训练时 HSV 非零，已退出候选；本轮按规范 §14 停在 P0，不自动训练。

## 根因与修复

| 项 | 旧 V1 | strict-negative V2 |
|---|---|---|
| 负样本 split | 按正样本的 `(symbol, split)` 分组，但候选来自该币全历史 | 窗口 start/end 必须完整落入冻结的 train/val 时间块 |
| train 越界负样本 | **317** 条晚于正样本 train 结束 | **0** |
| val 过早负样本 | **296** 条早于正样本 val 开始 | **0** |
| 负样本 start timestamp | 无，审计时由 15m × window_len 反推 | 100% 显式记录 |
| P0 时间门 | 误报 PASS（只审正样本） | 同时审正/负窗口，V1 FAIL、V2 PASS |
| 失败退出码 | P0 fail 仍返回 0 | P0 fail 返回 1，流水线 fail-closed |
| 固定 seed 原地复跑 | 既有文件可能导致 manifest 被清空 | 两份 native manifest 前后 SHA256 完全一致 |

`scripts/build_local_signal_v2_stageb.py` 的 V1 采样算法与既有数据集不覆盖；修复通过新入口 `scripts/build_local_signal_v2_stageb_strictneg_v2.py` 和新数据集目录交付。

## 数据统计

| split | positive | easy negative | 全样本最早可见 bar | 全样本最晚可见 bar |
|---|---:|---:|---|---|
| train | 2,030 | 2,030 | 2025-06-02 13:30 UTC | 2026-03-18 12:45 UTC |
| val | 358 | 358 | 2026-03-20 00:15 UTC | 2026-05-03 10:45 UTC |
| 合计 | **2,388** | **2,388** | 4,776 images | 4,776 labels |

- 正类率：train/val 均为 50%。
- 正样本 confirm delay：1 bar = 1,185；2 bars = 1,203。
- 正样本 holdout/no-time 跳过 246；purge zone 跳过 1。
- train 最后可见 bar 严格早于 val 最早可见 bar；同 event 跨 split = 0。
- holdout 边界 `2026-05-04 00:00 UTC`；正/负窗口进入 holdout 均为 0。

## P0 结果

| 机器门 | V1 | V2 |
|---|---|---|
| visible_end ≤ decision | PASS | PASS |
| box_end ≤ decision | PASS | PASS |
| event 不跨 split | PASS | PASS |
| 正/负窗口严格时间切分 | **FAIL** | **PASS** |
| 无 holdout 样本 | PASS | PASS |
| label 不越界 | PASS | PASS |
| manifest / image / label 守恒 | PASS | PASS |
| 100% market-bar 可追溯 | PASS | PASS |
| **P0 总裁决** | **FAIL** | **PASS** |

新版统一 `manifest.jsonl` 为 4,776 行，图片/标签均为 4,776；无重复 sample_id、无未入 manifest 图片、所有图片与标签均有 SHA256。正样本的 `anchor/decision/visible_end/window_start/window_end` timestamp 全部记录；负样本不适用的 anchor/decision 字段保持 null，不制造伪语义。

## 可复现性

同 seed（`20260807`）在同一目录完整重跑两次后：

| 文件 | SHA256（两次一致） |
|---|---|
| `w20_manifest.json` | `6814b86cdda7ca62ab4b1df8e7fa9be9acc96f184475430965b00079c1b8b047` |
| `w20_neg_manifest.json` | `2cdcf8898f70a1e8e9d453c23cbf93180dec323ee133db39d14bb3cd0f5213ba` |

P0 可视检查包为 24 个事件、24 个不同币种：`analysis/output/local_signal_v2_stageb_strictneg_v2_preview/`。抽样函数固定 seed，先保证 symbol 去重，再允许重复。

## 训练入口修复

- `scripts/train_local_signal_v2_stageb_on_3060.sh` 默认只认 strict-negative V2 和新 run name。
- 通用 3060 包装不再调用远端未纳入 git 的 `train_dense.py`；每次训练都从本仓下发 `src/detection/train.py`。
- 仓库训练器强制 `fliplr/flipud/mosaic/mixup/hsv_h/hsv_s/hsv_v = 0`。
- P0 自动脚本只写审计与裁决，然后停止等待 owner，不再自动进入 P1。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

PYTHONPYCACHEPREFIX=/tmp/fable_pycache PYTHONPATH=.:../yoyo-trading \
  .venv/bin/python scripts/build_local_signal_v2_stageb_strictneg_v2.py

.venv/bin/python scripts/audit_local_signal_v2.py \
  --dataset datasets/local_signal_v2_stageb_strictneg_v2 \
  --out analysis/output/p0_local_signal_v2_stageb_strictneg_v2_audit.json

.venv/bin/python scripts/backfill_dataset_manifests.py \
  --dataset datasets/local_signal_v2_stageb_strictneg_v2

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_local_signal_v2_stageb_strictneg_v2.py --preview 24

.venv/bin/python -m pytest \
  tests/test_local_signal_v2_stageb.py \
  tests/test_manifest_backfill.py \
  tests/test_detection_train_config.py \
  tests/test_detection_train_speed_knobs.py \
  tests/test_w20_midbox_causality.py -q
```

## 必报模型/交易指标说明

本轮是数据协议修复，不是模型实验：没有训练或评估新模型，因此 val AUC、置换检验 p、top-decile 毛/净收益、胜率、单特征基线与匹配随机对照组均不适用。旧权重的 mAP/tip 指标不能转移到新数据集，也不能用来通过 P1。

## 风险与诚实声明

1. P0 PASS 只证明数据因果性、切分、守恒与可追溯性通过，不证明模型有效或存在交易 edge。
2. 新数据集仍由旧 pad200 event anchor 迁移，不是真 tip 金标；规范要求的 event precision / recall / FP per 1,000 bars 尚未产生。
3. 旧 `owner_lsv2_stageb_cold` 同时受 V1 负样本跨时间块与 HSV 非零影响，不能继续作为 V2 候选；其历史报告不篡改，只在 live verdict/decision 中标为 invalidated。
4. 规范 §15 的 A/B1/B2/C1/C2/C3 矩阵尚未执行；本轮没有用“修数据”的名义偷跑多变量训练。
5. 本轮没有读取或评分 holdout，V2 holdout 消耗次数仍为 0。

## 下一步（需要 owner 决策）

P0 已完成并按规范 §14 停止。若 owner 批准进入 P1，必须以 strict-negative V2 为输入、零 HSV 训练，并先冻结 A/B/C 的同事件同时间对照矩阵与 event-level 验收阈值；不得复用旧权重的结果冒充新数据集结果。
