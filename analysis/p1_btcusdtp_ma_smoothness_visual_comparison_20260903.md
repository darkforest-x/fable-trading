# BTCUSDT.P 六均线平滑度视觉对照（2026-09-03）

性质：P0/P1 只读、pre-holdout、单窗口配对视觉筛查。  
授权边界：**只渲染；未推理、未训练、未调阈值、未读 holdout、未改标签/数据集/ACTIVE/frozen、未部署。**

## 给 Owner 的两张选择图

第一张只比较**均线数值算法**。所有面板使用同一段 BTCUSDT.P 15m、同一 160 根 K、同一个
Y 轴、同样 2px 六色线；右下角是用户给出的 ChartPrime 默认单线参考，不是六线。

![BTCUSDT.P numerical MA families](output/p1_btcusdtp_ma_visual_comparison_20260903/numeric_families_contact_sheet.png)

编号从左到右、从上到下：

1. 当前：close 的 SMA/EMA 20/60/120
2. 当前体系只把 close 换成 hl2
3. hl2 的六条 SMA：20/40/60/80/100/120
4. hl2 的六条 EMA
5. hl2 的六条 RMA
6. hl2 的六条 WMA
7. hl2 的六条 VWMA
8. hl2 的六条 HMA（额外对照；ChartPrime 主线没有 HMA）
9. ChartPrime 默认：单条 `SMA40(hl2)`，2px 主线 + 7px、20% opacity 柔光

第二张只比较**画线方式**。六条线的数值全部保持当前 close-SMA/EMA 20/60/120 不变，窗口是
YOLO 实际看到的同一 19 根图。

![BTCUSDT.P pixel styles](output/p1_btcusdtp_ma_visual_comparison_20260903/pixel_styles_contact_sheet.png)

编号从左到右、从上到下：

1. 当前 1px、六色
2. 2px、六色
3. 3px、六色
4. 2px 六色 + 7px 柔光
5. 1px、六线同色
6. 2px、六线同色
7. 2px 同色 + 7px 柔光
8. SMA 2px / EMA 1px、六色

全尺寸 1280×742 单图分别在：

- `analysis/output/p1_btcusdtp_ma_visual_comparison_20260903/numeric_full_resolution/`
- `analysis/output/p1_btcusdtp_ma_visual_comparison_20260903/style_full_resolution/`

## 数据与公平比较合同

| 项目 | 值 |
|---|---|
| 合约 | Binance USD-M `BTCUSDT.P` |
| 周期 | 15m |
| 事件 | `d2fdfcb2fc81a9d80439`，SHORT |
| YOLO 19 根窗口 | 2021-07-30 04:15Z ～ 08:45Z |
| 数值观察窗 | 同一终点向前 160 根 |
| 因果读取 | 只读取 CSV 前 55,332 行，最大时间 2021-07-30 08:45Z |
| holdout 读取 | 0 |
| 坐标轴 | 每张 sheet 内完全共用，不随 MA 变体缩放 |
| baseline parity | 19 根当前样式与冻结训练 PNG 解码像素完全一致 |

使用共用 Y 轴很重要：如果每个算法各自自动缩放，较宽或较平的线束也可能被显示成看似同样
平滑，Owner 的目测就会被坐标轴混淆。

## 单窗口描述性测量

“曲率”是六条线在共用坐标轴上的平均绝对二阶像素差，越低表示这一段画面越圆滑；“包络宽度”
是六线最大/最小 y 的中位像素距离。它们只描述这一个 BTC 窗口，不能直接预测 YOLO 准确率。

