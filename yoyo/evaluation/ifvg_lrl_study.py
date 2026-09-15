"""Frozen ETH native-3m iFVG/LRL study; source decisions live in PROJECT_PLAN.

Reads only timestamp-approved pre-May OHLC through the existing prefix reader.
No parameter search, model training, production import, or network access.
Signals use closed bars; future OHLC is isolated to outcome resolution. Paired
random controls use causal 30-bar range bins and the target's frozen risk size.
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.spike_fanshen_prefix import read_prefix
from yoyo.evaluation.ifvg_lrl import Config, generate, replay, matched_controls, metrics

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-ifvg-lrl-eth-20260915-v1'
REPORT = ROOT / 'analysis/p1_ifvg_lrl_eth_20260915.md'


def paired_stats(controls, seed=91537, draws=9999):
    """One-sided daily cluster sign permutation; null mean excess <= zero.

    The complete paired sample is used, with a single sign shared by every
    pair on a NY date. It tests paired exchangeability, not strategy ranking.
    """
    if controls.empty:
        return dict(matched=0, unmatched=0, days=0, target_mean=None, control_mean=None, excess=None, p=None)
    c = controls.loc[controls.matched].copy()
    out = dict(matched=len(c), unmatched=len(controls)-len(c), days=0,
               target_mean=None, control_mean=None, excess=None, p=None)
    if c.empty:
        return out
    c['difference'] = c.target_net_r-c.control_net_r
    day = c.groupby('block').difference.sum().to_numpy(float)
    observed = day.sum(); rng = np.random.default_rng(seed)
    null = (rng.choice([-1,1], size=(draws,len(day))) * day).sum(axis=1)
    out.update(days=len(day), target_mean=float(c.target_net_r.mean()),
               control_mean=float(c.control_net_r.mean()), excess=float(c.difference.mean()),
               p=float((1+np.count_nonzero(null >= observed-1e-12))/(draws+1)))
    return out


def binary_auc(trades):
    """Diagnostic LRL membership AUC for net-positive independent candidates."""
    if trades.empty:
        return None
    c = trades.loc[~trades.censored]; y = c.net_r.to_numpy()>0
    pos, neg = int(y.sum()), int((~y).sum())
    if not pos or not neg:
        return None
    ranks = c.has_lrl.astype(int).rank(method='average').to_numpy()
    return float((ranks[y].sum()-pos*(pos+1)/2)/(pos*neg))


def source_receipt():
    """Fail before price parsing unless every builder dependency is committed."""
    paths = ['yoyo/evaluation/ifvg_lrl.py', 'yoyo/evaluation/ifvg_lrl_study.py',
             'yoyo/data/spike_fanshen_prefix.py', 'yoyo/data/release_eth_prefix.py',
             'yoyo/contracts/holdout.py', str((EXP/'config.json').relative_to(ROOT)),
             str((EXP/'PROJECT_PLAN.md').relative_to(ROOT))]
    commit = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
    hashes = {}
    for path in paths:
        committed = subprocess.check_output(['git','show',f'{commit}:{path}'], cwd=ROOT)
        local = (ROOT/path).read_bytes()
        if committed != local:
            raise RuntimeError(f'Uncommitted builder dependency: {path}')
        hashes[path] = hashlib.sha256(local).hexdigest()
    return dict(source_commit=commit, source_sha256=hashes)


def fmt(value, digits=3):
    return '不适用' if value is None else f'{value:.{digits}f}'


def write_report(summary, cfg, output):
    rows = summary['results']; receipt = summary['data_receipt']
    main = next(r for r in rows if r['fold']=='validation' and r['mode']=='serial' and r['arm']=='ifvg_lrl')
    baseline = next(r for r in rows if r['fold']=='validation' and r['mode']=='serial' and r['arm']=='ifvg_only')
    conclusion = '未显示扣费后盈利能力' if main['net_r'] <= 0 else '样本内净R为正，仍需后续确认'
    parts = [f'# ETH 3分钟 iFVG＋LRL v1：{conclusion}',
        f'生成时间：{summary["generated_at"]}。配置：`{cfg["version"]}`。这是原帖的机械化研究版本，尚非原作者系统的完整复刻。',
        '## 结论',
        f'验证段（2025-01-01至2026-05-01）单仓 LRL 组完成 **{main["n"]} 笔**，毛收益 **{main["gross_r"]:.2f}R**，扣0.2%往返成本后 **{main["net_r"]:.2f}R**，净胜率 **{fmt(None if main["win_rate"] is None else 100*main["win_rate"],2)}%**，净PF **{fmt(main["pf"])}**。同口径单独iFVG为{baseline["n"]}笔、{baseline["net_r"]:.2f}R。两组交易数量不同，不能把总亏损缩小直接解释成质量改善。',
        f'LRL组配对随机样本{main["matched"]}笔、{main["days"]}个纽约交易日：策略/随机平均净R为{fmt(main["target_mean"])} / {fmt(main["control_mean"])}，增量{fmt(main["excess"])}R，单侧按日聚类置换p={fmt(main["p"],4)}。未建立原帖80%胜率在ETH上的可复制性；本轮不调参追逐该数值。',
        '## 数据、口径与复现身份',
        f'- 市场：OKX ETH-USDT-SWAP，原生3分钟；只使用已有数据。安全前缀{receipt["rows"]:,}根，{receipt["first_open"]}至{receipt["last_close"]}（末根收盘）。缺口{receipt["gaps"]}、重复{receipt["duplicates"]}。',
        f'- 分析起点{cfg["start"]}；之前数据仅做上下文。全前缀反转事件{summary["events_total"]:,}个；时段内、止损有效的分析候选{summary["candidates"]:,}个，其中LRL通过{summary["lrl_candidates"]}个。',
        f'- 代码先提交再读取价格：`{summary["source_commit"]}`。前缀SHA256：`{receipt["prefix_sha256"]}`。Python {summary["runtime"]["python"]} / numpy {summary["runtime"]["numpy"]} / pandas {summary["runtime"]["pandas"]}。',
        '- 本配置holdout消耗 **0次**；timestamp-first reader在2026-05-01边界停下，restricted_price_rows_parsed=0。未把其他实验的holdout授权移用。',
        '- 开发描述段2023-08-01至2025-01-01；验证段2025-01-01至2026-05-01。两段独立重置持仓，跨终点未出场记录censored；全期另做连续回放，不把分段结果拼接冒充连续账户。',
        '- 固定1R止盈、结构止损、下一根开盘、同柱双触取止损。成本固定入场名义本金0.2%；无资金费路径或额外滑点模拟。R=该笔初始价格风险，累计R是固定风险单位加总，**不是账户收益率或资金回报百分比**。',
        '## 单仓结果（同一时点至多一笔持仓）',
        '| 区间 | 规则 | 已平/截尾 | 毛R | 净R | 净胜率 | 净PF | 配对策略/随机均净R | 增量p |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    labels={'development':'开发描述','validation':'验证','full':'全期连续'}
    arms={'ifvg_only':'仅iFVG','ifvg_lrl':'iFVG＋LRL'}
    def result_line(r):
        return f'| {labels[r["fold"]]} | {arms[r["arm"]]} | {r["n"]}/{r["censored"]} | {r["gross_r"]:.2f} | {r["net_r"]:.2f} | {fmt(None if r["win_rate"] is None else r["win_rate"]*100,2)}% | {fmt(r["pf"])} | {fmt(r["target_mean"])}/{fmt(r["control_mean"])} | {fmt(r["p"],4)} |'
    parts += [result_line(r) for r in rows if r['mode']=='serial']
    parts += [f'![全期单仓净R曲线]({output}/equity.png)', '## 独立候选诊断（允许重叠，不代表可执行账户）',
        '本表分离“筛出的候选怎样”与“过滤后释放持仓容量”。只把LRL作为准入开关；其余障碍与成本相同。',
        '| 区间 | 规则 | 已平/截尾 | 毛R | 净R | 净胜率 | 净PF | 配对策略/随机均净R | 增量p |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    parts += [result_line(r) for r in rows if r['mode']=='independent']
    parts += ['## 配对覆盖与成本诊断（单仓）',
        '| 区间 | 规则 | 成功匹配/缺失 | 日期数 | 平均增量R | 累计成本R | 中位成本R/笔 | TP仍净亏笔数 | 双触SL | 最大回撤R | 最长净连亏 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        if r['mode']=='serial':
            parts.append(f'| {labels[r["fold"]]} | {arms[r["arm"]]} | {r["matched"]}/{r["unmatched"]} | {r["days"]} | {fmt(r["excess"])} | {r["cost_r"]:.2f} | {fmt(r["median_cost_r"])} | {r["tp_net_loss"]} | {r["ambiguous"]} | {r["max_dd_r"]:.2f} | {r["longest_loss"]} |')
    parts += ['配对：同ETH、同纽约日期及30分钟块、同因果30根范围/价格桶，移植目标交易相对风险距离，保持1R和成本；固定seed只抽一次，不按收益重抽。配对策略/随机均值只覆盖共同已完成样本，不能拿随机均值乘完整交易数推算组合收益。控制组可重叠，属于事件对照。',
        '置换用每个纽约日期共享正负号，9999次，检验配对交换性下净R增量；不是LRL排序检验。主要关注验证段LRL单仓，其余p为关联的描述性诊断，不据多表最小p挑结论。极少日期或匹配缺失时结论弱。',
        '## 必报诊断与解释',
        f'- 验证集样本数与正类率：独立基线已平{summary["validation_label_n"]}笔，净正收益率{fmt(summary["validation_positive_rate"],4)}。LRL二值分数val AUC={fmt(summary["validation_auc"],4)}，仅作诊断，没有训练模型；AUC不作为成功门。',
        '- Top-decile毛/净收益：不适用。LRL只有是/否，没有连续排序，任意打破并列会制造不存在的前10%策略。已完整报告过滤组与单特征基线（仅iFVG）毛/净结果及匹配随机。',
        '- 同版本对照为LRL关闭；不存在本市场此前已冻结的iFVG＋LRL版本。原帖MNQ截图缺少交易账本、成本及完整规则，不把截图PnL与ETH回测放在同一尺度比较。',
        '- 1R获利并不保证净盈利：若结构风险只有0.1%，固定0.2%成本等于2R；毛赚1R仍净亏1R。窄止损把名义费率放大为很高的风险单位成本。',
        '## 指标使用与原帖边界',
        f'源码：[Pine v6指标]({ROOT}/yoyo/evaluation/pine/ifvg_lrl_eth_v1.pine)。TradingView标准ETH永续3分钟图，Pine编辑器粘贴源码；图上三角形是收盘候选，圆点为模拟入场，线为持仓SL/1R。右上角自定义账本使用下一开盘成交，不是平台Strategy Tester。LRL开关对应本轮唯一对照。',
        '- 默认截止2026-05-01，近期图表没有信号是预期；图表仅加载的有限历史无法自动重现本地长历史总数。编译/运行核验状态另见同实验目录 `pine_validation.md`，未完成逐笔跨平台数据对齐前不声称成交完全相同。',
        '- LRL原帖说3–4个整齐高/低点，本版固定最近3个已确认且未扫点；左右2根枢轴、60分钟有效期、10%线容差、最新FVG区、收盘直接反转入场均为透明补齐假设。原帖可包含主观行情与更高周期选择。',
        '- 本版只有原生3分钟；未实施30秒/1/2/3分钟最高周期仲裁，也不是对原作者80%胜率的完整证伪。纽约开盘时段照搬MNQ规则，其对ETH是否合理属于另一个待检验变量。',
        '## 风险与诚实声明',
        '- 使用历史、单一ETH市场；验证数据曾被其他策略研究，不称从未见过的盲样本。所有结果是这一组冻结机械规则的结果，不能外推实盘。',
        '- 无真实资金费、订单簿、队列或强平模拟；1R结构止损可能极窄。3分钟OHLC不知道柱内先后，同柱双触止损优先，包括获利跳空后触止损的保守分支；不把此近似称为真实成交。',
        '- 指标保留历史事件；图形对象数量受平台限制而清理旧FVG框，不删除已发生候选或回写账本。源图表最后剩下的标记不用于回测。',
        '- 未训练、未promote、未部署、未下单。任何不利结果与拒单、截尾都保留。',
        '## 复现命令',
        '已有指定OKX数据文件前提下，从仓库根目录执行；缺少原始文件会失败，不静默联网替换：',
        '```bash\ncd /Users/zhangzc/fable-trading\npython3 -m pytest tests/evaluation/test_ifvg_lrl.py tests/evaluation/test_ifvg_lrl_study.py tests/test_spike_fanshen_study.py tests/test_release_eth_prefix.py tests/boundaries/test_layer_imports.py tests/boundaries/test_experiment_isolation.py tests/boundaries/test_yoyo_package_is_local.py tests/causality/test_holdout_boundary_is_single_valued.py -q\npython3 -m yoyo.evaluation.ifvg_lrl_study --output data/research/ifvg_lrl_eth_20260915_reproduction\npython3 scripts/md_to_html.py analysis/p1_ifvg_lrl_eth_20260915.md --out-dir analysis/html\n```',
        f'本次产物目录：`{output}`。保存summary、事件、逐笔、拒单、配对对照及SHA manifest；受限源仅哈希安全前缀，不哈希整文件。',
        '## 下一步选项',
        '- 可先逐张检查指标与原帖形态的一致性；主观规则差异需Owner确认，不用结果反推定义。',
        '- 若要完整多周期复刻，需要明确最高周期仲裁、LRL容差与失效规则；属于新配置，先定规则再回测。',
        '- 更换成本、TP/SL、时段或消耗holdout均需要Owner决定；本轮不自动做。']
    REPORT.write_text('\n\n'.join(parts)+'\n')
    subprocess.run(['python3','scripts/md_to_html.py',str(REPORT),'--out-dir','analysis/html'], cwd=ROOT, check=True)


def run(output):
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite previous evidence: {output}')
    identity = source_receipt()
    config = json.loads((EXP/'config.json').read_text())
    cfg = Config(**{f.name:config[f.name] for f in fields(Config)})
    print('Committed builder verified; reading approved prefix.', flush=True)
    frame, receipt = read_prefix(ROOT/config['source'],cfg.minutes,config['end'])
    print(f'Prefix validated: {len(frame)} bars. Generating immutable events.', flush=True)
    events, context = generate(frame,cfg)
    output.mkdir(parents=True)
    events.to_csv(output/'events.csv.gz',index=False)
    starts = [('development',config['start'],config['validation_start']),
              ('validation',config['validation_start'],config['end']),
              ('full',config['start'],config['end'])]
    results=[]; curves={}; validation_base=None
    for fold,start,end in starts:
        for mode in ['independent','serial']:
            for arm,use_lrl in [('ifvg_only',False),('ifvg_lrl',True)]:
                trades,rejects = replay(frame,events,cfg,use_lrl=use_lrl,serial=mode=='serial',start=start,end=end)
                controls = matched_controls(frame,trades,context,cfg,start=start,end=end,seed=config['random_seed'])
                key=f'{fold}_{mode}_{arm}'
                trades.to_csv(output/f'{key}_trades.csv.gz',index=False)
                rejects.to_csv(output/f'{key}_rejects.csv.gz',index=False)
                controls.to_csv(output/f'{key}_controls.csv.gz',index=False)
                stats=metrics(trades); stats.update(paired_stats(controls,seed=config['random_seed']))
                closed=trades.loc[~trades.censored] if not trades.empty else trades
                stats.update(fold=fold,mode=mode,arm=arm,rejected=len(rejects),
                    median_cost_r=None if closed.empty else float(closed.cost_r.median()),
                    ambiguous=0 if closed.empty else int(closed.exit_reason.eq('sl_ambiguous').sum()),
                    tp_net_loss=0 if closed.empty else int((closed.exit_reason.eq('tp') & (closed.net_r<0)).sum()))
                results.append(stats)
                if fold=='full' and mode=='serial':
                    curves[arm]=closed
                if fold=='validation' and mode=='independent' and arm=='ifvg_only':
                    validation_base=trades
                print(f'{key}: {stats["n"]} closed, net {stats["net_r"]:.3f}R, matched {stats["matched"]}',flush=True)
    candidates=events.loc[(events.reason=='candidate') & (events.signal_time>=pd.Timestamp(config['start'])) & (events.signal_time<pd.Timestamp(config['end']))]
    validation_closed=validation_base.loc[~validation_base.censored] if not validation_base.empty else validation_base
    summary=dict(**identity,config=config,data_receipt=receipt,results=results,
        generated_at=datetime.now(timezone.utc).isoformat(), events_total=len(events),
        candidates=len(candidates),lrl_candidates=int(candidates.has_lrl.sum()),
        validation_auc=binary_auc(validation_base),validation_label_n=len(validation_closed),
        validation_positive_rate=None if validation_closed.empty else float((validation_closed.net_r>0).mean()),
        runtime=dict(python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__))
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    pd.DataFrame(results).to_csv(output/'summary.csv',index=False)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax=plt.subplots(figsize=(10,4.5),layout='constrained')
    for arm,t in curves.items():
        if not t.empty:
            ax.plot(pd.to_datetime(t.exit_time),t.net_r.cumsum(),label=arm,linewidth=1.5)
    ax.axhline(0,color='gray',linewidth=.5); ax.legend(); ax.grid(alpha=.2)
    ax.set(title='ETH 3m: full-period serial closed-trade net R (20bp cost)',ylabel='Cumulative fixed-risk units (R)',xlabel='UTC exit date')
    fig.savefig(output/'equity.png',dpi=170); plt.close(fig)
    write_report(summary,config,output)
    manifest=dict(source_commit=identity['source_commit'],generated_at=summary['generated_at'],
                  data_receipt=receipt,holdout_consumed=False,training_eligible=False,production_eligible=False,
                  files={})
    for p in sorted(output.iterdir()):
        manifest['files'][str(p.relative_to(ROOT))]=dict(sha256=hashlib.sha256(p.read_bytes()).hexdigest(),size_bytes=p.stat().st_size)
    (EXP/'delivery_manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
    print(f'Report: {ROOT / "analysis/html" / REPORT.with_suffix(".html").name}; results: {output}',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'data/research/ifvg_lrl_eth_20260915')
    args=parser.parse_args(); run(args.output.resolve())


if __name__=='__main__':
    main()
