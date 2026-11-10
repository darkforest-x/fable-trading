import {amount, price, signed, beijing, escapeHTML as esc, tableHTML} from './data.js';
export {mountGallery} from './gallery.js';

const statusText = {
  active: '运行中', running: '运行中', watching: '等待下一次收盘', idle: '等待下一次收盘', healthy: '运行中',
  ready: '监控中', waiting: '等待行情', warming: '正在同步行情', starting: '正在启动', degraded: '行情或数据待恢复',
  error: '需要检查', stopped: '已停止', processing: '处理中', queued: '待判定', opened: '已开仓',
  accepted: '已接纳', skipped: '跳过', rejected: '未通过', missed: '错过入场', missed_entry: '错过入场',
  closed: '已平仓', open: '持仓'
};
const reasons = {
  inverse_regime_direction_gate: 'ETH/BTC 30 天方向不符', exact_eth_btc_regime_tie: 'ETH/BTC 30 天持平',
  missing_contiguous_eth_btc_61_close_window: 'ETH 或 BTC 的 30 天数据有缺口', missing_preceding_12h_atr14: '缺前一根已收盘 ATR',
  same_time_rankable_universe_below_10: '同一时钟满足历史覆盖的合约少于 10 个', same_contract_busy: '同币已有持仓',
  all_slots_busy: '9 个资金槽已满', insufficient_free_cash: '可用保证金不足', entry_beyond_signal_stop: '最新入场价已越过止损',
  not_first_eligible_signal_for_symbol_week: '该币本周已有更早的合格信号',
  source_clock_ahead_of_runner: '信号时钟快于本地校准时钟', source_clock_before_confirmed_close: '信号时钟早于收盘',
  signal_batch_expired: '已错过 15 分钟收集窗口', signal_batch_expired_during_quote_collection: '等待新报价时超时',
  source_scan_incomplete_at_deadline: '收集窗口结束时仍有合约未扫描', missing_public_ticker_quote: '缺少有效公开买卖报价',
  missing_or_invalid_public_quote: '公开报价缺失或无效', admission_unavailable_at_deadline: '入场时限已过，行情或账户估值未就绪',
  ticker_stale_or_predates_confirmation: '报价已过期或早于信号确认'
};

const stateName = key => statusText[key] || String(key || '尚无状态');
const pnl = value => '<b class="rs-cloud-pnl' + (value > 0 ? ' is-positive' : value < 0 ? ' is-negative' : '') + '">' + signed(value) + ' U</b>';
const fact = (label, value) => '<div><small>' + esc(label) + '</small><b>' + esc(value) + '</b></div>';

function positions(rows, closed = false) {
  const data = Array.isArray(rows) ? rows : [];
  if (!data.length) return '<p class="rs-muted">' + (closed ? '尚无启用后结清的交易。' : '当前空仓。') + '</p>';
  return tableHTML(['合约 / 方向', '开仓（北京）', '模拟入场价', closed ? '退出价' : '止损价', closed ? '净盈亏 U' : '最新估值价', '收盘到入场'], data.map(row => [
    esc(row.symbol) + ' · ' + (row.side === 1 ? '多' : '空'),
    esc(beijing(row.entry_ms)), esc(price(row.entry_price)), esc(price(closed ? row.exit_price : row.stop_price)),
    closed ? pnl(row.realized_pnl) : esc(price(row.mark_price)),
    esc(amount((row.entry_ms - row.confirmed_ms) / 1000, 1) + ' 秒')
  ]), 'rs-cloud-forward-table');
}

function panel(title, content, extra = '') {
  return '<section class="rs-cloud-forward-panel"><h3>' + esc(title) + '</h3>' + content + extra + '</section>';
}

export function renderForward(container, snapshot) {
  if (!container?.replaceChildren) throw new TypeError('renderForward 需要有效的容器元素');
  const d = snapshot || {};
  const a = d.account || {};
  if (d.strategy_id !== 'live-cloud-v17' || d.mode !== 'paper' || d.schema_version !== 'live-cloud-v17.v1' || !Number.isFinite(a.equity)) {
    container.innerHTML = '<div class="rs-cloud-forward"><p class="rs-warning">前向模拟账本快照暂不可用或格式不匹配。可刷新后重试。</p></div>';
    return false;
  }

  const market = d.market || {};
  const start = d.started_ms || a.started_ms;
  const stale = !Number.isFinite(d.heartbeat_ms) || Date.now() - d.heartbeat_ms > 150000;
  const status = stale ? '心跳已过期' : stateName(d.phase);
  const decisions = Array.isArray(d.recent_decisions) ? d.recent_decisions : [];
  const closed = (Array.isArray(d.recent_trades) ? d.recent_trades : []).filter(row => row.status === 'closed');
  const quotes = market.last_signal_close_ms || market.boundary_ms || market.source_boundary_ms;
  const count = market.rankable_count;
  const metric = (label, value, note, tone = '') => '<div><small>' + esc(label) + '</small><strong' + (tone ? ' class="' + tone + '"' : '') + '>' +
    esc(value) + '</strong><span>' + esc(note) + '</span></div>';

  container.innerHTML = '<div class="rs-cloud-forward">' +
    '<div class="rs-forward-metrics">' + [
      metric('前向净值', amount(a.equity) + ' U', amount(a.start_equity, 0) + 'U 独立起步'),
      metric('已实现盈亏', signed(a.realized_pnl) + ' U', '含已计手续费', a.realized_pnl > 0 ? 'is-positive' : a.realized_pnl < 0 ? 'is-negative' : ''),
      metric('持仓浮盈亏', signed(a.unrealized_pnl) + ' U', '按最近可得行情估值', a.unrealized_pnl > 0 ? 'is-positive' : a.unrealized_pnl < 0 ? 'is-negative' : ''),
      metric('已用仓位', (Number.isFinite(a.open_position_count) ? a.open_position_count : '—') + ' / ' +
        (Number.isFinite(a.slot_count) ? a.slot_count : '—'), '每币最多一仓')
    ].join('') + '</div>' +
    '<section class="rs-cloud-forward-panel"><div class="rs-cloud-forward-title"><h3>运行记录</h3>' +
    '<span class="rs-cloud-status' + (stale ? ' is-stale' : ' is-live') + '">' + esc(status) + '</span></div>' +
    '<div class="rs-facts rs-cloud-forward-facts">' + fact('账户启用', beijing(start)) + fact('最近心跳', beijing(d.heartbeat_ms)) +
    (quotes ? fact('行情确认时钟', beijing(quotes)) : '') + (Number.isFinite(count) ? fact('覆盖 90 天的合约', String(count)) : '') +
    (market.next_eligible_close_ms ? fact('下一次可判定收盘', beijing(market.next_eligible_close_ms)) : '') + '</div>' +
    (d.error ? '<p class="rs-warning">' + esc(d.error) + '</p>' : '') +
    '<p>北京时间 08:00、20:00 收盘后检查信号。前向采用当时可观察的公开买卖报价，买入取卖一、卖出取买一。超时信号记作错过。</p></section>' +
    panel('当前持仓', positions(d.positions)) + panel('最近结清', positions(closed, true)) +
    panel('最近信号判定', decisions.length ? tableHTML(['合约', '确认（北京）', '判定', '原因'], decisions.map(row => [
      esc(row.symbol), esc(beijing(row.confirmed_ms || row.boundary_ms)), esc(stateName(row.status)),
      esc(reasons[row.reason] || row.reason || '—')
    ]), 'rs-cloud-forward-table') : '<p class="rs-muted">尚无启用后记录的信号判定。</p>') +
    '</div>';
  return true;
}
