# 本机调试工具（不抢 v13 / 不进 VPS 脉冲）

**日期**：2026-07-22 · 配合 `analysis/p_wuzao_topics_scan.md` A 档 + 旁路落地报告  
**落地总表**：[`analysis/p_side_tools_landed.md`](../analysis/p_side_tools_landed.md)

训练占着 MPS 时，只做 **CPU / 旁路监视**。不要在此文档指引下开大 YOLO 推理。

**Side venv（勿污染训练 `.venv`）**：

| venv | 装什么 | 勿装 |
|------|--------|------|
| `.venv-tools` | supervision / nvitop / mitmproxy / marimo / ydata-profiling | FiftyOne（依赖掐架） |
| `.venv-fo` | FiftyOne + opencv-headless | 训练权重相关 |
| `.venv` | ultralytics 训练 | 旁路 CV/监控新包 |

```bash
# 一次性重建旁路环境（示例）
python3 -m venv .venv-tools
.venv-tools/bin/pip install supervision nvitop mitmproxy marimo 'ydata-profiling' \
  pandas opencv-python-headless 'matplotlib>=3.5,<3.8'
python3 -m venv .venv-fo
.venv-fo/bin/pip install fiftyone opencv-python-headless
```

---

## 本机看板 :8642（launchd 常驻）

**为何会拒连**：在 Cursor agent 后台 shell 里起的 `uvicorn` 会被会话收割（日志常见优雅 `Shutting down` + `exit_code: unknown`），不是业务 crash。详见 `docs/learnings/cursor-agent-shell-kills-background-servers.md`。

**启动 / 重载**（user launchd，不抢 MPS，不影响 v13）：

```bash
bash scripts/webapp_start.sh
```

**状态检查**：

```bash
bash scripts/webapp_status.sh
# 或
lsof -nP -iTCP:8642 -sTCP:LISTEN
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8642/
```

- URL：http://127.0.0.1:8642/#explore
- Label：`com.fable.local-webapp`
- 日志：`logs/local_webapp.log` / `logs/local_webapp.err.log`
- 停止：`launchctl bootout gui/$(id -u)/com.fable.local-webapp`

勿在 Cursor agent 终端里裸跑长驻 uvicorn；要临时前台调试可开独立 Terminal.app。

---

## nvitop（本机 GPU/MPS 旁路监视）

macOS 无 NVIDIA 时，`nvitop` 对 CUDA 用处有限；Apple Silicon 仍可用系统监视 + 进程 RSS。

```bash
.venv-tools/bin/nvitop --version
# 一次性快照（不要 nohup 常驻干扰训练）
.venv-tools/bin/nvitop -1
# 或只看本仓训练进程
bash scripts/v13_train_status.sh
pgrep -lf 'src.detection.train|owner_v13'
ps -p $(pgrep -f 'src.detection.train.*owner_v13' | head -1) -o pid,etime,%cpu,rss,command
```

可选 alias（放 `~/.zshrc`；GPU 监视别名不要挂 launchd——看板服务才用 launchd，见上文）：

```bash
alias fable-gpu='pgrep -lf "src.detection.train|owner_v13"; ps -p $(pgrep -f "src.detection.train" | head -1) -o etime,rss,%mem 2>/dev/null'
```

---

## supervision 叠框（hardneg）

```bash
PYTHONPATH=. .venv-tools/bin/python scripts/overlay_hardneg_boxes.py \
  --prefer-supervision --out-dir analysis/output/hardneg_overlay_supervision
# 打开 analysis/output/hardneg_overlay_supervision/index.html
```

无 supervision 时脚本自动回退 matplotlib（昨夜画廊路径仍可用）。

---

## FiftyOne（难例策展 · 独立 `.venv-fo`）

```bash
.venv-fo/bin/python scripts/fiftyone_hardneg_browse.py --limit 12
# 可选开 App（本机，不抢 MPS）：
.venv-fo/bin/python scripts/fiftyone_hardneg_browse.py --limit 12 --launch --port 5152
```

若 FO 过重：用 LS 小包代替（下一节）。历史说明见 `output/offline_tasks/FIFTYONE_ACCESS.md`。

---

## Label Studio hardneg 小包

