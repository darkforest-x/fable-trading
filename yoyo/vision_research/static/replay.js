// Use the unified workspace module scope when mounted; keep standalone tests pure.
const document = typeof window !== "undefined" && window.SpikeVision ? window.SpikeVision.document : globalThis.document;
/* Historical replay UI. All chart rows and frozen images come from the replay API. */
const API_BASE = typeof window !== 'undefined' && window.SpikeVision ? '/api/vision' : '/api';
const REPLAY_BASE = `${API_BASE}/replay`;
const UTC8_OFFSET_MS = 8 * 60 * 60 * 1000;
const MA_KEYS = ['sma20', 'ema20', 'sma60', 'ema60', 'sma120', 'ema120'];
const STATE_LABELS = {
  converging: '仍在收敛', launching: '正在启动', extended: '已经延伸', no_setup: '没有形态', unclear: '不确定',
};
const VERDICT_LABELS = { match: '符合', no_match: '不符合', uncertain: '不确定' };
const SIDE_LABELS = { long: '多', short: '空', unknown: '未知 / 不适用' };
const CASE_BUCKET_LABELS = {
  all: '全部（混合盲审）', ge3: '净收益 ≥3R', ge5: '净收益 ≥5R', gt10: '净收益 >10R',
  loss: '净亏损', other: '其他已结束', open: '未结束',
};
const CASE_COUNT_KEYS = ['all', 'ge3', 'ge5', 'gt10', 'loss', 'other', 'open'];
const OUTCOME_STATUS_LABELS = {
  closed: '已结束', censored: '观察期结束仍未平仓（删失）', open: '尚未结束',
};
const EXIT_REASON_LABELS = {
  initial_stop: '初始止损', trailing_stop: '追踪止损',
  initial_stop_gap: '初始止损跳空', trailing_stop_gap: '追踪止损跳空',
  opposite_v6_next_open: 'V6 反向信号后次开盘退出',
  take_profit: '止盈', target: '止盈', time_exit: '时间退出', timeout: '超时退出',
  boundary_mark: '样本边界标记', data_gap_censored: '数据缺口删失',
};
const COMPARISON_MODES = ['vision', 'text', 'hybrid'];
const COMPARISON_MODE_LABELS = { vision: '纯图像', text: '纯数值', hybrid: '图像 + 数值' };
const COMPARISON_STATUS_LABELS = {
  pending: '待调用', running: '调用中', completed: '已完成', failed: '失败', interrupted: '已中断',
};

export function shanghaiInputToMs(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(String(value || ''));
  if (!match) return null;
  const [, year, month, day, hour, minute] = match.map(Number);
  const valueMs = Date.UTC(year, month - 1, day, hour, minute) - UTC8_OFFSET_MS;
  const roundTrip = new Date(valueMs + UTC8_OFFSET_MS).toISOString().slice(0, 16);
  return roundTrip === value ? valueMs : null;
}

export function msToShanghaiInput(value) {
  const time = Number(value);
  return Number.isFinite(time) ? new Date(time + UTC8_OFFSET_MS).toISOString().slice(0, 16) : '';
}

export function validReplayStep(value) {
  return [-10, -5, -1, 1, 5, 10].includes(Number(value));
}

export function buildReplayMoveBody(session, movement) {
  if (!session || !Number.isSafeInteger(Number(session.cursor_ms))) return null;
  if (typeof movement?.steps === 'number' && validReplayStep(movement.steps)) {
    if (session.mode === 'blind' && movement.steps < 0) return null;
    return { expected_cursor_ms: Number(session.cursor_ms), steps: movement.steps };
  }
  if (session.mode === 'free' && Number.isSafeInteger(Number(movement?.target_ms)) && Number(movement.target_ms) >= 0) {
    return { expected_cursor_ms: Number(session.cursor_ms), target_ms: Number(movement.target_ms) };
  }
  return null;
}

export function buildReplayCasesQuery({ dataset = '', bucket = 'all', symbol = '', timeframe = '', offset = 0, limit = 20 } = {}) {
  const values = { bucket, symbol, timeframe, offset, limit };
  if (dataset) values.dataset = dataset;
  return new URLSearchParams(values).toString();
}

export function buildReplayCaseSessionBody(bucket = 'all', mode = 'blind') {
  const selectionBucket = Object.hasOwn(CASE_BUCKET_LABELS, bucket) ? bucket : 'all';
  const requestedMode = mode === 'free' ? 'free' : 'blind';
  return { mode: selectionBucket === 'all' ? requestedMode : 'free', selection_bucket: selectionBucket };
}

export function visibleReplayCaseOutcome(caseInfo) {
  return caseInfo?.outcome_revealed && caseInfo.outcome && typeof caseInfo.outcome === 'object'
    ? caseInfo.outcome : null;
}

export function canRevealReplayCaseOutcome(caseInfo) {
  return Boolean(caseInfo && !caseInfo.outcome_revealed);
}

export function replayTargetValueForSession(ownerId, sessionId, currentValue, cursorMs) {
  if (!sessionId || ownerId === sessionId) return currentValue;
  return msToShanghaiInput(cursorMs);
}

export function replayOutcomeCodeLabel(kind, code) {
  if (code === null || code === undefined || code === '') return '未提供';
  const labels = kind === 'status' ? OUTCOME_STATUS_LABELS : kind === 'exit_reason' ? EXIT_REASON_LABELS : {};
  return Object.hasOwn(labels, code) ? labels[code] : String(code);
}

export function replayCaseJudgmentOutcomeKnown(caseInfo, observation) {
  if (observation?.independence === 'outcome_known_before_judgment') return true;
  if (observation?.human) return false;
  return Boolean(caseInfo?.outcome_known || (caseInfo?.selection_bucket && caseInfo.selection_bucket !== 'all'));
}

export function canSaveFirstJudgment(session, observation) {
  if (!session || !observation || observation.human || observation.run_id) return false;
  if (observation.comparison_requested) return false;
  if (observation.independence === 'future_seen_in_session'
      || observation.independence === 'ai_requested_before_human_judgment') return false;
  const seenUntil = Number(session.seen_until_ms);
  const cursor = Number(observation.cursor_ms);
  return !(Number.isFinite(seenUntil) && Number.isFinite(cursor) && seenUntil > cursor);
}

export function comparisonCanPrepare(session, observation, imageState) {
  if (!session || !observation?.id) return false;
  return Boolean(observation.image_url && imageState?.id === observation.id && imageState.status === 'loaded');
}

export function comparisonCanRunArm(mode, session, observation, imageState) {
  const comparison = observation?.comparison;
  if (!COMPARISON_MODES.includes(mode) || !session || !observation?.id
      || comparison?.observation_id !== observation.id
      || !comparison?.data_sha256 || Number(comparison?.input_window?.bar_count) !== 120) return false;
  const arm = Array.isArray(comparison.arms) ? comparison.arms.find((item) => item.mode === mode) : null;
  if (!arm || arm.status !== 'pending') return false;
  if (replayRequiresHuman(session, observation) && !observation.human) return false;
  return Boolean(observation.image_url && imageState?.id === observation.id && imageState.status === 'loaded');
}

export function comparisonArmStatusLabel(status) {
  return Object.hasOwn(COMPARISON_STATUS_LABELS, status) ? COMPARISON_STATUS_LABELS[status] : '状态未提供';
}

export function comparisonFailedMeansUnknown(status) {
  return status === 'failed' || status === 'interrupted';
}

export function replayObservationHasJudgmentOrComparison(observation) {
  return Boolean(observation?.human || observation?.run_id || observation?.comparison_requested);
}

export function comparisonResponseIsCurrent(request, current) {
  return Boolean(request && current && request.token === current.token
    && request.sessionId === current.sessionId && request.observationId === current.observationId);
}

export function canAnalyzeObservation(session, observation, imageState) {
  if (!session || !observation?.id || !observation.image_url || observation.run_id || observation.run) return false;
  if (replayRequiresHuman(session, observation) && !observation.human) return false;
  return imageState?.id === observation.id && imageState.status === 'loaded';
}

export function disclosedReplayRows(session) {
  return Array.isArray(session?.chart?.candles) ? session.chart.candles : [];
}

export function replayRequiresHuman(session, observation) {
  if (observation?.retrospective_learning === true && observation.mode === 'free') return false;
  return session?.mode === 'blind' || observation?.mode === 'blind';
}

export function replaySignalObservation(session, activeObservation) {
  const cutoff = Number(session?.case?.signal_close_ms);
  if (!Number.isSafeInteger(cutoff) || cutoff <= 0) return null;
  const items = (session.observations || []).filter((item) => Number(item.cursor_ms) === cutoff);
  const active = activeObservation?.session_id === session.id && Number(activeObservation.cursor_ms) === cutoff
    ? activeObservation : null;
  // An existing attempt takes precedence: the main CTA must not spend again after a failed request.
  const attempted = items.filter((item) => item.run_id).at(-1);
  if (attempted) return active?.id === attempted.id ? active : attempted;
  return active || items.at(-1) || null;
}

export function replaySignalMarkers(session, color) {
  const cutoff = Number(session?.case?.signal_close_ms);
  const duration = timeframeDurationMs(session?.timeframe);
  const open = cutoff - duration;
  if (!Number.isSafeInteger(cutoff) || !duration || cutoff > Number(session?.cursor_ms)
      || !['long', 'short'].includes(session?.case?.side)
      || !disclosedReplayRows(session).some((row) => Number(row.t) === open)) return [];
  const short = session.case.side === 'short';
  return [{ time: open / 1000, position: short ? 'aboveBar' : 'belowBar',
    shape: short ? 'arrowDown' : 'arrowUp', color, text: short ? 'SPIKE 原信号 · 空' : 'SPIKE 原信号 · 多' }];
}

export function replayPriceFormat(rows) {
  const values = rows.flatMap((row) => [row.o, row.h, row.l, row.c])
    .filter((value) => Number.isFinite(value) && value > 0);
  const minimum = values.length ? Math.min(...values) : 1;
  const precision = Math.min(10, Math.max(2, 4 - Math.floor(Math.log10(minimum))));
  return { type: 'price', precision, minMove: 10 ** -precision };
}

export function replayResponseIsCurrent(request, current) {
  return Boolean(request && current && request.token === current.token
    && request.sessionId === current.sessionId);
}

export function createReplayPlayback({ schedule, cancel, shouldContinue, step, getDelay, onChange = () => {} }) {
  const setTimer = schedule || ((callback, delay) => setTimeout(callback, delay));
  const clearTimer = cancel || ((handle) => clearTimeout(handle));
  let active = false;
  let inFlight = false;
  let timer = null;
  let generation = 0;

  function pause() {
    if (!active && timer === null) return;
    active = false;
    generation += 1;
    if (timer !== null) clearTimer(timer);
    timer = null;
    onChange();
  }

  function queue(tokenValue) {
    if (!active || tokenValue !== generation || timer !== null || inFlight) return;
    const delay = Number(getDelay?.());
    timer = setTimer(() => tick(tokenValue), Number.isFinite(delay) && delay > 0 ? delay : 1000);
  }

  async function tick(tokenValue) {
    timer = null;
    if (!active || tokenValue !== generation) return;
    if (!shouldContinue()) {
      pause();
      return;
    }
    inFlight = true;
    let moved = false;
    try { moved = await step(); }
    catch { moved = false; }
    finally { inFlight = false; }
    if (!active || tokenValue !== generation) return;
    if (!moved || !shouldContinue()) {
      pause();
      return;
    }
    queue(tokenValue);
  }

  function play() {
    if (active || !shouldContinue()) return false;
    active = true;
    const tokenValue = ++generation;
    onChange();
    queue(tokenValue);
    return true;
  }

  return {
    play,
    pause,
    isRunning: () => active,
    hasPendingTick: () => timer !== null,
    isInFlight: () => inFlight,
  };
}

const state = {
  catalog: [], catalogWarning: '', catalogError: '', catalogLoading: false,
  caseBrowser: { datasets: [], dataset: '', bucket: 'all', symbol: '', timeframe: '', items: [], total: 0, offset: 0, limit: 20, counts: {}, warning: '', loading: false, error: '' },
  caseRequestToken: 0, caseOutcomeError: '',
  coverage: { pending: false, error: '', firstClose: null, lastClose: null, sourceLabel: '', segments: [], selectedIndex: null },
  sessions: [], sessionsError: '', sessionsLoading: false,
  session: null, activeObservation: null, observationError: '',
  imageState: { id: null, status: 'empty' },
  errors: { freeze: '', human: '', ai: '', followup: '', observations: '' },
  comparisonError: '',
  defaultCriteria: '', criteriaEdited: false,
  busy: '', globalError: '', notice: '',
  signalPhase: '', signalError: '',
  observationRequestToken: 0, sessionRequestToken: 0, sessionListRequestToken: 0, pendingSessionId: null, operationToken: 0,
};

const byId = (id) => document.getElementById(id);
let initialized = false;
let replayActive = false;
let replayChart = null;
let replayCandles = null;
let replayAverages = [];
let replayResizeObserver = null;
let replayThemeObserver = null;
let chartDataHash = '';
let coverageRequestToken = 0;
let startTimeEdited = false;
let caseFilterTimer = null;
let replayTargetSessionId = null;
const replayPlayback = createReplayPlayback({
  shouldContinue: () => Boolean(replayActive && !document.hidden && state.session && !state.busy
    && Number(state.session.cursor_ms) < Number(state.session.last_cursor_ms)),
  step: () => moveReplay({ steps: 1 }, { fromPlayback: true }),
  getDelay: () => Number(byId('replay-speed')?.value),
  onChange: () => { if (initialized) renderReplay(); },
});
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

