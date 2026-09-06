# V8 technical report design

## Reporting contract

- Audience: technical because the main value is strategy methodology and
  experiment validation; owner-facing narrative is Chinese, with the authored
  English title shared exactly by the manifest and visible heading.
- Question: on the exact frozen V7 requests and controls, does native 15m SMA40
  colour-transition management improve on native 5m SMA40 management?
- This is a management-specification comparison, not a pure clock-frequency
  effect: aggregation, colour updates and SMA memory all change together.
- Decision: describe the frozen development result and its economic/inference
  gates, including failure; no new threshold selection, audit or live promotion.
- Scope: BTC-USDT-SWAP, reused 2023-2024 development, original requests retained.
  Monthly dependence and fixed three-control assignments remain explicit.
- Exactly one delivery mode: portable self-contained HTML from the canonical
  Data Analytics artifact. No visible MCP report, bespoke HTML, SVG or JS runtime.
- This is an independent V8 report. It links the existing full V1-V7 report,
  rather than replacing, shortening, rewriting or repackaging that report.
  Historical report source: analysis/p1_btcusdtp_hourly_impulse_ltf_exit_20260906.md.

## Technical audience structure mapping

| Visible section role | Evidence and purpose | Specification mapping |
| --- | --- | --- |
| Title | Same authored title in manifest and visible heading | Title |
| Technical summary | Root-authored answer and immediate limitation | Technical summary |
| Definitions and scope | Fixed request/cohort counts, costs, pair units and clocks | Scope/data/metric definitions; moved before comparisons to avoid ambiguous denominators |
| Management comparison | Both arms in one exact lookup table; unchanged entry/control assumptions | Key findings; model/experiment specification |
| Paired evidence | Distribution of every request's 15m-minus-5m difference, adjacent interpretation | Key findings with visual evidence |
| Entry states and failure mechanics | Known initial state versus future-path diagnostics, subgroups with denominators | Segment evidence and methodology |
| Validation and uncertainty | Saved parity, complete support, dependence, reuse and negative results | Validation; limitations/uncertainty/robustness |
| Next steps and further questions | Evidence-supported next mechanism and what remains unresolved | Recommended next steps; further questions, allowed to share one section |
| Reproduction | Technical methods layer and exact commands for auditability | Methodology and auditability |

Every authored peer `##` remains its own full-width markdown block, including
all tables, lists, caveats and fenced code. The builder does not author results,
rename headings or drop sections. Root authors the narrative and checks its
claims against the saved evidence. Quantitative single-source sections use
`<!-- SOURCE: v8_summary -->`; mixed-source/prose-only sections may remain
unbound. The title has no block-wide quantitative provenance.

## Evidence and provenance

- `--summary` points to the exact reviewed JSON supplied by root, including the
  original V8 summary keys and any root-authored saved diagnostic consolidation.
  For the current report this is the planned diagnostics/report_facts.json.
  This argument, not a guessed historical source, defines `v8_summary`.
- `--case-delta` is the saved results/case_delta.csv: event_id,
  mother_decision_time, before, after, difference. One identity per original
  request, unique and nonnull, same time window; difference=after-before.
- Upstream Python evaluator: yoyo/evaluation/hourly_impulse_management_research.py.
- The chart query actually runs in local SQLite over the reviewed delta table.
  Its exact SQL, source CSV identity, table name, filters and metric definitions
  are recorded in canonical source.query. This aggregation does not rerun or
  claim to have generated the strategy's original outcomes.
- All artifact paths are normalized repository-relative identities. No machine
  paths, invented SQL, old V1 sources or raw-price rows are embedded.

## Chart contract and map

- Segment/question: paired evidence; is any aggregate change widespread or
  concentrated among a minority of requests? Interpretation comes from results,
  never a predetermined positive takeaway.
- Family: distribution. Native type: binned `bar`, explicit numeric signed
  intervals, counts rather than density. It is not a continuous-width histogram.
- Grain/population: every row in case_delta, expected full frozen cohort from
  summary.effects.case_delta.total_pairs (286 for the registered real study,
  never hard-coded by the implementation). No matched-only or winner-only cut.
- Bins: fixed open-ended negative/positive tails, interior signed numeric
  intervals, a distinct effectively-zero atom, and an explicit unknown category.
  The zero atom follows the frozen paired-summary tolerance abs(difference)<=1e-12
  in fractional-return units. Source values are never altered by that grouping.
- Unequal-width/open-ended bins are explicit labels; Y is request count, not
  probability density. Numeric bin limits, observed extrema/mean/sum, per-bin
  count, full total, finite total, unknown total and overall count-sum survive in
  the chart dataset. Even extreme tails and all-unknown fixtures remain visible.
- Required query reconciliation: sum of all bin counts, including unknown,
  equals all input requests. A missing outcome is unknown, never a zero return;
  +/-infinite outcomes are invalid evidence and fail rather than disappear.
  Summary population/state counts and finite paired mean must also reconcile
  with the full saved case ledger before quantitative provenance is attached.
- Baseline: visible zero-change atom separates signs; neutral zero-count Y
  baseline. Do not invent a categorical-X reference line unsupported by the
  native renderer. Adjacent prose states that positive means 15m minus 5m.
- Palette: single blue root plus neutral grid/reference; no green/red semantics.
  One measure and no color/series grouping means the native legend is absent.
  Direct count labels and signed interval labels provide non-colour meaning.
- Footprint: full-width 12-column equivalent, one chart on its own single-column
  row. Mobile stacks through the official reader. No half-width compromise.
- Marker: exactly one standalone `<!-- V8_DISTRIBUTION -->`, at the end of its
  adjacent explanatory section, outside code fences. No duplicate chart block.
- Other comparisons remain exact lookup tables: there are only two management
  arms, and the method/denominator fields matter more than redundant bars.
  Entry/failure strata keep tables for precise counts and missingness.

## Validation and handoff

- Synthetic tests precede real packaging: full cohort counts including nulls,
  sign/tail/zero boundaries, source time/identity validation, summary-count
  reconciliation, complete authored markdown, fenced headings/marker literals,
  no duplicate marker, safe relative metadata and actual SQL replay.
- Root validates and packages the final complete artifact with the official
  deliver_portable_artifact.mjs command. The helper itself renders nothing.
- Keep native semantic no-script/print fallback, system light/dark tokens and
  source affordances. The official receipt provides desktop/narrow smoke;
  disclose structural_only rather than claim browser QA if Chromium unavailable.
- No browser installs, custom screenshots, custom runtime or strategy rerun is
  required for this presentation task. A fixture-only test is not final report
  delivery; owner handoff remains the independently generated V8 HTML file.
