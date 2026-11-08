// Batch the production chart's historical excursion function on closed native 12H bars.
// Return first/peak candle OPEN times, not inferred intrabar crossing timestamps.
'use strict';
const fs = require('node:fs');
const {planExcursion} = require('../monitor/static/cloud-signal-chart.js');
const {bars, signals, cutoff} = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = signals.map(({idx, signal}) => {
  const future = bars.slice(idx + 1);
  const p = planExcursion(signal, future, cutoff);
  if (!p) return {state: 'unavailable', lowerR: 0, upperR: 0};
  let first = null, peakAt = null, peak = 0;
  const risk = (signal.entry - signal.stop) * signal.side;
  for (const bar of future) {
    if (bar.time > p.through_ms || (bar.time === p.through_ms && !p.exit_state)) break;
    const price = bar.time === p.exit_bar_ms ? bar.open : signal.side === 1 ? bar.high : bar.low;
    const r = (price - signal.entry) * signal.side / risk;
    if (r > 10 && first === null) first = bar.time;
    if (r > peak) { peak = r; peakAt = bar.time; }
  }
  if (Math.abs(peak - p.lowerR) > 1e-8) throw Error('Chart peak date mismatch: ' + signal.id);
  return {...p, first, peakAt};
});
process.stdout.write(JSON.stringify(out));
