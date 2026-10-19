import {$,$$,text,api,poll,num,pct,time,base,params,storage,on,active,contains,byText,route,empty,notice,pendingData,replaceLeaf} from './core.js';
import {chart,series} from './chart.js';
import {previewOrder} from './orders.js';
export function mount(){
 const main=$('main'),first=$('article',main),list=first.parentElement,template=first.cloneNode(true),panel=$('#activeSymbolTitle').closest('section'),top=$('section',main),inputs=$$('input',panel),execute=$('#btnExecuteOrder');
 let rows=[],selected=null,filter='',generation=0,customStop=false;const searchSymbol=params.get('symbol');
 pendingData(main);replaceLeaf(top,'Binance Futures API 直连','OKX · 均线密集专用账户');replaceLeaf(top,'基于 SMA 20/60/120 与 EMA 20/60/120 六轨标准差收敛算法与爆发动能识别','SMA / EMA 20、60、120 · YOLO 检测记录，首次发现与确认时间分别保留');replaceLeaf(top,'≤ 1.85%','模型配置');
 const engineState=$$('span',top).find(e=>e.textContent.includes('巡检中'));text(engineState,'读取巡检状态…');
 const svg=$$('svg',panel).find(e=>(e.getAttribute('viewbox')||e.getAttribute('viewBox'))?.includes('800'));const plot=chart(svg,{legend:r=>{text($('#activePriceTag'),'$'+num(r.c,8));$$('span',panel).filter(e=>/^(SMA|EMA)(20|60|120):$/.test(e.textContent.trim())).forEach(e=>text(e.nextElementSibling,num(r[e.textContent.trim().replace(':','').toLowerCase()],8)));}});plot.set([]);
 // Source annotations are illustrative; remove only their text/geometry until
 // a selected detection supplies matching evidence.
 for(const el of $$('div,span,p',panel))if(!el.children.length&&!el.closest('button')&&/密集挤压突破已确认|均线密集粘合带|密集区下沿防御止损|当前 K 线|筹码平均换手|布林带带宽|能量潮 OBV|触发后第|多时间框架标准差/.test(el.textContent))text(el,'等待所选检测记录');
 const formulas=$$('div',panel).filter(e=>!e.children.length&&/MDI =|Trigger:/.test(e.textContent));formulas.forEach(e=>text(e,'实际检测依据：所选模型、检测窗口及确认收盘时间'));
 inputs.forEach((input,i)=>{input.value='';input.readOnly=i!==1;input.placeholder=i===1?'5m 六均线外侧止损':'预览后计算';});on(inputs[1],()=>customStop=true,'input');
 text(execute,'预览实盘订单');execute.disabled=true;on(execute,async()=>{execute.disabled=true;try{await previewOrder(selected,{stop:customStop?inputs[1].value:undefined});}finally{execute.disabled=!selected;}});
 on(contains('加入回测观察池',panel),()=>{if(!selected)return;const saved=storage('observations')||[];if(!saved.some(r=>r.id===selected.id)){saved.push(selected);storage('observations',saved);}notice('已加入本地回测观察池。研究工作区可导出这些记录。');});
 on(contains('调参',top),()=>route('research'));const filterButtons=$$('#filterButtonGroup button');filterButtons.forEach((b,i)=>on(b,()=>{filter=['','long','short','score'][i];active(b);render();}));
 on(byText('sync',main),()=>refresh());on(byText('filter_list',main),()=>{filter=filter==='score'?'':'score';render();});
 on(contains('6条均线带',panel),()=>plot.ma());on(contains('量能OBV',panel),()=>notice('当前图表使用实际 OHLCV 与六条均线；OBV 尚未接入，未显示演示值。'));
 for(const b of $$('button',panel).filter(b=>/^(15m|1H|4H|1D)$/.test(b.textContent.trim())))on(b,()=>{if(selected)route('market',{symbol:selected.symbol,timeframe:b.textContent.trim()});});
 for(const b of $$('button',panel).filter(b=>/^(10%|25%|50%|MAX)$/.test(b.textContent.trim())))on(b,()=>notice('实盘数量按现有单笔 1R＝账户权益 3% 计算，不能用演示比例覆盖风控配置。'));
 empty(list,'正在读取检测记录…');
 function render(){let items=rows.filter(r=>!searchSymbol||r.symbol===searchSymbol).filter(r=>!filter||filter==='score'||r.side===filter);if(filter==='score')items=items.slice().sort((a,b)=>b.confidence-a.confidence);const frag=document.createDocumentFragment();
  for(const r of items){const c=template.cloneNode(true);c.id='detection-'+r.detection_id;c.tabIndex=0;c.setAttribute('role','button');c.setAttribute('aria-label',`${base(r.symbol)} ${r.timeframe} ${r.side}`);pendingData(c);text($('.w-10',c),base(r.symbol));text($('.text-headline-md',c),base(r.symbol)+' / USDT');text($('.text-label-numeric-lg',c),num(r.confidence*100,1));replaceLeaf(c,'突破概率评级','模型置信度');text($('.bg-primary-container\\/20',c),r.side==='long'?'多头形态':'空头形态');
   const meta=$$('.text-on-surface-variant.font-code-snippet > span',c);text(meta[0],r.timeframe+' · '+time(r.first_observed_ms));text($('strong',c),'—');replaceLeaf(c,'均线离散度 (MDI)','确认后 K 线');text($('.col-span-5 .font-bold',c),String(r.post_bars??'—'));text($('.col-span-5 > span',c),'核心 '+r.core_bars+' 根');text($('.col-span-7 span',c),'确认 '+time(r.first_confirmed_ms));text($('.font-mono',c),'扫描 '+r.scan_count+' 次');const bar=$('.h-full',c);if(bar)bar.style.width=`${Math.max(0,Math.min(100,r.confidence*100))}%`;$('svg',c)?.replaceChildren();
   on(c,()=>select(r));on(c,e=>{if(e.key==='Enter')select(r);},'keydown');frag.append(c);
  }list.replaceChildren(frag);if(!items.length)empty(list,'没有符合筛选条件的检测记录');if(!selected&&items.length)select(items[0]);
 }
 async function select(r){selected=r;customStop=false;const seq=++generation;text($('#activeSymbolTitle'),base(r.symbol)+'/USDT');text($('#activePriceTag'),'读取中…');execute.disabled=false;inputs.forEach(i=>i.value='');plot.set([]);for(const c of $$('article',list))c.classList.toggle('bg-surface-container-high',c.id==='detection-'+r.detection_id);
  try{const data=await api(`/api/yolo-detect/jobs/${r.job_id}/results/${encodeURIComponent(r.symbol)}`);if(seq!==generation)return;plot.set(data.candles||[]);const metrics=$$('.text-headline-lg',panel);text(metrics[0],String(r.core_bars));text(metrics[1],pct(r.confidence*100));text(metrics[2],String(r.scan_count));text(inputs[0],null);inputs[0].value=String(data.candles?.at(-1)?.close||'');}catch(e){if(seq===generation)text($('#activePriceTag'),e.message);}
 }
 const refresh=poll(async()=>{try{const d=await api('/api/yolo-detect/discoveries?limit=100');rows=d.items||[];render();text(engineState,d.latest_job?`${d.latest_job.status} · ${d.latest_job.progress?.completed||0}/${d.latest_job.progress?.total||0}`:'暂无检测任务');text($('strong',top),d.latest_job?.progress?.elapsed_seconds?num(d.latest_job.progress.elapsed_seconds)+'s':'—');}catch(e){if(!rows.length)empty(list,e.message);}},15000);
 poll(async()=>{const d=await api('/api/manual-live/dashboard?view=workspace'),account=$$('div',top).find(e=>e.classList.contains('xl:col-span-5')),vals=$$('.text-label-numeric-lg',account),s=d.executions?.statistics||{};[d.account?.data?.total_equity_usdt,s.realized_net_usdt,d.account?.data?.balance?.upl].forEach((v,i)=>text(vals[i],'$'+num(v,2)));const buttons=$$('button',account),positions=d.account?.data?.positions||[];buttons.forEach((b,i)=>{text(b,positions[i]?`${base(positions[i].instId)} ${num(positions[i].upl)} USDT`:'无持仓');on(b,()=>route('account'));});account.title=`账户数据：${d.account?.status} · ${time(d.account?.updated_ms)}`;},10000);
}
