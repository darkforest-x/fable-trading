# Luna Max bounded read-only review

Reviewer: existing `/root/v12_local_touch_oracle`, configured gpt-5.6-luna / max. Scope: commits 3f32da3ac1, da6a478790, 87d0f6d250, b3d09d8481, 158ff1e339; final Pine and the two V12 Python references. No edits, new tests, or market replay performed by reviewer.

No new blocking finding in the reviewed scope.

- Pine 1394–1425 resets/cancels held evidence before current events; 1444–1452 appends only confirmed breaks. 1840–1854 requires a prior break for the new break-first path; 1856–1876 consumes the active box opportunity and clears held evidence. HTF equivalent at 1815–1829 and 1848–1853. Python oracle cancels before pairing and requires prior visibility.
- All four chart/HTF standalone/joint paths at 2039–2138 store frozen line, tag, and ABC marks as one bounded drawing group. Whole-group erase prevents orphan markers. ABC trimming alone is intentional and documented.
- HTF geometry uses the requested context and its timeframe inputs. Held validation deliberately uses chart closes and chart lifetime, documented at header lines 11–14. This is a design boundary, not a claim that held lifetime is measured in HTF bars.
- Exit values are captured before a new frame starts; RR drawing finishes the ended frame before constructing the replacement. The `not exitReference` guard prevents a same-bar new joint from revealing an old hidden frame.
- B ATR normalization at Pine 1036–1037 / Python 260–269 uses the already confirmed B pivot snapshot. Valleys read only past A..C-1 bars. Only the AB denominator changes; BC and original major family remain unchanged.

Documentation reference checked by reviewer: https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/

Limits: static review does not prove all Pine request.security historical runtime state. Python semantic oracle is not full native parity. Primary agent owns final integration, 51 test result, and native revision11 observations.
