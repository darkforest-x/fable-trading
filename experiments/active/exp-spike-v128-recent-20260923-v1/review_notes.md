# Independent implementation and smoke audit notes

These are scoped reviews, not native TradingView event parity or a profitability acceptance gate.

## Coverage review and correction

The Terra High reviewer identified that the initial report authenticated ledgers but discarded receipt coverage, making missing streams invisible. The subsequent Terra High implementer added coverage.csv, explicit stream coverage classes, arm denominators, per-symbol matched controls and report builder provenance. Synthetic complete/partial/empty coverage cases and the existing metric tests passed (3 tests). Parent integrated this in 3cf36c32b8 and recorded the learning. No actual data gaps were inferred from this code defect.

## ETH 60m large-return smoke audit

The read-only Terra High debugger independently reaggregated registered ETH 5m data SHA 6ab362ad4dc8bb9c548a8c129c6c7b876d8c065fd46d6c50e85c2c3a117f1144. The 2026-08-18 16:00Z V9 next-open entry was 1916.88. The five-bar low 1893.07 and ATR 9.24769076 with tick 0.01 give initial stop 1891.22 and risk 25.66. The pre-exit peak 2549.40 at 2026-08-21 22Z gives 24.65003897R MFE.

The trail had reached 2383.05 at 2026-08-22 04Z. The 2026-08-23 04Z bar open/high/low/close was 2413.20/2416.57/2371.20/2378.24, crossing that already-active stop. Realized gross/net return is 24.3192%/24.1192%, or 18.1672R/18.0178R. The approximately 24R figure is excursion, not realized R.

This V9 event is not an HTF joint event; the related joint entry at 17Z uses chart pairing. Both 60m smoke streams had 1500 complete 4h prewindow bars, and the three actual 15m H1 events became visible only after H1 closure. The smoke has no actual 60m/4h joint event and does not prove exhaustive HTF parity. All four stream signal clocks satisfy the frozen window; 41 trades closed and one BTC 60m joint trade is boundary-censored.

The reviewer also noted the original smoke identity bound V12.6 but not V12.8 Pine. Parent corrected the full-run declaration in 84a53d74ac before the full run. The smoke remains preserved under its original identity. Two V12.8 ETH hint candidates were after the original frame; they were not treated as fills.

## Data collection failure retained

The first input build completed 635/638 contracts and failed on three Unicode contract names because an inherited REST URL interpolator expected ASCII. data_failure_v1.json records the exact names/errors and failed manifest hash. The isolated recent-data adapter now percent-encodes the symbol, with a REST-boundary round-trip test; fix 1387ad9355 precedes retry. Successful inputs are hash-verified and reused; no data source substitution or pool exclusion was performed.

## Independent frozen-input evidence audit

The Luna Max evidence agent validated all638 audit JSONs and compressed series in one scoped pass. Requested/stream/audit/file membership is638 unique, matching archived symbols parsed from source input paths. All638 frozen compressed-file SHA256s match manifest/audit. Config SHA is bc0d9886ffa60c53197febb99af23b45fe784392feeb1e2dffe2cde84b82426a; final manifest SHA is15a251d5e6ea017702b6c3c644a41369a6b0809403d9ee17017ba2a52067db70.

Every symbol has17904 recent5m rows from2026-07-23T00:00Z through2026-09-23T03:55Z, with no duplicate, out-of-order, misaligned or missing timestamp. Across56304804 total rows there are no nonfinite/invalid OHLC values or negative volumes. The only historical gaps are AIAUSDT from2026-01-19T23:55Z to2026-01-20T11:15Z (135 missing5m rows) and LITUSDT from2025-12-22T23:55Z to2025-12-23T17:30Z (210 missing rows), both before the evaluation window.

589 symbols have all1500 complete prewindow4h buckets;49 have shorter coverage. The first evidence response counted occupied buckets, including a partial first bucket. Parent challenged AIGENSYN507 against24309 available5m rows; the child corrected to506 COMPLETE48-row buckets, with21 rows in the omitted partial bucket. Revised shortest prewindow count is506, and589/49 membership is unchanged. The correction reused the retained scan and boundary/gap arithmetic, without rescanning56304804rows. Do not infer actual listing dates from the file start.

This child checked source input SHA formatting and consistency between manifest/audit but did not reopen external ancestral source files. Its independent content verification applies to the638 frozen series actually used by this replay, not to every original downloaded ancestor. Data integrity is not a profitability or native Pine parity claim.

## Zero-readiness cause and complete targeted follow-up

The Terra High debugger traced three examples (1000XUSDT, BNXUSDT, MKRUSDT): each has exactly17904 recent rows, all flat OHLC and zero-volume with one unique close. ATR/width/ready rejects them while complete_bars accepts the full time grid. The recent builder concatenates/filters/deduplicates and does not fill these rows. Selected inherited cache/local monthly archive already contains the representation; no exchange listing or original producer claim is made. An initial diagnostic used an incorrect hard-coded start epoch and overcounted the span; that count was discarded and corrected directly from config before publication.

Parent committed diagnostic builder a95a348d1a before executing it. input_diagnostics_v1 checks every final zero-ready symbol:55/55 are flat-zero in the exact frozen window; other zero-ready causes=0. All583 other symbols have full ready coverage on both chart periods. This is additional data-quality evidence, not a strategy rule change.

## Final aggregate audit

Luna Max independently reconciled1276 receipt hashes, all19 summary file hashes, status/candidate totals,10515 closed/198 censored trades, wins and net-return/R/PF arithmetic. Exact trade keys and same-arm entry tuples contain no duplicates; 15m joint overlaps2405 taken entries with V9 and1h joint overlaps593, so arms are not independent. Known/upper MFE counters at0/1/2R and matched target/control means all reproduce. No raw market reread or strategy experiment was performed in this phase.

The child treated stored p/Holm as reported evidence. Parent independently enumerated all1024 signed UTC-week bp sums with stable math.fsum and applied Holm; all four p-values and adjusted values exactly match (pvalue_audit.json). The first independent dot-product reduction was sensitive to floating summation of the identity permutation; using the same stable sum on observed and permuted vectors resolved the audit artifact without changing any experiment result.

## Two unusual paths checked from raw frozen5m buckets

Terra High independently validated USUSDT15m July27: entry0.046406, initial stop0.044872, risk0.001534. The06:15bar high0.062308 gives10.366362R; close0.051351 gives3.223598R and arms trailing, but ATR0.001908048914 makes the next trail candidate0.043718, BELOW initial. No subsequent candidate exceeds initial. The11:00bar low0.044198 crosses the still-active0.044872; net-1.060503R is correct. Each cited15m bucket is exactly three frozen5m rows. Learning note records why arming is not protection.

APRUSDT15m August11: entry17:15 at0.2021, initial stop0.1986, risk0.0035. August12 at01:30 close0.2097 first arms; later23:30 high0.6325/close0.6258/ATR0.0351748608 produces the next-bar stop0.4851. August13 at00:45low0.4284 hits that pre-existing stop; net139.829688% equals80.741657R. MFE122.971429R was before exit and after entry. This validates the frozen-data path and arithmetic; it does not independently authenticate upstream exchange history.
