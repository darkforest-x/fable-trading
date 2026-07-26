# 执行器每条腿都是市价单 → maker 成本口径不可达;裁决成本必须按真实路由

**日期**:2026-07-26
**类型**:审计发现(为 short 链路定成本口径时查出)

## 一句话

`FORWARD_COST = SWAP_MAKER = 0.0006` 被当作前向/裁决口径,但**当前执行器根本没挂过限价单**:
入场 `ordType="market"`,出场 OCO 两条腿 `tpOrdPx="-1"` / `slOrdPx="-1"`(OKX 语义 =
触发后市价)。**三条腿全 taker** → maker 口径不可达,`SWAP_TAKER=0.0010` 才是地板,
且它是**纯手续费、不含滑点**。

## 证据链

| 位置 | 内容 | 含义 |
|---|---|---|
| `src/execution/executor.py:1` | "Poll forward_log → place **market** + TP/SL bracket" | 设计即市价 |
| `okx_client.place_market:239` | `"ordType": "market"` | 入场 taker |
| `okx_client` OCO:267 | `ordType="oco"`, `tpOrdPx="-1"`, `slOrdPx="-1"` | 出场触发后市价 = taker |
| `src/costs.py` | `SWAP_TAKER=0.0010  # 0.05%/side (fills always)` | 纯费,**无滑点余量** |
| `src/costs.py` | `SWAP_MAKER=0.0006  # 0.02%/side + 滑点余量` | 需挂单成交,**当前拿不到** |

## 同时纠正另一端:0.2% 也不是裁决线

`src/costs.py` 对 `LEGACY_P0_ROUND_TRIP=0.002` 的注释是
**"P0-era blanket assumption … Not used for decisions."**(现货时代口径,仅为对齐
已发表 p2b 数字的连续性)。而主线宇宙是 **SWAP**。
→ **拿 0.2% 给 SWAP 链路当成功线,是用了仓库自己标注"不用于决策"的旧现货数字。**

## 教训

- **成本不是一个常数,是一条路由**;裁决前先问"**这条腿实际下什么单**",
  再去 `src/costs.py` 取对应值。别拿手边的 `FORWARD_COST` 当默认。
- **两端都会错**:用 0.2%(过悲观、且是现货旧口径)会埋掉真边;
  用 0.06% maker(过乐观、执行器拿不到)会造出假边。**本次两个错误同时存在。**
- **市价单的滑点必须单独立项**:`SWAP_TAKER` 只含手续费。山寨永续市价成交的滑点
  可能与手续费同量级甚至更大,**目前仓库里没有任何实测滑点数据**(forward_log 0 业务行)。
- **要么改口径、要么改执行器**:想用 maker 口径,就得把执行器改成挂限价
  (代价:可能不成交/漏单);维持市价,就得按 taker+滑点裁决。**这是 owner 决策。**

## 相关

- 敏感度表:`scripts/it19_short_at_real_execution_cost.py` /
  `analysis/output/it19_short_at_real_execution_cost.json`
- [[atr-scaled-barriers-vs-fixed-cost-fake-an-edge]](同一轮:成本口径如何造出/埋掉边)
- CLAUDE.md:成本假设属 owner 决策,不得自行改动
