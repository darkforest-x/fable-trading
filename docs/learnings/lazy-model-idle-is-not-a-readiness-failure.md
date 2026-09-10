# 惰性模型空闲状态不能显示成就绪故障

- **问题**：模型 gate 在 `idle`、队列为空且尚未按需加载时，页面把 `loaded=false` 显示为“模型尚未就绪”，把没有候选的正常状态误导成故障。
- **死胡同**：只按 `loaded` 这个实现字段展示状态，会混淆惰性加载、正在加载和实际错误，也会让用户误以为原始 V1 启动被阻断。
- **有效路径**：先识别 `status=idle && loaded=false && queue_depth=0`，显示“YOLO 待命”；`loading`、显式 error 和 last_error 仍保留各自提示。原始 V1 和通知协议不参与该展示判断。
- **通用规则**：把按需加载组件的运行状态拆成待命、加载与失败；不要用“未加载”替代“不可用”。
- **牵连**：`yoyo/monitor/static/app.js`、`tests/monitor/frontend_cards.test.cjs`；不改变候选、YOLO 推理、Bark 或 Telegram。
