import React, {useMemo,useState} from 'react';
import {Dropdown, EvidenceChart, ReportSection, Section, useDataApp} from '../../data-app-public.jsx';
import {altN5Images} from './alt-n5-images.js';
import {Copy,EvidenceTable,ResearchGallery,downloadRows,fmt,txt,num,pct,periods} from './ResearchEvidence.jsx';

const dimensionNames={entry_year_utc:'入场年份（UTC）',entry_month_utc:'入场月份（UTC）',side:'多空方向',entry_hour_bj:'入场小时（北京）',symbol:'逐币种'};
const baseColumns=[txt('label','范围'),num('closed','已平仓',0),num('censored','未决',0),pct('win_rate','净胜率'),num('mean_net_r','均净R'),num('realized_payoff','实际盈亏比'),pct('breakeven_win_rate','盈亏平衡胜率'),num('profit_factor','PF'),num('paired','严格配对',0),num('paired_signal_r','配对信号R'),num('paired_random_r','配对随机R')];

export function AltN5Chapter(){
 const {reviewedRows}=useDataApp();
 const stats=reviewedRows('alt-n5-stats'),ledger=reviewedRows('alt-n5-ledger'),exc=reviewedRows('alt-n5-excursions'),rr=reviewedRows('alt-n5-rr-bands'),dist=reviewedRows('alt-n5-distributions'),quant=reviewedRows('alt-n5-quantiles'),streaks=reviewedRows('alt-n5-streaks'),samples=reviewedRows('alt-n5-samples');
 const [dimension,setDimension]=useState('entry_year_utc'),[outcome,setOutcome]=useState('ALL'),[period,setPeriod]=useState('all'),[direction,setDirection]=useState('ALL'),[scope,setScope]=useState('market_exit_order'),[kind,setKind]=useState('stop');
 const periodStats=stats.filter(r=>r.dimension==='period');
 const split=stats.filter(r=>r.dimension===dimension);
 const all=periodStats.find(r=>r.value==='all');
 const cost=all?[{part:'毛收益',r:all.total_gross_r},{part:'手续费',r:-all.total_fee_r},{part:'扣费净收益',r:all.total_net_r}]:[];
 const runs=streaks.filter(r=>(scope==='market_exit_order'?r.scope===scope:r.scope.startsWith(scope+':'))&&r.kind===kind).sort((a,b)=>b.count-a.count);
 const records=useMemo(()=>ledger.filter(r=>(outcome==='ALL'||r.outcome_cn===outcome)&&(direction==='ALL'||r.direction_cn===direction)&&(period==='all'||(!r.crosses_selection_cut&&r.segment===(period==='from2023'?1:0)))),[ledger,outcome,direction,period]);
 const display=useMemo(()=>records.map(r=>({...r,exit_display:r.closed?`${r.exit_start_bj} → ${r.exit_end_bj}`:'未决：无真实退出',realized_r:r.closed?r.net_r:null,realized_fee:r.closed?r.fee_r:null,holding_display:r.closed?`${fmt(r.holding_h_lower,2)}–${fmt(r.holding_h_upper,2)}h`:'未决：不可作为实际持仓',mfe_display:r.closed?r.mfe_r:null,mae_display:r.closed?r.mae_r:null})),[records]);
 return <section id="alt-n5" className="report-section" aria-label="山寨4h五根确认详细专题">
 <ReportSection id="alt-n5-answer" title="山寨4h＋5根确认：61%胜率，仍被盈亏比与费用抵消" queryId="alt-n5-stats" sourceRows={periodStats}>
 <Copy id="alt-n5:answer">{"**2023年起的7,476笔交易，胜率61.25%，平均每笔−0.0120R。** 关键不在于赢的次数少，而是每次平均只赚0.633R、亏损平均为1.032R，实际盈亏比仅0.614。以这组盈亏金额计算，胜率至少要61.97%才能持平，当前低0.72个百分点。\n\n全历史9,041笔的实际盈亏比为0.608，平均每笔−0.0081R。毛收益合计+259.81R，被333.05R手续费抵消后剩−73.24R；5,685次止盈成交中，还有107笔扣费后亏损。**“止盈成交”不等于“净盈利”。**\n\n这是一组独立交易的统计，含同一行情下多币同时触发；没有统一资金和仓位预算，R总和不能当作账户收益。"}</Copy>
 </ReportSection>
 <EvidenceTable id="alt-n5-periods" title="全期与2023年前后：胜率、盈亏比、严格配对随机" queryId="alt-n5-stats" sourceRows={periodStats} rows={periodStats.map(r=>({...r,label:periods[r.value]??r.value}))} columns={baseColumns}/>
 <ReportSection id="alt-n5-rules" title="这9,065笔究竟怎么进、怎么出" queryId="alt-n5-ledger" sourceRows={ledger}>
 <Copy id="alt-n5:rules">{"每周每个方向首次影线触及周一1.618，插针实体必须留在线内。其后**5根完整4h K线只检查收盘**：多头收盘不低于下沿，空头收盘不高于上沿；开盘与影线可以越线。五根确认完成后，下一根4h开盘市价入场，距离插针开盘已24小时。\n\n**止损一直放原插针极值；止盈回近侧周一边界，一次全平。** 多头目标是周一低点，空头目标是周一高点。没有分批、保本、移动止损或固定持仓上限。入场时已越过目标或止损无效的候选被排除。\n\nN5来自已完成的VectorBT参数研究，底层5m原生止损与Portfolio输出冻结复用；本次补充统计和渲染没有重跑成交。周一高低取纽约当地完整周一；4h K线按UTC整点聚合。展示时点用北京时间，年度/月度统计明确使用UTC。\n\n总计9,065笔、620个山寨品种；9,041笔已判定，24笔因数据边界未决。**未决交易不计胜率和已实现收益，也不把边界结算时间展示成真实平仓。**"}</Copy>
 </ReportSection>
 <div className="analysis-charts"><EvidenceChart id="alt-n5-cost-chart" queryId="alt-n5-stats" title="毛收益如何变成净亏损 · 独立R总和" rows={cost} sourceRows={periodStats.filter(r=>r.value==='all')} spec={{type:'bar',x:'part',y:'r',valueDecimals:2}} height={300} variant="card"/><EvidenceChart id="alt-n5-distribution" queryId="alt-n5-distributions" title="净R分布 · 每个区间的笔数" rows={dist.filter(r=>r.metric==='net_r')} sourceRows={dist.filter(r=>r.metric==='net_r')} spec={{type:'bar',x:'bin',y:'n',valueDecimals:0}} height={300} variant="card"/></div>
 <EvidenceTable id="alt-n5-fees" title="逐期费用：目标已成交，也可能净亏" queryId="alt-n5-stats" sourceRows={periodStats} rows={periodStats.map(r=>({...r,label:periods[r.value]}))} columns={[txt('label','范围'),num('total_gross_r','毛R合计',2),num('total_fee_r','费用R合计',2),num('total_net_r','净R合计',2),num('take_profit','TP成交',0),num('stop_loss','SL成交',0),num('take_profit_net_loss','TP但净亏',0),num('mean_net_bp','平均净bp',2)]}/>
 <Copy id="alt-n5:cost-explain">{"市场开仓和止损按taker单边0.05%，限价止盈按maker单边0.02%，沿用2026-10-23读取的OKX Lv1费率。费用按每次实际成交金额计算。Binance历史价格配OKX费率属于研究成本情景，未含资金费、滑点和容量。\n\nR以每笔原始止损距离归一化，小止损会放大费用：最极端的SXT交易风险仅1.93bp，止损毛−1R，加5.1875R费用后净−6.1875R。平均名义净收益+1.42bp与平均净R为负可以并存，因为两者对不同风险距离交易的权重不同。"}</Copy>
 <EvidenceTable id="alt-n5-rr" title="计划盈亏比分层：最高胜率区间也没有盈利" queryId="alt-n5-rr-bands" rows={rr} columns={[txt('band','入场计划RR区间'),num('closed','已平仓',0),pct('win_rate','净胜率'),num('mean_net_r','均净R',4),num('paired','配对数',0),num('paired_signal_r','配对信号R'),num('paired_random_r','配对随机R')]}/>
 <Copy id="alt-n5:rr-explain">{"**计划RR不足0.25的2,499笔，胜率高达86.35%，平均仍亏0.0027R。** 等待五根确认后，价格往往已经朝周一目标走了一段：距离目标更近，但止损仍在原针尖，剩余收益空间相对风险变小。这解释了高命中率与低盈亏比同时出现的几何关系，不证明等待确认造成了全部亏损。\n\n上述分层是原交易的事后描述，没有筛选后重新回测。每个区间同时保留其自身严格配对随机值；不能拿全池随机均值给某个子组作对照。计划RR为1–2的区间虽略正，也不能据此把它升级为新规则。"}</Copy>
 <Section id="alt-n5-excursion-heading" title="多少单曾经浮盈，最后又止损？">
 <EvidenceTable id="alt-n5-excursion" title="仅在最终止损交易中计数 · 不把浮盈当作已成交收益" queryId="alt-n5-excursions" sourceRows={exc} rows={exc.map(r=>({...r,label:periods[r.period],interval:`[${fmt(r.mean_r_low,4)}, ${fmt(r.mean_r_high,4)}]`}))} columns={[txt('label','范围'),num('stops','止损笔数',0),num('sl_after_positive_mfe','曾有毛浮盈',0),num('sl_after_positive_net_mfe','浮盈足以覆盖往返费',0),num('sl_after_half_r','曾到0.5R',0),num('sl_after_one_r','曾到1R',0),num('sl_after_one_r_prior_bar','退出前完整K已到1R',0),txt('interval','均净R的周聚类95%区间')]}/>
 <Copy id="alt-n5:mfe">{"全期3,356笔止损中，3,230笔曾有毛浮盈，2,999笔的最好价格足以覆盖原市场开仓及假设市场退出的费用；489笔曾达到1R，其中486笔在退出之前的完整5m K线已经达到1R。\n\nMFE是持仓路径中的最大有利波动，MAE是最大不利波动，均只读取原框架退出前的可见路径。**不能把这489笔直接当成设置保本后能挽救的盈利单。** 保本会改变交易路径，也可能把原本最后盈利的单提前止掉，必须让框架重算全部同组交易。\n\n按实际入场纽约周进行2,000次整体重抽样，全期均净R的95%区间为[−0.0475,+0.0300]，2023年起为[−0.0584,+0.0315]，均跨零；这组已被反复探索的历史证据不能确认稳定盈利。"}</Copy>
 </Section>
 <Section id="alt-n5-calendar-heading" title="时间、方向与币种：亏损是否集中？">
 <EvidenceTable id="alt-n5-calendar" title="完整分组统计 · 切换年月、小时、多空或币种" queryId="alt-n5-stats" sourceRows={split} rows={split.map(r=>({...r,label:r.dimension==='side'?(r.value==='long'?'多头':'空头'):r.value}))} columns={baseColumns} searchable controls={<Dropdown label="统计维度" showLabel value={dimension} choices={Object.keys(dimensionNames)} choiceLabels={dimensionNames} onChange={setDimension}/>}/>
 <Copy id="alt-n5:splits">{"全期多头5,123笔，胜率58.85%，均净−0.0425R；空头3,918笔，胜率65.42%，均净+0.0369R。方向、年份和币种都是后验切片，不能自动推导为只做空或只交易某币。早期与后期按2023-01-01 UTC时间切分，跨切点交易单独处理；因此早后两期数量可能与全期差1笔，不能把它误当数据丢失。\n\n严格配对要求同币、同实际入场纽约周、同方向、同因果波动桶的三个随机对照全部已判定。全期6,519组配对中，信号均−0.0173R、随机−0.1520R；优于随机说明可能存在相对优势，但不等于自身有正净收益。"}</Copy>
 </Section>
 <Section id="alt-n5-streak-heading" title="最长连损：市场共振与单币连损要分开看">
 <EvidenceTable id="alt-n5-streak-table" title="全部连续区间 · 按长度降序，可搜索交易ID" queryId="alt-n5-streaks" rows={runs} searchable columns={[txt('scope','统计范围与顺序'),num('count','连续笔数',0),num('symbol_count','币种数',0),txt('first_exit_bj','首笔退出区间上界'),txt('last_exit_bj','末笔退出区间上界'),num('net_r','合计净R'),txt('first_trade_id','首笔ID'),txt('last_trade_id','末笔ID')]} controls={<><Dropdown label="连损范围" showLabel value={scope} choices={['market_exit_order','coin_exit_order','coin_entry_order']} choiceLabels={{market_exit_order:'跨币按退出排序',coin_exit_order:'单币按退出排序',coin_entry_order:'单币按入场排序'}} onChange={setScope}/><Dropdown label="连损定义" showLabel value={kind} choices={['stop','loss']} choiceLabels={{stop:'连续触发止损',loss:'连续净亏损'}} onChange={setKind}/></>}/>
 <Copy id="alt-n5:streaks">{"全市场按退出顺序最长连续52次止损，涉及52个不同币种：北京时间2025-08-01 03:05至08-02 09:15，合计−53.757R。同一5m内改变排序的上下界仍为52。它反映同向市场共振，**不是一个币连续52次，也不是账户回撤**。\n\n单币最长为AUCTION连续10次止损，入场跨度2024-02-22至2026-02-14，合计−10.345R。已平仓交易持仓中位约4小时25–30分，最长27天19小时50–55分。没有人为时间退出，因此少量交易跨周很久；24笔未决的边界时间不能用于计算真实最长持仓。"}</Copy>
 </Section>
 <EvidenceTable id="alt-n5-quantiles-table" title="尾部与持仓分位数 · 仅已判定交易" queryId="alt-n5-quantiles" rows={quant} columns={[txt('metric','指标'),num('p0','最小'),num('p5','P05'),num('p25','P25'),num('p50','中位'),num('p75','P75'),num('p95','P95'),num('p99','P99'),num('p100','最大')]}/>
 <Section id="alt-n5-sample-heading" title="12笔随机盈亏样本，逐根核对确认与退出">
 <Copy id="alt-n5:sampling">{"按多／空 × 盈／亏四个分层，各随机抽3笔，种子20261024，共12笔、24张图。选定交易后再加载K线，没有按好看的形态挑选。抽样盈亏各半不能估计胜率。图中的P是插针，1–5是确认，E是下一根开盘入场；X为实际退出5m区间。图可切换局部与全持仓，并保留币种水印、周一高低和全部交易价格。"}</Copy>
 <ResearchGallery id="alt-n5-gallery" queryId="alt-n5-samples" title="山寨N5盈亏图与逐根规则" samples={samples} images={altN5Images}/>
 </Section>
 <Section id="alt-n5-ledger-heading" title="全部9,065笔：可筛选、搜索和导出">
 <EvidenceTable id="alt-n5-ledger-table" title="原始信号与浮盈浮亏 · 未决显示为空" queryId="alt-n5-ledger" sourceRows={records} rows={display} searchable columns={[txt('symbol','品种'),txt('direction_cn','方向'),txt('outcome_cn','净结果'),txt('pin_bj','插针开盘'),txt('entry_bj','入场'),txt('exit_display','退出区间'),num('entry','入场价',7),num('stop','止损',7),num('target_price','近侧止盈',7),num('planned_rr','计划RR'),num('realized_r','实际净R'),num('realized_fee','费用R'),num('mfe_display','最大浮盈R'),num('mae_display','最大浮亏R'),txt('holding_display','持仓区间'),txt('trade_id','交易ID')]} controls={<><Dropdown label="交易时期" showLabel value={period} choices={['all','before2023','from2023']} choiceLabels={periods} onChange={setPeriod}/><Dropdown label="交易盈亏" showLabel value={outcome} choices={['ALL','盈利','亏损','未决']} choiceLabels={{ALL:'全部'}} onChange={setOutcome}/><Dropdown label="交易方向" showLabel value={direction} choices={['ALL','多','空']} choiceLabels={{ALL:'多空'}} onChange={setDirection}/></>}/>
 <div className="download-row"><button onClick={()=>downloadRows(ledger,'山寨N5-全部9065笔原始记录.csv')}>下载全部9,065笔原始CSV</button><button onClick={()=>downloadRows(display.map(r=>Object.fromEntries(['trade_id','symbol','direction_cn','closed','outcome_cn','pin_bj','entry_bj','exit_display','entry','stop','target_price','planned_rr','realized_r','realized_fee','mfe_display','mae_display','holding_display'].map(k=>[k,r[k]]))),'山寨N5-筛选后展示记录.csv')}>下载当前筛选CSV</button></div>
 <Copy id="alt-n5:ledger-warning">{"原始导出保留冻结框架字段，以便逐笔复核。closed=false的记录里，exit_price、net_r、费用和时长是数据边界字段，不代表真实成交或已实现收益；只有筛选后展示导出将这些字段明确留空。样本图和浮盈浮亏诊断只涉及已判定交易。"}</Copy>
 </Section>
 <ReportSection id="alt-n5-pullback" title="新增对照：五根确认后等回调再入场（待规则确认）" queryId="alt-n5-stats" sourceRows={periodStats}>
 <Copy id="alt-n5:pullback">{"回调入场有望用更接近原针止损的价格换取更大的计划盈亏比，但可能错过不回头的盈利单，也可能集中成交继续走弱的行情，所以不能仅凭RR改善推算胜率。\n\n当前尚未执行：需确定挂单在1.618，还是确认入场价与1.618之间的50%位置；并确定等到下一次纽约周一区间完成、12小时或20小时后撤单。止损仍放原插针极值，近侧周一目标不变。\n\n规则确定后，直接复用NautilusTrader做一次固定规则比较；保留原9,065次机会，分别统计成交、未成交、无效与未决，同时报告原始风险R₀、新入场风险R、费用和匹配随机对照。该项没有结果，不能用旧的直接挂单定时退出或1:1目标实验代替。"}</Copy>
 </ReportSection>
 </section>;
}
