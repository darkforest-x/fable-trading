# P2 Owner-short Hard-Negative重训与连续密度Canary（2026-08-11）

## 结论先行

- Owner于2026-08-11 16:12 CST明确授权的run
  `owner_lsv2_short_gold_center_hardneg_r1_ft`已在3060完整跑满40轮，耗时3423.21秒；曲线最佳点
  epoch 38，取回`best.pt` SHA-256为
  `029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537`。
- Mac独立固定val复验为P/R/mAP50/mAP50-95 =
  **0.8626 / 0.7770 / 0.8980 / 0.7405**。相对1:1 baseline，模型更保守：precision略升，
  recall下降约12.5pp；不能只凭mAP裁决。
- 在固定的post-val、pre-holdout连续12小时canary上，两模型各扫描215币、10,320个bar endpoints、
  82,560个W12–19暴露窗。hard-negative新模型的原始命中由22,037降至8,268（**-62.48%**），
  去重事件由732降至331（**-54.78%**）。
- 但新模型仍为 **32.074 events/1000 bar endpoints**，折算全市场 **662 events/day**，140/215
  个币在12小时内至少触发一次。相比旧模型1,464 events/day虽明显改善，密度仍然失败。
- 裁决：第二训练臂证明hard negatives方向有效，但尚未解决泛滥触发；不得promote、不得调高conf
  在同一canary上自我美化、不得读取最近两天holdout。下一步先人工复核canary的331个事件，区分
  真形态与可安全写入第三臂的难负例。

## 冻结训练合同

| 项目 | 1:1 baseline | 1:3 hard-negative arm |
|---|---:|---:|
| train positive | 1,143 | 1,143 |
| train easy negative | 1,143 | 1,143 |
| train hard negative | 0 | 2,286 |
| val positive / easy negative | 202 / 200 | 202 / 200（逐文件相同） |
| 初始化 | Stage A best | 同一Stage A best |
| 配方 | 40ep / patience10 / batch8 / seed0 | 完全相同 |
| 增强 | flip/mosaic/mixup/HSV全0 | 完全相同 |
| 唯一变量 | — | 增加hard negatives |

hard negative中916个来自Owner-long方向反类，1,370个来自原train时间块的模型排序安全背景；
hard占训练负例66.67%。选样未使用val、未来收益或holdout。

## 固定val结果

| 模型 / 设备 | Precision | Recall | mAP50 | mAP50-95 |
|---|---:|---:|---:|---:|
| 1:1 baseline / 3060 best复验 | 0.8508 | 0.9035 | 0.9224 | 0.7302 |
| 1:1 baseline / Mac MPS | 0.8467 | 0.9024 | 0.9206 | 0.7294 |
| 1:3 hardneg / epoch38记录 | 0.8376 | 0.8069 | 0.9000 | 0.7486 |
| 1:3 hardneg / 3060 best复验 | 0.8329 | 0.7895 | 0.8927 | 0.7307 |
| 1:3 hardneg / Mac MPS | 0.8626 | 0.7770 | 0.8980 | 0.7405 |

3060与Mac结论一致：mAP50-95没有崩，但recall明显下降。固定val仍只是平衡Owner正例和easy背景，
不能替代连续市场密度与真形态precision。

## Pre-holdout连续Canary合同

| 项目 | 冻结值 |
|---|---|
| val最后窗口结束 | 2026-05-02 23:45 UTC |
| canary endpoints | 2026-05-03 00:15–12:00 UTC |
| holdout边界 | 2026-05-04 00:00 UTC |
| 币种 | 215 / 215，无stale |
| bar endpoints / 模型 | 10,320 |
| W12–19 exposures / 模型 | 82,560 |
| conf / NMS IoU | 0.25 / 0.70（沿用，未调参） |
| 事件去重 | 同币核心中点±5根；保留首次跨门决策 |
| 数据读取 | 有界CSV前缀；`max_materialized_time=2026-05-03 12:00 UTC` |
| holdout读取 | 0 |

这块数据晚于val，不参与训练、hard-negative选择或best epoch选择；又严格早于holdout。原始K线通过
连续序列索引推导`nrows`，不是整表读取后过滤，因此没有在内存里碰到holdout行。

