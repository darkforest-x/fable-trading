import React,{useState} from 'react';
import {Dropdown,ReportSection,useDataApp} from '../../data-app-public.jsx';
import {Copy,EvidenceTable,downloadRows,txt,num,pct} from './ResearchEvidence.jsx';

export const groupLabels={BTC:'BTC',ETH:'ETH',XAUUSD:'现货黄金',ALTCOINS:'山寨币',GROUP1_GROSS:'BTC＋ETH＋黄金（毛收益汇总）'};
export const basisLabel=b=>b==='net'?'扣手续费':b==='gross'?'毛收益（未扣费用）':b;
export function configLabel(r){
 const n=r.confirmations??r.config_id?.match(/-n(\d+)/)?.[1];
 if(r.config_id?.startsWith('case'))return {2:`15分钟插针＋${n}根实体不碰线 · 量能前${r.volume_window}根`,3:`1小时插针＋${n}根影线也不碰线`,4:`4小时插针＋${n}根收盘守线`}[r.case];
 if(r.config_id?.startsWith('body'))return `1小时实体守线＋${n}根确认`;
 if(r.config_id?.startsWith('near'))return `1小时实体守线＋${n}根确认 · 近侧全平`;
 if(r.config_id?.startsWith('far'))return `1小时实体守线＋${n}根确认 · 远侧全平`;
 return r.config_id;
}
const qid='parameter-readout';
const csvLabels={rank:'胜率排序序号',config_label:'中文方案',group_label:'品种分组',period_label:'统计时段',basis_label:'收益口径',volume_label:'实际成交量条件',closed:'已判定笔数',censored:'未决笔数',wins:'盈利笔数',losses:'亏损笔数',flat:'持平笔数',win_rate:'胜率比例',mean_r:'平均每笔R',total_r:'合计独立R',mean_win_r:'平均盈利R',mean_loss_r:'平均亏损R',payoff:'实际盈亏比',breakeven_rate:'保本所需胜率比例',profit_factor:'盈利因子',mean_fee_r:'平均手续费R',planned_rr_median:'计划盈亏比中位数',sl_count:'止损笔数',tp_count:'止盈笔数',tp_losers:'止盈后扣费亏损笔数',longest_coin_loss:'单品种入场顺序最长连亏',longest_coin_sl:'单品种入场顺序最长连损',market_sl_min:'市场退出顺序连损下界',market_sl_max:'市场退出顺序连损上界',holding_h_median:'持仓小时上界中位数',holding_h_max:'最长持仓小时上界',first_entry:'首笔入场UTC',last_entry:'末笔入场UTC',paired:'完整配对数',paired_signal_r:'配对信号平均R',paired_random_r:'配对随机平均R',config_id:'原始配置代码'};
const chineseRows=rows=>rows.map(r=>Object.fromEntries(Object.entries(csvLabels).map(([k,v])=>[v,r[k]])));
const overview=[txt('period_label','统计时段'),num('closed','已判定',0),num('wins','盈利',0),num('losses','亏损',0),pct('win_rate','胜率'),num('mean_r','平均R',4),num('payoff','实际盈亏比'),num('profit_factor','盈利因子'),num('paired','配对数',0),num('paired_signal_r','配对信号R'),num('paired_random_r','配对随机R')];

