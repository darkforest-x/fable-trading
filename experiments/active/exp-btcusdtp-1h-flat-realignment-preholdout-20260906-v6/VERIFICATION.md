# V6 独立保存结果复核

日期：2026-09-06。结论：**保存结果及实验完整性检查 PASS；策略经济验收 REJECT。**
本复核由独立审阅者只读检查已保存的 V4/V5/V6 请求、母账本、交易、诊断、摘要和源码；
不重新打开原始价格，不运行新的回测，不调整规则。仅新增本文件；主报告和元数据由主执行者维护。

## 来源与中断尝试

- 完成运行的 builder：`824d2d1e771d6695491d07a992668feef9095462`。
- 提交时间 `2026-09-06 12:03:15+08:00`，`results/started.json` 时间
  `2026-09-06 04:03:15.759919+00:00`；提交先于运行 0.759919 秒。
- `summary.json` 的 14 个源码/配置/计划 SHA256 均独立重算，并同时匹配当前文件和该提交中的字节。
- 配置钉住的 6 个 V4 输入及 4 个 V5 输入 SHA256 全部匹配。
- 第一次运行仍算一次 development 价格读取，不能称为未接触数据，详见 `ATTEMPTS.md`。
  `attempt_01_parity_serialization/` 共9文件；除首次 started receipt 外的8个派生文件，
  与完成运行的同名 context/request/status/flat-diagnostic 文件**逐字节完全一致**。
- 修复仅将混合 Timestamp/NaT 对象列和 CSV 日期字符串统一为 UTC，未删除比较列；
  纳秒变化仍会失败。此修复没有改变样本、入场、风险、退出或经济参数。

## 全列基线与时钟

| 保存结果比较 | 行数 | 比较的原有列数 | 结果 |
|---|---:|---:|---|
| V5 → V6 immediate 案例交易 | 55 | 80 | 全列一致 |
| V5 → V6 immediate 对照请求/结果 | 89 | 89 | 全列一致 |
| V5 → V6 immediate 案例母账本 | 251 | 40 | 全列一致 |
| V5 → V6 immediate 对照母账本 | 462 | 49 | 全列一致 |

初始已同向的22个案例请求和13个对照请求，新旧结果全部原有列一致。
仅归一化序列化时间/列类型，数值比较容差 `rtol=atol=1e-12`；时间点仍精确比较。

逐笔完成以下独立算术/账本检查：

- 实际 `entry_time == decision_time`；总等待在0–480分钟、精确5分钟栅格；
  `wait_hours` 与 UTC 时间差一致；新请求的 `total_wait_minutes` 亦一致。
- 新确认 `confirmation_bar_open + 5min == confirmation_available_at == entry_time`；
  所有实际新入场均已同向。
- `mother_deadline == mother_decision_time + 4320min`，每笔退出不超过母截止。
- 每笔 `initial_stop/signal_atr/direction` 与原始请求一致；实际风险按新入场价重算，
  硬止损填价等于固定 `initial_stop`；无部分平仓。
- 所有230笔完成交易满足 `gross_return = direction * (exit_price/entry_price - 1)`，
  且 `gross_return - net_return == 0.002`（数值容差不超过1e-14）。
- 真翻色退出的前一管理bar、当前管理bar严格相邻5分钟，当前bar结束可用时间等于实际退出时间。
- 所有母收益都与对应完成交易吻合；已知未入场为零，未知值未被填零。

本轮不重读原始价格，因此上述确认时钟检查是**保存字段及源码契约检查**，不冒充对每个市场
bar的再次独立取价或颜色重算。原始档案 SHA `767f67c2...94eed5ac`、前缀219551行到2024年末、
holdout0行仅核对已保存 receipt；没有在本复核中重新扫描原始档案计算哈希。

## 完整分母及收益

| 策略/角色 | 请求 | 完成交易 | 母数 | 已知未交易零值 | 未知/删失 | 每完成交易净bp |
|---|---:|---:|---:|---:|---:|---:|
| immediate 案例 | 55 | 55 | 251 | 196 | 0 | -22.448892 |
| flat alignment 案例 | 47 | 46 | 251 | 205 | 0 | -30.423771 |
| immediate 对照 | 89 | 68 | 462 | 394 | 0 | -7.272629 |
| flat alignment 对照 | 67 | 61 | 462 | 401 | 0 | -17.877111 |

共258个请求结果、230笔完成交易、28条无效风险拒绝结果；四份母账本共1426条且全部已知。
新案例从原55请求中8条等待到期、1条首次尝试风险无效，剩46成交；
新对照从原89请求中22条到期、6条首次尝试风险无效，剩61成交。
原21个无效风险对照中，7个在新的首次确认价合法成交：**没有先按旧成交结果删除这些对照**。

