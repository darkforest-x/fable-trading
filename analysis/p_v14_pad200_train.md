# v14 pad200（MAD-on）终局 + tip 对照 — 2026-07-22

**纪律**：未 promote `owner_best` / ACTIVE / frozen；未评 holdout；未清 forward_log。

## 复现命令

```bash
# Windows 3060 → Mac（已做）
HOST=zzc@192.168.1.3 NAME=owner_v14_pad200
scp "$HOST:C:/fable/runs/detect/runs/detect/$NAME/weights/best.pt" \
  "runs/detect/runs/detect/$NAME/weights/best.pt"
cp -f "runs/detect/runs/detect/$NAME/weights/best.pt" models/owner_v14_pad200.pt

bash scripts/eval_v14_vs_v12_tip.sh
```

## 训练终局（Windows RTX 3060）

| 项 | 值 |
|---|---|
| 数据 | `dense_owner_v14_pad200`（**MAD 默认开**；修正 v13 关 MAD 盲 end_incl 错窗） |
| 底座 | `models/owner_v12_htip.pt` |
| 计划 | 40 ep，patience=10，batch=16 |
| 实际 | **ep26 early stop**；best=**ep16** → `best.pt` |
| 稳定权重 | `models/owner_v14_pad200.pt`（sha256 `442adb05…f861`） |
| 机器 | `zzc@192.168.1.3`；日志 `C:\fable\logs\owner_v14_pad200.log` |

同口径官方 val（辅表；**不可当 tip 裁决**；val 仍为 v11 中段金标未 pad）：

| 权重 | best ep | P | R | mAP50 | mAP50-95 |
|---|---:|---:|---:|---:|---:|
| v12_htip | 22 | 0.513 | 0.638 | **0.534** | **0.284** |
| v13_pad200（MAD 关 / 错窗） | 22 | 0.109 | 0.051 | **0.027** | **0.010** |
| v14_pad200（MAD 开） | 16 | 0.226 | 0.244 | **0.155** | **0.063** |

val mAP 相对 v13 抬了约 **5–6×**，仍远低于 v12——符合「train tip 几何 vs val 中段」错位预期，**禁止**单独用这张表判 tip。

## H-DET-1 发现级（主表）

| 指标 | v12 (`owner_best`) | v13 pad200 | v14 pad200 MAD-on | 判定 |
|---|---:|---:|---:|---|
| true_tip tip_hit（val 重渲 tip，conf=0.3，n=120） | **0.925** (111/120) | **0.008** (1/120) | **0.033** (4/120) | 仍崩（略好于 v13） |
| tip-smoke 贴边开火 | **0/27** | **0/27** | **0/27** | **未过线**（须 ≫ v12） |

产物：`analysis/output/tip_rate_v14_pad200.json`、`analysis/output/diag_tip_smoke_v14.json`。

细节：`details_head` 前 30 窗里 **29/30 为 n_boxes=0**（conf=0.3）；4 次 tip_hit 不足以改变发现级结论。

## 解读

1. **MAD-on 修正了标签错窗，但没有修好 tip**：相对 v13，true_tip 从 0.008→0.033、val mAP 从 0.027→0.155，说明「数据没坏那么惨」有一点回报；相对 v12 的 0.925 / 实盘 tip 口径仍是两个数量级差距。  
2. **发现级仍未过**：通过标准是 tip-smoke ≫ v12 的 0/27；本轮仍是 **0/27**。  
3. **不能上**：不 promote。主线继续 v12。v14 证明「修标签 + 再训一轮 pad200」不是 tip 解药。  
4. 假设归因收敛：pad200「无后文」协议本身 +/或 训推渲染差（H-DET-4），而不是「只因 v13 关了 MAD」。

## 风险与诚实声明

- tip-smoke 用 `forward_log_vps_20260721.csv` 快照，与 v12/v13 同口径；非新前向 100。  
- 未跑 frozen-F1 / holdout；未做 owner 目视叠框。  
- tip_hit 略升可能是噪声（4/120）；不宣称为「MAD 救活了 tip」。  
- **未**改 ACTIVE / `owner_best`。

## 下一步（需 owner 点头的标出）

1. **默认**：主线保持 v12；H-DET-1（含 v14 MAD-on 复验）记 🔴。  
2. **排队**：H-DET-4 / EXT-5 渲染消融（GPU 空闲、单变量）——pad200 再训一轮应停。  
3. **可选（owner）**：H-DET-2 硬负开训。  
4. **禁止**：用 val mAP「再训凑数」、自动切 ACTIVE、耗 holdout、清 forward_log。
