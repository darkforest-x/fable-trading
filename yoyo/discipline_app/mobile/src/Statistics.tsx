import {PageHeader} from './PageHeader';
/** Stitch screen 7ef322e0d5404da9a262019d19c701aa; performance states from Stitch 7decfef946554bc0af201b633556a748; account-scoped display cache. */
import {memo,useEffect,useState} from 'react';
import {type Connection,type State} from './api';
import {cachedStatistics,loadStatistics,statisticsKey,type StatisticsData} from './statisticsData';
import {Icon} from './Icon';
import {feedback} from './feedback';

const fmt=(value?:string|number|null,d=2)=>value==null?'—':Number(value).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const percent=(value?:string|null)=>value==null?'—':fmt(Number(value)*100,1);
const tone=(value?:string|null)=>!value||Number(value)===0?'':Number(value)>0?'long':'short';

const PnlChart=memo(function PnlChart({points}:{points:StatisticsData['curve']}){
 const values=[0,...points.map(p=>Number(p.pnl))];
 const low=Math.min(...values),high=Math.max(...values),span=high-low||1;
 const y=(v:number)=>150-(v-low)/span*120;
 const first=Date.parse(points[0].date),last=Date.parse(points.at(-1)!.date);
 const x=(date:string)=>first===last?294:48+(Date.parse(date)-first)/(last-first)*246;
 const path=`M24,${y(0)} `+points.map(p=>`H${x(p.date)} V${y(Number(p.pnl))}`).join(' ');
 return <div className="pnl-chart"><svg viewBox="0 0 320 190" role="img" aria-label={`已平仓累计净盈亏 ${fmt(points.at(-1)!.pnl)} USDT，按北京时间每日平仓归集`}>
  <line x1="24" x2="296" y1={y(0)} y2={y(0)} stroke="#c6d8cd" strokeDasharray="4 5"/>
  <text x="24" y="16">{fmt(high)} USDT</text><text x="24" y="174">{fmt(low)} USDT</text>
  <path d={path} fill="none" stroke={Number(points.at(-1)!.pnl)>=0?'var(--green)':'var(--red)'} strokeWidth="2.5" strokeLinejoin="round"/>
  <circle cx={x(points.at(-1)!.date)} cy={y(Number(points.at(-1)!.pnl))} r="4" fill="currentColor"/>
 </svg><div className="chart-dates"><span>{points.length===1?'区间起点':points[0].date.slice(5)}</span><span>{points.at(-1)!.date}</span></div>
 <details className="chart-data"><summary>查看每日数据</summary>{points.map(point=><div key={point.date}><span>{point.date}</span><b className={tone(point.pnl)}>{fmt(point.pnl)} USDT</b></div>)}</details></div>;
});

