# 同周同方向随机对照会继承这一周的单边趋势

- **问题**：周一 1.618 研究里，信号平均 R≈0，但同币、同 NY 周、同方向、同止损/目标距离的随机入场是 -0.18 到 -0.28R，于是信号“超额 +0.24R、p<0.001”，前后两段都显著。
- **死胡同**：把这个超额当成 1.618 有反应力的证据。实际上能触及外侧 1.618 的周，本身就是单边周：多头 1.618 出现在大跌周，在同周随机时点做多等于逆着这周的跌势；空头同理。对照被选择条件拖成负值，信号只是“比逆势随机入场好”，并非绝对有优势。
- **有效路径**：另用无漂移障碍概率 `risk/(risk+reward)` 做绝对基准（见 [confirmation-win-rate-must-be-compared-with-driftless-barrier-odds](confirmation-win-rate-must-be-compared-with-driftless-barrier-odds.md)），并要求平均 R>0。按这个口径，10130 次触及有效率 39.1% 对理论 38.2%，平均 +0.03R，实质无优势；BTC 发现期 49% 在币安 2022-11..2025-05 样本外只有 31.5%。
- **通用规则**：匹配随机对照只能回答“同样条件下这个时点是否更好”；当信号条件本身筛选了趋势环境（触及极端位、突破周），必须再报一个不依赖该环境的绝对基准，且以平均 R/净收益为准，不用超额 p 值代替。
- **牵连**：`yoyo/evaluation/monday_1618_validity.py`（`random_geometry`）、`monday_1618_post_touch.py`（controls）、`monday_1618_oos_1m.py`；`experiments/active/exp-monday-1618-*-20261023-v1`。
