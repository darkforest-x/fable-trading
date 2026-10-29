/* Stitch 8e070b181b7a4b43a48692d1b8f7a2ee. Saved funded-trade browser.
 * List filters never select a trade; only opening a strategy or choosing a row
 * changes the wallpaper. A generation owns each paginated read, including retry. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tracks=['60','5','v3','v3add','spike','v3lock','allin3','v3add20'];
  const steps={'5m':300000,'15m':900000,'1H':3600000,'4H':14400000,'12H':43200000,'1D':86400000};
  const symbolOK=v=>typeof v==='string'&&/^[A-Z0-9]{1,32}USDT$/.test(v);
  const valid=r=>r&&Number.isInteger(r.id)&&r.id>0&&symbolOK(r.symbol)&&[1,-1].includes(r.side)&&['entry_ms','exit_ms','pnl','return_pct'].every(k=>Number.isFinite(r[k]))&&r.entry_ms>0&&r.exit_ms>=r.entry_ms;
  const base=s=>s.replace(/USDT$/,''),label=t=>({60:'60m',5:'5m'}[t]||t);
  const money=v=>(v>0?'+':'')+v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  const tone=v=>v>0?'rp-positive':v<0?'rp-negative':'';
  const format=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  const date=t=>format.format(t);
  function periodFor(row,period){return Object.keys(steps).find(p=>steps[p]>=steps[period]&&(row.exit_ms-row.entry_ms)/steps[p]<=5000)||'1D';}
  function validate(p,q,offset){
    if(p?.schema!==1||p.track!==q.track||p.sort!==q.sort||p.direction!==q.direction||p.symbol!==q.symbol||p.offset!==offset||!Array.isArray(p.rows)||!p.rows.every(valid)||new Set(p.rows.map(r=>r.id)).size!==p.rows.length||!Number.isInteger(p.total)||p.total<offset+p.rows.length||typeof p.has_more!=='boolean'||!p.meta||p.meta.track!==q.track||!Array.isArray(p.ranking)||!p.ranking.every(r=>symbolOK(r.symbol)&&Number.isFinite(r.pnl)&&Number.isInteger(r.trades))||(p.focus!=null&&!valid(p.focus)))throw Error('回测账本返回的数据不完整');
    return p;
  }
  function createModel({read,cancel=()=>{},change=()=>{},focus=()=>{}}){
    let version=0,pending=null,auto=false;
    let state={track:'',sort:'time',direction:'desc',symbol:'',rows:[],data:null,total:0,has_more:false,busy:false,error:''};
    function pause(){version++;cancel();pending=null;state.busy=false;}
    function reset(){pause();state={...state,rows:[],total:0,has_more:false,data:null,error:''};}
    async function load(more=false){
      if(pending||!state.track)return pending;
      const revision=version,q={track:state.track,sort:state.sort,direction:state.direction,symbol:state.symbol},offset=more?state.rows.length:0;
      state.busy=true;state.error='';change(state);
      pending=Promise.resolve().then(async()=>{
        if(revision!==version)return;
        try{const value=validate(await read({...q,offset,limit:50}),q,offset);if(revision!==version)return;
          const rows=more?[...state.rows,...value.rows]:value.rows;
          if(new Set(rows.map(r=>r.id)).size!==rows.length)throw Error('回测分页出现重复记录');
          state={...state,data:value,rows,total:value.total,has_more:value.has_more};
          if(auto&&value.focus){auto=false;focus(value.focus,q.track);}
        }catch(e){if(revision===version)state.error=e.message||'回测读取失败，请重试';}
        finally{if(revision===version){state.busy=false;pending=null;change(state);}}
      });return pending;
    }
    return {load,pause,getState:()=>state,select(track){if(!tracks.includes(track))return;reset();state={...state,track,sort:'time',direction:'desc',symbol:''};auto=true;return load();},
      filter(symbol){if(symbol&&!symbolOK(symbol))return;reset();state.symbol=symbol;auto=false;return load();},
      sort(key){if(!['time','pnl'].includes(key))return;const direction=state.sort===key&&state.direction==='desc'?'asc':'desc';reset();state.sort=key;state.direction=direction;auto=false;return load();}};
  }
  function rowHTML(r,selected){
    return `<button type="button" class="bt-row" data-bt-trade="${r.id}" aria-pressed="${r.id===selected}" aria-label="查看 ${esc(base(r.symbol))} ${date(r.entry_ms)} 回测交易"><span class="bt-coin"><b>${esc(base(r.symbol))}</b><em class="${r.side===1?'rp-positive':'rp-negative'}">${r.side===1?'多':'空'}</em>${r.id===selected?'<small>图表焦点</small>':''}</span><span class="bt-pnl ${tone(r.pnl)}"><b>${money(r.pnl)} <small>USDT</small></b><small>${money(r.return_pct)}%</small></span><span class="bt-times"><span>开 ${date(r.entry_ms)}</span><span>平 ${date(r.exit_ms)}</span></span></button>`;
  }
  const api={createModel,validate,valid,rowHTML,periodFor,base,date};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  function create({root,onOpen=()=>{},onSelect}){
    let opened=false,selected=null,returnFocus=null,request=null;
    const panel=document.createElement('section');panel.id='strategy-backtest-panel';panel.className='bt-panel rp-glass';panel.inert=true;panel.setAttribute('aria-hidden','true');panel.setAttribute('aria-label','历史回测交易');
    panel.innerHTML='<header class="bt-heading" data-yd-sheet-drag><strong tabindex="-1" data-bt-title>回测交易</strong><button type="button" data-bt-close aria-label="收起回测交易">×</button></header><div class="bt-controls"><div class="bt-badges"><span>历史回测 · Binance</span><button type="button" data-bt-top title="查看累计净盈亏第一名">Top 1 · 读取中</button></div><div class="bt-sort"><select aria-label="回测币种" data-bt-symbol><option value="">全部币种</option></select><button type="button" data-bt-sort="time" aria-pressed="true">开仓时间 ↓</button><button type="button" data-bt-sort="pnl" aria-pressed="false">净收益</button></div><small>时间为北京时间 · 收益已扣原研究成本</small></div><div class="bt-scroll"><p data-bt-status role="status"></p><button type="button" data-bt-retry hidden>重试读取</button><div class="bt-rows"></div><button type="button" class="bt-more" data-bt-more hidden>再显示 50 笔</button><p class="bt-count"></p><details class="bt-notes"><summary>历史版本与统计口径</summary><p></p></details></div>';
    root.append(panel);
    const node=s=>panel.querySelector(s),sheet=window.SpikeYoloPreviewSheet.bind({panel,isOpen:()=>opened,dismiss:()=>setOpen(false)});
    async function read(params){const c=new AbortController();request=c;const timeout=setTimeout(()=>c.abort(),20000);
      try{const r=await fetch('/api/strategy-backtest?'+new URLSearchParams(params),{signal:c.signal,cache:'no-store'});const value=await r.json();if(!r.ok)throw Error(value.detail||'回测读取失败');return value;}
      catch(e){if(e.name==='AbortError')throw Error('回测读取超时，请重试');throw e;}
      finally{clearTimeout(timeout);if(request===c)request=null;}}
    function choose(row,track,manual=false){selected=row.id;onSelect({track,row});render();if(manual&&matchMedia('(max-width:700px)').matches)setOpen(false,false);}
    const model=createModel({read,cancel:()=>{request?.abort();request=null;},change:render,focus:(row,track)=>choose(row,track)});
    function setOpen(value,restore=true){opened=value;sheet.reset();panel.classList.toggle('is-open',value);panel.inert=!value;panel.setAttribute('aria-hidden',String(!value));if(!value){model.pause();if(restore)returnFocus?.focus({preventScroll:true});}else{onOpen();render();if(!model.getState().data&&!model.getState().busy)model.load();node('[data-bt-title]').focus({preventScroll:true});}}
    function render(){
      const s=model.getState();node('[data-bt-title]').textContent=label(s.track)+' · 回测交易';panel.setAttribute('aria-busy',String(s.busy));
      node('[data-bt-status]').textContent=s.error||(s.busy?'正在读取回测成交…':s.total?'':'没有符合条件的回测成交');node('[data-bt-retry]').hidden=!s.error;
      const ranking=s.data?.ranking||[],top=ranking[0];node('[data-bt-top]').textContent=top?`Top 1 · ${base(top.symbol)} ${money(top.pnl)}`:'Top 1 · —';node('[data-bt-top]').disabled=!top;
      const select=node('[data-bt-symbol]');const options='<option value="">全部币种</option>'+ranking.map((r,i)=>`<option value="${esc(r.symbol)}">${i+1}. ${esc(base(r.symbol))} · ${money(r.pnl)} USDT</option>`).join('');
      if(select.innerHTML!==options)select.innerHTML=options;select.value=s.symbol;
      panel.querySelectorAll('[data-bt-sort]').forEach(b=>{const active=s.sort===b.dataset.btSort;b.setAttribute('aria-pressed',String(active));b.textContent=(b.dataset.btSort==='time'?'开仓时间':'净收益')+(active?(s.direction==='desc'?' ↓':' ↑'):'');b.setAttribute('aria-label',b.textContent+(active?'，点击反向排序':'，点击降序排序'));});
      const markup=s.rows.map(r=>rowHTML(r,selected)).join('');if(node('.bt-rows').innerHTML!==markup)node('.bt-rows').innerHTML=markup;
      node('[data-bt-more]').hidden=!s.has_more;node('[data-bt-more]').disabled=s.busy;
      node('.bt-count').textContent=s.data?`显示 ${s.rows.length} / ${s.total} 笔 · 全策略 ${s.data.meta.trades} 笔`:'';
      node('.bt-notes').hidden=!s.data;node('.bt-notes p').textContent=s.data?[s.data.meta.basis,s.data.meta.note,s.data.meta.limitation].join(' '):'';
    }
    panel.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
      if(b.hasAttribute('data-bt-close'))setOpen(false);
      if(b.hasAttribute('data-bt-retry'))model.load(model.getState().rows.length>0);
      if(b.hasAttribute('data-bt-more'))model.load(true);
      if(b.dataset.btSort){node('.bt-scroll').scrollTop=0;model.sort(b.dataset.btSort);}
      if(b.dataset.btTrade){const s=model.getState(),r=s.rows.find(r=>r.id===Number(b.dataset.btTrade));if(r)choose(r,s.track,true);}
      if(b.hasAttribute('data-bt-top')){const s=model.getState();if(s.data?.focus){choose(s.data.focus,s.track);model.filter(s.data.focus.symbol);}}
    });
    node('[data-bt-symbol]').onchange=e=>{node('.bt-scroll').scrollTop=0;model.filter(e.target.value);};
    panel.addEventListener('keydown',e=>{if(e.key==='Escape'){e.stopPropagation();setOpen(false);}});
    return {open(track){returnFocus=root.querySelector('[data-rp="track"]');selected=null;model.select(track);setOpen(true);},setOpen,getState:model.getState,isOpen:()=>opened,
      setActive(value){if(!value){model.pause();sheet.reset();}else if(opened&&!model.getState().data)model.load();}};
  }
  window.SpikeStrategyBacktest={...api,create};
})();
