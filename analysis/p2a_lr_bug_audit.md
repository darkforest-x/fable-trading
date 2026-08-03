# p2a — 学习率 bug 审计与 v8 重训

日期: 2026-07-16
一句话: `optimizer='auto'` 给续训用了从零训练的学习率(0.002),**本项目至今所有
chain 模型都在 epoch 3 被打飞**;修复后 v8_chain 达到 frozen-F1 0.650(历史最佳可信值),
但提升仅 +0.025 —— **配方不是主要瓶颈**,原先"加标注有效"的结论已撤回。

## 复现命令

```bash
# 1) 复现 bug:看任意 chain 训练的逐轮曲线
python3 - <<'PY'
import csv
for run in ("owner_v7_chain", "owner_v7_holdout"):
    rows = list(csv.DictReader(open(f"runs/detect/runs/detect/{run}/results.csv")))
    m = next(c for c in rows[0] if "mAP50(B)" in c)
    lr = next(c for c in rows[0] if "lr/pg0" in c)
    print(run)
    for x in rows[:4]:
        print(f"  epoch {x['epoch'].strip():>2} lr={float(x[lr]):.6f} mAP50={float(x[m]):.4f}")
PY

# 2) auto 的 lr 公式(nc=1)
python3 -c "print(round(0.002 * 5 / (4 + 1), 6))"   # -> 0.002

# 3) 修复后重训(3060,约 1h;Mac 上约 6h)
bash scripts/train_on_3060.sh --check
ssh zzc@192.168.1.5 '...python -u train_dense.py --name owner_v8_chain \
  --model base/owner_v7_chain.pt --dataset C:/fable/datasets/dense_owner_v7 \
  --epochs 40 --patience 10 --cache false --workers 4'

# 4) 干净尺子评估 + 提升
PYTHONPATH=. .venv/bin/python scripts/promote_owner_best.py
```

## 数据统计

| 数据集 | 图片 | 框 | 含 eval 币种 |
|---|---|---|---|
| dense_owner_v7(v8 两条都用它) | 5659(train 4245 / val 1414) | 2054 | **0 张 ✅** |
| dense_owner_v7h(A/B 用的) | 4604 | — | **596 张(12.9%)❌** |
| dense_owner_v5 及更早 | 3581 | — | **464 张(13%)❌** |
| owner_eval_frozen(尺子) | 768 | — | 47 币种,从未参训 |

golden_pool: 6501 张人工标注。时间范围与 15m bar 一致。

## 病因

`src/detection/train.py` 未传 optimizer/lr0 → ultralytics 默认 `optimizer='auto'`:

```
lr0 = round(0.002 * 5 / (4 + nc), 6)      # nc=1 → 0.002,并配 AdamW
warmup_epochs = 3.0
```

0.002 是**从零训练**的学习率。续训一个已收敛的检测器 = 灾难性遗忘。
warmup 需要 3 轮爬到位,所以**崩溃精确发生在 epoch 3**。

## 结果表(逐轮 lr vs mAP50,两次独立训练交叉验证)

| | owner_v7_chain | | owner_v7_holdout | |
|---|---|---|---|---|
| epoch | lr | mAP50 | lr | mAP50 |
| 1 | 0.000665 | **0.3832** ←最好 | 0.000665 | **0.7235** ←最好 |
| 2 | 0.001299 | 0.3432 | 0.001310 | 0.6175 |
| 3 | **0.001900** | **0.0000** ←崩 | **0.001933** | **0.0414** ←崩 |
| 4 | 0.001852 | 0.0004 | 0.001901 | 0.0036 |

**两次都在 lr 爬到 0.0019 的那一刻崩溃。** `best.pt` 均为 epoch 1 = 基础模型 + 一个预热轮次。

### 修复后(v8_chain,lr0=1e-4,warmup 0.5)

```
epoch 1  lr=0.000100  mAP50=0.3730
epoch 2  lr=0.000098  mAP50=0.3811
epoch 3  lr=0.000095  mAP50=0.3768   ← 不崩了
...
epoch 16 lr=0.000085  mAP50=0.3690   ← 早停;峰后崩溃 0/13
```

