# Detector lag was model-side; a conf number without box position proves nothing

**Date:** 2026-07-19
**Context:** Live forward signals showed detection lags of 92–587 min despite
15-min pulses. First 10 live-clock trades were all hindsight. Owner asked why.

## The wrong turn (and why it was convincing)

A tip-truncated render of EDEN (window ending at the 04:30 signal bar) returned
a box with conf 0.357 ≥ the 0.30 scan threshold. Conclusion drawn: "the signal
WAS detectable at the tip, therefore the live pipeline has a drop bug." Two VPS
reproductions later, the pipeline looked guilty: data fresh, pulses on time,
box above threshold — yet no log row for 9 hours.

The conf number was real. The interpretation was wrong. That box had cx=0.498 —
**it sat in the middle of the window, boxing the PREVIOUS DAY's pattern**
(2026-07-18 04:45), which the `start_from_i` filter was correctly dropping.
The fresh 07-19 pattern at the right edge had NO box at all.

## The decisive instrument

Copy the live scan loop verbatim but print every raw box BEFORE filters:
conf, cx/w, `right_edge_to_bar` mapping to a dated bar, and which filter drops
it. Replay across simulated tips (+0, +1, +2, +4, … +35 bars). Result read like
a flight recorder:

- +0 … +34 bars: only the day-old pattern is boxed (conf rising 0.36→0.68 as
  the new launch printed next to it — the launch made *yesterday's* cluster
  look better). New pattern: zero boxes.
- +35 bars (13:15 tip): first box on the new pattern, right edge → 04:45 bar,
  KEEP — matching the live 13:32 detection exactly.

Pipeline exonerated line by line: update fresh (401/401 symbols +1 bar),
start_from_i correct, tip-bar exclusion correct (structural 15-min only),
min-gap correct. The entire 9-hour lag is the model waiting to see the
completed launch. Same mechanism as KAITO, now proven bar-by-bar.

## Rules extracted

1. **A detection claim needs (conf, position, mapped bar), never conf alone.**
   Any future "was it detectable at time T" experiment must print the box's
   mapped bar index/date, or it can silently prove the wrong pattern.
2. **When replaying "what did live see", replay the exact live code path** —
   same window construction, same filters, with prints — not a hand-framed
   render. The hand-framed render is what created the false positive.
3. **Structural detection floor is ~31–37 min** (signal bar closes +15 min;
   scan can only record once the entry bar exists; pulses at :01/:16/:31/:46).
   Any freshness threshold below ~40 min excludes even a perfect detector —
   FRESH_DETECT_MIN was fixed from 20 → 55 to match the executor gate.
4. **Fix path is training-side (H-TIP):** re-render existing golden_pool labels
   with windows truncated at the signal bar so the model learns tip-firing.
   No pipeline work will reduce the lag; the pipeline is already correct.

## Related

- `analysis/` forward verdict hindsight exclusion (commit e15bbb4)
- RESEARCH_AGENDA: H-TIP experiment
- ultralytics-auto-lr-destroys-finetune.md (same lesson shape: verify the
  mechanism at the exact code path, not a plausible proxy)