function formatTime(value, seconds = false) {
  const time = Number(value);
  if (!Number.isFinite(time)) return '时间未提供';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}), hour12: false,
  }).format(new Date(time));
}

function formatOutcomeTime(value) {
  if (value === null || value === undefined || value === '') return '未提供';
  if (typeof value === 'number' && Number.isFinite(value)) return formatTime(value < 10_000_000_000 ? value * 1000 : value);
  const raw = String(value).trim();
  if (/^\d{1,13}$/.test(raw)) {
    const numericTime = Number(raw);
    return formatTime(raw.length <= 10 ? numericTime * 1000 : numericTime);
  }
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? formatTime(parsed) : raw;
}

function formatR(value) {
  if (value === null || value === undefined || value === '') return '未提供';
  const amount = Number(value);
  return Number.isFinite(amount) ? `${amount > 0 ? '+' : ''}${amount.toFixed(2)}R` : '未提供';
}

function setHidden(element, hidden) {
  if (element) element.hidden = Boolean(hidden);
}

function showInline(id, message, kind = 'error') {
  const target = byId(id);
  if (!target) return;
  target.textContent = message || '';
  target.classList.toggle('is-success', kind === 'success');
  setHidden(target, !message);
}

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

async function apiJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    cache: 'no-store', credentials: 'same-origin', ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
  });
  let payload = null;
  try { payload = await response.json(); } catch { /* The status remains useful when a proxy returned plain text. */ }
  if (!response.ok) {
    const detail = typeof payload?.detail === 'string' ? payload.detail : `请求失败（HTTP ${response.status}）。`;
    throw new Error(detail);
  }
  if (!payload || typeof payload !== 'object') throw new Error('本地 API 没有返回可读取的记录。');
  return payload;
}

function postJson(path, body) {
  return apiJson(path, { method: 'POST', body: JSON.stringify(body) });
}

function sameSessionResponse(operation, sessionToken, sessionId) {
  return replayResponseIsCurrent(
    { token: operation.ticket, sessionId },
    { token: state.operationToken, sessionId: state.pendingSessionId },
  ) && sessionToken === state.sessionRequestToken && state.busy === 'session';
}

function sameObservationResponse(operation, observationToken, sessionId) {
  return replayResponseIsCurrent(
    { token: operation.ticket, sessionId },
    { token: state.operationToken, sessionId: state.session?.id || null },
  ) && observationToken === state.observationRequestToken && state.busy === 'observation';
}

function setBusy(action, { sessionId = state.session?.id || null } = {}) {
  if (state.busy) return null;
  state.busy = action;
  const ticket = ++state.operationToken;
  renderReplay();
  return { action, ticket, sessionId };
}

function operationIsCurrent(operation) {
  if (!operation || operation.ticket !== state.operationToken || state.busy !== operation.action) return false;
  return operation.sessionId === null || operation.sessionId === state.session?.id;
}

function finishBusy(operation) {
  if (operationIsCurrent(operation)) state.busy = '';
  renderReplay();
}

function currentCatalogSymbol() {
  return byId('replay-symbol')?.value || '';
}

function timeframeDurationMs(timeframe) {
  const match = /^(\d+)(m|h|H)$/.exec(String(timeframe || ''));
  if (!match) return null;
  const amount = Number(match[1]);
  return amount * (match[2].toLowerCase() === 'h' ? 60 * 60_000 : 60_000);
}

export function defaultStartForSegment(segment, timeframe) {
  const firstReplay = Number(segment?.first_replay_close_ms);
  const lastClose = Number(segment?.last_close_ms);
  const duration = timeframeDurationMs(timeframe);
  if (![firstReplay, lastClose, duration].every(Number.isFinite) || duration <= 0) return firstReplay;
  return Math.max(firstReplay, lastClose - 500 * duration);
}

export function latestSegmentIndex(segments) {
  if (!Array.isArray(segments) || !segments.length) return -1;
  return segments.reduce((latest, segment, index, values) => Number(segment.last_close_ms) > Number(values[latest].last_close_ms) ? index : latest, 0);
}

function renderCoverage() {
  const coverage = state.coverage;
  const message = byId('replay-coverage');
  const select = byId('replay-segment-select');
  const symbol = currentCatalogSymbol();
  const timeframe = byId('replay-timeframe')?.value || '';
  if (coverage.pending) {
    message.textContent = '正在读取所选市场的可回放日期…';
  } else if (coverage.error) {
    message.textContent = `可回放日期读取失败：${coverage.error}`;
  } else if (!symbol || !timeframe) {
    message.textContent = '选择币种与周期后读取本地可回放范围。';
  } else if (!coverage.segments.length) {
    message.textContent = '该市场没有足够长的连续历史区间可回放。';
  } else {
    const rawIndex = Number(select.value);
    const requestedIndex = Number(coverage.selectedIndex);
    const selectedIndex = Number.isInteger(requestedIndex) && requestedIndex >= 0 && requestedIndex < coverage.segments.length
      ? requestedIndex : Number.isInteger(rawIndex) && rawIndex >= 0 && rawIndex < coverage.segments.length
        ? rawIndex : coverage.segments.length - 1;
    const range = coverage.segments[selectedIndex];
    const overall = Number.isFinite(Number(coverage.firstClose)) && Number.isFinite(Number(coverage.lastClose))
      ? `总范围 ${formatTime(coverage.firstClose)} 至 ${formatTime(coverage.lastClose)}。` : '';
    const source = coverage.sourceLabel ? `${coverage.sourceLabel}。` : '';
    const selectedRange = range ? `当前区间 ${formatTime(range.first_replay_close_ms)} 至 ${formatTime(range.last_close_ms)}。` : '';
    message.textContent = `${source}${overall}共 ${coverage.segments.length} 段连续历史；${selectedRange}开始时间默认取该段最近 500 根范围内。`;
  }
  message.classList.toggle('is-error', Boolean(coverage.error));
  const rawSelectedIndex = Number(select.value);
  const requestedIndex = Number(coverage.selectedIndex);
  const selectedIndex = Number.isInteger(requestedIndex) && requestedIndex >= 0 && requestedIndex < coverage.segments.length
    ? requestedIndex : Number.isInteger(rawSelectedIndex) && rawSelectedIndex >= 0 && rawSelectedIndex < coverage.segments.length
      ? rawSelectedIndex : coverage.segments.length - 1;
  select.innerHTML = coverage.segments.length
    ? coverage.segments.map((segment, index) => `<option value="${index}">区间 ${index + 1} · ${escapeHtml(formatTime(segment.first_replay_close_ms))} – ${escapeHtml(formatTime(segment.last_close_ms))}</option>`).join('')
    : `<option value="">${coverage.pending ? '读取可用区间…' : coverage.error ? '无法读取区间' : '没有可用区间'}</option>`;
  if (coverage.segments.length) select.value = String(selectedIndex);
  select.disabled = Boolean(state.busy) || coverage.pending || coverage.segments.length < 2;
  byId('replay-create-session').disabled = Boolean(state.busy) || state.catalogLoading || coverage.pending
    || Boolean(coverage.error) || !symbol || !timeframe || !coverage.segments.length
    || !byId('replay-start-time').value;
}

async function loadCoverage({ resetStart = true } = {}) {
  const symbol = currentCatalogSymbol();
  const timeframe = byId('replay-timeframe')?.value || '';
  const tokenValue = ++coverageRequestToken;
  if (!symbol || !timeframe) {
    state.coverage = { pending: false, error: '', firstClose: null, lastClose: null, sourceLabel: '', segments: [] };
    renderCoverage();
    return;
  }
  if (resetStart) startTimeEdited = false;
  state.coverage = { pending: true, error: '', firstClose: null, lastClose: null, sourceLabel: '', segments: [] };
  renderCoverage();
  const query = new URLSearchParams({ symbol, timeframe });
  try {
    const payload = await apiJson(`/replay/coverage?${query}`);
    if (tokenValue !== coverageRequestToken || symbol !== currentCatalogSymbol() || timeframe !== byId('replay-timeframe').value) return;
    const segments = Array.isArray(payload.segments) ? payload.segments.filter((item) => item && Number.isFinite(Number(item.first_replay_close_ms)) && Number.isFinite(Number(item.last_close_ms))) : [];
    state.coverage = {
      pending: false, error: '', firstClose: payload.first_close_ms ?? null,
      lastClose: payload.last_close_ms ?? null, sourceLabel: typeof payload.source_label === 'string' ? payload.source_label : '', segments, selectedIndex: latestSegmentIndex(segments),
    };
    if (segments.length) {
      const latestIndex = state.coverage.selectedIndex;
      if (!startTimeEdited) {
        byId('replay-start-time').value = msToShanghaiInput(defaultStartForSegment(segments[latestIndex], timeframe));
      }
    }
  } catch (error) {
    if (tokenValue !== coverageRequestToken) return;
    state.coverage = { pending: false, error: error.message || '无法读取覆盖范围。', firstClose: null, lastClose: null, sourceLabel: '', segments: [] };
  }
  renderCoverage();
}

function selectCoverageSegment() {
  const selectedIndex = Number(byId('replay-segment-select').value);
  const segment = state.coverage.segments[selectedIndex];
  if (!segment) return;
  state.coverage.selectedIndex = selectedIndex;
  startTimeEdited = false;
  byId('replay-start-time').value = msToShanghaiInput(defaultStartForSegment(segment, byId('replay-timeframe').value));
  renderCoverage();
}

function renderCatalog() {
  const symbolSelect = byId('replay-symbol');
  const timeframeSelect = byId('replay-timeframe');
  if (!symbolSelect || !timeframeSelect) return;
  const selectedSymbol = symbolSelect.value;
  const symbols = [...new Set(state.catalog.map((item) => String(item.symbol || '')).filter(Boolean))].sort();
  symbolSelect.innerHTML = symbols.length
    ? symbols.map((symbol) => `<option value="${escapeHtml(symbol)}">${escapeHtml(symbol)}</option>`).join('')
    : '<option value="">没有可用历史数据</option>';
  symbolSelect.disabled = state.catalogLoading || symbols.length === 0 || Boolean(state.busy);
  if (symbols.includes(selectedSymbol)) symbolSelect.value = selectedSymbol;
  const symbol = symbolSelect.value || symbols[0] || '';
  const timeframes = [...new Set(state.catalog.filter((item) => item.symbol === symbol)
    .map((item) => String(item.timeframe || '')).filter(Boolean))].sort();
  const selectedTimeframe = timeframeSelect.value;
  timeframeSelect.innerHTML = timeframes.length
    ? timeframes.map((timeframe) => `<option value="${escapeHtml(timeframe)}">${escapeHtml(timeframe)}</option>`).join('')
    : '<option value="">当前币种无周期</option>';
  timeframeSelect.disabled = state.catalogLoading || timeframes.length === 0 || Boolean(state.busy);
  if (timeframes.includes(selectedTimeframe)) timeframeSelect.value = selectedTimeframe;
  renderCoverage();
  const canCreate = Boolean(symbol && timeframeSelect.value && byId('replay-start-time')?.value && !state.catalogLoading && !state.busy && !state.coverage.pending && !state.coverage.error && state.coverage.segments.length);
  byId('replay-create-session').disabled = !canCreate;
  showInline('replay-catalog-warning', state.catalogError || state.catalogWarning, state.catalogError ? 'error' : 'info');
}

function renderSessions() {
  const target = byId('replay-session-list');
  if (!target) return;
  if (state.sessionsLoading && !state.sessions.length) {
    target.innerHTML = '<p class="replay-empty">正在读取已保存会话…</p>';
  } else if (!state.sessions.length) {
    target.innerHTML = `<p class="replay-empty">${escapeHtml(state.sessionsError || '暂无回放会话。创建会话后会保存在这里。')}</p>`;
  } else {
    target.innerHTML = state.sessions.map((session) => {
      const selected = state.session?.id === session.id;
      const mode = session.mode === 'blind' ? '盲审' : '自由回放';
      return `<button class="replay-session-item ${selected ? 'is-selected' : ''}" type="button" data-replay-session="${escapeHtml(session.id)}" aria-current="${selected ? 'true' : 'false'}" ${state.busy ? 'disabled' : ''}>
        <strong>${escapeHtml(session.symbol)} · ${escapeHtml(session.timeframe)}</strong>
        <span>${mode} · 游标 ${escapeHtml(formatTime(session.cursor_ms))}</span>
        <time>${escapeHtml(formatTime(Date.parse(session.created_at)))}</time>
      </button>`;
    }).join('');
  }
  showInline('replay-sessions-error', state.sessionsError);
  byId('replay-refresh-sessions').disabled = state.sessionsLoading || Boolean(state.busy);
}

