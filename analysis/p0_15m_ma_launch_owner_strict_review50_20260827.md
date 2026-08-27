# P0：15m 均线密集启动严格 shortlist Review50 v5

## 技术结论

这轮没有把 50 张继续强制框满。冻结 Review50 中只保留 **20 张单框提案**，其余 **30 张无框**：6 张来自 Owner 明确否决，24 张在完整联系表复核中因均线平行/过宽、价格已脱离均线、启动大 K 已进入核心或框后没有新鲜释放而严格淘汰。

Owner 附件中的样本身份也已纠正：截图标题是 `40/50 | GRT_USDT_SWAP | SHORT`，所以“第一张明显应该框右边一点”应用到 **第 40 张 GRT**，不是编号 01。第 40 张从旧 `t-11..t-7` 改为 `t-9..t-5`；第 01 张恢复其原提案 `t-7..t-3`。

20 张模型候选窗全部结束在核心、核心后 1 根或核心后 2 根：旧 v3 的核心到输入右端中位距离为 8 根，本轮降为 **0.5 根**，最大严格为 **2 根**。人工判断用的后 5 根 K 另存到 `future_review_only/`，没有 labels；实际拟模型输入是 `model_inputs_clean/` 中的无框 1280×742 PNG。

这仍是 **P0 待 Owner 复审提案，不是 Gold Dataset**。没有生成 YOLO 标签，没有训练 3060，没有读取 holdout OHLCV，没有修改 ACTIVE/frozen、forward、部署或交易状态。

2026-08-27 17:25（Asia/Shanghai），本轮 50 个原编号已全部用 Telegram `sendDocument` 逐张送达：20 张各 1 个红框、30 张明确无框，成功 **50/50**；PNG 字节没有经过 Telegram photo 路径重编码。发送器按每张的 source order、sample ID、文件 SHA 写入可续传回执，未发生失败或重复发送。

审核入口：`experiments/active/exp-15m-ma-launch-owner-strict-review50-v5/results/public/index.html`。

## 20 张保留、30 张无框比继续凑满 50 更可靠

| 版本 | 有框提案 | 无框 | 核心到模型右端中位/最大 | 主要结论 |
|---|---:|---:|---:|---|
| v3 相对最窄 L5 | 50 | 0 | 8 / 10 根 | 被 Owner 否决；相对 argmin 强迫每张都有框 |
| v4 短窗中间产物（未发 TG） | 44 | 6 | 1 / 2 根 | 只修复时效，联系表仍见旧提案语义错误 |
| **v5 严格 shortlist** | **20** | **30** | **0.5 / 2 根** | 时效与语义分开把关；30 张诚实返回无框 |

v4 是一次必要但失败的中间检查：把输入右端裁到 tip/tip-1/tip-2 后，延迟指标变好，但 36 张未点名旧提案中仍有平行均线和框内已经启动的样本。动态重裁剪只改变输入上下文，不会自动把弱标签变成正确标签，因此 v4 没有发到 TG，也没有转训练。

v5 联系表逐张查看拟模型输入和物理分离的未来 +5 图，只保留来源编号：

`01, 05, 09, 10, 14, 18, 19, 25, 26, 29, 32, 35, 40, 42, 43, 44, 46, 47, 48, 50`。

无框编号：

`02, 03, 04, 06, 07, 08, 11, 12, 13, 15, 16, 17, 20, 21, 22, 23, 24, 27, 28, 30, 31, 33, 34, 36, 37, 38, 39, 41, 45, 49`。

## 第 40 张身份和边界已按附件纠正

附件本身显示 `40/50`、`GRT_USDT_SWAP`、`SHORT`。v4 曾把“第一张”错配到来源编号 01；v5 先以附件编号、symbol、side 联结冻结 manifest，再修改唯一的 sample ID `e9578d1f834c6c5e2fa33fe3`。

第 40 张拟模型输入（右端只到核心后 0–2 根）：

![40 GRT model input](../experiments/active/exp-15m-ma-launch-owner-strict-review50-v5/results/public/images/40_GRT_USDT_SWAP_SHORT_e9578d1f834c6c5e2fa33fe3_MODEL_REVIEW.png)

同一核心的未来 +5 人工审核图；这张图不属于模型输入：

![40 GRT future review](../experiments/active/exp-15m-ma-launch-owner-strict-review50-v5/results/future_review_only/40_GRT_USDT_SWAP_SHORT_e9578d1f834c6c5e2fa33fe3_FUTURE_PLUS5_REVIEW_ONLY.png)

