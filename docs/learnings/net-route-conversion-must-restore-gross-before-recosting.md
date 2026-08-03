## 问题

冻结 v10 数据集的 `realized_ret` 已经是扣除 taker 往返成本后的净收益，但历史报告又直接减去 maker 成本并称为 `net maker`，造成成本双扣和路由含义错位。

## 失败尝试

依据列名 `realized_ret` 推断它是毛收益，再在不同报告中直接减目标路由成本。模糊列名掩盖了源收益已含成本这一事实，数值仍然“看起来合理”，无法靠范围检查发现。

## 有效做法

协议同时声明 `target_ret_column`、`target_semantics`、`target_cost_included` 和 `reporting_route`。收益换路只允许经过唯一毛收益桥：`net_taker + taker_cost = gross`，再计算 `gross - maker_cost = net_maker`；API 拒绝对已净收益再次直接扣费。

## 可推广原则

收益列必须携带成本语义，任何路由转换都先还原到唯一毛收益基准，再扣一次目标路由成本。没有来源语义的“减一个成本”不应进入正式报告。

## 本次涉及

- `src/costs.py`
- `src/judgment/protocol.py`
- `models/active_bundle.example.json`
- `scripts/audit_l2_v10_return_semantics_20260803.py`
- `analysis/output/p0_return_semantics_20260803.json`

