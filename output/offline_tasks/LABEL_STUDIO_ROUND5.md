# Round5 — v5 预标（owner_v5_from_v4 F1=0.663）

- URL: http://127.0.0.1:8081
- 账号: fable-review@example.com
- 密码: fable-review-local
- 模型: models/owner_best.pt = owner_v5_from_v4（frozen F1 0.663 / P 0.76）
- 数据: dense_swap_v1 合约图
- conf 保留: ≥0.20

## 项目
见 LS 首页 `round5_chunk1_v5` / `round5_chunk2_v5`

## 打标
1. 打开项目 → **Label All Tasks**
2. 快捷键 1 画框 · 2 标杆 · ⌘⌫ 清空 · ⌘↵ 提交
3. 预标是 v5 输出，直接改即可

任务 JSON: output/label_studio/tasks_round5_chunk{1,2}.json
