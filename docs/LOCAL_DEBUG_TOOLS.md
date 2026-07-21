# 本机调试工具（不抢 v13 / 不进 VPS 脉冲）

**日期**：2026-07-22 · 配合 `analysis/p_wuzao_topics_scan.md` A 档。

训练占着 MPS 时，只做 **CPU / 旁路监视**。不要在此文档指引下开大 YOLO 推理。

---

## nvitop（本机 GPU/MPS 旁路监视）

macOS 无 NVIDIA 时，`nvitop` 对 CUDA 用处有限；Apple Silicon 仍可用系统监视 + 进程 RSS。

```bash
# 可选：用户级安装，勿塞进训练 .venv（避免和 ultralytics 抢依赖）
python3 -m pip install --user nvitop

# 一次性快照（不要 nohup 常驻干扰训练）
nvitop -1
# 或只看本仓训练进程
pgrep -lf 'src.detection.train|owner_v13'
ps -p $(pgrep -f 'src.detection.train.*owner_v13' | head -1) -o pid,etime,%cpu,rss,command
```

可选 alias（放 `~/.zshrc`，**不要**挂进 systemd/launchd）：

```bash
alias fable-gpu='pgrep -lf "src.detection.train|owner_v13"; ps -p $(pgrep -f "src.detection.train" | head -1) -o etime,rss,%mem 2>/dev/null'
```

---

## netron（看 ONNX 结构）

本仓训练中勿 export（export 可能碰 MPS）。**等 v13 落盘后**再：

```bash
# 一键命令（训完后；device=cpu 优先）
PYTHONPATH=. OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python - <<'PY'
from ultralytics import YOLO
m = YOLO("models/owner_v13_pad200.pt")  # 或 owner_best.pt
m.export(format="onnx", imgsz=960, device="cpu", simplify=True)
print("wrote onnx next to weights")
PY

# 看图（浏览器或桌面）
python3 -m pip install --user netron
netron models/owner_v13_pad200.onnx
# 或 https://netron.app 拖文件
```

若 export 仍试图占 MPS：加环境变量 / 确认 `device="cpu"`，或干脆跳过到 tip-smoke 之后再做。

---

## 相关离线产物（本夜）

| 工具 | 命令 | 产出 |
|------|------|------|
| LWC hardneg 批量 | `scripts/build_hardneg_lwc_batch.py` | `analysis/output/wuzao_lwc_hardneg_batch/index.html` |
| 叠框画廊 | `scripts/overlay_hardneg_boxes.py` | `analysis/output/hardneg_overlay_gallery/index.html` |
| LS 小包 | `scripts/hardneg_to_labelstudio.py` | `output/label_studio/tasks_hardneg_discovery.json` |
| Protections 规格 | — | `docs/EXEC_PROTECTIONS_SPEC.md` |

---

## 待 Owner 批 · VPS 可观测（本夜**不装**）

见 `docs/ops/VPS_OBSERVABILITY_PENDING.md`。
