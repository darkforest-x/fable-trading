#!/bin/zsh

set -eu

ROOT="${0:A:h:h}"
source "$ROOT/scripts/lib/grok_retry_policy.zsh"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

telemetry_log="$tmp_dir/telemetry.log"
rate_limit_log="$tmp_dir/rate-limit.log"
healthy_log="$tmp_dir/healthy.log"

cat > "$telemetry_log" <<'EOF'
Batch result: all PASS
ERROR name="BatchSpanProcessor.Shutdown.Timeout" BatchSpanProcessor shutdown timing out.
EOF

cat > "$rate_limit_log" <<'EOF'
Error: HTTP 429 rate limit exceeded; try again later
EOF

cat > "$healthy_log" <<'EOF'
Batch result: all PASS
EOF

if grok_should_backoff 0 "$telemetry_log"; then
  print -u2 "telemetry shutdown timeout must not trigger failure backoff"
  exit 1
fi

if ! grok_should_backoff 0 "$rate_limit_log"; then
  print -u2 "HTTP 429 must trigger failure backoff"
  exit 1
fi

if ! grok_should_backoff 7 "$healthy_log"; then
  print -u2 "non-zero Grok exit must trigger failure backoff"
  exit 1
fi

print "grok retry policy: PASS"
