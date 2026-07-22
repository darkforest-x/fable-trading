# 本机旁路工具集落地 — 发现级收尾

**日期**：2026-07-22  
**约束**：不杀 v13、不抢 MPS、不 promote、不耗 holdout、不装 VPS、不污染训练 `.venv`  
**索引**：[`docs/LOCAL_DEBUG_TOOLS.md`](../docs/LOCAL_DEBUG_TOOLS.md)

前一 agent（490633fe）PING 超时；本报告为核对现状后的收尾账本。

---

## 总表

| # | 项 | 状态 | 命令 / 产出 | 备注 |
|---|----|------|-------------|------|
| 1 | **supervision** | ✅ | `.venv-tools` + `scripts/overlay_hardneg_boxes.py --prefer-supervision` → `analysis/output/hardneg_overlay_supervision/`（10 张 annotated + index） | 修 API：`class_id` + `ColorLookup.INDEX` |
| 2 | **FiftyOne** | ✅（独立 venv） | `.venv-fo` + `scripts/fiftyone_hardneg_browse.py --limit 12` → dataset `fable_hardneg_discovery` | **未**塞进 `.venv-tools`（与 mitm/ydata 掐架）；App 可选 `--launch` |
| 3 | **LS** | ✅ | `python3 scripts/ls_hardneg_import_check.py` → OK n=24 | 不强制起 UI；import 命令由脚本打印 |
| 4 | **nvitop** | ✅ | `.venv-tools/bin/nvitop --version` → 1.7.1 | macOS 无 CUDA 时配合 `v13_train_status.sh` |
| 5 | **ML4T 只读** | ✅ | `docs/refs/ML4T_walkforward_notes.md` | **未** clone 巨仓 |
| 6 | **ydata-profiling** | ✅ | `analysis/output/profiling/judgment_v2_strict_profile.html` | 全量 strict CSV、minimal；**未**切 holdout |
| 7 | **LEAN/vnpy 对照** | ✅ | `docs/refs/event_semantics_lean_vnpy.md` | **未**安装全家桶 |
| 8 | **mitmproxy** | ✅ | `.venv-tools/bin/mitmdump --version` → 9.0.1 | 安全说明已写入 LOCAL_DEBUG_TOOLS |
| 9 | **marimo** | ✅ | `analysis/notebooks/forward_log_browse.py` | `.venv-tools/bin/marimo edit …` |

⏭ / 故意轻量跳过：

| 项 | 原因 |
|----|------|
| Netron / ONNX export | 训中 export 可能碰 MPS；等 v13 落盘 |
| VPS Kuma/Grafana/Loki | 需 owner 批；见 `VPS_OBSERVABILITY_PENDING.md` |
| Protections 上线数字 | 规格已有；阈值要 tip 样本 + owner |
| FiftyOne 常驻 App / launchd | 偏重；按需 `--launch` |
| ML4T submodule | 体积与 tip 无关；清单够用 |

---

## Side venv 布局

| 路径 | 用途 | git |
|------|------|-----|
| `.venv-tools/` | supervision, nvitop, mitmproxy, marimo, ydata-profiling | ignore |
| `.venv-fo/` | FiftyOne | ignore |
| `.venv/` | 训练（ultralytics） | ignore |

学习笔记：旁路工具勿进忙训 venv；FO 再拆一房（见 `docs/learnings/avoid-pip-into-busy-train-venv.md` 续）。

---

## 复现命令（从零核验）

```bash
# 1 supervision 叠框
PYTHONPATH=. .venv-tools/bin/python scripts/overlay_hardneg_boxes.py \
  --prefer-supervision --out-dir analysis/output/hardneg_overlay_supervision

# 2 FiftyOne 小批（无 App）
.venv-fo/bin/python scripts/fiftyone_hardneg_browse.py --limit 12

# 3 LS 包校验
python3 scripts/ls_hardneg_import_check.py

# 4–5 版本
.venv-tools/bin/nvitop --version
.venv-tools/bin/mitmdump --version
.venv-tools/bin/marimo --version

# 6 marimo
.venv-tools/bin/marimo run analysis/notebooks/forward_log_browse.py

# 7 只读文档
ls docs/refs/ML4T_walkforward_notes.md docs/refs/event_semantics_lean_vnpy.md
```

---

## 风险与诚实声明

- 旁路工具**不抬 tip_fire**；主线仍是 v13 → tip-smoke。
- profiling 是发现级卫生报告，不是验收；未评估 holdout。
- mitmproxy 若对着 LIVE key 改包 = 违纪；文档已标红。
- `.venv-tools` / `.venv-fo` 体积大，仅本机；不入 git、不 rsync VPS。
- FiftyOne persistent dataset 在用户本地 fo 库，换机需重跑 browse 脚本。

---

## 下一步（需 owner 决策的标出）

1. （可选）Owner 本机开 LS UI 真审 24 条 hardneg — **不**自动进训。
2. （可选）v13 落盘后 Netron export — **你批**后再做。
3. （排队）VPS 可观测 / Protections 数字 — **必须批**。
4. 不自动 promote；不 clear forward_log。
