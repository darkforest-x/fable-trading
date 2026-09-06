# ChartPrime Confluence Audit — artifact plan

Question: which of all 148 publicly listed ChartPrime publications can add
useful, causally available information to the owner's trend system?
Technical audience: owner explicitly requires actual indicator source, its
rules and parameters; this is a methods/source audit, not a market forecast.
Delivery: one portable HTML report, canonical artifact.json; repository MD
source is generated and immediately converted before canonical packaging.

Reading path / technical specification mapping:
1. Matching English title, Chinese technical summary answering add/not-stack.
2. Scope and evidence definitions before interpreting counts: frozen 148 IDs,
   134 official open sources, 14 description-only, date, versions, no prices.
3. Key findings: source-vs-marketing, actual knowledge clock, duplicated votes.
4. Evidence overview: one bar chart by manually assigned primary mechanism
   family. It explains composition of the large library, not strategy quality.
   Categories are mutually exclusive for navigation only; many scripts mix
   mechanisms. No profitability scores, invented independence or win rates.
5. Proposed test sequence and a compact exact-lookup table. Existing base and
   exits remain fixed for future entry tests; no tests occur in this audit.
6. Every publication has a separate numbered source-backed narrative card:
   formula, defaults, clock risk, role, dependence, one testable hypothesis.
   Closed scripts explicitly retain unknown mechanism and no code citation.
7. Limitations / robustness / further questions; financial metrics N/A with
   reason and exact coverage/hash/access/negative-fixture controls instead.
8. Reproduction commands required by repository owner (not hidden).

Visual contract: count chart has one numeric series, zero baseline, visible
category labels, count units, no legend, descending count order. Full-width
table sorted by profile order; narrow columns and full per-script prose below
avoid an unreadable 12-column mobile table. No price chart: no market data was
collected and a picture would not validate temporal availability or profit.
Source metadata retains official publication URLs, byte SHA, version, source
line references and exact local evidence identity. Preserve MPL attribution.

QA: fail on missing/duplicate IDs, title mismatch, wrong SHA, source line
out-of-bounds, unsupported source-read claim, missing formula/risk/role fields,
or family partition gaps. Run synthetic failing fixtures and official portable
validator. Inspect final HTML in CUA if possible; do not claim visual/theme QA
from structural validation alone. Any dependency not independently audited is
explicitly outside full-runtime verification. No Sites/public publishing.
