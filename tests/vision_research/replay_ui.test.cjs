const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '../..');
const sourcePath = path.join(root, 'yoyo/vision_research/static/replay.js');
const htmlPath = path.join(root, 'yoyo/vision_research/static/index.html');
const source = fs.readFileSync(sourcePath, 'utf8')
  .replace(/^export function /gm, 'function ')
  + '\n;globalThis.__replayApi = { shanghaiInputToMs, msToShanghaiInput, validReplayStep, buildReplayMoveBody, buildReplayCasesQuery, buildReplayCaseSessionBody, visibleReplayCaseOutcome, canRevealReplayCaseOutcome, replayCaseJudgmentOutcomeKnown, replayTargetValueForSession, replayOutcomeCodeLabel, canSaveFirstJudgment, canAnalyzeObservation, disclosedReplayRows, replayResponseIsCurrent, createReplayPlayback, defaultStartForSegment, independenceLabel };';

function loadApi() {
  const context = vm.createContext({ URLSearchParams });
  vm.runInContext(source, context, { filename: sourcePath });
  return context.__replayApi;
}

test('replay module references only IDs present in the page', () => {
  const html = fs.readFileSync(htmlPath, 'utf8');
  const declared = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]));
  const required = new Set([...source.matchAll(/byId\('([^']+)'\)/g)].map((match) => match[1]));
  const missing = [...required].filter((id) => !declared.has(id));
  assert.deepEqual(missing, []);
});

test('datetime-local values round-trip as Asia/Shanghai wall time', () => {
  const api = loadApi();
  const expected = Date.parse('2026-09-23T00:00:00Z');
  assert.equal(api.shanghaiInputToMs('2026-09-23T08:00'), expected);
  assert.equal(api.msToShanghaiInput(expected), '2026-09-23T08:00');
  assert.equal(api.shanghaiInputToMs('2026-02-30T08:00'), null);
});

test('blind movement is forward-only and chart rows remain exactly server supplied', () => {
  const api = loadApi();
  const session = { mode: 'blind', cursor_ms: 1000 };
  assert.deepEqual({ ...api.buildReplayMoveBody(session, { steps: 5 }) }, { expected_cursor_ms: 1000, steps: 5 });
  assert.equal(api.buildReplayMoveBody(session, { steps: -1 }), null);
  assert.equal(api.buildReplayMoveBody(session, { target_ms: 2000 }), null);
  assert.equal(api.buildReplayMoveBody({ ...session, mode: 'free' }, { target_ms: 2000 }).target_ms, 2000);
  for (const steps of [-10, -5, -1, 1, 5, 10]) assert.equal(api.validReplayStep(steps), true);
  assert.equal(api.validReplayStep(2), false);

  const rows = [{ t: 1, c: 10 }, { t: 2, c: 11 }, { t: 3, c: 12 }];
  assert.equal(api.disclosedReplayRows({ chart: { candles: rows } }), rows);
});

test('case filters build a stable query and result-filtered cases always use free replay', () => {
  const api = loadApi();
  assert.equal(api.buildReplayCasesQuery({ bucket: 'ge5', symbol: 'BTC/USDT', timeframe: '15m', offset: 20, limit: 20, dataset: 'gold set' }),
    'bucket=ge5&symbol=BTC%2FUSDT&timeframe=15m&offset=20&limit=20&dataset=gold+set');
  assert.deepEqual({ ...api.buildReplayCaseSessionBody('all') }, { mode: 'blind', selection_bucket: 'all' });
  assert.deepEqual({ ...api.buildReplayCaseSessionBody('all', 'free') }, { mode: 'free', selection_bucket: 'all' });
  assert.deepEqual({ ...api.buildReplayCaseSessionBody('ge5', 'blind') }, { mode: 'free', selection_bucket: 'ge5' });
  assert.deepEqual({ ...api.buildReplayCaseSessionBody('unknown', 'blind') }, { mode: 'blind', selection_bucket: 'all' });
});