function renderCaseBrowser() {
  const browser = state.caseBrowser;
  const datasets = byId('replay-case-dataset');
  const bucket = byId('replay-case-bucket');
  const symbol = byId('replay-case-symbol');
  const timeframe = byId('replay-case-timeframe');
  if (!datasets || !bucket || !symbol || !timeframe) return;

  datasets.innerHTML = browser.datasets.length
    ? browser.datasets.map((item) => `<option value="${escapeHtml(item.id)}" ${item.available ? '' : 'disabled'}>${escapeHtml(item.label || item.id)} · ${escapeHtml(item.count ?? 0)} 条${item.available ? '' : ' · 不可用'}</option>`).join('')
    : '<option value="">读取案例数据集…</option>';
  if (browser.dataset && browser.datasets.some((item) => item.id === browser.dataset)) datasets.value = browser.dataset;
  else if (browser.datasets.length && !browser.dataset) datasets.value = browser.datasets.find((item) => item.available)?.id || browser.datasets[0].id;
  datasets.disabled = Boolean(state.busy) || browser.loading || !browser.datasets.some((item) => item.available);

  bucket.innerHTML = Object.entries(CASE_BUCKET_LABELS)
    .map(([key, label]) => `<option value="${key}">${escapeHtml(label)}</option>`).join('');
  bucket.value = Object.hasOwn(CASE_BUCKET_LABELS, browser.bucket) ? browser.bucket : 'all';
  bucket.disabled = Boolean(state.busy) || browser.loading;
  if (symbol.value !== browser.symbol) symbol.value = browser.symbol;
  if (timeframe.value !== browser.timeframe) timeframe.value = browser.timeframe;
  symbol.disabled = Boolean(state.busy);
  timeframe.disabled = Boolean(state.busy);

  const counts = browser.counts || {};
  byId('replay-case-counts').innerHTML = CASE_COUNT_KEYS.map((key) => `<span class="replay-case-count"><strong>${escapeHtml(counts[key] ?? 0)}</strong>${escapeHtml(CASE_BUCKET_LABELS[key])}</span>`).join('');
  const start = browser.total ? browser.offset + 1 : 0;
  const end = Math.min(browser.offset + browser.limit, browser.total);
  byId('replay-case-page-status').textContent = browser.loading
    ? '正在读取历史信号案例…' : `${start}–${end} / ${browser.total} 条`;
  byId('replay-case-prev').disabled = Boolean(state.busy) || browser.loading || browser.offset <= 0;
  byId('replay-case-next').disabled = Boolean(state.busy) || browser.loading || browser.offset + browser.limit >= browser.total;

  if (browser.loading && !browser.items.length) {
    byId('replay-case-list').innerHTML = '<p class="replay-empty">正在读取历史信号案例…</p>';
  } else if (!browser.items.length) {
    byId('replay-case-list').innerHTML = `<p class="replay-empty">${escapeHtml(browser.error || '没有符合筛选条件的历史信号案例。')}</p>`;
  } else {
    byId('replay-case-list').innerHTML = browser.items.map((item) => {
      const description = [
        `${escapeHtml(SIDE_LABELS[item.side] || item.side || '方向未知')} · ${escapeHtml(item.family || '形态未分类')}`,
        `信号收盘 ${escapeHtml(formatTime(item.signal_close_ms))}`,
        item.source_label ? `来源 ${escapeHtml(item.source_label)}` : '',
      ].filter(Boolean).join(' · ');
      return `<article class="replay-case-item">
        <div><strong>${escapeHtml(item.symbol)} · ${escapeHtml(item.timeframe)}</strong><span>${description}</span></div>
        <button class="button button-secondary" type="button" data-replay-case-id="${escapeHtml(item.id)}" ${state.busy || browser.loading ? 'disabled' : ''}>回放此信号</button>
      </article>`;
    }).join('');
  }
  const bucketNote = browser.bucket === 'all'
    ? '默认混合盲审：案例列表不显示未来交易结果。'
    : `当前按“${CASE_BUCKET_LABELS[browser.bucket]}”筛选，类别在判断前已知；这些案例仅用于学习，不作为独立盲审标签。`;
  const selectedDataset = browser.datasets.find((item) => item.id === browser.dataset);
  const warning = [...new Set([browser.error, browser.warning, selectedDataset?.warning, bucketNote]
    .map((item) => String(item || '').trim()).filter(Boolean))].join(' ');
  showInline('replay-case-warning', warning, browser.error ? 'error' : 'info');
}

function renderReplayCaseOutcome() {
  const caseInfo = state.session?.case;
  const empty = byId('replay-case-outcome-empty');
  const content = byId('replay-case-outcome-content');
  setHidden(empty, Boolean(caseInfo));
  setHidden(content, !caseInfo);
  if (!caseInfo) {
    showInline('replay-case-outcome-error', '');
    return;
  }

  const known = Boolean(caseInfo.outcome_known || (caseInfo.selection_bucket && caseInfo.selection_bucket !== 'all'));
  const caseMeta = byId('replay-case-outcome-meta');
  caseMeta.textContent = [
    caseInfo.symbol || state.session.symbol,
    caseInfo.timeframe || state.session.timeframe,
    caseInfo.family,
    caseInfo.source_label ? `来源 ${caseInfo.source_label}` : '',
    caseInfo.signal_close_ms ? `信号收盘 ${formatTime(caseInfo.signal_close_ms)}` : '',
  ].filter(Boolean).join(' · ');
  const knownNote = byId('replay-case-outcome-known');
  const observation = state.activeObservation;
  const preservedJudgment = Boolean(observation?.human && !replayCaseJudgmentOutcomeKnown(caseInfo, observation));
  knownNote.textContent = preservedJudgment
    ? '当前结果已知，但这条人工判断在揭晓结果或推进游标前已保存；原有独立性记录保持不变。后续新观察按已知结果处理。'
    : known
      ? caseInfo.selection_bucket && caseInfo.selection_bucket !== 'all'
        ? '此案例按历史结果类别筛选，判断前已知结果类别；本次人工判断仅作学习标签。'
        : '此案例的历史结果在判断前已知；本次人工判断仅作学习标签。'
      : '结果仍隐藏。盲审请在信号时点先保存人工判断；揭晓后会记录结果暴露。';
  knownNote.classList.toggle('is-known', known);
  const warning = byId('replay-case-outcome-warning');
  warning.textContent = typeof caseInfo.warning === 'string' ? caseInfo.warning : '';
  setHidden(warning, !warning.textContent || state.notice.trim() === warning.textContent.trim());
  const outcome = visibleReplayCaseOutcome(caseInfo);
  const censored = caseInfo.selection_bucket === 'open' || outcome?.status === 'open' || outcome?.status === 'censored';
  const openNote = byId('replay-case-open-note');
  openNote.textContent = censored
    ? outcome
      ? '观察期结束时仍未平仓（删失），不代表净亏损；当前仅展示已知入场与毛浮盈，净 R 尚未确定。'
      : '观察期结束时仍未平仓（删失），不代表净亏损。可揭晓已知入场与浮盈，净 R 尚未确定。'
    : '';
  setHidden(openNote, !censored);

  const revealButton = byId('replay-case-outcome-reveal');
  const revealable = canRevealReplayCaseOutcome(caseInfo);
  setHidden(revealButton, Boolean(outcome));
  revealButton.disabled = Boolean(state.busy) || !revealable;
  revealButton.textContent = state.busy === 'outcome' ? '正在揭晓…'
    : '揭晓历史结果';
  const result = byId('replay-case-outcome-result');
  if (outcome) {
    const cost = outcome.cost_bps !== null && outcome.cost_bps !== undefined && outcome.cost_bps !== ''
      && Number.isFinite(Number(outcome.cost_bps)) ? `${Number(outcome.cost_bps).toFixed(2)} bps` : '未提供';
    const resultIsCensored = censored || outcome.status === 'open' || outcome.status === 'censored';
    const ambiguousStopBar = Boolean(outcome.stop_bar_excursion_ambiguous);
    const confirmedMfe = formatR(outcome.mfe_r);
    const netR = resultIsCensored ? '尚未确定' : formatR(outcome.net_r);
    const statusLabel = resultIsCensored
      ? OUTCOME_STATUS_LABELS.censored : replayOutcomeCodeLabel('status', outcome.status);
    result.innerHTML = `<div class="replay-case-r-metrics">
        <div><span>净收益</span><strong>${escapeHtml(netR)}</strong></div>
        <div><span>毛收益</span><strong>${escapeHtml(formatR(outcome.gross_r))}</strong></div>
        <div><span>已确认毛浮盈</span><strong>${escapeHtml(confirmedMfe)}</strong></div>
      </div>
      <dl class="replay-case-outcome-details">
        <div><dt>入场时间</dt><dd>${escapeHtml(formatOutcomeTime(outcome.entry_time))}</dd></div>
        <div><dt>退出时间</dt><dd>${escapeHtml(formatOutcomeTime(outcome.exit_time))}</dd></div>
        <div><dt>退出原因</dt><dd>${escapeHtml(replayOutcomeCodeLabel('exit_reason', outcome.exit_reason))}</dd></div>
        <div><dt>状态 / 成本</dt><dd>${escapeHtml(statusLabel)} · ${escapeHtml(cost)}</dd></div>
      </dl>
      ${ambiguousStopBar ? `<p class="replay-case-ambiguity">止损柱内顺序不明，浮盈上界 ${escapeHtml(formatR(outcome.mfe_upper_r))}。</p>` : ''}
      ${outcome.meaning ? `<p>${escapeHtml(outcome.meaning)}</p>` : ''}
      <p class="replay-source-note">历史策略账本记录；不等同模型预测收益。结果已揭晓并登记暴露。</p>`;
    setHidden(result, false);
  } else {
    result.innerHTML = caseInfo.outcome_revealed
      ? '<p>本案例已揭晓，但服务端没有提供可展示的结果明细。</p>'
      : '<p>历史交易结果默认隐藏。揭晓只读取历史策略账本，并不代表模型预测。</p>';
    setHidden(result, Boolean(revealable));
  }
  showInline('replay-case-outcome-error', state.caseOutcomeError);
}

function observations() {
  return Array.isArray(state.session?.observations) ? state.session.observations : [];
}

function observationExposure(observation) {
  if (observation?.human) return '人工判断已保存';
  if (observation?.run_id) return '已调用模型';
  if (observation?.comparison_requested) return '已请求图文输入对照';
  const seenUntil = Number(state.session?.seen_until_ms);
  const cursor = Number(observation?.cursor_ms);
  if (Number.isFinite(seenUntil) && Number.isFinite(cursor) && seenUntil > cursor) return '后续 K 线已显示';
  return '尚未判断';
}

export function independenceLabel(observation, session) {
  let label;
  if (observation?.independence === 'outcome_known_before_judgment') label = '历史结果类别在判断前已知，仅作学习标签';
  if (!label && observation?.comparison_requested && !observation?.human) label = '已请求图文输入对照，不能补录独立判断';
  const seenUntil = Number(session?.seen_until_ms);
  const cursor = Number(observation?.cursor_ms);
  const laterSeen = Number.isFinite(seenUntil) && Number.isFinite(cursor) && seenUntil > cursor;
  if (!label && observation?.independence === 'ai_requested_before_human_judgment') {
    label = laterSeen ? '模型先于人工判断；其后已推进后续行情' : '模型先于人工判断查看';
  }
  if (!label && observation?.independence === 'future_seen_in_session') label = '冻结时已看过后续行情';
  if (!label && (observation?.independence === 'before_ai_and_later_bars_in_this_session' || observation?.human)) {
    label = laterSeen ? '人工判断先于模型；其后已推进后续行情' : '人工判断先于模型';
  }
  if (!label && laterSeen) label = '已看过后续行情，不能补录独立判断';
  if (!label) label = observation?.mode === 'blind' ? '盲审观察，尚未判断' : '自由回放观察';
  if (observation?.comparison_requested && observation?.human) label += '；首次人工判断后请求图文输入对照';
  return label;
}

function renderObservations() {
  const target = byId('replay-observation-list');
  if (!target) return;
  const items = observations();
  if (!state.session) {
    target.innerHTML = '<p class="replay-empty">选择会话后显示观察历史。</p>';
  } else if (!items.length) {
    target.innerHTML = '<p class="replay-empty">此会话尚未冻结观察。</p>';
  } else {
    target.innerHTML = items.map((item) => {
      const selected = state.activeObservation?.id === item.id;
      const stateText = item.human ? STATE_LABELS[item.human.current_state] || '已判断' : observationExposure(item);
      return `<button class="replay-observation-item ${selected ? 'is-selected' : ''}" type="button" data-replay-observation="${escapeHtml(item.id)}" aria-current="${selected ? 'true' : 'false'}" ${state.busy ? 'disabled' : ''}>
        <strong>${escapeHtml(formatTime(item.cursor_ms))}</strong>
        <span>${escapeHtml(stateText)}${item.human?.side ? ` · ${escapeHtml(SIDE_LABELS[item.human.side] || item.human.side)}` : ''}${item.run_id ? ' · 含模型结果' : ''}${item.comparison_requested ? ' · 已请求图文对照' : ''}</span>
      </button>`;
    }).join('');
  }
}

function apiImageUrl(value) {
  if (typeof value !== 'string' || !value.trim()) return '';
  try {
    const parsed = new URL(value, window.location.href);
    if (parsed.origin !== window.location.origin || !parsed.pathname.startsWith(`${API_BASE}/images/`)) return '';
    return parsed.href;
  } catch { return ''; }
}

function setImage(element, value, alt) {
  if (!element) return false;
  const src = apiImageUrl(value);
  if (!src) {
    element.removeAttribute('src');
    element.alt = alt;
    return false;
  }
  if (element.getAttribute('src') !== src) element.src = src;
  element.alt = alt;
  return true;
}

function resetObservationDrafts() {
  byId('replay-human-state').value = '';
  byId('replay-human-side').value = 'unknown';
  byId('replay-human-note').value = '';
  byId('replay-followup-note').value = '';
}

