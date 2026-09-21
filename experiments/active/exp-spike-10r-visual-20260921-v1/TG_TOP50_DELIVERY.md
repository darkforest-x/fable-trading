# 前50笔历史复盘图Telegram交付

用户明确要求将448笔赢家的前50张复盘图发到现有TG群，采用参考图的白底K线和均线样式，去掉手动涨幅测量框。

口径：按原候选最终扣费净R降序，event_key打破并列；从winner_gt10移除隐藏重复曝光，仍保留同一币种跨交易所/周期的独立原始候选。50张范围27.72195R至134.65116R。本轮仅重绘已有结果，不是新回测或新鲜信号。

使用真实原周期OHLC、SMA/EMA20/60/120、入场/初始SL/退出价格。显示信号前48根及退出后12根，不含蓝色手动测量框；每张1080×1620。源数据哈希、50个实际入场价和净R算术逐张校验。抽看最高收益、急拉、短持仓、长持仓四类样图，修正均线图例后再生成全套。

发送结果：Yolo均线密集交易系统，5组photo相册，50/50成功；消息ID2044–2093。图片总计8,451,428字节（本地原PNG），不声称Telegram压缩后与本地文件字节一致。每批先写pending，收到10个Message后写sent，模糊回执不自动重试。监控通知开关未改。

首次构建与发送命令（渲染需提交renderer/plan；交付后的图包冻结，禁止覆盖。只重跑发送命令时，完整回执会使其跳过全部相册）：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m yoyo.evaluation.spike_10r_top50_charts
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m yoyo.evaluation.spike_10r_top50_telegram
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m yoyo.evaluation.spike_10r_top50_telegram --send
```

产物：本目录tg_top50_v1/selection.csv、manifest.json、telegram_receipt.json、delivery_manifest.json及50张PNG。收益排名为事后筛选；相册不能用于评估形态胜率，不是训练金标。原blind/图像未修改。本次无新策略、阈值、模型训练或实盘变更，AUC/随机对照/新收益验收不适用。
