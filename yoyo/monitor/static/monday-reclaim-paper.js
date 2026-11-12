/* Monday limit-reclaim independent paper accounts.
 * Contract: monday_reclaim_paper.py snapshot(); position allocation owns P&L,
 * signal.taken only records the score gate. API equity excludes floating P&L.
 * Design: Stitch 9b7f625827da4e60a41b5bf1361c7829 (synthetic examples only).
 * Rollover/one-row ledger: Stitch 6798eaf559a24559819826c8c01df7e7.
 * Compact ledger/scores: Stitch 77f940d44558444db5ef189e93e9dce0.
 * Four accounts/pending adds: Stitch 571f390de49b49fcbf11ae0c054d3c95.
 * Fifth account/order scores: Stitch f56301aa72694cb58f4450afb088c191.
 * Sixth account/profit lock: Stitch 071103a8b81847049747df436aeec779.
 * Seventh account/capital refills: Stitch b218f8c044f442c7bb5650a93a42d7f3.
 * Eighth account/dynamic margin: Stitch a4e9abe06abd4685af45da545b6003d1.
 * Today/history drawer: Stitch 678fa6592e6b4343a02a3a4b745a55b5.
 * Historical trade framing: Stitch 069bd922d6c148638f3c3e85648a7394.
 * Unified replay layout: Stitch 051be7f411404315b5acb109f5d456b3.
 * Sorting icon: Stitch 0ee14c4f3aba409ebba1ea8c88e85516 (owner: no extra row).
 */