export function Statistics({connection,overview,online,onConnect}:{connection:Connection|null;overview:State|null;online:boolean;onConnect:()=>void}){
 const [days,setDays]=useState(30),[result,setResult]=useState<{key:string;data:StatisticsData}|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[refresh,setRefresh]=useState(0);
 const key=connection?statisticsKey(connection,days):'';
 const data=result?.key===key?result.data:cachedStatistics(connection,days);
 useEffect(()=>{
  setError('');
  if(!connection){setLoading(false);return}
  let active=true;let running=false;let timer:ReturnType<typeof setTimeout>;
  const load=async()=>{
   if(!active||running||document.hidden)return;
   running=true;setLoading(true);
   try{const next=await loadStatistics(connection,days,refresh>0);if(active){setResult({key,data:next});setError('')}}
   catch(e){if(active)setError((e as Error).message)}
   finally{running=false;if(active){setLoading(false);timer=setTimeout(load,10000)}}
  };
  const visible=()=>{clearTimeout(timer);if(!document.hidden)void load()};
  document.addEventListener('visibilitychange',visible);void load();
  return()=>{active=false;clearTimeout(timer);document.removeEventListener('visibilitychange',visible)};
 },[connection,days,refresh,key]);
 const live=connection?.mode==='okx_live';
 const current=connection&&overview&&overview.mode===connection.mode&&overview.account_id===(connection.accountId??'primary')?overview:null;
 const currentAt=current?(live?current.private_at??0:current.server_time):0;
 const cachedAt=data?(data.account_updated_at??data.server_time):0;
 const useCurrent=!!current&&currentAt>0&&currentAt>=cachedAt;
 const equity=useCurrent?current.equity:data?.account.equity;
 const updatedAt=useCurrent?currentAt:cachedAt;
 const stale=!online||!updatedAt||Date.now()/1000-updatedAt>15||(useCurrent&&!!current.private_error);
 const summary=data?.closed,account=data?.account;
 const unrealized=useCurrent?String(Number(current.equity)-Number(current.cash)):account?.unrealized_pnl;
 const updateText=updatedAt?`${stale?'上次记录':'截至'} ${new Date(updatedAt*1000).toLocaleTimeString('zh-CN')}${loading?' · 更新中':''}`:loading?'正在读取…':'待连接';
 return <div className="statistics-page">
  <PageHeader title="账户统计" onSettings={onConnect}/>
  {!connection&&<div className="warning"><Icon name="link" size={18}/><span>连接服务后，读取此 App 管理交易的统计。</span><button onClick={onConnect}>连接</button></div>}
  {error&&<div className="error" role="alert"><span>统计暂不可用{data?'，以下为上次记录':''}：{error}</span><button onClick={()=>setRefresh(v=>v+1)}>重试</button></div>}
  <section className={`card account-hero ${loading&&equity==null?'is-loading':''}`} aria-busy={loading}>
   <div className="section-heading"><h2>{live?'OKX USDT 当前净值':'当前模拟账户净值'}</h2><span>{updateText}</span></div>
   <div className="account-equity"><strong>{fmt(equity)}</strong><span>USDT</span></div>
   <p className="account-total">累计净盈亏 <b className={tone(account?.net_pnl)}>{fmt(account?.net_pnl)} <small>USDT</small></b><span>({percent(account?.return_fraction)}%)</span></p>
   <div className="account-split"><div><span>已实现净盈亏</span><b className={tone(account?.realized_pnl)}>{fmt(account?.realized_pnl)}</b></div><div><span>当前浮动盈亏</span><b className={tone(unrealized)}>{fmt(unrealized)}</b></div></div>
   {stale&&equity!=null&&<p className="micro short">当前显示上次记录，等待账户数据更新。</p>}
  </section>
  <div className="segment stats-period" aria-label="统计周期">{[[7,'近 7 天'],[30,'近 30 天'],[0,'全部']].map(([value,label])=><button key={value} className={days===value?'active':''} aria-pressed={days===value} onClick={()=>{if(days!==value)feedback('light');setDays(Number(value))}}>{label}</button>)}</div>
  <section className="card stats-summary"><div className="section-heading"><h2>已平仓表现</h2><span>仅计完全平仓</span></div>
   <div className="stats-four"><div><span>已平仓</span><b>{summary?.count??'—'}</b><small>笔</small></div><div><span>胜率</span><b>{percent(summary?.win_rate)}</b><small>%</small></div><div><span>盈亏比</span><b>{fmt(summary?.payoff_ratio)}</b><small>均盈 ÷ 均亏</small></div><div><span>平均净 R</span><b>{fmt(summary?.average_net_r)}</b><small>R</small></div></div>
   {!!summary?.count&&summary.payoff_ratio===null&&<p className="micro">{summary.losses===0?'暂无亏损样本':summary.wins===0?'暂无盈利样本':'盈亏比暂无可比样本'}</p>}
   <div className="trade-counts"><span className="long">盈利 <b>{summary?.wins??'—'}</b></span><span className="short">亏损 <b>{summary?.losses??'—'}</b></span><span>净零 <b>{summary?.flat??'—'}</b></span></div>
   <div className="fee-row"><span>{live?'周期内已平仓交易手续费':'周期内已结手续费'}</span><b>{fmt(data?.fees)} <small>USDT</small></b></div>
  </section>
  <section className="card stats-curve"><div className="section-heading"><h2>已平仓累计净盈亏</h2></div><p className={`curve-total ${tone(summary?.net_pnl)}`}>{fmt(summary?.net_pnl)} <small>USDT</small></p>
   {data?.curve.length?<PnlChart points={data.curve}/>:<div className="chart-empty"><Icon name="trend" size={24}/><b>{!connection?'连接后查看统计':loading&&!data?'正在读取记录…':error&&!data?'记录暂不可用':'暂无已平仓记录'}</b><span>完成交易后生成统计曲线</span></div>}
   <p className="micro">按平仓日累计净盈亏，不是历史账户净值。</p>
  </section>
  <section className="card stats-directions"><h2>多空表现</h2><div className="direction-grid">{['long','short'].map(side=>{const row=data?.sides.find(r=>r.side===side);return <div key={side}><b className={side==='long'?'long':'short'}>{side==='long'?'做多':'做空'}</b><p>{row?.count??'—'} <small>笔 · 胜率 {percent(row?.win_rate)}%</small></p><strong className={tone(row?.net_pnl)}>{fmt(row?.net_pnl)} <small>USDT</small></strong></div>})}</div></section>
  <section className="card stats-symbols"><h2>币种表现</h2>{data?.symbols.length?<table><thead><tr><th>合约</th><th>笔数</th><th>净盈亏</th><th>胜率</th></tr></thead><tbody>{data.symbols.map(row=><tr key={row.symbol}><th>{row.symbol.replace('-USDT-SWAP','')}</th><td>{row.count}</td><td className={tone(row.net_pnl)}>{fmt(row.net_pnl)}</td><td>{percent(row.win_rate)}%</td></tr>)}</tbody></table>:<p className="micro">{data?'暂无已平仓合约':'读取服务后显示'}。净盈亏单位为 USDT。</p>}</section>
  <details className="explainer"><summary>统计口径<Icon name="chevron" size={16}/></summary><p>账户概览始终显示当前全量数据。周期为最近 7 / 30 个连续自然时长天数；交易归属以完全平仓时刻为准，手续费按当前交易模式的结算记录归集。</p><p>胜率 = 净盈利笔数 ÷ 已平仓笔数，净零也计入分母。盈亏比 = 平均盈利 ÷ 平均亏损绝对值，两侧均有样本才计算。平均净 R = 每笔完整净盈亏 ÷ 各自初始固定风险 R 后的算术平均。</p><p>{live?'实盘仅统计本 App 管理并完成结算的交易，净盈亏包含交易所返回的手续费与资金费；不混入历史订单。未核对全部充提记录，因此不显示累计账户收益率。':'净盈亏已计入模型手续费及成交滑点；没有再次扣除风险预留。未计资金费，不代表交易所账户收益。'}</p></details>
 </div>;
}