export function ParameterReadout(){
 const {reviewedRows}=useDataApp(),all=reviewedRows(qid),top=all.slice(0,16);
 const [rank,setRank]=useState('1');const chosen=top.find(r=>String(r.rank)===rank)??top[0];
 const peers=chosen?all.filter(r=>r.config_id===chosen.config_id&&r.group===chosen.group).sort((a,b)=>['all','earlier','later'].indexOf(a.period)-['all','earlier','later'].indexOf(b.period)):[];
 return <section id="parameter-ranking" className="report-section" aria-label="截图前16条中文方案解读">
 <ReportSection id="ranking-intro" title="截图前16条：5种参数方案，7条平均收益为负" queryId={qid} sourceRows={top}>
 <Copy id="ranking:intro">{"你打开的是按胜率倒序排列的原始统计表。**前16条不是16种独立策略，而是5种配置按品种与统计时期展开。** 本节按不含表头的前16条逐项对应；同胜率保留原表顺序。第一组汇总又包含BTC、ETH、黄金，‘全历史’又包含早期与后期，所以这些行不能加总为交易总数。\n\n最高两条均为黄金2023年起，只有5赢1亏，胜率83.33%，且费用未扣。高胜率排名不能直接当作收益排名或最优参数。下方可以选择任何一条，展开它的全历史与两个时期、盈亏金额、连损和时间范围。"}</Copy>
 </ReportSection>
 <EvidenceTable id="ranking-table" title="前16条逐项中文对照 · 表格可横向滚动、翻页" queryId={qid} sourceRows={top} rows={top} columns={[num('rank','序号',0),txt('config_label','方案'),txt('group_label','品种'),txt('period_label','时期'),txt('basis_label','口径'),num('closed','已判定',0),pct('win_rate','胜率'),num('mean_r','平均R',4),num('payoff','实际盈亏比'),num('paired','配对数',0),num('paired_signal_r','配对信号R'),num('paired_random_r','配对随机R')]}/>
 <div className="download-row"><button onClick={()=>downloadRows(chineseRows(top),'截图前16条-中文详表.csv')}>下载前16条中文详表</button><button onClick={()=>downloadRows(chineseRows(all),'全部243条参数统计-中文详表.csv')}>下载全部243条中文详表</button></div>
 <Copy id="ranking:rules">{"### 这5种配置具体怎么做\n\n- **1小时＋4根 / 5根：** 情况3原版。每个纽约周、每个方向第一次仅影线触及1.618，插针实体在内侧；其后4根或5根1小时K线连影线也不能再碰线。\n- **4小时＋3根 / 4根 / 5根：** 情况4。首次仅影线触及1.618；其后3根、4根或5根4小时K线收盘守线。做多不能收在线下，做空不能收在线上；后续影线可以越线。\n\n确认K线全部收盘，才在下一根开盘市价入场。确认等待分别是4／5小时或12／16／20小时（从插针收盘算起）。**止损固定在原插针最低点／最高点；止盈回近侧周一边界，一次全平：多头回周一低，空头回周一高。** 没有分批、保本、移动止损或定时平仓；数据结束等无法判定的记录列为未决。入场时目标已失效的候选不计有效交易。\n\n`case3-n4-v20` 的含义是‘情况3、确认4根、共用配置里记录窗口20’。**情况3和4完全不使用成交量条件，v20在这里不生效。** 只有情况2使用‘插针量为前N根及自身最大’。这里的情况3也不是后来新增的‘允许后续影线碰线、实体守线’版本，后者在参数与退出研究中单独列出。"}</Copy>
 <EvidenceTable id="ranking-periods" title={chosen?`第${chosen.rank}条展开 · ${chosen.group_label} · ${chosen.config_label}`:'时段对照'} queryId={qid} sourceRows={peers} rows={peers} columns={overview} controls={<Dropdown label="展开截图条目" showLabel value={rank} choices={top.map(r=>String(r.rank))} choiceLabels={Object.fromEntries(top.map(r=>[String(r.rank),`第${r.rank}条 · ${r.group_label} · ${r.period_label}`]))} onChange={setRank}/>}/>
 <Copy id="ranking:periods">{"早期＝2023-01-01 00:00 UTC之前；后期＝该时刻起；全历史＝该品种已有历史。跨切点持仓不进入早／后期两侧，所以两个分段之和可能小于全期。各币起止日期、上市时间不同，下方另列实际合格交易时间。黄金及第一组汇总全部使用毛收益；单独BTC、ETH和山寨使用扣手续费收益。第一组按交易合并，不是三品种等权资金账户。\n\n配对信号R与配对随机R使用同一可匹配子集，应互相比；不能拿全体平均R减配对子集的随机R。配对不足时，子集结果不能代表全部交易。"}</Copy>
 <EvidenceTable id="ranking-payoff" title="盈利为什么没盖住亏损：盈亏幅度与费用" queryId={qid} rows={peers} columns={[txt('period_label','时期'),txt('basis_label','口径'),num('mean_win_r','平均盈利R'),num('mean_loss_r','平均亏损R'),num('payoff','实际盈亏比'),pct('breakeven_rate','保本所需胜率'),pct('win_rate','实际胜率'),num('mean_gross_r','平均毛R'),num('mean_fee_r','平均费用R'),num('mean_r','最终平均R',4),num('total_r','合计独立R',2),num('planned_rr_median','计划盈亏比中位')]}/>
 <Copy id="ranking:payoff">{"**平均每笔收益＝盈利比例×平均盈利＋亏损比例×平均亏损。** 实际盈亏比＝平均盈利／平均亏损绝对值；盈利因子＝全部盈利之和／全部亏损绝对值，两者不是同一个指标。保本所需胜率按已观察盈亏幅度计算，假定无持平，用来解释结果，不是预测。\n\n例如第9条BTC 4小时＋5根确认：23赢12亏，平均盈利0.526R、平均亏损1.159R。约65.71%的胜率仍没达到约68.78%的保本要求，所以每笔平均−0.0517R。止盈是回近侧周一边界；等待20小时后才入场，剩余目标空间可能很小，而原针止损仍较远。表里的计划盈亏比可以检验这种结构。\n\n前两条黄金虽然胜率相同，但每笔毛R分别为+0.7428和+0.2148，已说明‘赢的次数一样’不等于‘赚的钱一样’。第二条配对子集的信号与随机平均R几乎一样。第15条山寨只展示早期：全期和后期仍为负值，可切换到第15条查看。"}</Copy>
 <EvidenceTable id="ranking-streaks" title="止盈、止损与最长连续亏损" queryId={qid} rows={peers} columns={[txt('period_label','时期'),num('tp_count','止盈笔数',0),num('sl_count','止损笔数',0),num('tp_losers','止盈却净亏',0),num('longest_coin_loss','单币最长连亏',0),num('longest_coin_sl','单币最长连损',0),num('market_sl_min','市场最长连损下界',0),num('market_sl_max','市场最长连损上界',0)]}/>
 <Copy id="ranking:streaks">{"‘连亏’指收益为负，‘连损’指止损成交；少数到达止盈的交易仍会被手续费吃掉利润。单币序列按入场时间排列，未决交易打断序列；市场序列只含已判定交易，按退出5分钟区间排列。同区间先后无法从OHLC确定，所以上下界可能不同。跨币同时亏损并不等于一个账户顺次承担同样风险。\n\n浮盈后止损需要原始持仓路径，不能仅用这张汇总表反推。目前完整路径诊断已覆盖山寨4小时＋5根、BTC同配置，以及1小时实体守线研究；对应章节保留浮盈回吐和盈亏K线图。其他配置未补路径扫描，不能把某一配置的比例套到全部16条。"}</Copy>
 <EvidenceTable id="ranking-time" title="所选方案的交易时点与持仓长度" queryId={qid} rows={peers} columns={[txt('period_label','时期'),txt('first_entry','首笔入场UTC'),txt('last_entry','末笔入场UTC'),num('holding_h_median','持仓中位小时上界',2),num('holding_h_max','最长持仓小时上界',2),num('censored','未决',0)]}/>
 <Copy id="ranking:dictionary">{"### 原表字段怎么读\n\n- `signals / closed / censored`：全部信号／已判定／未决。胜率分母是已判定笔数，未决不计胜负。\n- `win_rate`：胜率比例，0.8333即83.33%。\n- `mean_r / total_r / median_r`：平均每笔R／独立R合计／每笔R中位数。1R是原始入场至止损的风险距离；不能把R合计当作账户收益率。\n- `profit_factor`：盈利因子，小于1表示总盈利不足以盖住总亏损。\n- `basis: net / gross`：扣手续费／未扣费用。加密沿用2026-10-23读取的maker 0.02%、taker 0.05%单边费率，未含滑点和资金费；黄金费用未知。\n- `paired_*`：同币、同实际纽约入场周、同方向、同波动桶，三个随机对照均已判定的完整配对子集。\n\n持仓时间使用退出5分钟区间的结束时刻，是最多偏长5分钟的上界。本次只补充中文展示和已有成交的描述统计，没有更改信号、成交、费用、原参数排名或重新回测。后续章节保留全量明细、盈亏样本图和其他独立研究。"}</Copy>
 </section>;
}
