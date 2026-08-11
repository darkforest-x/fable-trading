# Local Signal V2 Stage B-from-A 数据验收报告（2026-08-11）

## 直接结论

Owner 已确认 Stage B 的严格因果布局，并确认使用 Stage A `best.pt` 初始化微调。独立数据版本
`local_signal_v2_stageb_from_stagea_v1` 已完成物化与二次复现，九道 P0 机械门全部通过，可以启动
Stage B 微调。

该数据不是重新启用历史失败方案：`causal_blank_w30_v3` 当时被否决的是“拿它替代 Stage A”，
因为框相对真实 K 线仍在右缘；Owner 本次确认的正是它在 **Stage B** 中应承担的角色。新版本不
修改旧目录或旧裁决，像素和标签逐字节复制后，额外写入 Stage B 训练角色与
`production_eligible=false`。

## 最终目标语义补充

Owner 同日以 2026-08-10 ETHUSDT.P 15m 截图指出最终目标：均线密集完成后，价格在密集带右缘
反抽或贴线失败，短均线刚开始向下张口，但主跌尚未完成的做空启动前沿。Owner 箭头是因果右
边界；箭头右侧连续大阴线只能解释为什么该信号完美，不得进入 YOLO 输入、训练样本或实时特征。

该截图仅归档为目标语义参考，不进入本轮训练或评分，不构成 holdout 消耗：
`analysis/reference/owner_ethusdt_15m_perfect_signal_20260810.png`，SHA-256
`6b028a1820e8ff6bd893c10e3b3521e641388474a1375609ac28b7f7f2a53b98`。

## Owner 授权与边界

- 已授权：真实 K 线止于 decision；正框位于真实内容右缘；右侧只允许 0–12 个布局空白槽；
  使用 Stage A `best.pt` 微调。
- 未授权：读取 holdout、修改阈值/成本/障碍、切换 ACTIVE、promote、部署或真下单。
- 训练角色：严格因果 Stage B curriculum；自家 val/mAP 仍不是生产晋升证据。
- 后续角色：训练完成后才可从新模型重新挖 hard negatives；最终仍须连续窗口密度与真 tip 验收。

## 数据合同与统计

| 项目 | 冻结值 |
|---|---:|
| 真实窗口 | 固定 30 根 15m K |
| confirmation delay | 1 或 2 bars |
| 框规则 | `anchor-2..decision` |
| 因果约束 | `visible_end == decision`，`future_bars=0` |
| 右侧空白 | 0–12 槽，仅画布布局，不是市场 bar |
| 目标框中心 X | 65%–95% 画布宽度 |
| split | 按时间最后 15% 为 val |
| train/val purge | 150 bars |
| holdout 起点 | 2026-05-04 00:00 UTC |
| dataset seed | 20260807 |
| 生产资格 | `false` |

| split | 正例 | easy negatives | 总数 | 正类率 | 完整样本时间范围（UTC） |
|---|---:|---:|---:|---:|---|
| train | 2,030 | 2,030 | 4,060 | 50.00% | 2025-06-02 13:30 — 2026-03-18 12:45 |
| val | 358 | 358 | 716 | 50.00% | 2026-03-19 22:45 — 2026-05-03 10:45 |
| 合计 | 2,388 | 2,388 | 4,776 | 50.00% | 全部早于 holdout |

## P0 审计与上一阶段对照

| 项目 | Stage A randomcrop v1 | **Stage B-from-A v1** | 解释 |
|---|---:|---:|---|
| 正例 / 负例 | 2,378 / 2,378 | **2,388 / 2,388** | Stage B 固定 W30 的合格事件数 |
| train / val 总数 | 4,040 / 716 | **4,060 / 716** | 时间切分与 150-bar purge |
| decision 后真实 K | 1–22 | **0** | 唯一课程阶段变化：恢复严格因果 |
| 真实内容内的框位置 | 20%–85% | **真实内容右缘** | Stage B 必须模拟盘口 tip |
| 画布内框中心 X | 不作为门 | **65.85%–94.83%** | 0–12 空白槽消除固定像素 X |
| 位置四桶 | Stage A 真实裁剪四桶 | **732 / 625 / 572 / 459** | Stage B 画布位置四桶均有覆盖 |
| holdout 样本 | 0 | **0** | 未读取 holdout |
| 生产资格 | false | **false** | 仍不得直接晋升 |

九道门全部通过：严格因果、box 不晚于 decision、event 不跨 split、正负时间切分、0 holdout、
标签在界内、4,776 image/label/manifest 守恒、100% 行情 bar 可追溯、因果画布位置覆盖合格。
正负样本均完整覆盖 0–12 空白槽；无未登记图片、无 image/label 数量不配对、无越界标签。

