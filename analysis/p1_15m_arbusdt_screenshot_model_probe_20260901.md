# ARBUSDT 截图：15m Grade-A 模型单样本检测回放（2026-09-01）

## 结论先行

**检测出来了。** 使用最新完成的 Grade-A 8,000 正样本 + 24,000 负样本、full40、原生
`imgsz=1280` YOLO 权重，在截图匹配的 Binance ARBUSDT 15m 已收盘 K 线上得到一个
`dense_long` episode：

| 项目 | 北京时间（UTC+8） | 点位 / 结果 |
|---|---|---:|
| 模型原框对应的密集核心 | **09-01 00:00–00:45** | 实际核心 K 区间 `0.08680–0.08807` |
| 首次检出 bar 标签 | **09-01 01:15** | `confidence=0.6713`，该 bar 收盘 `0.08962` |
| 信号完整可得 | **09-01 01:30** | 01:15 bar 此时收完；下一根开盘 `0.08963` |
| 截图 | 09-01 09:05 | 屏显 `0.11694` |

这里必须把“bar 标签”和“可用时间”分开：仓内 K 线以开盘时间命名，所以模型的
`window_end_time=01:15` 表示最后纳入 01:15–01:30 这根 K；不前视的完整可用时刻是
**01:30**。若只说“01:15 检出”而不写这 15 分钟，会夸大提前量。

![ARBUSDT 15m 模型原框、首次检出点与时间](output/arb_screenshot_probe_20260901_v1/ARB_15m_model_detection_annotated.png)

绿色框是模型保留的原始 YOLO 四坐标框；黑色虚线是首次合法检出的窗口右端；圆点标的是该
bar 的收盘价。虚线右侧灰区只是为了对照用户截图而展示的后来行情，**没有进入模型输入**。
右下角是逐像素重建的真实 1280×742 模型输入及原框。

## 模型到底看到了什么

冻结参数没有按这张图回调：

- 权重：`ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt`
- SHA-256：`862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838`
- `conf=0.25`、NMS IoU `0.70`、W18/W19、core 4/5、post-core 2–9、原生 `imgsz=1280`
- 精确首检输入像素 SHA-256：`9be5f134ff99d13fbebc83fee83ee9e3c0732afaa43bb6b215e92021f912933b`

Binance 共计实际评分 127 个合法模型窗口：20 个窗口有原始框，20 个原框中 15 个通过冻结的
core/post 几何合同，最终合并为 **1 个 episode**。这 15 个候选是同一物理形态在相邻窗口里的
重复观察，不能报成 15 个信号。episode 的代表框按冻结规则取**最早可见**，不是取事后最高分；
最早分数为 0.6713，后来同一 episode 的最高分在 01:45 bar 达到 0.9218。

模型原框逆投影的价格范围是 `0.085806–0.088144`；对应离散核心四根 K 的实际 high/low 是
`0.08680–0.08807`。检测 bar（01:15–01:30）的 OHLC 为
`0.08952 / 0.09022 / 0.08865 / 0.08962`。

## 数据源与跨交易所核对

截图的 ARBUSDT 报价与 Binance USD-M 15m K 对上：截图时 09:00 K 仍在形成，因此正式输入
只保留到 08:45 这根已收盘 K，09:00 K 明确排除。随后又用相同权重和完全相同参数在模型历史
原生数据源 OKX `ARB-USDT-SWAP` 上独立扫描：

| 项目 | Binance（截图匹配） | OKX（原生源交叉核对） |
|---|---:|---:|
| episode | 1 | 1 |
| 方向 | LONG | LONG |
| 核心 | 00:00–00:45 CST | 00:00–00:45 CST |
| 首次检出 bar | 01:15 CST | 01:15 CST |
| 首次置信度 | 0.6713 | 0.5152 |
| 核心实际 low/high | 0.08680 / 0.08807 | 0.08678 / 0.08803 |

置信度不是胜率，且跨交易所不应直接比较绝对分数。这里有价值的是两套独立报价给出同一个核心、
同一个方向和同一个首次时间。作为时点身份零假设，把 OKX 结果在同一 UTC 日内循环错开
1–95 根 15m K，95 个非零位移没有一个仍同时匹配核心起止和首检时点；加一校正
`p=1/96=0.0104`。它只验证时间映射不是任意错位，不证明 detector 的 precision 或收益。

## 为什么不把后来 +30.48% 当模型成绩

从 01:15 检测 bar 收盘 `0.08962` 到截图 `0.11694` 的表面涨幅为 **+30.48%**。这是用户在
大涨后挑来的一张单图，存在极强的结果条件选择；它既不是盲测样本，也没有预注册退出、成本、
同币同时间块同波动桶随机对照。因此本轮的 val AUC、置换收益 p、top-decile 毛/净收益、胜率、
0.2% 往返成本和匹配随机交易对照均**不适用**，不能用这一张成功案例替代。

本轮能诚实回答的只有：“这一张在冻结模型和冻结阈值下是否检出、框在哪里、最早什么时候完整
可得。”答案是：**是；LONG；核心 00:00–00:45；01:15 bar 首检；01:30 才完整可用；点位约
0.08962–0.08963。**

## Holdout 与生产状态

- 这是 Grade-A full40 native-1280 这一 checkpoint 配置的 holdout 使用 **#3**；用户本轮请求
  明确授权了这张 2026-09-01 截图的模型试跑。
- 本模型需要核心后 2–9 根确认 K；本样本用了 2 根，仍是 completed-history / delayed detector，
  不能冒充 tip / tip-1 / tip-2 的生产即时信号。
- 没有训练、调参、改阈值、改权重、promote、部署、写 forward、发 Telegram 或下单。
- `models/active_bundle.json` 仍不存在，生产执行器仍 fail-closed；`training_eligible=false`、
  `production_eligible=false` 均未改变。

## 复现与审计入口

用户请求与运行合同：
`experiments/active/exp-15m-arbusdt-screenshot-probe-20260901-v1/run_contract.json`。

冻结输出：

- `analysis/output/arb_screenshot_probe_20260901_v1/result.json`
- `analysis/output/arb_screenshot_probe_20260901_v1/source_receipt.json`
- `analysis/output/arb_screenshot_probe_20260901_v1/inference_candidates_binance.csv`
- `analysis/output/arb_screenshot_probe_20260901_v1/inference_candidates_okx.csv`
- `analysis/output/arb_screenshot_probe_20260901_v1/exact_model_input.png`
- `analysis/output/arb_screenshot_probe_20260901_v1/exact_model_input_with_raw_box.png`

行情读取使用两家官方公共 REST K 线接口，时间边界固定为
`2026-09-01T01:00:00Z` **exclusive**。推理复用已提交的
`scan_symbol_day_candidates()`、`_predict_batches()`、`map_prediction_to_core()` 和
`render_chart()`；模型输入没有读取该边界或之后的 bar。独立像素重渲与 inference ledger 的输入
SHA 完全一致。

报告转换：

```bash
cd /Users/zhangzc/fable-trading
python3 scripts/md_to_html.py \
  analysis/p1_15m_arbusdt_screenshot_model_probe_20260901.md \
  --out-dir analysis/html
```