test('case outcomes stay hidden until the server marks them revealed', () => {
  const api = loadApi();
  const hidden = { selection_bucket: 'all', outcome_revealed: false, outcome: { net_r: 7 } };
  assert.equal(api.visibleReplayCaseOutcome(hidden), null);
  assert.equal(api.canRevealReplayCaseOutcome(hidden), true);
  assert.deepEqual({ ...api.visibleReplayCaseOutcome({ ...hidden, outcome_revealed: true }) }, { net_r: 7 });
  assert.equal(api.canRevealReplayCaseOutcome({ selection_bucket: 'open', outcome_revealed: false }), true);
  assert.equal(api.independenceLabel({ independence: 'outcome_known_before_judgment' }, {}), '历史结果类别在判断前已知，仅作学习标签');
  assert.equal(api.replayCaseJudgmentOutcomeKnown({ outcome_known: true }, { human: { current_state: 'launching' }, independence: 'before_ai_and_later_bars_in_this_session' }), false);
  assert.equal(api.replayCaseJudgmentOutcomeKnown({ outcome_known: true }, { independence: 'outcome_known_before_judgment' }), true);
});

test('jump draft belongs to a session and resets only when switching sessions', () => {
  const api = loadApi();
  const draft = '2026-09-24T12:30';
  assert.equal(api.replayTargetValueForSession('ksm-session', 'ksm-session', draft, 1000), draft);
  assert.equal(api.replayTargetValueForSession('ksm-session', 'ptb-session', draft, 1000), api.msToShanghaiInput(1000));
  assert.equal(api.replayTargetValueForSession('ksm-session', null, draft, 1000), draft);
});

test('common outcome codes display in Chinese while unknown codes remain visible', () => {
  const api = loadApi();
  assert.equal(api.replayOutcomeCodeLabel('status', 'closed'), '已结束');
  assert.equal(api.replayOutcomeCodeLabel('exit_reason', 'trailing_stop'), '追踪止损');
  assert.equal(api.replayOutcomeCodeLabel('exit_reason', 'custom_exit_v2'), 'custom_exit_v2');
});

test('first judgment and model gates reflect future exposure and loaded frozen image', () => {
  const api = loadApi();
  const session = { mode: 'blind', seen_until_ms: 100 };
  const observation = { id: 'obs-1', cursor_ms: 100, image_url: '/api/images/obs-1' };
  assert.equal(api.canSaveFirstJudgment(session, observation), true);
  assert.equal(api.canSaveFirstJudgment({ ...session, seen_until_ms: 101 }, observation), false);
  assert.equal(api.canSaveFirstJudgment(session, { ...observation, run_id: 'run-1' }), false);
  assert.equal(api.canAnalyzeObservation(session, observation, { id: 'obs-1', status: 'loading' }), false);
  assert.equal(api.canAnalyzeObservation(session, observation, { id: 'obs-1', status: 'error' }), false);
  assert.equal(api.canAnalyzeObservation(session, observation, { id: 'obs-1', status: 'loaded' }), false);
  assert.equal(api.canAnalyzeObservation(session, { ...observation, human: { current_state: 'unclear' } }, { id: 'obs-1', status: 'loaded' }), true);
  assert.equal(api.canAnalyzeObservation({ mode: 'free' }, observation, { id: 'obs-1', status: 'loaded' }), true);
});

test('later followup disclosure does not rewrite the original independence sequence', () => {
  const api = loadApi();
  const session = { seen_until_ms: 300 };
  const judged = {
    cursor_ms: 100,
    human: { current_state: 'unclear' },
    independence: 'before_ai_and_later_bars_in_this_session',
  };
  assert.equal(api.independenceLabel(judged, session), '人工判断先于模型；其后已推进后续行情');
  assert.equal(api.independenceLabel({ ...judged, human: null, independence: 'future_seen_in_session' }, session), '冻结时已看过后续行情');
});

