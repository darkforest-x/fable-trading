# 启动入场 vs 盘整中入场：因果 base rate 单变量对照 — 2026-07-23

> **⚠ 测量呈现 bug（owner 2026-07-23）**：本报告把 long/short **混成一行 PF**，
> 主裁决作废/降权。方向规则本身未写反（突破上→多、下→空）。
> **请改读分边报告 → [`p_launch_entry_long_short.md`](./p_launch_entry_long_short.md)**。

**纪律**：纯离线，`<2026-05-04`（**未碰 holdout**）；入场=下一根开盘（全变体统一）；
障碍=现役 **TP5/SL2/72bar**；成本同时报 maker 0.06% 与 legacy 0.20%。不 promote、
不下单、不清账、不改三门。

回答 owner：「启动那一刻 + 跟随突破方向」相对「密集第 5 根（盘整中）」是否抬高 PF，
抬到能否过 1.3？几种启动定义**分别、单变量**测，不打包。

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python scripts/launch_entry_base_rate.py \
  --n-symbols 0 --tag launch_entry_base_rate
# 输出: analysis/output/launch_entry_base_rate.json
```

## 约定（全变体统一）

| 项 | 值 |
|---|---|
| 入场价 | 信号 bar 的**下一根开盘**（与 `labeling.py` / `base_rate_dense_offline` 同） |
| 障碍 | TP5 × ATR14 / SL2 × ATR14 / 72 bar；同 bar 双触→SL；`atr_pct≥0.0015` |
| 成本 | maker 0.06%（`FORWARD_COST`）+ legacy 0.20%（`LEGACY_P0_ROUND_TRIP`） |
| 密集门 | judgment `add_indicators`：`fast_spread≤0.0028` & `full_spread≤0.0055`，run≥5 |
| Bundle | **本实验内对齐**：只调 `add_indicators`，不用 `add_mas→add_indicators` 链。门控 spread = EMA8/13/21/34/55（快）+ EMA144/200（满），与已发表 emergence 脚本在 overwrite 后的**有效定义一致**（见诚实声明） |
| 启动窗 | 密集合格后最多等 48 根（12h）找启动；MIN_GAP=18 去重 |
| 宇宙 | OKX SWAP，剔 stockish / frozen-eval；train only |

## 变体定义（各自独立一行）

1. **emergence_always_long**：密集 run 刚到 5 → 固定做多（与 `p_base_rate_dense_verdict` 可比对照）
2. **emergence_mom24**：同上 tip，方向=近 24 根动量符号
3. **range_break_n20**：密集合格后，第一根收盘突破前 N=20 高低 → 跟突破方向
4. **vol_break_n20_k1.5**：同上 + 当根量 > 近 M=20 均量 × k=1.5（单组默认，未扫 k）
5. **spread_expand_chg8**：密集后第一根 `fast_spread[i]-fast_spread[i-8] ≥ 0.00383` → 方向=收盘相对 cluster 中轴
6. **ma_arrange_cross**：密集后第一根 ema8×ema21 交叉或 close×sma20 → 跟向
7. **owner 框右缘 oracle**：引用已发表，不重算（非因果对照）

## 数据统计

| 项 | 值 |
|---|---|
| 币数 | 236 |
| 时间 | 2025-06-05 → 2026-05-03（train） |
| 各变体触发数 | 见下表 n 列 |

## 对照表（train · TP5/SL2 · 下一开）

| 变体 | n | 胜率 | 毛/笔 | 净@maker | PF@maker | PF@0.2% | 多空比(多%) |
|---|---:|---:|---:|---:|---:|---:|---:|
| emergence_always_long（盘整中） | 16145 | 29.6% | -0.00024 | -0.00084 | **0.876** | 0.711 | 100% |
| emergence_mom24 | 15891 | 31.1% | +0.00039 | -0.00021 | 0.968 | 0.787 | 48.4% |
| range_break_n20 | 15115 | 31.7% | +0.00061 | +0.00001 | 1.002 | 0.816 | 48.2% |
| vol_break_n20_k1.5 | 13772 | 31.7% | +0.00073 | +0.00013 | 1.019 | 0.834 | 47.1% |
| **spread_expand_chg8** | 12002 | 32.8% | +0.00107 | +0.00047 | **1.065** | 0.887 | 48.3% |
| ma_arrange_cross | 15941 | 30.7% | +0.00013 | -0.00047 | 0.930 | 0.759 | 51.6% |
| owner 框右缘 oracle（引用） | 3112 | 35.0% | — | +0.00183 | **1.183** | **1.039** | 多（原实验） |

无变体 PF@maker > 1.3 → 未触发强制泄漏复查；信号定义均为 bar≤i 因果量。

## 与既有锚点对照

| 锚点 | 数 | 本轮关系 |
|---|---:|---|
| emergence 0.87 | 0.874 已发表 / **0.876 本轮复现** | 对照机与测量机对齐，可信 |
| oracle 方向 2.68 | TP3/SL1 上「事后选对边」天花板 | **不同障碍**，不可与本表 PF 直接比绝对值；只说明「若方向全知」的上限叙事 |
| owner 框 oracle 1.18 | 框右缘入场 train PF | 仍高于一切因果启动规则；手感边在确认态选点，不是可部署 tip |

## 裁决句

**启动入场相对盘整中入场抬高了 PF，但抬不过 1.3。**

- 盘整中（emergence 固定多）：PF **0.876**（复现 0.87）
- 最好的因果启动（spread 散开 ≥0.00383）：PF **1.065**（Δ≈+0.19）
- 区间/放量突破：≈ **1.00–1.02**（毛转正，扣 maker 擦盈亏线）
- 全部因果变体 **远低于** owner 框 oracle 1.18，更远低于方向全知叙事 2.68（且 2.68 还是另一套退出）
- **没有任何因果启动定义越过可交易线 1.3**；扣 0.2% 后全部 <1.0

解读：owner 纠正方向是对的——「第 5 根」确实偏早，换成突破/散开/交叉能抠出约 0.1–0.2 PF；但粗糙规则启动 ≠ owner 眼睛，增量不够付钱。

## 风险与诚实声明

1. **粗糙突破 ≠ owner 真实眼睛**：N=20 高低、固定 k=1.5、固定 chg8 阈值都是机械代理；owner 框 1.18 的选点过滤未被这些规则复制。
2. **未碰 holdout**：本表是 train 发现级，不作上线裁决。
3. **Bundle 说明**：历史上 `base_rate_dense_offline` 写了 `add_indicators(add_mas(...))`，`add_indicators` 会覆盖 detection 层 spread；本轮显式只用 judgment bundle，与已发表 emergence **有效定义一致**，故 0.876≈0.874。若改用 detection SMA/EMA20/60/120 门控，数字会变，需单独立项。
4. **方向 oracle 2.68 不可直接对齐**：来自 TP3/SL1 + 事后选边；本轮统一 TP5/SL2。
5. **单阈值未扫参**：vol 的 k、spread 的 thr、MAX_WAIT=48 均未网格；改阈值属新实验，需 owner 批准。
6. **多空对称障碍假设**：空头用镜像 TP/SL；真实资金费/借币未建模。

## 下一步选项（需 Owner 决策）

- **A. 停在「启动规则不够」**：接受因果启动最高 ~1.06，不再扫更多机械定义；精力转成本工程或收摊。
- **B. 只深挖 spread_expand**（单变量）：扫 `spread_chg8` 阈值或换 `spread_chg24`——预期边际，**不承诺过 1.3**；若做需批准阈值网格范围。
- **C. 对照 owner 框手法**：不重训 YOLO；用框右缘附近的可观测确认特征做**确认态规则**（已知因果 AND 规则曾≈0.87，需新假说才值得再测）。
- **D. 动 holdout**：本轮明确不建议；任何 holdout 验收需单独口头批准并记消耗次数。

测量机建议：**A 为默认**；B 仅当 owner 想确认「散开阈值是否假敏感」。启动入场抬 PF 的命题已答完。
