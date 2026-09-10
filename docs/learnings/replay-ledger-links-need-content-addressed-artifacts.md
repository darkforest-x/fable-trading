# 回放账本联结必须指向内容寻址工件

- **问题**：回放事件保存了覆盖账本和进度文件的 SHA，但两个路径是会被后续 `evaluate-covered` 重写的可变汇总文件。文件被重建后，页面仍会显示旧的逐笔退出和净 R，无法再读取原字节验证该证据。
- **死胡同**：只把事件里的 SHA 更新为当前文件哈希会把新的账本冒充成旧的逐笔联结，也会丢失“旧证据已不可读”的事实。
- **有效路径**：先把当前 ledger 与 coverage receipt 原样复制为以 SHA 命名的不可变副本，逐字验证副本；旧链接先降为 `stale_evidence_unverified` 并移除可显示 outcome，同时嵌入原状态和原证据审计；随后只按四元组、输入 source SHA 和冻结 OHLC SHA 对快照重链。
- **通用规则**：任何会在下一轮汇总时覆盖的输入都不能作为长期逐笔证据路径。哈希只有在同一可读、不可变字节对象旁才可验证。
- **牵连**：`yoyo/monitor/replay_ledger.py`、`tests/monitor/test_replay_ledger.py`、`yoyo/monitor/static/app.js`；回放仍只读，绝不创建候选或通知。
