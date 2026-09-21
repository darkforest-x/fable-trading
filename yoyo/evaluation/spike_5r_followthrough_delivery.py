"""Render all frozen V6 results and bind the evidence; never tune a rule.

This delivery step authenticates the evaluation summary, replay receipts and
their leaves. It does not recompute price paths, choose thresholds or fit a
model. Passing research arms require separate serial verification.
"""
from pathlib import Path
import json
import subprocess

import pandas as pd

from yoyo.evaluation import spike_10r_search as search
from yoyo.evaluation.spike_5r_followthrough import EXP
from yoyo.evaluation.spike_v8_six_filters import _committed

REPORT = Path('analysis/p1_spike_5r_followthrough_20260921.md')
NOTION = 'https://app.notion.com/p/3e28856479af81658827e1c09d4efcbd'


def fmt(value, digits=2):
    return 'N/A' if pd.isna(value) else f'{value:.{digits}f}'


def pct(value):
    return fmt(value * 100) + '%'


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |',
                      *['| ' + ' | '.join(map(str, row)) + ' |' for row in rows]])


def main():
    if not _committed([Path(__file__)]):
        raise ValueError('commit delivery builder before generating evidence')
    target = EXP / 'delivery_manifest.json'
    if REPORT.exists() or target.exists():
        raise ValueError('refuse to overwrite a delivered report')
    result = EXP / 'results_v1'
    summary = json.loads((result / 'summary.json').read_text())
    run = EXP / 'run_v1'
    if search.digest(run / 'manifest.json') != summary['source_manifest_sha256']:
        raise ValueError('replay manifest drift')
    manifest = json.loads((run / 'manifest.json').read_text())
    leaves = 0
    for root, files in [(result, summary['files']), (run, manifest['files'])]:
        for name, sha in files.items():
            if search.digest(root / name) != sha:
                raise ValueError(f'aggregate drift: {root / name}')
    for key, sha in manifest['receipts'].items():
        folder = run / 'streams' / key
        if search.digest(folder / 'completion.json') != sha:
            raise ValueError('stream receipt drift')
        rec = json.loads((folder / 'completion.json').read_text())
        if rec['identity'] != manifest['identity']:
            raise ValueError('stream identity drift')
        for name, expected in rec['files'].items():
            if search.digest(folder / name) != expected:
                raise ValueError('stream leaf drift')
            leaves += 1
    for dependencies in (manifest['dependencies'], summary['dependencies']):
        for path, sha in dependencies.items():
            if search.digest(Path(path)) != sha:
                raise ValueError('source dependency drift')
    if summary['passed_arms']:
        raise ValueError('passing arms need serial verification before final delivery')
    outcomes = pd.read_csv(result / 'comparison.csv.gz')
    parents = pd.read_csv(result / 'parent_comparisons.csv.gz')
    ranking = pd.read_csv(result / 'ranking.csv.gz')
    groups = pd.read_csv(result / 'path_groups.csv')
    flags = pd.read_csv(result / 'gate_flags.csv.gz')
    later = outcomes.loc[outcomes.period.eq('later')].set_index('arm')
    base = later.loc['original']
    best = later.drop(index=['original', 'old_risk_low10']).sort_values('precision', ascending=False).iloc[0]
    old = later.loc['old_risk_low10']
    full = outcomes.loc[outcomes.period.eq('full')].set_index('arm').loc['original']
    text = f'''# SPIKE V6：全原始信号的路径、等待确认、环境与两次加仓

## 结论

49,207个原始多头请求、3,169条非空行情流全部回放完成；原始3,531流中362条没有请求，并非运行失败。16个预先固定的新方案中，**0个通过全部研究门**。原版和旧风险十分位另作为2个基线，合计18臂。

后段原版最终净>5R为{int(base.gt5)}/{int(base.closed)}={pct(base.precision)}；新方案中点估计最高为`{best.name}`：{int(best.gt5)}/{int(best.closed)}={pct(best.precision)}，均净{fmt(best.mean_net_bp)}bp；旧风险基线{int(old.gt5)}/{int(old.closed)}={pct(old.precision)}，均净{fmt(old.mean_net_bp)}bp。排名是结果描述，未据此调阈值或选择生产版本。未验证Owner设想的30%高R胜率。

## 数据、口径与预注册

- 全期2024-09-10至2026-09-10 UTC，切点2025-09-10。earlier要求实际入场和已知退出都在切点前；later要求实际入场在切点后；跨界及早期未决另列。取消请求按原定决策时刻归期。
- 原版全期已结束{int(full.closed)}，删失{int(full.censored)}，严格净>5R {int(full.gt5)}，严格净>10R {int(full.gt10)}；这些是独立候选路径，重叠请求不等于串行账户持仓。
- 等待/确认保留原信号SL绝对价，以真正后来次开价重算初始R。仅用已经完成的等待K线检查止损、反向信号与断档；不会拿入场K线未来low取消订单。保留原2R收盘启动/4ATR保护、原反向退出及20bp名义往返成本。
- BTC同交易所同原生周期；4h只读应当已完成且组件齐全的桶。缺失保留unknown。平台/风险基线/十分位阈值只读此前3个完整UTC月、同周期至少100个已知特征，不读收益标签。
- structure0/structure2均用已有100U资金、1U毛初始风险、1倍敞口上限的结构管理；只改变最多加仓0或2次。这不是100U爆仓止损模式，也没有最大杠杆收益结论。
- 每个有效事件一次确定性匹配随机时点，同流/实际决策UTC月/信号ATR桶/时间段；排除所有原始V9多头信号。对照无效或删失不重抽。特征筛选复用原时点随机对照，未强制随机时点同时满足该特征，故不能宣称完全排除了市场状态影响。
- 16新臂在各时段对匹配随机的净收益月块p做Holm；研究门只用later。门还要求至少250闭合、20个>5赢家、10%原赢家保留、6月20资产、均净bp正、命中率高于两个基线、相对原版率差95%下界正、Holm p<0.01。未事后放宽。

## 为什么没有兑现5R：原退出前的价格路径

已扣同一20bp；退出根内先到high还是先止损未知时只记上界。开盘反向/跳空退出后该根high不计为可见利润。删失不当失败。`gaveback_confirmed`证明过去有价格机会，仍不证明能事先知道并成交在峰值。

'''
    text += table(['时段', '路径分类', '数量'], [[r.period, r.path_group, r.n] for r in groups.itertuples()])
    path_counts = groups.loc[groups.period.eq('full')].set_index('path_group').n
    reached = int(path_counts['gaveback_confirmed'] + path_counts['realized_gt5'])
    possible = reached + int(path_counts['exit_bar_ambiguous'])
    text += f'\n\n全期{int(full.closed)}笔已结束中，{int(path_counts["never_observed_gt5"])}笔（{pct(path_counts["never_observed_gt5"] / full.closed)}）在原退出前连上界也未超过净5R；明确有过机会{reached}笔（{pct(reached / full.closed)}），其中最终兑现{int(full.gt5)}笔。这说明选信号仍是大头，同时存在可研究的利润回吐。即使把退出根次序模糊也算入，原路径到过净5R的上界只有{possible}笔（{pct(possible / full.closed)}）；这是固定原退出窗口的诊断，不是新退出策略的收益上界，也不是可兑现率。更晚退出会改变路径，必须另测。\n'
    text += '\n## 特征可用性\n\n缺失未默认为通过。以下是全49,207请求的已知数/未知数/筛入数，筛入后的收益与随机对照在下一节完整列出。\n\n'
    text += table(['筛选', '已知', '未知', '筛入'], [[arm, int(g.known.sum()), int((~g.known).sum()), int(g.selected.sum())]
                  for arm, g in flags.groupby('arm')]) + '\n'
    text += '\n\n## 全部方案与匹配随机对照\n\n均毛/净bp以初始名义仓位为分母，不是账户收益。普通胜率是净R>0；5R命中率分母为各臂已结束交易。保留率是同event_key原>5赢家交集。随机均值/5R率只含成功配对且双方已结束的样本，因此另报配对数；不能用全样本均值相减代替配对差。\n'
    for period in ['full', 'earlier', 'later', 'crossing_or_unresolved']:
        part = outcomes.loc[outcomes.period.eq(period)]
        text += f'\n### {period}\n\n'
        text += table(['方案', '请求/取消/未决', '闭合', '>5/>10', '>5率', '普通胜率', '保留率', '均毛/净bp', '随机配对', '随机>5率/均净bp', '配对净差bp', 'Holm p'], [
            [r.arm, f'{r.events}/{r.invalid}/{r.censored}', r.closed, f'{r.gt5}/{r.gt10}', pct(r.precision), pct(r.win_rate), pct(r.recall),
             f'{fmt(r.mean_gross_bp)}/{fmt(r.mean_net_bp)}', r.matched_pairs,
             f'{pct(r.control_gt5 / r.matched_pairs) if r.matched_pairs else "N/A"}/{fmt(r.control_mean_net_bp)}',
             fmt(r.paired_excess_net_bp), fmt(r.random_net_holm_p, 6)] for r in part.itertuples()]) + '\n'
    text += '\n## 确认、等待与加仓的父比较\n\n以下p是5个预声明父比较各自的原始月块p，未做父比较族校正，只作描述，不用于通过研究门或宣称确认性显著。仅比较双方均闭合的同一事件；取消的覆盖损失另列。确认方案本质上筛掉等待方案中的部分单子，同事件都通过时路径相同，配对差为0并不代表筛选无效。结构0对原版隔离结构止损变化；结构2对结构0隔离加仓。\n\n'
    text += table(['时段/比较', '左/右闭合', '左/右>5', '配对数', '配对均净差bp', '原始p'], [
        [r.period + '/' + r.comparison, f'{r.left_closed}/{r.right_closed}', f'{r.left_gt5}/{r.right_gt5}', r.paired_closed_events,
         fmt(r.paired_mean_net_delta_bp), fmt(r.paired_net_month_block_p, 6)] for r in parents.itertuples()])
    text += '\n\n## 因果单特征排序与旧风险基线\n\n未训练模型。val AUC指later净>5标签的单特征排序AUC，不是成功标准。top-decile使用此前3完整月的90%分位；离散取值并列可使实际覆盖超过10%。下表保存前/后段，毛净收益、胜率与匹配随机均完整列出；该诊断未另外用于选择新参数，原始p未校正。\n\n'
    text += table(['时段/特征', 'AUC', '闭合', '>5率', '普通胜率', '毛/净bp', '随机配对', '随机净bp', '配对净差bp', '原始p'], [
        [r.period + '/' + r.feature, fmt(r.auc_gt5, 6), r.closed, pct(r.precision), pct(r.win_rate),
         f'{fmt(r.mean_gross_bp)}/{fmt(r.mean_net_bp)}', r.matched_pairs, fmt(r.control_mean_net_bp),
         fmt(r.paired_excess_net_bp), fmt(r.random_net_p, 6)] for r in ranking.itertuples()])
    text += '''

## 验证与风险诚实声明

- 源码828f300ccd先于回放；统计源码fdd251a54a先于评价。原版逐事件netR/删失与哈希认证V3来源一致。manifest认证3,169条完成回执、12,676叶文件及4个汇总文件，缺失不会静默跳过。
- 本轮17项特征/路径/统计专项通过。仓库边界、因果、parity共468通过、8失败：归档路径1项，旧artifact缺少source_commit连带5项，已迁移candidates.py/render.py哈希漂移2项。这些文件未由本轮改动；不能宣称全仓全绿或可上线。
- Terra High只读复核随机抽样、时间分区、分母和加仓父比较，未发现使回放失效的问题；指出父比较p未经族校正，已在本报告限制解释，未拿它绕过冻结的研究门。
- 本轮全部历史都已在先前研究中接触；时间分段是历史复核，不能恢复从未见过数据的验证资格。同币多场所、多周期、重叠路径相关；月块只部分处理相关性，源币池亦可能有幸存者偏差。
- OHLC没有真实订单簿、完整资金费、逐档维持保证金和标记价格。20bp成本模型不是逐笔真实费用。不得把价格R当本金倍数，也不得把独立路径均值当1000U账户收益。
- 没有新YOLO或ML训练、Pine交付、ACTIVE切换或真金下单。training_eligible与production_eligible均为false。16新臂未过门，因此不继续以成功策略名义做串行验证。

## 复现命令

从已冻结的V3全信号dataset和V9原生行情缓存开始，源数据重建见`analysis/p1_spike_10r_discovery_v3_20260921.md`与对应实验注册；不下载或替换数据源。输出目录应不存在；统计及交付拒绝覆盖，runner仅复用身份及叶文件一致的已完成流。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/evaluation/test_spike_5r_context_features.py tests/evaluation/test_spike_5r_followthrough.py tests/evaluation/test_spike_5r_followthrough_report.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -s -q -p no:cacheprovider tests/boundaries tests/causality tests/parity
PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m yoyo.evaluation.spike_5r_followthrough --output experiments/active/exp-spike-5r-followthrough-20260921-v6/run_v1 --workers 4
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m yoyo.evaluation.spike_5r_followthrough_report --source experiments/active/exp-spike-5r-followthrough-20260921-v6/run_v1 --output experiments/active/exp-spike-5r-followthrough-20260921-v6/results_v1
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m yoyo.evaluation.spike_5r_followthrough_delivery
```

## 下一步选项

保留本轮负面证据，不把多个无效条件拼成“高胜率”。Owner的1000U总预算、每次100U、最多两次加仓、爆仓止损是另一项已记录目标，需要独立实现有因果入场/加仓/退出且包含所有失败的账户回放；本轮1U风险对照不能代替。若依据路径回吐诊断改变止盈或保护参数，应另立单变量实验并明确新退出规则，不回写本次冻结方案。
'''
    text += f'\nNotion研究记录：[{NOTION}]({NOTION})。\n'
    REPORT.write_text(text)
    paths = [REPORT, Path(__file__), EXP / 'PROJECT_PLAN.md', EXP / 'config.json', run / 'manifest.json', result / 'summary.json']
    paths += [run / n for n in manifest['files']] + [result / n for n in summary['files']]
    paths += [Path(p) for p in set(manifest['dependencies']) | set(summary['dependencies'])]
    search.dump(target, dict(experiment_id=EXP.name, status='rejected', generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        candidates=49207, completed_streams=len(manifest['receipts']), verified_stream_leaves=leaves, new_arms_passed=0,
        training_eligible=False, production_eligible=False, reuse_allowed=False, notion_url=NOTION,
        source_dataset_receipt_sha256=summary['source_dataset_receipt_sha256'],
        files={str(p):dict(sha256=search.digest(p),size_bytes=p.stat().st_size) for p in sorted(set(paths))}))
    print(json.dumps(dict(report=str(REPORT),status='rejected',verified_stream_leaves=leaves)))


if __name__ == '__main__':
    main()