## 连续密度结果

| 指标 | 1:1 baseline | 1:3 hardneg | 变化 |
|---|---:|---:|---:|
| raw detections | 22,037 | 8,268 | -62.48% |
| raw / 1000 exposures | 266.921 | 100.145 | -62.48% |
| deduplicated events | 732 | 331 | -54.78% |
| events / 1000 bar endpoints | 70.930 | 32.074 | -54.78% |
| 全市场折算events/day | 1,464 | 662 | -54.78% |
| 触发币种 / 215 | 177 | 140 | -20.90% |
| 单币12h事件 median / p90 / max | 3 / 7 / 9 | 2 / 3 / 5 | 改善但不静默 |
| 核心4–7根占比 | 93.03% | 96.07% | +3.04pp |
| 首次延迟3–5根占比 | 99.32% | 95.77% | -3.55pp |

新模型没有靠把框宽或确认位置整体破坏来换取静默：核心4–7根占比反而略升，确认延迟中位/p90
仍为3/5根。问题是剩余触发覆盖140个币，331个事件仍远超“少而准”的目标。

## 必报指标状态

- val AUC、置换检验p、top-decile毛/净收益、胜率、单特征基线：N/A；本轮只验证YOLO检测密度，
  没有LightGBM排序和交易收益裁决。
- 匹配随机对照：N/A；canary没有使用未来收益，也没有把检测事件冒充订单。
- event precision / recall：N/A；canary没有独立逐事件Owner金标。低密度是必要门，不等于precision
  已经合格；当前密度本身已足以判失败。

## 风险与诚实声明

- 12小时canary只是一个独立市场块，不能代表所有行情；但662 events/day已经高到无需holdout即可
  判定当前版本不具生产资格。
- 331个事件中可能包含真实目标形态，不能全部自动写成负例；必须先做只看形态的人工审核。
- 不得在这块canary上反复选择新conf再把它称为独立结果；阈值仍是Owner决策。
- 未读取holdout、未改ACTIVE、未promote、未部署、未下单。

## 复现命令

```bash
# 数据与训练
FABLE_3060_HOST=zzc@192.168.1.4 bash scripts/train_w20_midbox_on_3060.sh \
  --dataset datasets/owner_short_gold_center_hardneg_r1 \
  --base analysis/output/lsv2_stagea/owner_lsv2_stagea_randomcrop_v1_cold/weights/best.pt \
  --name owner_lsv2_short_gold_center_hardneg_r1_ft \
  --epochs 40 --patience 10 --batch 8 --seed 0 --finetune

# 物理前缀截断的独立canary快照
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \
  scripts/backtest_owner_short_gold_center_recent.py historical \
  --out-dir analysis/output/owner_short_gold_center_preholdout_canary_20260503_v1 \
  --end 2026-05-03T12:00:00Z --context-bars 420

# 两个权重分别以完全相同参数运行scan；可用--shard-index 0/1并行
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \
  scripts/backtest_owner_short_gold_center_recent.py scan \
  --snapshot-dir analysis/output/owner_short_gold_center_preholdout_canary_20260503_v1/kline_snapshot \
  --out-dir analysis/output/owner_short_gold_center_preholdout_canary_20260503_v1/example_shard \
  --weights /path/to/frozen_best.pt --device 0 --batch 32 --hours 12 \
  --shard-index 0 --shard-count 2 --evaluation-scope preholdout_postval_canary

PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/pytest -q tests
python3 scripts/md_to_html.py \
  analysis/p2_owner_short_gold_center_hardneg_canary_20260811.md --out-dir analysis/html
```

## 下一步

先把331个新模型事件渲染成独立审核页，按Owner形态语义分为“真目标 / 相邻延续重复 / 镜像或普通
平台 / 明确难负例”。只允许最后两类中语义确定的样本进入第三臂；保持正例、val、初始化、训练
配方和conf不变，再验证另一个未使用的pre-holdout时间块。第三臂训练与任何新holdout读取均需Owner
再次逐次授权。
