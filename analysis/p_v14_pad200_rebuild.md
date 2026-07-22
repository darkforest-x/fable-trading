# v14 pad200 重建（MAD-on）— 2026-07-22

> Owner 批准重建。**未**覆盖 v13、**未**开训、**未** promote、**未**耗 holdout。  
> Windows 开训交接：`analysis/p_v14_windows_train.md`。

## 复现命令

```bash
# MAD 默认 ON；勿传 --no-mad-gate。v13 目录保留。
PYTHONPATH=. OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  caffeinate -i .venv/bin/python -u scripts/build_crop_pad200_dataset.py \
  --src datasets/dense_owner_v11 \
  --out datasets/dense_owner_v14_pad200 \
  --resume   # 中途 jetsam 可续跑；watchdog: scripts/watch_v14_pad200_build.sh

# 快速抽查
PYTHONPATH=. .venv/bin/python scripts/make_v14_pad200_sample.py --n 20
open analysis/output/v14_train_sample20/index.html
```

日志：`logs/build_v14_pad200.log`。摘要：`datasets/dense_owner_v14_pad200/pad200_summary.json`。

## 数据统计

| 项 | v14（本集） | v13（对照，保留） |
|----|-------------|-------------------|
| mad_gate | **true** | false（毒因） |
| win_index | mad_vs_stored_png | 盲 end_incl |
| train 正样本 pad200 | **2635** | 3947 |
| train skip | **1406** | 94 |
| train bg | 4520 | 4520 |
| val（原样拷贝） | 3169 | 3169 |
| 体积 | **~1.6 GB** | ~1.8 GB |
| 右缘 ≥0.95 | **2635/2635（100%）** | （错窗集勿比语义） |
| 其中 okx_* 正样本 | 1227 | ~1228（多数错窗） |

### Skip 按原因（v14）

| 原因 | 数 | 含义 |
|------|-----|------|
| `mad_fail_both_high` | **1318** | 存档 PNG 与当前 kline 候选窗 MAD>5（漂移币 both_high → **skip**，审计建议） |
| `other` | 82 | `process_returned_none`（缺序列 / 无候选窗 / eval stem 等） |
| `short_history` | 6 | 切点左侧不够 200 bar |
| `okx_blind_end_incl` | **0** | MAD 开着时不会走盲切；守卫仍保留 |

正样本相对 v13 少 ~1312，几乎全是 MAD 漂移 skip——**宁缺勿毒**，不是漏跑。

## 抽查

- 目录：`analysis/output/v14_train_sample20/`（20 张，含 6 张 okx_*）
- 目视：绿框贴右、黄线 x=0.95；抽查未见 v13 式「框罩错 K 线」
- **下一步重训须 Owner 再点头**（本报告不自动开训；Mac 不训，交给 Windows）

## 与 v13 对比（一句话）

v13 关 MAD 盲 `end_incl` → ~31% okx 错窗；v14 MAD-on + both_high skip → 正样本变少但窗对齐。

## 风险与诚实声明

- 16GB Mac 上 MAD bulk 曾 jetsam；靠 `--resume` + watchdog 跑完，非单次无故障。
- `other=82` 未逐条人工审；不影响「可训」但若某币全灭需另查 kline。
- 抽查 20 张不能证明 2635 全无错窗；系统性保障靠 MAD 门，不是抽样。
- 右缘 100%≥0.95 是 pad200 协议期望（切在框右缘），不代表 tip 可学成。

## 下一步（Owner 决策）

1. **拷** `datasets/dense_owner_v14_pad200`（~1.6G）+ `models/owner_v12_htip.pt` 到 Windows  
2. **`git pull`** 拿训练脚本/文档（datasets 不在 git）  
3. Windows 跑 `analysis/p_v14_windows_train.md` §2 或 `scripts/train_v14_pad200_windows.ps1`  
4. 权重拷回后 tip-smoke / tip_hit；**promote 另批**
