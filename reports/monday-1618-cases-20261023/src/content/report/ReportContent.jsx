import React, {useMemo, useState} from 'react';
import {DataComponent, DataTable, Dropdown, SegmentedControl, EvidenceChart, ReportSection, RichNarrative, Section, SectionNavigator, SortableRegion, SortableItem, useDataApp} from '../../data-app-public.jsx';
import {chartImages} from './chart-images.js';
import {AltN5Chapter} from './AltN5Chapter.jsx';
import {LatestResearch} from './LatestResearch.jsx';
import {ParameterReadout} from './ParameterReadout.jsx';

const names={BTC:'BTC',ETH:'ETH',XAUUSD:'XAUUSD · 黄金',GROUP1:'第一组汇总',ALTCOINS:'第二组 · 山寨'};
const cases={'1':'情况1 · 5m 吞没','2':'情况2 · 15m 量价','3':'情况3 · 1h 不再触线','4':'情况4 · 4h 收盘守线'};
const fmt=(x,n=3)=>x==null?'—':Number(x).toLocaleString('zh-CN',{minimumFractionDigits:n,maximumFractionDigits:n});
const time=x=>x?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(x.replace(' ','T'))):'—';
const col=(field,label,n=3)=>({field,label,renderCell:v=>fmt(v,n)});
const txt=(field,label)=>({field,label});
const baseCols=[txt('case_label','规则'),txt('group_label','分组 / 品种'),txt('basis','收益口径'),col('n','已判定',0),col('censored','未决',0),col('win_pct','盈利率 %',2),col('mean_r','均值 R'),col('random_mean_r','随机 R'),col('mean_bp','均值 bp',2),col('pf','盈亏因子',2),txt('ci_label','均值 R 95%区间')];
const rowsWithLabels=rows=>rows.map(r=>({...r,case_label:cases[r.case],group_label:names[r.group],basis:r.gross_basis?'毛收益':'扣手续费',ci_label:`[${fmt(r.ci_low)}, ${fmt(r.ci_high)}]`}));
function Table({id,title,queryId,rows,columns,sourceRows=rows,children,searchable=false}) {return <DataComponent id={id} title={title} queryId={queryId} sourceRows={sourceRows} displayRows={rows} kind="table" variant="card"><DataTable rows={rows} columns={columns} label={title} searchable={searchable}/>{children}</DataComponent>;}
function Text({id,children}) {return <RichNarrative id={id} value={children}/>;}
const order=['rules','overview','analysis','samples','ledger','method'];

