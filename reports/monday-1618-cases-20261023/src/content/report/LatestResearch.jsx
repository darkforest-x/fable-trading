import React,{useState} from 'react';
import {DataComponent, Dropdown, ReportSection, Section, useDataApp} from '../../data-app-public.jsx';
import {Copy,EvidenceTable,ResearchGallery,downloadRows,txt,num,pct,periods} from './ResearchEvidence.jsx';
import {latestImages} from './latest-images.js';
const studies={parameters:'确认根数与量能窗口',body:'1h允许影线再触线',targets:'近侧 / 远侧目标',partial:'分批与TP1后保本',timed:'直接挂1.618，定时退出'};
const groups={BTC:'BTC',ETH:'ETH',XAUUSD:'现货黄金',ALTCOINS:'山寨',GROUP1:'第一组汇总'};
const policies={near_full:'近侧全平',far_full:'远侧全平',split_hold:'近侧平半 / 余仓原止损',split_be:'近侧平半 / 余仓含费保本'};
const common=[txt('config_label','配置'),txt('basis','净 / 毛'),num('signals','信号',0),num('closed','已判定',0),num('censored','未决',0),pct('win_rate','盈利率'),num('mean_r','均R'),num('profit_factor','PF'),num('paired','配对数',0),num('paired_signal_r','配对信号R'),num('paired_random_r','配对随机R')];
export function LatestResearch(){
 const {reviewedRows}=useDataApp();const [study,setStudy]=useState('parameters'),[group,setGroup]=useState('ALTCOINS'),[period,setPeriod]=useState('all');
 const qid=`latest-${study}`,all=reviewedRows(qid),selected=all.filter(r=>r.group===group&&r.period===period&&(study!=='timed'||r.cohort==='shared_four_horizons'));
 const rows=selected.map(r=>({...r,config_label:policies[r.config_id]??r.config_id}));
 const cols=study==='timed'?[num('horizon','持仓分钟',0),num('n','同机会数',0),pct('grid_win_rate','网格参考胜率'),pct('true_time_win_rate_lower','真实时钟胜率下界'),pct('true_time_win_rate_upper','真实时钟胜率上界'),num('grid_mean_bp','网格参考bp',2),num('mean_bp_lower','真实时钟bp下界',2),num('mean_bp_upper','真实时钟bp上界',2),num('paired','配对数',0),num('paired_signal_bp','配对信号bp',2),num('paired_random_bp','配对随机bp',2)]:common;
 const selection=reviewedRows('latest-selection'),changes=reviewedRows('latest-targets-changes'),bechanges=reviewedRows('latest-partial-changes'),btcstats=reviewedRows('latest-n5-stats'),btctrades=reviewedRows('latest-n5-ledger'),btcsamples=reviewedRows('latest-n5-samples'),aug=reviewedRows('latest-aug19');
 return <section id="latest-research" className="report-section" aria-label="参数研究与退出方案更新">
 <ReportSection id="latest-intro" title="此前已完成的研究：参数、止盈与保本放在同一处比较" queryId="latest-parameters" sourceRows={reviewedRows('latest-parameters')}>
 <Copy id="latest:intro">{"参数研究使用隔离环境中的VectorBT，复用同一历史输入与固定成本。情况2／3／4的确认根数分别扫1–5；情况2成交量前置窗口扫10／20／30／40，另一参数固定。1h实体守线、近远侧目标及分批保本分别是独立研究，不能把它们混成一个同时改了多项的方案。\n\n下表保留已完成研究的663行指标，可切换研究、品种和时间段；黄金费用未知，只报毛收益。**按全历史最高胜率挑选的配置不是已验证的最优策略。** 比较时同时看净R、盈亏比、样本量、后期表现和同条件随机。"}</Copy>
 </ReportSection>
 <EvidenceTable id="latest-study-table" title="已完成方案比较" queryId={qid} sourceRows={selected} rows={rows} columns={cols} controls={<><Dropdown label="研究" showLabel value={study} choices={Object.keys(studies)} choiceLabels={studies} onChange={setStudy}/><Dropdown label="研究品种" showLabel value={group} choices={Object.keys(groups)} choiceLabels={groups} onChange={setGroup}/><Dropdown label="研究时期" showLabel value={period} choices={['all','earlier','later']} choiceLabels={periods} onChange={setPeriod}/></>}/>
 {selected.length===0&&<Copy id="latest:no-data">{"该研究与品种组合没有有效结果；空白不代表零收益。情况2现货黄金的成交量条件无法从已有数据可靠计算。"}</Copy>}
 <div className="download-row"><button onClick={()=>downloadRows(all,`${study}-全部冻结统计.csv`)}>下载当前研究全部统计CSV</button></div>
 <Copy id="latest:study-rules">{study==='parameters'?"情况2：15m首次插针且量能为前置窗口加自身最大，后N根实体不碰1.618。情况3：1h首次仅影线触价，后N根连影线也不再碰线。情况4：4h首次仅影线触价，后N根收盘守线。均在确认后下一根开盘入场，固定原针止损、近侧周一目标。\n\n这不是对所有可能参数的穷举；仅覆盖Owner已授权的单变量范围。":study==='body'?"这是情况3的放宽版：仍用1h首次仅影线触价，后N根允许影线碰或穿线，但实体不得越过1.618。确认后下一根开盘入场，原针止损，近侧全平。与原版‘后续影线也不能碰’分开记录。":study==='targets'?"保持1h实体守线及其他条件，比较近侧和远侧全仓目标：多头近侧为周一低、远侧为周一高；空头相反。目标变远会改变命中率和持仓，不能把原近侧盈利单的后续行情直接叠加为额外利润。":study==='partial'?"基于1h实体守线、三根确认的冻结入场。近侧平半、远侧平半，再单独比较余仓保持原止损或TP1成交后推含费保本。固定规则使用NautilusTrader原生HEDGING、限价目标与保护止损。保本只针对余仓，整体结果仍包含已实现半仓和逐次手续费。": "每周每方向首次触及1.618即限价入场，不等待插针确认，不设提前止损，分别在5／15／60／240分钟后市场退出。下表采用四种期限都有数据的共同机会集合。\n\n历史OHLC无法知道每次盘中限价成交的精确秒数，因此‘5分钟后’与固定K线时钟并非完全相同；网格值是参考，另给真实时钟的价格与胜率可识别上下界，不能把参考值冒充精确5分钟胜率。该方案没有固定止损，R不适用，统一用名义bp。"}</Copy>
 <EvidenceTable id="latest-time-selection" title="按时间选参：早期选定后，再看后期表现" queryId="latest-selection" rows={selection} columns={[txt('group','品种'),num('case','情况',0),txt('axis','扫描轴'),num('value','早期选值',0),num('earlier_n','早期笔数',0),num('earlier_mean_r','早期均R'),num('later_n','后期笔数',0),num('later_mean_r','后期均R'),num('baseline_later_r','原基线后期R'),num('paired_signal_r','后期配对信号R'),num('paired_random_r','后期配对随机R'),num('holm_p','校正p',4)]}/>
 <Copy id="latest:selection">{"时间切分点为2023-01-01 UTC；跨切点交易不混入选择两侧。先用早期选择，再观察后期，比全历史挑最高值更接近参数稳定性问题。后期也已用于多轮研究，不是全新前向样本；即便某个数值较好，也不自动成为默认参数。"}</Copy>
 <Section id="latest-exits" title="远侧目标和TP1后保本：哪些单被救回，哪些利润被截断？">
 <EvidenceTable id="latest-target-transitions" title="同一批三根1h实体确认入场：近侧改远侧" queryId="latest-targets-changes" rows={changes} columns={[txt('group','品种'),txt('basis','口径'),num('entries','入场数',0),num('both_closed','两边均平仓',0),num('near_tp','近侧TP数',0),num('near_tp_far_tp','之后仍到远侧',0),num('near_tp_far_sl','之后反转止损',0),num('near_tp_far_censored','远侧未决',0),num('both_closed_near_mean_r','同组近侧R'),num('both_closed_far_mean_r','同组远侧R')]}/>
 <EvidenceTable id="latest-be-transitions" title="近侧平半后：推保本相对保持原止损的变化" queryId="latest-partial-changes" sourceRows={bechanges} rows={bechanges.map(r=>({...r,label:periods[r.period]}))} columns={[txt('group','品种'),txt('label','时期'),num('common_closed','共同已平仓',0),num('mean_delta_r','平均改善R'),num('improved','改善笔数',0),num('worsened','恶化笔数',0),num('original_far_sl_saved','原止损被救回',0),num('original_far_tp_cut','原远侧盈利被截断',0),num('gain_on_improved','改善单增益R'),num('loss_on_worsened','恶化单损失R')]}/>
 <Copy id="latest:be-tradeoff">{"例如山寨这组1h三根确认，全期近侧全平−0.0867R、远侧全平−0.1004R、平半后保持原止损−0.0945R、平半后推含费保本−0.1043R。保本把胜率从29.05%推高至48.35%，但平均收益变差：被提前截断的原盈利也有代价。**这不是4h五根确认的保本结果，不能直接套到本次山寨N5。**"}</Copy>
 </Section>
 <Section id="latest-btc-n5" title="BTC高胜率候选：35笔也要看盈亏金额">
 <EvidenceTable id="latest-btc-n5-metrics" title="BTC 4h＋5根确认 · 全部35笔" queryId="latest-n5-stats" sourceRows={btcstats} rows={btcstats.map(r=>({...r,label:periods[r.period]}))} columns={[txt('label','时期'),num('closed','平仓数',0),pct('win_rate','胜率'),num('mean_r','均净R'),num('mean_win_r','平均盈利R'),num('mean_loss_r','平均亏损R'),num('realized_payoff_ratio','实际盈亏比'),num('paired','配对数',0),num('paired_signal_r','配对信号R'),num('paired_random_r','配对随机R')]}/>
 <Copy id="latest:btc">{"BTC全期23赢12亏，65.71%胜率；平均盈利0.526R、平均亏损1.159R，实际盈亏比0.454，平均净−0.0517R。2023年后只有20笔，平均净−0.2213R。12笔最终止损都曾出现毛浮盈，5笔达到1R；这些路径统计仍不能替代新的保本回测。下面6笔为固定种子抽取的盈亏样本。"}</Copy>
 <ResearchGallery id="latest-btc-gallery" queryId="latest-n5-samples" samples={btcsamples} images={latestImages} title="BTC N5：6笔样本、12张图"/>
 <EvidenceTable id="latest-btc-ledger" title="BTC N5全部35笔原始成交" queryId="latest-n5-ledger" rows={btctrades} searchable columns={[txt('trade_id','ID'),txt('direction','方向'),txt('entry_time','入场UTC'),num('entry','入场',2),num('stop','止损',2),num('target_price','近侧止盈',2),num('net_r','净R'),num('mfe_r','最大浮盈R'),num('mae_r','最大浮亏R'),txt('exit_interval_end','退出5m上界UTC')]}/>
 <div className="download-row"><button onClick={()=>downloadRows(btctrades,'BTC-N5-全部35笔.csv')}>下载BTC全部35笔CSV</button></div>
 </Section>
 <Section id="latest-aug19" title="2026年8月19日：1.618被突破后，行情继续上行">
 <EvidenceTable id="latest-aug19-table" title="北京时间当日 · 价格与首次触上沿时段" queryId="latest-aug19" rows={aug} columns={[txt('symbol','品种'),num('monday_high','周一高',5),num('monday_low','周一低',5),num('upper','上1.618',5),num('lower','下1.618',5),txt('first_upper_interval_bj','首次触上沿所在1m开盘'),num('day_change_pct','当日涨幅%',2),num('upper_outside_closes','收于上沿外的5m根数',0)]}/>
 <DataComponent id="latest-aug19-chart" title="四币历史复盘图 · 非交易执行结果" queryId="latest-aug19" sourceRows={aug} kind="custom" variant="card"><div className="candle-scroll" data-reviewed-rows><img className="candle-image" src={latestImages.aug19} alt="2026年8月19日BTC ETH BNB DOGE与周一1.618关系"/></div></DataComponent>
 <Copy id="latest:aug19">{"当日四币都触及并多次收在上1.618外侧，BTC和ETH日涨幅分别约5.71%、8.78%。这说明1.618是待观察的位置，不是碰到就必然反转的保证。首次触价是所在分钟区间，不是准确成交秒数；复盘使用事后完整行情，不能当作当时可见的入场信号。"}</Copy>
 </Section>
 </section>;
}
