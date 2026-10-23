# 960 模型为何第 6 轮成为最佳（2026-08-29）

## 技术结论

960 并非只训练了 6 轮。旧运行在第 16 轮触发 `patience=10` 早停；关闭早停后的对照运行完整
训练了 40 轮。两次运行前 16 轮除耗时外所有训练与验证数值完全一致，且两份 `best.pt` 的
499 个模型张量逐值完全相同，均对应第 6 轮。

第 6 轮之后出现的是**定位过拟合**，不是训练中断：训练框损失继续下降，但验证框损失和 DFL
持续恶化。第 40 轮相对第 6 轮，训练框损失下降 75.9%，验证框损失却上升 44.3%，验证 DFL
上升 57.0%；严格定位指标 mAP50-95 从 0.77768 降至 0.69426。故额外轮次没有产生更好的
960 `best.pt`。

## 关键证据

| 轮次 | Precision | Recall | mAP50 | mAP50-95 | train box | val box | val DFL |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.79287 | 0.85095 | 0.89239 | 0.69692 | 0.68533 | 0.16051 | 0.25782 |
| **6** | **0.85377** | **0.85228** | 0.91156 | **0.77768** | 0.22495 | **0.11650** | 0.26638 |
| 9 | 0.91119 | 0.88787 | **0.95028** | 0.72430 | 0.17437 | 0.17008 | 0.34467 |
| 16 | 0.92070 | 0.90949 | 0.93045 | 0.73187 | 0.12406 | 0.15209 | 0.35019 |
| 25 | 0.90548 | 0.89169 | 0.91402 | 0.75146 | 0.09079 | 0.13576 | 0.34423 |
| 40 | 0.92215 | 0.88488 | 0.89527 | 0.69426 | 0.05427 | 0.16812 | 0.41811 |

mAP50 在第 9 轮达到最高，但 mAP50-95 已下降。这表示模型仍能在较宽松的 IoU=0.50 条件下找到
目标，却越来越难把小框精确贴合标签。对于本项目强调的几根 K 线小框，mAP50-95 比单独 mAP50
更贴近框位置要求。

第 16 轮之后最好的 mAP50-95 是第 25 轮的 0.75146，仍比第 6 轮低 0.02622；继续训练没有
“后面重新变好”。第 40 轮 Precision/Recall 较高而 AP 较低并不矛盾：P/R 是某个工作点，AP
衡量整条置信度排序曲线，mAP50-95 还同时要求多个更严格 IoU 阈值。

## 为什么会这么早收敛

### 已由曲线证实：定位头在第 6 轮后过拟合

训练 box loss 从第 6 轮的 0.22495 单调下降至第 40 轮的 0.05427，但 val box loss 从最低
0.11650 上升到 0.16812，val DFL 同期从 0.26638 上升到 0.41811。训练集继续变好、验证集
定位同时变差，是明确的泛化间隙。

### 很可能的背景：任务规则统一，预训练模型很快学会

模型从 `yolo11s.pt` 预训练权重开始，训练图全部来自同一渲染器、同一 1280×742 画布，只含
W18/W19、核心 4/5 根和固定的 post2–9 支持。32,000 张数据中为 8,000 正例与 24,000 负例；
每轮训练约 27,200 张，至第 6 轮已经看过约 163,200 个图像呈现。形态和标签几何高度规范，
因此 6 个 epoch 并不等于训练量很少。

3:1 的负例会让“有没有目标”的背景判别快速稳定，而框回归只从正例得到监督；这与后期
Precision 较高、严格定位下降的现象一致。但它是否是主因尚未经过负例比例单变量实验，不能
直接作为因果结论。

### 排除项：不是早停耐心不足，也不是学习率爆炸

- `patience=0` 的完整 40 轮运行仍选择第 6 轮，排除了“多等几轮会出现更好权重”。
- AdamW 学习率从约 `1e-4` 平滑降至 `3e-6`，曲线没有突然崩溃，排除了历史上
  `optimizer=auto` 高学习率炸权重的故障模式。
- 两次运行前 16 轮数值完全相同，说明复跑没有数据顺序、seed 或配方漂移。

## 范围、方法与零假设对照

本审计只读取两次训练的 `results.csv`、`args.yaml` 和 checkpoint，不读取行情、holdout、收益或
Owner 新标签。旧运行与 full40 运行固定使用相同数据集、`imgsz=960`、batch 8、seed 0、
确定性训练和依赖环境；唯一有意变化是 `patience 10 → 0`。

非方向性模型审计不适用 val AUC、收益、胜率、top-decile、置换收益检验或匹配随机入场对照，
不编造这些指标。等价的严格零假设是“文件 SHA 不同代表模型参数不同”：逐张量审计结果为
499/499 完全相同、差异元素 0，因此拒绝这个解释。第 40 轮 `last.pt` 与最佳模型则有
412/499 个张量变化，证明审计能够检出真正不同的模型。

## 限制与尚未确定的原因

- 第 6 轮是这一个冻结时间验证切分上的最佳点，不证明所有 seed、所有时间段都固定在第 6 轮。
- 当前证据不能在标签边界噪声、有效图像多样性、正负比例和 scale/translate 轻微增强之间分摊
  因果贡献；必须一次只改一个变量。
- 自家 val mAP 不能代替 tip Gold 或新鲜前向验收，本结论只决定训练 checkpoint 选择。
- 本轮没有新增 holdout 消耗、行情扫描、阈值调整、promote、部署或交易状态变化。

## 建议

1. 960 继续使用第 6 轮 `best.pt`，不要把第 40 轮 `last.pt` 当成升级版。
2. 等 1280 跑完后先看它的最佳轮次和完整曲线，再决定是否值得做同输入推理对照。
3. 若要解释“为何第 6 轮”到因果层面，预注册三项独立实验：多 seed 稳定性、负例比例单变量、
   标签边界/框高噪声审计；不得打包同时改。

## 复现

```bash
cd /Users/zhangzc/fable-trading

OLD=analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960
FULL=analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960_full40

.venv/bin/python - <<'PY'
from pathlib import Path
import pandas as pd
import torch

old = Path("analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960")
full = Path("analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960_full40")
a = pd.read_csv(old / "results.csv").rename(columns=str.strip)
b = pd.read_csv(full / "results.csv").rename(columns=str.strip)
assert (a.drop(columns="time") == b.iloc[:len(a)].drop(columns="time")).all().all()

def state(path):
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    return (ckpt.get("ema") or ckpt["model"]).state_dict()

sa = state(old / "weights/best.pt")
sb = state(full / "weights/best.pt")
assert list(sa) == list(sb)
assert all(torch.equal(sa[key], sb[key]) for key in sa)
print("overlap curves exact; 499/499 best tensors exact")
PY
```