function setActiveObservation(observation) {
  if (state.activeObservation?.id !== observation?.id) {
    resetObservationDrafts();
    state.imageState = { id: observation?.id || null, status: observation?.image_url ? 'loading' : 'empty' };
    state.comparisonError = '';
  }
  state.activeObservation = observation || null;
}

function bindObservationImage(element, observation, alt) {
  if (!element || !observation?.image_url) {
    if (element) element.removeAttribute('src');
    return false;
  }
  const src = apiImageUrl(observation.image_url);
  if (!src) {
    state.imageState = { id: observation.id, status: 'error' };
    element.removeAttribute('src');
    return false;
  }
  const changed = element.getAttribute('src') !== src;
  if (state.imageState.id !== observation.id) state.imageState = { id: observation.id, status: 'loading' };
  if (changed) state.imageState = { id: observation.id, status: 'loading' };
  element.alt = alt;
  element.onload = () => {
    if (state.activeObservation?.id !== observation.id) return;
    state.imageState = { id: observation.id, status: 'loaded' };
    renderReplay();
  };
  element.onerror = () => {
    if (state.activeObservation?.id !== observation.id) return;
    state.imageState = { id: observation.id, status: 'error' };
    renderReplay();
  };
  if (changed) element.src = src;
  if (element.complete && element.naturalWidth > 0 && state.imageState.status !== 'loaded') {
    state.imageState = { id: observation.id, status: 'loaded' };
  }
  return true;
}

function renderHuman() {
  const observation = state.activeObservation;
  const session = state.session;
  const existing = byId('replay-human-existing');
  const form = byId('replay-human-form');
  const allowed = canSaveFirstJudgment(session, observation);
  const blind = replayRequiresHuman(session, observation);
  const outcomeKnown = replayCaseJudgmentOutcomeKnown(session?.case, observation);
  const exposedBeforeHuman = !observation?.human && Boolean(observation?.comparison_requested || observation?.run_id
    || observation?.independence === 'ai_requested_before_human_judgment');
  byId('replay-human-heading').textContent = outcomeKnown ? '历史结果已知 · 学习标签'
    : exposedBeforeHuman ? '模型已先接触 · 不可补独立人工标签'
      : blind ? '盲审人工标签（先于 AI）' : '自由回放人工判断（与 AI 分开记录）';
  if (!observation) {
    setHidden(existing, true);
    setHidden(form, true);
    showInline('replay-human-error', state.errors.human);
    byId('replay-save-human').disabled = true;
    return;
  }
  if (observation.human) {
    const human = observation.human;
    existing.innerHTML = `<strong>${escapeHtml(STATE_LABELS[human.current_state] || human.current_state)} · ${escapeHtml(SIDE_LABELS[human.side] || human.side)}</strong>${human.note ? `<p class="replay-human-note">${escapeHtml(human.note)}</p>` : ''}<span>${escapeHtml(formatTime(Date.parse(human.created_at)))}</span>`;
    setHidden(existing, false);
    setHidden(form, true);
    showInline('replay-human-error', state.errors.human);
  } else if (!allowed) {
    const seenLater = Number(session?.seen_until_ms) > Number(observation.cursor_ms);
    const message = observation.comparison_requested
      ? '已准备或请求图文输入对照；首次人工判断应先于对照，因此不能在此后补录独立标签。'
      : seenLater
      ? '后续 K 线已显示，不能再补记这张原图的独立判断；可在回访备注中补充观察。'
      : observation.run_id ? '模型已调用，首次人工判断已锁定；请用回访备注记录补充意见。'
        : '这条观察不能再保存为首次判断。';
    existing.innerHTML = `<span>${escapeHtml(message)}</span>`;
    setHidden(existing, false);
    setHidden(form, true);
    showInline('replay-human-error', state.errors.human);
  } else {
    setHidden(existing, true);
    setHidden(form, false);
    byId('replay-save-human').disabled = Boolean(state.busy) || !byId('replay-human-state').value;
    showInline('replay-human-error', state.errors.human);
  }
}

function safeArray(value) {
  return Array.isArray(value) ? value.filter((item) => typeof item === 'string' && item.trim()).slice(0, 40) : [];
}

function renderAi() {
  const observation = state.activeObservation;
  const result = byId('replay-ai-result');
  const gate = byId('replay-blind-gate');
  const button = byId('replay-analyze');
  const hasRun = Boolean(observation?.run_id || observation?.run);
  const blindNeedsHuman = Boolean(replayRequiresHuman(state.session, observation)
    && !observation?.human);
  button.disabled = Boolean(state.busy) || !canAnalyzeObservation(state.session, observation, state.imageState) || hasRun || blindNeedsHuman;
  button.textContent = state.busy === 'analyze' ? '正在请求模型…' : hasRun ? '本观察已调用模型' : '手动调用模型';
  setHidden(gate, !blindNeedsHuman);
  showInline('replay-ai-error', state.errors.ai || '');
  const run = observation?.run;
  if (!run) {
    setHidden(result, false);
    result.innerHTML = state.busy === 'analyze'
      ? '<p>正在等待模型响应，冻结输入已保存。请勿重复提交；刷新后可恢复观察查看进度。</p>'
      : observation?.run_id
      ? '<p>模型调用记录已关联；当前接口尚未返回完整结果，请刷新观察记录。</p>'
      : '<p>尚未调用模型。推进游标不会自动发送模型请求。</p>';
    byId('replay-ai-status').textContent = state.busy === 'analyze' ? '正在等待模型' : observation?.run_id ? '模型记录' : '未调用';
    setHidden(byId('replay-ai-status'), !observation);
    return;
  }
  const decision = run.decision || {};
  const evidence = safeArray(decision.evidence);
  const risks = safeArray(decision.risks);
  const list = (items) => items.length ? `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '<p>未提供。</p>';
  const verdict = VERDICT_LABELS[decision.verdict] || (run.status === 'completed' ? '未判定' : run.status === 'failed' ? '识别失败' : run.status === 'interrupted' ? '调用已中断' : '处理中');
  const currentState = STATE_LABELS[decision.current_state] || '';
  result.innerHTML = `<span class="replay-ai-verdict">${escapeHtml(verdict)}${currentState ? ` · ${escapeHtml(currentState)}` : ''}${decision.side ? ` · ${escapeHtml(SIDE_LABELS[decision.side] || decision.side)}` : ''}</span>
    <p>${escapeHtml(decision.summary || run.error || '模型未返回摘要。')}</p>
    <div><h5>可观察证据</h5>${list(evidence)}</div><div><h5>不确定性与风险</h5>${list(risks)}</div>
    <p class="replay-source-note">${escapeHtml(run.model || observation.model || '模型未提供')} · ${escapeHtml(formatTime(Date.parse(run.created_at)))}</p>`;
  setHidden(result, false);
  byId('replay-ai-status').textContent = run.status === 'completed' ? '已完成' : run.status === 'failed' ? '失败' : run.status === 'interrupted' ? '已中断' : '处理中';
  setHidden(byId('replay-ai-status'), false);
}

function comparisonOrderText(value) {
  if (!Array.isArray(value)) return '未提供';
  const labels = value.filter((mode) => COMPARISON_MODES.includes(mode)).map((mode) => COMPARISON_MODE_LABELS[mode]);
  return labels.length ? labels.join(' → ') : '尚无记录';
}

function comparisonRunDetails(run, arm) {
  const status = arm.status || run?.status || 'pending';
  if (status === 'unprepared') return '<p class="replay-empty">先准备对照包；准备不会调用模型。</p>';
  const usage = run?.usage || {};
  const inputTokens = usage.input_tokens ?? usage.prompt_tokens ?? run?.input_tokens;
  const outputTokens = usage.output_tokens ?? usage.completion_tokens ?? run?.output_tokens;
  const totalTokens = usage.total_tokens ?? run?.total_tokens;
  const tokenParts = [inputTokens !== undefined ? `输入 ${inputTokens}` : '', outputTokens !== undefined ? `输出 ${outputTokens}` : '', totalTokens !== undefined ? `合计 ${totalTokens}` : '']
    .filter(Boolean);
  const latency = Number(run?.latency_ms ?? run?.duration_ms ?? run?.elapsed_ms);
  const metrics = [Number.isFinite(latency) ? `耗时 ${(latency / 1000).toFixed(2)} 秒` : '', tokenParts.length ? `Token ${tokenParts.join(' / ')}` : 'Token 未提供']
    .filter(Boolean).join(' · ');
  if (comparisonFailedMeansUnknown(status)) {
    return `<p class="replay-comparison-failure">${status === 'failed' ? '本次调用失败' : '本次调用已中断'}，不代表模型判断为“不符合”；按一次调用原则不自动重试。${run?.error ? ` ${escapeHtml(run.error)}` : ''}</p><p class="replay-source-note">${escapeHtml(metrics || '耗时与 Token 未提供')}</p>`;
  }
  if (status !== 'completed' || !run) return '<p class="replay-empty">尚无已完成的判断结果。</p>';
  const decision = run.decision || {};
  const verdict = VERDICT_LABELS[decision.verdict] || '未判定';
  const currentState = STATE_LABELS[decision.current_state] || decision.current_state || '';
  const side = decision.side ? SIDE_LABELS[decision.side] || decision.side : '';
  const evidence = safeArray(decision.evidence);
  const risks = safeArray(decision.risks);
  const list = (items) => items.length ? `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '<p>未提供。</p>';
  return `<p class="replay-comparison-verdict">${escapeHtml(verdict)}${currentState ? ` · ${escapeHtml(currentState)}` : ''}${side ? ` · ${escapeHtml(side)}` : ''}</p>
    <p>${escapeHtml(decision.summary || '模型未提供判断理由。')}</p>
    <details class="replay-comparison-reasons"><summary>查看依据与不确定性</summary><h5>可观察依据</h5>${list(evidence)}<h5>不确定性与风险</h5>${list(risks)}</details>
    <p class="replay-source-note">${escapeHtml(metrics || '耗时与 Token 未提供')}</p>`;
}

