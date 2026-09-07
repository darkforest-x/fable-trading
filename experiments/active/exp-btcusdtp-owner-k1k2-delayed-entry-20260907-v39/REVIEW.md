# V39 独立保存结果复核

## 判定

**未发现阻断性的保存结果、记账或统计计算错误；可以发布为未通过验收的研究结果，不能宣称策略已赚钱。**

独立检查没有重跑价格路径，也没有读取其他实验的收益文件或 holdout。范围限本实验的 `summary.json`、`pre_outcome_receipt.json`、计划/配置、`summary.files` 列出的 19 份 CSV，以及主线明确授权的既有 `month_inference` 函数第 168–199 行。只写本 REVIEW，不修改代码、参数、数据或实盘配置，不提交。

## 证据完整性与分母

- 19 份 CSV 的 SHA256、行数和字段顺序全部与 summary 收据一致；三份事前请求/配对文件与 pre-outcome 收据一致。配置 SHA 为 `28d9057e78499a7ab8a2ca609b7a77b88310d981fb1374dcd940dc65a9f3063a`。
- 审计/最终复核提交均为 `1682629dc2c1ae1147b69b37db120c971f221027`；pre-outcome 时间早于最终结果时间。这是保存收据一致性，不是对原行情来源的再次独立审计。
- 保留 63 个 case、108 个 control，共 171 个不同事件；36 个原 matched case 每个恰好三个 control，27 个未匹配 case 未删除。control ID 和原 decision_time 均不复用；方向、原月份、UTC 六小时区间、fold 与 gap 的配对关系一致，控制使用各自时钟。
- 两轮成功完成的审查分别记录 1,265 和 283 个保存输出一致性断言，另核对 16 行 fold 表及 43 个同入口事件的精确执行一致性。这些不是 pytest 数量，也不能与主线报告的 **650 个聚焦 pytest** 相加。本次没有重新运行该 650 项测试，亦不声称全仓测试通过。

## 记账、时钟与单仓

真实输出中 immediate 的 63/108 个请求全部成交并闭合；delayed 为 38/65 个成交并闭合、25/43 个已知不成交，所有请求的经济状态均已知，没有未知请求或未闭合仓位。

逐请求核对：

- 已知不成交的 `request_net=request_gross=0`，没有成本；trade gross/net、入场价/时间和退出价/时间保持缺失，`closed=false`，没有伪造成零收益成交。
- 成交时 request 与 trade 收益相同，毛收益由保存的方向及入退场报价独立复算；`request_gross-request_net = 0.002 × filled`。风险百分比、ATR 风险距离也与原止损、ATR 和实际入场价一致。
- 所有原请求字段、原 E、止损、方向、ATR 保持；actual entry 在 `[E,E+60m)`，已确认 native5 seed 的 open+5m 等于入场边界。audit 共 307+610=917 行 trace，逐事件恰好覆盖 E 至终态的连续 5m 边界，且不越过 E+55m。
- 过期请求最后已知反向观察在 E+55m，政策过期记为 E+60m；等待触损优先，触损终态不读取新的边界 OPEN 或颜色。没有把 E+60m 的新确认算入可入场。
- `absolute_deadline=E+72h`，`remaining_minutes=4320−等待分钟`，实际退出均未越界。**真实样本没有 time_exit**；delayed case/control 最长持仓分别为 570/655 分钟。因此保存结果能核对截止算术与无越界，不能声称真实行情执行了 72h 超时分支；该边界属于合成测试证据。
- 独立按实际 entry_time、event_id 和占仓区间重建每臂账本：immediate case 59 笔选中、4 笔占仓跳过；delayed case 38 笔选中、25 个已知不成交。control 对应 106/2 与 65/43。各臂保留全部原行，未复用 baseline 接受名单，也未让 pending 预占仓。
- 当前真实样本没有未知，所以本次实际账本复算未触发 unknown blocking；不可借本次零 unknown 声称验证了所有未知分支。未知处理由另行合成测试覆盖。

16 个 case、27 个 control 在两臂入场时间相同；这些 43 个事件的入退场价/时间、outcome、毛净收益、风险与持仓时间逐值相同（忽略 CSV 因其他行缺失产生的整列 int/float dtype 差异）。

summary 另记录 immediate 与原 V37 的 63/108 行、18 字段 baseline parity，身份一致、数值容差 1e−12。**本复核未读取 V37 原收益文件，不能把这条 runner 自检冒称为 reviewer 对 V37 的独立重比。**

## 关键经济数字独立复算

单位 bp；全请求分母包含已知不成交，成交条件均值另列。