test('coverage default starts within the latest 500 bars but never before replayable data', () => {
  const api = loadApi();
  assert.equal(api.defaultStartForSegment({ first_replay_close_ms: 100, last_close_ms: 1_000_000_000 }, '15m'), 550_000_000);
  assert.equal(api.defaultStartForSegment({ first_replay_close_ms: 100, last_close_ms: 10_000 }, '15m'), 100);
});

test('playback serializes at least two moves and waits before scheduling each tick', async () => {
  const timers = [];
  const pendingSteps = [];
  let concurrent = 0;
  let maxConcurrent = 0;
  let started = 0;
  let cursor = 0;
  const playback = loadApi().createReplayPlayback({
    schedule: (callback, delay) => {
      const timer = { callback, delay, cancelled: false, fired: false };
      timers.push(timer);
      return timer;
    },
    cancel: (timer) => { timer.cancelled = true; },
    shouldContinue: () => cursor < 3,
    getDelay: () => 400,
    step: () => {
      started += 1;
      concurrent += 1;
      maxConcurrent = Math.max(maxConcurrent, concurrent);
      return new Promise((resolve) => pendingSteps.push((moved) => {
        concurrent -= 1;
        if (moved) cursor += 1;
        resolve(moved);
      }));
    },
  });
  const fire = () => {
    const timer = timers.find((item) => !item.cancelled && !item.fired);
    assert.ok(timer, 'a playback tick is scheduled');
    timer.fired = true;
    timer.callback();
  };
  const settle = () => new Promise((resolve) => setImmediate(resolve));

  assert.equal(playback.play(), true);
  assert.equal(timers[0].delay, 400);
  fire();
  assert.equal(playback.isInFlight(), true);
  assert.equal(timers.filter((item) => !item.cancelled && !item.fired).length, 0);
  pendingSteps.shift()(true);
  await settle();
  assert.equal(started, 1);
  assert.equal(timers.filter((item) => !item.cancelled && !item.fired).length, 1);

  fire();
  assert.equal(started, 2);
  assert.equal(playback.isInFlight(), true);
  pendingSteps.shift()(true);
  await settle();
  assert.equal(started, 2);
  assert.equal(maxConcurrent, 1);
  assert.equal(timers.filter((item) => !item.cancelled && !item.fired).length, 1);
  playback.pause();
});

test('pausing while a move is in flight never restarts playback after it completes', async () => {
  const timers = [];
  let resolveMove;
  const playback = loadApi().createReplayPlayback({
    schedule: (callback) => {
      const timer = { callback, cancelled: false, fired: false };
      timers.push(timer);
      return timer;
    },
    cancel: (timer) => { timer.cancelled = true; },
    shouldContinue: () => true,
    getDelay: () => 1000,
    step: () => new Promise((resolve) => { resolveMove = resolve; }),
  });
  const timer = () => timers.find((item) => !item.cancelled && !item.fired);
  assert.equal(playback.play(), true);
  const firstTick = timer();
  firstTick.fired = true;
  firstTick.callback();
  assert.equal(playback.isInFlight(), true);
  playback.pause();
  resolveMove(true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(playback.isRunning(), false);
  assert.equal(timers.filter((item) => !item.cancelled && !item.fired).length, 0);
});

test('stale request results cannot cross token or session boundaries', () => {
  const api = loadApi();
  assert.equal(api.replayResponseIsCurrent({ token: 2, sessionId: 'a' }, { token: 2, sessionId: 'a' }), true);
  assert.equal(api.replayResponseIsCurrent({ token: 1, sessionId: 'a' }, { token: 2, sessionId: 'a' }), false);
  assert.equal(api.replayResponseIsCurrent({ token: 2, sessionId: 'a' }, { token: 2, sessionId: 'b' }), false);
});
