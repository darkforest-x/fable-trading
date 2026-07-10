# P2a 因果方向 YOLO 经济性验收

## 结论

固定 `yolo11n-cls` 因果方向分类器训练自然结束，epoch 6 为最佳，epoch 14 因 patience
提前停止。验证准确率 `34.78%`，低于只猜验证集多数类 long 的 `35.68%`；`no_trade`
召回率仅 `19.16%`，导致 5,900 个候选中执行 4,850 笔。

图像模型的毛收益为每笔 `+0.04764%`，比 numeric LightGBM 基线高约 `0.08576` 个百分
点，但仍低于 `0.06%` 的低成本情景。按项目固定 `0.20%` 往返成本后，每笔净收益
`-0.15236%`、PF `0.7472`，未达到净收益为正且 PF≥1.3 的门槛。因此该模型
**验收失败、拒绝晋升，不接入 ACTIVE、主账本或 VPS 信号链**。

## 复现命令

```bash
PYTHONPATH=. python3 -m src.detection.build_direction_dataset \
  --out datasets/ma206_direction_causal_v1

PYTHONPATH=. /Users/zhangzc/fable-trading/.venv/bin/python \
  -m src.detection.train_direction_classifier \
  --data datasets/ma206_direction_causal_v1 \
  --device mps \
  --name ma206_direction_causal_yolo11n_repro

PYTHONPATH=. uv run scripts/evaluate_direction_classifier.py \
  --dataset datasets/ma206_direction_causal_v1 \
  --weights runs/classify/runs/classify/ma206_direction_causal_yolo11n/weights/best.pt
```

## 数据与配置

| 项目 | 值 |
|---|---|
| 候选来源 | MA206 expanded long/short 规则并集，因果时间去重 |
| train / val | 23,520 / 5,900 |
| train 类别 | long 7,040 / short 8,383 / no_trade 8,097 |
| val 类别 | long 2,105 / short 1,749 / no_trade 2,046 |
| val 时间 | 2026-03-24 11:00 至 2026-05-03 05:00 UTC |
| 输入 | 200 bars，640×640 原图，训练 imgsz=320 |
| 模型 | yolo11n-cls.pt，seed=42，batch=32 |
| 计划 / 实际 epochs | 20 / 14，patience=8，best epoch=6 |
| 增强 | HSV、flip、mosaic、mixup、copy_paste、translate、scale、erasing 全关 |
| manifest SHA-256 | `ad174ad4dc6914dc87dc746fb7df1c7f9ff91fa7a5b27ae6476a3ccb29c9f1a2` |
| best.pt SHA-256 | `d28b830927adf35559294198312dcbcd850a97e00a7d46ea5c7958560c1b0363` |

训练 loss 从 `1.1092` 降至 `0.1452`，但 val loss 从 `1.1353` 升至 `3.6958`，是明显
过拟合。best epoch 6 top-1 为 `34.78%`，继续训练没有改善泛化。

## 分类结果

| 模型 | accuracy | balanced accuracy | long recall | short recall | no_trade recall |
|---|---:|---:|---:|---:|---:|
| 图像 YOLO11n | 34.78% | 34.07% | 58.24% | 24.81% | 19.16% |
| numeric LightGBM | 29.69% | 33.36% | 0.00% | 99.60% | 0.49% |
| 候选 side 基线 | 29.73% | 30.50% | 43.14% | 48.37% | 0.00% |
| 多数类 long | 35.68% | 33.33% | 100.00% | 0.00% | 0.00% |

图像模型预测 long 3,415 次、short 1,435 次、no_trade 1,050 次。核心错误不是候选不足，
而是无法可靠识别应放弃的候选，交易覆盖率高达 `82.20%`。

## 扣费收益

| 模型 | 交易数 | 毛收益/笔 | 0.06% 成本净收益/笔 | PF | 0.20% 成本净收益/笔 | PF |
|---|---:|---:|---:|---:|---:|---:|
| 图像 YOLO11n | 4,850 | +0.04764% | -0.01236% | 0.9759 | -0.15236% | 0.7472 |
| numeric LightGBM | 5,868 | -0.03812% | -0.09812% | 0.8202 | -0.23812% | 0.6287 |
| 候选 side 基线 | 5,900 | -0.03760% | -0.09760% | 0.8217 | -0.23760% | 0.6304 |

图像模型毛收益胜率为 `36.68%`；固定成本 `0.20%` 时顺序复利诊断最大回撤约 `99.96%`。该回撤
不是组合仓位回测，但足以说明把大多数候选直接下单不可用。`0.30%` 成本下净收益进一步
降至 `-0.25236%`、PF `0.6214`。

## 与项目指标的对应

- val AUC、置换检验 p、top-decile 收益：本实验输出是三分类 argmax，不生成二分类
  ranking，因此这些指标不适用，不能用 AUC 替代真实扣费收益。
- 单特征基线：用候选 side 原样下单；另加同 manifest 数值特征 LightGBM 基线。
- 正式门槛：固定成本 `0.20%` 下净收益必须为正、PF≥1.3、交易数≥100；实际为
  `-0.15236%`、`0.7472`、`4,850`，判定 false。

## 风险与诚实声明

- 标签由固定 TP5/SL2、h72 barrier 生成；标签本身定义的“正确方向”不等于可交易净
  收益。图片准确率和交易盈利必须分开验收。
- 该实验只证明当前固定数据、标签、模型和 argmax 策略失败，不能证明所有视觉方向模型
  都无效；但没有证据支持继续用相同配方加 epoch。
- 验证集已用于本轮判定，后续不能围绕这份结果反复调参并宣称独立验证。
- 本轮没有读取 judgment holdout，没有修改阈值、成本、TP/SL、候选预设、ACTIVE、
  q90/H1 账本或实盘执行器。

## 完整性证据

| 工件 | SHA-256 / 状态 |
|---|---|
| `models/ACTIVE` | `42df83c98247188873613eec3af04ffd258520a98e8b4b089c5f322b9db8b9c7`，未变 |
| q90 主账本 | `c903d37798d374bef59404adcc18c92e3024ac77ab348b1435bb760e19198527`，未变 |
| H1 影子账本 | `02ecccec22dceca0dd324460e6a9baa6e73997aabd22783784502a870a87af36`，未变 |
| holdout | 未读取、未消耗 |
| 模型晋升 | rejected |
| 相关测试 | 20 passed |
| 完整验证推理 | 5,900 rows，exit 0 |

完整分类矩阵、每类 precision/recall/F1、成本情景和逐笔预测分别保存在
`analysis/output/causal_direction_profit_metrics.json` 与本机未入 git 的
`analysis/output/causal_direction_val_predictions.csv`。

## 下一步

1. 保持 q90 主线冻结，继续 q80 独立影子漏斗，用真实前向数据判断“候选不足”还是“评分
   过滤过严”。
2. 不继续相同 YOLO 分类配方；下一轮只能做一个预注册变量，并重新划分未使用验证窗口。
3. 优先研究降低交易覆盖率的 `no_trade` 判别，而不是通过放宽阈值制造更多信号。
