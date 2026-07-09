# 标注可视化工具已就绪（睡醒直接用）

更新：2026-07-10 ~01:58 CST

---

## 1. FiftyOne — 已完全可用 ✅

| 项 | 值 |
|----|-----|
| URL | **http://127.0.0.1:5151** |
| 状态 | App 已 launch，mistakenness 已算完 |
| Dataset | `fable_dense_val`（1255 val 图） |
| ground_truth | E2.1 规则框（MAX_DENSE=12） |
| predictions | 旧 best.pt conf=0.30 |
| 难例列表 | `output/offline_tasks/fiftyone_hard/top50_mistakenness.tsv` |
| screen | `fable_fiftyone` |

**操作**：打开 URL → 左侧字段打开 `ground_truth` + `predictions` → 按 `mistakenness` **降序**排序。

重启：

```bash
screen -dmS fable_fiftyone bash -lc 'cd ~/fable-trading && bash scripts/start_fiftyone_review.sh'
```

---

## 2. Label Studio — 服务已起 ✅（首次需登录导入一次）

| 项 | 值 |
|----|-----|
| URL | **http://127.0.0.1:8081** |
| 用户 | `fable-review@local` |
| 密码 | `fable-review-local` |
| Docker | `fable_label_studio` Up |
| 任务包 | `output/label_studio/tasks_val.json`（80 张） |
| 标签配置 | `output/label_studio/label_config.xml` |

**第一次打开（约 1 分钟）**：

1. 打开 http://127.0.0.1:8081  
2. 用上面账号登录（用户已建好）  
3. Create Project → 名 `dense_15m_val_audit`  
4. Settings → Labeling Interface → 粘贴 `label_config.xml` 全文  
5. Import → 上传 `tasks_val.json`  
6. 开始改框（绿框=E2.1 预标）

> 新版 LS 禁用了 legacy API token，无法无人值守自动建项目；**服务与账号已就绪**，导入只需你点一次。

重启：

```bash
docker start fable_label_studio
# 或
bash scripts/start_label_studio_review.sh
```

---

## 3. 静态页（无需这两工具时）

- 本地对照：http://127.0.0.1:8643/label_audit_e2_compare.html  
- VPS 看板：http://103.214.174.58:8642  

---

## 4. 注意

- 仅本机；密码勿用于公网  
- 改框 **不会** 自动写回 `auto_label` / `datasets`  
- 主线交易仍不依赖 YOLO 框  

---

## 5. 其它通宵任务（并行中）

- YOLO E2.1 训练：`screen -r fable_yolo_e21_train`  
- SWAP expand：仍在拉  
- P2.5 Phase2 已合 main 并部署 VPS  
