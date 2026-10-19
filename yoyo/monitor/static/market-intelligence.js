/* Projections of the read-only OKX workstation endpoint. Samples retain their
 * coverage and freshness; missing source values are never replaced by zero. */
(() => {
  'use strict';
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const numeric=x=>x!==null&&x!==undefined&&x!==''&&Number.isFinite(Number(x));
  const value=(x,d=2)=>numeric(x)?Number(x).toLocaleString('en-US',{maximumFractionDigits:d}):'—';
  const compact=x=>numeric(x)?Number(x).toLocaleString('en-US',{notation:'compact',maximumFractionDigits:2}):'—';
  const signed=x=>numeric(x)?(Number(x)>0?'+':'')+compact(x):'—';
  const time=x=>numeric(x)?new Date(Number(x)).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
  const field=(data,name)=>data?.fields?.[name]&&!data.fields[name].stale&&!data.fields[name].error?data.fields[name].data?.[0]:null;
  function metrics(data,quote){
    const funding=field(data,'funding'),oi=field(data,'interest'),mark=field(data,'mark'),index=field(data,'index');
    const fee=numeric(funding?.fundingRate)?(Number(funding.fundingRate)*100).toFixed(4)+'%':'—';
    const flow=data?.flow,liq=data?.liquidations;
    const rows=[['标记 / 指数',value(mark?.markPx,5)+' / '+value(index?.idxPx,5),'OKX · USDT'],
      ['资金费率',fee,funding?'结算 '+time(funding.fundingTime):'等待公开数据'],
      ['合约持仓量 OI',oi?'$'+compact(oi.oiUsd):'—',oi?value(oi.oiCcy,2)+' '+data.symbol.split('-')[0]:'OKX 永续'],
      ['24H 成交额',quote?'$'+compact(quote.volume_usdt_24h):'—','最高 '+value(quote?.high_24h,5)+' / 最低 '+value(quote?.low_24h,5)],
      ['主动成交净额',flow?.available?'$'+signed(flow.delta_usdt):'—',flow?.available?flow.count+' 笔 · '+time(flow.start_ms)+'—'+time(flow.end_ms):'最近成交样本'],
      ['已发生强平',liq?'$'+compact(liq.long_usdt+liq.short_usdt):'—',liq?liq.count+' 条 · 近 3 小时样本':'读取交易所公开记录']];
    return rows.slice(0,4).map(([label,v,note])=>`<article tabindex="0" title="${esc(label+' · '+note)}"><span>${label}</span><strong>${v}</strong><small>${esc(note)}</small></article>`).join('');
  }
  function pending(data,name){const f=data?.fields?.[name];return f?.error?'公开数据暂不可用，自动重试':f?.pending?'正在同步公开数据…':f?.stale?'样本已过期，正在更新…':'正在读取公开数据…';}
  function curve(points){
    if(!points?.length)return '';
    const values=points.map(p=>p.value),lo=Math.min(0,...values),hi=Math.max(0,...values),range=hi-lo||1;
    const pts=values.map((x,i)=>`${(i/Math.max(1,values.length-1)*232).toFixed(1)},${(52-(x-lo)/range*48).toFixed(1)}`).join(' ');
    const positive=values.at(-1)>=0;
    return `<svg class="mt-flow-curve" viewBox="0 0 232 58" role="img" aria-label="当前成交样本累计主动买卖净额"><line x1="0" x2="232" y1="${52+(lo/range)*48}" y2="${52+(lo/range)*48}" stroke="currentColor" opacity=".2"/><polyline points="${pts}" fill="none" stroke="${positive?'#00e599':'#ff3366'}" stroke-width="1.5"/></svg>`;
  }
  function panels(data,currentPrice){
    const liq=data?.liquidations,flow=data?.flow,max=Math.max(1,...(liq?.items||[]).map(r=>r.notional_usdt));
    const liquidation=`<section class="mt-intel-card" id="market-liquidations"><header><h2>强平记录与风险价位</h2><span>OKX</span></header><p class="mt-intel-caption">已发生强平 · 近 3 小时样本</p><div class="mt-liquidation-list">${liq?(liq.items.length?liq.items.slice(0,5).map(r=>`<article class="${r.side==='long'?'down':'up'}" style="--amount:${Math.max(3,r.notional_usdt/max*100)}%"><div><b>${value(r.price,6)}</b><strong>$${compact(r.notional_usdt)}</strong></div><small>${r.side==='long'?'多头':'空头'}强平 · ${time(r.ts)}${numeric(currentPrice)?' · 距现价 '+((r.price/Number(currentPrice)-1)*100).toFixed(2)+'%':''}</small></article>`).join(''):'<p class="mt-intel-empty">公开样本中暂无近期强平</p>'):`<p class="mt-intel-empty">${pending(data,'liquidations')}</p>`}</div><p class="mt-intel-foot">破产价记录，不代表未来清算带。</p></section>`;
    const trades=`<section class="mt-intel-card" id="market-orderflow"><header><h2>主动资金与成交</h2><span>${flow?.available?flow.count+' 笔':'REST'}</span></header>${flow?.available?`<div class="mt-flow-total"><span>样本净额</span><strong class="${flow.delta_usdt>=0?'up':'down'}">$${signed(flow.delta_usdt)}</strong></div>${curve(flow.curve)}<div class="mt-flow-ratio" aria-label="主动买入占比 ${(flow.buy_ratio*100).toFixed(1)}%"><i style="width:${flow.buy_ratio*100}%"></i></div><div class="mt-flow-labels"><span class="up">买入 $${compact(flow.buy_usdt)}</span><span class="down">卖出 $${compact(flow.sell_usdt)}</span></div><div class="mt-trade-tape">${flow.items.slice(0,5).map(r=>`<div><time>${time(r.ts)}</time><span class="${r.side==='buy'?'up':'down'}">主动${r.side==='buy'?'买入':'卖出'}</span><b>$${compact(r.notional_usdt)}</b></div>`).join('')}</div><p class="mt-intel-foot">${time(flow.start_ms)}—${time(flow.end_ms)} · 样本内大额优先</p>`:`<p class="mt-intel-empty">${pending(data,'trades')}</p>`}<details><summary>数据口径</summary><p>OKX 最近最多 500 笔成交，按合约面值换算 USDT。曲线仅覆盖当前样本，不是完整时段 CVD；强平为交易所返回的历史样本。</p></details></section>`;
    return liquidation+trades;
  }
  const api={metrics,panels,field};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.SpikeMarketIntelligence=api;
})();
