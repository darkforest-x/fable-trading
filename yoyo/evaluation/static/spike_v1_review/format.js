/* Numeric scale contract for local Lightweight Charts; preserve meaningful small-token and oscillator precision. */
(() => {
  "use strict";
  function dynamicPriceFormat(values) {
    const finite = [...values].map(Number).filter(Number.isFinite).map(Math.abs).filter((value) => value > 0);
    const precision = finite.length ? Math.min(12, Math.max(2, Math.ceil(-Math.log10(Math.min(...finite))) + 3)) : 2;
    return Object.freeze({ type: "price", precision, minMove: 10 ** -precision });
  }
  globalThis.SpikeV1ReviewFormat = Object.freeze({ dynamicPriceFormat });
})();
