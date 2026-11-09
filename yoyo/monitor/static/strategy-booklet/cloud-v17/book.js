import {read, amount, price, signed, beijing} from './data.js';
import {gallery, esc, table} from './gallery.js';
const $ = id => document.getElementById(id);
const chapters = ['overview','rules','history','forward'];
const metric = (title,value,note,tone='') => '<div class="metric"><small>'+esc(title)+'</small><strong class="'+tone+'">'+esc(value)+'</strong><span>'+esc(note)+'</span></div>';
let timer, overviewReady=false, rulesReady=false, forwardSequence=0;

async function overview() {
  if(overviewReady) return;
  try {
    const d=await read('overview.json'), m=d.metrics;
    $('historical-metrics').innerHTML=[
      metric('历史终值',amount(m.final_usdt,0)+' U','1,000U 起步','green'),
      metric('资金倍数',amount(m.multiple)+'×','目标 > 1,000 倍'),
      metric('金额盈亏比',amount(m.payoff_usdt), '平均盈利 / 平均亏损'),
      metric('胜率',amount(m.win_pct)+'%',d.winners+' 胜 · '+d.losers+' 负'),
      metric('最大账面回撤',amount(m.dd_mark_pct)+'%', '上界 '+amount(m.dd_bound_pct)+'%','red'),
      metric('实际资金单',amount(m.trades,0),d.symbols+' 个币种')
    ].join('');
    $('years').innerHTML=table(['年份','期末净值（U）','本段净收益率'],d.years.map((y,i)=>[
      String(y.year)+(i===0?' · 1 月 24 日起':i===d.years.length-1?' · 至 11 月 8 日':''),
      amount(y.end),signed(y.pct)+'%'
    ]));
    overviewReady=true;
  } catch(error) { $('historical-metrics').textContent='历史摘要读取失败，请刷新重试。'; }
}
async function rules() {
  if(rulesReady) return;
  try{
    const res=await fetch('rules.html'); if(!res.ok) throw Error();
    $('rule-text').innerHTML=await res.text(); rulesReady=true;
  } catch { $('rule-text').textContent='规则暂时无法载入，可下载本页文字版。'; }
}
const statusText = {
  active:'运行中', running:'运行中', watching:'等待下一次收盘', idle:'等待下一次收盘', healthy:'运行中',
  ready:'监控中', waiting:'等待行情', warming:'正在同步行情', starting:'正在启动', degraded:'行情或数据待恢复',
  error:'需要检查', stopped:'已停止', processing:'处理中', queued:'待判定', opened:'已开仓',
  accepted:'已接纳', skipped:'跳过', rejected:'未通过', missed:'错过入场', missed_entry:'错过入场',
  closed:'已平仓', open:'持仓'
};
const stateName = key => statusText[key] || String(key || '尚无状态');
const reasons = {
  inverse_regime_direction_gate:'ETH/BTC 30 天方向不符',exact_eth_btc_regime_tie:'ETH/BTC 30 天持平',
  missing_contiguous_eth_btc_61_close_window:'ETH 或 BTC 的 30 天数据有缺口',missing_preceding_12h_atr14:'缺前一根已收盘 ATR',
  same_time_rankable_universe_below_10:'同一时钟满足历史覆盖的合约少于 10 个',same_contract_busy:'同币已有持仓',
  all_slots_busy:'9 个资金槽已满',insufficient_free_cash:'可用保证金不足',entry_beyond_signal_stop:'最新入场价已越过止损',
  not_first_eligible_signal_for_symbol_week:'该币本周已有更早的合格信号',
  source_clock_ahead_of_runner:'信号时钟快于本地校准时钟',source_clock_before_confirmed_close:'信号时钟早于收盘',
  signal_batch_expired:'已错过 15 分钟收集窗口',signal_batch_expired_during_quote_collection:'等待新报价时超时',
  source_scan_incomplete_at_deadline:'收集窗口结束时仍有合约未扫描',missing_public_ticker_quote:'缺少有效公开买卖报价',
  missing_or_invalid_public_quote:'公开报价缺失或无效',admission_unavailable_at_deadline:'入场时限已过，行情或账户估值未就绪',
  ticker_stale_or_predates_confirmation:'报价已过期或早于信号确认'
};
function positions(rows,closed=false) {
  if(!rows.length) return '<p class="muted">'+(closed?'尚无启用后结清的交易。':'当前空仓。')+'</p>';
  return table(['合约 / 方向','开仓（北京）','模拟入场价',closed?'退出价':'止损价',closed?'净盈亏 U':'最新估值价','收盘到入场'],rows.map(r=>[
    esc(r.symbol)+' · '+(r.side===1?'多':'空'),beijing(r.entry_ms),price(r.entry_price),price(closed?r.exit_price:r.stop_price),
    closed?signed(r.realized_pnl):price(r.mark_price),amount((r.entry_ms-r.confirmed_ms)/1000,1)+' 秒'
  ]));
}
async function forward() {
  const ticket=++forwardSequence;
  try {
    const d=await read('../live-cloud-v17-forward.json',{cache:'no-store'});
    if(ticket!==forwardSequence) return;
    if(d.strategy_id!=='live-cloud-v17'||d.mode!=='paper'||d.schema_version!=='live-cloud-v17.v1'||!Number.isFinite(d.account?.equity)) throw Error('账户快照暂不可用');
    const a=d.account, market=d.market || {}, start=d.started_ms || a.started_ms;
    const stale=Date.now()-d.heartbeat_ms > 150000 || !Number.isFinite(d.heartbeat_ms);
    const status=stale?'心跳已过期':stateName(d.phase);
    const decisions=d.recent_decisions || [];
    const closed=(d.recent_trades || []).filter(r=>r.status==='closed');
    const quotes=market.last_signal_close_ms || market.boundary_ms || market.source_boundary_ms;
    const count=market.rankable_count;
    $('forward-body').innerHTML='<div class="metrics">'+[
      metric('前向净值',amount(a.equity)+' U',amount(a.start_equity,0)+'U 独立起步'),
      metric('已实现盈亏',signed(a.realized_pnl)+' U','含已计手续费'),
      metric('持仓浮盈亏',signed(a.unrealized_pnl)+' U','按最近可得行情估值'),
      metric('已用仓位',a.open_position_count+' / '+a.slot_count,'每币最多一仓')
    ].join('')+'</div><div class="panel"><div class="bar"><h3>运行记录</h3><span>'+esc(status)+'</span></div>' +
    '<div class="facts"><span>账户启用 <b>'+beijing(start)+'</b></span><span>最近心跳 <b>'+beijing(d.heartbeat_ms)+'</b></span>'+
    (quotes?'<span>行情确认时钟 <b>'+beijing(quotes)+'</b></span>':'')+
    (Number.isFinite(count)?'<span>覆盖 90 天的合约 <b>'+count+'</b></span>':'')+
    (market.next_eligible_close_ms?'<span>下一次可判定收盘 <b>'+beijing(market.next_eligible_close_ms)+'</b></span>':'')+'</div>'+
    (d.error?'<p class="state-error">'+esc(d.error)+'</p>':'')+
    '<p>北京时间 08:00、20:00 收盘后检查信号。前向采用当时可观察的公开买卖报价，买入取卖一、卖出取买一。超时信号记作错过。</p></div>'+
    '<div class="panel"><h3>当前持仓</h3>'+positions(d.positions || [])+'</div>'+
    '<div class="panel"><h3>最近结清</h3>'+positions(closed,true)+'</div>'+
    '<div class="panel"><h3>最近信号判定</h3>'+(decisions.length?table(['合约','确认（北京）','判定','原因'],decisions.map(r=>[
      esc(r.symbol),beijing(r.confirmed_ms || r.boundary_ms),esc(stateName(r.status)),esc(reasons[r.reason] || r.reason || '—')
    ])):'<p class="muted">尚无启用后记录的信号判定。</p>')+'</div>';
  } catch(error) {
    if(ticket===forwardSequence) $('forward-body').innerHTML='<p class="panel state-error">暂时无法取得前向账本。可点刷新重试。'+esc(error.message)+'</p>';
  }
}
function route() {
  const [requested, query] = location.hash.slice(1).split('?');
  const chapter=chapters.includes(requested)?requested:'overview';
  for(const name of chapters) $(name).hidden=name!==chapter;
  for(const link of document.querySelectorAll('nav a')) {
    if(link.hash==='#'+chapter) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  }
  clearInterval(timer); forwardSequence++;
  document.title=document.querySelector('nav [aria-current]').textContent+' · 云带 v1.7 策略小册';
  if(chapter==='overview') void overview();
  if(chapter==='rules') void rules();
  if(chapter==='history') void gallery(new URLSearchParams(query || ''));
  if(chapter==='forward') { void forward(); timer=setInterval(()=>{if(!document.hidden)void forward();},30000); }
  window.scrollTo(0,0);
}
$('refresh').addEventListener('click',forward);
window.addEventListener('hashchange',route);
window.addEventListener('pagehide',()=>clearInterval(timer));
window.addEventListener('pageshow',e=>{if(e.persisted)route();});
route();