function Samples({samples}) {
 const [c,setC]=useState('1'),[asset,setAsset]=useState('ALL'),[outcome,setOutcome]=useState('ALL'),[side,setSide]=useState('ALL'),[chosen,setChosen]=useState(''),[view,setView]=useState('detail');
 const pool=samples.filter(r=>String(r.case)===c&&(asset==='ALL'||r.asset===asset)&&(outcome==='ALL'||r.outcome===outcome)&&(side==='ALL'||r.direction===side));
 const s=pool.find(r=>r.sample_id===chosen)??pool[0];
 const controls=<><Dropdown label="情况" showLabel value={c} choices={Object.keys(cases)} choiceLabels={cases} onChange={setC}/><Dropdown label="资产" showLabel value={asset} choices={['ALL','BTC','ETH','XAUUSD','ALTCOINS']} choiceLabels={{ALL:'全部资产',...names}} onChange={setAsset}/><Dropdown label="结果" showLabel value={outcome} choices={['ALL','盈利','亏损']} choiceLabels={{ALL:'盈亏全部'}} onChange={setOutcome}/><Dropdown label="方向" showLabel value={side} choices={['ALL','多','空']} choiceLabels={{ALL:'多空全部'}} onChange={setSide}/></>;
 const meta=s?[{name:'插针 K 线开盘',value:time(s.pin_time)},{name:'确认完成 / 实际入场',value:time(s.entry_time)},{name:'退出所在 5m 区间',value:`${time(s.exit_interval_start)} → ${time(s.exit_interval_end)}`},{name:'1.618 / 插针极值止损',value:`${fmt(s.level,6)} / ${fmt(s.stop,6)}`},{name:'实际入场 / 近侧周一止盈',value:`${fmt(s.entry,6)} / ${fmt(s.target,6)}`},{name:'实际退出价',value:fmt(s.exit_price,6)},{name:'毛 R / 手续费 R / 净 R',value:`${fmt(s.gross_r)} / ${fmt(s.fee_r)} / ${fmt(s.net_r)}`},{name:'初始风险 / 计划收益风险比',value:`${fmt(s.risk_pct,3)}% / ${fmt(s.planned_rr,3)}`},{name:'持仓时间（上界）',value:`${fmt(s.holding_h,2)} 小时`},{name:'同一 5m 同触 TP/SL',value:s.ambiguous?'是，另有止损优先敏感性':'否'},{name:'抽样池 / 唯一交易 ID',value:`${s.sampling_pool} 笔 / ${s.trade_id}`},...(s.case===2?[{name:'插针成交量 / 前20根最大量',value:`${fmt(s.pin_volume,2)} / ${fmt(s.prior20_volume_max,2)}`}]:[])]:[];
 return <DataComponent id="sample-atlas" title="60 个分层随机盈亏样本 · 120 张真实 K 线图" queryId="samples" kind="custom" variant="card" sourceRows={pool} displayRows={pool} headerControls={controls}>
 <Text id="samples:selection">{"按情况 × BTC／ETH／黄金／山寨 × 多空 × 盈亏各固定抽 1 笔，种子 20261023；未按形状、盈利幅度或持仓时长挑图。情况2黄金缺少有效量，四个分层缺样。这里的盈亏各半来自抽样设计，不能用来估计胜率。"}</Text>
 {s?<><div className="specimen-controls"><Dropdown label="样本" showLabel value={s.sample_id} choices={pool.map(r=>r.sample_id)} choiceLabels={Object.fromEntries(pool.map(r=>[r.sample_id,`${r.sample_id} · ${r.symbol} · ${r.direction} · ${r.outcome} ${fmt(r.net_r??r.gross_r)}R`]))} onChange={setChosen}/><SegmentedControl label="图表范围" value={view} onChange={setView} options={[{value:'detail',label:'入场局部'},{value:'full',label:'持仓全程'}]}/></div>
 <div className="candle-scroll" data-reviewed-rows><img className="candle-image" src={chartImages[`${s.sample_id}-${view}`]} alt={`${s.sample_id} ${s.symbol} ${cases[s.case]} ${s.direction} ${s.outcome} ${view==='detail'?'入场局部':'持仓全程'}K线`}/></div>
 <div className="sample-details"><DataTable rows={meta} columns={[txt('name','时间 / 价格 / 费用'),txt('value','原始记录（时间为北京 UTC+8）')]} searchable={false} label="样本交易记录"/><DataTable rows={JSON.parse(s.checks_json).map(r=>({...r,open_time:time(r.open_time),close_time:time(r.close_time),passed:r.passed?'满足':'不满足'}))} columns={[col('number','确认',0),txt('open_time','开盘时间'),col('open','开'),col('high','高'),col('low','低'),col('close','收'),txt('passed','逐根规则')]} searchable={false} label="确认K线逐根检查"/></div>
 </>:<p data-reviewed-rows>该组合没有有效样本：情况2的现货黄金历史成交量不可用。</p>}
 <Text id="samples:clock">{"图上使用信号原周期，黄色 P 为插针，蓝色 1／2／3 为确认（情况1的 1 为吞没），E 为下一根开盘入场。水平线保留周一高低、1.618、止损、止盈及入场价；实体和影线来自历史 OHLC。图中包含事后行情，仅用于复盘。底层成交来自 5m 回放，退出只能定位到 5 分钟区间，无法知道区间内的具体成交秒数；持仓时长按该区间结束计，是最多偏长 5 分钟的上界。休市或缺口按已观测 K 线压缩，横轴时间仍是真实北京时间。"}</Text>
 </DataComponent>;
}