| 指标 | Immediate | Delayed |
|---|---:|---:|
| Case 全 63 请求平均净贡献 | −12.809320 | −5.764857 |
| Case 成交数 / 成交平均净收益 | 63 / −12.809320 | 38 / −9.557527 |
| Case 成交 PF / 净胜率 | 0.544556 / 15.87% | 0.684267 / 18.42% |
| Control 全 108 请求平均净贡献 | −22.180024 | −10.748909 |
| Control 成交数 / 成交平均净收益 | 108 / −22.180024 | 65 / −17.859725 |

Case delayed 全请求净贡献的 SD 为 68.032709bp、中位数 0、范围 [−122.394179, 401.223011]bp。中位数为零包含 25 个现金贡献零，不是“典型成交保本”。未删尾或换检验来追求显著。

### 改善主要来自哪里

全 63 case 的净改善 **+7.044463bp = 毛变化 −0.892045bp + 省成本 +7.936508bp**。因此不能把“少亏”归因为毛交易优势已提高。

- 真正延迟后仍成交的 22 个 case：baseline 平均 +19.564689bp，延迟后 −0.765418bp，平均恶化 −20.330107bp；这组没有省成本。该后验分组只用于归因，不是可在原 E 预知的筛选标签。
- 25 个放弃请求：baseline 平均 −35.642541bp，改为现金零。其中 15 个等待触损对应的 baseline 在本次回放确实都是净亏，平均 −47.847405bp；另 10 个过期请求含 **3 个 baseline 盈利、7 个亏损**，不能说全都避开亏损。
- 同一入口的 16 个 case 没有变化。整体 25 个改善、22 个恶化、16 个不变，与保存诊断表一致。
- Control 全请求也改善 +11.431116bp。不能直接拿全 63 case 与全 108 control 的改善差作为原 36 组配对效应，两者分母不同。

## 原 36 组主效应与统计方法

没有采用只保留“case 和三个 controls 都成交”的九组子集。每个原 matched case 都保留，已知不成交按政策贡献零，三个 control 等权 1/3；本次 36 组全部已知。

令 `D` 为 delayed request contribution，`I` 为 immediate contribution，逐组主量为：

`X_j = (D_case − I_case) − (Σ三个control的(D_control − I_control))/3`。

独立从四份 trade 表及原 control→case 关系重建，逐组与 paired_contrasts 相等：

- 平均净增量 **+2.330101982947bp**，SD 36.366971bp，中位数 0。
- 95% 区间 **[−8.292696660193, +13.756494515234]bp**；单侧月符号翻转 **p=0.3796**，与 summary 逐值相同。
- Delayed 绝对配对净超额 **+0.894543387026bp**。
- 净增量拆分：毛增量 **+0.848620501466bp**，相对省成本 **+1.481481481481bp**；不能把配对省下的成本再扣一次或称纯入场 alpha。

四半年原组数为 16/12/6/2，平均净增量分别为 **+20.641722 / −20.767887 / +8.565739 / −24.281835bp**；不是各时段一致改善。

### 精确继承的抽样合同

这里不是 IID 交易 bootstrap，也不是任意跨半年的 24 月抽样：

1. 按原 case decision_time 的 UTC 月将完整 matched set 聚类，固定 2023-01…2024-12 共 24 月；空月的 sum/count 为 0。记月和 `S_m`、月事件数 `N_m`，本次 17 个活跃月、总 36 组。
2. 新建 `Generator(PCG64(20260906))`。**先**取 `choice([-1.0,1.0], size=(9999,24))`。以月和为统计量：`p=(1+count(sum(sign*S)>=sum(S)))/10000`，包含等号与 +1 校正。
3. 使用同一 RNG 的后续状态，依次对 `[0,6)`、`[6,12)`、`[12,18)`、`[18,24)` 各抽 `(9999,6)` 个整数索引，拼成 `(9999,24)`。每次抽样均保留每半年六个月槽位。
4. 每次均值是 `sum(S[indices])/sum(N[indices])`，不是月份均值的均值，也不是固定除以 36；只排除零分母重抽结果。本次零分母次数为 0。取 `.025/.975` 分位数。

最初用“先 bootstrap、跨全部 24 月重抽”的通用实现得到不同区间；核对授权的冻结函数后，确认那不是继承合同。上述精确方法独立复算吻合，**没有发现原实现的数值错误，也没有改换检验**。计划中缩写的 inherited monthcluster 不足以单独复现，故在本 REVIEW 补全调用顺序和分层规则。

