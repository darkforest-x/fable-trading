# V11 report design

## Audience and delivery

Technical: owner wants implemented rules, actual cost-adjusted performance and
detailed failure causes. Portable canonicalHTML, single report, offline source
metadata; sourceMD required by repository. No dashboard/newwebsite, no external
publication. Reuse the official DataAnalytics renderer; no customHTMLchart runtime.

## Reading path and specification mapping

Technical summary first; then rules/scope/definitions before evidence; net levels
and frozen controls; fourfold/month stability; pairedD/I and distribution; lost
winners/savedlosers and unchangedfailures; singleposition; validation/coststress;
risks and honestlimitations; nextstep and openquestions; repository-required
reproductioncommands. One visible peer##section per narrativeblock. This reorders
technicalspec definitions before numericalfindings to avoid ambiguousdenominators.

## Visual contract

Primary chart is distribution of all251 case paired changes, not just timeouts.
Use inherited actualSQLite fixedsigned-bin counts (including exactzero atom and
unknown) over saved case_delta.csv. Categorybars for discrete signed intervals,
not density with misleading equalwidth numericbins; fullwidth, countYzero,
signedbpX labels. Blue single-root marks with direct counts; no redundantseries.
Keep bothmeans/total/known/unknown in source rows. No interiminline chart.

Exact multi-metric fold,control,fee and failure lookup uses tables; lines would
imply a smooth trend across4halfyear anchors.24month detail saved asCSV, not an
entrygate. Detailedtradeledger is source artifact, not251cluttered visiblecards.
Quantiles and binnedchangechart retain extremes; no trimming/optimization.

## Provenance and QA

Each quantitative block points to summary or savedpaired ledger; source metadata
retains exactrelativepath/query/builderidentity. ChartSQL really aggregates saved
rows, not rawfeature recomputation. Officialrendererexactpayload/semanticfallback
QA; if structural_only, browser/mobile/touch/colourappearance remainunverified,
never installChromium or substitute screenshotforHTML. Companionnotebook uses
saved evidence with top-downchecks; clearly distinguish plainPython fromJupyter.
