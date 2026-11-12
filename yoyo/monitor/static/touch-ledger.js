/* Shared 1.618 touch ledger. Only the visible source owns a polling request. */
(() => {
  'use strict';
  const list=typeof module!=='undefined'&&module.exports?require('./monday-list-model.js'):window.SpikeMondayList;
  const periods=['1H','4H','12H'];
  const sources={week:'周一',month:'月首周',cloud:'12H 云带',paper:'策略记录'};
  const labels={reclaimed:'已收回',not_reclaimed:'未收回',pending:'待收盘',unavailable:'缺数据'};
  const timeFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  const stamp=ms=>Number.isFinite(ms)&&ms>0?timeFormat.format(ms):'—';
  const {esc,price}=list;
  const isCloud=source=>source==='cloud';
  function toolbar(){
    const buttons=Object.entries(sources).map(([id,label])=>
      `<button type="button" data-tl-source="${id}" aria-pressed="false">${label}</button>`);
    const filters=['all',...periods].map(period=>
      `<button type="button" data-tl-period="${period}" aria-pressed="false">${period==='all'?'全部触价':period+' 收回'}</button>`);
    return `<div class="tl-sources" role="group" aria-label="触价来源">${buttons.join('')}</div>
      <div class="tl-periods" role="group" aria-label="按收回周期筛选">${filters.join('')}</div>`;
  }
  const eventTime=row=>row.source==='history'?row.candle_ms:row.occurred_ms??row.observed_ms??row.candle_ms;
  function selectRows(items,query,period,sort,scores){
    const q=query.trim().toUpperCase();
    const ranked=row=>Number.isFinite(scores?.get(row.event_id))?scores.get(row.event_id):-Infinity;
    return items.filter(row=>(!q||row.symbol.includes(q))&&(period==='all'||row.reclaims?.[period]?.status==='reclaimed'))
      .sort((a,b)=>(sort==='score'?ranked(b)-ranked(a):0)||(sort==='time_asc'?1:-1)*(eventTime(a)-eventTime(b))||a.event_id.localeCompare(b.event_id));
  }
  function rowHTML(row,{source,period,chartPeriod,selected,platform,score}){
    const cloud=isCloud(source),id=cloud?row.id:row.event_id,coin=row.symbol.replace(/-USDT-SWAP$/,'');
    const upper=cloud?row.side===-1:row.side==='upper';
    const tvPeriod=cloud?'12H':period==='all'?chartPeriod:period;
    const href=list.tradingViewURL(row.symbol,platform,tvPeriod),when=cloud?row.confirmed_ms:eventTime(row);
    const states=cloud?'':periods.map(p=>{
      const value=row.reclaims?.[p],state=value?.status||'unavailable';
      const label=state==='pending'&&value.reason!=='candle_not_closed'?'核对中':labels[state]||'待核对';
      const close=Number.isFinite(value?.close)?' · 收盘 '+price(value.close):'';
      return `<span class="tl-status" data-status="${esc(state)}" title="${esc(p+' '+label+close)}">${p} ${label}</span>`;
    }).join('');
    const side=upper?'上轨':'下轨',active=id===selected;
    return `<article class="tl-row" data-key="${esc(id)}" data-selected="${active}">
      <button class="tl-pick" type="button" data-tl-pick="${esc(id)}" aria-label="查看 ${esc(coin)} ${side}触价图表" aria-pressed="${active}">
        <span class="tl-line"><strong>${esc(coin)}</strong><span class="tl-side" data-side="${upper?'upper':'lower'}">${side}</span><b>${price(cloud?row.entry:row.level)}</b></span>
        <span class="tl-time">${cloud?'12H 云带收回 · ':''}${stamp(when)}${Number.isFinite(score)?' · 评分 '+score.toFixed(4):''}</span>
        ${states?`<span class="tl-states">${states}</span>`:''}
      </button>
      <a class="tl-tv" href="${esc(href)}" ${href.startsWith('https:')?'target="_blank" rel="noopener"':''}
         aria-label="在 TradingView 打开 ${esc(coin)} ${esc(tvPeriod)}" title="${esc(tvPeriod)} · TradingView">TV ↗</a>
    </article>`;
  }

  async function readPage(path,params,signal){
    const response=await fetch(path+'?'+params,{signal,credentials:'same-origin',cache:'no-store'});
    if(!response.ok)throw Error('读取失败');
    const page=await response.json();
    if(!Array.isArray(page.items))throw Error('数据格式异常');
    return page;
  }

  async function readTouches({source,month},signal){
    const params=new URLSearchParams({kind:source==='month'?'month_first_week':'monday',limit:'1000'});
    if(source==='month'&&month)params.set('month',month);
    let page=await readPage('/api/touch-reclaims',params,signal);
    const items=page.items.slice(),visited=new Set();
    params.set(source==='month'?'month':'week',page.period);
    while(page.truncated){
      const cursor=page.next_cursor;
      if(!cursor||visited.has(cursor))throw Error('分页游标异常');
      visited.add(cursor);
      params.set('after',cursor);
      page=await readPage('/api/touch-reclaims',params,signal);
      items.push(...page.items);
    }
    return {...page,items};
  }

  async function readCloud({q,sort},target,signal){
    const params=new URLSearchParams({scope:'all',search:q,sort:sort==='time_asc'?'oldest':'newest'});
    const items=[];
    let page;
    do{
      params.set('offset',String(items.length));
      params.set('limit',String(Math.min(target-items.length,1000)));
      page=await readPage('/api/cloud-signals',params,signal);
      items.push(...page.items);
    }while(page.items.length&&items.length<Math.min(target,page.total));
    return {...page,items};
  }
  function create({host,controls,onSource,onPeriod,onPick,onCount,platform}){
    const scroll=host.querySelector('.tl-scroll'),rows=host.querySelector('.tl-rows'),status=host.querySelector('[role=status]'),more=host.querySelector('[data-tl-more]'),retry=host.querySelector('[data-tl-retry]');
    let context=null,queryKey='',controller=null,timer=null,generation=0,dataset=null,problem='',shown=50,lastMarkup='',rowIndex=new Map();
    function cancel(){generation++;clearTimeout(timer);controller?.abort();controller=null;}
    async function load(target=shown){
      if(!context||controller||document.hidden)return;
      clearTimeout(timer);
      const request=new AbortController(),epoch=generation,cloud=isCloud(context.source);
      const timeout=setTimeout(()=>request.abort(),20000);
      controller=request;more.disabled=true;
      try{
        const page=cloud?await readCloud(context,target,request.signal):await readTouches(context,request.signal);
        if(epoch!==generation)return;
        dataset=page;
        if(cloud)shown=target;
        problem='';
      }catch(err){
        if(epoch===generation)problem=request.signal.aborted?'读取超时':err.message||'网络异常';
      }finally{
        clearTimeout(timeout);
        if(epoch===generation){
          controller=null;
          paint();
          timer=setTimeout(load,cloud?60000:15000);
        }
      }
    }
    function paint(){
      if(!context)return;
      const {source,q,period,sort,chartPeriod,selected,scores}=context,cloud=isCloud(source);
      const found=cloud?dataset?.items||[]:selectRows(dataset?.items||[],q,period,sort,scores),visible=found.slice(0,shown);
      rowIndex=new Map(visible.map(row=>[cloud?row.id:row.event_id,row]));
      const markup=visible.map(row=>rowHTML(row,{source,period,chartPeriod,selected,platform,score:scores?.get(row.event_id)})).join('');
      if(markup!==lastMarkup){
        const focus=document.activeElement,key=focus?.closest('.tl-row')?.dataset.key,action=focus?.classList.contains('tl-tv')?'.tl-tv':'.tl-pick',top=scroll.scrollTop;
        const edge=scroll.getBoundingClientRect().top;
        const anchor=top>1&&Array.from(rows.children).find(row=>row.getBoundingClientRect().bottom>edge);
        const anchorKey=anchor?.dataset?.key,offset=anchorKey?anchor.getBoundingClientRect().top-edge:0;
        rows.innerHTML=markup;lastMarkup=markup;
        if(key)Array.from(rows.children).find(e=>e.dataset.key===key)?.querySelector(action)?.focus({preventScroll:true});
        const current=anchorKey&&Array.from(rows.children).find(row=>row.dataset.key===anchorKey);
        if(current)scroll.scrollTop+=current.getBoundingClientRect().top-edge-offset;else scroll.scrollTop=top;
      }
      const count=cloud?dataset?.total||0:found.length;
      onCount(dataset?count:null);
      if(problem)status.textContent=dataset?'更新失败 · 保留上次记录':'暂时无法读取'+sources[source]+'记录';
      else if(!dataset)status.textContent='正在读取触价…';
      else{
        const pending=!cloud&&period!=='all'&&dataset.items.some(row=>
          row.symbol.includes(q.toUpperCase().trim())&&row.reclaims?.[period]?.reason?.startsWith('history_fetch_'));
        if(count)status.textContent=sources[source]+' · '+count+' 条 · 北京时间'+(pending?' · 部分待核对':'');
        else if(pending)status.textContent='正在核对已收盘 K 线…';
        else status.textContent=q||period!=='all'?'没有符合筛选的触价':'暂无触价记录';
      }
      retry.hidden=!problem;more.hidden=!dataset||visible.length>=count;more.disabled=!!controller;
    }
    function set(next){
      const active=next?.visible&&next.source!=='paper'&&!document.hidden;
      controls.hidden=!next?.events;
      controls.querySelector('.tl-periods').hidden=!['month','week'].includes(next?.source);
      controls.querySelectorAll('[data-tl-source]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.tlSource===next?.source)));
      controls.querySelectorAll('[data-tl-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.tlPeriod===next?.period)));
      host.hidden=!next?.events||next.source==='paper';
      if(!active){if(context){cancel();context=null;}return;}
      const month=next.month||'';
      const key=JSON.stringify([next.source,month,...(isCloud(next.source)?[next.q,next.sort]:[])]);
      const resume=!context;
      const monthResolved=next.source==='month'&&context?.source==='month'&&dataset?.month===month;
      const fetchChanged=key!==queryKey&&!monthResolved;
      if(fetchChanged){cancel();dataset=null;queryKey=key;problem='';shown=50;scroll.scrollTop=0;}
      else if(context&&(context.period!==next.period||context.q!==next.q||context.sort!==next.sort)){shown=50;scroll.scrollTop=0;}
      queryKey=key;
      const changed=resume||fetchChanged||context.q!==next.q||context.sort!==next.sort;
      const repaint=changed||context.period!==next.period||context.chartPeriod!==next.chartPeriod||context.selected!==next.selected||context.scores!==next.scores;
      context=next;
      if(resume||fetchChanged){clearTimeout(timer);timer=setTimeout(load,isCloud(next.source)&&next.q?200:0);}
      if(repaint)paint();
    }
    controls.addEventListener('click',e=>{const b=e.target.closest('button');if(b?.dataset.tlSource)onSource(b.dataset.tlSource);if(b?.dataset.tlPeriod)onPeriod(b.dataset.tlPeriod);});
    host.addEventListener('click',e=>{
      const b=e.target.closest('button');if(!b||!context)return;
      if(b===retry){load();return;}
      if(b===more){if(isCloud(context.source))load(shown+50);else{shown+=50;paint();}return;}
      if(b.dataset.tlPick){const row=rowIndex.get(b.dataset.tlPick);if(row)onPick(row,context,b);}
    });
    return {set,refresh:load};
  }
  const api={toolbar,create,selectRows,rowHTML,periods,sources};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else window.SpikeTouchLedger=api;
})();
