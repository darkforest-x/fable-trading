#!/bin/zsh

set -u

ROOT="/Users/zhangzc/fable-trading-grok-2day"
RUNTIME="$ROOT/.omo/runtime"
RUNNER="$RUNTIME/run_grok_two_day.sh"
LOCK_PID="$RUNTIME/grok-worker.lock/pid"
STATUS="$RUNTIME/GROK_2DAY_STATUS.md"
END_FILE="$RUNTIME/end_epoch"
LOG="$RUNTIME/grok-supervisor.log"
POLL_SECONDS=120

while true; do
  now_epoch="$(date '+%s')"
  if [[ -f "$END_FILE" ]] && (( now_epoch >= $(<"$END_FILE") )); then
    printf '[%s] deadline reached\n' "$(date '+%F %T %Z')" >> "$LOG"
    exit 0
  fi
  if rg -q 'final_complete: true|FINAL_COMPLETE' "$STATUS"; then
    printf '[%s] final completion marker found\n' "$(date '+%F %T %Z')" >> "$LOG"
    exit 0
  fi

  runner_alive=false
  if [[ -f "$LOCK_PID" ]]; then
    runner_pid="$(<"$LOCK_PID")"
    if kill -0 "$runner_pid" 2>/dev/null; then
      runner_alive=true
    fi
  fi

  if [[ "$runner_alive" != true ]]; then
    session="fable_grok_auto_$(date '+%Y%m%d_%H%M%S')"
    printf '[%s] runner absent; starting %s\n' "$(date '+%F %T %Z')" "$session" >> "$LOG"
    screen -dmS "$session" /bin/zsh "$RUNNER"
    sleep 10
  fi

  sleep "$POLL_SECONDS"
done
