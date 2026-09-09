# 展示资格、通知权限与历史回执必须分开

- **问题**：Owner 要求 5m/15m/30m 继续在前端展示，同时关闭 Bark 两阶段通知；1H/4H/日线保留推送。
- **死胡同**：复用旧的“撤销周期”实现会同时停止扫描、隐藏箭头并终止模型候选。检查发现 `DIRECT_TIMEFRAMES` 还参与 API 的原始信号筛选，直接缩成 Bark 周期会误删短周期展示。只在生成端过滤，也拦不住持久队列里升级前的待发项；把过去的 sent/unknown 全改为 skipped 则篡改发送事实。
- **有效路径**：扫描和原始事件资格覆盖全部六周期；独立 `BARK_TIMEFRAMES` 只授权三个长周期。原始箭头入队、模型确认入队、发送前统一检查通道权限。启动在单进程锁内先恢复 sending 为 unknown，再仅清退静音周期 pending，保留事件、候选、启用时间与终态回执。前端从 runtime 读取当前权限，同时显示真实历史回执。
- **通用规则**：关闭一种副作用时先枚举共享常量的读者，拆开数据是否存在、当前是否允许操作、历史是否发生过三个事实；至少同时验证两阶段、多方向、旧队列、重启与历史回执。
- **牵连**：`yoyo/monitor/{__init__,notification_policy,model_gate,service,store}.py`、`static/app.js`、`tests/monitor/test_display_only_periods.py`。Telegram 保持关闭，不改信号阈值、模型权重、新鲜度和订单路径。
