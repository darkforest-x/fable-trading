/* Stitch 3addf9a46166403e99a844214cde4b94. Saved funded-trade browser.
 * List filters never select a trade; only opening a strategy or choosing a row
 * changes the wallpaper. A generation owns each paginated read, including retry. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const CLOUD='live-cloud-v17';
  const tracks=['60','5','v3','v3add','spike','v3lock','allin3','v3add20','live-allin3','live-wick6x3','live-wick6x3mid','live-wick3x3','live-wick6x3hi','live-wick3x3far','live-breadth',CLOUD];
  const BREADTH='breadth-ema20', SEPTEMBER_CASE='BTC|2026-09-15T19:20:00Z';
  tracks.push(BREADTH);
  const steps={'1m':60000,'2m':120000,'3m':180000,'5m':300000,'15m':900000,'1H':3600000,'4H':14400000,'12H':43200000,'1D':86400000};
  const symbolOK=v=>typeof v==='string'&&/^[A-Z0-9]{1,32}USDT$/.test(v);
  const valid=r=>r&&Number.isInteger(r.id)&&r.id>0&&(symbolOK(r.symbol)||(r.event_id&&r.asset==='ALT'&&r.member_symbols?.includes(r.symbol)&&/^[\p{L}0-9]{1,32}USDT$/u.test(r.symbol)))&&[1,-1].includes(r.side)&&['entry_ms','exit_ms','pnl','return_pct'].every(k=>Number.isFinite(r[k]))&&r.entry_ms>0&&r.exit_ms>=r.entry_ms;
  const base=s=>s.replace(/USDT$/,''),label=t=>({60:'60m',5:'5m',[BREADTH]:'全市场下轨反弹',[CLOUD]:'云带 v1.7 · 12H 逆向'}[t]||t);
  const money=v=>(v>0?'+':'')+v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  const tone=v=>v>0?'rp-positive':v<0?'rp-negative':'';
  const format=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  const date=t=>format.format(t);
  const pointFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
  const shortTime=t=>date(t).slice(-5);
  function cloudTradeId(value){
    const match=String(value??'').match(/^(?:cloud-v17-)?0*(\d+)$/i);
    if(!match)return '';
    const id=Number(match[1]);return Number.isSafeInteger(id)&&id>0?String(id):'';
  }
  const entryLabel=r=>r.point_times||r.entry_start_ms===r.entry_ms?date(r.entry_ms):Number.isFinite(r.entry_start_ms)?`${date(r.entry_start_ms)}–${shortTime(r.entry_ms)}`:date(r.entry_ms)+'（记账）';
  const price=v=>Number.isFinite(v)&&v>0?v.toLocaleString('en-US',{maximumFractionDigits:10}):'—';
  function fillLabel(r,key){
    const f=r.fills,start=f?.[key+'_start_ms'],end=f?.[key+'_end_ms'];
    if(!Number.isFinite(start)||!Number.isFinite(end))return key==='entry'?entryLabel(r):date(r.exit_ms)+'（记账）';
    if((r.point_times||r.kind==='cloud-v17'||r.entry_rules?.kind==='cloud-v17')&&start===end)return pointFormat.format(start);
    return date(start)+(end>start?'–'+(date(start).slice(0,-5)===date(end).slice(0,-5)?shortTime(end):date(end)):'');
  }
  function breadthSummary(r){
    const alt=r.asset==='ALT', options=alt?r.member_symbols.map(s=>`<option value="${esc(s)}"${s===r.symbol?' selected':''}>${esc(base(s))}</option>`).join(''):'';
    const heading=alt?`<select aria-label="回测篮子成分币" data-bt-member>${options}</select>`:esc(base(r.symbol));
    const exits=r.fills.exit_legs||[];
    const fills=[{time_ms:r.entry_ms,price:r.fills.entry_price,label:'开仓'},...exits];
    const cost=r.scope==='2026_actual_funded'?'含手续费与资金费':'仅手续费';
    const details=alt?` · 成分币净收益 ${Number.isFinite(r.member_return_pct)?money(r.member_return_pct)+'%':'—'}`:` · 每枚 BTC 净收益 ${money(r.entry_price*r.return_pct/100)} 点`;
    return `<div class="bt-summary-coin"><strong>${heading}<small> / USDT</small></strong><span><em class="rp-positive">做多</em> · Binance · ${esc(r.branch_label)}</span></div>`+
      fills.map((f,i)=>`<div class="bt-summary-fill" data-fill="${i?'exit':'entry'}"><span>${esc(f.label||'平仓')}</span><b>${price(f.price)}</b><time>${date(f.time_ms)}</time></div>`).join('')+
      `<div class="bt-summary-pnl"><span>${alt?'篮子净收益':'净收益'} <small>USDT</small></span><strong class="${tone(r.pnl)}">${money(r.pnl)}</strong><small>${money(r.return_pct)}%</small></div><small class="bt-summary-note">北京时间 · ${cost}${details}</small>`;
  }
  function wickTargetText(r){
    const rule=r.entry_rules;
    if(!['shallow_ny_monday_midpoint','near_monday_edge'].includes(rule?.target_rule)||!Number.isFinite(rule.beyond_pct))return '';
    const target=r.target==='range_0.5'?'周一中点':r.target==='near_monday_edge'?'周一近侧':'';
    return target?`穿幅 ${Number(rule.beyond_pct.toFixed(4))}% · 目标 ${target}`:'';
  }
  function cloudExecutionsHTML(r){
    if(r.kind!=='cloud-v17'&&r.entry_rules?.kind!=='cloud-v17')return '';
    const fills=r.fills,legs=Array.isArray(fills?.exit_legs)?fills.exit_legs:null;
    if(!legs)return '<details class="bt-executions"><summary>分批成交明细</summary><p class="bt-executions-status">正在读取原始 1H 成交分组…</p></details>';
    const entryQuantity=legs.reduce((sum,leg)=>sum+(Number.isFinite(leg.quantity)?leg.quantity:0),0);
    const rows=[{start_ms:fills.entry_start_ms,end_ms:fills.entry_end_ms,price:fills.entry_price,label:'开仓',phase:'close',quantity:entryQuantity,share:1},...legs];
    const interval=(start,end,phase)=>{
      if(!Number.isFinite(start)||!Number.isFinite(end))return '—';
      if(phase==='intrabar'&&end>start)return '['+date(start)+'，'+date(end)+') · 1H 盘中';
      if(start===end&&phase==='open')return pointFormat.format(start)+' · 1H 开盘';
      if(start===end&&phase==='close')return pointFormat.format(start)+' · 收盘边界';
      return date(start)+'–'+(date(start).slice(0,-5)===date(end).slice(0,-5)?shortTime(end):date(end))+' · 1H';
    };
    return `<details class="bt-executions"><summary>全部成交 · ${rows.length} 组（退出 ${legs.length} 组）</summary><div class="bt-executions-scroll"><table><thead><tr><th>时段（北京）</th><th>动作</th><th>成交价</th><th>数量</th><th>仓位占比</th></tr></thead><tbody>${rows.map((f,i)=>`<tr><td>${esc(interval(f.start_ms??f.time_ms,f.end_ms??f.time_ms,f.phase))}</td><td>${esc(f.label||(i?'退出':'开仓'))}</td><td>${price(f.price)}</td><td>${Number.isFinite(f.quantity)?esc(f.quantity.toLocaleString('en-US',{maximumFractionDigits:6})):'—'}</td><td>${Number.isFinite(f.share)?esc((f.share*100).toLocaleString('en-US',{maximumFractionDigits:2})+'%'):'—'}</td></tr>`).join('')}</tbody></table></div></details>`;
  }
  function summaryHTML(r,options={}){
    const source=options.source||r.source||'Binance',mode=options.mode||'历史回测',costNote=options.costNote||'已扣原研究成本',funded=options.funded!==false;
    if(r.event_id&&r.branch_label)return breadthSummary(r);
    const wickTarget=wickTargetText(r);
    const interval=['entry','exit'].some(k=>r.fills?.[k+'_end_ms']>r.fills?.[k+'_start_ms']);
    return `<div class="bt-summary-coin"><strong>${esc(base(r.symbol))}<small> / USDT</small></strong><span><em class="${r.side===1?'rp-positive':'rp-negative'}">${r.side===1?'做多':'做空'}</em> · ${esc(source)} · ${esc(mode)}</span></div>${['entry','exit'].map((key,i)=>`<div class="bt-summary-fill" data-fill="${key}"><span>${i===0?'① 开仓':'② 平仓'}</span><b>${price(r.fills?.[key+'_price'])}</b><time>${fillLabel(r,key)}</time></div>`).join('')}${Number.isFinite(r.entry_rules?.score)?`<div class="bt-summary-fill bt-summary-score" data-fill="score"><span>入场评分</span><b>${r.entry_rules.score.toFixed(4)}</b><time>${r.entry_rules.score_basis==='pre_touch'?'触价前评分':r.entry_rules.kind==='touch_close'?'触价收盘评分':'原回测评分'} · 门槛 ${r.entry_rules.minimum<.3?'前40%':'≥'+r.entry_rules.minimum}</time></div>`:''}<div class="bt-summary-pnl"><span>${funded?'净收益 <small>USDT</small>':'未计入资金'}</span><strong class="${funded?tone(r.pnl):''}">${funded?(Number.isFinite(r.pnl)?money(r.pnl):'—'):'影子记录'}</strong>${funded&&Number.isFinite(r.return_pct)?`<small class="${tone(r.return_pct)}">${money(r.return_pct)}%</small>`:''}</div><small class="bt-summary-note">北京时间 · ${r.kind==='cloud-v17'?'12H 入场收盘点 · 退出按原 1H 开盘或盘中区间':r.point_times?'按实际成交标记':interval?'横线表示原周期成交区间':'按原回测成交记录'} · ${esc(costNote)}${wickTarget?' · '+esc(wickTarget):''}</small>${cloudExecutionsHTML(r)}`;
  }
  function rulesHTML(r){
    if(r.event_id&&r.branch_label){const b=r.signal_snapshot;return `<span class="bt-entry">${esc(r.branch_label)}${Number.isInteger(b?.initial_touch_count)?' · 首触 '+b.initial_touch_count+' 币':''}${Number.isInteger(b?.new_coins)?' · 新增 '+b.new_coins+' 币':''}</span>`;}
    const e=r.entry_rules;
    if(e?.kind==='cloud-v17'||r.kind==='cloud-v17')return '<span class="bt-entry">云带 v1.7 · 12H 收盘入场 · 原始 1H 分批退出</span>';
    // live-breadth rows carry the saved breadth facts instead of a score (owner 2026-11-08).
    if(e?.status==='saved'&&e.kind==='breadth'&&Number.isFinite(e.initial))return `<span class="bt-entry">宽度信号 · ${esc(e.initial)} 个币 · 30分钟零新增</span>`;
    if(e?.status!=='saved'||!Number.isFinite(e.score)||!Number.isFinite(e.minimum))return '<span class="bt-entry">入场条件／评分未留存</span>';
    const add=e.kind==='add5',gate=e.minimum<.3?' · 前40%':`≥${e.minimum}`;
    const wickTarget=wickTargetText(r);
    return `<span class="bt-entry"><span>${add?'逆向5%加仓':e.kind==='touch_close'?'触价收盘进场':'1.618首触'}</span><span class="bt-score" title="原回测评分 ${e.score}，门槛 ${e.minimum}；不是胜率">${e.score_basis==='pre_touch'?'触价前评分':add?'主单评分':'评分'} ${e.score.toFixed(4)}${gate}</span>${Number.isFinite(e.minimum_order)&&Number.isFinite(e.order)?`<span>排列 ${e.order}≥${e.minimum_order}</span>`:''}${wickTarget?`<span>${esc(wickTarget)}</span>`: ''}</span>`;
  }
  function periodFor(row,period){
    if(row.kind==='cloud-v17'||row.entry_rules?.kind==='cloud-v17')return '12H';
    if(row.event_id&&steps[period])return period;
    return Object.keys(steps).find(p=>steps[p]>=steps[period]&&(row.exit_ms-row.entry_ms)/steps[p]<=5000)||'1D';
  }
  function validate(p,q,offset){
    if(p?.schema!==1||p.track!==q.track||p.sort!==q.sort||p.direction!==q.direction||p.symbol!==q.symbol||p.offset!==offset||!Array.isArray(p.rows)||!p.rows.every(valid)||new Set(p.rows.map(r=>r.id)).size!==p.rows.length||!Number.isInteger(p.total)||p.total<offset+p.rows.length||typeof p.has_more!=='boolean'||!p.meta||p.meta.track!==q.track||!Array.isArray(p.ranking)||!p.ranking.every(r=>symbolOK(r.symbol)&&Number.isFinite(r.pnl)&&Number.isInteger(r.trades))||(p.focus!=null&&!valid(p.focus))||['query','from','until','side','outcome'].some(key=>p[key]!==undefined&&p[key]!==q[key]))throw Error('回测账本返回的数据不完整');
    return p;
  }
  function createModel({read,cancel=()=>{},change=()=>{},focus=()=>{}}){
    let version=0,pending=null,auto=false;
    let state={track:'',sort:'time',direction:'desc',symbol:'',query:'',from:'',until:'',side:'',outcome:'',focusTrade:'',rows:[],data:null,total:0,has_more:false,busy:false,error:''};
    function pause(){version++;cancel();pending=null;state.busy=false;}
    function reset(){pause();state={...state,rows:[],total:0,has_more:false,data:null,error:''};}
    async function load(more=false){
      if(pending||!state.track)return pending;
      const revision=version,offset=more?state.rows.length:0,q={track:state.track,sort:state.sort,direction:state.direction,symbol:state.symbol};
      if(state.track===CLOUD)Object.assign(q,{query:state.query,from:state.from,until:state.until,side:state.side,outcome:state.outcome});
      const request={...q,offset,limit:50,...(!offset&&state.focusTrade?{trade:state.focusTrade}:{})};
      state.busy=true;state.error='';change(state);
      pending=Promise.resolve().then(async()=>{
        if(revision!==version)return;
        try{const value=validate(await read(request),q,offset);if(revision!==version)return;
          const rows=more?[...state.rows,...value.rows]:value.rows;
          if(new Set(rows.map(r=>r.id)).size!==rows.length)throw Error('回测分页出现重复记录');
          state={...state,data:value,rows,total:value.total,has_more:value.has_more};
          if(auto&&!offset){auto=false;if(value.focus)focus(value.focus,q.track);}
        }catch(e){if(revision===version)state.error=e.message||'回测读取失败，请重试';}
        finally{if(revision===version){state.busy=false;pending=null;change(state);}}
      });return pending;
    }
    return {load,pause,getState:()=>state,select(track,selectedRow=null,filters={}){if(!tracks.includes(track)||(selectedRow&&!valid(selectedRow)))return;const opts=filters instanceof URLSearchParams?Object.fromEntries(filters):filters||{};reset();state={...state,track,sort:'time',direction:opts.order==='early'?'asc':'desc',symbol:'',query:String(opts.query||''),from:String(opts.from||''),until:String(opts.until||''),side:String(opts.side||'').replace(/^1$/,'long').replace(/^-1$/,'short'),outcome:String(opts.outcome||''),focusTrade:cloudTradeId(opts.trade)};
        if(opts.order==='profit'){state.sort='pnl';state.direction='desc';}else if(opts.order==='loss'){state.sort='pnl';state.direction='asc';}
        auto=!selectedRow;if(selectedRow)focus(selectedRow,track);return load();},
      filter(symbol){if(symbol&&!symbolOK(symbol))return;reset();state.symbol=symbol;auto=false;return load();},
      filterCloud(patch={}){if(state.track!==CLOUD)return;const next={};for(const key of ['query','from','until','side','outcome'])if(Object.hasOwn(patch,key))next[key]=String(patch[key]||'');if(Object.hasOwn(patch,'order')){next.sort=patch.order==='profit'||patch.order==='loss'?'pnl':'time';next.direction=patch.order==='early'||patch.order==='loss'?'asc':'desc';}reset();state={...state,...next,focusTrade:''};auto=false;return load();},
      sort(key){if(!['time','pnl','score'].includes(key))return;const direction=state.sort===key&&state.direction==='desc'?'asc':'desc';reset();state.sort=key;state.direction=direction;auto=false;return load();}};
  }
  function rowHTML(r,selected){
    const coin=r.asset==='ALT'?'山寨篮子':base(r.symbol);
    const cloud=r.kind==='cloud-v17'||r.entry_rules?.kind==='cloud-v17';
    const reason=({stop:'止损',stop_gap_open:'开盘止损',ema_flip:'云层反转',ema_flip_open:'开盘云层反转',take_profit:'止盈',take_profit_open:'开盘止盈',series_tail_close:'序列尾结算',delisting_settlement:'退市结算',data_gap_markout:'缺口结算'}[r.exit_reason]||r.exit_reason_label||r.exit_reason||'');
    return `<button type="button" class="bt-row${cloud?' bt-cloud-row':''}" data-bt-trade="${r.id}" aria-pressed="${r.id===selected}" aria-label="查看 ${esc(coin)} ${date(r.entry_ms)} 回测交易"><span class="bt-coin"><b>${esc(coin)}</b><em class="${r.side===1?'rp-positive':'rp-negative'}">${r.side===1?'多':'空'}</em>${cloud?'<small>OKX · 12H</small>':''}${r.id===selected?'<small>图表焦点</small>':''}</span><span class="bt-pnl ${tone(r.pnl)}"><b>${money(r.pnl)} <small>USDT</small></b><small>${money(r.return_pct)}%</small></span>${rulesHTML(r)}<span class="bt-times"><span title="原始开仓成交点">开 ${entryLabel(r)}</span><span title="原退出成交时段">${cloud?'平 ':r.exit_reason===0?'期末结算':'平 '}${cloud?fillLabel(r,'exit'):date(r.exit_ms)}${cloud&&reason?' · '+esc(reason):''}</span></span></button>`;
  }
  const api={createModel,validate,valid,rowHTML,rulesHTML,entryLabel,fillLabel,summaryHTML,periodFor,base,date,cloudTradeId};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  function create({root,onOpen=()=>{},onSelect}){
    let opened=false,selected=null,returnFocus=null,request=null;
    let breadth=null,activeRow=null,renderedRows='',cloudQueryTimer=null;
    const panel=document.createElement('section');panel.id='strategy-backtest-panel';panel.className='bt-panel rp-glass';panel.inert=true;panel.setAttribute('aria-hidden','true');panel.setAttribute('aria-label','历史回测交易');
    panel.innerHTML='<header class="bt-heading" data-yd-sheet-drag><strong tabindex="-1" data-bt-title>回测交易</strong><button type="button" data-bt-close aria-label="收起回测交易">×</button></header><div class="bt-controls"><div class="bt-badges"><span data-bt-source>历史回测 · Binance</span><button type="button" data-bt-top title="查看累计净盈亏第一名">Top 1 · 读取中</button></div><details class="bt-cloud-filters" data-bt-cloud-filters hidden><summary>更多筛选</summary><div class="bt-cloud-filter-fields"><label class="bt-cloud-query">搜索<input type="search" data-bt-cloud="query" aria-label="搜索币种或交易编号" placeholder="币种或编号" maxlength="100"></label><label>入场起日<input type="date" data-bt-cloud="from" aria-label="入场起日（北京时间）"></label><label>入场止日<input type="date" data-bt-cloud="until" aria-label="入场止日（北京时间）"></label><label>方向<select data-bt-cloud="side" aria-label="交易方向"><option value="">全部方向</option><option value="long">做多</option><option value="short">做空</option></select></label><label>结果<select data-bt-cloud="outcome" aria-label="交易结果"><option value="">全部结果</option><option value="win">盈利</option><option value="loss">亏损</option></select></label><button type="button" data-bt-cloud-clear aria-label="清除云带筛选">清除条件</button></div></details><div class="bt-sort"><select aria-label="回测币种" data-bt-symbol><option value="">全部币种</option></select><select aria-label="下轨入场区间" data-bt-breadth-period hidden></select><button type="button" data-bt-sort="time" aria-pressed="true">开仓时间 ↓</button><button type="button" data-bt-sort="pnl" aria-pressed="false">净收益</button><button type="button" data-bt-sort="score" aria-pressed="false">评分</button></div><small data-bt-hint>原回测入场条件 · 评分非胜率<br>北京时间 · 收益已扣原研究成本</small></div><div class="bt-scroll"><p data-bt-status role="status"></p><button type="button" data-bt-retry hidden>重试读取</button><div class="bt-rows"></div><button type="button" class="bt-more" data-bt-more hidden>再显示 50 笔</button><p class="bt-count"></p><details class="bt-notes"><summary>历史版本与统计口径</summary><p></p></details></div>';
    root.append(panel);
    const node=s=>panel.querySelector(s),sheet=window.SpikeYoloPreviewSheet.bind({panel,isOpen:()=>opened,dismiss:()=>setOpen(false)});
    async function read(params){const c=new AbortController();request=c;const timeout=setTimeout(()=>c.abort(),20000);
      try{
        if(params.track===BREADTH){
          const context=breadth;
          await window.SpikeModules.ensure('breadth-booklet');
          if(c.signal.aborted||context!==breadth)throw new DOMException('取消','AbortError');
          context.data??=await window.SpikeBreadthReplayData.load({signal:c.signal});
          if(c.signal.aborted||context!==breadth)throw new DOMException('取消','AbortError');
          context.view??=window.SpikeBreadthReplayData.create(context.data,{scope:context.scope,period:context.entryPeriod,kind:context.kind});
          const page=context.view.page(params);
          if(context.eventId){const row=context.view.resolve(context.eventId,context.memberSymbol);if(!row)throw Error('未找到这笔回测交易或成分币');return {...page,focus:row};}
          return page;
        }
        const r=await fetch('/api/strategy-backtest?'+new URLSearchParams(params),{signal:c.signal,cache:'no-store'});const value=await r.json();if(!r.ok)throw Error(value.detail||'回测读取失败');return value;}
      catch(e){if(e.name==='AbortError')throw Error('回测读取超时，请重试');throw e;}
      finally{clearTimeout(timeout);if(request===c)request=null;}}
    function choose(row,track){
      selected=row.id;activeRow=row;
      const options=track===BREADTH?{scope:breadth.scope,replay_source:BREADTH}:{};
      if(track===CLOUD){const route=window.SpikeCloudBookRoute?.read(location.search),params=route?.params||new URLSearchParams();params.set('trade',String(row.id));window.SpikeCloudBookRoute?.write({chapter:'history',params});}
      else if(window.SpikeCloudBookRoute?.read(location.search))window.SpikeCloudBookRoute.write(null);
      onSelect({track,row,options});
      if(track===BREADTH){
        window.SpikeBreadthBookRoute?.write({chapter:'replay',eventId:row.event_id,scope:breadth.scope,memberSymbol:row.asset==='ALT'?row.symbol:null});
        breadth.eventId=null;
      }
      render();
    }
    const model=createModel({read,cancel:()=>{request?.abort();request=null;},change:render,focus:(row,track)=>choose(row,track)});
    function setOpen(value,restore=true){
      if(value&&opened)return;
      opened=value;sheet.reset();
      panel.classList.toggle('is-open',value);root.classList.toggle('has-backtest-list',value);
      const trigger=root.querySelector('[data-rp-action="backtest-list"]');
      trigger?.setAttribute('aria-expanded',String(value));
      panel.inert=!value;panel.setAttribute('aria-hidden',String(!value));
      if(!value){
        clearTimeout(cloudQueryTimer);
        model.pause();
        if(restore)(root.classList.contains('is-backtest')?trigger:returnFocus)?.focus({preventScroll:true});
        return;
      }
      onOpen();render();
      const state=model.getState();
      if(!state.data&&!state.busy&&!state.error)model.load();
      node('[data-bt-title]').focus({preventScroll:true});
    }
    function render(){
      const s=model.getState();node('[data-bt-title]').textContent=label(s.track)+' · 回测交易';panel.setAttribute('aria-busy',String(s.busy));
      node('[data-bt-status]').textContent=s.error||(s.busy?'正在读取回测成交…':s.total?'':'没有符合条件的回测成交');node('[data-bt-retry]').hidden=!s.error;
      const ranking=s.data?.ranking||[],top=ranking[0];node('[data-bt-top]').textContent=top?`Top 1 · ${base(top.symbol)} ${money(top.pnl)}`:'Top 1 · —';node('[data-bt-top]').disabled=!top;
      const research=s.track===BREADTH,cloud=s.track===CLOUD;
      const select=node('[data-bt-symbol]'),period=node('[data-bt-breadth-period]');
      let options;
      if(research){
        options=[['','全部类型'],['medium','普通 BTC'],['case','急跌 BTC'],['ALT','山寨篮子']].map(([v,t])=>`<option value="${v}">${t}</option>`).join('');
        node('[data-bt-top]').textContent='9/16 BTC';node('[data-bt-top]').title='打开 9 月 16 日急跌 BTC 回放';node('[data-bt-top]').disabled=!breadth?.view;
        const years=[...new Set((breadth?.data?.events||[]).map(r=>r.entry_time.slice(0,4)))].sort().reverse();
        const choice=breadth?.entryPeriod||'';
        const periods=breadth?.scope==='2026_actual_funded'?['2026']:years;
        if(choice&&!periods.includes(choice))periods.push(choice);
        const choices='<option value="">当前区间</option>'+periods.map(y=>`<option value="${esc(y)}">${esc(y)}</option>`).join('');
        if(period.innerHTML!==choices)period.innerHTML=choices;period.value=choice;
      }else{
        options='<option value="">全部币种</option>'+ranking.map((r,i)=>`<option value="${esc(r.symbol)}">${i+1}. ${esc(base(r.symbol))} · ${money(r.pnl)} USDT</option>`).join('');
        node('[data-bt-top]').title='查看累计净盈亏第一名';
      }
      period.hidden=!research;
      node('[data-bt-cloud-filters]').hidden=!cloud;
      node('[data-bt-source]').textContent=cloud?'历史回测 · OKX · 12H':'历史回测 · Binance';
      select.setAttribute('aria-label',research?'下轨入场类型':'回测币种');
      if(select.innerHTML!==options)select.innerHTML=options;select.value=research?(breadth?.kind||''):s.symbol;
      panel.querySelectorAll('[data-bt-cloud]').forEach(input=>{const key=input.dataset.btCloud,value=s[key]||'';if(input.value!==value)input.value=value;});
      node('[data-bt-hint]').textContent=cloud?'12H OKX · 入场日期按北京时间 · 原始 1H 退出分组':'原回测入场条件 · 评分非胜率 · 北京时间 · 收益已扣原研究成本';
      if(research)node('[data-bt-hint]').textContent='北京时间 · 一次山寨篮子入场计一笔';
      panel.querySelectorAll('[data-bt-sort]').forEach(b=>{const key=b.dataset.btSort,active=s.sort===key;b.hidden=research&&key==='score'||cloud&&key==='score';b.setAttribute('aria-pressed',String(active));b.textContent=({time:'开仓时间',pnl:'净收益',score:'评分'}[key])+(active?(s.direction==='desc'?' ↓':' ↑'):'');b.setAttribute('aria-label',b.textContent+(active?'，点击反向排序':'，点击降序排序'));});
      const markup=s.rows.map(r=>rowHTML(r,selected)).join('');
      if(renderedRows!==markup){
        const list=node('.bt-rows'),scroller=node('.bt-scroll'),top=scroller.scrollTop;
        const focused=list.contains(document.activeElement)&&document.activeElement.dataset.btTrade;
        list.innerHTML=markup;renderedRows=markup;
        if(focused)list.querySelector(`[data-bt-trade="${focused}"]`)?.focus({preventScroll:true});
        scroller.scrollTop=top;
      }
      node('[data-bt-more]').hidden=!s.has_more;node('[data-bt-more]').disabled=s.busy;
      node('.bt-count').textContent=s.data?`显示 ${s.rows.length} / ${s.total} 笔 · 全策略 ${s.data.meta.trades} 笔`:'';
      node('.bt-notes').hidden=!s.data;node('.bt-notes p').textContent=s.data?[s.data.meta.basis,s.data.meta.note,s.data.meta.limitation].join(' '):'';
    }
    panel.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
      if(b.hasAttribute('data-bt-close'))setOpen(false);
      if(b.hasAttribute('data-bt-cloud-clear')){clearTimeout(cloudQueryTimer);node('.bt-cloud-filters').querySelectorAll('[data-bt-cloud]').forEach(input=>input.value='');node('.bt-scroll').scrollTop=0;writeCloudFilters({});model.filterCloud({query:'',from:'',until:'',side:'',outcome:''});}
      if(b.hasAttribute('data-bt-retry'))model.load(model.getState().rows.length>0);
      if(b.hasAttribute('data-bt-more'))model.load(true);
      if(b.dataset.btSort){const before=model.getState(),key=b.dataset.btSort,direction=before.sort===key&&before.direction==='desc'?'asc':'desc';node('.bt-scroll').scrollTop=0;model.sort(key);if(before.track===CLOUD){const order=key==='time'?(direction==='asc'?'early':'recent'):(direction==='asc'?'loss':'profit');writeCloudFilters(before, null);const route=window.SpikeCloudBookRoute?.read(location.search);if(route){route.params.set('order',order);window.SpikeCloudBookRoute.write(route);}}}
      if(b.dataset.btTrade){const s=model.getState(),r=s.rows.find(r=>r.id===Number(b.dataset.btTrade));if(r)choose(r,s.track);}
      if(b.hasAttribute('data-bt-top')&&breadth?.view){choose(breadth.view.resolve(SEPTEMBER_CASE),BREADTH);return;}
      if(b.hasAttribute('data-bt-top')){const s=model.getState();if(s.data?.focus){choose(s.data.focus,s.track);model.filter(s.data.focus.symbol);}}
    });
    function filterBreadth(changes){
      if(!breadth)return;
      Object.assign(breadth,changes,{view:null,eventId:null});
      node('.bt-scroll').scrollTop=0;model.filter('');
    }
    node('[data-bt-symbol]').onchange=e=>{if(breadth)filterBreadth({kind:e.target.value});else{node('.bt-scroll').scrollTop=0;model.filter(e.target.value);}};
    node('[data-bt-breadth-period]').onchange=e=>filterBreadth({entryPeriod:e.target.value});
    node('.bt-cloud-filters').addEventListener('input',e=>{
      if(e.target.dataset.btCloud!=='query')return;
      clearTimeout(cloudQueryTimer);cloudQueryTimer=setTimeout(()=>{
        const values=Object.fromEntries([...node('.bt-cloud-filters').querySelectorAll('[data-bt-cloud]')].map(input=>[input.dataset.btCloud,input.value]));
        node('.bt-scroll').scrollTop=0;writeCloudFilters(values);model.filterCloud(values);
      },240);
    });
    node('.bt-cloud-filters').addEventListener('change',e=>{
      if(!e.target.dataset.btCloud||e.target.dataset.btCloud==='query')return;
      clearTimeout(cloudQueryTimer);
      const values=Object.fromEntries([...node('.bt-cloud-filters').querySelectorAll('[data-bt-cloud]')].map(input=>[input.dataset.btCloud,input.value]));
      node('.bt-scroll').scrollTop=0;writeCloudFilters(values);model.filterCloud(values);
    });
    root.addEventListener('change',e=>{
      if(!e.target.matches('[data-bt-member]')||!breadth?.view||!activeRow)return;
      const row=breadth.view.resolve(activeRow.event_id,e.target.value);
      if(row)choose(row,BREADTH);
    });
    panel.addEventListener('keydown',e=>{if(e.key==='Escape'&&opened&&!e.defaultPrevented&&!document.querySelector('dialog[open], [popover]:popover-open')){e.preventDefault();e.stopPropagation();setOpen(false);}});
    function clearReplayLink(){
      if(window.SpikeBreadthBookRoute?.read(location.search)?.chapter==='replay')window.SpikeBreadthBookRoute.write(null);
      if(window.SpikeCloudBookRoute?.read(location.search))window.SpikeCloudBookRoute.write(null);
    }
    function writeCloudFilters(values,trade=null){
      if(!window.SpikeCloudBookRoute)return;
      const route=window.SpikeCloudBookRoute.read(location.search),params=route?.params||new URLSearchParams();
      for(const key of ['query','from','until','side','outcome']){
        const value=String(values[key]||'');if(value)params.set(key,value);else params.delete(key);
      }
      if(trade)params.set('trade',String(trade));else params.delete('trade');
      window.SpikeCloudBookRoute.write({chapter:'history',params});
    }
    function open(track,row=null,options={}){
      if(!tracks.includes(track)||(row&&!valid(row)))return;
      if(track!==BREADTH&&track!==CLOUD)clearReplayLink();
      breadth=track===BREADTH?{scope:options.scope||'all_history_fee_only',entryPeriod:options.entryPeriod||'',kind:options.kind||'',eventId:options.eventId,memberSymbol:options.memberSymbol,data:options.data}:null;
      returnFocus=root.querySelector('[data-rp="track"]');selected=null;activeRow=null;
      const compactDirect=!opened&&(row||options.eventId||track===CLOUD&&options.trade)&&matchMedia('(max-width:700px)').matches;
      const loaded=model.select(track,row,track===CLOUD?options:{});
      if(compactDirect){
        onOpen();
        loaded?.then(()=>{if(!opened&&model.getState().track===track&&model.getState().error)setOpen(true);});
      }else setOpen(true);
    }
    return {open,clearReplayLink,setOpen,getState:model.getState,isOpen:()=>opened,
      setActive(value){if(!value){model.pause();sheet.reset();}else if(opened&&!model.getState().data)model.load();}};
  }
  window.SpikeStrategyBacktest={...api,create};
})();