## 必报指标:frozen-eval(唯一尺子,47 币种从未参训)

| 模型 | 起点 | 训练集 | F1 | P | R | 判定 |
|---|---|---|---|---|---|---|
| v5_from_v4 | 续训 | v5(464张eval) | 0.663 | 0.758 | 0.590 | ❌ 泄漏,虚高 |
| **v8_chain** | 续训 | v7 干净 | **0.650** | 0.668 | 0.633 | ✅ **新最佳** |
| v5_coco | 冷启动 | v5(464张eval) | 0.641 | 0.567 | 0.738 | ❌ 泄漏,虚高 |
| v7_chain | 续训 | v7 干净 | 0.625 | 0.583 | 0.672 | 干净但=没训练 |
| v6_chain | 续训 | v6 干净 | 0.595 | 0.557 | 0.638 | 干净但受损 |
| v6_coco | 冷启动 | v6 干净 | 0.554 | 0.599 | 0.515 | ✅ 干净健康 |
| **v8_coco** | 冷启动 | v7 干净 | **0.549** | 0.617 | 0.493 | ✅ 冷启动补跑 |
| v7_coco | 冷启动 | v7 干净 | 0.311 | 0.321 | 0.301 | ep4 被杀,废 |

### 全量审计:bug 精确命中它理论上该命中的地方

| 类型 | 数量 | 峰后崩溃率 | 结论 |
|---|---|---|---|
| 冷启动(yolo11*.pt 起) | 6 | 0-4 / 26~46 轮 | ✅ **全部健康**(0.002 对冷启动本就正确) |
| 续训(best.pt 起) | 8 | 23%-33% | ⚠️ **全部受损** |
| 其中 v7_chain / v7_holdout | 2 | best=epoch 1 | ❌ **等于没训** |

**无一例外。** 这种一刀切的分界是对病因的强确认。

## 解读

1. **+0.025 是真的,但不是那堵墙。** 事先(实验开始前)登记的判据是"F1→0.70+ 说明配方是
   瓶颈 / F1→0.63 说明标注是瓶颈"。实际 0.650 **两边都没命中**,这个实验**没能干净地回答
   原问题**。不为它找解释。

2. **v8_chain 曲线全平(16 轮 0.373→0.369)才是关键信号。** 修好后模型不崩了,**但也不学了**
   —— 从第一轮就在平台上。这更像**容量或标注质量**的天花板,不像数量不足。
   参考:项目所有者标注自洽度 0.88,模型 0.65,中间 0.23 的差距未必靠加数量能补。

3. **"coco 血统该弃"被补跑证实。** v8_coco 跑满(61 轮早停)只到 0.549,与 v6_coco 的 0.554
   持平。之前判"连输两轮"其中一轮无效(v7_coco 在 epoch 4/100 被 SIGTERM),现已正式补上。
   续训血统(0.650)明显更强,因为它跨轮次累积见过 v1~v7 的全部数据。

4. **两把尺子打架。** v8_coco 的 val mAP50(0.4075)高于 v8_chain(0.3886),但 frozen-F1
   反而低(0.549 vs 0.650)。**在自己 val 上更好的模型,到没见过的币种上更差** ——
   val mAP 不能代表泛化,只有 frozen-eval 算数。

## 连带撤回的结论

- **"干净尺子首次证实加数据有效: v6(4501)0.595 → v7(6501)0.625"** —— **撤回**。
  v7_chain 只训了一个预热轮次,这 0.03 无法归因于 round6 的 2000 张标注。
  已在 `models/owner_best.json` 记录,`clean_curve_retracted: true`。
