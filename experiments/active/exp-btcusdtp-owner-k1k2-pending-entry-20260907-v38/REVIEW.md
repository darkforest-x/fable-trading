# V38 independent saved-output review

Verdict: publish with stated limitations; no blocking numeric inconsistency.
The read-only reviewer checked only exact V38 saved outputs, receipt/summary and
the V38 narrative. No source OHLC, V37 returns or other-session work was read.

733 consistency assertions passed: eight CSV SHA/row/column receipts, 171 IDs,
original36three-control groups,946 visited clocks,171 prefix records with
41+waiting_bars local rows, reference-stop risk formulas and all grouped tables.
These are review assertions, NOT733 additional pytest cases to add to494.

Matched36case:19eligible(9immediate/10delayed),8wait-stop,9pending.
Unmatched27case:19eligible(7/12),7wait-stop,1pending. Controls65/108eligible.
Allfourmemberseligible only9groups;18groups have at leastonepending. Do not
redefine the original36group comparison as only those9survivors.

39traceunknown rows are not missing data: fixed-stop precedence ended inspection
with management_reason=not_observed. Terminalunknown events are0.
No actual event firstqualified or invalidated at60minutes;29werependingthere.
Latesteligibilitycase45minutes/control55minutes;this does not optimize lifetime.
Delayed-only mean/median minutes:case16.36/10(n22),control24.74/20(n38).
Eligible riskmedian/IQR case0.4376%/[0.3392,0.5563]%,control0.4836%/[0.3439,0.9291]%.

Narrative correction adopted: distinguish full63case eligibility proportion
from matched36 proportion52.78%. Do not infer15savedlosses from15wait-stops.
No independent raw SMA reimplementation was performed. Old V37 economic text,
44differentK1 and494pytest results cite their existing receipts; this saved-output
review does not itself independently verify those prior-source claims.