## 可复现性

同一代码提交后在同一输出目录完整物化两次，两个 manifest 均逐字节稳定：

- positive manifest SHA-256：`57918bf0155aac54f578a902b7eb7454125e0f8b665aa8ec00c7b9cb804af8aa`
- negative manifest SHA-256：`a9a8bd085b0bb8d96d8db127a594f50e54c2ec0aca45e188015e0fe3e58d7a5d`
- Stage A 初始化权重 SHA-256：`c0e94f47df125e298b044d9f10acd0b8e4f525ccd6143ce34f8d174af802bf1a`

物化器还逐张验证源/目标图片与标签 SHA；新数据的像素和标签与冻结源逐字节一致。summary 内的
`generated_at` 按设计变化，不参与复现 hash。

## 冻结训练配方

| 参数 | 值 |
|---|---:|
| 初始化 | Stage A `best.pt` |
| 模式 | 显式 `--finetune` |
| optimizer / lr0 | AdamW / 0.0001 |
| epochs / patience | 40 / 10 |
| imgsz / batch / seed | 960 / 8 / 0 |
| flip / mosaic / mixup / HSV | 全部 0 |

远端 wrapper 曾把任意 base 统一改名为 `yolo11s_w20.pt`，会让按文件名自动推断的训练器误判为
冷启动。本轮已增加显式 `--finetune` 传递与测试；启动日志必须出现 `finetune=True`、
`optimizer=AdamW`、`lr0=0.0001`，否则立即停止。

## 模型与回测指标

本报告是训练前 P0 数据验收，尚未产生 Stage B 模型。因此 val AUC、置换检验 p、top-decile
毛/净收益、胜率、单特征基线、匹配随机对照、YOLO mAP、event precision/recall 和 FP/1000
均为 **不适用**。这些值不能从 Stage A 或旧 B2 借用。训练后先做独立位置诊断与连续窗口触发
密度；判断层/交易回测只有在检测层通过后才有意义。

## 复现命令

```bash
# 合同测试和脚本静态检查
PYTHONPATH=.:../yoyo-trading .venv/bin/pytest -q \
  tests/test_materialize_local_signal_v2_stageb_from_stagea.py \
  tests/test_local_signal_v2_stageb.py
bash -n scripts/train_w20_midbox_on_3060.sh

# 独立物化与机械审计
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/materialize_local_signal_v2_stageb_from_stagea.py
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/audit_local_signal_v2.py \
  --dataset datasets/local_signal_v2_stageb_from_stagea_v1 \
  --out analysis/output/p0_local_signal_v2_stageb_from_stagea_v1_audit.json

# Owner 已确认后的冻结训练命令
bash scripts/train_w20_midbox_on_3060.sh \
  --host zzc@192.168.1.4 \
  --dataset datasets/local_signal_v2_stageb_from_stagea_v1 \
  --base analysis/output/lsv2_stagea/owner_lsv2_stagea_randomcrop_v1_cold/weights/best.pt \
  --name owner_lsv2_stageb_from_stagea_v1 \
  --epochs 40 --patience 10 --batch 8 --seed 0 --finetune

# HTML 交付物
python3 scripts/md_to_html.py \
  analysis/p0_local_signal_v2_stageb_from_stagea_v1_report_20260811.md \
  --out-dir analysis/html
```

## 风险与诚实声明

- 历史正例锚仍来自旧 pad200 事件语义；本轮只解决课程顺序、严格因果和位置 shortcut，不证明
  已经识别 owner 新给出的完美 break-frontier。
- 1:1 easy-negative 是平衡训练集，不代表连续市场先验；不能从 val precision 外推每日订单数。
- 右侧空白只是消除固定画布坐标 shortcut，不会凭空解决 easy-negative 高触发；必须靠训练后
  hard-negative mining 与连续窗口回放验证。
- Owner 的 ETH 完美信号截图属于 holdout 时段的目标参考，本轮未读取对应行情、未转成训练样本、
  未作为评估分母，也未据此调阈值。
- 本轮未 promote、未部署、未修改 ACTIVE、未下单。

## 下一步

立即按冻结配方在 RTX 3060 启动 Stage B 微调。训练完成后先取回 `best.pt` 和完整日志，核对
实际参数与早停轮次；随后用 pre-holdout 独立数据做位置分桶与连续窗口触发密度诊断。只有新模型
不再大量误触发，才重新开始 P2 hard-negative mining；真 tip 与生产晋升仍是后续独立 owner 门。