- **决定性 A/B(owner_v7_holdout)** —— **三重无效**:
  1. 基础权重是 v7_chain(训过那 30% "留出"币种)
  2. `dense_owner_v7h` 含 **596 张 eval 币种图(12.9%)** —— `ab_decisive.sh` 自建数据集时
     漏了 `is_eval` 过滤,而正式流水线 `train_owner_v7_from_round6.sh` 第 3 步是有的
  3. 训练崩溃,best.pt = epoch 1
  它报出的 `yolo_holdout auc=0.845 vs rule_holdout auc=0.508` 现在完全解释得通。
  脚本自身的 `top_n>=50` 闸门也判了它无效(top_n=45)。

## 顺带修复的第二个 bug:promote 会把泄漏模型推上生产

`scripts/promote_owner_best.py` 的注释写着"只收训练集排除了 eval 的 run",
**但代码 `glob("owner_v*")` 抓全部、排序、取第一名** —— 它会把 `v5_from_v4`(0.663)推上生产。
已改为扫描每个 run 的 `args.yaml` → 数据集 → 统计 eval 币种图;
**不可验证的一律拒绝**(假设一个模型是干净的,正是 0.663 被相信的原因)。

> 后怕:2026-07-15 夜里因 GPU 争抢被挂起的那个 promote 进程,**若跑完就会污染生产模型**。

## 风险与诚实声明

- **本轮实验未能回答它要回答的问题。** 事先登记的两个判据都没命中,0.650 落在中间。
  "标注是不是瓶颈"仍然未知。
- **v8_chain 的 0.650 只比 v7_chain 高 0.025,尚未做显著性检验。** frozen-eval 只有 768 张图 /
  47 个币种,0.025 的差距可能在噪声范围内。**不应据此宣称"修复带来了提升"** ——
  只能说"修复消除了崩溃"(这一点由曲线形状证明,不依赖 F1 差值)。
- **v8 是在 3060 上训练的,与 Mac 的历史结果跨机器对比。** 已强制版本对齐
  (torch 2.8.0 / ultralytics 8.4.89 / numpy 2.0.2 逐项一致),数据集逐位对账
  (4245 图 / 2054 框),batch/imgsz/SAFE_AUG 未改。但**跨机器数值差异未做量化**。
- **`lr0=1e-4` 是按通用经验挑的,没测过。** 既然 lr 刚被证明是关键变量,这个值本身
  也应该被扫描验证,而不是换一个未经检验的默认值。
- **v8 的 args.yaml 记的是 Windows 路径**,promote 靠"同名数据集"回退解析。
  这依赖 `train_on_3060.sh` 传输时的对账,不是密码学级的保证。
- 全量审计的"峰后崩溃"判据(< 峰值 20%)是我定的启发式,不是标准指标。

## 下一步选项

1. **【推荐】用修好的 lr 重做 clean_curve** —— 冷启动 4501(v6池) vs 冷启动 6501(v7池),
   两边同配方。**这才是"加标注有没有用"的正确实验**,也直接决定 round7 那 3000 张
   值不值得标。3060 上 2×2.5h,Mac 上 36h。
2. **lr 扫描** 3e-5 / 1e-4 / 3e-4 —— 验证 1e-4 是否合适。3060 上 3h。
3. **容量实验 yolo11s → yolo11m** —— 3060 的 12GB 显存只用了 4.8GB,M4 Air 装不下 m。
   v8_chain 的平曲线暗示容量可能是限制。
4. **A/B #3** —— 需**项目所有者决策**:前两次都卡在样本量(top_n=4 → 45,门槛 50),
   YOLO 池天生比规则池稀疏(n_val 150 vs 346)。**这是实验设计问题,不是算力问题**;
   在想清楚放宽 conf / 拉长时间窗 / 换留出方式之前重跑,大概率又是白跑。
5. **需项目所有者决策**:是否把 `models/owner_best.pt` 切到 v8_chain(0.650)。
   它是当前唯一既干净又训练正常的最佳模型,但见上文"0.025 可能在噪声内"。

## 相关

- `docs/learnings/ultralytics-auto-lr-destroys-finetune.md`
- `docs/learnings/nice-does-not-isolate-gpu-contention.md`
- `scripts/train_on_3060.sh` — Mac 保持唯一真相,3060 只做拟合
