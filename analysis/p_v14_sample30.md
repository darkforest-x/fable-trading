# v14 pad200 抽检 30 张 + okx 错窗小样 — 2026-07-22

**结论：可以放心 sync 去 Windows。** `mad_gate=true`；okx 错窗抽检 **0**；未见 v13 式残留错框。未 sync、未开训、未 promote。

## 摘要核对

来源：`datasets/dense_owner_v14_pad200/pad200_summary.json`

| 项 | 值 |
|----|-----|
| mad_gate | **true** |
| train 正样本 pad200 | **2635** |
| train skip | **1406**（both_high 1318 / other 82 / short_history 6） |
| train bg | 4520 |
| val（原样） | 3169 |
| okx_* 正样本 | 1227 |

## 目视 30 张

```bash
open analysis/output/v14_train_sample30/index.html
# 或: PYTHONPATH=. .venv/bin/python scripts/make_v14_pad200_sample.py --n 30 --out analysis/output/v14_train_sample30 --seed 30
```

- 分层：10 okx + 20 非 okx；绿框=GT，黄线 tip x=0.95
- sample 右缘 ≥0.95：**30/30**

## okx 错窗小样（与 cut audit 同逻辑）

对 v14 池内 **60** 条随机 okx_*（seed=42）：

| 检查 | 结果 |
|------|------|
| BUG_wrong_window | **0** |
| 磁盘标签 vs MAD 重算 | **60/60 一致** |
| win_mode=start | **100%** |
| both_high 漏进正样本 | **0** |

明细：`analysis/output/v14_train_sample30/okx_wrong_window_audit.json`。

## 风险与诚实声明

- 60 条错窗抽检 ≠ 1227 全量 MAD；系统性保障仍靠构建时 `mad_gate` + both_high skip。
- 30 张目视不能证明 tip 可学；只证明「框在对的窗上」。

## 下一步（Owner）

1. 目视 `index.html` 无异议 → `bash scripts/sync_v14_to_windows.sh`
2. Windows 开训见 `analysis/p_v14_windows_train.md`（另批点头）
