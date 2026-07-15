# Round-6 打标包（一半 SWAP 难例 + 一半 scout/分歧）

**模型预标**：`owner_best.pt`（改框 / 删框 / 补框即可）  
**本批新增**：1500 张（chunk 3–5）  
**Round-6 累计**：2000 张（5 个 chunk 文件）

## bucket 含义

| bucket | 含义 |
|---|---|
| `swap_hard` | 合约图库难例（优先模型 conf 0.15–0.45 + FO hardlist） |
| `scout_gallery` | 侦察兵当前 gallery 图 |
| `model_uncertain` | 补齐「分歧半」的模型犹豫区（scout 图不够时） |

## 导入本批

1. Label Studio :8081，label_config 用 `label_config_v2.xml`（含 ⭐ 标杆）
2. 每个 chunk 一个项目：
```bash
PYTHONPATH=. python3 scripts/ls_auto_import.py round6_halfhalf_chunk3 \
  output/label_studio/tasks_round6_halfhalf_chunk3.json
PYTHONPATH=. python3 scripts/ls_auto_import.py round6_halfhalf_chunk4 \
  output/label_studio/tasks_round6_halfhalf_chunk4.json
PYTHONPATH=. python3 scripts/ls_auto_import.py round6_halfhalf_chunk5 \
  output/label_studio/tasks_round6_halfhalf_chunk5.json
```
3. 标完 export → 合并 golden_pool → build dense_owner_v7 → 再训

## 纪律

- 勿把 `owner_eval_frozen` 符号的新标并进训练集（build 时 `--exclude-eval`）
- 已在 golden_pool / 已有 round6 chunk 的 stem 已排除
