#!/bin/zsh

set -u

ROOT="/Users/zhangzc/fable-trading-grok-2day"
RUNTIME="$ROOT/.omo/runtime"
EVIDENCE="$ROOT/.omo/evidence"
PROMPT_FILE="$RUNTIME/RUN_PROMPT_COMPACT.md"
NEXT_TASK_FILE="$RUNTIME/NEXT_TASK.md"
STATUS_FILE="$RUNTIME/GROK_2DAY_STATUS.md"
LOG_FILE="$RUNTIME/grok-runner.log"
END_FILE="$RUNTIME/end_epoch"
LOCK_DIR="$RUNTIME/grok-worker.lock"
GROK="/Users/zhangzc/.grok/bin/grok"
CODEX="/Applications/ChatGPT.app/Contents/Resources/codex"
CODEX_THREAD_ID="019f44e5-10f7-79a3-892f-6abdd78ae054"
CODEX_HANDOFF_LOG="$RUNTIME/codex-event-handoff.log"
CODEX_HANDOFF_LOCK="$RUNTIME/codex-event-handoff.lock"
RETRY_POLICY="$ROOT/scripts/lib/grok_retry_policy.zsh"
INTERVAL_SECONDS=18000
SUCCESS_COOLDOWN_SECONDS=60
MAX_SLOTS=24
active_child_pid=""

source "$RETRY_POLICY"

mkdir -p "$RUNTIME" "$EVIDENCE"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [[ -f "$LOCK_DIR/pid" ]]; then
    lock_pid="$(<"$LOCK_DIR/pid")"
    if kill -0 "$lock_pid" 2>/dev/null; then
      printf '[%s] scheduler already active pid=%s\n' "$(date '+%F %T %Z')" "$lock_pid" >> "$LOG_FILE"
      exit 0
    fi
  fi

  stale_lock="$RUNTIME/grok-worker.lock.stale.$(date '+%Y%m%d_%H%M%S')"
  mv "$LOCK_DIR" "$stale_lock"
  mkdir "$LOCK_DIR"
fi

printf '%s\n' "$$" > "$LOCK_DIR/pid"

