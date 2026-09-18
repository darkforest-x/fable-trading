# In a zoomed-out screenshot, the crosshair time is not the event bar

- **Problem**: owner: "HYPE 9.17 at 7 pm, the 1h broke". The new monitor put the 15m 突破+spike（上级突破） on the
  18:00 bar, which looked about an hour early.
- **Dead ends**: suspected short history (the monitor's ~900 1h bars versus TV's full history) could
  change the line pool. Rerunning at 600/900/1200/1500/2000/2382 bars gave identical breaks, so it was not that.
- **What worked**: listing the engine's breaks with their A/B/C. Two 1h lines broke, on the 17:00 and
  21:00 bars; the TV-drawn line (A 09-06 20:00, B 09-10 01:00, C 09-15 04:00) is the second one. At that
  zoom one hour is about 1.6 px, so the two "突破" labels sit on top of each other, and 19:00 was just
  where the crosshair was. The first break (1h bar 17:00, closed 18:00) becomes visible on the 15m bar
  that opens at 18:00, and that is where the monitor placed it. Monitor and TV agree.
- **General rule**: before treating a time read off a screenshot as a discrepancy, list the events
  near that time with their bar timestamps from the data. Overlapping labels and crosshair positions
  are not timestamps.
- **Related**: `yoyo/monitor/spike_lines.py`, `spike_v11_study.htf_inputs` (a higher bar is
  visible from the first chart bar opening at its close).