export function ReportContent() {
 const {reviewedRows,appTitle,canEdit,mode,setAppTitle}=useDataApp();
 const agg=reviewedRows('aggregate'),cuts=reviewedRows('cuts'),months=reviewedRows('monthly'),distribution=reviewedRows('distribution'),controls=reviewedRows('controls'),symbols=reviewedRows('symbols'),samples=reviewedRows('samples'),trades=reviewedRows('trades'),rules=reviewedRows('rules');
 const [overviewCase,setOverviewCase]=useState('ALL'),[group,setGroup]=useState('BTC'),[c,setC]=useState('4'),[dimension,setDimension]=useState('segment');
 const [tradeCase,setTradeCase]=useState('1'),[tradeAsset,setTradeAsset]=useState('BTC'),[tradeOutcome,setTradeOutcome]=useState('ALL');
 const overview=agg.filter(r=>overviewCase==='ALL'||String(r.case)===overviewCase);
 const selected=agg.filter(r=>String(r.case)===c&&r.group===group),ar=selected[0];
 const calendar=cuts.filter(r=>String(r.case)===c&&r.group===group&&r.dimension===dimension).sort((a,b)=>Number(a.value)-Number(b.value));
 const calendarDisplay=calendar.map(r=>({...r,label:dimension==='segment'?(r.value==='0'?'2023-01-01 UTC 之前':'2023-01-01 UTC 起'):dimension==='hour'?`${r.value}:00–${r.value}:59`:dimension==='weekday'?`周${['一','二','三','四','五','六','日'][Number(r.value)-1]}`:r.value}));
 const monthly=months.filter(r=>String(r.case)===c&&r.group===group),dist=distribution.filter(r=>String(r.case)===c&&r.group===group).sort((a,b)=>a.order-b.order);
 const cost=agg.filter(r=>r.group===group),comp=controls.filter(r=>String(r.case)===c&&r.group===group);
 const selectedSymbols=symbols.filter(r=>String(r.case)===c&&(group==='GROUP1'?r.asset!=='ALTCOINS':r.asset===group));
 const records=useMemo(()=>trades.filter(r=>String(r.case)===tradeCase&&(tradeAsset==='ALL'||r.asset===tradeAsset)&&(tradeOutcome==='ALL'||r.outcome===tradeOutcome)),[trades,tradeCase,tradeAsset,tradeOutcome]);
 const tradeRows=useMemo(()=>records.map(r=>({...r,pin_bjt:time(r.pin_time),entry_bjt:time(r.entry_time),exit_bjt:`${time(r.exit_interval_start)} – ${time(r.exit_interval_end)}`,result_r:r.net_r??r.gross_r,basis:r.net_r==null?'毛':'净'})),[records]);
 const exportCsv=()=>{const keys=Object.keys(trades[0]??{}),escape=v=>'"'+String(v??'').replaceAll('"','""')+'"';const text='\ufeff'+keys.join(',')+'\n'+trades.map(r=>keys.map(k=>escape(r[k])).join(',')).join('\n');const url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='monday-1618-all-79430-signals.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 return <article className="report-content spike-report" aria-label="周一1.618四种反转确认回测报告">
 <header className="report-hero"><div className="research-kicker">SPIKE / MONDAY 1.618 / RESEARCH 2026-10-23</div><h1 data-data-app-title contentEditable={canEdit&&mode==='edit'} suppressContentEditableWarning onBlur={e=>setAppTitle(e.currentTarget.textContent.trim()||appTitle)}>{appTitle}</h1>
 <Text id="hero:deck">{"本次更新重点解释山寨4h＋5根确认为什么胜率超过61%仍亏损，补齐9,065笔详细数据、浮盈回吐与12笔盈亏样本，并汇总已完成的VectorBT参数、近远侧目标、分批保本和定时退出研究。下方完整保留原四种情况的79,430笔信号、60笔样本及原始规则。各实验可能包含同一行情，不能把记录数相加当作独立事件。"}</Text>
 </header>
 <SectionNavigator sections={[{id:'parameter-ranking',label:'截图前16条中文解读'},{id:'alt-n5',label:'山寨N5详细分析'},{id:'latest-research',label:'参数与退出研究'},{id:'rules',label:'原四种情况与明细'}]} placement="inline"/>
 <ParameterReadout/>
 <AltN5Chapter/>
 <LatestResearch/>
 <ReportSection id="answer" title="原四种情况基线：结果怎样理解" queryId="aggregate" sourceRows={agg} showHeading={false}>
 <Text id="answer:body">{"## 尚不能把这四种形态判定为稳定盈利策略\n\n山寨币四组的平均每笔净 R 均为负，虽然若干规则好于同条件随机入场。 **情况4的 BTC 是本次值得继续观察的正值** ：57 笔、平均 +0.278R、随机 −0.210R；但均值 R 的周聚类 95% 区间跨零，且 2023 年后的平均名义收益为 −4.14bp。ETH 情况4全期 +0.069R，2023 年后转为 −0.219R。不能只挑全期的正 R。\n\n情况1原先引用的黄金“361 笔、+0.045R”已被时区校正结果替代： **347 笔，平均 −0.022R（毛）** ；2023 年后 97 笔为 −0.642R（毛）。情况3黄金全期 +0.088R（毛），2023 年后只有 12 笔；费用未知，证据仍有限。"}</Text>
 </ReportSection>
 <SectionNavigator sections={order.map((id,i)=>({id,label:['规则与时点','总体与随机对照','收益结构与时间','盈亏K线样本','逐笔流水','方法与数据边界'][i]}))} placement="inline"/>
 <SortableRegion id="report:monday-sections" label="报告章节" variant="stack" authoredOrder={order} className="report-sortable-sections">
 <SortableItem id="rules" label="规则与时点" kind="narrative"><Section id="rules-heading" title="01 / 四种规则：确认到哪一根，才允许入场">
 <Text id="rules:common">{"周一范围取  **America/New_York 当地周一 00:00 到周二 00:00** ，自动区分夏令时，完整结束后才使用高 H、低 L。上沿 = H + 0.618 × (H−L)，下沿 = L − 0.618 × (H−L)。触下沿做多、触上沿做空。下表按多头描述，空头对称。\n\n所有情况均在确认完成后的下一根开盘入场；止损固定为原插针低点／高点，止盈为 **近侧周一边缘：多头回周一低，空头回周一高** 。入场时若止损不在风险侧或目标已越过，排除该候选；不设人为最长持仓，不加入跟踪止损、保本或减仓。"}</Text>
 <Table id="rules-table" title="触发、确认及首次触价口径" queryId="rules" rows={rules} columns={[txt('case','情况'),txt('touch','插针条件'),txt('confirmation','后续确认'),txt('first','首次限定'),txt('entry','最早入场')]} />
 <Text id="rules:edge">{"情况2允许成交量并列最大，窗口为“前20根完整15m + 插针自身”，采用历史原币成交量；与截图显示的 USDT 计价成交量不是同一口径。情况3你已明确仍只测每周每方向第一次触价，之后三根连影线也不能再触线。情况4只限制后续三根收盘，开盘或影线可以越线；本次沿用首次触价口径。情况1没有首次触价限制，统计全部合格的相邻吞没事件。\n\n高周期按 UTC 整点聚合。冬季周一范围在周二 05:00 UTC 完成，落在 04:00–08:00 的4h K线中：该边界内的首次触价会消耗“首次”资格，但不能把含周一数据的整根K线当作有效插针。所有确认 K 线均需完整、时间相邻。"}</Text>
 </Section></SortableItem>
 <SortableItem id="overview" label="总体与随机对照" kind="table"><Section id="overview-heading" title="02 / 先看绝对收益，再看相对随机的改善">
 <Text id="overview:units">{" **盈利率按最终扣费收益大于 0 统计，黄金按毛收益。** R 是每笔盈亏除以该笔初始止损距离；bp 是名义价格收益，100bp = 1%。两者的权重不同，正平均 R 不保证正平均 bp。盈亏因子 = 盈利 R 总和 ÷ 亏损 R 绝对值总和。\n\n含黄金的第一组汇总统一用毛收益，避免混合未知黄金费用与已扣费加密；情况2第一组只有 BTC、ETH，因此采用净收益并明确缺少黄金。汇总是逐笔加权，黄金历史更长，不能看作三资产等权组合。表中的随机 R 是可判定随机交易的均值，严格配对的差异另见下方。"}</Text>
 <DataComponent id="overall-table" title="四种情况 × 两组资产（19 个有效统计口径）" kind="table" variant="card" queryId="aggregate" sourceRows={overview} displayRows={rowsWithLabels(overview)} headerControls={<Dropdown label="情况" showLabel value={overviewCase} choices={['ALL',...Object.keys(cases)]} choiceLabels={{ALL:'全部四种',...cases}} onChange={setOverviewCase}/>}><DataTable rows={rowsWithLabels(overview)} columns={baseCols} searchable={false} label="分组回测结果"/></DataComponent>
 <Text id="overview:ci">{"95% 区间为按纽约周整体重抽样 2,000 次的均值区间，同周多币依赖一并保留；种子固定。区间跨零时，不能排除长期均值为零或负数。所有结果都是研究样本，不包含资金分配或可成交容量模型；同币重叠交易独立持仓。"}</Text>
 </Section></SortableItem>
 <SortableItem id="analysis" label="收益结构与时间" kind="chart"><Section id="analysis-heading" title="03 / 拆开费用、收益分布与时间稳定性" filters={<><Dropdown label="情况" showLabel value={c} choices={Object.keys(cases)} choiceLabels={cases} onChange={setC}/><Dropdown label="分组" showLabel value={group} choices={Object.keys(names)} choiceLabels={names} onChange={setGroup}/></>}>
 {!ar?<p data-reviewed-rows>情况2黄金无法测试：现有长期现货数据没有有效成交量。</p>:<>
 <Table id="detail-metrics" title={`${cases[c]} · ${names[group]} · ${ar.gross_basis?'毛收益':'净收益'}`} queryId="aggregate" rows={selected} columns={[col('median_r','中位R'),col('p05_r','P05 R'),col('p95_r','P95 R'),col('mean_win_r','盈利单均R'),col('mean_loss_r','亏损单均R'),col('sum_r','独立R总和',2),col('ledger_max_drawdown_r','独立R账本最大回撤',2),col('holding_median_h','持仓中位h',2),col('holding_p90_h','持仓P90 h',2),col('ambiguity','同K双触',0)]}/>
 <div className="analysis-charts"><EvidenceChart id="cumulative-ledger" queryId="monthly" title="累计独立 R · 按退出月份" spec={{type:'line',x:'month',y:'cumulative_r',valueDecimals:2,stackable:false}} rows={monthly} sourceRows={monthly} height={320} variant="card"/><EvidenceChart id="return-distribution" queryId="distribution" title="每笔 R 分布 · 左闭右开区间" spec={{type:'bar',x:'bin',y:'n',valueDecimals:0}} rows={dist} sourceRows={dist} height={320} variant="card"/></div>
 <Text id="analysis:ledger">{"累计 R 与最大回撤仅描述按退出顺序相加的独立交易账本， **不是账户权益曲线，也不是账户回撤或复利收益** 。同一时期可有多笔交易，没有统一资金、杠杆或风险预算。月份中没有交易不等于市场数据连续；最长持仓和尾部收益可在逐笔表查阅。"}</Text>
 <DataComponent id="calendar-table" title="哪些时段更强，是否延续到后期" queryId="cuts" kind="table" variant="card" sourceRows={calendar} displayRows={calendarDisplay} headerControls={<Dropdown label="分解维度" showLabel value={dimension} choices={['segment','year','hour','weekday','direction']} choiceLabels={{segment:'2023年前后',year:'入场年份',hour:'入场小时（北京）',weekday:'入场星期（北京）',direction:'多空'}} onChange={setDimension}/>}><DataTable rows={calendarDisplay} columns={[txt('label','时间 / 方向'),col('n','已判定',0),col('censored','未决',0),col('win_pct','盈利率 %',2),col('mean_r','均R'),col('random_mean_r','对应随机R'),col('random_n','随机笔数',0),col('mean_bp','均bp',2),col('pf','盈亏因子',2)]} label="时间与多空统计" searchable={false}/></DataComponent>
 <Text id="analysis:time">{"年份、小时和星期以北京时间入场时点分组；前后期以 2023-01-01 00:00 UTC 为界。每个分组的随机值来自这些信号对应的可判定随机交易，按随机交易笔数加权；不等于严格配对差异，也不把随机入场的小时重新作为筛选条件。切片用来观察稳定性，未据此优化交易时段；少量样本的高胜率不作结论。"}</Text>
 <Table id="paired-controls" title="严格配对：相同条件下比随机多赚多少" queryId="controls" sourceRows={comp} rows={comp.map(r=>({...r,interval:`[${fmt(r.ci95[0],2)}, ${fmt(r.ci95[1],2)}]`,metric_label:r.metric==='net_bp'?'扣费 bp':'毛 bp'}))} columns={[col('paired','可配对信号',0),txt('metric_label','口径'),col('mean_excess_bp','信号−随机 bp',2),txt('interval','差异95%区间 bp'),col('cluster_signflip_p','周聚类 p',5)]}/>
 <Text id="analysis:controls">{"每笔信号最多匹配 3 个随机开盘：同币、同纽约周、同方向、同因果波动桶（前14根已收盘5m真波幅／开盘价的对数分桶），沿用该笔比例止盈止损与成本。严格配对要求该信号的3个随机对照都可判定；先对3笔随机收益取均值，再求信号与随机的差。少于3个完整对照的信号不进入配对检验，但仍在绝对收益中。周聚类符号翻转单侧 p 值按已冻结实验口径保留（备择为信号优于随机）。多种规则和切片未做多重比较校正，因此“显著”仅是探索性证据；优于随机与自身盈利必须分别成立。"}</Text>
 </>}
 <Table id="cost-matrix" title={`${names[group]} · 四种规则的费用与入场结构`} queryId="aggregate" sourceRows={cost} rows={rowsWithLabels(cost)} columns={[txt('case_label','情况'),txt('basis','口径'),col('gross_mean_r','毛均R'),col('mean_fee_r','费用均R'),col('mean_r','最终均R'),col('random_mean_r','随机均R'),col('risk_median_pct','风险距离中位 %',3),col('planned_rr_median','计划RR中位'),col('fee_erased_wins','毛赢净不赢',0)]}/>
 <Text id="analysis:fees">{"情况1插针止损很近：BTC 初始风险距离中位仅 0.269%，平均每笔费用消耗 0.494R；它的计划收益风险比中位为 7.58，但实际盈利率只有 12.07%。费用对小止损的影响被 R 放大，不能把接近 −1R 当作固定净止损。后面三种规则等待更久，入场、止损距离、目标余量与样本池都同时变化，不能把收益差全部归因于“周期更大”。"}</Text>
 <Table id="symbol-results" title={`${cases[c]} · ${names[group]} · 逐品种结果`} queryId="symbols" sourceRows={selectedSymbols} rows={selectedSymbols} searchable columns={[txt('symbol','品种'),col('n','已判定',0),col('win_pct','盈利率 %',2),col('mean_r','均R'),col('random_mean_r','随机R'),col('mean_bp','均bp',2),col('pf','盈亏因子',2),txt('first_entry','首笔入场 UTC'),txt('last_entry','末笔入场 UTC')]}/>
 </Section></SortableItem>
 <SortableItem id="samples" label="盈亏K线样本" kind="custom"><Section id="samples-heading" title="04 / 盈利和亏损都展开看：逐根确认与完整持仓路径"><Samples samples={samples}/></Section></SortableItem>
 <SortableItem id="ledger" label="逐笔流水" kind="table"><Section id="ledger-heading" title="05 / 79,430 笔信号流水：时间、价格与最终盈亏">
 <DataComponent id="full-ledger" title="逐笔明细 · 表内时间为北京时间" queryId="trades" kind="table" variant="card" sourceRows={records} displayRows={tradeRows} headerControls={<><Dropdown label="情况" showLabel value={tradeCase} choices={Object.keys(cases)} choiceLabels={cases} onChange={setTradeCase}/><Dropdown label="资产" showLabel value={tradeAsset} choices={['ALL','BTC','ETH','XAUUSD','ALTCOINS']} choiceLabels={{ALL:'全部',...names}} onChange={setTradeAsset}/><Dropdown label="结果" showLabel value={tradeOutcome} choices={['ALL','盈利','亏损','截尾','持平']} choiceLabels={{ALL:'全部'}} onChange={setTradeOutcome}/></>}><DataTable rows={tradeRows} label="全部信号交易明细" columns={[txt('symbol','品种'),txt('direction','方向'),txt('outcome','结果'),txt('pin_bjt','插针开盘'),txt('entry_bjt','入场'),txt('exit_bjt','退出区间'),col('entry','入场价',6),col('stop','止损',6),col('target_price','止盈',6),col('result_r','每笔R'),txt('basis','口径'),col('fee_r','费用R'),col('holding_h','持仓h',2),txt('trade_id','唯一ID')]}/></DataComponent>
 <div className="download-row"><button type="button" onClick={exportCsv}>下载全部 79,430 笔 CSV</button><span>完整导出使用原始带时区时间；包含未决记录。不会仅下载当前筛选。</span></div>
 <Text id="ledger:scope">{"79,430 是跨四种方案的信号记录数，同一行情可能进入不同方案，不能当作 79,430 个独立市场事件。各方案的截尾记录不计胜负：历史到期或异常数据断裂时无法判定原止盈止损结果，不能把边界平仓当作策略收益。"}</Text>
 </Section></SortableItem>
 <SortableItem id="method" label="方法与数据边界" kind="narrative"><ReportSection id="method-evidence" title="06 / 数据、成交假设与复现依据" queryId="aggregate" queryIds={['aggregate','controls','samples']} sourceRowsByQuery={{aggregate:agg,controls,samples}} showHeading={false}>
 <Text id="method:body">{"## 06 / 数据、成交假设与复现依据\n\n **历史覆盖并非统一截止。** 加密沿用已有 Binance USDT 永续5m数据，632个品种中包括 BTC、ETH 和630个山寨，最早从2020年起；226个品种覆盖到2026-09-22，406个到2026-04-30，上市起点不同。逐品种表的首末时间是合格交易时间，不是原始文件完整覆盖。使用现有资产池，未重建逐日可投资宇宙，因此有选择及存活偏差风险。\n\n **黄金为现货 XAUUSD 长期 HistData M1，2011—2026-10-09。** 沿用已校验的纽约当地时钟解释与 DST 转换，清除746个冲突分钟、涉及415根5m及13个异常周一。普通休市保留；黄金的“首次”仅指可观测市场时段。周一加密要求288根5m，黄金沿用至少240根的完整性标准。1h、4h的完整相邻条件也会排除跨休市确认；情况4黄金最终仅8笔，不能据此泛化。\n\n **原四种情况的执行来自 NautilusTrader 1.231.0。** 使用 HEDGING 独立持仓、下一根开盘市场单、原生保护止损和原生限价止盈；底层5m回放顺序 O→H→L→C。适配器保留独立限价 FillModel，避免多交易共享报价干扰，触发时间由框架内核决定。同一5m同时触发止盈止损另报止损优先敏感性；例如情况4山寨净R总和从 −638.14 变为 −639.35，不能因此改变亏损结论。框架记录的5m结束时刻只作为成交区间上界。\n\n **费用逐次成交计入。** 2026-10-23 读取的 OKX USDT 永续 Lv1：maker 0.02%、taker 0.05% 单边。开仓市场单与止损按 taker，原生限价止盈按 maker，分别用实际成交名义金额计算；Binance 历史价配 OKX 费率是研究成本情景。滑点、资金费和容量未建模。XAUUSD 的券商点差和费用未知，只报毛收益，绝不把未知当作真实零费用。\n\n **不把截图里已经走出的目标当作入场收益。** 情况4截图对应的 Binance 例子在三根确认后，开盘价已越过近侧周一目标；按约定的下一根开盘执行和目标有效性要求会被排除。截图来自 OKX，交易所价格并不逐点相同。这样的排除影响样本数，但避免了事后选更早入场。\n\n **结论边界。** 绝对收益、相对随机差、R与bp、毛收益与净收益是不同问题。随机仍处于同一周行情，不消除所有市场结构偏差；多轮提出的规则属于研究探索，未做正式前向验证。本报告不改变生产准入或默认策略参数。AUC、top-decile排序指标不适用于本次确定性价格形态规则，使用同币同周同波动的匹配随机与周聚类检验作为对应基准。\n\n **复现及版本。** 本报告读取四个冻结实验的逐笔账本；情况1黄金用 exp-monday-5m-wick-gold-clock-20261023-v2 替换旧行。报告构建器为 yoyo/evaluation/monday_cases_report_data.py，K线绘图为 monday_cases_report_charts.py，报告没有重放或另造回测引擎。输入文件路径、大小及时间戳保存在 source-stamps.json；抽样池与固定种子保存在 sample-selection.json，设计项目与屏幕ID保存在 design/provenance.json。可通过每块右上角的来源菜单查看对应证据。\n\n数据复现命令：\n\n```text\n/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.monday_cases_report_data --reuse-samples\n.venv/bin/python -m yoyo.evaluation.monday_cases_report_charts\n```\n\n首次从原始已登记行情提取样本时去掉 --reuse-samples。需已挂载历史数据外盘；本轮未重新下载行情，也未修改旧回测结果。"}</Text>
 </ReportSection></SortableItem>
 </SortableRegion>
 </article>;
}
