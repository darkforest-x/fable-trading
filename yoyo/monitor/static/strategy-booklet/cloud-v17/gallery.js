import {read, readTrade, historyAsset, amount, price, signed, beijing, filterTrades, actionNames} from './data.js';

const $ = id => document.getElementById(id);
export const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tradeHref = id => '#history?trade=' + encodeURIComponent(id);
const side = r => r.side === 1 ? '多' : '空';
const pnl = n => '<b class="' + (n > 0 ? 'green' : n < 0 ? 'red' : '') + '">' + signed(n) + ' U</b>';
const pageSize = 20;
let manifest, matches = [], current, page = 0, selection = 0, restoring = false, navigation = 0;
const form = $('filters');
const options = () => Object.fromEntries(new FormData(form));
const fact = (label, value) => '<span>' + label + ' <b>' + esc(value) + '</b></span>';
export const table = (head, rows) => '<div class="table-wrap"><table><thead><tr>' +
  head.map(c => '<th scope="col">' + esc(c) + '</th>').join('') + '</tr></thead><tbody>' +
  rows.map(r => '<tr>' + r.map(c => '<td>' + c + '</td>').join('') + '</tr>').join('') + '</tbody></table></div>';

function updateAddress() {
  const qs = new URLSearchParams(Object.entries(options()).filter(([k,v]) => v && !(k === 'order' && v === 'recent')));
  if (current) qs.set('trade', current.id);
  history.replaceState(null, '', '#history' + (qs.size ? '?' + qs : ''));
}
function renderList() {
  const pages = Math.ceil(matches.length/pageSize);
  $('pages').innerHTML = '<button data-page="-1" aria-label="上一页"' + (!page?' disabled':'') + '>‹</button>' +
    (page+1) + ' / ' + pages + '<button data-page="1" aria-label="下一页"' + (page+1===pages?' disabled':'') + '>›</button>';
  $('list').innerHTML = table(['合约 / 方向','开仓（北京）','入场价','净盈亏','跟随仓退出'], matches.slice(page*pageSize,(page+1)*pageSize).map(r => [
    '<a data-trade="' + esc(r.id) + '" href="' + tradeHref(r.id) + '">' + esc(r.symbol) + ' · ' + side(r) + '</a>',
    beijing(r.entryMs), price(r.entryPrice), pnl(r.netPnl), esc(r.exitReasonLabel)
  ]));
  $('list').querySelector('[data-trade="' + current.id + '"]')?.closest('tr')?.classList.add('selected');
}

function renderTrade(index) {
  selection++;
  current = matches[index]; page = Math.floor(index/pageSize);
  const r = current, audit = r.chartAudit;
  const chart = historyAsset(r.chartUrl);
  $('trade').innerHTML = '<div class="bar"><h3>' + esc(r.symbol) + ' · ' + side(r) + '单　' + pnl(r.netPnl) + '</h3>' +
    '<div class="pager"><button data-step="-1"' + (index===0?' disabled':'') + '>上一笔</button>' +
    (index+1) + ' / ' + matches.length + '<button data-step="1"' + (index+1===matches.length?' disabled':'') + '>下一笔</button></div></div>' +
    '<div class="facts">' + fact('开仓',beijing(r.entryMs)) + fact('入场价',price(r.entryPrice)) + fact('名义金额',amount(r.notional)+' U') +
    fact('手续费',amount(r.fees)+' U') + '</div>' +
    '<a href="' + chart + '" target="_blank" rel="noopener" aria-label="查看原尺寸K线图"><img class="trade-chart" src="' + chart +
    '" width="1280" height="560" alt="' + esc(r.symbol) + ' 12H 开仓图，含云带、周轨和退出标记"></a>' +
    '<div class="bar detail-links"><span>' + audit.availableBars + ' 根 12H K 线 · 北京时间 · 蓝线 EMA20，金线 EMA50，紫色虚线为当周外轨，紫点为敞口减仓' +
    (audit.missingBars ? ' · 图窗缺 ' + audit.missingBars + ' 根，缺口留白' : '') + '</span><a href="' + chart +
    '" target="_blank" rel="noopener">放大原图 ↗</a></div><div class="facts">' +
    fact('跟随仓退出所在小时',beijing(r.exitMs)) + fact('退出价',price(r.exitPrice)) + fact('退出原因',r.exitReasonLabel) + '</div>' +
    '<details id="receipt"><summary>开仓依据与全部 ' + r.nativeOrderCount + ' 条子仓成交</summary><div id="receipt-body"></div></details>';
  const rev = selection;
  $('receipt').addEventListener('toggle', async e => {
    if (!e.target.open || e.target.dataset.loaded) return;
    const box = $('receipt-body'); box.textContent = '正在读取成交证据…'; e.target.dataset.loaded = '1';
    try {
      const d = await readTrade(r);
      if (rev !== selection) return;
      const ev = d.entryEvidence;
      box.innerHTML = (ev ? evidenceHTML(ev) : '<div class="facts">' + fact('触及侧 1.618',price(d.mondayRail.level)) + '</div>') +
        '<p class="muted">盘中成交只保留所属 1H 区间。流水的现金变动含开平本金，逐行求和为整笔扣费净盈亏。</p>' +
        table(['小时起点（北京）','子仓','动作','成交价','手续费 U','现金变动 U'],d.fills.map(f=>[
          beijing(f.hourMs) + (f.phase==='intrabar'?' · 盘中':' · 开盘'),
          String(f.childSlot%4+1),esc(actionNames[f.action] || f.action),price(f.fillPrice),amount(f.fees,4),signed(f.cashPnl)
        ]));
    } catch (error) { if(rev===selection){box.textContent='读取失败，请收起后重试。'; delete $('receipt').dataset.loaded;} }
  });
  renderList();
}

