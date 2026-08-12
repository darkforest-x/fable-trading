# Local Signal V2 Positive 语义纯度审计 PRE-REVIEW（2026-08-12）

## 直接结论

本轮 `DATA / SEMANTIC AUDIT ONLY` 的200张 Owner YES / NO / SKIP审核包已经完成，等待Owner人工审核。

- Positive Gold Pool：从当前R2使用的1,345个Owner-short positive中分层抽取100个；
- Canary Candidate：从最新独立Canary的163个R1/R2共同保留、32个R2新生、60个R1抑制事件中，按50/25/25抽取100个；
- 200个事件、200个独立PNG、200个唯一图片SHA；没有montage冒充逐样本审核；
- `visible_end_bar == decision_bar` 为200/200，`future_bars == 0`为200/200，因果审计PASS；
- Owner verdict预选0，训练资格0，holdout读取0；
- UI只显示盲化后的review id、symbol、因果图、橙色候选框和蓝色decision边界；不显示来源、置信度、未来K、未来收益、TP/SL或推荐答案。

这是审核前报告，**没有也不能给出Positive purity或Canary precision结论**。没有训练R3/R4，没有修改任何权重、conf、NMS、窗口、positive标签、ACTIVE或forward log。

## 1. 冻结状态

| 项目 | 冻结值 |
|---|---|
| 审计开始前main | `b134f211b4300d7d95679bebd5c50dc3b23d0789` |
| 正式builder commit | `1ff891adbe3f39c71a12f1bab42c35a8531331d8` |
| Stage A best | `c0e94f47df125e298b044d9f10acd0b8e4f525ccd6143ce34f8d174af802bf1a` |
| Stage B cold best | `de80173ed05962d70bb19ae50539ff08309579a9cae205403c4645e88b13b362` |
| Positive baseline best | `da278820f2d96a64006d9ff6358b7c98faec52249ec8a6f4fe6bf55254fc65b4` |
| R1 best | `029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537` |
| R2 best | `52cd38fda253f052c3c8eb712d93557c0125dceb336fb4cd58136236dca32afe` |
| R2 positive manifest | `dc82541c8404468d7b0dc38424535b478bbb79f14106690690870b38e1318492` |
| R1 Canary events | `72533957f18b017c18cdf913e8d609e56b56a439ba87c70fdbf290cb5e221dc0` |
| R2 Canary events | `d61c91e30a7ac4aa3b358088fc863aacc6ff41c7063b27ecfa229e445a4050b5` |
| Canary comparison | `5b8c1798f88e732cadd145a280ee5bbb363777bff09b75b94fa48ac0b8a9ffa4` |
| Canary conf / NMS / event gap | `0.25 / 0.70 / ±5 bars` |
| Canary window lengths | `W12–19`，未修改 |
| Canary最大物理时间 | `2026-05-03 23:45 UTC` |
| holdout边界 / 本轮消耗 | `2026-05-04 00:00 UTC / 0` |
| ACTIVE指针SHA | `899c36259950a3d376067958ec040638253defa9ef545fa51af2a004f95bb6ef` |
| ACTIVE值 | `models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.txt`，未修改 |

完整机器回执为 `analysis/output/local_signal_v2_positive_semantic_review200_v1/freeze_receipt.json`。

## 2. Positive Gold Pool抽样

母池是当前R2冻结positive manifest的全部1,345个positive，不是旧70/176标杆注册子集。固定seed为`20260812`。

为覆盖不同模型置信度，冻结R2只读扫描全部1,345张既有positive图片，使用`conf floor=0.001、NMS=0.70、imgsz=960`取得内部最高置信度；该分数只用于低/中/高分层，不选择新operating threshold，也不改变任何图片或标签。波动只用每个样本`decision_bar`及以前的OHLC计算。

| Positive维度 | 100张入选分布 |
|---|---|
| symbol | 100个，单币1张 |
| split | train 77 / val 23 |
| 时间 | early 28 / middle 22 / late 50 |
| 框位置（母池三分位） | low 22 / mid 38 / high 40 |
| R2置信度 | low 38 / mid 27 / high 35 |
| 因果窗口波动（母池三分位） | low 35 / mid 38 / high 27 |
| 核心宽度 | 4根23 / 5根17 / 6根22 / 7根38 |

抽样是对split、时间、位置、置信度、波动、核心宽度做确定性轮转，并设置symbol cap；禁止只挑最好看的样本。位置/波动使用冻结母池三分位，因为旧绝对阈值会把窄局部分布错误压进单一桶。

## 3. Canary Candidate抽样

复用`compare_owner_short_canary.py`的冻结一对一配对规则：同币且核心中点距离不超过5根，优先最小核心距离，再最小decision距离。机器重新分解得到163共同保留、32 R2新生、60 R1抑制，与最新Canary报告完全一致。

| Canary内部来源（UI隐藏） | 母池 | 入选 |
|---|---:|---:|
| R1/R2 common retained | 163 | 50 |
| R2 new | 32 | 25 |
| R1 suppressed | 60 | 25 |
| 合计 | 255 | 100 |

入选100张覆盖78个币；置信度low/mid/high为40/33/27，时间early/middle/late为34/33/33，因果窗口波动low/mid/high为33/31/36。Owner UI中不会显示上述来源和分层，只有完成200张审核后，汇总器才按内部字段解盲。

