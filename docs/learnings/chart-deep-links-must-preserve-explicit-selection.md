# A chart deep link must preserve its explicit contract and timeframe

- **Problem**: Patrol-to-Monday links need to open the same contract and timeframe. The Monday route ignored the timeframe, and its selection helper could replace an unavailable requested contract with the newest touched contract.
- **Dead end**: Adding parameters to the source link alone is insufficient when the destination treats missing data as permission to choose another asset. Filtering the visible touch list also must not remove an explicitly pinned chart.
- **Effective path**: Validate the contract, normalize the daily timeframe, consume both route parameters, and select the pinned contract from the full snapshot. When that contract is absent, retain the selection and show an unavailable state; resume it when data returns. Existing explicit follow-latest and week rollover behavior remain intact.
- **General rule**: An explicit entity selection takes precedence over a recommendation fallback. Test destination state, missing data, and recovery, not just URL construction.
- **Affected files**: `yoyo/monitor/static/monday-fib.js`, `yolo-market.js`, `tests/monitor/frontend_monday_fib.test.cjs`, `frontend_yolo_market.test.cjs`. Browser validation opened `USELESS-USDT-SWAP` at `1H` from patrol and retained both in the Monday chart.
