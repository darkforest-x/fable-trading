# 混合精度事件时间必须显式归一化后再排序

- **问题**：共享账户为了保证“同刻先退出、后入场”，把部分同 bar 退出记成
  `+1ns`。CSV 因而同时含秒级 ISO 时间和九位小数纳秒时间。批量解析器在看到第一种格式后
  推断单一模板，后续纳秒行变成 `NaT`，账户事件会被漏掉或错序。
- **死胡同**：逐个样本解析看似都成功，就假定整列 `to_datetime` 也等价；或直接删掉纳秒，
  会重新制造退出与入场的同刻歧义。静默 `errors="coerce"` 更危险，因为流程仍能产出数字。
- **有效路径**：读取边界统一使用支持混合 ISO 精度的显式解析，全部转成 UTC，并在任何排序、
  切片或 merge 前拒绝 `NaT`。用秒级与纳秒级交错的合成事件钉死顺序，再用真实 manifest
  哈希和 seed=0 结果复现钉死账本语义。
- **通用规则**：事件账本的时间戳精度是协议的一部分。只要系统人为加入亚秒 tie-break，
  解析、序列化、分段过滤和可视化都必须保留该精度；“时间看起来一样”不代表事件相同。
- **牵连**：`yoyo/evaluation/spike_account_growth.py`、
  `yoyo/evaluation/spike_account_growth_study.py`、
  `yoyo/evaluation/spike_account_growth_post.py` 及对应 evaluation tests。
