# V23：整体 K1 入口背景对照支持审计

## 问题与边界

这是新的**整体 K1 入口相对背景**问题，不是修复 V10，也不把 V10 的 154/251 覆盖追认为通过。V10 同时匹配小时/5 分钟颜色和小时斜率；本合同仅匹配同 BTCUSDT.P、同 decision month × UTC 六小时块 × 因果波动桶。方向沿用母信号。移除的三个状态不再固定，因此将来的差异不能被解释为独立于这些状态的“纯 K1 形态效应”。本轮只回答安全背景供给能否形成完整三控制组，不产生价格标签、收益、胜率或盈利结论。

## 冻结输入与时钟

仅读取已保存且按完整字节 SHA256 锁定的 V10 `results/matching_frame.csv.gz`、V10 `started.json` / `support_frozen.json` / `summary.json`，以及 V4 `results/original_mothers.csv.gz`。精确路径和 SHA 列于同目录 `config.json` 与 builder 的 `INPUTS`；运行时先校验 SHA，再校验 V10 三份收据的一致性、源代码在旧 builder commit 的字节、commit 早于原运行、保存表哈希、原母群和价格截止。V10 的物理归档时钟可以晚于 2024，但其实际 materialized prefix 必须严格早于 `2025-01-01T00:00:00Z`，且 holdout price rows 为零。V23 不打开原始归档，不声称独立重算了 raw5 → 1h 或特征。

保留原 251 母的所有字段、行序与身份；四半年母数固定为 55、66、55、75。母 signal_time 为小时开盘，decision_time 为下一小时开盘。每个半年要求 `start <= decision_time < end - 72h`，没有母前后 ±72h 的新增排除。控制可以晚于同月母时间：这是离线同期支持，不是在线可取得的随机分配。

## 唯一新合同

- 精确匹配键仅 `month, utc_6h_bucket, vol_bucket`；不跨币、不跨月、不跨 UTC 六小时块、不跨波动桶。
- 波动桶沿用 V10 的 signal-hour ATR/close 对此前 720 小时（最少 168）的 1/3、2/3 分位，当前小时不进入分位样本，不新增阈值。
- 原 `matching_support` 合法性全部保留：波动桶、ATR、已知真实 entry open、原始源连续性、5 分钟颜色和小时颜色/斜率可用性。虽然颜色/斜率不再是配对键，未知支持不会因此被补成已知。
- 原当前/前一小时 strict-body-cross 排除、真实母 decision 排除全部保留；不增加未来穿线排除。
- 风险只按母 `direction*(own_entry_open-initial_stop)/signal_atr` 转移到控制自己的 entry open 和 ATR。合成止损必须有限且正；真实信号 OHLC、MA、颜色、斜率和 ATR 来自控制自己的小时，不能转移母数据。
- 一个控制的身份是 UTC decision timestamp，与方向无关；所有组间不复用。每母完整三控制或零控制，保留所有母和逐层缺失原因，不能以部分组计算覆盖。

## 容量求解与停止门

先构造全部 admissible edges 和逐母分阶段供给；写出 `support_frozen.json` 后，才对每个二部连通组件调用现有 `maximum_complete_matching(count=3,time_limit=30.0)`。每个组件最多 30 秒；必须有最优状态、二元分配、目标/对偶和零 gap 的现有证书。任何超时或非最优一律失败，不能回退贪心、复用时间或修改键。

通过门预定为 **至少 226/251 完整母组**（90% 向上取整）。不足则终止为支持不足，不生成标签，不尝试替代键或数量。分段覆盖全部报告，但不按分段结果选实验。

求解器按 ID 排序且仅最大化完整组数；输出 allocation 是**最大容量可行见证**，不是均匀随机对照抽样，也不是经济最优样本。本轮 seed 不使用，不虚构随机过程。后续标签研究必须在看 markout 前另行注册、冻结与覆盖一致的固定种子随机选择合同，或明确采用非随机对照并预注册其敏感性分析；不能直接把本轮见证当作随机经济推断样本。

## 接口与保存证据

`build_support_graph(mothers, matching_frame)` 是无 I/O 纯函数，输出 `original_mothers`、`matching_frame`、`mother_support`、`stage_counts`、`eligible_edges`。`allocate_support(graph)` 独立分配并输出 `allocation`、`controls`、`assignments`、`component_capacity`、`fold_coverage` 和 summary。全部表保存为同名 `.csv.gz`。

CLI 在首次读取输入前检查自身代码、测试、配置、计划和容量 helper 已提交，拒绝任何已有 `results` 目录。`started.json` 保存当前 commit/source/input pins；冻结图收据在求解前；成功 summary 保存输出哈希和证书；错误保留 `failure.json` 及此前检查点。其他工作区 dirty 文件不被回滚。旧报告和旧输入不被覆盖。

母支持未知或风险无效而尚未开始搜索时，available_controls 和未到达的阶段数保存为空值，不冒充已知零。公共 allocation 接口在求解前从原始事前输入重建母支持、阶段数和每条 edge，精确拒绝 candidate_time、fold、stop 等资格字段篡改。所有 JSON 的非有限值序列化为标准 null；收据时钟先解析为 UTC 再比较。

## 后续诊断（本轮不实施）

若支持门通过，另注册固定入口持续性标签审计：主时钟 4h，1/12/24h 仅描述；保留全部 251，不依赖旧退出、未来盈利或 MFE 选择样本。确切窗口端点、成本、缺口未知、对照抽样和统计合同必须在标签前单独冻结。现有 72h fold embargo 保持。本轮不授权该标签运行、不读取 2025 年以后价格或 holdout；production/training eligibility 均为 false。

## 合成验收与复现

先执行（不读取真实数据）：

```bash
.venv/bin/python -m pytest -q tests/test_hourly_impulse_background_support.py tests/test_hourly_impulse_matching_capacity.py
```

必须覆盖：三键而不是六键、母风险差异、双方向共享同一控制时间、完整三组、缺失键不能 null 匹配、当前/前一穿线、实际母排除、fold 严格 72h、原字段保留、source/clock/flag 变异拒绝、逐组件精确容量、求解失败留收据、提交守卫先于读表、支持冻结先于求解、拒绝覆盖。所有测试仅合成 fixtures。

主代理审查、提交 builder 后，才允许唯一 saved-support 命令：

```bash
.venv/bin/python -m yoyo.evaluation.hourly_impulse_background_support
```

该命令不会打开 raw 归档或计算任何收益。没有通过支持不等于证明没有盈利；通过支持也不等于证明有入场优势。代码测试通过只证明所覆盖的实现合同，不能充当经济证据。

## 技术来源

使用仓库既有 pandas 2.3 / SciPy 1.13.1，不安装依赖。pandas 官方说明 null join 会相互匹配，本实现显式阻断缺失母键；SciPy MILP 的可行但非最优返回不能作为精确容量。依据：

- https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
- https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.optimize.milp.html
- `yoyo/evaluation/hourly_impulse_k2_matching.py` 的原始特征/排除定义与 V10 源收据。
- `yoyo/evaluation/hourly_impulse_matching_capacity.py` 的二元完整组与证书校验。
