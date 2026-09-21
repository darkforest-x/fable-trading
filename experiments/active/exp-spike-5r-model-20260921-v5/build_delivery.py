"""Build the V5 research report from receipt-verified frozen result tables.

This reporter never trains, ranks, filters or replays market paths. It reads
the preregistered 2x2 target comparison to separate relabelling from model
improvement, and records all eight model failures as well as risk controls.
"""
from pathlib import Path
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_5r_model_study as m
from yoyo.evaluation import spike_10r_search as s
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = m.EXP
REPORT = Path('analysis/p1_spike_5r_model_v5_20260921_final.md')
NOTION = 'https://app.notion.com/p/3e28856479af81eea861d838b90d45a7'


def number(value, digits=2):
    return 'N/A' if pd.isna(value) else f'{value:.{digits}f}'


def percent(value):
    return number(value*100)+'%'


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |',
                       *['| '+' | '.join(map(str, row))+' |' for row in rows]])


def outcome_table(frame, count='gt5'):
    return table(['方案', '闭合', count, '命中率', '原赢家保留率', '均净bp', '匹配随机命中率', '随机均净bp'], [
        [r.rule, r.closed, getattr(r, count), percent(r.precision), percent(r.recall),
         number(r.mean_net_bp), percent(r.random_precision), number(r.random_mean_net_bp)]
        for r in frame.itertuples()])


def verify_all():
    checks = {}
    for folder, filename in [('prediction_v1','prediction_receipt.json'),
                             ('evaluation_v1','evaluation_receipt.json'),
                             ('serial_v1','receipt.json'),('target_comparison_v2','receipt.json')]:
        root = EXP/folder
        r = m.verify_output(root, filename)
        for path, expected in r.get('input_receipts', {}).items():
            if s.digest(Path(path)) != expected:
                raise ValueError('comparison input receipt drift')
        leaves = 0
        for key, sha in r.get('stream_receipts', {}).items():
            stream = root/'streams'/key
            if s.digest(stream/'completion.json') != sha:
                raise ValueError('stream receipt drift')
            leaf = json.loads((stream/'completion.json').read_text())
            for name, expected in leaf['files'].items():
                if s.digest(stream/name) != expected:
                    raise ValueError('stream output drift')
                leaves += 1
        checks[folder] = dict(files=len(r['files']), dependencies=len(r['dependencies']),
            stream_receipts=len(r.get('stream_receipts',{})), stream_files=leaves, all_match=True)
    serial = json.loads((EXP/'serial_v1/receipt.json').read_text())
    assert serial['complete'] and serial['streams'] == 3531
    assert serial['baseline_parity'] and serial['candidate_economic_parity']
    return checks


