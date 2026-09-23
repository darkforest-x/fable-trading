# Historical replay API contract

Research-only, all timestamps are Unix milliseconds. The UI uses Asia/Shanghai.
Only disclosed bars appear in responses. Standard frozen images are server-rendered
from the exact causal rows; those same bytes are submitted to the model.

- `GET /api/replay/catalog` → `{items:[{symbol,timeframe,source_label}], warning}`.
- `GET /api/replay/coverage?symbol=...&timeframe=...` → `{first_close_ms,last_close_ms,source_label,segments:[{first_close_ms,last_close_ms,first_replay_close_ms}]}`. Only segments with enough warmup/display history are selectable.
- `POST /api/replay/sessions` with `{symbol,timeframe,start_ms,mode:"free"|"blind"}` → session.
- `GET /api/replay/sessions` → `{items:[{id,symbol,timeframe,mode,cursor_ms,created_at}]}`.
- `GET /api/replay/sessions/{id}` → session.
- `POST /api/replay/sessions/{id}/move` with `{expected_cursor_ms,steps}` or `{expected_cursor_ms,target_ms}` → session. Blind mode only moves forward. Steps 1/5/10 or negative equivalents in free mode. End of data returns a clear error and stops playback.
- `POST /api/replay/sessions/{id}/freeze` with `{expected_cursor_ms,criteria?,reference_revision?}` → observation. Freeze pauses playback. Same cursor+criteria+reference revision reuses the observation.
- `GET /api/replay/observations/{id}` → observation.
- `GET /api/replay/observations/{id}/export` → the same immutable input and append-only results as a downloadable JSON document.
- `POST /api/replay/observations/{id}/judgment` with `{current_state,side,note}` → observation. State is converging/launching/extended/no_setup/unclear; side long/short/unknown. First judgment immutable; blind label must precede AI and revealing later bars.
- `POST /api/replay/observations/{id}/analyze` with `{}` → observation containing `run`. Uses frozen criteria and references; shared inference lock; one model attempt per observation. Blind mode requires prior judgment. Does not autoplay or send recurring requests.
- `POST /api/replay/observations/{id}/followup` with `{expected_cursor_ms,note}` → observation with appended followups at a later session cursor. Followup includes time, frozen image, bars_elapsed and close_change_pct (descriptive price change, not trading PnL).

Session: `{id,symbol,timeframe,mode,cursor_ms,first_cursor_ms,last_cursor_ms,seen_until_ms,created_at,chart:{candles,chart_sha256,provenance,colors},observations:[{id,cursor_ms,image_url,human,run_id,status}]}`.
Last cursor is a historical coverage bound, not future prices. Candle fields match
the existing chart: t/o/h/l/c and sma20/ema20/sma60/ema60/sma120/ema120.

Observation: `{id,session_id,symbol,timeframe,cursor_ms,image_url,image_sha256,chart_sha256,criteria,reference_revision,references,human,run_id,run,followups,mode,independence,status}`. `human` contains current_state/side/note/created_at; `run` is null until invoked and follows existing run schema. Independence is a recorded exposure description, not a gold certification.

Errors use `{detail:"user-readable Chinese"}` with 400/404/409/503. No secret,
future candles, future prices, or private source paths are sent to the frontend.