## 4. 因果与图片门

| 检查 | 结果 |
|---|---:|
| review manifest行数 | 200 |
| Positive / Canary | 100 / 100 |
| 独立PNG文件 | 200 |
| 唯一event_id | 200 |
| 唯一image SHA | 200 |
| `visible_end_bar == decision_bar` | 200 / 200 |
| `future_bars == 0` | 200 / 200 |
| 图片存在且SHA匹配 | 200 / 200 |
| Owner verdict预选 | 0 |
| training eligible | 0 |
| holdout读取 | 0 |

橙框是当前positive标签或当前模型候选框的审核叠加层；蓝线标记图中最右侧decision bar。蓝线右侧没有真实K线，也没有用空白伪装未来。抽查与浏览器实测均确认图中包含真实K线和当前SMA/EMA 20/60/120渲染。

## 5. 审核界面

启动命令：

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:/Users/zhangzc/yoyo-trading \
  .venv/bin/python scripts/serve_local_signal_v2_semantic_review.py --port 8766
```

打开 `http://127.0.0.1:8766/`。快捷键：`Y=YES`、`N=NO`、`S=SKIP`、`←/→`前后移动。每次点击或按键都立即append到：

`analysis/output/local_signal_v2_positive_semantic_review200_v1/owner_verdicts.jsonl`

同一review id可再次判断，append-only日志保留历史，读取时最后一次判断生效。已用临时副本实测：Y后自动前进、刷新后恢复、上一张改成N、S保存、左右键导航均通过；正式目录仍为0判断。

## 6. 可复现性与关键输出SHA

正式代码提交后独立构建两次，review manifest SHA和image tree SHA逐字节一致：

| 输出 | SHA-256 |
|---|---|
| review manifest | `71cf3ace90fefce0ad8364fea90463d4d902795718c675b5898fd1d75b8f582a` |
| 200图内容树 | `458c06b949232b534b555f35233f6822f22a6da8088ca25c5cf68ef3fbc42480` |
| sampling audit | `e880cc49404ca1c6a4d8dd7030c728c72ed80b4939bd75a1aeea090ac6edbad9` |
| causality audit | `cafc9847765caafd45f55cb51d42809780364b07e857dc73318c09c17c901336` |
| freeze receipt | `183b14ac22ef50bfce6750a6aa8ff952abcda64fbfd4755fa72b2a8ee7c48d78` |
| review UI HTML | `bcf1a217b9b291902ed956308a60b5875d132d0773fde13ec8a68d6d0fea60c0` |
| README | `e1881e1761237f727e7ab9af932033bc02f45e9e5a031a98f288e99a7be9bf7b` |

复现命令：

```bash
PYTHONPATH=.:/Users/zhangzc/yoyo-trading \
  .venv/bin/python scripts/build_local_signal_v2_semantic_review.py \
  --device mps --batch 16 \
  --frozen-main-commit b134f211b4300d7d95679bebd5c50dc3b23d0789

PYTHONPATH=.:/Users/zhangzc/yoyo-trading \
  .venv/bin/pytest -q \
  tests/test_build_local_signal_v2_semantic_review.py \
  tests/test_serve_local_signal_v2_semantic_review.py
```

builder拒绝覆盖非空正式目录，防止误删Owner裁决。复现应使用新的`--out`目录，并按sample/event/image SHA逐轴比较。

## 7. 指标状态

| 指标 | PRE-REVIEW状态 |
|---|---|
| Positive purity `YES/(YES+NO)` | N/A，等待Owner完成100张Positive |
| Canary candidate precision estimate | N/A，等待Owner完成100张Canary |
| common / R2-new / R1-suppressed YES rate | N/A，完成后解盲 |
| YOLO val P/R/mAP | N/A，本轮禁止训练和模型裁决 |
| val AUC / 置换检验p / top-decile净收益 / 胜率 | N/A，本轮不是L2或收益实验 |
| 单特征基线 / 匹配随机入场对照 | N/A，本轮不产生交易结论 |

## 8. 风险与诚实声明

- Positive 100是分层样本，purity是对当前1,345母池的诊断估计，不是对所有历史⭐标杆或全市场频率的精确普查。
- Canary 100是按50/25/25人为提高R2-new和R1-suppressed覆盖的诊断样本；总YES率不能不加权外推为255事件或398 events/day的总体精度。
- Owner只判断“当时是否为目标SHORT启动前沿”。后续真实暴跌不能作为判断依据，因为本轮主图根本不显示未来。
- 本报告不提前判定A/B/C哪种情况成立；必须等200张全部完成后运行只读汇总器。
- 本轮没有训练、R3/R4、conf/NMS/窗口/positive修改、promote、ACTIVE、部署、TG、下单、forward log清理或holdout读取。

## 9. 停止条件与下一步

本轮停止条件已满足：200图、因果全绿、UI可用、测试通过、PRE-REVIEW报告完成。现在只等待Owner审核。

Owner完成200张后运行：

```bash
PYTHONPATH=.:/Users/zhangzc/yoyo-trading \
  .venv/bin/python scripts/summarize_local_signal_v2_semantic_review.py
```

汇总器只生成Positive purity、Canary estimate及三个内部来源YES率，不自动训练。根据结果再由Owner决定进入高纯Positive Gold Set重建、negative/decision-boundary研究，或重新评估目标信号自然频率。
