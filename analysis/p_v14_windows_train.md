# v14 pad200 → Windows（3060）训练交接

> 状态：Mac **只建数据集**，**不在 Mac 开 YOLO 大训**。不 promote、不耗 holdout。  
> 数据集：`datasets/dense_owner_v14_pad200`（MAD 默认开；v13 保留不动）。  
> **主路径 = SSH/scp 到局域网 Windows RTX 3060**（与 v8–v10 / H-TS 同一套路）。  
> 之前交接里写「U 盘 / 手动拷」是错的——已纠正。

## 0. 标准主机（仓库惯例）

| 项 | 默认值 | 覆盖 |
|----|--------|------|
| SSH | `zzc@192.168.1.5` | `FABLE_3060_HOST` |
| 远端根 | `C:/fable` | `FABLE_3060_REMOTE` |
| 传数 | `tar` + `scp`（排除 `.npy` / `.cache` / `._*`） | 见 `scripts/sync_v14_to_windows.sh` |
| 长训 | **WMI `Win32_Process.Create`**（防 SSH 断线杀进程） | 同 `train_owner_hts.sh` |

先例：`scripts/train_on_3060.sh`、`scripts/train_owner_hts.sh`、`scripts/train_owner_v9_from_round7.sh`。  
密码不进仓库；本机用 `ssh-agent` / 已配好的密钥（`BatchMode=yes`）。

## 1. Owner 在 Mac 上跑这一条（传数）

```bash
bash scripts/sync_v14_to_windows.sh
# 只测连通：
# bash scripts/sync_v14_to_windows.sh --check
```

脚本会：

1. 检查 SSH → `C:/fable`  
2. 打包 `datasets/dense_owner_v14_pad200`（~1.6G）+ scp  
3. scp 基座 `models/owner_v12_htip.pt`（缺则 `owner_best.pt`）→ 远端 `models/`  
4. 远端解包并打印 train/val 图数  

**不要拷**：`dense_owner_v13_pad200`、`data/kline_*`、旧 `runs/`。  
代码用 Windows 上已有的 `C:/fable` 仓库 `git pull`（datasets 不入 git）。

### 传数之后：开训（二选一）

**A. 推荐 — Mac 上 SSH + WMI（断 SSH 也不杀训）**

```bash
HOST="${FABLE_3060_HOST:-zzc@192.168.1.5}"
ssh "$HOST" "New-Item -ItemType Directory -Force -Path C:\fable\logs | Out-Null; Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='cmd.exe /c cd /d C:\fable && .venv\Scripts\python.exe -m src.detection.train --data datasets/dense_owner_v14_pad200/data.yaml --model models/owner_v12_htip.pt --epochs 40 --patience 10 --batch 16 --workers 4 --device 0 --cache false --name owner_v14_pad200 > logs\owner_v14_pad200.log 2>&1'} | Out-Null; Write-Output started"
```

看进度：

```bash
ssh zzc@192.168.1.5 "Get-Content C:\fable\logs\owner_v14_pad200.log -Tail 20"
```

**B. 人在 Windows 盒子上** — `.\scripts\train_v14_pad200_windows.ps1`（或 `.bat`）。

| 旋钮 | 建议 | 备注 |
|------|------|------|
| batch | **16**（8GB 用 8） | OOM 再降 |
| workers | **4**（16GB 机）/ 8（内存够） | 历史 3060 训用 `--cache false --workers 4` |
| device | `0` | CUDA |
| 增强 | **勿改** | `SAFE_AUG` 全关 |
| finetune | 自动 | 非 `yolo*.pt` → AdamW `lr0=1e-4` |

预计墙钟：RTX 3060 **数小时～一夜**。

## 2. 权重取回 Mac（训完）

ultralytics 路径可能多一层 `runs/detect/`：

```bash
HOST="${FABLE_3060_HOST:-zzc@192.168.1.5}"
REMOTE="${FABLE_3060_REMOTE:-C:/fable}"
NAME=owner_v14_pad200
mkdir -p "runs/detect/runs/detect/$NAME/weights" models
# 试双路径
scp "$HOST:$REMOTE/runs/detect/runs/detect/$NAME/weights/best.pt" \
    "runs/detect/runs/detect/$NAME/weights/best.pt" \
  || scp "$HOST:$REMOTE/runs/detect/$NAME/weights/best.pt" \
    "runs/detect/runs/detect/$NAME/weights/best.pt"
cp "runs/detect/runs/detect/$NAME/weights/best.pt" models/owner_v14_pad200.pt
```

**禁止**自动覆盖 `models/owner_best.pt` / `ACTIVE`。

## 3. 验收（回 Mac 跑；发现级）

```bash
PYTHONPATH=. python scripts/tip_detectability.py --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 --weights models/owner_v14_pad200.pt \
  --out analysis/output/tip_rate_v14_pad200.json

PYTHONPATH=. python scripts/diag_forward_detect_lag.py --from-log --tip-smoke \
  --weights models/owner_v14_pad200.pt \
  --out analysis/output/diag_tip_smoke_v14.json
```

通过标准仍是 tip-smoke / tip_hit，**不是** val mAP。**promote 另一次点头**。

## 4. Owner 同步清单（SSH 主路径）

1. Mac：重建已完成（`pad200_summary.json` + `mad_gate: true`）— 见 §5  
2. Windows：`git pull`（脚本/文档；大数据集不走 git）  
3. Mac：`bash scripts/sync_v14_to_windows.sh`  
4. 开训：§1A WMI 或 §1B `.ps1`  
5. Mac：§2 scp 取回 → §3 tip 验收  
6. **promote 另一次点头**

### 降级（仅 SSH 不通时）

U 盘 / 本机拖文件 **不是默认路径**。只有 `sync_v14_to_windows.sh --check` 失败、且 Owner 确认局域网不可用时，才临时用外置盘；解到 `C:\fable\datasets\dense_owner_v14_pad200\`。

## 5. 重建状态（Mac）— **已完成**

- `mad_gate: true`；正样本 **2635**；skip 1406（其中 both_high/MAD 1318）  
- 日志：`logs/build_v14_pad200.log`；报告：`analysis/p_v14_pad200_rebuild.md`  
- 抽查：`analysis/output/v14_train_sample20/`  
- **可 SSH 开训**（Mac 不训 YOLO）。

## 6. 若连不上要 Owner 补什么

默认已写在仓库里（`zzc@192.168.1.5` / `C:/fable`）。若机器换了，只需告诉 agent / 设环境变量：

- 新 `FABLE_3060_HOST`（`user@ip`）  
- 新 `FABLE_3060_REMOTE`（Windows 仓库根，正斜杠如 `D:/fable`）  
- 本机对该主机的 **SSH 密钥登录**（脚本用 `BatchMode=yes`，不读密码）