| 编号 | 数值方案 | 曲率 px/bar² | 平均斜率 px/bar | 六线中位宽度 px |
|---:|---|---:|---:|---:|
| 1 | 当前 close SMA/EMA | 1.392 | 2.492 | 73.0 |
| 2 | 当前体系改 hl2 | 1.254 | 2.436 | 72.5 |
| 3 | hl2 SMA6 | 0.974 | 2.002 | 40.0 |
| 4 | hl2 EMA6 | 1.150 | 2.072 | 55.0 |
| 5 | hl2 RMA6 | **0.775** | **1.718** | **228.5** |
| 6 | hl2 WMA6 | 1.164 | 2.137 | 32.0 |
| 7 | hl2 VWMA6 | 1.871 | 2.687 | 42.5 |
| 8 | hl2 HMA6 | 1.724 | 5.091 | 68.5 |
| 9 | ChartPrime 单 SMA40 | 1.025 | 1.824 | 不适用（单线） |

这张图最值得注意的不是“RMA 第一”，而是：RMA 的确最平滑，但六条不同周期线长期分得最开，
中位包络是当前的 3.13 倍。对于要识别“六线密集启动”的 YOLO，**圆滑与线束密集不是同一个
目标**。HMA 追价快、斜率最大，视觉上也没有比当前更平。

`close→hl2` 在这个窗口把曲率从 1.392 降到 1.254，约降低 9.9%，而包络几乎不变；它是比
“整套换 RMA”更温和的候选。但单个窗口只能供 Owner 看形态，不能据此采用。

## 计算口径

- SMA：窗口算术平均；
- EMA：`ewm(span=n, adjust=false)`，与仓库当前口径一致；
- RMA：`alpha=1/n`，第一项由前 n 根 SMA 播种；
- WMA：近端权重按 1..n 线性增加；
- VWMA：`sum(source*volume)/sum(volume)`；
- HMA：`WMA(2*WMA(source,n/2)-WMA(source,n), round(sqrt(n)))`；
- ChartPrime 默认参考：用户代码中的单条 `SMA40(hl2)`，颜色按 `hl2>=MA` 切换并叠加柔光。

除当前体系外，同类型六线统一使用 20/40/60/80/100/120，确保 SMA、EMA、RMA、WMA、VWMA、
HMA 之间只变化滤波器 family。

## 非方向性说明、零假设对照与限制

- 本轮没有模型输出、收益标签或交易裁决，因此 val AUC、mAP、top-decile 收益、胜率、成本、
  置换检验和匹配随机入场对照均不适用；不得编造。
- 同等严格的零差异控制是：同一行情、同一窗口、同一蜡烛、同一尺寸、同一 x/y transform；视觉
  样式 baseline 还必须逐像素复现冻结训练图。该 parity 已通过。
- 这是一个 BTC 单事件的目测屏，不代表全币种分布；Owner 选中候选后仍要在预注册的多事件样本上
  单变量重渲染、重训和评估。
- 数值 sheet 为方便观察使用 160 根；模型样式 sheet 才是实际 19 根视觉尺度。
- 柔光只是 ChartPrime 的视觉效果，可能把交叉/次序糊成色带；“人眼更顺”不能自动等于
  “YOLO 更准”。
- VWMA 引入 Binance volume 语义，跨 venue 可能漂移；HMA 是 ChartPrime oscillator 的平滑器，
  不是用户代码中主图 MA 的默认 family。

## 复现命令

脚本默认拒绝覆盖现有产物；复现时指定新目录：

```bash
cd /Users/zhangzc/fable-trading
render_dir="$(mktemp -d /tmp/fable-btc-ma-visual.XXXXXX)"
PYTHONPATH=. .venv/bin/python \
  scripts/render_btcusdtp_ma_smoothness_comparison.py \
  --output-dir "$render_dir"
python3 scripts/md_to_html.py \
  analysis/p1_btcusdtp_ma_smoothness_visual_comparison_20260903.md \
  --out-dir analysis/html
```

产物回执：
`analysis/output/p1_btcusdtp_ma_visual_comparison_20260903/render_receipt.json`。

## 下一步选项

Owner 可直接回复“数值图第 N 个、样式图第 M 个”。选择只代表后续候选，不会自动开训。

当前工程判断仍是：数值候选优先看 **2（hl2）或 3（SMA6）**；像素样式优先看
**2（2px 六色）**。但最终以 Owner 对形态语义的视觉判断为准，之后再冻结单变量实验合同。
