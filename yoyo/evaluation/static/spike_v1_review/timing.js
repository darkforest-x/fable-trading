/* Timing contract for the local V1 review: candles are keyed by bar open, confirmation exists at bar close. */
(() => {
  "use strict";
  function resolveChartTiming(payload, record, signal) {
    return Object.freeze({
      signalBarMs: payload?.signal_bar_open_ms ?? signal?.bar_open_ms ?? record?.signal_bar_open_ms ?? null,
      confirmationMs: payload?.signal_close_ms ?? signal?.time_ms ?? record?.signal_close_ms ?? signal?.bar_close_ms ?? null,
    });
  }
  function isCensoredRecord(exit) { return String(exit?.reason ?? "").toLowerCase() === "censored"; }
  globalThis.SpikeV1ReviewTiming = Object.freeze({ resolveChartTiming, isCensoredRecord });
})();
