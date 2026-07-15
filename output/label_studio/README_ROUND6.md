# Round-6 打标包（一半 SWAP 难例 + 一半 scout/分歧）

**模型预标**：`owner_best.pt`（改框 / 删框 / 补框即可）  
**总量**：500 张，2 个 chunk  

## bucket 含义

| bucket | 含义 |
|---|---|
| `swap_hard` | 合约图库难例（优先模型 conf 0.15–0.45 + FO hardlist） |
| `scout_gallery` | 侦察兵当前 gallery 图 |
| `model_uncertain` | 补齐「分歧半」的模型犹豫区（scout 图不够时） |

## 导入

1. Label Studio :8081，label_config 用 `label_config_v2.xml`（含 ⭐ 标杆）
2. 每个 chunk 一个项目，或同一项目分批 import：
```bash
PYTHONPATH=. python3 scripts/ls_auto_import.py round6_halfhalf_chunk1 \
  output/label_studio/tasks_round6_halfhalf_chunk1.json
PYTHONPATH=. python3 scripts/ls_auto_import.py round6_halfhalf_chunk2 \
  output/label_studio/tasks_round6_halfhalf_chunk2.json
```
3. 标完 export → 合并 golden_pool → build dense_owner_v7 → 再训

## 纪律

- 勿把 `owner_eval_frozen` 符号的新标并进训练集（build 时 `--exclude-eval`）
- 已在 golden_pool 的 stem 已排除
