"""Write honest morphology and conditional-return tables after verified collect.

This module consumes immutable receipts and never picks a model, changes a
threshold, retrains, or promotes. Old detection false-positive figures are not
compared because their outcome-negative ground truth had different semantics.
Only same-bank economic ranking is placed alongside the prior v4 experiment.
"""
from __future__ import annotations

from pathlib import Path
from yoyo.datasets.ma_morphology_redo import ROOT, read_json, sha, write_json

BASELINE=ROOT/'experiments/active/exp-ma-profit3r-20260922-v1/delivery_owner1500_v4/summary.json'


def show(value: object) -> str:
    return 'N/A' if value is None else f'{value:.4f}' if isinstance(value,float) else str(value)


def table(headers: list[str], rows: list[list]) -> str:
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+['| '+' | '.join(show(v) for v in r)+' |' for r in rows])


def deliver(exp: Path) -> dict:
    launch=read_json(exp/'launch_contract.json')
    if launch['files'].get(BASELINE.relative_to(ROOT).as_posix())!=sha(BASELINE):
        raise ValueError('Prior baseline not bound to frozen launch')
    job=read_json(exp/'collected/job_receipt.json')
    training=read_json(exp/'collected/training_receipt.json')
    if job['status']!='completed' or training['status']!='completed':raise ValueError('Completed training and economics required')
    plan=read_json(exp/'plan.json')
    data=ROOT/'datasets'/plan['dataset_name']
    morphology=[];economics=[];baseline=read_json(BASELINE)
    for r in baseline['economic_rows']:
        economics.append({'version':'v4',**r})
    arms={}
    for arm in ('A','B'):
        item=training['arms'][arm]
        best=exp/f'collected/training/arm_{arm}/weights/best.pt'
        if sha(best)!=item['best_sha256']:raise ValueError('Collected model mismatch')
        arms[arm]={'best_path':best.relative_to(ROOT).as_posix(),'best_sha256':sha(best),'epochs':item['results_csv']['epochs']}
        for split in ('val','test'):
            shape=item['common_image_evaluation']['by_split'][split]
            morphology.append({'arm':arm,'split':split,**shape,**item['evaluations'][split]['results_dict']})
            metrics=read_json(exp/f'collected/economics/arm_{arm}/metrics_{split}.json')
            controls=metrics['matched_random_control']['overall']
            for name,key,control_key in [(arm,'model','model_top10'),('quality_score','quality_score_baseline','quality_top10')]:
                if name=='quality_score' and arm=='B':continue
                m=metrics[key]
                if m['status']!='ok':raise ValueError('Missing economic baseline')
                top,c=m['top10'],controls[control_key]
                economics.append({'version':'v6','group':name,'split':split,'N':m['events'],'positives':m['retained_profit_events'],
                    'topN':top['events'],'auc':m['roc_auc_profit_label'],'gross_bp':top['mean_gross_bp'],'net_bp':top['mean_net_bp'],
                    'tp_rate':top['tp_rate'],'net_win_rate':top['net_profitable_rate'],
                    'p_diagnostic':m['ranking_permutation'].get('p_greater_or_equal'),'pairedN':c['paired_events'],
                    'paired_candidate_net_bp':c['candidate_on_paired_events']['net_bp'],
                    'paired_control_net_bp':c['per_event_mean_control']['net_bp'],'paired_delta_bp':c['candidate_minus_control']['net_bp']})
    result={'status':'completed_research_outputs_pending_interpretation','arms':arms,'morphology_rows':morphology,
        'economic_rows':economics,'counts':read_json(data/'summary.json')['counts'],'manifest_sha256':sha(data/'manifest.jsonl'),
        'launch_sha256':sha(exp/'launch_contract.json'),'production_eligible':False,'training_eligible':False,
        'research_limits':['Selected 3R winners versus conservative clear backgrounds; not unconditional shape evaluation.',
        'Core+5 confirmation images; not tip-only fresh signals.','B sees twice as many images per epoch.',
        'Return ranking is conditional on old rule candidates and known rule direction; overlapping events make permutation p diagnostic.',
        'No new owner per-sample gold; dense near misses remain quarantined.']}
    write_json(exp/'results_summary.json',result)
    countrows=[[arm,s,r['positive'],r['negative']] for arm,d in result['counts'].items() for s,r in d.items()]
    shapetable=[[r['arm'],r['split'],r['images'],r.get('metrics/mAP50(B)'),r.get('metrics/mAP50-95(B)'),
        r['low_conf_all_class_event_score_roc_auc'],r['positive_matched_class_spatial_hit_rate_conf025_iou05'],
        r['background_false_positive_rate_conf025_iou05']] for r in morphology]
    econrows=[[r['version'],r['group'],r['split'],r['N'],r['topN'],r['auc'],r['p_diagnostic'],r['gross_bp'],r['net_bp'],r['net_win_rate'],r['tp_rate'],r['pairedN'],r['paired_control_net_bp'],r['paired_delta_bp']] for r in economics]
    report=f'''# MA 形态负例纠正 v6：A/B 离线训练结果

A/B 各40轮训练及两种评估已完成。结果由冻结回执生成；未部署、未promote，不能据本报告宣称实盘盈利。

## 数据与标签

沿用1506训练、175验证、160测试正事件的原图原框。背景须在最宽可见窗每根K线上均满足六均线分散条件，且避开所有候选核心及人工标注保护区。旧收益非赢家不作为形态空标签。

{table(['组','split','正图','背景图'],countrows)}

图像1280×742，维持比例；A一图/事件，B两图/事件。按UTC2026-01-01、2026-05-01切分，测试截至2026-09-21T16:00Z；完整窗口和12h结果边界留在所属split。更早1200根仅用于均线预热。时间范围与独立事件明细在 dataset_ledger.jsonl，完整数据统计在summary.json。

## 形态指标

{table(['组','split','图数','mAP50','mAP50-95','事件AUC','正确方向IoU≥.5命中率','背景误报率'],shapetable)}

命中和误报使用conf≥.25，AUC使用conf≥.001的全类别最高分。验证与测试分开计算。旧v4/v5的“收益失败=形态不存在”真值有误，不能把旧mAP或误报率与本表直接对照。背景偏清晰，这个病例对照集合不能证明密集难例上的判别能力。

## 原完整候选池的收益评分

{table(['版','组/基线','split','N','topN','收益标签AUC','置换p(诊断)','毛bp','净bp','净胜率','TP率','配对N','随机净bp','配对超额bp'],econrows)}

val固定1305候选、test固定989候选。未来只用于收益标签；预测分数是规则方向的最高置信度，不使用真值框或收益。扣除原0.2%往返成本，沿用原TP/SL与期限及事先冻结的同币/时间块/波动桶随机入场对照。top10%先按全池排序后才统计对照可用性，缺对照不替补。表中“配对超额”使用相同配对事件；不是从所有top候选均值直接减随机均值。quality_score为原单变量基线。

## 归因与风险与诚实声明

本轮纠正训练和验证标签语义，保留原正例、图片比例、基础权重与训练配方；相对旧版属于Owner授权的语义联动修复，不能把变化全部归因于单一参数。A/B各自40轮但B每轮图片翻倍，因此差异包含训练曝光量。负例筛选不依赖未来亏损；困难的密集相似图尚无足够逐样本依据，仍隔离待审。

形态高分可能来自清晰背景与精选正例的分布差异。经济评估是原规则候选条件下的排序，不是自主选币/选方向收益；事件可能重叠，置换p仅作诊断，未进行前向实盘验收。图右端为核心后5根的收盘时点，禁止冒充tip新鲜信号。Owner逐样本金标状态仍为false。旧v5停在A8/B0，文件保留，未用于初始化新模型。

## 复现与产物

从仓库根目录先提交builder/plan再运行，输出已存在时脚本拒绝覆盖：

```bash
.venv/bin/python -m yoyo.datasets.ma_morphology_redo build --out datasets/{plan['dataset_name']}
.venv/bin/python -m yoyo.datasets.ma_morphology_redo audit --out datasets/{plan['dataset_name']}
.venv/bin/python -m yoyo.datasets.ma_morphology_review --dataset datasets/{plan['dataset_name']} --out {exp.relative_to(ROOT).as_posix()}/visual_review
# 人工查看对照图后写入manifest绑定的review.json；不是自动批准
.venv/bin/python -m scripts.research.run_ma_morphology_redo freeze
.venv/bin/python -m scripts.research.run_ma_morphology_redo stage
.venv/bin/python -m scripts.research.run_ma_morphology_redo start
# 两组与经济评估完成后
.venv/bin/python -m scripts.research.run_ma_morphology_redo collect
```

SHA、环境、源数据、图/标签、切分与回执在 `{exp.relative_to(ROOT)}`。本次manifest：`{result['manifest_sha256']}`。权重在collected/training/arm_A与arm_B；完整预测与对照表在collected/economics。

下一步：先解释本轮真实结果，并在独立真实输入与人工确认难例上检查；任何生产资格、ACTIVE切换或实盘部署仍须Owner另行决定。
'''
    (exp/'RESULTS.md').write_text(report,encoding='utf-8')
    return result