案例每母净收益 immediate `-4.919080bp` → alignment `-5.575671bp`；
每完成交易胜者8/55 → 3/46，PF `0.245179` → `0.172180`。
新策略四半年分别12/12/12/10笔，均值 `-51.1100/-29.4047/-14.0189/-26.5090bp`，全部为负。
硬止损11→4不是盈利改善的证据；交易数和入场价同时改变。

## 配对机会与执行账本

- 全251母中33条收益改变，16改善、17恶化、218不变；改变全部来自原K2已反色组。
- 46个共同成交母的时机效应为 **-1.134270bp/原母**；9个参与变化母贡献
  **+0.477679bp/原母**，合计 **-0.656591bp/原母**。
- 9个未再成交的原机会：避开6个原亏损，共187.639744 event-bp；也漏掉3个原赢家，
  共67.742347 event-bp。另2个原赢家在新实际交易中转为非正，故原赢家变非正共5个；
  原亏损变赢家为0。
- 46个共同成交的旧均值 `-24.234601bp`，新均值 `-30.423771bp`。
  其中22个同刻入场恒等；24个真正延后且仍成交的案例，等待中位50分钟，
  沿交易方向的不利入场价格变化中位10.215554bp。此为事后时机描述，不是可直接选样的特征。
- 独立按母时间排序重算单挂单/单仓：immediate 接受250母、55笔成交；
  alignment 接受249母、45笔成交，分别与保存单仓账本和摘要一致。
  单仓每完成交易净均值 `-22.448892bp` → `-30.091033bp`；不是复利组合回报。

## 对照与不确定性

两臂均154/251母有完整3个对照，覆盖 **61.354582%**，没有改用成交笔数作覆盖分母。
以下每项均为匹配母意图收益，不是每完成交易收益：

| 策略 | 匹配案例bp | 对照bp | 超额bp | 单侧月簇p |
|---|---:|---:|---:|---:|
| immediate | -2.383540 | -1.070430 | -1.313109 | 0.6897 |
| alignment | -3.188373 | -2.360398 | -0.827975 | 0.6155 |

独立从保存的251母配对差重算24个月簇、9999次、seed20260906：
95%比率自助区间 `[-1.946484926, +0.453573610]bp/母`，单侧符号翻转 `p=0.8377`。
两条 matched p 也独立重算一致。上述区间并不消除跨月依赖、既往多轮搜索和历史复用。

## 边界与诚实声明

- 本轮只有机制检验；46笔<80、61.35%覆盖<90%，净收益、四折、PF和对照证据也均未通过。
  不能因硬止损减少而宣布成功，不新增 audit、holdout、部署或实盘授权。
- `*_flat_path_diagnostics.csv` 只覆盖已发出确认的47个案例/67个对照请求；
  其中等待期触及旧K1极值分别6/20个。不得把它解释为全部过期/删失等待机会的触线比例。
- 新入场全同向是规则构造结果；应使用 `original_k2_ltf_state` 解释原机会差异。
- 本复核没有重跑合成测试；相应测试执行凭据由主执行者单独保存。
- 未进行浏览器/手机视觉QA；本文件只确认保存数据、代码来源及计算契约。

## 复现入口（只读保存结果）

主回测复现命令在 `PROJECT_PLAN.md`；本复核不调用它。核心独立检查可从仓库根目录运行：

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
import hashlib, json, subprocess
from pathlib import Path
import numpy as np
import pandas as pd
p = Path('experiments/active/exp-btcusdtp-1h-flat-realignment-preholdout-20260906-v6')
s = json.loads((p/'results/summary.json').read_text())
c = json.loads((p/'config.json').read_text())
commit = json.loads((p/'results/started.json').read_text())['builder_commit']
sha = lambda b: hashlib.sha256(b).hexdigest()
for item in s['sources']:
    assert sha(Path(item['path']).read_bytes()) == item['sha256']
    assert sha(subprocess.check_output(['git','show',commit+':'+item['path']])) == item['sha256']
for directory, key in [('parent_results','inputs'), ('previous_results','previous_inputs')]:
    for name, value in c[key].items():
        assert sha((Path(c[directory])/name).read_bytes()) == value
for f in (p/'attempt_01_parity_serialization').iterdir():
    if f.name != 'started.json':
        assert f.read_bytes() == (p/'results'/f.name).read_bytes()
for arm in c['arms']:
    for role in ['case','control']:
        t = pd.read_csv(p/'results'/f'{arm}_{role}_trades.csv.gz')
        t = t.loc[t.closed]
        assert np.allclose(t.gross_return-t.net_return, .002, rtol=0, atol=1e-14)
        assert np.allclose(t.gross_return,t.direction*(t.exit_price/t.entry_price-1),rtol=0,atol=1e-12)
        deadline = pd.to_datetime(t.mother_decision_time,utc=True)+pd.Timedelta(minutes=4320)
        assert (pd.to_datetime(t.mother_deadline,utc=True) == deadline).all()
        assert (pd.to_datetime(t.exit_time,utc=True) <= deadline).all()
print('saved-result provenance, attempt parity, cost and deadline PASS')
PY
```