```bash
python3 scripts/ls_hardneg_import_check.py
# Owner 真要开 UI 时再：
# docker compose -f scripts/label_studio_compose.yml up -d
# 见脚本打印的 import 命令 / output/label_studio/tasks_hardneg_discovery_README.md
```

---

## ydata-profiling（判断 CSV 卫生）

```bash
# 已生成发现级报告（未切 holdout）：
open analysis/output/profiling/judgment_v2_strict_profile.html
# meta: analysis/output/profiling/judgment_v2_strict_profile_meta.json
```

重跑（CPU，side venv）：

```bash
.venv-tools/bin/python - <<'PY'
from pathlib import Path
import pandas as pd
from ydata_profiling import ProfileReport
src = Path("data/judgment_dataset_v2_strict.csv")
df = pd.read_csv(src)
keep = [c for c in df.columns if c not in ("source", "symbol", "outcome", "signal_time")]
ProfileReport(df[keep], title="judgment_v2_strict", minimal=True).to_file(
    "analysis/output/profiling/judgment_v2_strict_profile.html"
)
PY
```

---

## marimo（forward / judgment 切片）

```bash
.venv-tools/bin/marimo edit analysis/notebooks/forward_log_browse.py
# 或
.venv-tools/bin/marimo run analysis/notebooks/forward_log_browse.py
```

---

## mitmproxy（本机只读看 OKX · 安全）

```bash
.venv-tools/bin/mitmdump --version
# 仅本机观察：例如把 fetch 脚本的代理指到 127.0.0.1:8080 后
# .venv-tools/bin/mitmweb --listen-host 127.0.0.1 --listen-port 8080
```

**禁止**：

- 对着 **LIVE** API key 做中间人改包 / 重放下单
- 在 VPS 脉冲路径挂代理
- 把抓到的密钥写进 git

只读调试 demo/本地 fetch 形态可以；真金操作仍只允许 owner 亲手或逐次授权。

---

## netron（看 ONNX 结构）

本仓训练中勿 export（export 可能碰 MPS）。**等 v13 落盘后**再：

```bash
PYTHONPATH=. OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python - <<'PY'
from ultralytics import YOLO
m = YOLO("models/owner_v13_pad200.pt")  # 或 owner_best.pt
m.export(format="onnx", imgsz=960, device="cpu", simplify=True)
print("wrote onnx next to weights")
PY

python3 -m pip install --user netron
netron models/owner_v13_pad200.onnx
```

若 export 仍试图占 MPS：确认 `device="cpu"`，或干脆跳过到 tip-smoke 之后再做。

---

## 只读规格（无安装）

| 文档 | 内容 |
|------|------|
| [`docs/refs/ML4T_walkforward_notes.md`](refs/ML4T_walkforward_notes.md) | 时间切分 / 无前视检查清单 |
| [`docs/refs/event_semantics_lean_vnpy.md`](refs/event_semantics_lean_vnpy.md) | LEAN/vnpy vs forward/executor 事件边界 |
| [`docs/EXEC_PROTECTIONS_SPEC.md`](EXEC_PROTECTIONS_SPEC.md) | Freqtrade Protections → 本仓（数字待批） |

---

## 相关离线产物

| 工具 | 命令 | 产出 |
|------|------|------|
| LWC hardneg 批量 | `scripts/build_hardneg_lwc_batch.py` | `analysis/output/wuzao_lwc_hardneg_batch/index.html` |
| 叠框画廊（mpl） | `scripts/overlay_hardneg_boxes.py` | `analysis/output/hardneg_overlay_gallery/index.html` |
| 叠框（supervision） | 同上 `--prefer-supervision` | `analysis/output/hardneg_overlay_supervision/` |
| LS 小包 | `scripts/hardneg_to_labelstudio.py` | `output/label_studio/tasks_hardneg_discovery.json` |
| Profiling | ydata（上） | `analysis/output/profiling/` |
| Protections 规格 | — | `docs/EXEC_PROTECTIONS_SPEC.md` |

---

## 待 Owner 批 · VPS 可观测（本夜**不装**）

见 `docs/ops/VPS_OBSERVABILITY_PENDING.md`。
