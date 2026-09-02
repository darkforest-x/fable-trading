# Telegram 原图交付需要字节合同与可续传账本

- **问题**：批量把研究检测图发到 Telegram 时，既要保留原始 PNG 字节，又要能证明每张都发过；网络在第 N 张中断后，还不能把前 N−1 张重复群发。
- **死胡同**：`sendPhoto` 会走图片展示链路，不能把远端对象当作原文件；一次性循环只在末尾写“成功”也不够，中途失败既没有逐张证据，重跑还会产生重复消息。
- **有效路径**：先用清单、图像 SHA256、顺序和安全标志生成冻结的交付合同；用 `sendDocument` 发送原始 PNG；每次 Telegram 返回成功后立刻原子写入一条动作回执。恢复时只跳过合同内已有回执的 ID，合同变化或完整回执再次执行时 fail closed。
- **通用规则**：批量向外部消息系统交付不可重算的文件，第一步先冻结“顺序 + 内容哈希”合同；传输方式、逐项回执、断点恢复和完成后防重发必须同时设计。
- **牵连**：`scripts/send_15m_ashare_standard_retail_to_telegram.py`、`yoyo/notify.py`、Telegram `sendDocument`、实验目录内的 `telegram_delivery_receipt.json`；回执不得保存或回显 bot token / chat id。
