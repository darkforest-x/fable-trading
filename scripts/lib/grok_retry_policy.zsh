#!/bin/zsh

# Return success when a Grok slot should use the long failure backoff.
grok_should_backoff() {
  local grok_rc="$1"
  local slot_log="$2"

  if (( grok_rc != 0 )); then
    return 0
  fi

  rg -v --text \
    'BatchSpanProcessor|Reference blob upload|batch_exists|dedup batch existence|SpansDropped' \
    "$slot_log" | rg -qi --text \
      'rate.?limit(ed| exceeded)?|quota (exceeded|exhausted)|usage limit|resource exhausted|try again later|network error|request timed out|connection error|http[ /-]?429'
}