function evidenceHTML(ev) {
  const bar=ev.signalOhlc || {}, eth=ev.benchmark30d?.ETH, btc=ev.benchmark30d?.BTC;
  const relative=eth?.available && btc?.available ? 100*((eth.endClose/eth.startClose)/(btc.endClose/btc.startClose)-1) : null;
  const values=[['信号最低',bar.low],['信号最高',bar.high],['确认收盘',bar.close],['EMA20',ev.ema?.ema20],['EMA50',ev.ema?.ema50],
    ['下轨',ev.rails?.lowerPrice],['上轨',ev.rails?.upperPrice],['固定止损',ev.fixedStopPrice],['2R 目标',ev.twoRPrice]];
  return '<div class="facts">'+values.filter(([,v])=>Number.isFinite(v)).map(([k,v])=>fact(k,price(v))).join('')+
    (Number.isFinite(relative)?fact('ETH/BTC 30 天',signed(relative)+'%'):'')+'</div>';
}

function filter(id, deep = false) {
  matches = filterTrades(manifest.records, options());
  const selected = id ? matches.findIndex(r=>r.id===id) : 0;
  $('gallery-state').textContent = '共 ' + manifest.records.length.toLocaleString('en-US') + ' 笔，当前匹配 ' +
    matches.length.toLocaleString('en-US') + ' 笔。' + (!matches.length?'可以清除条件或调整日期。':deep && selected<0?'该交易编号与当前条件不匹配。':'');
  $('trade').hidden = $('list-panel').hidden = !matches.length;
  current = null; selection++;
  if(matches.length) renderTrade(Math.max(0,selected));
}
export async function gallery(params) {
  const request = ++navigation;
  try {
    manifest ||= await read(historyAsset('manifest.json'));
    if (manifest.records.length !== manifest.tradeCount || manifest.tradeCount !== 1252) throw Error('交易目录计数不符');
    if (request !== navigation || !location.hash.startsWith('#history')) return;
    restoring = true;
    form.reset();
    restoring = false;
    for (const key of ['query','from','until','side','outcome','order']) if(params.has(key)) form.elements[key].value=params.get(key);
    filter(params.get('trade'), true);
  } catch(error) { $('gallery-state').textContent='无法载入历史目录。'+error.message; }
}
form.addEventListener('submit', e => e.preventDefault());
form.addEventListener('input', () => { if(manifest){filter();updateAddress();} });
form.addEventListener('reset', () => {
  if(restoring) return;
  setTimeout(()=>{if(manifest){filter();updateAddress();}},0);
});
$('history').addEventListener('click', e=>{
  const b=e.target.closest('[data-step],[data-page],[data-trade]');
  if(!b || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  let target;
  if(b.dataset.step) target=matches.indexOf(current)+Number(b.dataset.step);
  else if(b.dataset.page) target=(page+Number(b.dataset.page))*pageSize;
  else target=matches.findIndex(r=>r.id===b.dataset.trade);
  if(!matches[target]) return;
  renderTrade(target); updateAddress();
  if(b.dataset.trade) $('trade').scrollIntoView({block:'start'});
});
