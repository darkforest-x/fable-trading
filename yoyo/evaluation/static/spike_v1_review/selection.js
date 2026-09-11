/* A record fetch may complete after a newer filter or list selection. Only the current selection may paint charts. */
(() => {
  "use strict";
  function isCurrentSelection({ requestEpoch, activeEpoch, requestId, activeId }) {
    return requestEpoch === activeEpoch && requestId === activeId;
  }
  globalThis.SpikeV1ReviewSelection = Object.freeze({ isCurrentSelection });
})();