(() => {
  'use strict';
  const list=typeof module!=='undefined'&&module.exports?require('./monday-list-model.js'):window.SpikeMondayList;
  const icons=typeof module!=='undefined'&&module.exports?require('./coin-icons.js'):window.SpikeCoinIcons;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const numberFormats=new Map();
  function num(value,digits=2){
    if(!finite(value))return '—';
    if(!numberFormats.has(digits))numberFormats.set(digits,new Intl.NumberFormat('en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits}));
    return numberFormats.get(digits).format(value);
  }
  const signed = (value, digits=2) => finite(value) ? `${value > 0 ? '+' : ''}${num(value,digits)}` : '—';
  const bp = value => finite(value) ? `${signed(value/100)}%` : '—';
  const priceFormat=new Intl.NumberFormat('en-US',{maximumSignificantDigits:9});
  const price=value=>finite(value)?priceFormat.format(value):'—';
  const tone = value => !finite(value) || value === 0 ? '' : value > 0 ? 'rp-positive' : 'rp-negative';
  const dateFormat = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  const date = value => finite(value) && value > 0 && !Number.isNaN(new Date(value).getTime()) ? dateFormat.format(new Date(value)) : '—';
  const validSymbol = value => /^[A-Z0-9]{1,32}-USDT-SWAP$/.test(String(value || ''));
  const base = value => validSymbol(value) ? value.slice(0,-10) : String(value || '未知合约');
  const funded = row => row.taken === 1 && finite(row.notional) && row.notional > 0;
  const dayStart = ms => Math.floor((ms+28800000)/86400000)*86400000-28800000;
  const heldSignals = data => new Set(Object.values(data.tracks||{}).flatMap(t=>t.open||[])
    .filter(p=>funded(p)&&['pending_confirm','breakeven'].includes(p.stage)&&p.fill_ms>0&&p.entry>0&&(p.remaining_notional??p.notional)>0)
    .map(p=>p.signal_id));
  const TRACKS = ['60','5','v3','v3add','spike','v3lock','allin3','v3add20','live-allin3','live-wick6x3','live-wick6x3mid','live-wick3x3','live-wick6x3hi','live-wick3x3far','live-breadth'];
  const CLOUD='live-cloud-v17';
  const BACKTEST_TRACKS = [...TRACKS,'breadth-ema20',CLOUD];
  const SORTS = {score:'评分优先',time_desc:'时间：最新优先',time_asc:'时间：最早优先'};
  const recordSort = value => Object.hasOwn(SORTS,value) ? value : 'score';
  const trackLabel = key => ({60:'60m',5:'5m',v3:'v3',v3add:'v3add',spike:'spike',v3lock:'v3lock',allin3:'allin3',v3add20:'v3add20','live-allin3':'live-allin3','live-wick6x3':'live-wick6x3','live-wick6x3mid':'live-wick6x3mid','live-wick3x3':'live-wick3x3','live-wick6x3hi':'live-wick6x3hi','live-wick3x3far':'live-wick3x3far','live-breadth':'live-breadth','breadth-ema20':'全市场下轨反弹',[CLOUD]:'云带 v1.7 · 12H 逆向'}[key] || '未知策略');
  const isAllinTrack = key => ['allin3','live-allin3'].includes(key);
  const isWickTrack = key => ['live-wick6x3','live-wick6x3mid','live-wick3x3','live-wick6x3hi','live-wick3x3far'].includes(key);
  // Owner 2026-11-08, Stitch 2762455ffefe4f5a9784c2e2e0fd4db5: the breadth account is booked by breadth_paper.py
  // (SOL funded, BTC/ETH comparison rows, six-day exit); its rows carry track 9260 and signal rows carry breadth=true.
  const BREADTH_ID = 9260;
  const isBreadthTrack = key => key==='live-breadth';
  const isBreadthRow = row => row?.track===BREADTH_ID||row?.breadth===true;
  const BREADTH_STATUS = {taken:'已开仓',closed:'已平仓',followers:'观察期有新币 · 取消',busy:'已有持仓 · 跳过',out_of_band:'币数不在区间',watching:'观察中',entering:'等待开仓价',price_missing:'缺少开仓价'};
  const BREADTH_TONE = {taken:'is-open',closed:'is-open',followers:'is-muted',busy:'is-warn',out_of_band:'is-muted',watching:'is-watch',entering:'is-watch',price_missing:'is-muted'};
  const span = ms => {if(!finite(ms))return '—';const m=Math.max(0,Math.round(ms/60000)),d=Math.floor(m/1440),h=Math.floor(m%1440/60);return d?`${d}天${h}小时`:h?`${h}小时${m%60}分`:`${m%60}分`;};
  // Owner 2026-11-06 performance pass: the snapshot carries closed/today
  // records only for the requested account; another account's lists arrive
  // with the next read and must not be shown as "no records" meanwhile.
  const detailPending = (data,key) => !!data?.detail_track && data.detail_track!==key;
  const signalRows = (data,key,today=false) => isBreadthTrack(key) ? data?.['live-breadth']?.signals||[] : key==='live-allin3' ? (today?data?.live_record_signals??data?.live_signals:data?.live_signals)||[] : (today?data?.record_signals??data?.signals:data?.signals)||[];
  const isAddTrack = key => ['v3add','v3add20'].includes(key);
  const marginLeverage = (data,key) => ['spike','allin3','v3add20','live-allin3','live-wick6x3','live-wick6x3mid','live-wick3x3','live-wick6x3hi','live-wick3x3far'].includes(key) ? data?.[key]?.leverage : null;
  const orderLabel = value => Number.isInteger(value) && value >= 0 && value <= 4 ? `排列 ${value}/4` : '排列 —';
  function targetLabel(row, track='60', config={min_score:.5,far_score:.6}) {
    if (isBreadthTrack(track) || isBreadthRow(row)) return '6天到期';
    if (isAllinTrack(track) || [5060,7060].includes(row.track)) return '对侧 1.618';
    if (['v3','v3add','v3lock','v3add20'].includes(track) || [1060,2060,2061,4060,6060,6061].includes(row.track)) {
      if (!finite(row.score)) return '止盈目标';
      if (row.score >= config.far_score) return '对侧 1.618';
      if (row.score >= config.min_score) return '区间 0.618';
    }
    return row.side === 1 ? '周一低点' : row.side === -1 ? '周一高点' : '周一目标';
  }
  const stopPrice = row => row.be_from_ms ? (finite(row.be_price)?row.be_price:row.entry) : row.backstop;
  const legLabel = row => row.parent_id ? `加仓 · 主单 #${row.parent_id}` : `主单 #${row.id}`;
  const sideLabel = row => row.side === 1 ? '做多' : row.side === -1 ? '做空' : '方向未知';
  const reason = (value,track) => ({target:'到达目标',breakeven:track===4060?'利润锁止损':'保本止损',backstop:'兜底止损',reclaimed_inside_be:track===4060?'收回但未越过利润锁价':'收回但未越过保本价',failed_reclaim:'确认未收回',manual:'手动平仓',time_6d:'第6天平仓'}[value] || '未知退出原因');
  function stage(row, now) {
    if (isBreadthRow(row)) return row.stage === 'closed' ? '第6天平仓' : `持有中 · 剩 ${span(row.decision_ms-now)}`;
    if (row.stage === 'closed') return reason(row.reason,row.track);
    if (row.stage === 'breakeven') return finite(row.be_price)?(row.track===4060?'利润锁保护已启用':'保本保护已启用'):'入场价止损已启用';
    if (row.stage === 'pending_confirm') return finite(row.decision_ms) && row.decision_ms <= now ? '等待确认结算' : '等待收盘确认';
    return '状态待核对';
  }
  function signalStatus(row) {
    if (row.breadth === true) return BREADTH_STATUS[row.status] || '状态待核对';
    if (row.status === 'excluded') return '排除币种';
    if (row.status === 'feature_error') return '特征读取失败';
    if (row.status === 'pending_score') return '等待触及K线收盘评分';
    if (row.status === 'scored') return row.taken === 1 ? '通过评分' : '未通过评分';
    return '状态待核对';
  }
  function allocation(row) {
    if (funded(row)) return '计入资金';
    const label={no_capacity:'额度不足',no_slot:'仓位已满',comparison:'对照',order_missing:'排列分缺失',busy:'已有资金仓位'}[row.note]
      || (/^order_[0-4]$/.test(row.note||'')?'排列分不足':'不计入资金');
    return `影子 · ${label}`;
  }
  function route(params={}) {
    const extra={};
    if(['month','week','paper','cloud'].includes(params.source))extra.source=params.source;
    if(['1H','4H','12H'].includes(params.reclaim))extra.reclaim=params.reclaim;
    if(params.ledger==='1')extra.ledger='1';
    if(validSymbol(params.symbol)&&/^\d{4}-(0[1-9]|1[0-2])$/.test(String(params.monthEvent||'').split('|')[0])&&
       ['upper','lower'].some(side=>params.monthEvent===params.monthEvent.split('|')[0]+'|'+params.symbol+'|'+side))extra.monthEvent=params.monthEvent;
    return {...extra,track:TRACKS.includes(params.track) ? params.track : 'spike',tab:['open','closed','events'].includes(params.tab) ? params.tab : 'open',
      sort:recordSort(params.sort),
      history:params.history==='1'&&['closed','events'].includes(params.tab)?'1':'',
      scope:['funded','shadow','all'].includes(params.scope) ? params.scope : 'funded',q:String(params.q || '').slice(0,80),period:['1m','2m','3m','5m','15m','1H','4H','12H','1D'].includes(params.period)?params.period:'15m',trade:/^\d+$/.test(String(params.trade||''))?String(params.trade):'',symbol:validSymbol(params.symbol)?params.symbol:''};
  }
  function signalChartLink(symbol){return /^[A-Z0-9]{1,32}-USDT-SWAP$/.test(symbol||'')?'#signals?'+new URLSearchParams({pane:'records',scope:'history',cloudSymbol:symbol}):'';}
  function chartLink(symbol, track) {
    return validSymbol(symbol) ? '#monday-fib?' + new URLSearchParams({symbol,timeframe:track === '5' ? '5m' : '1H'}) : '#monday-fib';
  }
  function validate(data) {
    const records=value=>Array.isArray(value) && value.every(row=>row && typeof row==='object' && !Array.isArray(row) && typeof row.symbol==='string');
    if (data?.paper_only !== true || data?.strategy !== 'monday_1618_limit_reclaim_v2' || !finite(data.now_ms) || !records(data.signals)
      || (data.record_signals!==undefined&&!records(data.record_signals))
      || !['60','5'].every(key => data.tracks?.[key])
      || !TRACKS.every(key => !data.tracks?.[key] || (records(data.tracks[key].open) && records(data.tracks[key].closed)
        && (data.tracks[key].record_closed===undefined||records(data.tracks[key].record_closed))
        && (data.tracks[key].pending_adds === undefined || records(data.tracks[key].pending_adds))
        && (!isAllinTrack(key) || data.tracks[key].refills===undefined || finite(refillTotal(data.tracks[key].refills)))))) {
      throw Error('模拟盘返回格式不完整，请稍后重试');
    }
    return data;
  }
  // Refill cash is distinct from the stake-budget denominator (start * stakes).
  // Never infer complete realized P&L from the paginated closed-position list.
  function refillTotal(refills) {
    return Array.isArray(refills)&&refills.every(r=>r&&finite(r.equity_before)&&finite(r.equity_after))
      ? refills.reduce((sum,r)=>sum+r.equity_after-r.equity_before,0) : null;
  }
  function summary(track, key=track?.rule) {
    const stats=track?.stats || {}, count=stats.taken_closed;
    const added=isAllinTrack(key)?refillTotal(track?.refills):0;
    return {equity:track?.equity,start:track?.start_equity,
      pnl:finite(track?.equity) && finite(track?.start_equity) && finite(added) ? track.equity-track.start_equity-added : null,
      count,winRate:finite(count) && count>0 && finite(stats.wins) ? stats.wins/count*100 : null,
      wins:stats.wins,mean:stats.mean_net_bp};
  }
  function rows(data, state) {
    const source = state.tab === 'events' ? signalRows(data,state.track,true) : state.tab==='closed' ? data.tracks[state.track]?.record_closed??data.tracks[state.track]?.closed??[] : data.tracks[state.track]?.open||[];
    const query=state.q.trim().toUpperCase();
    const start=dayStart(data.now_ms), held=heldSignals(data);
    return source.filter(row => (!query || String(row.symbol).toUpperCase().includes(query))
      && (state.tab==='open'||isBreadthTrack(state.track)||(state.tab==='events'&&held.has(row.id))||row[state.tab==='events'?'candle_ms':'exit_ms']>=start&&row[state.tab==='events'?'candle_ms':'exit_ms']<start+86400000)
      && (state.tab === 'events' || state.scope === 'all' || (state.scope === 'funded' ? funded(row) : !funded(row))))
      .slice().sort((a,b) => compareRecords(a,b,state));
  }
  // Use the displayed event clock, never ingestion time; IDs break ties just
  // like the history SQL so separately fetched pages retain the same order.
  function compareRecords(a,b,state) {
    const sort=recordSort(state.sort),direction=sort==='time_asc'?1:-1;
    const field=state.tab==='events'?'candle_ms':state.tab==='closed'?'exit_ms':'fill_ms';
    const time=(Number(finite(b[field]))-Number(finite(a[field])))
      || (finite(a[field])&&finite(b[field])?direction*(a[field]-b[field]):0)
      || (isBreadthTrack(state.track)?Number(funded(b))-Number(funded(a)):0)
      || direction*(Number(a.id)-Number(b.id));
    return sort==='score' ? (Number(finite(b.score))-Number(finite(a.score)))
      || (finite(a.score)&&finite(b.score)?b.score-a.score:0) || time : time;
  }
  // History is fetched only while visible; changing filters cancels stale pages.
  function createHistory({read,cancel=()=>{},change=()=>{}}) {
    let key='',context=null,epoch=0,pending=null;
    let state={rows:[],total:0,has_more:false,loaded:false,busy:false,error:null};
    const emit=next=>{state={...state,...next};change(state);};
    function load(more=false) {
      if(!context||pending||more&&!state.has_more)return pending||Promise.resolve();
      const revision=epoch,offset=more?state.rows.length:0,args={...context,offset,limit:50};
      emit({busy:true,error:null});
      const task=Promise.resolve().then(()=>revision===epoch&&context?read(args):null).then(page=>{
        if(revision!==epoch)return;
        if(!Array.isArray(page?.rows)||!page.rows.every(r=>r&&typeof r.symbol==='string')||!Number.isInteger(page.total)||page.total<0||typeof page.has_more!=='boolean')throw Error('历史记录格式不完整');
        const combined=more?[...state.rows,...page.rows]:page.rows;
        emit({rows:Array.from(new Map(combined.map(r=>[r.id,r])).values()),total:page.total,has_more:page.has_more,loaded:true,busy:false,error:null});
      }).catch(error=>{if(revision===epoch)emit({error:error instanceof TypeError?'网络暂时不可用':error.message||'读取失败',busy:false});})
        .finally(()=>{if(pending===task)pending=null;});
      pending=task;return task;
    }
    return {load,getState:()=>state,select(next){
      const nextKey=next?JSON.stringify(next):'';if(key===nextKey)return;
      key=nextKey;context=next;epoch++;pending=null;cancel();
      state={rows:[],total:0,has_more:false,loaded:false,busy:false,error:null};
      if(next)load();
    }};
  }
  // One visible page owns the GET, timer and cancellation generation. Failed
  // reads retain the last successful snapshot; late responses cannot revive it.
  function createFeed({read,cancel=()=>{},change=()=>{},schedule=setTimeout,unschedule=clearTimeout,interval=10000,validateData=validate}) {
    let active=false, epoch=0, pending=null, timer=null, selection=null;
    let state={data:null,error:null,busy:false};
    const emit=next=>{state={...state,...next};change(state);};
    function refresh() {
      if (!active) return Promise.resolve();
      if (pending) return pending;
      unschedule(timer);timer=null;
      const revision=epoch;
      const task=Promise.resolve().then(()=>active && revision===epoch ? read() : null)
        .then(data=>{if(active && revision===epoch)emit({data:validateData(data),error:null,busy:false});})
        .catch(error=>{if(active && revision===epoch)emit({error:error instanceof TypeError ? '网络暂时不可用' : error?.message || '读取失败，请稍后重试',busy:false});})
        .finally(()=>{if(pending!==task)return;pending=null;if(active)timer=schedule(refresh,interval);});
      pending=task;emit({busy:true});return task;
    }
    // An explicit account switch supersedes its in-flight detail read. Other
    // refreshes of the same account continue sharing the existing promise.
    function select(value){
      if(selection===value)return;
      selection=value;
      if(!active)return;
      epoch++;
      if(pending){pending=null;cancel();}
      return refresh();
    }
    return {refresh,select,getState:()=>state,setActive(value){
      if(active===value)return;active=value;epoch++;unschedule(timer);timer=null;
      if(!active){pending=null;cancel();emit({busy:false});}else refresh();
    }};
  }
  function coinHTML(row, platform='',subtitle='',period='15m') {
    const symbol=base(row.symbol), href=list.tradingViewURL(row.symbol,platform,period);
    return `<span class="rp-coin">${icons.avatar(symbol)}<span><a href="${esc(href)}" data-rp-focus="tv" ${href.startsWith('https:')?'target="_blank"':''} rel="noopener noreferrer" title="在 TradingView 打开 ${esc(symbol)}" aria-label="在 TradingView 打开 ${esc(symbol)}">${esc(symbol)} <span aria-hidden="true">↗</span></a><small>${subtitle||sideLabel(row)+' · USDT'}</small></span></span>`;
  }
  const cell=(label,value,cls='')=>`<span class="rp-cell ${cls}"><small>${esc(label)}</small><strong>${value}</strong></span>`;
  const detail=(label,value)=>`<div><dt>${esc(label)}</dt><dd>${value}</dd></div>`;
  function capitalLabel(track) {
    return `第 ${num(track?.stakes,0)} 份本金 · ${num(track?.multiple)}×`;
  }
  function capitalHTML(track) {
    return cell('本金份数',`${num(track?.stakes,0)} 份`)+cell('累计本金（份额）',`${num(track?.injected)} USDT`)
      +cell('本金倍数',`${num(track?.multiple)}×`);
  }
  function refillsHTML(track) {
    if(!track)return '';
    const refills=track.refills;
    const body=refillTotal(refills)===null?'<p>补充本金记录暂不可用</p>':!refills.length?'<p>尚无补充本金记录</p>'
      :refills.slice().sort((a,b)=>b.ms-a.ms||b.id-a.id).map(r=>`<article class="rp-refill"><small>${date(r.ms)} · 北京时间 · 交易 #${esc(r.position_id)}</small><dl>${detail('补前净值',`${num(r.equity_before)} USDT`)+detail('补后净值',`${num(r.equity_after)} USDT`)+detail('补充本金 · 净入账',`${signed(r.equity_after-r.equity_before)} USDT`)}</dl></article>`).join('');
    return `<details class="rp-rules rp-refills"><summary>补充本金记录 · ${Array.isArray(refills)?refills.length:'—'} 笔</summary>${body}<p>净入账额为补后减补前，不计入交易盈亏。累计本金按每份初始资金 × 本金份数计算，倍数为净值 ÷ 累计本金。</p></details>`;
  }
  // Highlight the raw model score, independently of the execution score gate.
  function scoreHTML(score) {
    const known=finite(score), high=known&&score>0.5;
    const rounded=known?num(score,4):'—';
    const label=known&&score!==0.5&&rounded==='0.5000'?String(score):rounded;
    return `<span class="rp-score${high?' is-high':''}" title="${known?`入场评分 ${esc(score)}`:'暂无评分'} · 大于 0.5 高亮"><small>评分</small><strong>${esc(label)}</strong></span>`;
  }
  // Pending adds have zero allocated notional. The parent's original notional
  // is the original v3add ceiling; v3add20 recomputes sizing when the add fills.
  function pendingHTML(track, state, now, config={}) {
    if (!isAddTrack(state.track) || state.tab !== 'open' || state.scope === 'shadow' || !track) return '';
    const pending=(track.pending_adds||[]).filter(p=>p.symbol.toUpperCase().includes(state.q.trim().toUpperCase()));
    return `<section class="rp-pending" aria-label="待成交加仓"><h3>待成交加仓 <span>${pending.length}</span></h3><p>未入场 · 不计入持仓和盈亏</p>${pending.length?pending.map(p=>{
      const parent=track.open.find(row=>row.id===p.parent_id);
      return `<article class="rp-pending-order"><header><button type="button" data-rp-symbol="${esc(p.symbol)}">${esc(base(p.symbol))} <small>${sideLabel(p)}</small></button><span>主单 #${esc(p.parent_id)}</span></header><dl>${detail('挂单限价',price(p.entry))+(state.track==='v3add20'?detail('成交名义本金',`成交时净值 ÷ ${num(config?.slots,0)} × ${num(config?.leverage,0)}`):detail('计划本金上限',parent&&finite(parent.notional)?`${num(parent.notional)} USDT`:'—'))+detail('有效截止 · 北京',date(p.decision_ms))}</dl><small>${p.decision_ms<=now?'等待后台结算':'主单确认收盘前有效'} · ${state.track==='v3add20'?'按成交时净值重算，满仓或保证金不足则取消':'成交金额受剩余额度限制'}</small></article>`;
    }).join(''):'<p>当前没有待成交加仓单</p>'}</section>`;
  }
  function rulesHTML(data, key) {
    if (isBreadthTrack(key)) {
      const b=data?.['live-breadth'], t=data?.tracks?.[key];
      return `<p>live-breadth · 独立模拟账户，单独记账，初始资金 ${num(t?.start_equity)} USDT。</p>
      <p>同一根 UTC 5 分钟 K 线里有 ${num(b?.count_min,0)}–${num(b?.count_max,0)} 个 OKX 币首次碰到周一下轨 1.618，之后 ${num(b?.observe_minutes,0)} 分钟没有新币碰下轨，就按 OKX 1 分钟开盘价买入 SOL（计入资金，${num(b?.leverage,0)} 倍，用全部已实现净值）；BTC、ETH 以同样金额记作对照，不改变净值。${num(b?.hold_days,0)} 天后按 1 分钟开盘价平仓，不设止损。持有期间出现的新信号记为“已有持仓 · 跳过”。</p>
      <p>研究口径是币安 50–99 个币；本机只能读取 OKX，31–62 按两边重叠一周的计数比约 0.63 换算，只是近似。Taker ${num(b?.taker_bp/100)}%（单边），不计滑点和资金费。模拟运行，不下真单。</p>
      <p>研究依据：与同币、同等跌幅和 30 天趋势的急跌相比，SOL 六天多赚约 9 个百分点（区间 +2.6 至 +15.8）；BTC、ETH 的优势没有通过。这一档是看过结果后选出的，需要前向检验。</p>`;
    }
    const track=data?.tracks?.[key], v3=['v3','v3add','v3lock','v3add20'].includes(key), spike=key==='spike', allin=isAllinTrack(key), wick=isWickTrack(key), wk=data?.[key], sp=data?.spike, ai=data?.[key], config=data?.v3, sizing=v3?config?.sizing:data?.sizing;
    const add20=key==='v3add20', addConfig=data?.[key], lock=key==='v3lock', protection=lock?'利润锁价':'保本价';
    const offset=lock?data?.v3lock?.breakeven_offset_bp:data?.breakeven_offset_bp;
    const baseAllocation=v3
      ? `单笔名义本金按评分线性分配：净值 × min(${num(sizing?.fraction*100)}% × (分数 − ${num(sizing?.cut,4)}) / ${num(sizing?.mean_excess,4)}, ${num(sizing?.cap*100)}%)`
      : `每笔最多按当时净值的 ${num(sizing?.fraction*100,0)}% 分配名义本金`;
    const allocationRule=add20
      ? `最多 ${num(addConfig?.slots,0)} 笔资金仓位（主单与已成交加仓合计），${num(addConfig?.leverage,0)} 倍杠杆；每笔成交保证金为成交时已实现净值 ÷ ${num(addConfig?.slots,0)}，名义本金为保证金 × ${num(addConfig?.leverage,0)}；总保证金不超过净值。每笔亏损按该笔保证金封顶，分批平仓按对应份额封顶；主单遇到满仓或保证金不足时保留为影子单`
      :allin
      ? `同一时间仅持有 1 笔资金仓位；开仓保证金为当时全部已实现净值，名义本金为净值 × ${num(ai?.leverage,0)}；单笔亏损按保证金封顶，分批平仓按对应份额封顶`
      :wick
      ? `最多 ${num(wk?.slots,0)} 个资金仓位，${num(wk?.leverage,0)} 倍杠杆；开仓保证金为当时已实现净值 ÷ ${num(wk?.slots,0)}，名义本金为保证金 × ${num(wk?.leverage,0)}；总保证金不超过净值，单笔亏损按该笔保证金封顶`
      :spike
      ? `最多 ${num(sp?.slots,0)} 个资金仓位，${num(sp?.leverage,0)} 倍杠杆；开仓保证金为当时已实现净值 ÷ ${num(sp?.slots,0)}，名义本金为保证金 × ${num(sp?.leverage,0)}；单笔亏损按该笔保证金封顶`
      : `${baseAllocation}，总名义敞口上限 ${num(data?.sizing?.gross,1)} 倍，按剩余额度分配`;
    const wickTarget=key==='live-wick6x3mid'
      ?`触价 5 分钟收盘价到 1.618 的向外距离按该 1.618 价位计算。距离大于 0 且小于 ${num(wk?.midpoint_max_beyond_pct,1)}% 时，整仓止盈取纽约周一高低点的中点；达到该分界时及以上，多单取周一低点，空单取周一高点。`
      :'做多目标为周一低点，做空目标为周一高点。';
    const targets=wick?`${wickTarget}初始止损距开仓价 ${num(wk?.stop_pct,0)}%（多单向下、空单向上），确认收回后移到 1.618。`:allin?`目标为对侧 1.618；确认前设 ${num(ai?.backstop_pct,0)}% 价格兜底止损。`:v3?`分数 ${num(config?.min_score,2)} 至 ${num(config?.far_score,2)}（不含）目标为区间 0.618；从 ${num(config?.far_score,2)} 起目标为对侧 1.618；低于门槛的影子记录仍以近侧周一边界为目标。确认前设 ${num(config?.backstop_pct,0)}% 兜底止损。`:`做多目标为周一低点，做空目标为周一高点。${spike?`确认前设 ${num(sp?.backstop_pct,0)}% 兜底止损。`:''}`;
    return `<p>${trackLabel(key)} · 独立模拟策略。各策略分别记账，初始资金各 ${num(track?.start_equity)} USDT；${allocationRule}。影子记录不占用资金、不改变净值。</p>
      ${wick?`<p>触价那根 5 分钟 K 线收盘时：评分 ≥ ${num(wk?.min_score,2)}、收盘仍在 1.618 外且插出去的部分收回不到 ${num(wk?.max_recovery*100,0)}%、离 1.618 不到 ${num(wk?.max_beyond_pct,0)}%，按收盘价市价开仓（吃单 + 0.10% 滑点）。${wk?.boost?`评分 ≥ ${num(wk.boost_from_score,2)} 时保证金放大 ${num(wk.boost,1)} 倍（观察账户：该阈值由 2025–26 回测事后选出，未经检验）。`:''}${wk?.min_beyond_pct?`只做收盘离 1.618 至少 ${num(wk.min_beyond_pct,1)}% 的单（观察账户：该门槛由回测事后发现，两段时间都成立，待前向检验）。`:''}触价后下一根 1 小时 K 线收盘仍未回到 1.618 以内则市价平；回到以内则止损移到 1.618。仓位已满或保证金不足时保留为影子单。</p>`:''}
      ${allin?`<p>入场分 ≥ ${num(ai?.min_score)} 才记录交易；已有资金仓位时，后续达标触价保留为影子单。完全平仓后净值低于 ${num(ai?.blowup_below,0)} USDT，后台恢复至 ${num(track?.start_equity)} USDT，本金份数加 1，并记录补充本金。累计本金按每份初始资金 × 本金份数计算，本金倍数为净值 ÷ 累计本金，不是交易收益率。已实现交易盈亏剔除补充本金净入账额。</p>`:''}
      ${spike?`<p>入场分 ≥ ${num(sp?.min_score,2)} 且均线排列分 ≥ ${num(sp?.min_order,0)} 才可占用资金仓位。排列分来源 OKX · 15m，按触价时已闭合的 K 线计算，满分 4。高分信号因排列分不足、排列分缺失或仓位已满而未分配资金时，保留为影子单；入场分不足仅保留触价记录。币安排列线尚未接入。</p>`:''}
      ${wick?`<p>收盘价市价入场；${targets}确认周期 ${num(track?.confirm_minutes,0)} 分钟，以 1.618（不是入场价）判断是否收回；收回后止损移到 1.618，不加偏移。</p>`:`<p>1.618 限价入场；${targets}确认周期 ${num(track?.confirm_minutes,0)} 分钟。确认收盘未收回入场价则退出；已收回但未越过${protection}，也在该收盘退出。收回且越过后，止损移至${isAddTrack(key)?'主单':''}入场价向盈利方向偏移 ${num(offset/100)}%（多加、空减）。旧单以实际记录的保护价为准，${lock?'利润锁':'保本'}退出仍有成交费用。</p>`}
      ${isAddTrack(key)?`<p>分数从 ${num(addConfig?.from_score,2)} 起，在主单入场价逆向 ${num(addConfig?.drawdown_pct,0)}% 挂一次加仓，${add20?'加仓成交时按当时净值重新计算保证金和名义本金，占用独立仓位；满仓或保证金不足则取消。':'名义本金上限为主单原始名义本金。'}主单确认收盘前有效。加仓共用主单的确认、保本、兜底和目标。主单提前结束会取消未成交加仓；同分钟先触及加仓再触及兜底时，按后台路径结算。待成交单单列，不计入持仓和盈亏；主单与已成交加仓分别计笔。</p>`:''}
      <p>Maker ${num(data?.fees_bp?.maker/100)}% / Taker ${num(data?.fees_bp?.taker/100)}%（单边）；退出滑点情景 ${num(data?.fees_bp?.report_slippage/100)}%。成交费用以每条记录为准。</p>
      <p>${key==='live-allin3'?'live- 标识触价前规则；当前为模拟账户。评分只读取触价5m开始前已收盘数据；非目标退出的10bp滑点计入净盈亏。':isWickTrack(key)?'live- 标识收盘后可执行的规则；当前为模拟账户，不下真单。按触价5m收盘评分，收盘后市价入场、不回记；开仓和非目标退出各计0.10%滑点。':'原模拟策略按触价5m收盘评分，按回测回记入场。'}所选策略入场分数门槛 ${num(wick?wk?.min_score:allin?ai?.min_score:spike?sp?.min_score:v3?config?.min_score:data?.model?.threshold,4)}，分数不代表胜率。${key==='live-allin3'?'触价列表显示本策略独立触价前评分，门槛0.6':'触价列表共用原始信号，其评分结果按 v2 门槛 '+num(data?.model?.threshold,4)+' 显示'}，资金分配以各策略交易记录为准。模型训练截止 ${esc(data?.model?.trained_before||'—')}；训练行情与当前 OKX 行情存在来源差异。BTC、ETH、XAU、XAG、PAXG、XAUT 仅记录触价。</p>
      <p>统计为所选策略全部已结束资金交易；已结束与触价列表默认显示北京时间今日，往日记录从历史入口按页读取；跨日未平仓触价保留在今日，筛选不改变累计统计。部分平仓立即计入已实现盈亏，已平仓统计仅计完全结束的每笔持仓；策略净值不含浮动盈亏。</p>`;
  }
  function breadthRowHTML(row, state, now, platform='') {
    const event=state.tab==='events', closed=state.tab==='closed', isFunded=funded(row), key=`${state.track}-${state.tab}-${row.id}`;
    if (event) {
      const extra=row.status==='followers'&&finite(row.new_n)?` <b>+${num(row.new_n,0)}</b>`:row.status==='watching'&&finite(row.decision_ms)&&row.decision_ms>now?` · 剩 ${esc(span(row.decision_ms-now))}`:'';
      return `<article class="rp-record rp-breadth-signal" data-rp-record="${esc(key)}" data-symbol="${esc(row.symbol)}" data-status="${esc(row.status)}"><span class="rp-breadth-when"><strong>${date(row.candle_ms)}</strong><small>${finite(row.n)?`${num(row.n,0)} 个币首次碰周一下轨`:'币数待读取'}</small></span><span class="rp-breadth-chip ${BREADTH_TONE[row.status]||'is-muted'}">${esc(signalStatus(row))}${extra}</span></article>`;
    }
    const value=closed?(isFunded?`${signed(row.pnl)} <small>USDT</small>`:`${bp(row.net_bp)} · 不计入资金`):bp(row.unrealized_bp);
    const held=Math.min(1,Math.max(0,(now-row.fill_ms)/Math.max(1,row.decision_ms-row.fill_ms)));
    const progress=closed?'':`<div class="rp-hold-progress"><span style="--rp-hold:${(held*100).toFixed(1)}%" aria-hidden="true"></span><small>持有 ${esc(span(now-row.fill_ms))} / 6天</small><small>到期 ${date(row.decision_ms)} 平仓</small></div>`;
    const tag=`<span class="rp-breadth-tag${isFunded?' is-funded':''}">${esc(allocation(row))}</span>`;
    const fields=cell('买入价',price(row.entry))+cell('6天到期',date(row.decision_ms))+cell('模拟状态',esc(stage(row,now)))+cell(closed?'已实现盈亏':'参考浮动收益',value,closed?(isFunded?tone(row.pnl):''):tone(row.unrealized_bp))+cell(closed?'平仓时间 · 北京':'买入时间 · 北京',date(closed?row.exit_ms:row.fill_ms));
    const more=detail('名义本金',isFunded?`${num(row.notional)} USDT · 1 倍`:'对照记录，不占用资金')+detail('止损','不设 · 第 6 天按 1 分钟开盘价平仓')
      +(closed?detail('平仓价格',price(row.exit_price))+detail('毛收益',bp(row.gross_bp))+detail('手续费',finite(row.fee_bp)?`${num(row.fee_bp/100)}%`:'—')+detail('净收益',bp(row.net_bp)):detail('后台参考报价',price(row.mark)));
    const selected=state.trade===String(row.id), toneClass=closed?(isFunded?tone(row.pnl):''):tone(row.unrealized_bp);
    return `<article class="rp-record rp-breadth-position" data-rp-record="${esc(key)}" data-symbol="${esc(row.symbol)}" data-selected="${selected}"><div class="rp-record-main"><button type="button" class="rp-row-select" data-rp-trade="${esc(row.id)}" data-rp-focus="select" aria-pressed="${selected}" aria-label="查看 ${esc(base(row.symbol))} ${closed?'历史交易':'持仓'} K 线"></button>${coinHTML(row,platform,`${tag} · <span class="${toneClass}">${value}</span>`,state.period)}</div><div class="rp-record-bottom"><span class="rp-record-meta">买入 ${price(row.entry)} · ${date(row.fill_ms)}</span><button type="button" data-rp-detail="${esc(key)}" data-rp-focus="detail" aria-label="展开 ${esc(base(row.symbol))} 明细" aria-expanded="false" aria-controls="rp-detail-${esc(key)}"><span aria-hidden="true">⌄</span></button></div>${progress}<section id="rp-detail-${esc(key)}" class="rp-row-detail" hidden><div class="rp-detail-fields">${fields}</div><dl>${more}</dl></section></article>`;
  }
  function rowHTML(row, state, now, {favorite=false,platform='',carried=false}={}) {
    if (isBreadthRow(row)) return breadthRowHTML(row, state, now, platform);
    const event=state.tab==='events', closed=state.tab==='closed', isFunded=funded(row);
    const key=`${state.track}-${state.tab}-${row.id}`;
    const value=closed ? (isFunded?`${signed(row.pnl)} <small>USDT</small>`:'不计入资金') : bp(row.unrealized_bp);
    const fields=event
      ? cell('触及 1.618',price(row.level))+cell(targetLabel(row),price(row.target))+cell('评分结果',esc(signalStatus(row)))+cell('入场分数',finite(row.score)?num(row.score,4):'—')+cell('触价时间 · 北京',date(row.candle_ms))
      : cell(row.parent_id?'加仓入场价':'1.618 入场价',price(row.entry))+cell(targetLabel(row,state.track,state.v3),price(row.target))+cell('模拟状态',`${esc(stage(row,now))}<small class="rp-allocation">${esc(allocation(row))}</small>`)+cell(closed?'已实现盈亏':'参考浮动收益',value,closed?(isFunded?tone(row.pnl):''):tone(row.unrealized_bp))+cell(closed?'退出时间 · 北京':'确认时间 · 北京',date(closed?row.exit_ms:row.decision_ms));
    const more=event
      ? detail('观测时间',date(row.observed_ms))+detail('归属周一',esc(row.week))+detail('周一高点',price(row.high))+detail('周一低点',price(row.low))
        +detail('评分说明','通过评分表示满足入场门槛，是否计入资金以对应轨道的交易记录为准。')+(row.error?detail('读取错误',esc(row.error)):'')
      : detail('入场时间',date(row.fill_ms))+detail('确认时间',date(row.decision_ms))+detail('名义本金',isFunded?`${num(row.notional)} USDT`:'影子记录，不占用资金')
        +detail('保护止损',row.be_from_ms?`${price(stopPrice(row))} · ${date(row.be_from_ms)} 起生效`:finite(row.backstop)?`${price(row.backstop)} · 确认前兜底`:'未启用入场价止损')
        +(isAddTrack(state.track)?detail('交易关联',esc(legLabel(row))):'')
        +(row.parent_id?detail('主单确认参考价',price(row.reclaim_ref)):'')
        +(closed ? detail('退出价格',price(row.exit_price))+detail('毛收益',bp(row.gross_bp))+detail('手续费',finite(row.fee_bp)?`${num(row.fee_bp/100)}%`:'—')
          +detail('净收益',bp(row.net_bp))+detail('滑点情景净收益',bp(row.net_slip_bp))
          : detail('后台参考报价',price(row.mark))+detail('入场分数',finite(row.score)?num(row.score,4):'—'))
        +(row.note?detail('记录说明',esc(funded(row)?row.note:allocation(row))):'');
    const orderInfo=detail('触价时均线排列',`${esc(orderLabel(row.ema_order))} · OKX · 15m`);
    const closeButton=!event&&!closed&&isFunded&&finite(row.remaining_notional)&&row.remaining_notional>0?`<div class="rp-position-actions"><span>持仓 ${num(row.remaining_notional)} USDT${row.remaining_notional<row.notional?` · 已减 ${num((1-row.remaining_notional/row.notional)*100,1)}%`:''}</span><button type="button" data-rp-close="${esc(row.id)}" aria-label="模拟平仓 ${esc(base(row.symbol))}">平仓</button></div>`:'';
    const exits=Array.isArray(row.exits)&&row.exits.length?`<div class="rp-exits"><strong>逐笔平仓</strong>${row.exits.map(x=>`<p>${date(x.exit_ms)} · ${esc(reason(x.reason,row.track))} · ${price(x.notional/row.entry)} ${esc(base(row.symbol))}<br>成交 ${price(x.price)} · 净盈亏 ${signed(x.pnl)} USDT</p>`).join('')}</div>`:'';
    const selected=event?state.symbol===row.symbol:state.trade===String(row.id);
    const selectAttr=event?`data-rp-symbol="${esc(row.symbol)}"`:`data-rp-trade="${esc(row.id)}"`;
    const meta=event?date(row.candle_ms):value;
    const orderMeta=event||state.track==='spike'?`<span class="rp-order-meta" title="OKX · 15m · 触价时排列分">${esc(orderLabel(row.ema_order))}${!event&&!isFunded?`<br>${esc(allocation(row))}`:''}</span>`:!event&&['allin3','v3add20','live-allin3','live-wick6x3','live-wick6x3mid','live-wick3x3','live-wick6x3hi','live-wick3x3far'].includes(state.track)&&!isFunded?`<span class="rp-order-meta">${esc(allocation(row))}</span>`:'';
    return `<article class="rp-record" data-rp-record="${esc(key)}" data-symbol="${esc(row.symbol)}" data-selected="${selected}"><div class="rp-record-main"><button type="button" class="rp-row-select" ${selectAttr} data-rp-focus="select" aria-pressed="${selected}" aria-label="查看 ${esc(base(row.symbol))} ${event?'触价':'开仓'} K 线"></button>${coinHTML(row,platform,`<span class="${row.side===1?'rp-positive':row.side===-1?'rp-negative':''}">${sideLabel(row)}</span>${!event&&isAddTrack(state.track)?` · ${esc(legLabel(row))}`:''} · ${event?date(row.candle_ms):`<span class="${closed?(isFunded?tone(row.pnl):''):tone(row.unrealized_bp)}">${value}</span>`}${carried?'<span class="rp-held-tag">持仓</span>':''}${orderMeta}`,state.period)}${scoreHTML(row.score)}</div><div class="rp-record-bottom"><span class="rp-record-meta ${event?'':closed?tone(row.pnl):tone(row.unrealized_bp)}">${meta}</span>${closed?`<button type="button" data-rp-trade="${esc(row.id)}" data-rp-focus="chart" aria-label="查看 ${esc(base(row.symbol))} 历史交易 K 线" title="开仓时段 K 线">`:`<a href="${esc(list.inspectionURL(row.symbol,state.period))}" data-rp-focus="patrol" aria-label="查看 ${esc(base(row.symbol))} 巡检 K 线详情" title="巡检 K 线详情">`}<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>${closed?'</button>':'</a>'}<button type="button" data-rp-detail="${esc(key)}" data-rp-focus="detail" aria-label="展开 ${esc(base(row.symbol))} 明细" aria-expanded="false" aria-controls="rp-detail-${esc(key)}"><span aria-hidden="true">⌄</span></button></div>${closeButton}<section id="rp-detail-${esc(key)}" class="rp-row-detail" hidden><button type="button" data-rp-favorite="${esc(row.symbol)}" data-rp-focus="favorite" class="rp-detail-favorite" aria-pressed="${favorite}" aria-label="${favorite?'取消收藏':'收藏'} ${esc(base(row.symbol))}"><svg width="17" height="17" viewBox="0 0 24 24" fill="${favorite?'currentColor':'none'}" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/></svg><span>${favorite?'取消收藏':'收藏币种'}</span></button><div class="rp-detail-fields">${fields}</div><dl>${orderInfo}${more}</dl>${exits}${closed?`<button type="button" class="rp-chart-link" data-rp-focus="chart" data-rp-trade="${esc(row.id)}">查看开仓时段 K 线 ↗</button>`:`<a class="rp-chart-link" data-rp-focus="chart" href="${esc(chartLink(row.symbol,state.track))}">查看 ${esc(base(row.symbol))} 的 1.618 图表 ↗</a>`}</section></article>`;
  }
  const api={isBreadthTrack,isBreadthRow,breadthRowHTML,span,finite,esc,num,signed,bp,price,funded,detailPending,orderLabel,marginLeverage,TRACKS,trackLabel,targetLabel,stopPrice,stage,signalStatus,allocation,route,chartLink,signalChartLink,validate,summary,refillTotal,capitalLabel,capitalHTML,refillsHTML,rows,dayStart,heldSignals,createHistory,createFeed,rowHTML,pendingHTML,rulesHTML};
  if(typeof module!=='undefined' && module.exports){module.exports=api;return;}
  const root=document.getElementById('monday-reclaim-paper');
  if(!root)return;
  let state=route(), pageActive=false, mounted=false, shown=50, signature='', refillSignature='', chart=null, chartSettings=null, pinned=null, ma=false, internal=false, indicatorsOpen=false, recordsOpen=false, sheet=null, watch=null, chartSymbol='', closer=null, analytics=null,preferences=null,platform='',focus=null,quick=null,book=null,drawings=null,backtest=null,replay=null,liveState=null;
  let keepLedgerOpen=false;
  const ledgerPreference='spike.reclaim.ledgerOpen';
  try{keepLedgerOpen=window.sessionStorage.getItem(ledgerPreference)==='1';}catch{}
  if(keepLedgerOpen)state.ledger='1';
  let touchList=null,chosenTouch='',sourceHint='';
  let scoreRows=null,touchScores=null;
  function touchSource(){return state.source||(isBreadthTrack(state.track)||state.history?'paper':sourceHint||'week');}
  const requests=window.SpikeRuntime.createRequests();
  const historyRequests=window.SpikeRuntime.createRequests();
  const history=createHistory({read:({day,held,...params})=>historyRequests.json('/api/monday-reclaim-paper/history?'+new URLSearchParams(params)),cancel:()=>historyRequests.cancel(),change:()=>render(feed.getState())});
  let startupRead=null,cloudLayout=false,cloudSymbol='';
  const feed=createFeed({read:async()=>{
    const c=new AbortController();startupRead=c;
    try{const pending=window.SpikeStartup?.take('paper-snapshot:'+state.track,c.signal),warm=pending?await pending:null;if(c.signal.aborted)throw Error('cancelled');return warm?.value||await requests.json('/api/monday-reclaim-paper?list_period=today&track='+encodeURIComponent(state.track));}finally{if(startupRead===c)startupRead=null}
  },cancel:()=>{startupRead?.abort();startupRead=null;requests.cancel();},change:render});
  const el=id=>root.querySelector(`[data-rp="${id}"]`);
  function mount(){
    if(mounted)return;mounted=true;
    let storage;try{storage=window.localStorage;}catch{}
    preferences=list.createListPreferences(storage);platform=navigator.userAgentData?.platform||navigator.platform||'';
    const chartIcon=path=>`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;
    const chartSettingsHTML=window.SpikeMondayChartSettings.html(window.SpikeChartDrawings.toolsHTML());
    root.innerHTML=`<div class="rp-wallpaper" data-rp="chart"></div><div class="bt-mode" data-rp="backtest-mode" hidden><strong>历史回测</strong><select data-rp="backtest-track" aria-label="回测策略">${BACKTEST_TRACKS.map(t=>`<option value="${t}">${trackLabel(t)}</option>`).join('')}</select><button type="button" data-rp-action="backtest-list" aria-controls="strategy-backtest-panel" aria-expanded="false">交易列表</button><button type="button" data-rp-action="backtest-exit">返回行情 ↗</button></div><section class="bt-summary" data-rp="backtest-summary" aria-label="所选回测交易" hidden></section><div class="rp-watermark" aria-hidden="true" data-rp="watermark"></div><p class="rp-chart-notice rp-glass" data-rp="chart-notice" role="status" hidden></p>

      <section class="rp-context rp-glass"><div class="rp-context-line"><button type="button" class="rp-symbol-trigger" data-rp-action="watch" aria-controls="rp-watch-panel" aria-expanded="false"><strong data-rp="symbol-label">选择币种</strong><span aria-hidden="true">⌄</span></button><span class="rp-context-divider" aria-hidden="true"></span><div class="rp-context-tools"><div class="rp-track-picker"><span>策略</span><button type="button" class="rp-account-trigger" data-rp="track" aria-label="选择策略" aria-haspopup="dialog" aria-controls="rs-account-picker" aria-expanded="false"><b>spike</b><span aria-hidden="true">⌄</span></button></div><span class="rp-context-divider" aria-hidden="true"></span><a class="rp-signal-link" data-rp="signal-link" aria-label="打开当前币种的 12H 信号图表" title="12H 信号图表" hidden>${chartIcon('M3 18V6m5 13V9m5 6V3m5 16v-7M2 22h20')}</a><button type="button" data-rp-action="records" aria-controls="rp-record-panel" aria-expanded="false" aria-label="交易簿" title="交易簿">${chartIcon('M4 3h14a2 2 0 0 1 2 2v16H6a2 2 0 0 1-2-2V3Zm4 0v18m3-13h6m-6 4h6')}</button></div><details class="rp-context-inspector"><summary aria-label="查看当前币种状态与评分" aria-controls="rp-context-detail" title="查看状态与评分">${chartIcon('M12 16v-5m0-3v.1M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0')}</summary><div id="rp-context-detail" class="rp-context-details" aria-label="当前币种状态与评分"><span class="rp-selection-status" data-rp="selection-status"></span><small class="rp-selection-note" data-rp="selection-note"></small><span class="rp-context-score" data-rp="symbol-score" hidden aria-live="polite"><small>入场评分</small><b></b></span></div></details></div></section>
      <details class="rp-account rp-glass"><summary><span><small data-rp="account-label">spike 模拟</small> · 已实现净值 <b data-rp="equity">—</b><small> USDT</small></span><span data-rp="pnl"></span><span aria-hidden="true">⌄</span><span class="rp-capital-status" data-rp="capital" hidden title="净值 ÷ 累计本金，不是交易收益率"></span></summary><div class="rp-account-body"><section data-rp="metrics" class="rp-metrics"></section><p data-rp="status" role="status"></p><div data-rp="refills"></div><details class="rp-rules"><summary>规则与统计口径</summary><div data-rp="rules"></div></details></div></details><button class="rp-analysis-trigger rp-glass" data-rp-action="analytics" aria-controls="rp-analysis-panel" aria-expanded="false">统计分析 ↗</button>
      <div class="rp-alert rp-glass" data-rp="error" role="alert" hidden></div>
      <section id="rp-record-panel" class="rp-records rp-glass" aria-label="模拟交易簿" aria-hidden="true" inert>
        <header class="rp-record-heading rp-ledger-heading" data-yd-sheet-drag>
          <div class="rp-ledger-top"><div class="rp-ledger-tabs" role="group" aria-label="记录类型">${[['open','持仓'],['closed','已结束'],['events','触价']].map(([tab,label])=>`<button type="button" data-rp-tab="${tab}" aria-pressed="false">${label}<span data-rp="count-${tab}"></span></button>`).join('')}</div><label class="rp-search" hidden><input type="search" placeholder="搜索币种" aria-label="搜索模拟盘币种" maxlength="80" autocomplete="off" data-rp="search"></label><button type="button" data-rp-action="search" aria-label="搜索币种" aria-expanded="false"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg></button><label class="rp-record-sort" title="排序 · 评分优先"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M7 20V4m-4 4 4-4 4 4M17 4v16m-4-4 4 4 4-4"/></svg><select aria-label="记录排序" data-rp="sort">${Object.entries(SORTS).map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}</select></label><button type="button" data-rp-action="history" aria-label="历史记录" title="历史记录" aria-pressed="false"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M3 10a9 9 0 1 1 1.5 7M3 4v6h6M12 7v5l3 2"/></svg></button><button type="button" data-rp-action="close-records" aria-label="收起交易簿">×</button></div>
          <div class="rp-ledger-options">
            <div data-rp="touch-controls" class="tl-controls" hidden>${window.SpikeTouchLedger.toolbar()}<button type="button" data-tl-key title="收回状态说明" aria-label="收回状态说明" aria-controls="rp-touch-key" aria-expanded="false">${chartIcon('M12 16v-5m0-3v.1M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0')}</button></div>
            <label class="rp-scope" data-rp="scope-label" title="记录资金范围"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/></svg><select aria-label="记录资金范围" data-rp="scope"><option value="funded">计入资金</option><option value="shadow">影子记录</option><option value="all">全部记录</option></select></label><div class="rp-ledger-period"><span data-rp="list-period">今日 · 北京时间</span><button type="button" data-rp-action="today" hidden>← 返回今日</button></div>
          </div>
        </header>
        <div class="tl-body" data-rp="touch-body" hidden>${window.SpikeTouchLedger.bodyHTML()}</div><div class="rp-record-scroll"><p data-rp="history-error" class="rp-history-error" role="status" hidden></p><button type="button" data-rp-action="retry-history" class="rp-more" hidden>重试读取历史</button><div data-rp="rows" class="rp-rows"></div><button type="button" data-rp-action="more" class="rp-more" hidden>再显示 50 条</button><div data-rp="pending"></div><p class="rp-record-caption" data-rp="caption"></p><p class="rp-footnote" data-rp="record-note"></p></div></section>
      <nav class="rp-dock" aria-label="模拟盘图表工具"><div class="rp-layout-switch" role="group" aria-label="图表布局"><button type="button" aria-pressed="true">区间</button><button type="button" data-rp-action="cloud-layout" aria-pressed="false">信号</button></div><div class="rp-periods" role="group" aria-label="图表周期">${['5m','15m','1H','4H','12H','1D'].map(p=>`<button type="button" data-rp-period="${p}" aria-pressed="false">${p}</button>`).join('')}</div><label class="rp-more-periods" title="更多周期"><span data-rp="extra-period" hidden></span><span aria-hidden="true">⌄</span><select data-rp="period" aria-label="K 线周期">${['1m','2m','3m','5m','15m','1H','4H','12H','1D'].map(v=>`<option value="${v}">${v}</option>`).join('')}</select></label><button type="button" class="rp-indicator-quick" data-rp-action="monthly" role="switch" aria-checked="true" aria-label="月首周 1.618 区间" title="月首周 1.618 区间">月首周</button><button type="button" class="rp-indicator-quick" data-rp-action="ma" role="switch" aria-checked="false" aria-label="六均线组" title="SMA / EMA · 20、60、120">六均线</button><div class="rp-indicators"><button type="button" data-rp-action="indicators" aria-label="图表设置" aria-controls="rp-indicator-panel" aria-expanded="false"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="var(--rp-canvas)"/><circle cx="15" cy="17" r="3" fill="var(--rp-canvas)"/></svg><span class="rp-indicator-label">设置</span><span data-rp="indicator-count" hidden></span></button></div><button type="button" data-rp-action="fit" title="显示全部已加载 K 线" aria-label="显示全部 K 线">${chartIcon('M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5')}</button><button type="button" data-rp-action="latest" title="回到最新 K 线" aria-label="回到最新 K 线">${chartIcon('m5 5 8 7-8 7m14-14v14')}</button><button type="button" data-rp-action="follow" aria-pressed="true" aria-label="跟随最新单" title="跟随最新单">${chartIcon('M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1')}</button></nav><section id="rp-indicator-panel" class="rp-indicator-panel rp-glass mcs-panel" aria-label="图表设置" aria-hidden="true" inert>${chartSettingsHTML}</section><span class="rp-chart-foot">逐周 1.618 区间 · 开平仓按实际成交标记</span>`;
    const account=root.querySelector('.rp-account');
    window.SpikeYoloPreviewSheet.bindOutside({panel:account,dismiss:restore=>{account.open=false;if(restore)account.querySelector('summary').focus({preventScroll:true});}});
    const heightProperties=new Map([[root.querySelector('.rp-context'),'--rp-context-height'],[root.querySelector('.rp-account>summary'),'--rp-account-summary-height'],[el('backtest-summary'),'--bt-summary-height']]);
    const heights=new ResizeObserver(entries=>{
      // Read the browser's measured border boxes before writing layout vars.
      // Re-reading geometry after each write forces synchronous style/layout.
      const measured=entries.map(entry=>[heightProperties.get(entry.target),
        (entry.borderBoxSize?.[0]||entry.borderBoxSize)?.blockSize??entry.target.getBoundingClientRect().height]);
      for(const [property,height] of measured){const value=`${height}px`;if(root.style.getPropertyValue(property)!==value)root.style.setProperty(property,value);}
    });
    for(const node of heightProperties.keys())heights.observe(node);
    drawings=window.SpikeChartDrawings.create({root,onTool:()=>setIndicators(false)});
    chart=window.SpikeReclaimChart.create(el('chart'),text=>{el('chart-notice').textContent=text;el('chart-notice').hidden=!text;},drawings,(trade,track)=>{if(replay?.track===track&&replay.row.id===trade.id){replay.row={...replay.row,fills:trade.fills};replace('backtest-summary',window.SpikeStrategyBacktest.summaryHTML(replay.row));}});
    chartSettings=window.SpikeMondayChartSettings.create({panel:root.querySelector('.rp-indicator-panel'),storage,onChange:value=>{
      chart.setAppearance?.(value);
      chart.setMonthly?.(value.monthlyEnabled);
      root.querySelector('[data-rp-action="monthly"]')?.setAttribute('aria-checked',String(value.monthlyEnabled));
      if(chartSettings){const count=Number(ma)+Number(internal)+chartSettings.activeCount();el('indicator-count').textContent=String(count);el('indicator-count').hidden=!count;}
    }});
    sheet=window.SpikeYoloPreviewSheet.bind({panel:root.querySelector('.rp-records'),isOpen:()=>recordsOpen,dismiss:()=>setRecords(false)});
    touchList=window.SpikeTouchLedger.create({
      host:el('touch-body'),controls:el('touch-controls'),platform,
      onCount:count=>{el('count-events').textContent=count==null?'…':String(count);},
      onSource:source=>{state.source=source;state.history='';delete state.reclaim;shown=50;sync();render(feed.getState());},
      onPeriod:period=>{if(period==='all')delete state.reclaim;else state.reclaim=period;sync();render(feed.getState());},
      onPick:(row,context)=>{
        leaveBacktest(false);state.symbol=row.symbol;state.trade='';pinned=null;
        if(context.source==='cloud'){
          chosenTouch=row.id;delete state.monthEvent;chartSymbol=row.symbol;
          if(cloudLayout)openCloud(row);else setCloudLayout(true,row);
          sync();render(feed.getState());return;
        }
        if(cloudLayout)setCloudLayout(false);
        chosenTouch=row.event_id;
        if(context.source==='month')state.monthEvent=row.event_id;else delete state.monthEvent;
        if(state.reclaim)state.period=state.reclaim;
        chart.resetFocus();sync();render(feed.getState());
      },
    });
    closer=window.SpikeReclaimClose.create({root,onChanged:()=>{feed.refresh();watch?.refresh();analytics?.refresh();}});
    watch=window.SpikeReclaimWatchlist.create({root,createFeed,onScore:score=>{const node=el('symbol-score');node.hidden=!!replay||!score;node.classList.toggle('is-high',!!score?.high);node.querySelector('b').textContent=score?.text||'';node.title=score?.title||'';node.setAttribute('aria-label',score?.title||'');},getContext:()=>({track:feed.getState().data?.tracks[state.track],signals:signalRows(feed.getState().data,state.track),symbol:chartSymbol,period:state.period}),onOpen:()=>{backtest?.setOpen(false,false);analytics?.setOpen(false,false);},onPeriodChange:period=>{sourceHint=period;if(state.tab==='events'&&['week','month'].includes(touchSource())){state.source=period;sync();render(feed.getState());}},onSelect:(symbol,options={})=>{leaveBacktest(false);delete state.monthEvent;chosenTouch='';sourceHint=options.period==='month'?'month':'week';state.track=window.SpikeReclaimWatchlist.holdingTrack(feed.getState().data?.tracks,symbol,state.track,book.getOrder());state.symbol=symbol;state.trade='';shown=50;pinned=null;chart.resetFocus();sync();render(feed.getState());}});
    analytics=window.SpikeReclaimAnalytics.create({root,createFeed,onOpen:()=>{backtest?.setOpen(false,false);watch.setOpen(false,false);},onTrack:selectTrack});
    backtest=window.SpikeStrategyBacktest.create({root,onOpen:()=>{watch?.setOpen(false,false);analytics?.setOpen(false,false);quick?.close();},onSelect:value=>{
      if(cloudLayout)setCloudLayout(false);
      if(!replay)liveState={...state};const raw=value.options||value.replayOptions||value,source=raw.replay_source||raw.source;
      const options=source==='breadth-ema20'?{replay_source:source,event_id:raw.event_id||value.row.event_id,scope:raw.scope||value.row.scope||''}:{};
      replay={track:value.track,row:value.row,options};if(TRACKS.includes(value.track))state.track=value.track;state.trade='';state.symbol=value.row.symbol.replace(/USDT$/,'-USDT-SWAP');state.period=window.SpikeStrategyBacktest.periodFor(value.row,state.period);pinned=null;chart.resetFocus();render(feed.getState());
    }});
    book=window.SpikeStrategyBook.create({root,tracks:TRACKS,getTrack:()=>state.track,getSnapshot:()=>feed.getState().data,getPnl:t=>summary(feed.getState().data?.tracks?.[t],t).pnl,renderRules:rulesHTML,createFeed,onSelect:selectTrack,onBacktest:(track,row,options)=>{focus?.reset();backtest.open(track,row,options);}});
    el('backtest-track').addEventListener('change',event=>{if(root.classList.contains('is-paper-history')){selectTrack(event.target.value);setRecords(true);}else backtest.open(event.target.value);});
    el('period').addEventListener('change',event=>{state.period=replay?.track===CLOUD?'12H':event.target.value;sync();render(feed.getState());});
    quick=window.SpikeHomeTicket.create({root,context:()=>root.classList.contains('is-replay')?null:({symbol:chartSymbol,timeframe:state.period})});
    focus=window.SpikeChartFocus.bind(root);
    root.addEventListener('keydown',event=>{if(event.key!=='Escape'||event.defaultPrevented||document.querySelector('dialog[open], [popover]:popover-open'))return;const contextInspector=root.querySelector('.rp-context-inspector');if(contextInspector?.open){event.preventDefault();event.stopPropagation();contextInspector.open=false;contextInspector.querySelector('summary').focus({preventScroll:true});}else if(recordsOpen&&root.querySelector('.rp-ledger-heading').classList.contains('is-searching')){event.preventDefault();event.stopPropagation();setSearch(false);}else if(indicatorsOpen){event.preventDefault();event.stopPropagation();setIndicators(false,true);}else if(recordsOpen){event.preventDefault();event.stopPropagation();setRecords(false);}});
    document.addEventListener('pointerdown',event=>{const contextInspector=root.querySelector('.rp-context-inspector');if(contextInspector?.open&&!contextInspector.contains(event.target))contextInspector.open=false;if(!focus?.hidden&&indicatorsOpen&&!event.target.closest('.rp-indicators,.rp-indicator-panel'))setIndicators(false);},true);
    root.addEventListener('focusin',event=>{if(!focus?.hidden&&indicatorsOpen&&!event.target.closest('.rp-indicators,.rp-indicator-panel'))setIndicators(false);});
    root.addEventListener('click',event=>{
      const record=event.target.closest('[data-rp-record]');
      if(record&&!event.target.closest('a,button,input,select,.rp-row-detail')){record.querySelector('.rp-row-select').click();return;}
      const signalLink=event.target.closest('[data-rp=signal-link]');if(signalLink){event.preventDefault();setCloudLayout(true);return;}
      const button=event.target.closest('button');if(!button)return;
      if(button.dataset.rpPeriod){state.period=replay?.track===CLOUD?'12H':button.dataset.rpPeriod;sync();render(feed.getState());return;}
      if(button.dataset.rpDetail){const detail=document.getElementById(button.getAttribute('aria-controls'));detail.hidden=!detail.hidden;button.setAttribute('aria-expanded',String(!detail.hidden));return;}
      if(button.dataset.rpFavorite){preferences.toggle(button.dataset.rpFavorite);watch?.refreshPreferences();render(feed.getState());return;}
      if(button.dataset.rpSymbol){leaveBacktest(false);delete state.monthEvent;chosenTouch='';state.symbol=button.dataset.rpSymbol;state.trade='';pinned=null;chart.resetFocus();sync();render(feed.getState());return;}

      if(button.dataset.rpClose){const data=feed.getState().data,p=data?.tracks[state.track]?.open.find(p=>String(p.id)===button.dataset.rpClose);if(p)closer.open({...p,margin_leverage:marginLeverage(data,state.track)});}
      if(button.dataset.rpTab){state.tab=button.dataset.rpTab;if(state.tab==='open')state.history='';shown=50;sync();render(feed.getState());}
      if(button.dataset.rpTrade){leaveBacktest(false);delete state.monthEvent;chosenTouch='';state.symbol='';state.trade=button.dataset.rpTrade;pinned=window.SpikeReclaimChart.select(feed.getState().data?.tracks[state.track],state.trade,history.getState().rows.find(p=>String(p.id)===state.trade));if(pinned?.stage==='closed')state.period=window.SpikeStrategyBacktest.periodFor({entry_ms:pinned.fill_ms,exit_ms:pinned.exit_ms},state.period);chart.resetFocus();sync();render(feed.getState());}
      const action=button.dataset.rpAction;
      if(action==='cloud-layout'){setCloudLayout(true);return;}
      if(action==='history'||action==='today'){state.history=action==='today'||state.history?'':'1';if(state.history&&state.tab==='open')state.tab='closed';shown=50;sync();render(feed.getState());}
      if(action==='retry-history')history.load(history.getState().loaded);
      if(action==='search')setSearch(!root.querySelector('.rp-ledger-heading').classList.contains('is-searching'));
      if(action==='watch')watch.toggle();
      if(action==='analytics')analytics.toggle();
      if(action==='records')setRecords(!recordsOpen);
      if(action==='close-records')setRecords(false);
      if(action==='fit'){if(root.classList.contains('is-replay')){chart.resetFocus();render(feed.getState());}else chart.fit();}if(action==='latest')chart.latest();
      if(action==='indicators')setIndicators(!indicatorsOpen);
      if(action==='monthly')chartSettings?.setMonthlyEnabled(!chartSettings.getState().monthlyEnabled);
      if(action==='ma'){ma=!ma;render(feed.getState());}
      if(action==='internal'){internal=!internal;render(feed.getState());}
      if(action==='backtest-list'){if(root.classList.contains('is-paper-history'))setRecords(!recordsOpen);else backtest.setOpen(true);}
      if(action==='backtest-exit'){if(root.classList.contains('is-paper-history')){state.symbol=chartSymbol;state.trade='';pinned=null;chart.resetFocus();}else leaveBacktest();sync();render(feed.getState());}
      if(action==='follow'){leaveBacktest(false);delete state.monthEvent;chosenTouch='';state.symbol='';state.trade='';pinned=null;chart.resetFocus();sync();render(feed.getState());}
      if(button.dataset.rpAction==='more'){if(state.history)history.load(true);else{shown+=50;render(feed.getState());}}
    });
    el('scope').addEventListener('change',event=>{state.scope=event.target.value;shown=50;sync();render(feed.getState());});
    el('sort').addEventListener('change',event=>{state.sort=recordSort(event.target.value);shown=50;root.querySelector('.rp-record-scroll').scrollTop=0;sync();render(feed.getState());});
    el('search').addEventListener('input',event=>{state.q=event.target.value;shown=50;sync();render(feed.getState());});
    root.addEventListener('error',event=>{if(event.target.tagName==='IMG')event.target.hidden=true;},true);
  }
  function leaveBacktest(restore=true){
    if(replay&&cloudLayout)setCloudLayout(false);
    if(replay&&restore&&liveState)state={...liveState};if(replay?.track==='breadth-ema20')backtest?.clearReplayLink();if(replay?.track===CLOUD)window.SpikeCloudBookRoute?.write(null);replay=null;liveState=null;backtest?.setOpen(false,false);chart?.resetFocus();
  }
  function selectTrack(track){
    if(!TRACKS.includes(track))return;
    const wasReplay=!!replay;leaveBacktest(false);if(track===state.track&&!wasReplay)return;
    state.track=track;state.trade='';pinned=null;shown=50;sync();render(feed.getState());
  }
  function setSearch(value){
    const heading=root.querySelector('.rp-ledger-heading'),button=heading.querySelector('[data-rp-action="search"]');
    heading.classList.toggle('is-searching',value);heading.querySelector('.rp-search').hidden=!value;
    button.setAttribute('aria-expanded',String(value));button.setAttribute('aria-label',value?'收起搜索':'搜索币种');
    (value?el('search'):button).focus({preventScroll:true});
  }
  function setIndicators(value,focus=false){
    indicatorsOpen=value;const panel=root.querySelector('.rp-indicator-panel');if(!panel)return;
    panel.classList.toggle('is-open',value);panel.inert=!value;panel.setAttribute('aria-hidden',String(!value));
    const trigger=root.querySelector('[data-rp-action="indicators"]');trigger.setAttribute('aria-expanded',String(value));
    if(focus)trigger.focus({preventScroll:true});
  }
  function setRecords(value,focus=true){
    if(value!==keepLedgerOpen){
      keepLedgerOpen=value;
      try{if(value)window.sessionStorage.setItem(ledgerPreference,'1');else window.sessionStorage.removeItem(ledgerPreference);}catch{}
    }
    if(value)backtest?.setOpen(false,false);if(value&&matchMedia('(max-width:700px)').matches)analytics?.setOpen(false,false);if(value)watch?.setOpen(false,false);sheet?.reset();recordsOpen=value;root.classList.toggle('has-ledger',value);if(value)state.ledger='1';else{delete state.ledger;history.select(null);touchList?.set(null);}sync();const panel=root.querySelector('.rp-records');panel.classList.toggle('is-open',value);root.classList.toggle('has-history-list',value&&root.classList.contains('is-paper-history'));if(root.classList.contains('is-paper-history'))root.querySelector('[data-rp-action="backtest-list"]').setAttribute('aria-expanded',String(value));panel.inert=!value;panel.setAttribute('aria-hidden',String(!value));root.querySelector('[data-rp-action="records"]').setAttribute('aria-expanded',String(value));if(value)render(feed.getState());else if(focus)root.querySelector(root.classList.contains('is-paper-history')?'[data-rp-action="backtest-list"]':'[data-rp-action="records"]').focus({preventScroll:true});}
  function openCloud(record=null){
    if(!chartSymbol||!window.SpikeCloudSignals)return;
    cloudSymbol=chartSymbol;
    window.SpikeCloudSignals.openEmbedded(chartSymbol,()=>{cloudLayout=false;cloudSymbol='';root.classList.remove('is-cloud-layout');chart?.setActive(pageActive&&!document.hidden);if(pageActive)render(feed.getState());},root,record);
  }
  function setCloudLayout(value,record=null){
    if(value===cloudLayout||!chartSymbol)return;
    if(value){focus?.reset();setIndicators(false);watch?.setOpen(false,false);analytics?.setOpen(false,false);if(!replay)backtest?.setOpen(false,false);cloudLayout=true;root.classList.add('is-cloud-layout');chart?.setActive(false);openCloud(record);}
    else window.SpikeCloudSignals?.closeEmbedded();
  }
  function sync(){window.SpikeNavigation?.sync('signals',{pane:'reclaim',...state});}
  // Opening a fills table changes the live DOM; account ticks must not close it.
  const summaryMarkup=new WeakMap();
  function replace(id,html){
    const node=el(id);
    if(id==='backtest-summary'){
      if(summaryMarkup.get(node)===html)return;
      summaryMarkup.set(node,html);
    }else if(node.innerHTML===html)return;
    node.innerHTML=html;
  }
  function render(current){
    if(!pageActive)return;mount();
    if(feed.select(state.track))return;
    const {data,error,busy}=current, track=data?.tracks?.[state.track], stats=summary(track,state.track);
    const cloudReplay=replay?.track===CLOUD;if(cloudReplay)state.period='12H';
    const pendingDetail=detailPending(data,state.track);
    if(pendingDetail&&!busy&&!error)feed.refresh();
    root.setAttribute('aria-busy',String(busy && !data));
    el('account-label').textContent=trackLabel(state.track)+' 模拟';
    root.querySelector('.rp-records').setAttribute('aria-label',trackLabel(state.track)+' 模拟交易簿');
    const missingTrack=data&&!track;
    const worker=data?.worker, workerIssue=worker && (worker.alive!==true || worker.error);
    el('status').textContent=data ? `${error?'上次记录':'更新于'} ${date(data.now_ms)} · 北京时间 · ${workerIssue?'后台需要检查':busy?'同步中…':'每 10 秒同步'}${worker?.queue>0?` · 等待处理 ${worker.queue} 条`:''}` : busy?'正在读取模拟盘…':'尚未读取到模拟盘数据';
    el('error').hidden=!error && !workerIssue && !missingTrack;
    el('error').textContent=error ? `同步失败：${error}。${data?'保留上次记录，正在自动重试。':'正在自动重试。'}` : workerIssue ? `后台状态异常：${worker.error || '处理线程未运行'}。当前展示已保存的记录。` : missingTrack ? `${trackLabel(state.track)} 策略暂未返回，无法显示该策略数据。` : '';
    book?.sync();
    root.querySelectorAll('[data-rp-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.rpTab===state.tab)));
    const breadth=isBreadthTrack(state.track),publicTouches=state.tab==='events'&&touchSource()!=='paper',eventTab=root.querySelector('[data-rp-tab="events"]');if(eventTab?.firstChild?.nodeType===3)eventTab.firstChild.nodeValue='触价';
    if(el('search').value!==state.q)el('search').value=state.q;
    root.querySelector('[data-rp-action="search"]').classList.toggle('has-query',!!state.q);
    root.querySelector('[data-rp-action="history"]').setAttribute('aria-pressed',String(!!state.history));
    root.querySelector('[data-rp-action="history"]').setAttribute('aria-label',state.history?'返回今日记录':'历史记录');
    root.querySelector('[data-rp-action="today"]').hidden=publicTouches||!state.history;
    el('list-period').textContent=state.history?'历史 · 今日之前':state.tab==='open'?'当前持仓':breadth?'近期记录 · 北京时间':'今日 · 北京时间';
    el('scope').value=state.scope;el('scope-label').hidden=state.tab==='events';
    root.querySelector('[data-rp-action="history"]').hidden=publicTouches;
    const timeSortOnly=publicTouches&&touchSource()!=='week';
    el('sort').querySelector('option[value="score"]').hidden=timeSortOnly;
    el('sort').value=timeSortOnly&&state.sort==='score'?'time_desc':state.sort;
    el('sort').parentElement.title='排序 · '+SORTS[el('sort').value];el('sort').parentElement.dataset.time=String(el('sort').value!=='score');
    const selected=replay?null:state.symbol&&!state.trade?window.SpikeReclaimWatchlist.coinTrade(track,state.symbol):window.SpikeReclaimChart.select(track,state.trade,pinned||history.getState().rows.find(p=>String(p.id)===state.trade));if(state.trade&&selected)pinned=selected;
    chartSymbol=state.symbol||selected?.symbol||(state.trade?'':'BTC-USDT-SWAP');
    const historical=!!state.trade&&selected?.stage==='closed',replaying=!!replay||historical;
    root.classList.toggle('is-backtest',!!replay);root.classList.toggle('is-paper-history',historical);root.classList.toggle('is-replay',replaying);root.classList.toggle('is-cloud-replay',cloudReplay);root.classList.toggle('has-history-list',historical&&recordsOpen);quick?.refresh();
    el('backtest-mode').hidden=!replaying;el('backtest-summary').hidden=!replaying;
    el('backtest-mode').querySelector('strong').textContent=cloudReplay?'OKX · 12H 历史回测':historical?'历史交易':'历史回测';
    el('backtest-track').setAttribute('aria-label',historical?'历史交易策略':'回测策略');el('backtest-summary').setAttribute('aria-label',historical?'所选历史交易':'所选回测交易');
    const listButton=root.querySelector('[data-rp-action="backtest-list"]');listButton.setAttribute('aria-controls',historical?'rp-record-panel':'strategy-backtest-panel');listButton.setAttribute('aria-expanded',String(historical?recordsOpen:backtest?.isOpen()));
    if(replay){el('backtest-track').value=replay.track;replace('backtest-summary',window.SpikeStrategyBacktest.summaryHTML(replay.row));}
    else if(historical){el('backtest-track').value=state.track;replace('backtest-summary',window.SpikeStrategyBacktest.summaryHTML(window.SpikeReclaimChart.paperReplayTrade(selected),{source:'OKX',mode:'历史交易',costNote:funded(selected)?'已扣手续费':'影子记录 · 不计入净值',funded:funded(selected)}));}
    const breadthTrackOption=el('backtest-track').querySelector('option[value="breadth-ema20"]');breadthTrackOption.hidden=historical;breadthTrackOption.disabled=historical;
    const liveBreadthOption=el('backtest-track').querySelector('option[value="live-breadth"]');liveBreadthOption.hidden=!historical;liveBreadthOption.disabled=!historical;
    const fitButton=root.querySelector('[data-rp-action="fit"]');fitButton.setAttribute('aria-label',replaying?'定位交易':'显示全部 K 线');fitButton.title=replaying?'显示开仓、平仓及附近 K 线':'显示全部已加载 K 线';
    analytics?.setTrack(state.track);
    if(cloudLayout){if(cloudSymbol!==chartSymbol)openCloud();}else if(replay)chart.showReplay(replay.track,replay.row,state.period,cloudReplay?false:ma,cloudReplay?false:internal,false,replay.options||replay.replayOptions||(replay.replay_source?replay:{}));else if(state.monthEvent&&state.symbol&&!state.trade)chart.showMonthEvent(chartSymbol,state.monthEvent,state.period,ma,internal);else if(historical)chart.showHistory(state.track,selected,state.period,ma,internal);else chart.show(selected?{...selected,asof_ms:data.now_ms}:null,state.period,ma,chartSymbol,internal,[...(track?.open||[]),...(track?.closed||[]),...(pinned&&!track?.closed?.some(p=>p.id===pinned.id)?[pinned]:[])].filter(p=>funded(p)&&p.symbol===chartSymbol));watch?.render();if(replay)el('symbol-score').hidden=true;el('period').value=state.period;el('period').disabled=cloudReplay;
    if(!selected&&state.trade){el('chart-notice').textContent='该交易不在当前返回记录中，请点击跟随最新单。';el('chart-notice').hidden=false;}
    root.querySelectorAll('[data-rp-action="ma"]').forEach(button=>button.setAttribute('aria-checked',String(ma)));
    root.querySelector('[data-rp-action="internal"]').setAttribute('aria-checked',String(internal));
    root.querySelector('[data-rp-action="monthly"]').setAttribute('aria-checked',String(!!chartSettings?.getState().monthlyEnabled));
    const activeSettings=Number(ma)+Number(internal)+(chartSettings?.activeCount()||0);el('indicator-count').textContent=String(activeSettings);el('indicator-count').hidden=!activeSettings;
    root.querySelector('[data-rp-action="follow"]').setAttribute('aria-pressed',String(!replay&&!state.trade&&!state.symbol));
    root.querySelector('[data-rp-action="follow"]').setAttribute('aria-label',historical?'返回实时':'跟随最新单');
    root.querySelector('[data-rp-action="follow"]').title=historical?'返回实时':'跟随最新单';
    const periodButtons=root.querySelectorAll('[data-rp-period]');
    const extraPeriod=el('extra-period');
    root.querySelector('.rp-layout-switch').hidden=cloudReplay;root.querySelector('.rp-more-periods').hidden=cloudReplay;root.querySelector('.rp-indicators').hidden=cloudReplay;root.querySelectorAll('.rp-indicator-quick').forEach(button=>button.hidden=cloudReplay);
    extraPeriod.hidden=Array.from(periodButtons).some(b=>b.dataset.rpPeriod===state.period);
    if(!extraPeriod.hidden)extraPeriod.textContent=state.period;
    periodButtons.forEach(b=>{
      b.hidden=cloudReplay&&b.dataset.rpPeriod!=='12H';b.disabled=cloudReplay;
      const pressed=String(b.dataset.rpPeriod===state.period);
      if(b.getAttribute('aria-pressed')!==pressed){
        b.setAttribute('aria-pressed',pressed);
        if(pressed==='true')window.SpikeChartFocus.revealControl(b);
      }
    });
    el('signal-link').hidden=!chartSymbol;el('signal-link').href=signalChartLink(chartSymbol);el('signal-link').setAttribute('aria-label','打开 '+base(chartSymbol||'')+' 的 12H 信号图表');
    replace('symbol-label',chartSymbol?`${esc(base(chartSymbol))}<small> / USDT</small>`:'选择币种');
    replace('selection-status',replay?`<span class="${tone(replay.row.pnl)}">历史回测 · ${sideLabel(replay.row)} · ${signed(replay.row.pnl)} USDT</span>`:selected?`<span class="${selected.side===1?'rp-positive':'rp-negative'}">${sideLabel(selected)} · ${esc(stage(selected,data.now_ms))}</span>`:'');
    el('selection-note').textContent=(cloudReplay?'':state.track==='live-allin3'?'触价前评分 · 模拟运行 · ':isWickTrack(state.track)?'触价收盘进场 · 模拟运行 · ':breadth?'全市场宽度信号 · 模拟运行 · ':'')+(replay?`${replay.row.source||'Binance'} · ${window.SpikeStrategyBacktest.entryLabel(replay.row)} 开仓区间`:selected?`${historical?'历史交易 · 开仓区间':state.trade?'固定交易':state.symbol?'固定币种':'最新资金交易'} · ${date(selected.fill_ms)} 开仓`:chartSymbol?(state.symbol?'固定币种 · 最近记录无资金成交':'默认行情 · 暂无资金成交'):'等待资金交易 · 点击币种查看触价列表');
    if(state.monthEvent&&!replay&&!state.trade){el('selection-note').textContent='月首周触价 · '+(state.monthEvent.endsWith('|upper')?'上轨':'下轨');el('selection-status').textContent='';el('symbol-score').hidden=true;}
    root.querySelector('.rp-chart-foot').textContent=cloudReplay?'OKX · 12H · EMA20/EMA50 与周轨按冻结信号绘制 · 退出保留原 1H 时段':replay?'历史回测 · 标线为原成交价及原周期成交区间':historical?'历史交易 · 开平仓时段 · OKX':'逐周 1.618 区间 · 开平仓按实际成交标记';
    el('watermark').textContent=chartSymbol?base(chartSymbol)+' / USDT':'';
    const allin=isAllinTrack(state.track);
    root.querySelector('.rp-account').classList.toggle('has-capital',allin);
    el('capital').hidden=!allin;el('capital').textContent=allin?capitalLabel(track):'';
    const refillMarkup=allin?refillsHTML(track):'';
    if(refillMarkup!==refillSignature){
      const refillOpen=el('refills').querySelector('details')?.open;
      replace('refills',refillMarkup);refillSignature=refillMarkup;
      if(refillOpen&&el('refills').querySelector('details'))el('refills').querySelector('details').open=true;
    }
    replace('equity',num(stats.equity));replace('pnl',`<b class="${tone(stats.pnl)}">${signed(stats.pnl)}</b>`);
    replace('metrics',cell('已实现交易盈亏',`${signed(stats.pnl)} USDT`,tone(stats.pnl))+cell('累计已平仓',`${num(stats.count,0)} 笔`)+cell('已平仓胜率',finite(stats.winRate)?`${num(stats.winRate,1)}%`:'—')+cell('平均单笔净收益',bp(stats.mean),tone(stats.mean))+cell('初始资金',`${num(stats.start)} USDT`)+(allin?capitalHTML(track):''));
    replace('rules',rulesHTML(data,state.track));
    // Closed drawers retain their DOM but do no record formatting or image work.
    if(!recordsOpen)return;
    const source=touchSource();
    root.querySelector('.rp-record-scroll').hidden=publicTouches;
    const signals=publicTouches&&source==='week'&&data?signalRows(data,state.track):null;
    if(signals!==scoreRows){scoreRows=signals;touchScores=signals&&new Map(signals.map(r=>[r.week+'|'+r.symbol+'|'+(r.side===1?'lower':'upper'),r.score]));}
    touchList.set({visible:publicTouches&&pageActive&&!document.hidden,events:state.tab==='events',source,month:source==='month'?state.monthEvent?.split('|')[0]:'',q:state.q,sort:state.sort,period:state.reclaim||'all',chartPeriod:state.period,selected:source==='month'?state.monthEvent:chosenTouch,scores:touchScores});
    if(publicTouches){
      history.select(null);
      el('list-period').textContent=source==='month'?(state.monthEvent?.split('|')[0]||'本月')+' · 北京时间':source==='cloud'?'北京时间':'本周 · 北京时间';
      for(const tab of ['open','closed'])el('count-'+tab).textContent=data?String(rows(data,{...state,tab}).length):'—';
      return;
    }
    const held=data?heldSignals(data):new Set(),archive=!!state.history;
    history.select(archive&&data&&!document.hidden?{track:state.track,tab:state.tab,scope:state.scope,sort:state.sort,q:state.q.trim(),day:dayStart(data.now_ms),held:Array.from(held).sort().join(',')}:null);
    const past=history.getState();
    for(const tab of ['open','closed','events'])el(`count-${tab}`).textContent=archive&&tab!=='open'?(tab===state.tab&&past.loaded?String(past.total):''):data&&!(tab==='closed'&&pendingDetail)?String(rows(data,{...state,tab}).length):'—';
    const items=archive?past.rows:data&&!(state.tab==='closed'&&pendingDetail)?rows(data,state):[],visible=archive?items:items.slice(0,shown);
    el('history-error').hidden=!archive||!past.error;
    el('history-error').textContent=past.error?`历史读取失败：${past.error}。${past.loaded?'已保留加载的记录。':''}`:'';
    root.querySelector('[data-rp-action="retry-history"]').hidden=!archive||!past.error;
    el('caption').textContent=archive?(past.busy?'正在读取历史…':past.loaded?`历史${state.tab==='events'?(breadth?'信号':'触价'):'已结束'} · ${SORTS[state.sort]} · 已显示 ${items.length} / ${past.total} 条`:'历史记录待读取'):!data?(error?'记录暂时不可用':'正在读取记录…'):`${state.tab==='open'?'当前持仓':breadth?(state.tab==='events'?'近期信号':'近期已结束'):state.tab==='events'?'今日触价 · 含跨日持仓':'今日已结束'} · ${SORTS[state.sort]} · ${items.length} 条`;
    preferences?.read();
    const rowSignature=visible.length ? visible.map(row=>rowHTML(row,{...state,v3:data.v3,trade:String(selected?.id||state.trade),symbol:chartSymbol},data.now_ms,{favorite:preferences?.has(row.symbol),platform,carried:state.tab==='events'&&row.candle_ms<dayStart(data.now_ms)&&held.has(row.id)})).join('')
      : `<div class="rp-empty"><span aria-hidden="true">${data?'◌':'⋯'}</span><strong>${archive?(past.busy?'正在读取历史':past.error?'历史暂时无法读取':state.q?'没有匹配的历史记录':'暂无历史记录'):!data?(error?'暂时无法读取':'正在读取模拟盘'):missingTrack?'该策略暂不可用':state.tab==='closed'&&pendingDetail?'正在读取记录':state.q?'没有匹配的币种':state.tab==='open'?'当前没有持仓':state.tab==='closed'?'当前没有已结束记录':breadth?'还没有信号':'还没有触价记录'}</strong><p>${data?(state.q?'换一个币种或清空搜索。':state.scope==='shadow'&&state.tab!=='events'?'未计入资金的模拟交易会出现在这里。':'后台产生新记录后会自动更新。'):'请稍候，数据会自动重试同步。'}</p></div>`;
    if(rowSignature!==signature){
      const container=el('rows'),open=new Set(Array.from(container.querySelectorAll('[data-rp-detail][aria-expanded="true"]'),node=>node.dataset.rpDetail));
      const focused=document.activeElement,focusedRow=focused?.closest?.('[data-rp-record]')?.dataset.rpRecord,focusKind=focused?.dataset?.rpFocus;
      const scroll=container.parentElement,top=scroll.scrollTop;
      container.innerHTML=rowSignature;
      container.querySelectorAll('[data-rp-detail]').forEach(button=>{if(open.has(button.dataset.rpDetail)){button.setAttribute('aria-expanded','true');document.getElementById(button.getAttribute('aria-controls')).hidden=false;}});
      if(focusedRow&&focusKind)Array.from(container.querySelectorAll('[data-rp-record]')).find(node=>node.dataset.rpRecord===focusedRow)?.querySelector(`[data-rp-focus="${focusKind}"]`)?.focus({preventScroll:true});
      scroll.scrollTop=top;
      signature=rowSignature;
    }

    root.querySelector('[data-rp-action="more"]').hidden=archive?!past.has_more:items.length<=shown;
    root.querySelector('[data-rp-action="more"]').disabled=archive&&past.busy;
    replace('pending',archive?'':pendingHTML(track,state,data?.now_ms,data?.v3add20));
    el('record-note').textContent=breadth ? (state.tab==='events' ? '同一根5分钟K 31–62 个 OKX 币首次碰周一下轨、之后30分钟无新币才开仓；31–62 是币安 50–99 的近似换算。' : '同一根5分钟K 31–62 个币首次碰周一下轨，之后30分钟无新币 → 买入 SOL，6天后按开盘价平仓；BTC/ETH 只作对照 · OKX 计数为近似换算') : state.tab==='events' ? '共用触价记录 · 评分结果为 v2 门槛；评分 > 0.5 高亮，非胜率。各策略是否入场请看其持仓与已结束记录。' : state.tab==='open' ? '参考浮动收益未扣费；策略净值包含已减仓盈亏，不包含余仓浮盈。' : '净收益已扣手续费；滑点情景仅供对照，不重复扣入策略净值。';
  }
  // Owner 2026-11-06: the SPIKE menu's 1.618 entry always lands on the BTC 15m range wallpaper,
  // also when this page is showing the embedded signal layout or a pinned trade.
  document.addEventListener('click',event=>{if(!pageActive||!event.target.closest?.('a[data-terminal-route="reclaim"]'))return;setCloudLayout(false);pinned=null;chart?.resetFocus?.();});
  document.addEventListener('visibilitychange',()=>{backtest?.setActive(pageActive&&!document.hidden);if(document.hidden){history.select(null);touchList?.set(null);}else if(pageActive)render(feed.getState());feed.setActive(pageActive && !document.hidden);chart?.setActive(pageActive && !cloudLayout && !document.hidden);watch?.setActive(pageActive && !document.hidden);analytics?.setActive(pageActive && !document.hidden);});
  window.SpikeReclaimPaper={
    setActive(value){pageActive=value;root.hidden=!value;if(value){mount();if(state.ledger&&!recordsOpen)setRecords(true,false);}book?.setActive(value);backtest?.setActive(value);if(!value){touchList?.set(null);if(cloudLayout)window.SpikeCloudSignals?.closeEmbedded();history.select(null);focus?.reset();quick?.close();setIndicators(false);sheet?.reset();closer?.hide();}chart?.setActive(value && !cloudLayout && !document.hidden);if(value)render(feed.getState());feed.setActive(value && !document.hidden);watch?.setActive(value && !document.hidden);analytics?.setActive(value && !document.hidden);},
    refresh:()=>{if(recordsOpen)touchList?.refresh();watch?.refresh();analytics?.refresh();return feed.refresh();},route:()=>({...state}),
    restore(params={}){leaveBacktest(false);const next=route(params);
      if(keepLedgerOpen&&!next.ledger){
        next.ledger='1';
        if(!params.tab)next.tab=state.tab;
        if(!params.source&&next.tab==='events'&&state.source)next.source=state.source;
      }
      if(next.track!==state.track||next.trade!==state.trade||next.symbol!==state.symbol)pinned=null;state=next;shown=50;if(mounted&&recordsOpen!==!!state.ledger)setRecords(!!state.ledger,false);render(feed.getState());}
  };
})();
