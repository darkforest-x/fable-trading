"""Render frozen development/validation exit outcomes; no price re-evaluation."""
import json
import subprocess
import pandas as pd
import numpy as np
from yoyo.evaluation.ma_stoch_exit_study import EXP,ROOT
from yoyo.evaluation.ma_shift_stoch_report import num,table

NAMES={'baseline':'原反向箭头','reverse_kd':'任意反向K/D交叉','reverse_ma':'15m翻色也退出',
       'time_6':'最多30分钟','time_12':'最多60分钟','time_24':'最多120分钟',
       'stop_1':'1 ATR止损','stop_2':'2 ATR止损','stop_3':'3 ATR止损',
       'tp_1':'2 ATR止损＋1R全平','tp_2':'2 ATR止损＋2R全平','tp_3':'2 ATR止损＋3R全平',
       'break_even':'2 ATR止损＋1R后含费保本','partial_1r':'2 ATR止损＋1R平一半',
       'trail_1':'2 ATR止损＋1 ATR跟踪','trail_2':'2 ATR止损＋2 ATR跟踪'}


def result_table(summary):
    rows=[]
    for name,r in summary['arms'].items():
        c=r['control']
        rows.append([NAMES[name],r['n'],r['open_count'],num(r['final_equity']),num(r['mtm_return_pct']),num(r['mtm_max_drawdown_pct']),num(r['net_win_rate']*100),num(r['pf'],3),num(r['gross_mean_bp']),num(r['net_mean_bp']),num(c.get('control_mean_bp')),num(c.get('excess_mean_bp'))])
    return table(['方案','已平','末仓','期末U','净收益%','回撤%','净胜率%','PF','单笔毛bp','单笔净bp','随机净bp','配对超额bp'],rows)


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    dev=json.loads((EXP/'dev/summary.json').read_text());val=json.loads((EXP/'validate/summary.json').read_text())
    selection=json.loads((EXP/'selection.json').read_text());audit=json.loads((EXP/'ledger_audit.json').read_text())
    fig,axes=plt.subplots(2,2,figsize=(12,7),sharex='col',gridspec_kw={'height_ratios':[2,1]})
    for col,(phase,label) in enumerate([('dev','Development: Jan-Feb 2026'),('validate','Validation: Mar-Apr 2026')]):
        for name,color in [('baseline','#077e85'),('stop_2','#c96a38')]:
            curve=pd.read_csv(EXP/phase/f'{name}_curve.csv');x=pd.to_datetime(curve.time,utc=True);eq=curve.equity.to_numpy()
            axes[0,col].plot(x,eq,color=color,lw=1.2,label='Opposite arrow' if name=='baseline' else 'Add 2 ATR stop')
            axes[1,col].plot(x,100*(eq/np.maximum.accumulate(eq)-1),color=color,lw=1)
        axes[0,col].axhline(1000,color='#999',ls='--',lw=.6);axes[0,col].set_title(label);axes[0,col].legend()
        axes[1,col].set_xlabel('UTC date')
    axes[0,0].set_ylabel('Net equity (USDT)');axes[1,0].set_ylabel('Close MTM drawdown (%)')
    for ax in axes.flat:ax.grid(alpha=.15);ax.tick_params(axis='x',rotation=25)
    fig.suptitle('ETH perpetual | frozen entries | 1x equity | 20 bp round trip')
    fig.tight_layout();fig.savefig(EXP/'equity.png',dpi=150);plt.close(fig)
    a,b=val['arms']['baseline'],val['arms']['stop_2']
    md=['# ETH：退出、止盈、止损优化 v1\n',
        '**这轮没有找到优于原退出的盈利方案。** 1—2月16组全部亏损，开发按净权益预选回原反向箭头；3—4月验证原规则净−34.91%，2 ATR止损净−36.34%。止损能压低单笔最坏损失，却没改善账户收益。\n',
        '**这不是近一月的新结果。** 此前近月基线仍是−17.04%，本轮没有用该月挑参数或重跑新退出。优化使用2026年1—4月，holdout消费0；开发选择在验证前提交冻结。\n',
        '## 1. 入场不变，退出怎样比较\n',
        '15m最近已收盘hl2≥SMA40允许多，低于则允许空；5m按你原Stoch5/3/3箭头开仓。原箭头本身包含K上穿D且两者<20的多箭头、K下穿D且两者>80的空箭头，没有在箭头之外再加一遍过滤。5m收盘确认，下一开盘执行；两者在连续K线边界是同一时间，不额外等5分钟。\n',
        '原规则遇反向5m箭头全平，退出不受15m颜色限制。增加的退出各自独立对照：提前反向交叉、15m翻色、30/60/120分钟超时、1/2/3 ATR硬止损；在固定2 ATR止损上分别加1/2/3R全平、1R后保本、1R平一半、1/2 ATR跟踪。没有把保本＋分批＋跟踪打包择优。完整16组保留如下。\n',
        'ATR用5m Wilder14、首14根真实波幅均值播种，入场前最后一根值冻结；R是入场价到初始止损的距离。全平目标1/2/3R是毛价格目标，净收益还要扣20bp。含费保本指浮盈收盘达到1R后，将次bar止损移至入场价沿盈利方向0.2%；跳空仍可能亏。跟踪在收盘浮盈达到1R后，以该收盘距1/2个入场ATR更新且只收紧。\n',
        '## 2. 数据和先选后验\n',
        f'同一OKX ETH-USDT-SWAP原生5m。开发UTC 2026-01-01—03-01（{dev["evaluated_bars"]}根），验证03-01—05-01（{val["evaluated_bars"]}根），端点不含右侧开盘。预热从2025-12-20 10:00开始；允许源前缀共{val["source"]["rows"]}根，缺根0、重复0。15m由完整3根5m聚合，无价格填充。\n',
        '开发只读到3月1日，16个候选至少30笔已平交易才参与选择，按最终净权益、回撤、笔数、ID依次打破平手。结果预选baseline；提交selection.json后才读取和评分验证。验证预设baseline、固定stop_2锚点、开发选中方案，重复项去重为2组；没有在验证选第二名或继续扩参数。\n',
        f'引擎冻结：`{dev["source_commit"]}`；验证开始时已含选择文件的提交：`{val["source_commit"]}`。开发源prefix SHA：`{dev["source"]["prefix_sha256"]}`；验证源prefix SHA：`{val["source"]["prefix_sha256"]}`。采用timestamp-first读取，在截止后只看首个排除时间戳，不解析其价格。\n',
        '本地缓存此前用于其他研究，不能称为全新未见市场；此处“验证”只表示本次选择未看该段结果。两个时段独立1000U空仓启动，末仓只估值并预留退出费，不计已平仓胜率。\n',
        '## 3. 开发：全部16组结果\n',result_table(dev),
        '账户每笔按入场前权益1倍名义，逐笔复投。PF为正净收益率之和/负净收益率绝对值之和；回撤为每根5m收盘持仓估值回撤，不是盘中最坏路径。平均每笔bp以该筆入场名义为分母；每笔完整往返固定20bp，不能把收益率求和当账户收益。\n',
        '开发中原规则单笔平均毛14.34bp，还不够覆盖20bp成本；放宽为任意反向K/D交叉后平均毛−5.60bp，净−25.60bp。2 ATR止损相对原规则降低最坏一笔，却增加平仓笔数167→193、毛均值14.34→1.46bp。分批相对固定2 ATR锚点，最大盈利9.03%降到4.63%，平均净收益−18.54→−23.13bp；它缩小盈利尾部，未改善整体收益。不同退出释放不同空仓机会，因此比较的是整套串行规则，并非原交易的简单替价。\n',
        '## 4. 验证：冻结后只看2组\n',result_table(val),
        f'![开发与验证权益、回撤]({EXP/"equity.png"})\n',
        '2 ATR止损在验证中毛均值略好（−6.21→−3.22bp），但交易从156增至191笔；每笔仍负期望，复投账户更差。它把最大单笔净亏6.59%缩至1.30%，最长连亏却从9笔变12笔，账户回撤只改善0.66个百分点。**限制一笔损失与改善整套收益必须分别验证。**\n',
        '原规则相对匹配随机入场仍没有正超额证据；两组验证账户均亏，未满足预设“验证净收益>0且正超额p<.01”。因此本轮退出优化判为拒绝，不选分批或跟踪方案接入当前指标。原规则被开发排序选中也不等于适合实盘。\n',
        '## 5. 尾部、持仓和原始风险单位\n']
    rows=[]
    for phase,summ in [('开发',dev),('验证',val)]:
        for name in ['baseline','stop_2']:
            r=summ['arms'][name];c=r['control']
            rows.append([phase,NAMES[name],num(r['min_trade_net_pct']),num(r['max_trade_net_pct']),r['longest_loss'],num(r['median_hold_minutes']),num(r['max_hold_minutes']),num(r['net_r_mean'],3),num(c.get('control_mean_bp'))])
    md+=[table(['区间','方案','最差单笔净%','最好单笔净%','最长连亏','持仓中位min','持仓最长min','平均净R','匹配随机净bp'],rows),
         '原规则无初始止损，R不适用。止损组的R可复算，但按固定1倍名义，不按固定R仓位复投；ATR小的时候20bp成本占R的比例会更大。单笔历史最差值不构成未来损失上限。\n',
         '## 6. 匹配随机对照与检验\n']
    rows=[]
    for phase,summ in [('开发',dev),('验证',val)]:
        for name,r in summ['arms'].items():
            c=r['control'];rows.append([phase,NAMES[name],c['matched'],c['unmatched'],c['utc_day_blocks'],num(c['target_mean_bp']),num(c['control_mean_bp']),num(c['excess_mean_bp']),num(c['p_greater'],4),num(c['p_two_sided'],4)])
    md+=[table(['区间','方案','配对','未配','日块','目标净bp','随机净bp','超额bp','正超额单侧p','双侧p'],rows),
         '每个已平目标一次抽同ETH、同方向、同UTC日、同SMA20真实波幅/close固定桶(.001/.002/.004/.008)的随机入场，控制采用同一组退出、障碍、成本。种子9152401，按入场时间和方向哈希一次选择，不因控制亏损或未平仓重抽。控制可能重复/重叠，仅作事件比较，不合并成单仓账户。目标和控制均已平仓才进入配对，验证两组各有1个未平控制被删失；这种筛选及持仓跨日可能残留偏差。\n',
         '日块符号置换9999次检验正超额，依赖块交换对称，不能证明因果。开发16组属多重探索，不把其p值当独立发现；验证没有挑选新方案。没有预定预测分数，val AUC与top-decile毛/净收益不适用；val样本数为156/191笔已平仓，净胜率即对应正收益占比。用全部交易、原规则/单组件锚点和匹配控制替代，禁止按事后收益排top-decile。\n',
         '## 7. 执行细节与风险、诚实声明\n',
         '- 顺序：开盘已挂止损/目标→前收盘反向、翻色、超时退出→空仓才按前收盘箭头入场→盘中保护→收盘更新保本/跟踪。收盘更新从次bar生效；盘中退出不回填当根开盘新单。\n- 跳空越止损以开盘成交；已挂盈利目标被开盘越过，以开盘成交。盘中同时触达止损/目标而先后未知时按止损先；开发tp_1、partial_1r各出现1根此类冲突，其余0。部分目标只平原始50%一次，余仓仍原止损或反向箭头退出，不重加仓。\n- 盘中成交只知道所属5m，CSV的exit_time用该bar收盘作为上界，同时保存bar_open和intrabar标记；不声称知道精确秒数。\n- 成本20bp为冻结研究假设（开/平各10bp按入场名义），无资金费率、额外滑点、强平、最小合约量或资金冲击；不等于真实交易所账单。\n- 指标按已完成15m颜色和5m箭头，实时未收盘箭头可能变化；没有重建盘中箭头出现即消失的过程。\n- 本次只测试所列参数和一种半仓规则，不能断言所有退出方式无效；也不能用16次失败后再无限搜索同段来声称稳定最优。\n- 参考[TradingView官方策略执行文档](https://www.tradingview.com/pine-script-docs/concepts/strategies/)，此处采用明确的保守OHLC模型，尚无TV策略测试器全部逐成交parity。\n- 本轮holdout消耗0，不改变已交付近月结果或生产配置，training_eligible/production_eligible均false。\n',
         '## 8. 验证与复现\n',
         f'32项信号/退出定向测试通过，另88项层边界与holdout一致性测试通过。原引擎与新引擎baseline在开发167笔、验证156笔逐笔一致。主代理另以保存的份额成交独立重算{audit["closed_trades"]}笔已平仓、{audit["fills"]}条成交及{audit["matched_controls"]}对随机控制，费用、复投、末仓、风险单位、时钟和选择提交身份均通过。此为同代理账本核验，没有声称获得独立代理复核。\n',
         '上一轮全库边界/因果/parity为468通过、7既有失败（TOTAL2资产缺source_commit×5，迁移哈希不一致×2）。本轮未重复全库，也未绕过这些失败；只运行与本改动有关的120项。HTML生成后检查结构和本地链接、目视检查权益图；没有HTML浏览器像素验收。\n',
         '完整重建顺序（当前冻结结果目录存在时runner会拒绝覆盖；以下仅在拥有原始数据且相应输出尚不存在时执行，保留旧版产物）：\n',
         '```bash\ncd /Users/zhangzc/fable-trading\n.venv/bin/python -m pytest -q tests/test_ma_stoch_exit_engine.py tests/test_ma_stoch_exit_study.py tests/test_ma_shift_stoch.py tests/test_spike_fanshen_exit.py tests/boundaries/test_layer_imports.py tests/causality/test_holdout_boundary_is_single_valued.py\n# Builders/config/plan must already be committed, and source prefix SHA must match.\n.venv/bin/python -m yoyo.evaluation.ma_stoch_exit_study --phase dev\ngit branch --show-current\n# Must show main; commit ONLY the newly produced selection and dev summary.\ngit add experiments/active/exp-ma-stoch-exit-optimization-20260915-v1/selection.json experiments/active/exp-ma-stoch-exit-optimization-20260915-v1/dev/summary.json\ngit commit -m "research: freeze development exit selection"\n.venv/bin/python -m yoyo.evaluation.ma_stoch_exit_study --phase validate\n.venv/bin/python -m yoyo.evaluation.ma_stoch_exit_audit\n.venv/bin/python -m yoyo.evaluation.ma_stoch_exit_report\n```\n',
         f'[预注册规则]({EXP/"PROJECT_PLAN.md"}) · [开发结果]({EXP/"dev/summary.json"}) · [验证结果]({EXP/"validate/summary.json"}) · [选择冻结]({EXP/"selection.json"}) · [账本核验]({EXP/"ledger_audit.json"})\n',
         f'[验证原规则逐笔]({EXP/"validate/baseline_trades.csv"}) · [验证2ATR止损逐笔]({EXP/"validate/stop_2_trades.csv"}) · [原规则随机对照]({EXP/"validate/baseline_controls.csv"})\n',
         '## 9. 下一步边界\n',
         '本轮不选一个看起来复杂的退出方案上线。若继续研究，可先针对入场条件与行情环境建立新假设，并用新的开发/验证安排检验；这属于新研究范围。若指定只优化单笔风险或回撤，需要先固定风险目标，不能事后更换本轮净收益优先的选择标准。任何近期holdout复测、生产切换或真实仓位操作仍需Owner明确授权。\n']
    meta=EXP/'validation.json'
    if meta.exists():
        v=json.loads(meta.read_text())
        if v.get('notion_url'):md.append(f'[Spike Notion研究记录]({v["notion_url"]})\n')
    report=ROOT/'analysis/p1_ma_stoch_exit_optimization_20260915.md'
    report.write_text('\n'.join(md))
    subprocess.run(['python3','scripts/md_to_html.py',str(report),'--out-dir','analysis/html'],cwd=ROOT,check=True)
    print(report)

if __name__=='__main__':main()