function renderComparison() {
  const observation = state.activeObservation;
  const session = state.session;
  const packageInfo = observation?.comparison;
  const blindNeedsHuman = Boolean(observation && replayRequiresHuman(session, observation) && !observation.human);
  const imageMissing = Boolean(observation && (!observation.image_url
    || state.imageState.id === observation.id && state.imageState.status === 'error'));
  const prepare = byId('replay-comparison-prepare');
  prepare.disabled = Boolean(state.busy) || !comparisonCanPrepare(session, observation, state.imageState);
  prepare.textContent = state.busy === 'comparison_prepare' ? '正在准备对照包…'
    : packageInfo ? '刷新对照状态' : '准备图文输入对照';
  showInline('replay-comparison-error', state.comparisonError || '');
  setHidden(byId('replay-comparison-blind-gate'), !blindNeedsHuman);
  const imageGate = byId('replay-comparison-image-gate');
  imageGate.textContent = imageMissing
    ? '原冻结图缺失或无法载入，无法复现三组对照；纯图像、纯数值和图文组均已拦截。'
    : observation && state.imageState.id === observation.id && state.imageState.status !== 'loaded'
      ? '正在验证原冻结图；载入成功前不能准备或调用三组对照。' : '';
  setHidden(imageGate, !observation || !imageMissing && !(state.imageState.id === observation.id && state.imageState.status !== 'loaded'));
  byId('replay-comparison-observation').textContent = observation
    ? `${observation.symbol || session?.symbol || ''} · ${observation.timeframe || session?.timeframe || ''} · 冻结于 ${formatTime(observation.cursor_ms)}`
    : '先冻结一个时点，或从会话观察历史中打开已有观察。';
  const exportLink = byId('replay-comparison-export');
  const exportable = Boolean(observation?.id && packageInfo);
  exportLink.href = exportable
    ? `${REPLAY_BASE}/observations/${encodeURIComponent(observation.id)}/comparison/export` : '#';
  exportLink.download = exportable ? `replay-comparison-${observation.id}.json` : '';
  exportLink.setAttribute('aria-disabled', exportable ? 'false' : 'true');
  setHidden(exportLink, !exportable);

  const summary = byId('replay-comparison-summary');
  const packet = byId('replay-comparison-packet');
  const modelWarning = packageInfo?.returned_models_consistent === false
    ? '警告：三组返回的实际模型不一致，不能按同模型输入对照解读。' : '';
  showInline('replay-comparison-model-warning', modelWarning, 'error');
  if (!packageInfo) {
    summary.textContent = observation
      ? '对照包尚未准备。准备操作不调用模型、不产生模型调用费用。'
      : '暂无冻结观察。';
    packet.textContent = '准备对照包后可查看数值输入。';
    byId('replay-comparison-order').textContent = '建议顺序：未提供 · 实际顺序：尚无记录';
    byId('replay-comparison-progress').textContent = '已完成 0 / 3 · 尚无一致性结论';
  } else {
    const inputWindow = packageInfo.input_window || {};
    summary.textContent = `${packageInfo.model || '模型未提供'} · 同一识别规则 · ${inputWindow.bar_count ?? '未提供'} 根冻结行情 · ${formatTime(inputWindow.start_ms)} 至 ${formatTime(inputWindow.end_ms)}`;
    byId('replay-comparison-order').textContent = `建议顺序：${comparisonOrderText(packageInfo.recommended_order)} · 实际顺序：${comparisonOrderText(packageInfo.execution_order)}`;
    const completed = Number.isFinite(Number(packageInfo.completed_count)) ? Number(packageInfo.completed_count)
      : (Array.isArray(packageInfo.arms) ? packageInfo.arms.filter((arm) => arm.status === 'completed').length : 0);
    const agreement = packageInfo.agreement === true ? '三组分类标签相同' : packageInfo.agreement === false ? '三组分类标签有差异' : '尚未形成三组完整分类结果';
    const modelConsistency = packageInfo.returned_models_consistent === true ? ' · 返回模型一致'
      : packageInfo.returned_models_consistent === false ? ' · 返回模型不一致' : '';
    byId('replay-comparison-progress').textContent = `已完成 ${completed} / 3 · ${agreement}${modelConsistency}（仅描述分类结果，不证明准确或盈利）`;
    packet.textContent = packageInfo.market_packet && typeof packageInfo.market_packet === 'object'
      ? JSON.stringify({ protocol_version: packageInfo.protocol_version, model: packageInfo.model,
        data_sha256: packageInfo.data_sha256, image_sha256: packageInfo.image_sha256,
        criteria_sha256: packageInfo.criteria_sha256, reference_policy: packageInfo.reference_policy,
        market_packet: packageInfo.market_packet }, null, 2) : '服务端未返回数值输入预览。';
  }

  const arms = Array.isArray(packageInfo?.arms) ? packageInfo.arms : [];
  byId('replay-comparison-arms').innerHTML = COMPARISON_MODES.map((mode) => {
    const arm = arms.find((item) => item.mode === mode) || { mode, status: packageInfo ? 'pending' : 'unprepared', run_id: null, run: null };
    const run = arm.run || null;
    const prepared = Boolean(packageInfo);
    const status = arm.status || run?.status || (prepared ? 'pending' : 'unprepared');
    const attempted = Boolean(arm.run_id) || ['running', 'completed', 'failed', 'interrupted'].includes(status);
    const imageMode = mode === 'text' ? '无图，只读同一份冻结数值输入' : mode === 'vision' ? '只传一张冻结原图' : '传一张冻结原图和同一份 JSON 数值输入';
    const canRun = comparisonCanRunArm(mode, session, observation, state.imageState);
    const disabled = Boolean(state.busy) || !canRun;
    const buttonText = state.busy === `comparison_${mode}` ? '正在调用…'
      : !observation ? '先冻结观察' : !prepared ? '先准备对照包'
        : status === 'pending' ? '手动调用一次' : status === 'running' ? '调用进行中'
          : attempted ? '此组已尝试，不可重试' : '状态暂不可用';
    const statusText = !prepared ? '待准备' : comparisonArmStatusLabel(status);
    const imageBlock = observation && imageMissing
      ? '<p class="replay-comparison-failure">原冻结图缺失或无法载入（可能返回 404），无法复现三组对照；此组已拦截。</p>' : '';
    return `<article class="replay-comparison-arm">
      <div class="panel-heading"><div><h4>${escapeHtml(COMPARISON_MODE_LABELS[mode])}</h4><p class="replay-source-note">${escapeHtml(imageMode)}</p></div><span class="source-chip">${escapeHtml(statusText)}</span></div>
      <button class="button button-secondary replay-full-width" type="button" data-comparison-arm="${mode}" ${disabled ? 'disabled' : ''}>${escapeHtml(buttonText)}</button>
      ${imageBlock}${comparisonRunDetails(run, arm)}
    </article>`;
  }).join('');
}

function renderFollowups() {
  const observation = state.activeObservation;
  const session = state.session;
  const cursor = Number(session?.cursor_ms);
  const observationCursor = Number(observation?.cursor_ms);
  const later = Boolean(observation && Number.isFinite(cursor) && Number.isFinite(observationCursor) && cursor > observationCursor);
  const eligible = later && replayObservationHasJudgmentOrComparison(observation);
  byId('replay-followup-eligibility').textContent = !observation ? '先选择观察' : eligible ? `当前 ${formatTime(cursor)}` : later ? '需先保存判断、调用模型或对照组' : '先推进到更晚时间';
  byId('replay-save-followup').disabled = Boolean(state.busy) || !eligible;
  showInline('replay-followup-error', state.errors.followup);
  const target = byId('replay-followup-list');
  if (!target) return;
  if (!observation) {
    target.innerHTML = '<p class="replay-empty">选择观察后显示原判断、模型判断与回访记录。</p>';
    return;
  }
  const human = observation.human;
  const run = observation.run;
  const followups = Array.isArray(observation.followups) ? observation.followups : [];
  const humanMarkup = human ? `<section><h4>原人工判断</h4><p>${escapeHtml(STATE_LABELS[human.current_state] || human.current_state)} · ${escapeHtml(SIDE_LABELS[human.side] || human.side)}</p>${human.note ? `<p class="replay-human-note">${escapeHtml(human.note)}</p>` : ''}<time>${escapeHtml(formatTime(Date.parse(human.created_at)))}</time></section>`
    : '<section><h4>原人工判断</h4><p>未保存首次判断。</p></section>';
  const aiDecision = run?.decision || {};
  const aiMarkup = run ? `<section><h4>原模型判断</h4><p>${escapeHtml(VERDICT_LABELS[aiDecision.verdict] || run.status || '未判定')}${aiDecision.current_state ? ` · ${escapeHtml(STATE_LABELS[aiDecision.current_state] || aiDecision.current_state)}` : ''}</p><p>${escapeHtml(aiDecision.summary || run.error || '无摘要')}</p></section>`
    : '<section><h4>原模型判断</h4><p>尚未调用模型。</p></section>';
  const followupMarkup = followups.length ? followups.map((item) => {
    const imageUrl = apiImageUrl(item.image_url);
    return `<article class="replay-followup-item"><time>${escapeHtml(formatTime(item.cursor_ms))}</time><span class="replay-followup-meta">间隔 ${escapeHtml(item.bars_elapsed ?? '—')} 根 · 价格变化 ${escapeHtml(Number.isFinite(Number(item.close_change_pct)) ? `${Number(item.close_change_pct).toFixed(2)}%` : '未提供')}</span>${item.note ? `<p class="replay-followup-note">${escapeHtml(item.note)}</p>` : '<p>未写备注。</p>'}${imageUrl ? `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(formatTime(item.cursor_ms))} 的后续回访标准图" loading="lazy">` : ''}</article>`;
  }).join('') : '<p>还没有后续回访。</p>';
  target.innerHTML = `<h4>观察 ${escapeHtml(formatTime(observation.cursor_ms))}</h4>
    <p class="replay-source-note">${escapeHtml(observation.symbol || session.symbol)} · ${escapeHtml(observation.timeframe || session.timeframe)} · ${escapeHtml(independenceLabel(observation, session))}</p>
    ${humanMarkup}${aiMarkup}<div class="replay-followups"><h4>后续回访</h4>${followupMarkup}</div>`;
  target.querySelectorAll('img').forEach((image) => image.addEventListener('error', () => image.remove(), { once: true }));
}

function renderSignalGuide() {
  const session = state.session;
  const signal = session?.case;
  const observation = replaySignalObservation(session, state.activeObservation);
  const run = observation?.run;
  const attempted = Boolean(observation?.run_id);
  const running = state.busy === 'signal' && state.signalPhase === 'analyze'
    || run?.status === 'running' || observation?.status === 'running';
  const failed = ['failed', 'interrupted'].includes(run?.status || observation?.status);
  const duration = timeframeDurationMs(session?.timeframe);
  const delta = signal && duration ? Math.round((Number(session.cursor_ms) - Number(signal.signal_close_ms)) / duration) : 0;
  const futureSeen = signal && Number(session.seen_until_ms) > Number(signal.signal_close_ms);
  const signalInView = replaySignalMarkers(session, '').length > 0;
  const blindNeedsHuman = session?.mode === 'blind' && !futureSeen && !observation?.human && !attempted;
  const pending = Boolean(state.busy);
  byId('replay-signal-title').textContent = signal
    ? `SPIKE 原信号 · ${SIDE_LABELS[signal.side] || '未知'} · ${formatTime(signal.signal_close_ms)}`
    : session ? '当前是自由选时回放，尚未绑定历史信号' : '先从左侧选择一个历史信号';
  byId('replay-signal-position').textContent = signal
    ? `${signal.family || '历史策略信号'} · ${delta === 0 ? '现在正停在信号收盘' : delta > 0 ? `当前已到信号后 ${delta} 根 K 线` : `距离信号还有 ${-delta} 根 K 线`} · ${signalInView ? '图中箭头标记原策略信号' : delta < 0 ? '信号尚未进入图中' : '信号已不在当前 120 根图内，可回看冻结输入'}`
    : '选择案例 → 识别信号当时的图 → 播放后续行情。';
  byId('replay-signal-ai-status').textContent = running ? 'AI 调用中…'
    : failed ? 'AI 调用失败' : attempted ? 'AI 已调用' : 'AI 尚未调用';
  const action = byId('replay-signal-analyze');
  const activeProviderName = document?.documentElement?.dataset?.activeProviderName || '当前已保存的模型服务';
  action.textContent = state.busy === 'signal'
    ? state.signalPhase === 'analyze' ? `正在调用${activeProviderName}…` : '正在读取信号输入…'
    : attempted ? (running ? '刷新 AI 调用状态' : '查看 AI 结果')
      : blindNeedsHuman ? '先记录我的判断，再调用 AI'
        : session?.mode === 'blind' && futureSeen ? '以学习模式识别信号 · 调用一次 AI'
          : '识别这个信号 · 调用一次 AI';
  action.disabled = pending || !signal || delta < 0;
  byId('replay-signal-return').disabled = pending || !signal || delta === 0 || session.mode === 'blind' && delta > 0;
  byId('replay-signal-input').hidden = !observation;
  byId('replay-signal-input').disabled = pending;
  byId('replay-signal-help').textContent = signal
    ? delta < 0 ? '先播放到信号收盘，再识别。当播放到该信号时会自动暂停。'
      : blindNeedsHuman ? '盲审先记录你的判断。点击上方按钮会冻结信号图并打开判断表单；保存后再点击调用 AI。'
        : `${futureSeen ? '你已看过后续行情，本次仅作学习。' : ''}AI 只看信号收盘及以前的 120 根 K 线；播放不调用模型，已有调用不自动重试。`
    : '从左侧历史信号案例点击进入；自由选时回放可在下方冻结当前图表。顶部 API 已连接不代表 AI 已识别。';
  const result = byId('replay-signal-result');
  result.textContent = run?.decision
    ? `AI：${VERDICT_LABELS[run.decision.verdict] || '未判定'} · ${STATE_LABELS[run.decision.current_state] || '状态未提供'}。${run.decision.summary || ''}`
    : failed ? `本次未取得有效判断：${(run?.error || '打开调用记录查看原因').replace(/[。.]$/, '')}。失败不代表形态不符合。`
      : attempted ? '已有调用记录，点击“查看 AI 结果”读取详情；不会重新请求模型。' : '';
  result.hidden = !result.textContent;
  showInline('replay-signal-error', state.signalError);
}

