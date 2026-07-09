**运行状态见** `output/offline_tasks/REVIEW_TOOLS_READY.md`（本地访问 URL）。

# 标注可视化审查：FiftyOne + Label Studio

给 owner 用的「看框对不对」工具。**不改主 `.venv`**；不自动改 `auto_label`；不碰 holdout。

当前标签语义：E2（`MAX_DENSE_BARS=24` + `X_PAD_PX=6`）。

---

## 你要的两件事分别干什么

| 工具 | 你怎么用 | 解决什么 |
|------|----------|----------|
| **FiftyOne** | 浏览器里翻图、按 mistakenness 排序 | 自动找「模型与 GT 不一致」的难例 / 可疑标 |
| **Label Studio** | 点选、拖框、删框、通过 | **人手改标**的可视化工作台 |

流程建议：

```
FiftyOne 找可疑图名  →  Label Studio 精修这些图  →  导出/反馈规则  →  单变量改 auto_label  →  relabel
```

---

## A. FiftyOne（质量审计 / mistakenness）

### 环境

已有隔离环境：`fable-trading-codex/.venv_yolo_tools`（fiftyone 1.16）。  
项目主 `.venv` 有 ultralytics，用来导出预测框。

### 一键命令

```bash
cd ~/fable-trading

# 1) 用现有 best.pt 在 val 上出预测（约几分钟，MPS/CPU）
.venv/bin/python scripts/export_yolo_preds_for_audit.py \
  --dataset datasets/dense_15m_full --split val --conf 0.30

# 2) 导入 GT + 预测 + mistakenness，打开 App
/Users/zhangzc/fable-trading-codex/.venv_yolo_tools/bin/python \
  scripts/fiftyone_label_audit.py --split val \
  --preds datasets/dense_15m_full/preds_val_conf30 \
  --launch --port 5151
```

浏览器打开：**http://127.0.0.1:5151**

### App 里怎么操作

1. 左侧字段打开 `ground_truth`（绿/GT）和 `predictions`（模型）
2. 若有 `mistakenness`：按该字段 **降序排序** → 顶部最可能「标错或难学」
3. 保存的视图：`has_gt` / `background`
4. 记下图名（文件名 stem），丢回对话做单变量改规则

仅看 GT、不跑模型时：

```bash
.../python scripts/fiftyone_label_audit.py --split val --launch
```

---

## B. Label Studio（可视化改框）

### 启动（Docker，端口 **8081**，避开本机 8080）

```bash
cd ~/fable-trading
mkdir -p label_studio_data
docker compose -f scripts/label_studio_compose.yml up -d
```

打开：**http://127.0.0.1:8081**  
首次自己注册本地账号（只存在 docker volume，不进 git）。

### 导入抽样任务

```bash
python3 scripts/label_studio_prepare_import.py --split val --limit 80 --seed 20260709 --stratify
```

产物：

- `output/label_studio/tasks_val.json`
- `output/label_studio/label_config.xml`

在 LS 中：

1. Create Project  
2. Settings → Labeling Interface → 粘贴 `label_config.xml`  
3. Import → 上传 `tasks_val.json`  
4. 逐张：接受 / 拖动 / 删除 / 新增 `dense_cluster` 框  

预标来自 **当前磁盘 GT（E2）**，不是让你从零画。

### 和 auto_label 的关系

- LS 里改框 = **你的意见**  
- **不会**自动写回 `datasets/.../labels` 或改 `auto_label.py`  
- 你导出或记下图名后，再决定：改规则（单变量）还是人工硬标进数据集（另开实验）

---

## 约束（别踩）

| 禁止 | 原因 |
|------|------|
| 为 mAP 放宽 conf/IoU 定义 | 铁律 |
| 开 flip/mosaic 增强 | 铁律 |
| 用审查结果直接宣称验收过 0.90 | 须正式训练评估 |
| 把 LS/FO 装进主 `.venv` | 污染训练环境 |

---

## 故障排查

| 现象 | 处理 |
|------|------|
| FiftyOne 打不开 | 查 5151 端口；看终端 traceback |
| mistakenness 失败 | 仍可浏览 GT；检查 `--preds` 路径是否含 `labels/val/*.txt` |
| Label Studio 8081 冲突 | 改 compose 端口映射 |
| 图片空白 | 确认 compose 挂载了 `datasets/dense_15m_full`；URL 含 `dense_15m_full/images/...` |
| 想停服务 | `docker compose -f scripts/label_studio_compose.yml down`；FiftyOne 终端 Ctrl+C |

---

## 与现有静态页的关系

| 入口 | 用途 |
|------|------|
| `/label_audit.html` | 固定 18 张 seed 快照 |
| `/label_audit_e2_compare.html` | E2 红绿对照 |
| **FiftyOne :5151** | 全 split 可筛选、可排序 |
| **Label Studio :8081** | 交互改框 |
