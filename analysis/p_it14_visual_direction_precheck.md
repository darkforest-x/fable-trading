# IT-14 · tip 窗图像素是否携带方向信号（冻结 COCO embed 预检）

> 日期：2026-07-24 · **未碰 holdout** · **未训 YOLO** · **未 promote**  
> 脚本：`scripts/it14_visual_direction_precheck.py`（本机冻结 `yolo11n.pt` embed + LightGBM）  
> 机器产物：`analysis/output/it14_visual_direction_precheck.json`  
> 嵌入缓存：`analysis/output/_it14_scratch/it14_embed.npz`（不入 git）

## 0. 裁决（一句话）

**红灯。** 视觉 embed 三期 held-out AUC 均 ≤0.507、top-decile 方向 PF 均 ≤1.096，
**未过**脚本门（AUC>0.55 **或** top_dir_PF>1.3）。**不**值得因此上 3060 开双检测器训练。
像素相对 130 表特征没有多出可交易方向边；与 IT-02（表方向=硬币）同结论。

## 1. 复现命令

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=. MPLBACKEND=Agg .venv/bin/python -u -c \
  "from scripts.it14_visual_direction_precheck import main; raise SystemExit(main())"
# 无 argparse。首次跑会渲染 tip 窗并写 embed 缓存；再跑直接读缓存做 walk-forward。
# 依赖：models/yolo11n.pt、data/v16_candidates_100.csv、data/kline_*（loader）、ultralytics。
```

**本机范围**：冻结 COCO backbone **推理** embed + LightGBM 方向探针。  
**不在本机**：任何 YOLO train / finetune / GPU 重训（Owner 2026-07-24：训练默认 3060）。

## 2. 图从哪来（框=tip）

| 项 | 值 |
|----|----|
| 候选池 | `data/v16_candidates_100.csv`（v16 盘口火点，4014 行 → 有效嵌入 4012） |
| 窗定义 | `WIN=160` 根 15m bar，**窗右缘 = tip bar**（`frame.iloc[i-WIN+1 : i+1]`） |
| 渲染 | `src.detection.render.render_chart`（六均线 + K 线，1280×742，无未来 bar） |
| 语义 | 与 Owner「框=tip」一致：cluster 贴右缘；**无**事后回看窗、无确认态右填充 |
| 标签 | 对称障碍 `DIR_K=3*ATR`，从 **次根开盘** 起 72 bar 内先触上/下 → 方向 0/1（因果） |
| 成本 | 方向交易净收益用 IT-09 `net_dir`（TP5/SL2 − maker `FORWARD_COST`） |
| 切分 | 按时间排序 walk-forward 三桶 0–50→65 / 0–65→80 / 0–80→100；**<2026-05-04 池内**，无 holdout |

## 3. 数据统计

| 量 | 值 |
|----|----|
| 嵌入 tip 数 | 4012 |
| emb_dim | 256（冻结 `yolo11n` COCO） |
| up_rate（方向正类） | 0.482 |
| 币种 | 79（与 v16 候选一致） |
| 对照特征 | 同 tip 的 130 列表特征（候选 CSV 去掉 symbol/time/net） |

## 4. 结果表（walk-forward 三期）

门控：`visual AUC>0.55`（任 held-out 期）**或** `top_dir_PF>1.3` → 绿灯上 3060。

| 期 | n | VISUAL AUC | VISUAL dir_acc | VISUAL top_dir_PF | TABULAR AUC | TABULAR top_dir_PF |
|----|---|------------|----------------|-------------------|-------------|---------------------|
| 1 (50→65%) | 601 | 0.460 | 0.496 | 1.065 | 0.511 | 0.746 |
| 2 (65→80%) | 602 | 0.507 | 0.517 | 1.096 | 0.488 | 0.915 |
| 3 (80→100%) | 803 | 0.475 | 0.483 | **0.650** | 0.486 | 0.875 |

- VISUAL 最好 AUC **0.507**（<0.55）；最好 top_dir_PF **1.096**（<1.3）；最近期 PF **0.65** 塌。  
- TABULAR 同池同标签也不过门（AUC~0.49–0.51，PF 0.75–0.92）——与 IT-02 一致。  
- 视觉相对表特征：**没有**「像素看见了特征错过的方向」。

## 5. 与 `p_judgment_layer_lab.md` §7 的关系

§7 岔路口：

1. 接受「仅检测有价值」  
2. 3060 大样本重扫（最后一钉）  
3. 换信号  

IT-14 测的是 §7 **之外**的一条旁路假设——Owner 双检测器（视觉 gestalt 方向）在烧 3060 之前的廉价门。  
**红灯 = 不把「双检测器训练」从 §7 升成优先项。**  
§7 本身仍待 Owner 选；IT-14 **不**替代、也**不**否决 §7-(2) 大样本重扫（那是另一验证：墙是否小样本假象）。

## 6. 下一步建议（停在 3060 门前）

| 选项 | 建议 |
|------|------|
| 本机再调 embed/LGBM | **不建议**——门已清楚；再调 = 在探针上过拟合 |
| 3060 双检测器开训 | **不建议**（相对本门）——视觉未过门 |
| 若仍想「训练型 backbone 可能不同」 | 仅作 Owner 显式例外；需另写预注册卡 + 数据集规格，**Mac 只备数据，WMI 交 3060**，且先验低于 §7-(1)/(2) |
| §7 主建议 | 仍偏 **(1) 接受检测/告警价值**；**(2) 大样本最后一钉** 与 IT-14 正交、可另排 |

**禁止**：holdout#8、promote、本机 YOLO 重训、自动开训。

## 7. 若将来 Owner 仍批「双检测器」——3060 交训清单（预置，本轮不执行）

主机：`FABLE_3060_HOST` 默认 `zzc@192.168.1.3`（IP 会漂，见 `docs/learnings/3060-lan-ip-can-drift-from-dot5.md`）；远端 `C:/fable`；长训用 **WMI**（`v16_train_start.sh`），不用纯 SSH 前台。

```bash
# 0) 连通
bash scripts/sync_v16_to_windows.sh --check
# 或：bash scripts/train_on_3060.sh --check