function renderSession() {
  const session = state.session;
  const observation = state.activeObservation;
  const hasSession = Boolean(session);
  const mode = session?.mode;
  byId('replay-chart-heading').textContent = hasSession ? `${session.symbol} · ${session.timeframe}` : '选择或创建一个回放会话';
  byId('replay-mode-badge').textContent = mode === 'blind' ? '盲审 · 只向前' : mode === 'free' ? '自由回放' : '';
  setHidden(byId('replay-mode-badge'), !hasSession);
  byId('replay-cursor-label').textContent = hasSession
    ? `当前游标 ${formatTime(session.cursor_ms)} · 已看至 ${formatTime(session.seen_until_ms)}`
    : '尚未载入历史图表';
  byId('replay-chart-caption').textContent = hasSession ? '当前历史前缀 · SPIKE 箭头是原策略信号，AI 结论显示在上方 · 北京时间' : '图表显示已披露的历史前缀 · 北京时间';
  setHidden(byId('replay-chart-empty'), hasSession);
  const chartElement = byId('replay-chart');
  const standardImageElement = byId('replay-standard-image');
  setHidden(chartElement, !hasSession || !replayActive);
  const standardImage = byId('replay-standard-image-src');
  const observationImage = byId('replay-observation-image');
  setHidden(standardImageElement, true);
  standardImage.removeAttribute('src');
  byId('replay-freeze').disabled = Boolean(state.busy) || !hasSession;
  byId('replay-freeze').textContent = state.busy === 'freeze' ? '正在冻结…' : '冻结当前图表';
  byId('replay-observation-status').textContent = observation ? observation.status || '已冻结' : '';
  setHidden(byId('replay-observation-status'), !observation);
  byId('replay-observation-time').textContent = observation ? formatTime(observation.cursor_ms) : '';
  setHidden(byId('replay-observation-empty'), Boolean(observation));
  const imageAvailable = Boolean(observation
    && bindObservationImage(observationImage, observation, '冻结回放输入：模型实际看到的标准图'));
  if (!imageAvailable) observationImage.removeAttribute('src');
  setHidden(byId('replay-observation-figure'), !imageAvailable);
  const originalUrl = observation?.id ? apiImageUrl(observation.image_url) : '';
  const originalLink = byId('replay-view-original');
  originalLink.href = originalUrl || '#';
  setHidden(originalLink, !originalUrl);
  const exportLink = byId('replay-export-observation');
  const exportable = Boolean(observation?.id);
  exportLink.href = exportable ? `${REPLAY_BASE}/observations/${encodeURIComponent(observation.id)}/export` : '#';
  exportLink.download = exportable ? `replay-${observation.id}.json` : '';
  setHidden(exportLink, !exportable);
  const imageStatus = byId('replay-image-status');
  const imageStatusText = state.imageState.id === observation?.id ? state.imageState.status : 'empty';
  imageStatus.textContent = !observation ? '' : imageStatusText === 'loaded' ? '服务器冻结原图已成功载入，可提交模型复核。'
    : imageStatusText === 'error' ? '冻结原图无法载入（可能已失效或返回 404）；模型复核保持禁用。'
      : '正在载入服务器冻结原图；载入成功前模型复核保持禁用。';
  setHidden(imageStatus, !observation);
  byId('replay-observation-meta').innerHTML = observation
    ? `<details class="replay-technical"><summary>输入来源、参考版本与哈希</summary><p>${escapeHtml(observation.mode === 'blind' ? '盲审' : '自由回放')} · 参考图版本 ${escapeHtml(observation.reference_revision ?? '未提供')}</p><code>图片 SHA-256 ${escapeHtml(observation.image_sha256 || '未提供')}<br>图表 SHA-256 ${escapeHtml(observation.chart_sha256 || '未提供')}</code></details>`
    : '';
  setHidden(byId('replay-observation-meta'), !observation);
  showInline('replay-frozen-error', state.errors.freeze);
  showInline('replay-observations-error', state.errors.observations);

  const blind = mode === 'blind';
  const cursor = Number(session?.cursor_ms);
  const first = Number(session?.first_cursor_ms);
  const last = Number(session?.last_cursor_ms);
  const moving = Boolean(state.busy);
  const canGoBack = hasSession && !blind && Number.isFinite(first) && cursor > first;
  const canGoForward = hasSession && Number.isFinite(last) && cursor < last;
  byId('replay-free-jump').hidden = !hasSession || blind;
  byId('replay-target-time').disabled = moving || !hasSession || blind;
  byId('replay-jump').disabled = moving || !hasSession || blind || !byId('replay-target-time').value;
  document.querySelectorAll('[data-replay-step]').forEach((button) => {
    const amount = Number(button.dataset.replayStep);
    button.disabled = moving || !hasSession || amount < 0 && !canGoBack || amount > 0 && !canGoForward;
  });
  byId('replay-play').disabled = moving || !hasSession || !canGoForward || replayPlayback.isRunning();
  byId('replay-pause').disabled = !replayPlayback.isRunning();
  byId('replay-speed').disabled = moving || !hasSession;
  const start = byId('replay-start-time');
  const jump = byId('replay-target-time');
  if (!hasSession) replayTargetSessionId = null;
  else {
    jump.value = replayTargetValueForSession(replayTargetSessionId, session.id, jump.value, session.cursor_ms);
    replayTargetSessionId = session.id;
  }
  if (!hasSession && !start.value) start.value = msToShanghaiInput(Date.now());

  const stateLabel = byId('replay-human-state');
  byId('replay-save-human').disabled = Boolean(state.busy) || !observation || !canSaveFirstJudgment(session, observation) || !stateLabel.value;
  const criteria = byId('replay-criteria');
  criteria.disabled = Boolean(state.busy);
  byId('replay-save-followup').textContent = state.busy === 'followup' ? '正在保存…' : '保存回访';
  showInline('replay-observations-error', state.errors.observations || state.observationError);
  if (hasSession && replayActive) updateReplayChart(session);
  renderSignalGuide();
  renderReplayCaseOutcome();
  renderHuman();
  renderAi();
  renderComparison();
  renderFollowups();
  renderObservations();
}

function renderReplay() {
  if (!initialized) return;
  renderCatalog();
  renderCaseBrowser();
  renderSessions();
  renderSession();
  byId('replay-status').textContent = state.busy
    ? ({ create: '正在创建会话…', session: '正在恢复会话…', move: '正在读取历史前缀…', freeze: '正在保存冻结图…', judgment: '正在保存人工判断…', analyze: '正在请求一次模型判断…', comparison_prepare: '正在准备冻结输入对照包…', comparison_vision: '正在调用纯图像组…', comparison_text: '正在调用纯数值组…', comparison_hybrid: '正在调用图文组…', followup: '正在保存回访…', observation: '正在读取观察…', outcome: '正在揭晓历史交易结果…' })[state.busy] || '正在处理…'
    : state.session ? `${state.session.mode === 'blind' ? '盲审' : '自由回放'} · ${observations().length} 条冻结观察 · 推进不调用模型`
      : state.catalog.length ? `${state.catalog.length} 项历史行情可用 · 北京时间` : '等待历史数据目录';
  showInline('replay-error', state.globalError);
  showInline('replay-notice', state.notice, 'info');
}

function chartPalette() {
  const axisDate = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit' });
  const axisTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false });
  return {
    layout: { background: { type: 'solid', color: token('--surface') }, textColor: token('--text-muted'), fontSize: 13, fontFamily: getComputedStyle(document.body).fontFamily },
    grid: { vertLines: { color: token('--chart-grid') }, horzLines: { color: token('--chart-grid') } },
    rightPriceScale: { borderColor: token('--border'), scaleMargins: { top: .12, bottom: .12 } },
    timeScale: { borderColor: token('--border'), timeVisible: true, secondsVisible: false, rightOffset: 4, minBarSpacing: 2,
      tickMarkFormatter: (time, type) => (type < 3 ? axisDate : axisTime).format(new Date(time * 1000)) },
    crosshair: { mode: 0, vertLine: { color: token('--text-muted'), labelBackgroundColor: token('--text-soft') }, horzLine: { color: token('--text-muted'), labelBackgroundColor: token('--text-soft') } },
  };
}

function updateReplaySeries(rows) {
  if (!replayCandles) return;
  const priceFormat = replayPriceFormat(rows);
  replayCandles.applyOptions({ priceFormat });
  replayAverages.forEach((series) => series.applyOptions({ priceFormat }));
  replayCandles.setData(rows.map((row) => ({ time: row.t / 1000, open: row.o, high: row.h, low: row.l, close: row.c })));
  replayAverages.forEach((series, index) => {
    const field = MA_KEYS[index];
    series.setData(rows.map((row) => Number.isFinite(row[field]) ? { time: row.t / 1000, value: row[field] } : { time: row.t / 1000 }));
  });
}

function updateReplayChart(session) {
  const rows = disclosedReplayRows(session);
  if (!rows.length) return;
  if (!window.LightweightCharts) {
    state.observationError = 'TradingView 图表组件尚未载入，请刷新页面后重试。';
    showInline('replay-observations-error', state.observationError);
    return;
  }
  const host = byId('replay-chart');
  if (!replayChart) {
    const width = host.clientWidth || host.parentElement?.clientWidth || 0;
    const height = host.clientHeight || host.parentElement?.clientHeight || 0;
    if (!width || !height) return;
    replayChart = window.LightweightCharts.createChart(host, {
      ...chartPalette(), width, height, localization: { locale: 'zh-CN' }, handleScroll: { vertTouchDrag: false },
    });
    replayCandles = replayChart.addCandlestickSeries({ borderVisible: false, upColor: token('--chart-up'), downColor: token('--chart-down'), wickUpColor: token('--chart-up'), wickDownColor: token('--chart-down') });
    replayAverages = MA_KEYS.map((key) => replayChart.addLineSeries({ color: token(`--ma-${key}`), lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }));
    replayResizeObserver = new ResizeObserver(() => {
      if (!replayChart || !host.clientWidth || !host.clientHeight) return;
      replayChart.resize(host.clientWidth, host.clientHeight);
    });
    replayResizeObserver.observe(host);
  }
  if (session.chart?.chart_sha256 && session.chart.chart_sha256 === chartDataHash) {
    replayCandles.setMarkers(replaySignalMarkers(session, token('--accent')));
    return;
  }
  updateReplaySeries(rows);
  replayCandles.setMarkers(replaySignalMarkers(session, token('--accent')));
  replayChart.applyOptions(chartPalette());
  replayChart.timeScale().fitContent();
  chartDataHash = session.chart?.chart_sha256 || '';
}

function applyReplayTheme() {
  if (!replayChart) return;
  replayChart.applyOptions(chartPalette());
  replayCandles?.applyOptions({ upColor: token('--chart-up'), downColor: token('--chart-down'), wickUpColor: token('--chart-up'), wickDownColor: token('--chart-down') });
  replayAverages.forEach((series, index) => series.applyOptions({ color: token(`--ma-${MA_KEYS[index]}`) }));
  replayCandles?.setMarkers(replaySignalMarkers(state.session, token('--accent')));
}

function teardownReplayChart() {
  replayResizeObserver?.disconnect();
  replayChart?.remove();
  replayChart = null;
  replayCandles = null;
  replayAverages = [];
  replayResizeObserver = null;
  chartDataHash = '';
}

async function loadCatalog() {
  state.catalogLoading = true;
  state.catalogError = '';
  renderReplay();
  try {
    const payload = await apiJson('/replay/catalog');
    state.catalog = Array.isArray(payload.items) ? payload.items.filter((item) => item && item.symbol && item.timeframe) : [];
    state.catalogWarning = typeof payload.warning === 'string' ? payload.warning : '';
  } catch (error) {
    state.catalog = [];
    state.catalogError = error.message || '历史数据目录读取失败。';
  } finally {
    state.catalogLoading = false;
    renderReplay();
    if (state.catalog.length) loadCoverage();
  }
}

async function loadReplayCases() {
  const browser = state.caseBrowser;
  const tokenValue = ++state.caseRequestToken;
  browser.loading = true;
  browser.error = '';
  renderReplay();
  const query = buildReplayCasesQuery(browser);
  try {
    const payload = await apiJson(`/replay/cases?${query}`);
    if (tokenValue !== state.caseRequestToken) return;
    browser.datasets = Array.isArray(payload.datasets)
      ? payload.datasets.filter((item) => item && typeof item.id === 'string') : [];
    if (typeof payload.dataset === 'string') browser.dataset = payload.dataset;
    browser.items = Array.isArray(payload.items) ? payload.items.filter((item) => item && typeof item === 'object' && item.id) : [];
    browser.total = Math.max(0, Number(payload.total) || 0);
    browser.offset = Math.max(0, Number(payload.offset) || 0);
    browser.limit = Math.max(1, Number(payload.limit) || 20);
    browser.counts = payload.counts && typeof payload.counts === 'object' ? payload.counts : {};
    browser.warning = typeof payload.warning === 'string' ? payload.warning : '';
  } catch (error) {
    if (tokenValue === state.caseRequestToken) {
      browser.items = [];
      browser.total = 0;
      browser.error = error.message || '历史信号案例读取失败。';
    }
  } finally {
    if (tokenValue === state.caseRequestToken) {
      browser.loading = false;
      renderReplay();
    }
  }
}

async function loadSessions(preferredId = '') {
  const tokenValue = ++state.sessionListRequestToken;
  state.sessionsLoading = true;
  state.sessionsError = '';
  renderReplay();
  try {
    const payload = await apiJson('/replay/sessions');
    if (tokenValue !== state.sessionListRequestToken) return;
    state.sessions = Array.isArray(payload.items) ? payload.items.filter((item) => item && typeof item === 'object') : [];
    state.sessionsError = '';
    if (preferredId && state.session?.id === preferredId) {
      const current = state.sessions.find((item) => item.id === preferredId);
      if (current && state.session) state.session = { ...state.session, ...current };
    }
  } catch (error) {
    if (tokenValue === state.sessionListRequestToken) state.sessionsError = error.message || '历史会话读取失败。';
  } finally {
    if (tokenValue === state.sessionListRequestToken) {
      state.sessionsLoading = false;
      renderReplay();
    }
  }
}

async function loadSession(id) {
  if (!id || state.busy) return;
  pauseReplay();
  const operation = setBusy('session');
  if (!operation) return;
  const tokenValue = ++state.sessionRequestToken;
  state.pendingSessionId = id;
  const ticket = operation.ticket;
  state.globalError = '';
  state.observationError = '';
  state.caseOutcomeError = '';
  state.signalError = '';
  try {
    const session = await apiJson(`/replay/sessions/${encodeURIComponent(id)}`);
    if (!sameSessionResponse(operation, tokenValue, id)) return;
    state.session = session;
    state.pendingSessionId = id;
    setActiveObservation(null);
    state.errors = { freeze: '', human: '', ai: '', followup: '', observations: '' };
    state.observationRequestToken += 1;
    state.notice = '历史会话已恢复；图表只绘制服务端返回的当前已披露前缀。';
    renderReplay();
  } catch (error) {
    if (ticket === state.operationToken && tokenValue === state.sessionRequestToken) state.globalError = error.message || '回放会话读取失败。';
  } finally {
    if (ticket === state.operationToken && tokenValue === state.sessionRequestToken) finishBusy(operation);
  }
  const signalObservation = replaySignalObservation(state.session, null);
  if (state.session?.id === id && signalObservation && !state.busy) await loadObservation(signalObservation.id);
}

