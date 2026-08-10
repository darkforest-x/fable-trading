# P1 局部因果窗口对照预注册

**冻结日期**：2026-08-10

**依据**：`YOLO局部信号检测重构_Claude开发交接规范_V1.md` §14–§16，以及仓库铁律 1、5、12。

## 一句话目的

只用当前已有的历史标签与原始 K 线，回答一个问题：在相同事件、相同时间切分和相同训练参数下，20–30 根严格因果局部窗口是否比冻结的 200 根旧模型更少误报、且不发生召回崩塌。

本轮不新增盘口数据，不读取 holdout，不训练新标签，不碰 ACTIVE，也不做部署。

## 冻结矩阵

| 臂 | 输入 | 位置/因果 | 训练动作 | 数据集/权重 |
|---|---:|---|---|---|
| A | 200 | 旧模型；评估时窗口止于同一 decision | 冻结权重，只评估 | `models/owner_v10_chain.pt` |
| B1 | 24 | fixed、`visible_end=decision` | yolo11s 冷启动 | `datasets/local_signal_v2_p1_b1_w24` |
| B2 | 30 | fixed、`visible_end=decision` | yolo11s 冷启动 | `datasets/local_signal_v2_p1_b2_w30` |
| C3 | 20–30 | causal-right-range、`visible_end=decision` | yolo11s 冷启动 | `datasets/local_signal_v2_stageb_strictneg_v2` |

所有新训练臂固定 dataset seed=20260807、training seed=0、confirm delay={1,2}、小框左扩 2 根、easy negative 1:1、60 epochs、patience=15、batch=8、imgsz=960，全部 HSV/flip/mosaic/mixup 关闭。B1/B2 之间只改变窗口长度；C3 用同一构建逻辑的 20–30 根范围。

> 2026-08-10 执行勘误：最初把单一 `seed=20260807` 写成了同时约束数据构建和训练；实际仓库 trainer 在 C3/B1/B2 均使用 Ultralytics 默认 training seed=0，数据构建 seed 才是 20260807。该差异在 B2 结果产生前由 `args.yaml` 发现；三臂训练 seed 一致，没有做 seed 搜索，也不改变任何已运行权重。trainer 与 3060 wrapper 现已显式接受、传递并记录 training seed，避免今后再次依赖隐式默认值。

规范里的 C1/C2 Stage-A future pretrain 不进入本轮：仓库铁律 12 禁止新增事后可见数据路径；P1 先用 C3 直接检验是否不需要 Stage A。Hard negative 按规范留到 P2，不与 P1 窗口变量打包。

## 统一评估与机器裁决

- 统一使用 pre-holdout 的时间后移 validation 事件；禁止读取 `>=2026-05-04`。
- A/B1/B2/C3 都从同一个 decision endpoint 重渲染各自长度窗口。
- event match：预测框中心映射到 bar 后，与 anchor 相差不超过 ±2 根。
- 每 event 最多计 1 个 TP；额外框记 duplicate/FP。
- FP/1000 bars 的分母是实际扫描的 decision endpoints，不是把窗口内重复 K 线相加。
- 先只跑 A，冻结 `R*=min(0.70, A_max_recall)`；若 A 最大召回低于 0.50，则只报告曲线，不作相对接受。
- 候选通过发现级相对门需同时满足：召回 ≥R*；Event Precision 至少比 A 高 5 个百分点；FP/1000 ≤ A 的 80%。
- 若 A 最大召回低于 0.50，则在读取任何候选结果前改用冻结的绝对发现门：Event Recall ≥0.50、Event Precision ≥0.50、FP/1000 ≤250。
- mAP 只作训练诊断，不参与接受。

这仍是 P1 历史发现级对照，不是生产晋升。没有独立未见 forward/holdout 结果时，任何候选都不得 promote。

## 复现入口

```bash
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_local_signal_v2_stageb_strictneg_v2.py \
  --fixed-window-len 24 --out datasets/local_signal_v2_p1_b1_w24

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_local_signal_v2_stageb_strictneg_v2.py \
  --fixed-window-len 30 --out datasets/local_signal_v2_p1_b2_w30

bash scripts/train_w20_midbox_on_3060.sh \
  --dataset datasets/local_signal_v2_p1_b1_w24 \
  --name p1_b1_causal_w24_cold --epochs 60 --patience 15 --batch 8 \
  --host zzc@192.168.1.4
```

完整冻结配置见 `configs/local_signal_v2_p1.yaml`。

## A 基线后冻结记录（尚未读取 B/C 结果）

A 在 715 个共同 endpoint 上、预注册阈值网格内的最大召回仅 0.0754（27/358）；对应 Event Precision=0.0296、FP/1000=1239.2。它低于共享召回门的 0.50，因此相对同召回裁决不可计算，正式启用上面的绝对发现门。该门在任何 B1/B2/C3 结果产生前冻结。
