# 勘误：BTCUSDT.P 1h Owner Causal V2 的 holdout 读取声明

日期：2026-09-04<br>
原报告：`analysis/p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904.md`<br>
影响实验：`exp-btcusdtp-1h-owner-causal-v2-preholdout-20260904-v1`

## 勘误结论

原报告关于 `data/kline_deep/okx_BTC_USDT_SWAP_15m_158499.csv` “物理截止于 2026-02-28、holdout 读取 0 行”的声明是错误的。

2026-09-04 为 15m / 5m 新实验做 fail-closed 源预检时，实际解析该文件的时间戳最大值已经到达项目 holdout（起点 `2026-05-04T00:00:00Z`）之后。旧 1h 脚本会先对整文件执行 SHA256，再由 `resample_hourly()` 整表读取 CSV，最后才按 `safe_end` 截取，因此即使特征和收益只使用 2026-02-28 之前的行，**读取行为本身仍然接触了 holdout 价格**。

据此更正：

- `holdout_consumed: false` → **`true`**；
- “holdout rows read = 0” → **错误，不能成立**；
- 记为该 1h 配置的**意外 holdout 访问 #1**；当时没有逐次授权；
- 原报告的收益数字不因本次勘误被重新计算，但不再具有“未触碰 holdout 的纯预留验证”证据资格；
- 该规则原本已经因成本后收益为负而 `rejected`，状态保持 `rejected`，不得据此 promote、部署或实盘。

## 为什么数字可能不变、证据资格仍然变化

旧脚本在整表读入后、生成 1h 特征与交易之前，按 `validation_end_exclusive` 过滤，所以 2026-05-04 之后的行没有进入已报告的信号或 PnL。由此可推断原数值大概率仍对应 pre-holdout 前缀；但项目铁律约束的是“不得读取”，不是只约束“不得用于计算”。因此数值不变不能修复违规读取，也不能恢复 OOS 身份。

## 发现、隔离与替代源

- 发现收据：`experiments/active/exp-btcusdtp-k1k2-15m-5m-params-preholdout-20260904-v1/results/preflight_failure.json`；新实验在只完成时间戳预检、尚未计算任何信号或结果时停止并废弃。
- 替代源：OKX 官方月度 1m 归档因果聚合出的独立 5m 文件，物理末端 `2026-02-28T15:55:00Z`，SHA256 `767f67c2b0ae5a8c83369a7cb950334e61de09edbb82a0158122c41794eed5ac`。
- 替代源验证：39/39 月份成功，物理 holdout 行为 0；本次 15m / 5m v2 实验只从该安全源派生。

## 零假设对照与适用范围

这是一项数据访问纪律勘误，不是方向策略收益实验，因此 AUC、top-decile、匹配随机入场和收益置换检验不适用。严格零假设是“当前被打开的源物理末端早于 holdout”；旧源预检明确拒绝该假设。安全替代源则以文件物理末端和月度归档清单独立通过该门。

## 风险与诚实声明

- 这是事后发现并主动更正的访问纪律事件，不能回写成当时已知。
- 没有重新打开 1h 验证、没有利用意外读到的 holdout 调参，也没有训练、promote、ACTIVE/frozen/forward 变更、部署、消息或订单。
- 原报告其余交易统计暂不改写；引用原报告时必须同时附上本勘误。

## 复核命令

```bash
python3 -m pytest tests/test_fetch_okx_archives.py tests/test_optimize_btcusdtp_k1k2_intraday_preholdout.py -q
python3 scripts/md_to_html.py \
  analysis/p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904_erratum.md \
  --out-dir analysis/html
```