async function createSession() {
  if (state.busy) return;
  const startMs = shanghaiInputToMs(byId('replay-start-time').value);
  if (startMs === null) {
    state.globalError = '开始时间无效，请填写北京时间。';
    renderReplay();
    return;
  }
  const symbol = currentCatalogSymbol();
  const timeframe = byId('replay-timeframe').value;
  const mode = byId('replay-mode').value;
  if (!symbol || !timeframe || !['free', 'blind'].includes(mode)) {
    state.globalError = '请先选择可用币种、周期和回放方式。';
    renderReplay();
    return;
  }
  pauseReplay();
  const operation = setBusy('create', { sessionId: null });
  if (!operation) return;
  state.globalError = '';
  state.notice = '';
  try {
    const session = await postJson('/replay/sessions', { symbol, timeframe, start_ms: startMs, mode });
    if (operation.ticket !== state.operationToken || state.busy !== 'create') return;
    state.session = session;
    state.caseOutcomeError = '';
    state.signalError = '';
    setActiveObservation(null);
    state.errors = { freeze: '', human: '', ai: '', followup: '', observations: '' };
    state.observationError = '';
    state.sessionRequestToken += 1;
    state.pendingSessionId = session.id;
    state.observationRequestToken += 1;
    state.notice = mode === 'free'
      ? '自由回放已创建。你可以前后跳转；它不代表随机盲测。'
      : '盲审会话已创建。可以逐步播放；冻结样本后，需先保存人工判断才能交给模型。';
    renderReplay();
  } catch (error) {
    if (operation.ticket === state.operationToken) state.globalError = error.message || '无法创建历史回放。';
  } finally {
    finishBusy(operation);
  }
  if (!state.busy) loadSessions(state.session?.id || '');
}