cleanup() {
  if [[ -n "$active_child_pid" ]] && kill -0 "$active_child_pid" 2>/dev/null; then
    kill -TERM "$active_child_pid" 2>/dev/null || true
    wait "$active_child_pid" 2>/dev/null || true
  fi
  rm -f "$LOCK_DIR/pid"
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

terminate() {
  exit_code="$1"
  if [[ -n "$active_child_pid" ]] && kill -0 "$active_child_pid" 2>/dev/null; then
    kill -TERM "$active_child_pid" 2>/dev/null || true
    wait "$active_child_pid" 2>/dev/null || true
    active_child_pid=""
  fi
  exit "$exit_code"
}

trap cleanup EXIT
trap 'terminate 130' INT
trap 'terminate 143' TERM

run_codex_handoff() {
  local completed_slot="$1"
  local grok_exit="$2"
  local completed_log="$3"
  local handoff_prompt

  if ! mkdir "$CODEX_HANDOFF_LOCK" 2>/dev/null; then
    printf '[%s] codex handoff skipped: lock active\n' "$(date '+%F %T %Z')" \
      >> "$LOG_FILE"
    return 0
  fi

  handoff_prompt="Grok 子代理 slot=${completed_slot} 已结束，exit=${grok_exit}，日志=${completed_log}。这是事件驱动接力，不是4小时例行检查。立即核验该 slot 的真实工具调用、产物、测试、git commit/push、GROK_2DAY_STATUS.md 和 NEXT_TASK.md。若完成，马上把 NEXT_TASK.md 改为下一个原子任务；若空退、失败或状态过期，诊断原因并改写修复任务。重活继续交给 Grok 4.5，不要重复实现；不要停止正在等待你的 runner。holdout 封存，禁止实盘、泄密、VPS executor 和 force push。最终只用中文4-6行报告真实结果与下一任务。"

  printf '[%s] codex event handoff start slot=%s\n' \
    "$(date '+%F %T %Z')" "$completed_slot" >> "$LOG_FILE"
  "$CODEX" exec resume \
    -m gpt-5.3-codex-spark \
    --dangerously-bypass-approvals-and-sandbox \
    "$CODEX_THREAD_ID" \
    "$handoff_prompt" >> "$CODEX_HANDOFF_LOG" 2>&1
  local codex_rc=$?
  printf '[%s] codex event handoff exit=%s slot=%s\n' \
    "$(date '+%F %T %Z')" "$codex_rc" "$completed_slot" >> "$LOG_FILE"
  rmdir "$CODEX_HANDOFF_LOCK" 2>/dev/null || true
  return "$codex_rc"
}

if [[ ! -f "$END_FILE" ]]; then
  date -v+48H '+%s' > "$END_FILE"
fi

deadline_epoch="$(<"$END_FILE")"
printf '[%s] scheduler started pid=%s deadline_epoch=%s\n' \
  "$(date '+%F %T %Z')" "$$" "$deadline_epoch" >> "$LOG_FILE"

slot=1
while (( slot <= MAX_SLOTS )); do
  now_epoch="$(date '+%s')"
  if (( now_epoch >= deadline_epoch )); then
    printf '[%s] deadline reached before slot=%s\n' "$(date '+%F %T %Z')" "$slot" >> "$LOG_FILE"
    break
  fi

  if rg -q 'final_complete: true|FINAL_COMPLETE' "$STATUS_FILE"; then
    printf '[%s] final completion marker found\n' "$(date '+%F %T %Z')" >> "$LOG_FILE"
    break
  fi

  printf '[%s] slot=%s grok start\n' "$(date '+%F %T %Z')" "$slot" >> "$LOG_FILE"
  prompt_text="$(<"$PROMPT_FILE")

--- WORKER CONTRACT ---
$(<"$RUNTIME/WORKER_CONTRACT.md")

--- COMPACT STATUS ---
$(<"$STATUS_FILE")

--- NEXT ITERATION PACKET ---
$(<"$NEXT_TASK_FILE")"
  slot_log="$RUNTIME/grok-slot-${slot}-$(date '+%Y%m%d_%H%M%S').log"
  grok_model="grok-4.5"
  printf '[%s] slot=%s model=%s\n' "$(date '+%F %T %Z')" "$slot" "$grok_model" >> "$LOG_FILE"

  "$GROK" \
    --cwd "$ROOT" \
    --model "$grok_model" \
    --reasoning-effort high \
    --no-subagents \
    --no-memory \
    --max-turns 90 \
    --always-approve \
    --permission-mode bypassPermissions \
    --output-format plain \
    -p "$prompt_text" > "$slot_log" 2>&1 &
  active_child_pid=$!
  wait "$active_child_pid"
  grok_rc=$?
  active_child_pid=""
  cat "$slot_log" >> "$LOG_FILE"

  printf '[%s] slot=%s grok exit=%s\n' "$(date '+%F %T %Z')" "$slot" "$grok_rc" >> "$LOG_FILE"
  run_codex_handoff "$slot" "$grok_rc" "$slot_log" || true
  slot=$((slot + 1))

  now_epoch="$(date '+%s')"
  if (( slot > MAX_SLOTS || now_epoch >= deadline_epoch )); then
    break
  fi
  if rg -q 'final_complete: true|FINAL_COMPLETE' "$STATUS_FILE"; then
    break
  fi

  remaining=$((deadline_epoch - now_epoch))
  sleep_seconds=$SUCCESS_COOLDOWN_SECONDS
  if grok_should_backoff "$grok_rc" "$slot_log"; then
    sleep_seconds=$INTERVAL_SECONDS
  fi
  if (( remaining < sleep_seconds )); then
    sleep_seconds=$remaining
  fi
  printf '[%s] sleeping=%s seconds before next slot\n' "$(date '+%F %T %Z')" "$sleep_seconds" >> "$LOG_FILE"
  sleep "$sleep_seconds" &
  active_child_pid=$!
  wait "$active_child_pid" || true
  active_child_pid=""
done

printf '[%s] scheduler stopped after completed_slots=%s\n' \
  "$(date '+%F %T %Z')" "$((slot - 1))" >> "$LOG_FILE"
