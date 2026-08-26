# 因果输入与未来验真必须并排联结，不能强制同一坐标尺度

- **问题**：同一启动事件的 14--22 根训练图与 48 根完成走势审核图视觉差异很大，Owner 无法确认模型是否真的在学习先前找到的形态。
- **死胡同**：把短窗和长窗都拉满同一画布后宣称 renderer 一致；或者反过来让训练图采用含未来高低点的纵轴、把 48 根全塞进训练输入。前者隐藏了每根 K 线宽度和 auto-Y 的变化，后两者分别造成未来泄漏和检测窗合同漂移。
- **有效路径**：以 `event_id` 联结两个物理视图：左栏直接复用 canonical 模型 PNG 并从同名 YOLO 文本做非破坏叠框；右栏独立显示含未来的人工验真图。两个 panel 各保留自己的坐标系，manifest 同时锁定图片/标签 SHA、可见时间右端和 review-only 资格；时间切分 purge 的事件没有模型图就明确缺失，不补造。
- **通用规则**：视觉 parity 不是“所有图看起来一样”，而是每个消费面看到的像素与其合同一致。模型输入、标签叠框和未来验真应按同一事件三联展示，但未来面板永远不得参与训练像素、框坐标或缩放参数。
- **牵连**：`yoyo/datasets/ma_launch_review_parity.py`、`experiments/active/exp-15m-ma-launch-t3-review-parity-v2/`、`datasets/ma_launch_t3_10000_v1/`；延伸自 [渲染一致性必须明确比较的是哪两种视图](renderer-parity-must-name-both-surfaces.md) 与 [人工验真可以看未来，但模型输入必须从截止时刻重渲染](human-review-may-see-future-but-model-input-must-be-re-rendered.md)。