该方法仍依赖月误差对称/独立及半年内月份可交换的近似；月滞后相关记录约 0.325，重复 K1、跨月持仓和反复使用开发集限制因果与样本外解释。本 p 值是探索性结果，不能解释为策略获利概率或“已证明无效”；不显著也不等于等效。

## Gate、单特征与诚实限制

所有保存 gate 可从输出重建。Delayed case 仅 38 个成交，四半年 12/15/8/3；17 个活跃月、各半年 4/6/4/3 月。全请求四半年净贡献为 −6.504515 / +5.852332 / −20.821754 / −2.075029bp。

因此成交数≥80、每 fold≥12、四 fold 均正、整体净收益正、PF≥1.1、匹配覆盖≥90%、主 p<.01 和主 CI 下界>0 均未通过。原匹配覆盖仅 36/63=57.14%；固定 63 cohort 不可能通过 80 成交门，不能事后降低门。活跃月、全部配对已知及 delayed 绝对超额为正通过，不抵消其他失败。

继承 body_ratio 单特征的描述数字也复算一致：AUC 0.494898，top-decile 7 个请求，平均净 +46.313594bp，但仅 1 个盈利；零贡献请求在 AUC 中属于“非正收益”，不是亏损成交。这不是新的验证集或筛选器成功证据。

本轮“不读新行情”的复核无法重新确认实际 bar 内路径、源文件真实性或原 V37 全量 parity；650 个合成/聚焦测试与保存结果复算是互补而非互相替代的证据。不安装依赖、不训练、不读取新 holdout、不更改 TradingView 或交易执行。结果支持继续诚实报告未过门，不能据此部署或宣称最优。

## 最小独立主量复算命令

在仓库根使用既有环境；只读本实验已登记 CSV，不调用研究统计函数或回测引擎：

```bash
.venv/bin/python -B - <<'PY'
from pathlib import Path
import hashlib, json
import numpy as np
import pandas as pd

experiment = Path('experiments/active/exp-btcusdtp-owner-k1k2-delayed-entry-20260907-v39')
base = Path('data/owner_k1k2_delayed_entry_v39')
summary = json.loads((experiment / 'summary.json').read_text())
def read(name):
    path = base / (name + '.csv')
    meta = summary['files'][str(path)]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == meta['sha256']
    frame = pd.read_csv(path, float_precision='round_trip')
    assert len(frame) == meta['rows'] and list(frame) == meta['columns']
    return frame.set_index('event_id')

case_i, case_d = read('immediate_case_trades'), read('delayed_case_trades')
ctrl_i, ctrl_d = read('immediate_control_trades'), read('delayed_control_trades')
requests, assignments = read('control_requests'), read('assignments')
values = []
for mother in assignments.index[assignments.match_status.eq('matched')]:
    ids = requests.index[requests.parent_event_id.eq(mother)]
    assert len(ids) == 3
    assert case_d.at[mother, 'request_known'] and case_i.at[mother, 'request_known']
    assert ctrl_d.loc[ids, 'request_known'].all() and ctrl_i.loc[ids, 'request_known'].all()
    value = ((case_d.at[mother, 'request_net'] - case_i.at[mother, 'request_net'])
             - (ctrl_d.loc[ids, 'request_net'] - ctrl_i.loc[ids, 'request_net']).sum() / 3)
    values.append((case_i.at[mother, 'decision_time'], value))
frame = pd.DataFrame(values, columns=['decision_time', 'value'])
assert len(frame) == 36 and frame.value.notna().all()
frame['month'] = pd.to_datetime(frame.decision_time, utc=True).dt.strftime('%Y-%m')
months = pd.period_range('2023-01', '2024-12', freq='M').astype(str)
group = frame.groupby('month').value.agg(['sum', 'count']).reindex(months, fill_value=0)
sums, counts = group['sum'].to_numpy(float), group['count'].to_numpy(float)
rng = np.random.Generator(np.random.PCG64(20260906))
signs = rng.choice([-1.0, 1.0], size=(9999, 24))
p = (1 + ((signs * sums).sum(axis=1) >= sums.sum()).sum()) / 10000
indices = np.concatenate([rng.integers(a, a + 6, size=(9999, 6))
                          for a in [0, 6, 12, 18]], axis=1)
den = counts[indices].sum(axis=1)
boot = sums[indices].sum(axis=1)[den > 0] / den[den > 0]
print('mean bp:', frame.value.mean() * 10000)
print('95% CI bp:', np.quantile(boot, [.025, .975]) * 10000)
print('one-sided p:', p)
PY
```
