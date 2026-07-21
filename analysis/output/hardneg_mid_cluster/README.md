# Hardneg mid-cluster 目录索引（H-DET-2 inventory）

**状态**：清单已备，**未开训**。等 v13 pad200 结束 + owner 单变量批准。  
**勿与** pad200 空标背景混谈（见 `docs/learnings/pad200-empty-bg-is-not-mid-hardneg.md`）。

## 本目录

| 文件 | 说明 |
|------|------|
| `hardneg_mid_cluster_candidates.csv` | 全量候选（~2892） |
| `hardneg_mid_cluster_summary.json` | 统计 + 10 预览清单 |
| `PROTOCOL_train_after_v13.md` | 加训协议 |
| `previews/*.png` | cv2 预览（青框+黄 tip 线） |

## 旁路调试产出（同夜，不抢 MPS）

| 产出 | 路径 |
|------|------|
| LWC 批量交互 | `../wuzao_lwc_hardneg_batch/index.html` |
| 叠框画廊 | `../hardneg_overlay_gallery/index.html` |
| LS 发现级小包 | `../../../output/label_studio/tasks_hardneg_discovery.json` |
| 早期 3 窗对照 | `../wuzao_lwc_tip_compare/compare.html` |

## 复现

```bash
PYTHONPATH=. .venv/bin/python scripts/build_hardneg_mid_cluster_inventory.py --preview 10
PYTHONPATH=. .venv/bin/python scripts/build_hardneg_lwc_batch.py
PYTHONPATH=. .venv/bin/python scripts/overlay_hardneg_boxes.py
PYTHONPATH=. .venv/bin/python scripts/hardneg_to_labelstudio.py --limit 24
```
