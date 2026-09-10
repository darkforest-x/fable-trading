# V5 多空确认、白色信号 K 线与无边框盈亏区

当前私有 TradingView **V5 第 3 版**已保存，原生显示保存时间为 2026-09-10 23:48。默认启用多空确认，确认 K 线白色，R 里程碑开启，盈亏矩形外边框移除。保留原填色及独立入场、初始止损、趋势保护线。

## 对照与验证

| 项目 | 更新前 V5 | 本次 V5 |
|---|---|---|
| 方向 | 仅多头确认 | 多空独立确认、独立冷却与结构等待 |
| 确认 K 线 | 原多头颜色 | 多空均为白色 |
| R 里程碑默认 | 关闭 | 开启，原 3R/5R/10R 达到提示 |
| 盈亏框外边框 | 有 | 无，保留填色和独立价格线 |
| PEPE 1H 已冻结多头案例 | 2 个 | 2 个，时间完全保留 |
| 同窗口空头确认 | 未实现 | 4 个 |
| 功能检查 | 前版基线 | 29 项通过；多空前缀一致性通过 |

**原生 TV 核对：**PEPE 1H，北京时间 8 月 20 日 00:00 多头确认仍在，收盘参考 0.000002714；8 月 26 日 22:00 出现空头确认，收盘参考 0.000003677，冻结父高/父低 0.000003880 / 0.000003698。上述均为 K 线开盘标签，收盘确认时间晚一小时。原生数据窗口最终确认分别为 1，空头确认等待 0 根。已目视看到白色确认 K 线、空头风险向上/收益向下的无边框填色，以及多头区间的 3R/5R/10R 文字。

里程碑是走势达到提示，不是固定止盈。原风险倍数、跟随趋势的保护方法不变；空头保护价只向下收紧。当前仍只有一组活跃跟踪参考，其他确认不会强行重置它或反手。已有 V4 隐藏状态及用户批注保留。

## 数据与方法

这是一轮功能修改及软件回归，并非参数优化或收益回测。源代码及 builder 先提交于 `cb246cdd40c9a962e99d9f8525ecdea1bbde3d93`，再执行历史回归。

认证特征源：`experiments/active/exp-spike-burst-validation-20260910-v1/results/features/75d10ad8568817c52dd030fd1e6a.pkl.gz`，SHA256 为 `031d3e57bb6081002e3ce4484e999b90ca7759322e6e78da8d9d61173248d07a`。复用 PEPE 1H 已有数据；完整来源 3144 根，复核 UTC 2026-08-14 00:00 至 2026-09-09 00:00（右端不含）中的 624 根。产生 2 个多头、4 个空头确认。没有新标注、训练集、val 正类率或 val 样本。

Owner 已在当前会话授权任何时间段数据；**这是该配置第 1 次消耗 holdout**，仅已知案例软件回归，不能作为盲测或样本外盈利证据。

AUC、置换 p、top-decile 毛/净收益、胜率、随机入场收益对照、单特征收益基线不适用：本轮不计算经济结果。替代功能对照为：冻结多头时点、合成方向镜像、无旧候选/区间失效/数据缺口反例、任意前缀结果一致、空头风险方向和不借用止损后最低价的测试。镜像只用于软件测试，未用于 YOLO 图像增强或训练。

## 复现命令

在仓库根目录，使用现有锁定 venv 与上述已认证本地特征工件：

```bash
git show cb246cdd40c9a962e99d9f8525ecdea1bbde3d93:yoyo/evaluation/spike_v5_bidirectional_check.py
.venv/bin/python -m pytest -q tests/test_spike_burst_v5_structure.py tests/test_spike_v5_bidirectional.py tests/test_spike_burst_v4_release.py
.venv/bin/python -m yoyo.evaluation.spike_v5_bidirectional_check
.venv/bin/python scripts/md_to_html.py analysis/p1_spike_v5_bidirectional_20260910.md --out-dir analysis/html
```

原生验证需打开现有私有 V5，粘贴完整 `yoyo/evaluation/pine/spike_burst_v5.pine`、保存编译，检查 PEPE 1H 两个时间点和当前实例输入。已保存的回执为 `experiments/active/exp-spike-v5-bidirectional-20260910-v1/qa/tradingview_receipt.json`；历史回归逐事件结果在同目录 `results/regression.json`。

## 风险与诚实声明

29 项测试及少量原生案例只能证明本次功能和方向一致性，不能证明空头盈利、所有币种等价或噪音问题已解决。9 月 4 日原有不理想的多头形态仍在。本次没有放宽多头门槛或重新搜索参数。白色在浅色图表上对比度较低，此处按 Owner 指定颜色设置。少量原生与缓存样本不构成全历史逐根 parity。

本次没有改通知、后台服务、模型 ACTIVE 或执行配置，没有下单。后续若继续筛噪，需要单独定义成功/失败结构并做时间隔离验证；不把增加空头方向当成盈利结论。
