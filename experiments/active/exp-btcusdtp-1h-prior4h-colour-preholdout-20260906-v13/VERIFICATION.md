# V13 verification and delivery receipt

## Actual outcome and scope

Rejected, not profitable and not accepted. Goal turn made real progress; goal
remains active and unmet, not blocked. One price replay after builder d3366d3:
2026-09-06 09:08:45.371254UTC start;713 context rows frozen at09:08:47.049258UTC
before any baseline/candidate returns. Only219551 rawprice rows through
2024-12-31 23:55UTC, pre2023warmup included. No2025+prices or holdout consumed.
Physicalarchive341567 timestamps through2026-02-28 are metadata/hash preflight,
not price materialization. No live/TV/production/training/dependency changes.

Baseline six-table all-old-field parity:251x67/462x76 trade;251x37/462x46 episode;
251x7 matched;251x39serial. Candidateactuallyreplayed accepted121cases311controls,
not justmaskedoldoutcomes; all432acceptedoldfields unchanged. Full mother251/462,
154triples and97unmatched remain. Candidate casegate121accepted127abstain3unknown;
controls311accepted151abstain0unknown. Eachcontrol independently ownprior4hgate.

Actualcase121mean-18.954082bp/PF.547610/30wins91losses. All248known opportunities
including127abstention0 mean-9.247758bp. D same248known+4.854035bp,CIcross0,p.0927;
I154-1.653089bp,97unknown,p.6295. Serial250known/1unknown: firstunknownreserves72h,
twootherunknownrequestsblocked, their independentepisodes remainNaN. Thus serial
contribution0 does not mutate unknownobservations. No rule/filter/threshold was
changed after outcomes. No V14 or other next-experiment price/outcome run this turn.

## Independent saved-only verification

Verifier+tests and presentation builders committed7c56ef3 BEFORE actualnotebook
and finalsaved-verifier replay. Standardlibrary verifier directly loads separate
saved-audit helpers, notStudy/engine. It checks51outputhashes,20committedsource
receipts,sourcecommitbeforestart,own-clock/side/tri-state fields,fulloldidentity,
actualtrade economics/20bp,completeepisode/zero/unknowns,exactfixedcontrols/D/I,
serialoccupancy,48arm-monthrows,mechanismcounts andsums. Actual output saved in
saved_verification.json. raw_replay=false,inferential_p_recomputed=false.

This is NOT an independent reconstruction of40raw4h bars, rawsource continuity,
intrabar sequencing, statisticalp or profitability. Syntheticcausality tests and
mainrealreplay cover those implementation paths separately, not as an external
independent market sample.

```bash
.venv/bin/python scripts/verify_hourly_impulse_prior_colour_v13.py
.venv/bin/python -m pytest -q tests/test_hourly_impulse*.py tests/test_verify_hourly_impulse*.py tests/contracts/test_registries.py tests/boundaries/test_layer_imports.py
```

Final relevant suite:2879passed39.98s. Beforefirstbuilder325passed5.81s. Additional
presentation/verifier regression371passed10.71s. These counts overlap; do notadd
them into a fictional independenttestcount. An early test-command path typo
referenced nonexistenttest_artifact_contracts.py,so no tests ran; corrected to
tests/contracts/test_registries.py. No test expectation was relaxed to pass.

## Supporting notebook

prior_colour_audit.ipynb:5codecells actuallyrun top-downplainPython,10allowlisted
savedCSV hashes pluspinnedsummary,713gates,1145actualtradecostrows(713baseline+
432candidate),432acceptedfullfieldchecks,251D rows248known,allcontrolidentities.
No raw/SMA/firstcolourexit/I/p recomputation. Jupyterkernel and fullnbformatschema
NOTexecuted:nbformat/nbclient/ipykernel unavailable,noinstall. Minimumstructure,
cellIDs andcodecompilation checked. Cells/output record actualchecks,notstale
unexecutedclaims. Fullcommand in finalreport; use a newoutput path or a fresh
artifact-free workingcopy to rerun, neverdelete researchresults.

## Canonical portable report

Complete Chinese report:14peer sections +title+1nativechart=16blocks. Actual
SQLitequery overall251case_delta rows:248finite+3unknown,121zero,95positive,
32negative. Fixed12signedbins sum251 with ranges/context,oneblue root; neutral
title/no densityclaim. Fullreport not replaced bysummary-onlyHTML. SourceId only
on matching source-specificblocks; combinedcontext/serial andverificationnotes
have explicitlinks rather than attributing externalfields tosummary.

OfficialDataAnalytics0.2.10 renderer:validation=passed,package=passed,
verification=structural_only. NoinstalledChromiumheadless-shell;sourceDialog,
sourceInteraction andlaptop/mobileviewports NOTverified. No browserinstalled.
PortableQAexacttoolresult inportable_qa.json. MaincheckedsemanticHTMLsourceIDs,
all51rawoutputs unchanged,chartcountsum,all4localartifactlinks resolve under
finalanalysis/html location. HTML isselfcontained; additionalCSV/notebooklinks
refer to theexistingrepository checkout, not an externalhost.

Pre-render QA fixed misplaced distributionmarker, narrow sourceattribution and
relativeHTMLattachments. Oneproseupdate briefly introduced machine-local paths;
portablevalidator correctly rejected it, andrelativepaths/codeenvroot resolved
withoutweakeningvalidator. Derivedpresentation was rebuilt after these fixes;
financialresults/sourcecheckpoints unchanged. This is not a secondpriceattempt.

## Artifact identities

| Artifact | SHA256 |
|---|---|
| results/summary.json | 97a590bc4743f8fac4d1d273964b4b68c5ee7b71c6901f9315747524e86644b7 |
| config.json | 506f97b99d9935a07985f8922412fad4cc4835b51d23a6de961635eb59b11e2a |
| context_gates.csv | 6cac2592d52228e40ac33422decfed722deac735e764299c3ed490082996ff9e |
| artifact.json | 7e42a3c1c7819a09cfdbeaf5bf321e5dbe2436d0d193c7d49f1142717542dc65 |
| prior_colour_audit.ipynb | 96c1561cfb57711f9ee46061fffd700e539305b4f54ff10675add10c92a38437 |
| saved_verification.json | 0994e7ae79f0311499d78b870defe5abcb67e9d1c5beaeea8657946c67351b1a |
| report Markdown | fff78688cc30482c3c01bc12f3f8872670c36e651570091990ad219ee198213e |
| portable HTML | 77b9a03aeda2898f5305f9a9ccbafbb2040ee08e37e91cba87f9151a52279bea |

## Skills and final state

Experimentaldesign fixed onegate andmatchedknown-denominators;source-driven
development avoided43bar optional-slopewarmup;statisticalanalysis retainedmonth
clusters/exploratoryreuse;report/dataquality/validation/notebook/visualization
skills kept actualtrade,opportunity andunknownaudits separate. Learninglaw notes
record boththree-stateaccounting andreduced-exposure-vs-selectionfailure.

No fresh independent validation, matchingcoverage61.35% remains below90%; not
possible to callthis profitable. NEXT_EXPERIMENT is only a deduplicatedproposal
to isolate oldprior20breakout withoutslope/4h bundling; notimplemented orrun.
