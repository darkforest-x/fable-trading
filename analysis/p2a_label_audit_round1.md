# P2-11 YOLO Label Audit Round 1

日期：2026-07-09

## 目的

P2-11 的第一步是先审标签质量，再决定是否修改 `src/detection/auto_label.py`
或进入 hard-negative / 分辨率 / 模型大小实验。本轮只生成 owner 人工审计样本页，
不改规则阈值、不训练、不评估 holdout。

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python scripts/label_audit.py --seed 20260709
python3 -m uvicorn src.webapp.server:app --host 127.0.0.1 --port 8643
```

审计入口：`http://127.0.0.1:8643/label_audit.html`

## 样本统计

| 项 | 数值 |
|---|---:|
| dataset | dense_15m_full |
| seed | 20260709 |
| split | val + train |
| images | 18 |
| val with boxes | 6 |
| val background | 3 |
| train with boxes | 6 |
| train background | 3 |

## 样本清单

| split | image | 当前标签 | owner 审计结论 |
|---|---|---|---|
| val | PI_USDT_017060 | 2 框 | 待看 |
| val | LTC_USDT_016460 | 1 框 | 待看 |
| val | XLM_USDT_014760 | 2 框 | 待看 |
| val | PAXG_USDT_015960 | 2 框 | 待看 |
| val | XRP_USDT_016760 | 1 框 | 待看 |
| val | ALLO_USDT_014860 | 2 框 | 待看 |
| val | SPACE_USDT_012660 | 背景图（规则判定：无密集） | 待看 |
| val | NEAR_USDT_015460 | 背景图（规则判定：无密集） | 待看 |
| val | CHZ_USDT_014860 | 背景图（规则判定：无密集） | 待看 |
| train | BTC_USDT_008460 | 2 框 | 待看 |
| train | ADA_USDT_006060 | 3 框 | 待看 |
| train | BNB_USDT_005560 | 1 框 | 待看 |
| train | SUI_USDT_012660 | 2 框 | 待看 |
| train | ICP_USDT_000760 | 2 框 | 待看 |
| train | BNB_USDT_011660 | 2 框 | 待看 |
| train | BTC_USDT_005760 | 背景图（规则判定：无密集） | 待看 |
| train | WLFI_USDT_008860 | 背景图（规则判定：无密集） | 待看 |
| train | LTC_USDT_002660 | 背景图（规则判定：无密集） | 待看 |

## QA 记录

- `python3 -m compileall scripts/label_audit.py` 通过。
- Playwright desktop 1280x900：18 张图，横向溢出 0。
- Playwright mobile 390x844：18 张图，横向溢出 0，seed `20260709` 可见。
- 截图证据保存在本地运行产物 `output/playwright/p2-11-label-audit-*.png`，不入 git。

## 风险与诚实声明

- 本轮还没有 owner 人工审计结论，因此不能进入规则修正或训练。
- 抽样页只覆盖 18 张图，是标签质量抽查，不代表全量统计。
- 阈值、框合并/分裂参数、增强开关均未改动。

## 下一步

owner 人工打开 `/label_audit.html`，按图名记录三类问题：
漏标、有框但不密集、框形不贴。收到问题图名后，才能进入 P2-11 第 2 步规则修正。