async function createCaseSession(caseId) {
  const browser = state.caseBrowser;
  const item = browser.items.find((candidate) => candidate.id === caseId);
  if (!item || state.busy) return;
  pauseReplay();
  const body = buildReplayCaseSessionBody(browser.bucket, byId('replay-mode').value);
  const operation = setBusy('create', { sessionId: null });
  if (!operation) return;
  state.globalError = '';
  state.caseOutcomeError = '';
  state.signalError = '';
  state.notice = '';
  try {
    const session = await postJson(`/replay/cases/${encodeURIComponent(caseId)}/sessions`, body);
    if (operation.ticket !== state.operationToken || state.busy !== 'create') return;
    state.session = session;
    setActiveObservation(null);
    state.errors = { freeze: '', human: '', ai: '', followup: '', observations: '' };
    state.observationError = '';
    state.sessionRequestToken += 1;
    state.pendingSessionId = session.id;
    state.observationRequestToken += 1;
    const caseWarning = typeof session.case?.warning === 'string' ? session.case.warning.trim() : '';
    state.notice = body.selection_bucket !== 'all'
      ? caseWarning || '结果筛选已让你预先知道案例类别；本次按自由回放创建，人工判断只作学习标签。'
      : session.mode === 'blind'
        ? caseWarning || '历史案例已从信号收盘时点开始盲审回放。列表未披露未来结果；可先保存人工判断，再手动揭晓。'
        : session.case?.outcome_known
          ? caseWarning || '历史案例已从信号收盘时点开始自由回放；该事件此前已有结果暴露，不能再作为独立盲审。'
          : '历史案例已从信号收盘时点开始自由回放；自由回放不代表独立盲审。';
    renderReplay();
  } catch (error) {
    if (operation.ticket === state.operationToken) state.globalError = error.message || '无法创建历史案例回放。';
  } finally {
    finishBusy(operation);
  }
  if (!state.busy) loadSessions(state.session?.id || '');
  if (state.session?.case?.id === caseId) byId('replay-signal-title').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function revealCaseOutcome() {
  const session = state.session;
  if (!session?.case || state.busy) return;
  pauseReplay();
  const operation = setBusy('outcome', { sessionId: session.id });
  if (!operation) return;
  state.caseOutcomeError = '';
  state.globalError = '';
  try {
    const updated = await postJson(`/replay/sessions/${encodeURIComponent(session.id)}/outcome`, {});
    if (!operationIsCurrent(operation)) return;
    state.session = updated;
    state.notice = '历史交易结果已揭晓并记录结果暴露；这是历史策略账本，不等同模型预测收益。';
    renderReplay();
  } catch (error) {
    if (operationIsCurrent(operation)) state.caseOutcomeError = error.message || '历史交易结果未能揭晓。';
  } finally {
    finishBusy(operation);
  }
}

async function moveReplay(movement, { fromPlayback = false } = {}) {
  if (!state.session || state.busy) return false;
  const body = buildReplayMoveBody(state.session, movement);
  if (!body) return false;
  if (!fromPlayback) pauseReplay();
  const sessionId = state.session.id;
  const operation = setBusy('move');
  if (!operation) return false;
  state.globalError = '';
  state.notice = '';
  try {
    const session = await postJson(`/replay/sessions/${encodeURIComponent(sessionId)}/move`, body);
    if (!operationIsCurrent(operation)) return false;
    state.session = session;
    state.notice = session.seen_until_ms > body.expected_cursor_ms
      ? '游标已推进。若某条更早观察尚未保存人工判断，现在不能再补记为独立判断。' : '';
    if (Number(session.cursor_ms) >= Number(session.last_cursor_ms)) state.notice = '已到本次连续历史范围末端。';
    if (fromPlayback && Number(body.expected_cursor_ms) < Number(session.case?.signal_close_ms)
        && Number(session.cursor_ms) >= Number(session.case.signal_close_ms)) {
      replayPlayback.pause();
      state.notice = '已到原策略信号，播放已暂停。点击图表上方的识别按钮进行判断。';
    }
    renderReplay();
    return true;
  } catch (error) {
    if (operationIsCurrent(operation)) state.globalError = error.message || '历史游标移动失败。';
    return false;
  } finally {
    finishBusy(operation);
  }
}

async function reviewSignal() {
  const session = state.session;
  if (!session?.case || state.busy || Number(session.cursor_ms) < Number(session.case.signal_close_ms)) return;
  pauseReplay();
  state.signalError = '';
  const existing = replaySignalObservation(session, state.activeObservation);
  if (existing?.run_id) {
    await loadObservation(existing.id);
    state.signalError = state.errors.observations || '';
    renderReplay();
    return;
  }
  const criteria = byId('replay-criteria').value.trim();
  if (criteria.length < 10 && !existing) {
    state.signalError = '请先在下方填写至少 10 个字符的识别规则。';
    renderReplay();
    byId('replay-criteria').focus();
    return;
  }
  const retrospective = session.mode === 'blind' && Number(session.seen_until_ms) > Number(session.case.signal_close_ms);
  state.signalPhase = 'freeze';
  const operation = setBusy('signal');
  let observation = null;
  let needsHuman = false;
  try {
    if (existing) observation = await apiJson(`/replay/observations/${encodeURIComponent(existing.id)}`);
    if (!operationIsCurrent(operation)) return;
    if (!observation || retrospective && observation.mode === 'blind' && !observation.human) {
      const references = await apiJson('/references');
      if (!operationIsCurrent(operation)) return;
      observation = await postJson(`/replay/sessions/${encodeURIComponent(session.id)}/freeze`, {
        expected_cursor_ms: Number(session.cursor_ms), criteria,
        reference_revision: Number(references.revision), at_signal: true, retrospective,
      });
    }
    if (!operationIsCurrent(operation)) return;
    setActiveObservation(observation);
    addObservationSummary(observation);
    needsHuman = replayRequiresHuman(session, observation) && !observation.human;
    if (needsHuman) {
      state.notice = '信号时点已冻结。先记录你的判断，保存后再点击图表上方按钮调用 AI。';
      return;
    }
    if (!observation.run_id) {
      state.signalPhase = 'analyze';
      renderReplay();
      observation = await postJson(`/replay/observations/${encodeURIComponent(observation.id)}/analyze`, {});
      if (!operationIsCurrent(operation)) return;
      setActiveObservation(observation);
      addObservationSummary(observation);
    }
    state.notice = '信号识别请求已完成。AI 输入截止原信号收盘；可继续播放查看后续行情。';
  } catch (error) {
    if (operationIsCurrent(operation)) {
      state.signalError = error.message || '读取信号或调用模型失败。';
      // A lost HTTP response may follow a persisted attempt. Recover its receipt without another call.
      if (observation?.id) {
        try {
          const recovered = await apiJson(`/replay/observations/${encodeURIComponent(observation.id)}`);
          if (operationIsCurrent(operation)) { setActiveObservation(recovered); addObservationSummary(recovered); }
        } catch { /* Keep the original actionable error. */ }
      }
    }
  } finally {
    state.signalPhase = '';
    finishBusy(operation);
    if (needsHuman && state.session?.id === session.id) {
      byId('replay-human-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
      byId('replay-human-state').focus({ preventScroll: true });
    }
    loadSessions(session.id);
  }
}

async function returnToSignal() {
  const session = state.session;
  if (!session?.case || state.busy) return;
  const cutoff = Number(session.case.signal_close_ms);
  if (session.mode === 'blind') {
    // A blind replay cannot go backwards; forward playback pauses exactly at its signal.
    if (Number(session.cursor_ms) < cutoff) startReplay();
    return;
  }
  await moveReplay({ target_ms: cutoff });
}

async function showSignalInput() {
  const observation = replaySignalObservation(state.session, state.activeObservation);
  if (!observation || state.busy) return;
  if (state.activeObservation?.id !== observation.id) await loadObservation(observation.id);
  byId('replay-observation-heading').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function freezeCurrent() {
  if (!state.session || state.busy) return;
  pauseReplay();
  const criteria = byId('replay-criteria').value.trim();
  if (criteria.length < 10) {
    state.errors.freeze = '请填写至少 10 个字符的形态规则。';
    renderReplay();
    return;
  }
  const sessionId = state.session.id;
  const cursor = Number(state.session.cursor_ms);
  const operation = setBusy('freeze');
  if (!operation) return;
  state.globalError = '';
  state.errors.freeze = '';
  try {
    const references = await apiJson('/references');
    if (!operationIsCurrent(operation)) return;
    const revision = Number.isSafeInteger(Number(references.revision)) ? Number(references.revision) : 0;
    const observation = await postJson(`/replay/sessions/${encodeURIComponent(sessionId)}/freeze`, {
      expected_cursor_ms: cursor, criteria, reference_revision: revision,
    });
    if (!operationIsCurrent(operation)) return;
    setActiveObservation(observation);
    state.errors.observations = '';
    state.observationError = '';
    addObservationSummary(observation);
    state.notice = observation.independence === 'future_seen_in_session'
      ? '冻结图已保存，但此时点之后的 K 线已被看过，不能作为独立盲审标签。'
      : '服务器标准图已冻结；模型若调用，将使用这张原图与冻结时的规则和参考版本。';
    renderReplay();
    loadSessions(sessionId);
  } catch (error) {
    if (operationIsCurrent(operation)) state.errors.freeze = error.message || '冻结历史图表失败。';
  } finally {
    finishBusy(operation);
  }
}

function addObservationSummary(observation) {
  if (!state.session || !observation?.id) return;
  const items = observations().filter((item) => item.id !== observation.id);
  items.push({
    id: observation.id, cursor_ms: observation.cursor_ms, image_url: observation.image_url,
    human: observation.human, run_id: observation.run_id, status: observation.status,
    comparison_requested: Boolean(observation.comparison_requested),
  });
  items.sort((left, right) => Number(left.cursor_ms) - Number(right.cursor_ms));
  state.session = { ...state.session, observations: items };
}

async function loadObservation(id) {
  if (!id || !state.session || state.busy) return;
  const sessionId = state.session.id;
  setActiveObservation(null);
  state.errors = { freeze: '', human: '', ai: '', followup: '', observations: '' };
  const operation = setBusy('observation');
  if (!operation) return;
  const tokenValue = ++state.observationRequestToken;
  state.observationError = '';
  try {
    const observation = await apiJson(`/replay/observations/${encodeURIComponent(id)}`);
    if (!sameObservationResponse(operation, tokenValue, sessionId) || observation.session_id !== sessionId) return;
    setActiveObservation(observation);
    addObservationSummary(observation);
    renderReplay();
  } catch (error) {
    if (operationIsCurrent(operation) && tokenValue === state.observationRequestToken) state.errors.observations = error.message || '冻结观察读取失败。';
  } finally {
    if (operation.ticket === state.operationToken && tokenValue === state.observationRequestToken) finishBusy(operation);
  }
}

function sameComparisonResponse(operation, observationToken, observationId) {
  return comparisonResponseIsCurrent(
    { token: operation?.ticket, sessionId: operation?.sessionId, observationId },
    { token: state.operationToken, sessionId: state.session?.id || null, observationId: state.activeObservation?.id },
  ) && state.busy === operation?.action && observationToken === state.observationRequestToken;
}

async function prepareComparison() {
  const observation = state.activeObservation;
  const session = state.session;
  if (!comparisonCanPrepare(session, observation, state.imageState) || state.busy) {
    if (observation && (!observation.image_url || state.imageState.id === observation.id && state.imageState.status === 'error')) {
      state.comparisonError = '原冻结图缺失或无法载入，无法复现三组对照。';
      renderReplay();
    }
    return;
  }
  pauseReplay();
  const observationId = observation.id;
  const observationToken = state.observationRequestToken;
  const operation = setBusy('comparison_prepare', { sessionId: session.id });
  if (!operation) return;
  state.comparisonError = '';
  try {
    const updated = await postJson(`/replay/observations/${encodeURIComponent(observationId)}/comparison`, {});
    if (!sameComparisonResponse(operation, observationToken, observationId)) return;
    setActiveObservation(updated);
    addObservationSummary(updated);
    state.notice = updated.comparison_requested
      ? '对照状态已刷新；已有请求和结果保留，没有新增模型调用。'
      : '冻结输入对照包已准备；尚未调用模型。三组可分别手动调用一次。';
    renderReplay();
  } catch (error) {
    if (sameComparisonResponse(operation, observationToken, observationId)) {
      state.comparisonError = error.message || '图文输入对照包准备失败。';
    }
  } finally {
    finishBusy(operation);
  }
}

async function analyzeComparisonArm(mode) {
  const observation = state.activeObservation;
  const session = state.session;
  if (!comparisonCanRunArm(mode, session, observation, state.imageState) || state.busy) {
    if (observation && (!observation.image_url || state.imageState.id === observation.id && state.imageState.status === 'error')) {
      state.comparisonError = '原冻结图缺失或无法载入，无法复现三组对照；所有组已拦截。';
    } else if (observation && replayRequiresHuman(session, observation) && !observation.human) {
      state.comparisonError = '盲审观察必须先保存人工判断，之后才能调用对照组。';
    }
    renderReplay();
    return;
  }
  pauseReplay();
  const observationId = observation.id;
  const observationToken = state.observationRequestToken;
  const operation = setBusy(`comparison_${mode}`, { sessionId: session.id });
  if (!operation) return;
  state.comparisonError = '';
  try {
    const updated = await postJson(`/replay/observations/${encodeURIComponent(observationId)}/comparison/${mode}/analyze`, {});
    if (!sameComparisonResponse(operation, observationToken, observationId)) return;
    setActiveObservation(updated);
    addObservationSummary(updated);
    state.notice = `${COMPARISON_MODE_LABELS[mode]}组请求已返回；该组只调用一次，不自动重试。`;
    renderReplay();
  } catch (error) {
    if (sameComparisonResponse(operation, observationToken, observationId)) {
      state.comparisonError = `${error.message || '对照组请求失败。'} 已提交的组不会自动重试；请刷新观察确认服务端状态。`;
    }
  } finally {
    finishBusy(operation);
  }
}

async function saveJudgment() {
  const observation = state.activeObservation;
  const session = state.session;
  if (!canSaveFirstJudgment(session, observation) || state.busy) return;
  const currentState = byId('replay-human-state').value;
  const side = byId('replay-human-side').value;
  if (!Object.hasOwn(STATE_LABELS, currentState)) {
    state.errors.human = '请选择一种形态状态。';
    renderReplay();
    return;
  }
  const operation = setBusy('judgment');
  if (!operation) return;
  state.errors.human = '';
  try {
    const updated = await postJson(`/replay/observations/${encodeURIComponent(observation.id)}/judgment`, {
      current_state: currentState, side, note: byId('replay-human-note').value.trim(),
    });
    if (!operationIsCurrent(operation)) return;
    setActiveObservation(updated);
    addObservationSummary(updated);
    state.notice = updated.independence === 'outcome_known_before_judgment'
      ? '人工判断已作为历史结果已知案例的学习标签保存；它不构成独立盲审。模型判断仍需你单独手动调用。'
      : '首次人工判断已保存；模型判断仍需你单独手动调用。';
    byId('replay-human-state').value = '';
    byId('replay-human-side').value = 'unknown';
    byId('replay-human-note').value = '';
    renderReplay();
    loadSessions(session.id);
  } catch (error) {
    if (operationIsCurrent(operation)) state.errors.human = error.message || '人工判断未保存。';
  } finally {
    finishBusy(operation);
  }
}

async function analyzeObservation() {
  const observation = state.activeObservation;
  const imageLoaded = canAnalyzeObservation(state.session, observation, state.imageState);
  if (!observation || !observation.image_url || observation.run_id || observation.run || state.busy) return;
  if (!imageLoaded) {
    state.errors.ai = state.imageState.status === 'error'
      ? '冻结原图无法载入（可能已失效或返回 404），不能调用模型。'
      : '请等待冻结原图成功载入后再调用模型。';
    renderReplay();
    return;
  }
  if (replayRequiresHuman(state.session, observation) && !observation.human) {
    state.errors.ai = '盲审观察必须先保存人工判断。';
    renderReplay();
    return;
  }
  const operation = setBusy('analyze');
  if (!operation) return;
  state.globalError = '';
  state.errors.ai = '';
  try {
    const updated = await postJson(`/replay/observations/${encodeURIComponent(observation.id)}/analyze`, {});
    if (!operationIsCurrent(operation)) return;
    setActiveObservation(updated);
    addObservationSummary(updated);
    state.notice = '模型请求已完成；没有自动重试。';
    renderReplay();
    loadSessions(state.session.id);
  } catch (error) {
    if (operationIsCurrent(operation)) state.errors.ai = error.message || '模型请求失败。';
  } finally {
    finishBusy(operation);
  }
}

async function saveFollowup() {
  const observation = state.activeObservation;
  const session = state.session;
  if (!observation || !session || state.busy) return;
  if (Number(session.cursor_ms) <= Number(observation.cursor_ms) || !replayObservationHasJudgmentOrComparison(observation)) {
    state.errors.followup = '请先完成原观察判断、调用模型或图文对照，并将游标移到更晚时间。';
    renderReplay();
    return;
  }
  const operation = setBusy('followup');
  if (!operation) return;
  state.errors.followup = '';
  try {
    const updated = await postJson(`/replay/observations/${encodeURIComponent(observation.id)}/followup`, {
      expected_cursor_ms: Number(session.cursor_ms), note: byId('replay-followup-note').value.trim(),
    });
    if (!operationIsCurrent(operation)) return;
    setActiveObservation(updated);
    addObservationSummary(updated);
    state.notice = '后续回访已关联到原冻结观察；价格变化是描述性数据，不代表交易盈亏。';
    byId('replay-followup-note').value = '';
    renderReplay();
  } catch (error) {
    if (operationIsCurrent(operation)) state.errors.followup = error.message || '后续回访未保存。';
  } finally {
    finishBusy(operation);
  }
}

function pauseReplay() {
  replayPlayback.pause();
  if (initialized) renderReplay();
}

function startReplay() {
  if (!state.session || state.busy || replayPlayback.isRunning()) return;
  if (Number(state.session.cursor_ms) >= Number(state.session.last_cursor_ms)) {
    state.notice = '已到本次连续历史范围末端。';
    renderReplay();
    return;
  }
  state.globalError = '';
  state.notice = '正在按选定速度推进历史游标；播放只读取回放数据，不调用模型。';
  if (!replayPlayback.play()) return;
  renderReplay();
}

function updateModeHelp() {
  const mode = byId('replay-mode').value;
  byId('replay-mode-help').textContent = mode === 'blind'
    ? '盲审只允许向前推进；冻结样本后，先保存人工判断才能调用模型。继续推进后，未判断的旧观察不能再补记为独立判断。'
    : '自由回放可以前后跳转。若已看过后续 K 线，回到较早时点后不能再补记为独立判断；自由回放不代表随机盲测。';
}

function attachEvents() {
  byId('replay-case-dataset').addEventListener('change', () => {
    state.caseBrowser.dataset = byId('replay-case-dataset').value;
    state.caseBrowser.offset = 0;
    loadReplayCases();
  });
  byId('replay-case-bucket').addEventListener('change', () => {
    state.caseBrowser.bucket = byId('replay-case-bucket').value;
    state.caseBrowser.offset = 0;
    loadReplayCases();
  });
  const updateCaseTextFilter = () => {
    state.caseBrowser.symbol = byId('replay-case-symbol').value.trim();
    state.caseBrowser.timeframe = byId('replay-case-timeframe').value.trim();
    state.caseBrowser.offset = 0;
    if (caseFilterTimer) clearTimeout(caseFilterTimer);
    caseFilterTimer = setTimeout(() => {
      caseFilterTimer = null;
      loadReplayCases();
    }, 300);
  };
  byId('replay-case-symbol').addEventListener('input', updateCaseTextFilter);
  byId('replay-case-timeframe').addEventListener('input', updateCaseTextFilter);
  byId('replay-case-prev').addEventListener('click', () => {
    state.caseBrowser.offset = Math.max(0, state.caseBrowser.offset - state.caseBrowser.limit);
    loadReplayCases();
  });
  byId('replay-case-next').addEventListener('click', () => {
    if (state.caseBrowser.offset + state.caseBrowser.limit >= state.caseBrowser.total) return;
    state.caseBrowser.offset += state.caseBrowser.limit;
    loadReplayCases();
  });
  byId('replay-case-list').addEventListener('click', (event) => {
    const item = event.target.closest('[data-replay-case-id]');
    if (item) createCaseSession(item.dataset.replayCaseId);
  });
  byId('replay-case-outcome-reveal').addEventListener('click', revealCaseOutcome);
  byId('replay-signal-analyze').addEventListener('click', reviewSignal);
  byId('replay-signal-return').addEventListener('click', returnToSignal);
  byId('replay-signal-input').addEventListener('click', showSignalInput);
  byId('replay-symbol').addEventListener('change', () => { renderCatalog(); loadCoverage(); });
  byId('replay-timeframe').addEventListener('change', () => { renderCatalog(); loadCoverage(); });
  byId('replay-segment-select').addEventListener('change', selectCoverageSegment);
  byId('replay-start-time').addEventListener('input', () => { startTimeEdited = true; renderCatalog(); });
  byId('replay-mode').addEventListener('change', () => { updateModeHelp(); renderReplay(); });
  byId('replay-create-session').addEventListener('click', createSession);
  byId('replay-refresh-sessions').addEventListener('click', () => loadSessions(state.session?.id || ''));
  byId('replay-session-list').addEventListener('click', (event) => {
    const item = event.target.closest('[data-replay-session]');
    if (item) loadSession(item.dataset.replaySession);
  });
  byId('replay-observation-list').addEventListener('click', (event) => {
    const item = event.target.closest('[data-replay-observation]');
    if (item) loadObservation(item.dataset.replayObservation);
  });
  byId('replay-criteria').addEventListener('input', () => {
    state.criteriaEdited = true;
    byId('replay-freeze').disabled = Boolean(state.busy) || !state.session;
  });
  byId('replay-freeze').addEventListener('click', freezeCurrent);
  byId('replay-human-state').addEventListener('change', () => renderReplay());
  byId('replay-human-side').addEventListener('change', () => renderReplay());
  byId('replay-save-human').addEventListener('click', saveJudgment);
  byId('replay-analyze').addEventListener('click', analyzeObservation);
  byId('replay-comparison-prepare').addEventListener('click', prepareComparison);
  byId('replay-comparison-arms').addEventListener('click', (event) => {
    const button = event.target.closest('[data-comparison-arm]');
    if (button) analyzeComparisonArm(button.dataset.comparisonArm);
  });
  byId('replay-save-followup').addEventListener('click', saveFollowup);
  byId('replay-speed').addEventListener('change', () => {
    if (replayPlayback.isRunning()) {
      pauseReplay();
      state.notice = '播放速度已更改；当前播放已安全暂停，请重新开始。';
      renderReplay();
    }
  });
  byId('replay-play').addEventListener('click', startReplay);
  byId('replay-pause').addEventListener('click', pauseReplay);
  byId('replay-chart-stage').addEventListener('click', (event) => {
    const step = event.target.closest('[data-replay-step]');
    if (step) moveReplay({ steps: Number(step.dataset.replayStep) });
  });
  document.querySelectorAll('[data-replay-step]').forEach((button) => {
    if (button.parentElement?.id === 'replay-chart-stage') return;
    button.addEventListener('click', () => moveReplay({ steps: Number(button.dataset.replayStep) }));
  });
  byId('replay-jump').addEventListener('click', () => {
    const target = shanghaiInputToMs(byId('replay-target-time').value);
    if (target === null) {
      state.globalError = '跳转时间无效，请填写北京时间。';
      renderReplay();
      return;
    }
    moveReplay({ target_ms: target });
  });
  byId('replay-target-time').addEventListener('input', () => renderReplay());
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) pauseReplay();
  });
}

export function initReplay() {
  if (initialized) return;
  initialized = true;
  attachEvents();
  updateModeHelp();
  if (!replayThemeObserver) {
    replayThemeObserver = new MutationObserver(applyReplayTheme);
    replayThemeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  }
  const criteria = byId('replay-criteria');
  apiJson('/status').then((status) => {
    state.defaultCriteria = typeof status.default_criteria === 'string' ? status.default_criteria : '';
    if (!state.criteriaEdited && !criteria.value.trim()) criteria.value = state.defaultCriteria;
    renderReplay();
  }).catch((error) => {
    state.globalError = `默认识别规则读取失败：${error.message || '请检查本地 API。'}`;
    renderReplay();
  });
  if (!byId('replay-start-time').value) byId('replay-start-time').value = msToShanghaiInput(Date.now());
  renderReplay();
  loadCatalog();
  loadReplayCases();
  loadSessions().then(() => {
    if (!state.session && !state.busy && state.sessions[0]?.id) loadSession(state.sessions[0].id);
  });
}

export function setReplayActive(active) {
  replayActive = Boolean(active);
  if (!replayActive) pauseReplay();
  else {
    renderReplay();
    if (replayChart && byId('replay-chart').clientWidth && byId('replay-chart').clientHeight) {
      replayChart.resize(byId('replay-chart').clientWidth, byId('replay-chart').clientHeight);
    }
  }
}
