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
  const glyphs={reclaimed:'✓',not_reclaimed:'×',pending:'◷',checking:'…',unavailable:'–'};
  const meanings={...labels,checking:'核对中'};
  const option=(value,label)=>`<option value="${value}">${label}</option>`;
  function toolbar(){
    return `<label class="tl-select"><select aria-label="触价来源" data-tl-source>${Object.entries(sources).map(([key,name])=>option(key,name)).join('')}</select></label>
      <label class="tl-select tl-periods"><select aria-label="收回周期" data-tl-period>${option('all','全部触价')}${periods.map(p=>option(p,p+' 收回')).join('')}</select></label>`;
  }
  function bodyHTML(){
    const key=Object.entries(meanings).map(([mark,label])=>`<span><b data-mark="${mark}">${glyphs[mark]}</b> ${label}</span>`).join('');
    return `<div class="tl-key" id="rp-touch-key" hidden>${key}</div>
      <div class="tl-scroll">
        <div class="tl-columns" aria-hidden="true"><span>币种</span><span class="tl-price">触价</span><span class="tl-period-head">${periods.map(p=>`<span>${p}</span>`).join('')}</span><span class="tl-tv-head">TV</span></div>
        <div class="tl-rows"></div><button type="button" class="rp-more" data-tl-more hidden>再显示 50 条</button>
        <p role="status"></p><button type="button" data-tl-retry hidden>重新读取</button>
      </div>`;
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
    const states=cloud?[]:periods.map(p=>{
      const value=row.reclaims?.[p],status=value?.status||'unavailable';
      const mark=status==='pending'&&value.reason!=='candle_not_closed'?'checking':status;
      const description=p+' '+(meanings[mark]||meanings.unavailable);
      const detail=Number.isFinite(value?.close)?description+' · 收盘 '+price(value.close):description;
      return {mark,description,detail};
    });
    const side=upper?'上轨':'下轨',active=id===selected,level=price(cloud?row.entry:row.level);
    const scoreText=Number.isFinite(score)?'评分 '+score.toFixed(4):'';
    const rowName=[coin,side,'触价 '+level,stamp(when),scoreText,...states.map(s=>s.description)].filter(Boolean).join('，');
    return `<article class="tl-row" data-key="${esc(id)}" data-selected="${active}">
      <button class="tl-pick" type="button" data-tl-pick="${esc(id)}" aria-label="查看图表，${esc(rowName)}" aria-pressed="${active}">
        <span class="tl-pair"><span class="tl-name"><strong>${esc(coin)}</strong><span class="tl-side" data-side="${upper?'upper':'lower'}">${side}</span></span><small>${stamp(when)}</small></span>
        <span class="tl-price"><b>${level}</b>${scoreText?`<small>${scoreText}</small>`:''}</span>
        ${cloud?'':`<span class="tl-marks">${states.map(s=>`<span data-mark="${esc(s.mark)}" title="${esc(s.detail)}">${glyphs[s.mark]||glyphs.unavailable}</span>`).join('')}</span>`}
      </button>
      <a class="tl-tv" href="${esc(href)}" ${href.startsWith('https:')?'target="_blank" rel="noopener"':''}
         aria-label="在 TradingView 打开 ${esc(coin)} ${esc(tvPeriod)}" title="TradingView · ${esc(coin)} ${esc(tvPeriod)}">
         <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 3h4v4m0-4L7 9M7 4H4a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V9"/></svg>
      </a>
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
    const sourceSelect=controls.querySelector('[data-tl-source]'),periodSelect=controls.querySelector('[data-tl-period]');
    const periodField=controls.querySelector('.tl-periods'),periodHead=host.querySelector('.tl-period-head');
    const keyButton=controls.querySelector('[data-tl-key]'),keyPanel=host.querySelector('.tl-key');
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
      sourceSelect.value=next?.source||'week';
      periodSelect.value=next?.period||'all';
      const hasReclaims=['week','month'].includes(next?.source);
      periodHead.hidden=periodField.hidden=keyButton.hidden=!hasReclaims;
      if(!active||!hasReclaims){
        keyPanel.hidden=true;
        keyButton.setAttribute('aria-expanded','false');
      }
      host.classList.toggle('is-cloud',next?.source==='cloud');
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
    sourceSelect.addEventListener('change',()=>onSource(sourceSelect.value));
    periodSelect.addEventListener('change',()=>onPeriod(periodSelect.value));
    keyButton.addEventListener('click',()=>{
      keyPanel.hidden=!keyPanel.hidden;
      keyButton.setAttribute('aria-expanded',String(!keyPanel.hidden));
    });
    host.addEventListener('click',e=>{
      const b=e.target.closest('button');if(!b||!context)return;
      if(b===retry){load();return;}
      if(b===more){if(isCloud(context.source))load(shown+50);else{shown+=50;paint();}return;}
      if(b.dataset.tlPick){const row=rowIndex.get(b.dataset.tlPick);if(row)onPick(row,context,b);}
    });
    return {set,refresh:load};
  }
  const api={toolbar,bodyHTML,create,selectRows,rowHTML,periods,sources};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else window.SpikeTouchLedger=api;
})();