def main():
    if not _committed([Path(__file__)]):
        raise ValueError('commit delivery builder before report generation')
    if REPORT.exists() or (EXP/'delivery_manifest_v2.json').exists():
        raise ValueError('refuse to overwrite completed report')
    checks = verify_all()
    full = pd.read_csv(EXP/'serial_v1/comparison.csv')
    main_rows = full.loc[full.period.eq('oof')]
    newmodels = main_rows.loc[main_rows.rule.isin(m.model_policies())]
    passed = newmodels.historical_gate_passed.eq(True)
    status = 'rejected' if not passed.any() else 'candidate_requires_owner_review'
    independent = pd.read_csv(EXP/'evaluation_v1/comparison.csv')
    diag = pd.read_csv(EXP/'evaluation_v1/score_diagnostics.csv')
    diag = diag.loc[diag.period.eq('oof')]
    cross = pd.read_csv(EXP/'target_comparison_v2/target_comparison.csv')
    cross = cross.loc[cross.period.eq('oof')]
    old5 = cross.loc[cross.trained_target_r.eq(10) & cross.evaluation_target_r.eq(5)].set_index('rule')
    new5 = cross.loc[cross.trained_target_r.eq(5) & cross.evaluation_target_r.eq(5)].set_index('rule')
    old10 = cross.loc[cross.trained_target_r.eq(10) & cross.evaluation_target_r.eq(10)].set_index('rule')
    new10 = cross.loc[cross.trained_target_r.eq(5) & cross.evaluation_target_r.eq(10)].set_index('rule')
    folds = json.loads((EXP/'prediction_v1/folds.json').read_text())
    numeric = json.loads((EXP/'numerical_warning_audit.json').read_text())
    numeric_checks = numeric['global_checks']
    decision = pd.read_csv(EXP/'prediction_v1/decisions.csv.gz')
    wif = decision.loc[decision.event_key.eq('okx_60m_d567226c051c9c20:20551:1')].iloc[0]
    assert not wif[m.all_policies()].any()
    baseline = new5.loc['original_all']
    assert int(baseline.closed) == 24568 and int(baseline.gt5) == 662
    best = newmodels.sort_values('precision', ascending=False).iloc[0]
    changed_models = sum(new5.loc[name,'precision'] > old5.loc[name,'precision'] for name in m.model_policies())
    text = f'''# SPIKE 5R Model V5：降低标签门槛没有自动提高筛选能力

## 结论

Owner要求“或者大于5r”后，已按**最终扣费净R严格大于5**重训原先批准的两个固定模型，完成四季度前推预测和3,531流、14路径串行回放。主期2025-10-01至2026-09-10，原串行662/24,568={percent(baseline.precision)}；新模型最高点估计为`{best.rule}`，{int(best.gt5)}/{int(best.closed)}={percent(best.precision)}，均净{number(best.mean_net_bp)}bp。

8个新模型门槛中{int(passed.sum())}个通过事先固定的综合研究门。本轮状态：**{status}**。旧风险十分位127/3,138=4.05%，均净+20.08bp；旧10R训练逻辑回归5%档按5R计分54/1,201=4.50%，但只保留51/662=7.70%的原有赢家。这些比例不能解释成“只抓5R赢家”。新旧同档模型有{changed_models}/8个在5R点估计上上升，具体变化列于下表，不把降低标签本身算成训练进步。

## 唯一改动、母体与时间

- 仅将训练/评价标签>10改为>5。不是改5R止盈，也不使用盘中MFE。原V9风险分母、出入场、反向退出、2R收盘/4ATR保护、空头、20bp往返成本全部保留。
- 全49,207原始多头候选，49,070已结束、137删失；1,703严格>5（3.4706%），448严格>10（0.9130%）。失败、未实际开仓的原候选仍在母体；删失/无效/非有限值不当负类。父label_gt10未改，新label_gt5只在副本中派生。
- 共同前推期独立候选29,118，已结束28,982、删失136；789严格>5（2.7224%）、246严格>10。串行按原持仓顺序，只比较实际能开的交易，原版24,568闭合/662严格>5/206严格>10。
- 同20因果特征+固定周期one-hot、同模型参数/种子921401：每季度前9个月成熟标签训练、再3个月无标签分数定门槛，下季度预测，不复拟合。模型和校准都不能读当季未来标签。全部历史已暴露，属于时间前推探索，不是新的盲测。

'''
    text += table(['预测季度','成熟训练数','训练>5数','校准数','预测候选','拟合截止UTC'], [
        [f['quarter'],f['train_closed'],f['train_gt5'],f['calibration_candidates'],f['target_candidates'],f['fit_end']]
        for f in folds])
    text += '\n\n## 主结果：全部串行路径与匹配随机对照\n\n'
    text += 'top1/5/10/20指上一校准块的分数分位门槛，实际覆盖会随分布变化。保留率用原版赢家event_key交集，新增赢家另计；平均bp是每笔等名义收益，不是账户收益。随机与真实路径具有相同币种/交易所/周期、方向、月份、ATR桶、障碍和成本，删失对照不重抽。随机命中率分母是成功配对且双方结束的样本；对应数量见CSV。\n\n'
    text += outcome_table(main_rows)
    text += '\n\n'+table(['模型门槛','保留/漏掉/新增>5赢家','相对原版命中率差95%区间(pp)','尾部Holm p','净R Holm p','通过'], [
        [r.rule, f'{r.retained_gt5}/{r.lost_gt5}/{r.gained_gt5}',
         f'[{number(100*r.precision_ci_low)},{number(100*r.precision_ci_high)}]',
         number(r.random_tail_holm_p,6), number(r.random_net_holm_p,6), str(r.historical_gate_passed)]
        for r in newmodels.itertuples()])
    text += '\n\n研究门预定为命中率差95%下界>0、随机尾部及净R两种Holm p<0.01、均净bp>0、原赢家保留率>=10%；8模型门槛共同校正。月块bootstrap和配对月块符号置换不改变原交易经济路径。\n\n'
    text += '## 与上一版同标准比较：旧模型也重新按5R计分\n\n'
    text += '下表每一行前/后都评价最终>5，因此才是模型目标变更的比较。保存的旧交易只重计统计，未重新训练或改动。6条非模型控制路径逐行完全一致。\n\n'
    text += table(['模型门槛','旧→新>5赢家/闭合','旧→新>5率','旧→新保留率','旧→新均净bp','旧→新随机>5率','旧→新随机均净bp'], [
        [name, f'{int(old5.loc[name,"gt5"])}/{int(old5.loc[name,"closed"])} → {int(new5.loc[name,"gt5"])}/{int(new5.loc[name,"closed"])}',
         f'{percent(old5.loc[name,"precision"])} → {percent(new5.loc[name,"precision"])}',
         f'{percent(old5.loc[name,"recall"])} → {percent(new5.loc[name,"recall"])}',
         f'{number(old5.loc[name,"mean_net_bp"])} → {number(new5.loc[name,"mean_net_bp"])}',
         f'{percent(old5.loc[name,"random_precision"])} → {percent(new5.loc[name,"random_precision"])}',
         f'{number(old5.loc[name,"random_mean_net_bp"])} → {number(new5.loc[name,"random_mean_net_bp"])}']
        for name in m.model_policies()])
    text += '\n\n## 副指标：是否仍能抓到10R\n\n'
    text += table(['模型门槛','旧→新>10赢家','旧→新>10率','旧→新原>10保留率','旧→新随机>10率'], [
        [name, f'{int(old10.loc[name,"gt10"])} → {int(new10.loc[name,"gt10"])}',
         f'{percent(old10.loc[name,"precision"])} → {percent(new10.loc[name,"precision"])}',
         f'{percent(old10.loc[name,"recall"])} → {percent(new10.loc[name,"recall"])}',
         f'{percent(old10.loc[name,"random_precision"])} → {percent(new10.loc[name,"random_precision"])}']
        for name in m.model_policies()])
    text += '\n\n两种目标的全部时期、全部14路径及随机收益列保留于`target_comparison_v2/target_comparison.csv`。>10是副指标，未用于改参数或选择上线策略。\n\n'
    text += '## 时间稳定性\n\n'
    subset = full.loc[full.period.ne('oof') & full.rule.isin(['original_all','logistic_top20','lightgbm_top20','prior_v21_risk_decile'])].copy()
    subset['rule'] = subset.period+' / '+subset.rule
    text += outcome_table(subset)
    text += '\n\n完整各季度8模型/全部控制的结果已保存，不只展示较好的季度。2026Q3只到9月10日，不能当完整季度。\n\n'
    text += '## 排序诊断与单特征基线\n\n'
    text += table(['分数','val/OOF ROC-AUC','PR-AUC','前十分位>5率','前十分位毛/净bp','随机>5率','随机均净bp','随机尾部p/净Rp'], [
        [r.score,number(r.auc_gt5,6),number(r.pr_auc,6),percent(r.precision),
         f'{number(r.mean_gross_bp)}/{number(r.mean_net_bp)}', percent(r.random_precision),
         number(r.random_mean_net_bp), f'{number(r.random_tail_p,6)}/{number(r.random_net_p,6)}']
        for r in diag.itertuples()])
    text += '\n\n这里“前十分位”是在同一OOF母体整体排序后的诊断，不是可实时执行的门槛。模型筛入策略使用此前3个月分数门槛，两个表不能混用。risk的PR-AUC与净收益仍优于两个新分类器；AUC接近随机不支持高置信度挑出5R赢家。所有失败、普通盈利率、概率分箱、Brier/logloss保存在CSV。\n\n'
    text += '## WIF原案例\n\n'
    text += '原事件`okx_60m_d567226c051c9c20:20551:1`仍是最终净14.3299R。可用时刻2026-08-19 18:00 UTC（上海8月20日02:00），与信号bar起点时刻要区分。新5R模型分数约2.61%/3.24%，13条筛选门槛全部拒绝该事件。未为这张已知盈利截图改门槛；本轮结果不重新确认此前100U到3000U或22043U等账户推演。\n\n'
    text += f'''## 验证与风险诚实声明

- 全部3,531流完成，原版逐笔parity与共同候选经济parity通过；父数据SHA `a100b21e5f6343d5b5fba6a759701d5a359e6641cd534107c55c76d7a6cf4a68`。新旧6条非模型控制路径完全一致，证明未暗改风险/退出/成本。
- 18项模型/研究/统计专项通过，7项比较器控制路径与上游receipt篡改测试通过，108项相关边界/因果/数值门通过。注册表此前4项全仓失败源于两条旧source_commit缺失，V4留有HEAD复现证据，本轮未伪填历史或宣称全仓全绿。
- 实际新模型仍出现NumPy/sklearn matmul运行警告；保留原日志，独立逐元素重建四季所有目标/校准概率，最大差{numeric_checks['maximum_abs_saved_vs_manual_probability']:.3g}，新训练独立梯度最大绝对值{numeric_checks['maximum_abs_independent_training_gradient']:.3g}。仅支持这批冻结分数可用于离线评价，未解决底层警告根因。
- 更低的成功门槛增加标签样本量，却可能加入更难用现有特征区别的走势；本轮观察支持“仅改标签不够”，不能据此断言5R在所有方法下不可预测。
- 同币跨场所与重叠路径相关，月块统计不能消除所有依赖；原源池可能含幸存者偏差。历史反复研究有选择偏差，按时间分割不恢复盲测。
- 每笔固定20bp未建模额外资金费、成交深度、杠杆强平或账户多币同时持仓；不能从这些均值直接换算100U滚仓收益，也不存在必赢保证。未改Pine/TV/生产指针/真金账户。

独立代码审查指出初版比较器仅验交易与叶文件，未向上重验预测/评价receipt。已补齐上游SHA、完成标记、parity和源流集合校验，新增5项拒绝测试；比较统计另写v2，初版报告/manifest保留为交付前草稿。只重计统计，不重训或回放价格。注册表source_commit已更正为实际含V5源码的7482247。

## 完整复现顺序

运行环境沿用`.venv`：Python3.9.6、numpy2.0.2、pandas2.3.3、sklearn1.6.1、LightGBM4.6.0。先具备已冻结V3 dataset与V4预测/回放；所有原数据cache只读。新目录必须不存在，已完成输出拒绝覆盖。下列从既有固定源产物开始；重建源的完整命令见V3报告。

```bash
.venv/bin/python -m pytest -q tests/evaluation/test_spike_5r_models.py tests/evaluation/test_spike_5r_model_study.py tests/evaluation/test_spike_5r_statistics.py tests/evaluation/test_spike_5r_target_comparison.py
.venv/bin/python -m pytest -q tests/boundaries/test_layer_imports.py tests/boundaries/test_yoyo_package_is_local.py tests/boundaries/test_experiment_isolation.py tests/causality/test_continuity_and_availability.py tests/parity/test_numeric_baseline_parity.py
.venv/bin/python -m yoyo.evaluation.spike_5r_model_study --phase predict --dataset experiments/active/exp-spike-10r-discovery-20260921-v3/dataset_v1 --output {EXP}/prediction_v1
.venv/bin/python -m yoyo.evaluation.spike_5r_model_study --phase evaluate --dataset experiments/active/exp-spike-10r-discovery-20260921-v3/dataset_v1 --prediction {EXP}/prediction_v1 --output {EXP}/evaluation_v1
.venv/bin/python -m yoyo.evaluation.spike_5r_model_study --phase serial --dataset experiments/active/exp-spike-10r-discovery-20260921-v3/dataset_v1 --prediction {EXP}/prediction_v1 --evaluation {EXP}/evaluation_v1 --output {EXP}/serial_v1 --workers 6
.venv/bin/python -m yoyo.evaluation.spike_5r_target_comparison --dataset experiments/active/exp-spike-10r-discovery-20260921-v3/dataset_v1 --output {EXP}/target_comparison_v2
PYTHONPATH=. .venv/bin/python {EXP}/build_delivery.py
```

报告生成前还需独立新模型数值核验文件`numerical_warning_audit.json`（验证方法、四折输入/输出、梯度与哈希均在文件内）；它不是预测或回放依赖。源码提交`7482247bdc`早于预测。序列化模型、分数、大型逐笔压缩文件保留本地并由receipt绑定；小型CSV/receipt/本报告入库。

## 下一步选项

1. 保留>5作为后续研究主目标，当前版本拒绝准入；不因为降低目标就继续收紧模型分数。
2. 若继续改善，先单独验证新信息轴是否提供超过低风险基线的增量，例如信号前大盘/板块环境或高周期趋势。新增特征及离线训练范围需Owner确认并另立单变量版本，不能把一批想法打包宣称成功。
3. 目标从最终兑现>5改为途中到过5R，或改出场/止盈/成本，会变成不同实验，需Owner另行明确；本轮未混用。

Notion研究记录：[{NOTION}]({NOTION})。技术依据：[sklearn1.6评价指标](https://scikit-learn.org/1.6/modules/model_evaluation.html)。报告含有负面结果，属于研究证据，不是上线或实盘批准。
'''
    REPORT.write_text(text)
    paths = [REPORT, Path(__file__)]
    paths += [p for p in EXP.rglob('*') if p.is_file() and 'streams' not in p.parts
              and '__pycache__' not in p.parts and p.name != 'delivery_manifest_v2.json']
    paths += m.dependencies()
    paths += [Path('yoyo/evaluation/spike_5r_target_comparison.py'),
              Path('tests/evaluation/test_spike_5r_target_comparison.py')]
    s.dump(EXP/'delivery_manifest_v2.json',dict(experiment_id=EXP.name, status=status,
        generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        builder_commit_before_predictions='7482247bdc',
        reporter_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        production_eligible=False, training_eligible=False, reuse_allowed=False,
        target_r=5, notion_url=NOTION, hash_checks=checks,
        files={str(p):dict(sha256=s.digest(p),size_bytes=p.stat().st_size) for p in sorted(set(paths))}))
    print(json.dumps(dict(status=status,serial_models_passed=int(passed.sum()),report=str(REPORT)),ensure_ascii=False))


if __name__ == '__main__':
    main()
