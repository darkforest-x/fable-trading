# 自动生成语义门不能替代跨周期 Owner Gold

- **问题**：15m 自动生成验证集上通过的 YOLO + 数值语义门，被原样搬到 4h 后虽把 221 个事件筛到 34 个，Owner 看完全局图仍批次否决。
- **死胡同**：继续把自动生成器阈值解释成业务真值，或在已消费 4h holdout 上收紧 ATR 门；前者只证明协议自洽，后者会把事后观感过拟合成“验证成功”。
- **有效路径**：把技术谓词通过与 Owner 视觉通过拆成两层裁决；收到批次否决后冻结失败基线，不伪造逐样本标签，也不重调阈值。跨周期任务回到 pre-holdout P0/P1，先建立该周期自己的可重复目标协议和 Gold。
- **通用规则**：模型或门跨周期前，第一步检查训练标签来源、窗口所代表的真实时间和 Owner Gold；只要其中任一改变，就把它视为新目标域，而不是同一图形的无损缩放。
- **牵连**：`yoyo/layers/l1_detection/semantic_gate.py`、`experiments/active/exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1/`、holdout 使用 #7、ROADMAP P0/P1 与禁止事后调门约束。
