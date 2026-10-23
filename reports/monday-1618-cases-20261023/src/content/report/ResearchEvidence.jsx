import React, {useState} from 'react';
import {DataComponent, DataTable, Dropdown, SegmentedControl, RichNarrative} from '../../data-app-public.jsx';

export const fmt=(v,n=3)=>v==null?'—':Number(v).toLocaleString('zh-CN',{minimumFractionDigits:n,maximumFractionDigits:n});
export const txt=(field,label)=>({field,label});
export const num=(field,label,n=3)=>({field,label,renderCell:v=>fmt(v,n)});
export const pct=(field,label)=>({field,label,renderCell:v=>v==null?'—':`${fmt(v*100,2)}%`});
export const periods={all:'全历史',before2023:'2023年前',from2023:'2023年起',earlier:'2023年前',later:'2023年起'};
export const clock=v=>v?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(v.replace(' ','T'))):'—';
export function Copy({id,children}) {return <RichNarrative id={id} value={children}/>;}
export function EvidenceTable({id,title,queryId,rows,columns,sourceRows=rows,controls,searchable=false}) {return <DataComponent id={id} title={title} queryId={queryId} sourceRows={sourceRows} displayRows={rows} kind="table" variant="card" headerControls={controls}><DataTable label={title} rows={rows} columns={columns} searchable={searchable}/></DataComponent>;}
export function downloadRows(rows,name) {
 const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))];
 const quote=v=>'"'+String(v==null?'':typeof v==='object'?JSON.stringify(v):v).replaceAll('"','""')+'"';
 const csv='\ufeff'+keys.map(quote).join(',')+'\n'+rows.map(r=>keys.map(k=>quote(r[k])).join(',')).join('\n');
 const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function ResearchGallery({id,queryId,samples,images,title}) {
 const [result,setResult]=useState('ALL'),[direction,setDirection]=useState('ALL'),[chosen,setChosen]=useState(''),[view,setView]=useState('detail');
 const pool=samples.filter(r=>(result==='ALL'||r.outcome===result)&&(direction==='ALL'||r.direction===direction));
 const s=pool.find(r=>r.sample_id===chosen)??pool[0];
 const fields=s?[{label:'插针开盘（北京）',value:clock(s.pin_time)},{label:'5根确认完成 / 下一根开盘入场',value:clock(s.entry_time)},{label:'退出5m区间（北京）',value:`${clock(s.exit_interval_start)} → ${clock(s.exit_interval_end)}`},{label:'1.618 / 原针止损',value:`${fmt(s.level,8)} / ${fmt(s.stop,8)}`},{label:'入场 / 近侧周一止盈',value:`${fmt(s.entry,8)} / ${fmt(s.target_price,8)}`},{label:'毛R / 费用R / 净R',value:`${fmt(s.gross_r)} / ${fmt(s.fee_r)} / ${fmt(s.net_r)}`},{label:'计划RR / 风险距离bp',value:`${fmt(s.planned_rr)} / ${fmt(s.risk_bp,2)}`},{label:'持仓时长上界',value:`${fmt(s.holding_h,2)} 小时`},{label:'抽样分层总数 / 交易ID',value:`${s.sampling_pool} / ${s.trade_id}`}]:[];
 return <DataComponent id={id} queryId={queryId} title={title} kind="custom" variant="card" sourceRows={pool} displayRows={s?[s]:[]} headerControls={<><Dropdown label="样本盈亏" showLabel value={result} choices={['ALL','盈利','亏损']} choiceLabels={{ALL:'全部盈亏'}} onChange={setResult}/><Dropdown label="样本方向" showLabel value={direction} choices={['ALL','多','空']} choiceLabels={{ALL:'多空全部'}} onChange={setDirection}/></>}>
 {s&&<><div className="specimen-controls"><Dropdown label="交易样本" showLabel value={s.sample_id} choices={pool.map(r=>r.sample_id)} choiceLabels={Object.fromEntries(pool.map(r=>[r.sample_id,`${r.sample_id} · ${r.symbol} · ${r.direction}${r.outcome} ${fmt(r.net_r)}R`]))} onChange={setChosen}/><SegmentedControl label="K线范围" value={view} onChange={setView} options={[{value:'detail',label:'入场局部'},{value:'full',label:'持仓全程'}]}/></div>
 <div className="candle-scroll" data-reviewed-rows><img className="candle-image" src={images[`${s.sample_id}-${view}`]} alt={`${s.sample_id} ${s.symbol} ${s.direction}${s.outcome} ${view==='detail'?'入场局部':'持仓全程'}`}/></div>
 <div className="sample-details"><DataTable label="样本交易时点与价格" rows={fields} columns={[txt('label','字段'),txt('value','记录')]} searchable={false}/><DataTable label="五根确认逐根检查" rows={JSON.parse(s.checks_json).map(r=>({...r,time:clock(r.open_time),rule:r.passed?'满足收盘守线':'不满足'}))} columns={[num('number','第几根',0),txt('time','开盘（北京）'),num('open','开'),num('high','高'),num('low','低'),num('close','收'),txt('rule','检查')]} searchable={false}/></div></>}
 {!s&&<p data-reviewed-rows>这个分层没有样本。</p>}
 </DataComponent>;
}
