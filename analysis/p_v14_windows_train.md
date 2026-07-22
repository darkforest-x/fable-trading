# v14 pad200 → Windows 训练交接

> 状态：Mac **只重建数据集**，**不在 Mac 开 YOLO 大训**。不 promote、不耗 holdout。  
> 数据集：`datasets/dense_owner_v14_pad200`（MAD 默认开；v13 保留不动）。

## 1. 拷到 Windows 什么

| 必拷 | 说明 | 体积量级 |
|------|------|----------|
| `datasets/dense_owner_v14_pad200/` 整目录 | `images/{train,val}` + `labels/{train,val}` + `data.yaml` + `pad200_summary.json` | **~1.6 GB**（正样本 2635；对照 v13≈1.8G） |
| `models/owner_v12_htip.pt` 或 `models/owner_best.pt` | chain 基座（v12） | ~数十 MB |
| 仓库代码（`git pull`） | `src/detection/train.py` 已内置禁增强 | — |

**不要拷**：`dense_owner_v13_pad200`（错窗对照集）、`data/kline_*`（训练不需要）、`runs/` 旧 run。

建议（Mac → 移动硬盘 / scp / 局域网）：

```bash
# Mac 打 tar（重建完成后）
tar -C datasets -cf - dense_owner_v14_pad200 | pigz -1 > /tmp/dense_owner_v14_pad200.tar.gz
# 或
rsync -a --info=progress2 datasets/dense_owner_v14_pad200/ /Volumes/USB/dense_owner_v14_pad200/
```

Windows 解到仓库根下：`datasets\dense_owner_v14_pad200\`（保持 `data.yaml` 内 path 可用；`src.detection.train` 会 `resolve()`）。

## 2. Windows 第一条训练命令

PowerShell（仓库根，已装 torch+ultralytics+CUDA）：

```powershell
$env:PYTHONPATH="."
python -m src.detection.train `
  --data datasets/dense_owner_v14_pad200/data.yaml `
  --model models/owner_v12_htip.pt `
  --epochs 40 --patience 10 `
  --batch 16 --workers 8 `
  --device 0 `
  --cache disk `
  --name owner_v14_pad200
```

一键脚本：`.\scripts\train_v14_pad200_windows.ps1` 或 `scripts\train_v14_pad200_windows.bat`。

| 旋钮 | 建议 | 备注 |
|------|------|------|
| batch | **16**（8GB VRAM 用 8；12GB+ 可用 16–24） | OOM 再降 |
| workers | **8** | CPU 核多可 8–12 |
| device | `0` | CUDA |
| 增强 | **勿改** | `train.py` 的 `SAFE_AUG`：`fliplr/flipud/mosaic/mixup=0`，`hsv_h=0`，`hsv_s/v=0.05` |
| finetune | 自动开 | 非 `yolo*.pt` 基座 → AdamW `lr0=1e-4` |

预计墙钟：RTX 3060/4060 级 **数小时～一夜**（40 ep / patience 10；v13 Mac MPS ~20h 参考，CUDA 通常快数倍）。

## 3. 权重落点

| 路径 | 含义 |
|------|------|
| `runs/detect/owner_v14_pad200/weights/best.pt` | ultralytics 产出（有时多一层 `runs/detect/`） |
| `models/owner_v14_pad200.pt` | 脚本稳定拷贝（**未** promote） |

**禁止**自动覆盖 `models/owner_best.pt` / `ACTIVE`。

## 4. 验收（可回 Mac / VPS 跑）

```bash
# tip_hit（发现级）
PYTHONPATH=. python scripts/tip_detectability.py --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 --weights models/owner_v14_pad200.pt \
  --out analysis/output/tip_rate_v14_pad200.json

# tip-smoke（对照 v12 的 0/27；需 forward_log + kline）
PYTHONPATH=. python scripts/diag_forward_detect_lag.py --from-log --tip-smoke \
  --weights models/owner_v14_pad200.pt \
  --out analysis/output/diag_tip_smoke_v14.json
```

对照包：`bash scripts/eval_v13_vs_v12_tip.sh` 可改权重变量仿跑。通过标准仍是 tip-smoke / tip_hit，**不是** val mAP。

## 5. Owner 同步清单

1. Mac：等重建 `pad200_summary.json` + `mad_gate: true`  
2. `git pull`（文档/脚本；**默认不 push 大数据集**——datasets 在 gitignore）  
3. 拷 `datasets/dense_owner_v14_pad200` + v12 基座权重到 Windows  
4. 跑 §2 命令或 `.ps1`  
5. 把 `models/owner_v14_pad200.pt` 拷回 Mac 再 tip 验收  
6. **promote 另一次点头**

## 6. 重建状态（Mac）— **已完成**

- `mad_gate: true`；正样本 **2635**；skip 1406（其中 both_high/MAD 1318）  
- 日志：`logs/build_v14_pad200.log`；报告：`analysis/p_v14_pad200_rebuild.md`  
- 抽查：`analysis/output/v14_train_sample20/`  
- **可以拷盘开训**（Mac 不训 YOLO）。
