# SPIKE contract discipline interaction prototype

Independent mobile UX prototype added on 2026-10-18. Existing application modules and live execution settings are unchanged. This is a browser-rendered mobile prototype, not an iOS or Android installable app and not an OKX client. Its iPhone/Pixel selector tests layout, not native device execution.

Design source: `../../docs/design/spike-contract-app-20261018/source.json` and the unmodified Stitch export next to it. Original source is an OKX official public order-entry screenshot. Financial values are synthetic; all policy numbers remain unapproved examples. See `src/demo.ts` for example cost-inclusive sizing. No private credentials, account requests, exchange writes, production endpoints, or real protection orders exist here.

Working demo flows: select a contract, switch market/limit order, choose a fraction of the risk-sized amount, validate long/short stop direction, review quantity/notional/margin/estimated loss separately, create/cancel a local pending order, simulate its fill, preview and close a local position, demonstrate entry lock while leaving exits available. Pending exposure occupies the single-position allowance; cancellation does not refund the example daily count.

Limitations: quotes, depth, equity and funding values are static samples; positions are not marked to market. A button selects the loss-lock scenario, not a real loss calculation. The cooling state represents the 30-minute policy but has no running timer. All state is in-memory and reload resets it. Automatic breakeven, partial take-profit, durable account locks, server-side enforcement, authentication, real market feeds, native builds and signing remain unimplemented. No statement in the UI is evidence of live protection.

Run locally:

```sh
cd /Users/zhangzc/fable-trading/prototypes/spike-contract-discipline
npm ci --prefer-offline --no-audit --no-fund
node --test tests/discipline-demo.test.mjs
npm run build
npm run dev -- --host 127.0.0.1 --port 5198 --strictPort
```

Local preview is loopback-only. Browser tests use CUA and close task-created tabs afterward. The preview server is a temporary development process, not a persistent trading service. Raw UI evidence is in `evidence/`. No automatic deployment or Sites publication is configured for this task; template build support alone is not publication authorization.
