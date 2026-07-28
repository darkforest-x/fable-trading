# tip_v1b 1000 框 → 空头观感过滤包（S3 补丁，不 promote）

**日期**：2026-07-25  
**回应**：Owner 要求只保留“看起来像空头启动”的 tip。  
**源包**：`analysis/output/owner_side_short_tip_v1b_detect1000/`（检测器仍是 tip_v1b）  
**新包**：`analysis/output/owner_side_short_tip_v1b_detect1000_shortish/`  

## 一句话

在原 1000 张 tip-edge 检出上，加了一道 **空头观感启发式** 后保留 **432** 张（43.2%，183 币）。  
规则按 owner 金标 short/long 对照校准：**short 更像均线下方走弱，不是冲高回落。**  
仍是目视包，**不是**交易信号，**不** promote。

## 过滤规则（可解释）

保留当且仅当同时满足：

1. **非多头均线堆叠**：`NOT (ema20 > ema60 > ema120)`  
2. **近端不涨**：`ret_12 <= 0`（近 12 根 15m 收益）  
3. **价在 ema60 下/贴下**：`close <= ema60`

校准摘要（owner_side_review 抽样）：

| 形态特征 | owner short 中位 | owner long 中位 |
|---|---:|---:|
| close vs ema60 | **负** | **正** |
| ret12 | **负** | **正** |
| 近 24 根位置 pos24 | **偏低** | **偏高** |
| bear stack 比例 | 高 | 近 0 |

因此“空头启动”在本项目金标里更接近 **下方承压/走弱中的密集 tip**，不是“高位滞涨”。

## 结果

| 项 | 值 |
|---|---:|
| 输入 | 1000 |
| 保留 | **432** |
| 保留率 | 43.2% |
| 币种 | 183 |
| 去掉 ret12>0 | 295 |
| 去掉 bull_stack | 229 |
| 去掉 close>ema60 | 44 |
| conf 中位（保留） | 0.67 |

## 审阅入口

- 预览：`analysis/output/owner_side_short_tip_v1b_detect1000_shortish/index.html`
- 填表：`.../review_sheet.csv`（仍有 `owner_keep` / `owner_note`）
- 全量审计：`.../filter_audit_all1000.csv`（1000 行含 reason）
- 摘要：`.../manifest.json`

## 风险与诚实声明

- 这是 **启发式观感滤镜**，不是新模型，也不是判断层。  
- 会漏掉“上方假突破后转空”一类 short（若你要包含，需另开规则分支）。  
- 也会放过部分“弱势横盘”非启动结构——仍需人工 keep。  
- 检测器本身仍无方向；方向是后处理。

## 下一步（需 Owner）

1. 只审 shortish 包，填 keep/note。  
2. 若仍混多：收紧（如强制 `bear_stack` 或 `ret12 < -0.5%`）。  
3. 若漏太多真 short：放宽 `close<=ema60` → `close<=ema60+0.5%` 或允许小幅 ret12。