## Owner 参考样本保持原核心，但不再制造 8 根延迟

42（Owner 评价“不错”）和 44（“完美形态”）保留原核心 `t-6..t-2`；48 保留 `t-10..t-6` 的形态语义，但模型窗直接结束在该核心附近，不再让后续 8 根行情混入拟模型输入。

42 的拟模型输入与分离未来：

![42 FIL model input](../experiments/active/exp-15m-ma-launch-owner-strict-review50-v5/results/public/images/42_FIL_USDT_SWAP_SHORT_4e86ddc32a5401c49bf4aeb3_MODEL_REVIEW.png)

![42 FIL future review](../experiments/active/exp-15m-ma-launch-owner-strict-review50-v5/results/future_review_only/42_FIL_USDT_SWAP_SHORT_4e86ddc32a5401c49bf4aeb3_FUTURE_PLUS5_REVIEW_ONLY.png)

44 的拟模型输入与分离未来：

![44 NEIRO model input](../experiments/active/exp-15m-ma-launch-owner-strict-review50-v5/results/public/images/44_NEIRO_USDT_SWAP_SHORT_a20a0a4e50a94b1a017d38a0_MODEL_REVIEW.png)

![44 NEIRO future review](../experiments/active/exp-15m-ma-launch-owner-strict-review50-v5/results/future_review_only/44_NEIRO_USDT_SWAP_SHORT_a20a0a4e50a94b1a017d38a0_FUTURE_PLUS5_REVIEW_ONLY.png)

## 数据、窗口与像素定义

- 冻结来源：50 个唯一 pre-holdout 弱候选，LONG 25 / SHORT 25，train 40 / val 10。
- 来源时间：2022-01-14 13:00 UTC 至 2026-05-02 14:00 UTC；holdout 起点为 2026-05-04 00:00 UTC。
- v5 有框提案：20，LONG 8 / SHORT 12；来源 split 为 train 15 / val 5。这些 split 只保留身份，没有训练含义。
- 核心长度：5 根 19 张，4 根 1 张（第 18 张按 Owner“只看右侧 4 根”）。
- 拟模型窗：14–19 根，中位 16 根；核心前 10–12 根，核心后 0–2 根。
- 所有 PNG：1280×742，使用同一 frozen renderer、同一 K 线和六均线颜色；没有 resize、JPEG 或 TG photo 压缩。
- 红框宽度：327–401 px，中位 371.5 px；高度 95–400 px，中位 176 px。宽度变化来自 4/5 根核心及 14–19 根上下文，不是任意手缩像素框。
- 每张有框图严格 1 个框；无框图严格 0 个框。

## 模型 clean 图、审核框和未来 K 物理分离

每个有框提案有三份不同用途的文件：

1. `model_inputs_clean/*.png`：拟模型实际看到的无框 K 线图；右端只在 core/core+1/core+2。
2. `public/images/*_MODEL_REVIEW.png`：与 clean 图相同，只叠加一个红框供 Owner 看坐标。
3. `future_review_only/*_FUTURE_PLUS5_REVIEW_ONLY.png`：多显示核心后 5 根，只供人工判断；目录内禁止 labels。

像素验收：20/20 clean 图中审核红色 `(BGR 45,45,232)` 像素为 0；20/20 review 图均存在该红框颜色，单图 4,318–7,668 个精确红色像素；clean 与 review 每图差异 6,042–10,732 像素。三类图片全部 1280×742；未来目录 `.txt` 为 0，整轮 YOLO label 为 0。

## Telegram 逐张高清交付回执

- 发送区间：2026-08-27 17:21:17–17:25:05（Asia/Shanghai）。
- 图片 document：50/50；原编号严格为 01..50，无缺号、无重复。
- 其中有框：20；无框：30；每个有框文件只有一个红框。
- 发送方式：`sendDocument`，不是 `sendPhoto`；交付文件仍是本地验收过的 1280×742 PNG。
- 回执：`experiments/active/exp-15m-ma-launch-owner-strict-review50-v5/results/telegram_delivery_receipt.json`。
- 回执当前绑定 50 个文件 SHA；本 HTML 在图片送达后重新生成，并作为最后一个 document 单独发送。

## 方法与严格淘汰口径

