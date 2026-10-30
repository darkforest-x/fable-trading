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
 * Sorting icon: Stitch 0ee14c4f3aba409ebba1ea8c88e85516 (owner: no extra row).
 */
(() => {
  'use strict';
  const list=typeof module!=='undefined'&&module.exports?require('./monday-list-model.js'):window.SpikeMondayList;
  const icons=typeof module!=='undefined'&&module.exports?require('./coin-icons.js'):window.SpikeCoinIcons;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = (value, digits=2) => finite(value) ? value.toLocaleString('en-US', {minimumFractionDigits:digits,maximumFractionDigits:digits}) : '—';
  const signed = (value, digits=2) => finite(value) ? `${value > 0 ? '+' : ''}${num(value,digits)}` : '—';
  const bp = value => finite(value) ? `${signed(value/100)}%` : '—';
  const price = value => finite(value) ? value.toLocaleString('en-US', {maximumSignificantDigits:9}) : '—';
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
  const TRACKS = ['60','5','v3','v3add','spike','v3lock','allin3','v3add20'];
  const SORTS = {score:'评分优先',time_desc:'时间：最新优先',time_asc:'时间：最早优先'};
  const recordSort = value => Object.hasOwn(SORTS,value) ? value : 'score';
  const trackLabel = key => ({60:'60m',5:'5m',v3:'v3',v3add:'v3add',spike:'spike',v3lock:'v3lock',allin3:'allin3',v3add20:'v3add20'}[key] || '未知策略');
  const isAddTrack = key => ['v3add','v3add20'].includes(key);
  const marginLeverage = (data,key) => ['spike','allin3','v3add20'].includes(key) ? data?.[key]?.leverage : null;
  const orderLabel = value => Number.isInteger(value) && value >= 0 && value <= 4 ? `排列 ${value}/4` : '排列 —';
  function targetLabel(row, track='60', config={min_score:.5,far_score:.6}) {
    if (track==='allin3' || row.track===5060) return '对侧 1.618';
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
  const reason = (value,track) => ({target:'到达目标',breakeven:track===4060?'利润锁止损':'保本止损',backstop:'兜底止损',reclaimed_inside_be:track===4060?'收回但未越过利润锁价':'收回但未越过保本价',failed_reclaim:'确认未收回',manual:'手动平仓'}[value] || '未知退出原因');
  function stage(row, now) {
    if (row.stage === 'closed') return reason(row.reason,row.track);
    if (row.stage === 'breakeven') return finite(row.be_price)?(row.track===4060?'利润锁保护已启用':'保本保护已启用'):'入场价止损已启用';
    if (row.stage === 'pending_confirm') return finite(row.decision_ms) && row.decision_ms <= now ? '等待确认结算' : '等待收盘确认';
    return '状态待核对';
  }
  function signalStatus(row) {
    if (row.status === 'excluded') return '排除币种';
    if (row.status === 'feature_error') return '特征读取失败';
    if (row.status === 'scored') return row.taken === 1 ? '通过评分' : '未通过评分';
    return '状态待核对';
  }
  function allocation(row) {
    if (funded(row)) return '计入资金';
    const label={no_capacity:'额度不足',no_slot:'仓位已满',order_missing:'排列分缺失',busy:'已有资金仓位'}[row.note]
      || (/^order_[0-4]$/.test(row.note||'')?'排列分不足':'不计入资金');
    return `影子 · ${label}`;
  }
  function route(params={}) {
    return {track:TRACKS.includes(params.track) ? params.track : 'spike',tab:['open','closed','events'].includes(params.tab) ? params.tab : 'open',
      sort:recordSort(params.sort),
      history:params.history==='1'&&['closed','events'].includes(params.tab)?'1':'',
      scope:['funded','shadow','all'].includes(params.scope) ? params.scope : 'funded',q:String(params.q || '').slice(0,80),period:['1m','2m','3m','5m','15m','1H','4H','12H','1D'].includes(params.period)?params.period:'15m',trade:/^\d+$/.test(String(params.trade||''))?String(params.trade):'',symbol:validSymbol(params.symbol)?params.symbol:''};
  }
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
        && (key!=='allin3' || data.tracks[key].refills===undefined || finite(refillTotal(data.tracks[key].refills)))))) {
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
    const added=key==='allin3'?refillTotal(track?.refills):0;
    return {equity:track?.equity,start:track?.start_equity,
      pnl:finite(track?.equity) && finite(track?.start_equity) && finite(added) ? track.equity-track.start_equity-added : null,
      count,winRate:finite(count) && count>0 && finite(stats.wins) ? stats.wins/count*100 : null,
      wins:stats.wins,mean:stats.mean_net_bp};
  }
  function rows(data, state) {
    const source = state.tab === 'events' ? data.record_signals??data.signals : state.tab==='closed' ? data.tracks[state.track]?.record_closed??data.tracks[state.track]?.closed??[] : data.tracks[state.track]?.open||[];
    const query=state.q.trim().toUpperCase();
    const start=dayStart(data.now_ms), held=heldSignals(data);
    return source.filter(row => (!query || String(row.symbol).toUpperCase().includes(query))
      && (state.tab==='open'||(state.tab==='events'&&held.has(row.id))||row[state.tab==='events'?'candle_ms':'exit_ms']>=start&&row[state.tab==='events'?'candle_ms':'exit_ms']<start+86400000)
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
    let active=false, epoch=0, pending=null, timer=null;
    let state={data:null,error:null,busy:false};
    const emit=next=>{state={...state,...next};change(state);};
    function refresh() {
      if (!active) return Promise.resolve();
      if (pending) return pending;
      unschedule(timer);timer=null;
      const revision=epoch;
      emit({busy:true});
      const task=Promise.resolve().then(()=>active && revision===epoch ? read() : null)
        .then(data=>{if(active && revision===epoch)emit({data:validateData(data),error:null,busy:false});})
        .catch(error=>{if(active && revision===epoch)emit({error:error instanceof TypeError ? '网络暂时不可用' : error?.message || '读取失败，请稍后重试',busy:false});})
        .finally(()=>{if(pending!==task)return;pending=null;if(active)timer=schedule(refresh,interval);});
      pending=task;return task;
    }
    return {refresh,getState:()=>state,setActive(value){
      if(active===value)return;active=value;epoch++;unschedule(timer);timer=null;
      if(!active){pending=null;cancel();emit({busy:false});}else refresh();
    }};
  }
  function coinHTML(row, platform='',subtitle='') {
    const symbol=base(row.symbol), href=list.tradingViewURL(row.symbol,platform);
    return `<span class="rp-coin">${icons.avatar(symbol)}<span><a href="${esc(href)}" data-rp-focus="tv" target="_blank" rel="noopener noreferrer" title="在 TradingView 打开 ${esc(symbol)}" aria-label="在 TradingView 打开 ${esc(symbol)}">${esc(symbol)} <span aria-hidden="true">↗</span></a><small>${subtitle||sideLabel(row)+' · USDT'}</small></span></span>`;
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
    const track=data?.tracks?.[key], v3=['v3','v3add','v3lock','v3add20'].includes(key), spike=key==='spike', allin=key==='allin3', sp=data?.spike, ai=data?.allin3, config=data?.v3, sizing=v3?config?.sizing:data?.sizing;
    const add20=key==='v3add20', addConfig=data?.[key], lock=key==='v3lock', protection=lock?'利润锁价':'保本价';
    const offset=lock?data?.v3lock?.breakeven_offset_bp:data?.breakeven_offset_bp;
    const baseAllocation=v3
      ? `单笔名义本金按评分线性分配：净值 × min(${num(sizing?.fraction*100)}% × (分数 − ${num(sizing?.cut,4)}) / ${num(sizing?.mean_excess,4)}, ${num(sizing?.cap*100)}%)`
      : `每笔最多按当时净值的 ${num(sizing?.fraction*100,0)}% 分配名义本金`;
    const allocationRule=add20
      ? `最多 ${num(addConfig?.slots,0)} 笔资金仓位（主单与已成交加仓合计），${num(addConfig?.leverage,0)} 倍杠杆；每笔成交保证金为成交时已实现净值 ÷ ${num(addConfig?.slots,0)}，名义本金为保证金 × ${num(addConfig?.leverage,0)}；总保证金不超过净值。每笔亏损按该笔保证金封顶，分批平仓按对应份额封顶；主单遇到满仓或保证金不足时保留为影子单`
      :allin
      ? `同一时间仅持有 1 笔资金仓位；开仓保证金为当时全部已实现净值，名义本金为净值 × ${num(ai?.leverage,0)}；单笔亏损按保证金封顶，分批平仓按对应份额封顶`
      :spike
      ? `最多 ${num(sp?.slots,0)} 个资金仓位，${num(sp?.leverage,0)} 倍杠杆；开仓保证金为当时已实现净值 ÷ ${num(sp?.slots,0)}，名义本金为保证金 × ${num(sp?.leverage,0)}；单笔亏损按该笔保证金封顶`
      : `${baseAllocation}，总名义敞口上限 ${num(data?.sizing?.gross,1)} 倍，按剩余额度分配`;
    const targets=allin?`目标为对侧 1.618；确认前设 ${num(ai?.backstop_pct,0)}% 价格兜底止损。`:v3?`分数 ${num(config?.min_score,2)} 至 ${num(config?.far_score,2)}（不含）目标为区间 0.618；从 ${num(config?.far_score,2)} 起目标为对侧 1.618；低于门槛的影子记录仍以近侧周一边界为目标。确认前设 ${num(config?.backstop_pct,0)}% 兜底止损。`:`做多目标为周一低点，做空目标为周一高点。${spike?`确认前设 ${num(sp?.backstop_pct,0)}% 兜底止损。`:''}`;
    return `<p>${trackLabel(key)} · 独立模拟策略。各策略分别记账，初始资金各 ${num(track?.start_equity)} USDT；${allocationRule}。影子记录不占用资金、不改变净值。</p>
      ${allin?`<p>入场分 ≥ ${num(ai?.min_score)} 才记录交易；已有资金仓位时，后续达标触价保留为影子单。完全平仓后净值低于 ${num(ai?.blowup_below,0)} USDT，后台恢复至 ${num(track?.start_equity)} USDT，本金份数加 1，并记录补充本金。累计本金按每份初始资金 × 本金份数计算，本金倍数为净值 ÷ 累计本金，不是交易收益率。已实现交易盈亏剔除补充本金净入账额。</p>`:''}
      ${spike?`<p>入场分 ≥ ${num(sp?.min_score,2)} 且均线排列分 ≥ ${num(sp?.min_order,0)} 才可占用资金仓位。排列分来源 OKX · 15m，按触价时已闭合的 K 线计算，满分 4。高分信号因排列分不足、排列分缺失或仓位已满而未分配资金时，保留为影子单；入场分不足仅保留触价记录。币安排列线尚未接入。</p>`:''}
      <p>1.618 限价入场；${targets}确认周期 ${num(track?.confirm_minutes,0)} 分钟。确认收盘未收回入场价则退出；已收回但未越过${protection}，也在该收盘退出。收回且越过后，止损移至${isAddTrack(key)?'主单':''}入场价向盈利方向偏移 ${num(offset/100)}%（多加、空减）。旧单以实际记录的保护价为准，${lock?'利润锁':'保本'}退出仍有成交费用。</p>
      ${isAddTrack(key)?`<p>分数从 ${num(addConfig?.from_score,2)} 起，在主单入场价逆向 ${num(addConfig?.drawdown_pct,0)}% 挂一次加仓，${add20?'加仓成交时按当时净值重新计算保证金和名义本金，占用独立仓位；满仓或保证金不足则取消。':'名义本金上限为主单原始名义本金。'}主单确认收盘前有效。加仓共用主单的确认、保本、兜底和目标。主单提前结束会取消未成交加仓；同分钟先触及加仓再触及兜底时，按后台路径结算。待成交单单列，不计入持仓和盈亏；主单与已成交加仓分别计笔。</p>`:''}
      <p>Maker ${num(data?.fees_bp?.maker/100)}% / Taker ${num(data?.fees_bp?.taker/100)}%（单边）；退出滑点情景 ${num(data?.fees_bp?.report_slippage/100)}%。成交费用以每条记录为准。</p>
      <p>所选策略入场分数门槛 ${num(allin?ai?.min_score:spike?sp?.min_score:v3?config?.min_score:data?.model?.threshold,4)}，分数不代表胜率。触价列表共用原始信号，其评分结果按 v2 门槛 ${num(data?.model?.threshold,4)} 显示，资金分配以各策略交易记录为准。模型训练截止 ${esc(data?.model?.trained_before||'—')}；训练行情与当前 OKX 行情存在来源差异。BTC、ETH、XAU、XAG、PAXG、XAUT 仅记录触价。</p>
      <p>统计为所选策略全部已结束资金交易；已结束与触价列表默认显示北京时间今日，往日记录从历史入口按页读取；跨日未平仓触价保留在今日，筛选不改变累计统计。部分平仓立即计入已实现盈亏，已平仓统计仅计完全结束的每笔持仓；策略净值不含浮动盈亏。</p>`;
  }
  function rowHTML(row, state, now, {favorite=false,platform='',carried=false}={}) {
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
    const orderMeta=event||state.track==='spike'?`<span class="rp-order-meta" title="OKX · 15m · 触价时排列分">${esc(orderLabel(row.ema_order))}${!event&&!isFunded?`<br>${esc(allocation(row))}`:''}</span>`:!event&&['allin3','v3add20'].includes(state.track)&&!isFunded?`<span class="rp-order-meta">${esc(allocation(row))}</span>`:'';
    return `<article class="rp-record" data-rp-record="${esc(key)}" data-symbol="${esc(row.symbol)}" data-selected="${selected}"><div class="rp-record-main"><button type="button" class="rp-row-select" ${selectAttr} data-rp-focus="select" aria-pressed="${selected}" aria-label="查看 ${esc(base(row.symbol))} ${event?'触价':'开仓'} K 线"></button>${coinHTML(row,platform,`<span class="${row.side===1?'rp-positive':row.side===-1?'rp-negative':''}">${sideLabel(row)}</span>${!event&&isAddTrack(state.track)?` · ${esc(legLabel(row))}`:''} · ${event?date(row.candle_ms):`<span class="${closed?(isFunded?tone(row.pnl):''):tone(row.unrealized_bp)}">${value}</span>`}${carried?'<span class="rp-held-tag">持仓</span>':''}${orderMeta}`)}${scoreHTML(row.score)}</div><div class="rp-record-bottom"><span class="rp-record-meta ${event?'':closed?tone(row.pnl):tone(row.unrealized_bp)}">${meta}</span><a href="${esc(list.inspectionURL(row.symbol,state.period))}" data-rp-focus="patrol" aria-label="查看 ${esc(base(row.symbol))} 巡检 K 线详情" title="巡检 K 线详情"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg></a><button type="button" data-rp-detail="${esc(key)}" data-rp-focus="detail" aria-label="展开 ${esc(base(row.symbol))} 明细" aria-expanded="false" aria-controls="rp-detail-${esc(key)}"><span aria-hidden="true">⌄</span></button></div>${closeButton}<section id="rp-detail-${esc(key)}" class="rp-row-detail" hidden><button type="button" data-rp-favorite="${esc(row.symbol)}" data-rp-focus="favorite" class="rp-detail-favorite" aria-pressed="${favorite}" aria-label="${favorite?'取消收藏':'收藏'} ${esc(base(row.symbol))}"><svg width="17" height="17" viewBox="0 0 24 24" fill="${favorite?'currentColor':'none'}" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/></svg><span>${favorite?'取消收藏':'收藏币种'}</span></button><div class="rp-detail-fields">${fields}</div><dl>${orderInfo}${more}</dl>${exits}<a class="rp-chart-link" data-rp-focus="chart" href="${esc(chartLink(row.symbol,state.track))}">查看 ${esc(base(row.symbol))} 的 1.618 图表 ↗</a></section></article>`;
  }
  const api={finite,esc,num,signed,bp,price,funded,orderLabel,marginLeverage,TRACKS,trackLabel,targetLabel,stopPrice,stage,signalStatus,allocation,route,chartLink,validate,summary,refillTotal,capitalLabel,capitalHTML,refillsHTML,rows,dayStart,heldSignals,createHistory,createFeed,rowHTML,pendingHTML,rulesHTML};
  if(typeof module!=='undefined' && module.exports){module.exports=api;return;}
  const root=document.getElementById('monday-reclaim-paper');
  if(!root)return;
  let state=route(), pageActive=false, mounted=false, shown=50, signature='', refillSignature='', chart=null, pinned=null, ma=false, internal=false, indicatorsOpen=false, recordsOpen=false, sheet=null, watch=null, chartSymbol='', closer=null, analytics=null,preferences=null,platform='',focus=null,quick=null,book=null,drawings=null,backtest=null,replay=null,liveState=null;
  const requests=window.SpikeRuntime.createRequests();
  const historyRequests=window.SpikeRuntime.createRequests();
  const history=createHistory({read:({day,held,...params})=>historyRequests.json('/api/monday-reclaim-paper/history?'+new URLSearchParams(params)),cancel:()=>historyRequests.cancel(),change:()=>render(feed.getState())});
  let startupRead=null;
  const feed=createFeed({read:async()=>{
    const c=new AbortController();startupRead=c;
    try{const pending=window.SpikeStartup?.take('paper-snapshot',c.signal),warm=pending?await pending:null;if(c.signal.aborted)throw Error('cancelled');return warm?.value||await requests.json('/api/monday-reclaim-paper?list_period=today');}finally{if(startupRead===c)startupRead=null}
  },cancel:()=>{startupRead?.abort();startupRead=null;requests.cancel();},change:render});
  const el=id=>root.querySelector(`[data-rp="${id}"]`);
  function mount(){
    if(mounted)return;mounted=true;
    let storage;try{storage=window.localStorage;}catch{}
    preferences=list.createListPreferences(storage);platform=navigator.userAgentData?.platform||navigator.platform||'';
    root.innerHTML=`<div class="rp-wallpaper" data-rp="chart"></div><div class="bt-mode rp-glass" data-rp="backtest-mode" hidden><button type="button" data-rp-action="backtest-list">回测交易</button><button type="button" data-rp-action="backtest-exit">返回行情</button></div><div class="rp-watermark" aria-hidden="true" data-rp="watermark"></div><p class="rp-chart-notice rp-glass" data-rp="chart-notice" role="status" hidden></p>
      <header class="rp-heading rp-glass"><details class="rp-navigation"><summary aria-label="打开模拟盘导航"><b>ϟ SPIKE</b></summary><nav class="rp-glass"><a href="#signals?pane=reclaim" aria-current="page">1.618 首页</a><a href="#signals?pane=records">信号记录</a><a href="#signals?pane=overview">表现分析</a><a href="#yolo-detect">形态巡检</a></nav></details></header><button type="button" class="rp-theme-toggle theme-toggle rp-glass" data-theme-toggle aria-label="切换浅色或深色" title="切换浅色或深色"></button>
      <section class="rp-context rp-glass"><div class="rp-context-line"><button type="button" class="rp-symbol-trigger" data-rp-action="watch" aria-controls="rp-watch-panel" aria-expanded="false"><strong data-rp="symbol-label">选择币种</strong><span aria-hidden="true">⌄</span></button><span class="rp-context-score" data-rp="symbol-score" hidden aria-live="polite"><small>入场评分</small><b></b></span></div><span class="rp-selection-status" data-rp="selection-status"></span><small class="rp-selection-note" data-rp="selection-note"></small><div class="rp-context-tools"><div class="rp-track-picker"><span>策略</span><button type="button" class="rp-account-trigger" data-rp="track" aria-label="选择策略" aria-haspopup="dialog" aria-controls="rs-account-picker" aria-expanded="false"><b>spike</b><span aria-hidden="true">⌄</span></button></div><button type="button" data-rp-action="records" aria-controls="rp-record-panel" aria-expanded="false">交易簿 <span data-rp="total">—</span></button></div></section>
      <details class="rp-account rp-glass"><summary><span><small data-rp="account-label">spike 模拟</small> · 已实现净值 <b data-rp="equity">—</b><small> USDT</small></span><span data-rp="pnl"></span><span aria-hidden="true">⌄</span><span class="rp-capital-status" data-rp="capital" hidden title="净值 ÷ 累计本金，不是交易收益率"></span></summary><div class="rp-account-body"><section data-rp="metrics" class="rp-metrics"></section><p data-rp="status" role="status"></p><div data-rp="refills"></div><details class="rp-rules"><summary>规则与统计口径</summary><div data-rp="rules"></div></details></div></details><button class="rp-analysis-trigger rp-glass" data-rp-action="analytics" aria-controls="rp-analysis-panel" aria-expanded="false">统计分析 ↗</button>
      <div class="rp-alert rp-glass" data-rp="error" role="alert" hidden></div>
      <section id="rp-record-panel" class="rp-records rp-glass" aria-label="模拟交易簿" aria-hidden="true" inert><header class="rp-record-heading rp-ledger-heading" data-yd-sheet-drag><strong data-rp="ledger-label">60m</strong><label class="rp-search" hidden><input type="search" placeholder="搜索币种" aria-label="搜索模拟盘币种" maxlength="80" autocomplete="off" data-rp="search"></label><button type="button" data-rp-action="search" aria-label="搜索币种" aria-expanded="false"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg></button><label class="rp-record-sort" title="排序 · 评分优先"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M7 20V4m-4 4 4-4 4 4M17 4v16m-4-4 4 4 4-4"/></svg><select aria-label="记录排序" data-rp="sort">${Object.entries(SORTS).map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}</select></label><button type="button" data-rp-action="history" aria-label="历史记录" title="历史记录" aria-pressed="false"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M3 10a9 9 0 1 1 1.5 7M3 4v6h6M12 7v5l3 2"/></svg></button><button type="button" data-rp-action="close-records" aria-label="收起交易簿">×</button><div class="rp-ledger-period"><span data-rp="list-period">今日 · 北京时间</span><button type="button" data-rp-action="today" hidden>← 返回今日</button></div><div class="rp-ledger-filter"><div class="rp-ledger-tabs" role="group" aria-label="记录类型">${[['open','持仓'],['closed','已结束'],['events','触价']].map(([tab,label])=>`<button type="button" data-rp-tab="${tab}" aria-pressed="false">${label}<span data-rp="count-${tab}"></span></button>`).join('')}</div><label class="rp-scope" data-rp="scope-label" title="记录资金范围"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/></svg><select aria-label="记录资金范围" data-rp="scope"><option value="funded">计入资金</option><option value="shadow">影子记录</option><option value="all">全部记录</option></select></label></div></header><div class="rp-record-scroll"><p data-rp="history-error" class="rp-history-error" role="status" hidden></p><button type="button" data-rp-action="retry-history" class="rp-more" hidden>重试读取历史</button><div data-rp="rows" class="rp-rows"></div><button type="button" data-rp-action="more" class="rp-more" hidden>再显示 50 条</button><div data-rp="pending"></div><p class="rp-record-caption" data-rp="caption"></p><p class="rp-footnote" data-rp="record-note"></p></div></section>
      <nav class="rp-dock" aria-label="模拟盘图表工具"><select data-rp="period" aria-label="K 线周期">${['1m','2m','3m','5m','15m','1H','4H','12H','1D'].map(v=>`<option value="${v}">${v}</option>`).join('')}</select><div class="rp-indicators"><button type="button" data-rp-action="indicators" aria-label="图表指标" aria-controls="rp-indicator-panel" aria-expanded="false"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="var(--rp-canvas)"/><circle cx="15" cy="17" r="3" fill="var(--rp-canvas)"/></svg>指标<span data-rp="indicator-count" hidden></span></button><section id="rp-indicator-panel" class="rp-indicator-panel rp-glass" aria-label="图表指标" aria-hidden="true" inert><strong>图表指标</strong><button type="button" data-rp-action="ma" role="switch" aria-checked="false" aria-label="六均线"><span>六均线<small>SMA / EMA · 20、60、120</small></span><i aria-hidden="true"></i></button><button type="button" data-rp-action="internal" role="switch" aria-checked="false" aria-label="区间点位"><span>区间点位<small>0.5 · 0.618 · 0.705</small></span><i aria-hidden="true"></i></button>${window.SpikeChartDrawings.toolsHTML()}</section></div><button type="button" data-rp-action="fit" title="显示全部已加载 K 线">全图</button><button type="button" data-rp-action="latest" title="回到最新 K 线">最新 K</button><button type="button" data-rp-action="follow" aria-pressed="true">跟随最新单</button></nav><span class="rp-chart-foot">逐周 1.618 区间 · 开平仓按实际成交标记</span>`;
    const heightProperties=new Map([[root.querySelector('.rp-context'),'--rp-context-height'],[root.querySelector('.rp-account>summary'),'--rp-account-summary-height']]);
    const heights=new ResizeObserver(entries=>{
      // Read the browser's measured border boxes before writing layout vars.
      // Re-reading geometry after each write forces synchronous style/layout.
      const measured=entries.map(entry=>[heightProperties.get(entry.target),
        (entry.borderBoxSize?.[0]||entry.borderBoxSize)?.blockSize??entry.target.getBoundingClientRect().height]);
      for(const [property,height] of measured){const value=`${height}px`;if(root.style.getPropertyValue(property)!==value)root.style.setProperty(property,value);}
    });
    for(const node of heightProperties.keys())heights.observe(node);
    drawings=window.SpikeChartDrawings.create({root,onTool:()=>setIndicators(false)});
    chart=window.SpikeReclaimChart.create(el('chart'),text=>{el('chart-notice').textContent=text;el('chart-notice').hidden=!text;},drawings);
    sheet=window.SpikeYoloPreviewSheet.bind({panel:root.querySelector('.rp-records'),isOpen:()=>recordsOpen,dismiss:()=>setRecords(false)});
    closer=window.SpikeReclaimClose.create({root,onChanged:()=>{feed.refresh();watch?.refresh();analytics?.refresh();}});
    watch=window.SpikeReclaimWatchlist.create({root,createFeed,onScore:score=>{const node=el('symbol-score');node.hidden=!!replay||!score;node.classList.toggle('is-high',!!score?.high);node.querySelector('b').textContent=score?.text||'';node.title=score?.title||'';node.setAttribute('aria-label',score?.title||'');},getContext:()=>({track:feed.getState().data?.tracks[state.track],signals:feed.getState().data?.signals,symbol:chartSymbol,period:state.period}),onOpen:()=>{backtest?.setOpen(false,false);analytics?.setOpen(false,false);if(recordsOpen)setRecords(false,false);},onSelect:symbol=>{leaveBacktest(false);state.track=window.SpikeReclaimWatchlist.holdingTrack(feed.getState().data?.tracks,symbol,state.track,book.getOrder());state.symbol=symbol;state.trade='';shown=50;pinned=null;chart.resetFocus();sync();render(feed.getState());}});
    analytics=window.SpikeReclaimAnalytics.create({root,createFeed,onOpen:()=>{backtest?.setOpen(false,false);watch.setOpen(false,false);if(matchMedia('(max-width:700px)').matches)setRecords(false,false);},onTrack:selectTrack});
    backtest=window.SpikeStrategyBacktest.create({root,onOpen:()=>{watch?.setOpen(false,false);analytics?.setOpen(false,false);setRecords(false,false);quick?.close();},onSelect:value=>{
      if(!replay)liveState={...state};replay=value;state.track=value.track;state.trade='';state.symbol=value.row.symbol.replace(/USDT$/,'-USDT-SWAP');state.period=window.SpikeStrategyBacktest.periodFor(value.row,state.period);pinned=null;chart.resetFocus();render(feed.getState());
    }});
    book=window.SpikeStrategyBook.create({root,tracks:TRACKS,getTrack:()=>state.track,getSnapshot:()=>feed.getState().data,getPnl:t=>summary(feed.getState().data?.tracks?.[t],t).pnl,renderRules:rulesHTML,createFeed,onSelect:selectTrack,onBacktest:track=>{focus?.reset();backtest.open(track);}});
    el('period').addEventListener('change',event=>{state.period=event.target.value;sync();render(feed.getState());});
    quick=window.SpikeHomeTicket.create({root,context:()=>replay?null:({symbol:chartSymbol,timeframe:state.period})});
    focus=window.SpikeChartFocus.bind(root);
    root.addEventListener('keydown',event=>{if(event.key!=='Escape')return;if(root.querySelector('.rp-ledger-heading').classList.contains('is-searching')){event.stopPropagation();setSearch(false);}else if(indicatorsOpen){event.stopPropagation();setIndicators(false,true);}else if(recordsOpen)setRecords(false);});
    document.addEventListener('pointerdown',event=>{if(!focus?.hidden&&indicatorsOpen&&!event.target.closest('.rp-indicators'))setIndicators(false);},true);
    root.addEventListener('focusin',event=>{if(!focus?.hidden&&indicatorsOpen&&!event.target.closest('.rp-indicators'))setIndicators(false);});
    root.addEventListener('click',event=>{
      const record=event.target.closest('[data-rp-record]');
      if(record&&!event.target.closest('a,button,input,select,.rp-row-detail')){record.querySelector('.rp-row-select').click();return;}
      const button=event.target.closest('button');if(!button)return;
      if(button.dataset.rpDetail){const detail=document.getElementById(button.getAttribute('aria-controls'));detail.hidden=!detail.hidden;button.setAttribute('aria-expanded',String(!detail.hidden));return;}
      if(button.dataset.rpFavorite){preferences.toggle(button.dataset.rpFavorite);watch?.refreshPreferences();render(feed.getState());return;}
      if(button.dataset.rpSymbol){leaveBacktest(false);state.symbol=button.dataset.rpSymbol;state.trade='';pinned=null;chart.resetFocus();sync();render(feed.getState());if(matchMedia('(max-width:700px)').matches)setRecords(false);return;}

      if(button.dataset.rpClose){const data=feed.getState().data,p=data?.tracks[state.track]?.open.find(p=>String(p.id)===button.dataset.rpClose);if(p)closer.open({...p,margin_leverage:marginLeverage(data,state.track)});}
      if(button.dataset.rpTab){state.tab=button.dataset.rpTab;if(state.tab==='open')state.history='';shown=50;sync();render(feed.getState());}
      if(button.dataset.rpTrade){leaveBacktest(false);state.symbol='';state.trade=button.dataset.rpTrade;pinned=window.SpikeReclaimChart.select(feed.getState().data?.tracks[state.track],state.trade,history.getState().rows.find(p=>String(p.id)===state.trade));chart.resetFocus();sync();render(feed.getState());if(matchMedia('(max-width:700px)').matches)setRecords(false);}
      const action=button.dataset.rpAction;
      if(action==='history'||action==='today'){state.history=action==='today'||state.history?'':'1';if(state.history&&state.tab==='open')state.tab='closed';shown=50;sync();render(feed.getState());}
      if(action==='retry-history')history.load(history.getState().loaded);
      if(action==='search')setSearch(!root.querySelector('.rp-ledger-heading').classList.contains('is-searching'));
      if(action==='watch')watch.toggle();
      if(action==='analytics')analytics.toggle();
      if(action==='records')setRecords(!recordsOpen);
      if(action==='close-records')setRecords(false);
      if(action==='fit')chart.fit();if(action==='latest')chart.latest();
      if(action==='indicators')setIndicators(!indicatorsOpen);
      if(action==='ma'){ma=!ma;render(feed.getState());}
      if(action==='internal'){internal=!internal;render(feed.getState());}
      if(action==='backtest-list')backtest.setOpen(true);
      if(action==='backtest-exit'){leaveBacktest();sync();render(feed.getState());}
      if(action==='follow'){leaveBacktest(false);state.symbol='';state.trade='';pinned=null;chart.resetFocus();sync();render(feed.getState());}
      if(button.dataset.rpAction==='more'){if(state.history)history.load(true);else{shown+=50;render(feed.getState());}}
    });
    el('scope').addEventListener('change',event=>{state.scope=event.target.value;shown=50;sync();render(feed.getState());});
    el('sort').addEventListener('change',event=>{state.sort=recordSort(event.target.value);shown=50;root.querySelector('.rp-record-scroll').scrollTop=0;sync();render(feed.getState());});
    el('search').addEventListener('input',event=>{state.q=event.target.value;shown=50;sync();render(feed.getState());});
    root.addEventListener('error',event=>{if(event.target.tagName==='IMG')event.target.hidden=true;},true);
  }
  function leaveBacktest(restore=true){
    if(replay&&restore&&liveState)state={...liveState};replay=null;liveState=null;backtest?.setOpen(false,false);chart?.resetFocus();
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
  function setRecords(value,focus=true){if(value)backtest?.setOpen(false,false);if(value&&matchMedia('(max-width:700px)').matches)analytics?.setOpen(false,false);if(value)watch?.setOpen(false,false);sheet?.reset();recordsOpen=value;if(!value)history.select(null);const panel=root.querySelector('.rp-records');panel.classList.toggle('is-open',value);panel.inert=!value;panel.setAttribute('aria-hidden',String(!value));root.querySelector('[data-rp-action="records"]').setAttribute('aria-expanded',String(value));if(value)render(feed.getState());else if(focus)root.querySelector('[data-rp-action="records"]').focus({preventScroll:true});}
  function sync(){window.SpikeNavigation?.sync('signals',{pane:'reclaim',...state});}
  function replace(id,html){const node=el(id);if(node.innerHTML!==html)node.innerHTML=html;}
  function render(current){
    if(!pageActive)return;mount();
    const {data,error,busy}=current, track=data?.tracks?.[state.track], stats=summary(track,state.track);
    root.setAttribute('aria-busy',String(busy && !data));
    el('account-label').textContent=trackLabel(state.track)+' 模拟';el('ledger-label').textContent=trackLabel(state.track);
    root.querySelector('.rp-records').setAttribute('aria-label',trackLabel(state.track)+' 模拟交易簿');
    const missingTrack=data&&!track;
    const worker=data?.worker, workerIssue=worker && (worker.alive!==true || worker.error);
    el('status').textContent=data ? `${error?'上次记录':'更新于'} ${date(data.now_ms)} · 北京时间 · ${workerIssue?'后台需要检查':busy?'同步中…':'每 10 秒同步'}${worker?.queue>0?` · 等待处理 ${worker.queue} 条`:''}` : busy?'正在读取模拟盘…':'尚未读取到模拟盘数据';
    el('error').hidden=!error && !workerIssue && !missingTrack;
    el('error').textContent=error ? `同步失败：${error}。${data?'保留上次记录，正在自动重试。':'正在自动重试。'}` : workerIssue ? `后台状态异常：${worker.error || '处理线程未运行'}。当前展示已保存的记录。` : missingTrack ? `${trackLabel(state.track)} 策略暂未返回，无法显示该策略数据。` : '';
    book?.sync();
    root.querySelectorAll('[data-rp-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.rpTab===state.tab)));
    if(el('search').value!==state.q)el('search').value=state.q;
    root.querySelector('[data-rp-action="search"]').classList.toggle('has-query',!!state.q);
    root.querySelector('[data-rp-action="history"]').setAttribute('aria-pressed',String(!!state.history));
    root.querySelector('[data-rp-action="history"]').setAttribute('aria-label',state.history?'返回今日记录':'历史记录');
    root.querySelector('[data-rp-action="today"]').hidden=!state.history;
    el('list-period').textContent=state.history?'历史 · 今日之前':state.tab==='open'?'当前持仓':'今日 · 北京时间';
    el('scope').value=state.scope;el('scope-label').hidden=state.tab==='events';
    el('sort').value=state.sort;
    el('sort').parentElement.title='排序 · '+SORTS[state.sort];el('sort').parentElement.dataset.time=String(state.sort!=='score');
    const selected=replay?null:state.symbol&&!state.trade?window.SpikeReclaimWatchlist.coinTrade(track,state.symbol):window.SpikeReclaimChart.select(track,state.trade,pinned||history.getState().rows.find(p=>String(p.id)===state.trade));if(state.trade&&selected)pinned=selected;
    chartSymbol=state.symbol||selected?.symbol||(state.trade?'':'BTC-USDT-SWAP');quick?.refresh();
    el('backtest-mode').hidden=!replay;root.classList.toggle('is-backtest',!!replay);if(replay)root.querySelector('[data-rp-action="backtest-list"]').textContent=trackLabel(replay.track)+' · 回测交易';
    analytics?.setTrack(state.track);
    if(replay)chart.showReplay(replay.track,replay.row,state.period,ma,internal);else chart.show(selected?{...selected,asof_ms:data.now_ms}:null,state.period,ma,chartSymbol,internal,[...(track?.open||[]),...(track?.closed||[]),...(pinned&&!track?.closed?.some(p=>p.id===pinned.id)?[pinned]:[])].filter(p=>funded(p)&&p.symbol===chartSymbol));watch?.render();if(replay)el('symbol-score').hidden=true;el('period').value=state.period;
    if(!selected&&state.trade){el('chart-notice').textContent='该交易不在当前返回记录中，请点击跟随最新单。';el('chart-notice').hidden=false;}
    root.querySelector('[data-rp-action="ma"]').setAttribute('aria-checked',String(ma));
    root.querySelector('[data-rp-action="internal"]').setAttribute('aria-checked',String(internal));
    el('indicator-count').textContent=String(Number(ma)+Number(internal));el('indicator-count').hidden=!ma&&!internal;
    root.querySelector('[data-rp-action="follow"]').setAttribute('aria-pressed',String(!replay&&!state.trade&&!state.symbol));
    replace('symbol-label',chartSymbol?`${esc(base(chartSymbol))}<small> / USDT</small>`:'选择币种');
    replace('selection-status',replay?`<span class="${tone(replay.row.pnl)}">历史回测 · ${sideLabel(replay.row)} · ${signed(replay.row.pnl)} USDT</span>`:selected?`<span class="${selected.side===1?'rp-positive':'rp-negative'}">${sideLabel(selected)} · ${esc(stage(selected,data.now_ms))}</span>`:'');
    el('selection-note').textContent=replay?`Binance · ${window.SpikeStrategyBacktest.entryLabel(replay.row)} 开仓区间`:selected?`${state.trade?'固定交易':state.symbol?'固定币种':'最新资金交易'} · ${date(selected.fill_ms)} 开仓`:chartSymbol?(state.symbol?'固定币种 · 最近记录无资金成交':'默认行情 · 暂无资金成交'):'等待资金交易 · 点击币种查看触价列表';
    root.querySelector('.rp-chart-foot').textContent=replay?'历史回测 · 标线为原成交价及原周期成交区间':'逐周 1.618 区间 · 开平仓按实际成交标记';
    el('watermark').textContent=chartSymbol?base(chartSymbol)+' / USDT':'';
    el('total').textContent=track?String(track.open.filter(funded).length):'—';
    const allin=state.track==='allin3';
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
    const held=data?heldSignals(data):new Set(),archive=!!state.history;
    history.select(archive&&data&&!document.hidden?{track:state.track,tab:state.tab,scope:state.scope,sort:state.sort,q:state.q.trim(),day:dayStart(data.now_ms),held:Array.from(held).sort().join(',')}:null);
    const past=history.getState();
    for(const tab of ['open','closed','events'])el(`count-${tab}`).textContent=archive&&tab!=='open'?(tab===state.tab&&past.loaded?String(past.total):''):data?String(rows(data,{...state,tab}).length):'—';
    const items=archive?past.rows:data?rows(data,state):[],visible=archive?items:items.slice(0,shown);
    el('history-error').hidden=!archive||!past.error;
    el('history-error').textContent=past.error?`历史读取失败：${past.error}。${past.loaded?'已保留加载的记录。':''}`:'';
    root.querySelector('[data-rp-action="retry-history"]').hidden=!archive||!past.error;
    el('caption').textContent=archive?(past.busy?'正在读取历史…':past.loaded?`历史${state.tab==='events'?'触价':'已结束'} · ${SORTS[state.sort]} · 已显示 ${items.length} / ${past.total} 条`:'历史记录待读取'):!data?(error?'记录暂时不可用':'正在读取记录…'):`${state.tab==='open'?'当前持仓':state.tab==='events'?'今日触价 · 含跨日持仓':'今日已结束'} · ${SORTS[state.sort]} · ${items.length} 条`;
    preferences?.read();
    const rowSignature=visible.length ? visible.map(row=>rowHTML(row,{...state,v3:data.v3,trade:String(selected?.id||state.trade),symbol:chartSymbol},data.now_ms,{favorite:preferences?.has(row.symbol),platform,carried:state.tab==='events'&&row.candle_ms<dayStart(data.now_ms)&&held.has(row.id)})).join('')
      : `<div class="rp-empty"><span aria-hidden="true">${data?'◌':'⋯'}</span><strong>${archive?(past.busy?'正在读取历史':past.error?'历史暂时无法读取':state.q?'没有匹配的历史记录':'暂无历史记录'):!data?(error?'暂时无法读取':'正在读取模拟盘'):missingTrack?'该策略暂不可用':state.q?'没有匹配的币种':state.tab==='open'?'当前没有持仓':state.tab==='closed'?'当前没有已结束记录':'还没有触价记录'}</strong><p>${data?(state.q?'换一个币种或清空搜索。':state.scope==='shadow'&&state.tab!=='events'?'未计入资金的模拟交易会出现在这里。':'后台产生新记录后会自动更新。'):'请稍候，数据会自动重试同步。'}</p></div>`;
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
    el('record-note').textContent=state.tab==='events' ? '共用触价记录 · 评分结果为 v2 门槛；评分 > 0.5 高亮，非胜率。各策略是否入场请看其持仓与已结束记录。' : state.tab==='open' ? '参考浮动收益未扣费；策略净值包含已减仓盈亏，不包含余仓浮盈。' : '净收益已扣手续费；滑点情景仅供对照，不重复扣入策略净值。';
  }
  document.addEventListener('visibilitychange',()=>{backtest?.setActive(pageActive&&!document.hidden);if(document.hidden)history.select(null);feed.setActive(pageActive && !document.hidden);chart?.setActive(pageActive && !document.hidden);watch?.setActive(pageActive && !document.hidden);analytics?.setActive(pageActive && !document.hidden);});
  window.SpikeReclaimPaper={
    setActive(value){pageActive=value;root.hidden=!value;if(value)mount();book?.setActive(value);backtest?.setActive(value);if(!value){history.select(null);focus?.reset();quick?.close();setIndicators(false);sheet?.reset();closer?.hide();}chart?.setActive(value && !document.hidden);if(value)render(feed.getState());feed.setActive(value && !document.hidden);watch?.setActive(value && !document.hidden);analytics?.setActive(value && !document.hidden);},
    refresh:()=>{watch?.refresh();analytics?.refresh();return feed.refresh();},route:()=>({...state}),
    restore(params){leaveBacktest(false);const next=route(params);if(next.track!==state.track||next.trade!==state.trade||next.symbol!==state.symbol)pinned=null;state=next;shown=50;render(feed.getState());}
  };
})();
