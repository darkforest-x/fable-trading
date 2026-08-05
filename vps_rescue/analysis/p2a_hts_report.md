# H-TS — 检测层训练图时间切分实验

日期: 2026-07-17
一句话: 训练图 window-end 严格 < 2026-05-04，chain 续训自 owner_best，**不 promote**。

## 复现
```bash
PYTHONPATH=. .venv/bin/python scripts/build_hts_dataset.py
bash scripts/train_owner_hts.sh --force
```

## 数据
- src `dense_owner_v9` → `dense_owner_hts`
- kept=7819 dropped=1345 (post_cutoff=968, unresolved=377)
- stats: `{"train_kept": 5743, "train_post_cutoff": 715, "train_unresolved": 213, "val_kept": 2076, "val_unresolved": 164, "val_post_cutoff": 253}`

## frozen-eval（owner_eval_frozen 尺子）
- **H-TS** F1 **0.658**  P 0.665  R 0.651  conf=0.3
- baseline **owner_v10_chain** F1 **0.645**
- ΔF1 = +0.013
- 曲线: 15 轮 best@5 mAP50=0.5193 ✅
- sweep: [{'conf': 0.15, 'f1': 0.605, 'p': 0.485, 'r': 0.803, 'tp': 184, 'fp': 195, 'fn': 45}, {'conf': 0.2, 'f1': 0.642, 'p': 0.554, 'r': 0.764, 'tp': 175, 'fp': 141, 'fn': 54}, {'conf': 0.3, 'f1': 0.658, 'p': 0.665, 'r': 0.651, 'tp': 149, 'fp': 75, 'fn': 80}, {'conf': 0.4, 'f1': 0.619, 'p': 0.747, 'r': 0.528, 'tp': 121, 'fp': 41, 'fn': 108}]

## 解读
- **H-TS 不降反升（ΔF1 +0.013）**：踢掉 accept 窗 968 张训练图后 frozen-F1 更好。
  因此「检测层见过 holdout 期形态 → F1 虚高」这条解释**被削弱**——至少在 frozen-eval
  尺子上，时间泄漏不是虚高主因；噪声标签/过时窗反而可能更伤模型。
- **PF 7.5 仍说不清**：F1 尺子 ≠ 事件回测 PF。回测 accept 窗仍可能因候选生成器
  覆盖了那段历史而偏乐观；终审仍是**前向 100 笔**。
- **未 promote**：H-TS 略优于 v10，但是否替换 owner_best 需你拍板（小样本 Δ，
  且训练图更少）。

## 纪律
- 未读 holdout 判断数据；未写 models/ACTIVE；未 promote owner_best。
- 丢弃的 post_cutoff 图只用于本实验定义，不回灌训练。
