/* Preserve native links (including opening in another tab) while offering the in-place booklet. */
(() => {
  document.addEventListener('click', async event => {
    const link = event.target.closest?.('a[data-breadth-book]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey ||
        event.shiftKey || event.altKey || !window.SpikeModules) return;
    event.preventDefault();
    const url = link.href;
    try {
      await window.SpikeModules.ensure('breadth-booklet');
      window.SpikeBreadthBook.open({origin: link});
    } catch {
      location.assign(url);
    }
  });
})();