# 1) Mac 建数据集（规格另定；冷启基座 yolo11n —— 纪律同 v16）
#    … build dataset on Mac …

# 2) 传数（先例 v16）
bash scripts/sync_v16_to_windows.sh          # 或按新数据集写 sync_*_to_windows.sh

# 3) WMI 开训（不 promote）
bash scripts/v16_train_start.sh              # 或仿写 NAME/DATASET；底层 train_dense.py
# Watch:
#   ssh "$FABLE_3060_HOST" "Get-Content C:\\fable\\logs\\<NAME>.log -Tail 30"

# 4) 取回 best.pt → Mac tip-smoke / 真 tip 金标验收；默认不 promote
```

要点：`train_dense.py`（盒子无完整 `src/`）；`--cache false`；16GB 机 `batch 8 / workers 2`（oomfix）；SAFE_AUG 勿开 flip/mosaic/hsv。

## 8. 风险与诚实声明

- **负结果非终审**：冻结 COCO 非图表微调；理论上 chart-tuned 检测器仍可能不同——但 IT-14 门的设计就是「正才升 3060」；负则强烈反对默认开训。  
- LightGBM 在 embed 上可过拟合早期桶；已用时间 walk-forward，最近期视觉 PF 0.65 最像实盘。  
- 候选仅 4012 个 v16 tip；与 §7-(2) 大样本问题正交。  
- Mac 曾对 `embed(..., device="cpu")` / 大 batch 出现偶发 segfault；本跑用 `model.to("cpu")` + 单图 embed，结果可复现于 JSON。  
- **未**读 holdout、**未**改 ACTIVE、**未**下单。