本轮不再使用“在 `t-12..t-1` 中必选相对最窄 5 根”。先尊重 Owner 明确裁决，再对剩余图同时查看短模型窗与物理分离的未来 +5 联系表：

- 明确平坦/平行、六线仍宽或价格已经脱离均线：无框；
- 启动大 K 已进入提议核心：无框，避免模型学“已经发生”；
- 框后没有新鲜同方向释放：无框；
- 只有紧凑交互区位于新鲜启动之前，才保留一个最短充分框；
- Owner 指定边界的 14/18/32/35/40 按逐图 offset 提议，不做全批统一平移；
- 42/44/48 只保留语义核心，输入右端重新裁到核心附近。

这是一轮严格视觉提案，不是自动化可复用分类器。24 张 Codex 严格淘汰和 12 张 Codex 严格保留都仍待 Owner 复审；只有 6 张明确否决和 42/44/48 的方向性评价属于已有 Owner 语义裁决，具体 v5 像素坐标仍未获逐样本确认。

## 非方向性零假设与失败反证

本轮是标签几何与数据隔离审计，没有交易收益，因此 val AUC、top-decile 毛/净收益、胜率、收益置换检验、单特征基线和匹配随机入场对照均不适用；这些指标只有训练和方向性评估后才有定义，本轮禁止编造。

对应的零假设是“只要把旧核心裁到输入右端，标签语义就自动正确”。v4 把 44/44 有框样本都压到 core+0..2，时效统计已通过，但全量联系表仍出现大量平行均线、框内已启动或无新鲜释放样本，因此该零假设被直接否决。v5 的 24 个额外无框结果就是这一反证的产物。

## 风险、限制与鲁棒性

1. 20 张仍未获得 Owner 逐样本 START/END 完整确认，禁止写 YOLO labels 或训练。
2. 联系表严格门包含人工视觉判断，不应冒充已经泛化的自动筛选器；以后要自动化，必须用 Owner 复审结果拟合/验证规则。
3. 动态短窗会改变每根 K 在 1280 px 中的视觉宽度和纵轴范围，但没有压缩另存；这是“不同数据窗口重新渲染”，不是图像 resize。正式训练前必须固定同一套窗口分布并做训练/检测 renderer parity。
4. 第 40 张 `t-9..t-5` 是按“往右”做出的新提案，不是 Owner 已确认坐标。
5. holdout 消耗 0；读取只使用 `read_preholdout_prefix()`，边界之后 OHLCV materialized rows 为 0。
6. 没有模型训练、3060 作业、promote、ACTIVE/frozen、forward、部署或真金操作。

## 下一步只剩 Owner 复审，不是训练

1. TG 的 50 张逐图裁决和本 HTML 已交付；Owner 对 20 张有框提案给 KEEP / ADJUST / REJECT。
2. ADJUST 必须逐图给 START/END，不能统一 delta；30 张无框若认为应保留，也请按原编号指出。
3. 只有逐样本闭合后，才把确认框联结回 clean PNG 生成 labels，再重新构造负样本禁入区与时间 split。
4. 未确认前继续禁止 3060 训练和任何 promote。

进一步问题：20 张是否仍太多；第 14/32/35/40 的具体起止根是否还需缩短；Owner 是否认可 LONG/SHORT 共用同一类别语义。这些只能由下一轮逐样本复审回答。

## 复现命令

```bash
git branch --show-current
python3 -m pytest -q \
  tests/test_ma_launch_owner_strict_review.py \
  tests/test_ma_launch_owner_recrop_review.py \
  tests/test_ma_launch_density_core_box_review.py
PYTHONPATH=. python3 scripts/build_15m_ma_launch_owner_strict_review50.py
python3 scripts/md_to_html.py \
  analysis/p0_15m_ma_launch_owner_strict_review50_20260827.md \
  --out-dir analysis/html
PYTHONPATH=. python3 scripts/send_15m_ma_launch_owner_strict_review50.py images
PYTHONPATH=. python3 scripts/send_15m_ma_launch_owner_strict_review50.py report
```

生成器 fail-closed 拒绝覆盖已有 `results/`。其 builder commit 为 `d70e64938c498674dda2b75ac1a584c681ae16be`；source manifest SHA 为 `cc852cb9da838056a8c95e80ba60270fa1537860973437b35638cff1efe63c66`；v5 review manifest SHA 为 `239eab3eae9f7ff635e6e755b7dc934cd29152b5665cbdb7f934739ea189a00c`。
