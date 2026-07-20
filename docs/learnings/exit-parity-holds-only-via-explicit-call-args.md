# 回测/前向出场等价性靠调用方显式传参维持，不靠共享常量

- **问题**：验证回测标注器（`labeling.label_candidate`）与前向解析器
  （`forward_scan.resolve_forward_exit`）的 TP5/SL2 出场逻辑是否逐笔等价。
- **死胡同**：只看函数体会误判"完全一致"——两套 argmax 判定确实同构，但障碍倍数
  来源不同：前向用 `forward_types.TP_MULT=5.0` 常量，回测数据集靠
  `scripts/yolo_candidate_source.py` **显式传** `tp_mult=5.0`；而 `labeling.py`
  的模块默认值仍是 2b-v2 时代的 `TP_ATR_MULT=4.0`。裸调 `label_candidate()`
  （如 legacy `build_dataset.py`）得到的是 TP4 障碍，不是主线。静态读代码若只
  对照函数体、不追每个调用点的实参，会漏掉这类"默认值漂移"。
- **有效路径**：等价性测试不对比常量，而是把**同一个合成 OHLCV frame** 喂给两套
  函数、逐字段断言（outcome/label/exit_offset/realized_ret/exit_time）；边界形态
  手工枚举（同 bar 双触、跳空穿越、恰好触线、入场 bar 出场）+ 种子固定的随机游走
  模糊测试并断言四种 outcome 全部出现过（否则"全通过"可能只是没触发到分支）。
  设计性非对称（部分视界 open vs None、tip 信号代理入场）单独立测，明确它们只影响
  "何时给裁决"不影响"裁决数值"。
- **通用规则**：对照两套"应当等价"的实现时，(1) 先追调用点实参而非只读函数体；
  (2) 用同输入喂双实现的 property 测试代替常量比对；(3) 模糊测试必须断言所有
  outcome 分支都被命中过，否则覆盖是假的。
- **牵连**：`src/judgment/labeling.py`（TP_ATR_MULT=4.0 默认值，改动属 owner 障碍
  参数决策）、`src/judgment/forward_types.py`（TP_MULT/SL_MULT）、
  `tests/test_exit_parity.py`、`analysis/p_exit_parity.md`；成本扣法两侧都在
  报表层（`src/costs.py` 路由表），出场函数本身不含成本。
