#!/bin/bash
# Run one PowerShell command on the 3060 and get its output back.
#
# 2026-08-12: this box stopped answering plain `ssh host "cmd"`. The command
# runs, but the exec channel never closes and nothing comes back, so every
# `$(ssh ...)` in the 3060 scripts waits for an EOF that never arrives -- which
# from the Mac is indistinguishable from an unreachable machine, and is how a
# healthy 3060 got written up as "SSH not responding". ping, TCP 22, key auth,
# scp and nvidia-smi were fine throughout.
#
# What works: scp is reliable; remote PowerShell can write a result file even
# when the SSH exec channel hangs open. Ship the command as a .ps1 that writes
# its own output file, run it under a bounded client, then scp the file back.
# A hung client after the remote is done is treated as success if the out file
# is present and carries __rc__.
#
# Usage: bash scripts/ssh_ps.sh user@host '<powershell command text>'
# Env:   FABLE_SSH_DEADLINE (seconds per ssh/scp leg, default 60)
set -uo pipefail

[[ $# -ge 2 ]] || { echo "usage: ssh_ps.sh user@host <powershell command>" >&2; exit 2; }
host="$1"; shift
deadline="${FABLE_SSH_DEADLINE:-60}"
tag="ps_$$_$RANDOM"
remote_dir="C:/fable/_rpc"
remote_ps1="$remote_dir/$tag.ps1"
remote_out="$remote_dir/$tag.out"
local_script=$(mktemp -t "$tag")
local_out=$(mktemp -t "${tag}_out")

cleanup() { rm -f "$local_script" "$local_out"; }
trap cleanup EXIT

# Self-contained script: user body + rc marker, all streams into $tag.out.
# Do not rely on the remote shell's `*>` redirection — DefaultShell has changed
# more than once and is not a stable contract.
{
  printf '%s\n' "\$__out = '$remote_out'"
  printf '%s\n' "\$__rc = 0"
  printf '%s\n' "try {"
  printf '%s\n' "  \$__buf = & {"
  printf '%s\n' "$*"
  printf '%s\n' "  } *>&1 | ForEach-Object { \$_.ToString() }"
  printf '%s\n' "  \$__buf | Out-File -FilePath \$__out -Encoding utf8"
  printf '%s\n' "  if (\$null -ne \$LASTEXITCODE) { \$__rc = \$LASTEXITCODE }"
  printf '%s\n' "} catch {"
  printf '%s\n' "  \$_ | Out-File -FilePath \$__out -Encoding utf8"
  printf '%s\n' "  \$__rc = 1"
  printf '%s\n' "} finally {"
  printf '%s\n' "  Add-Content -Path \$__out -Value (\"__rc__=\" + \$__rc) -Encoding utf8"
  printf '%s\n' "}"
} >"$local_script"

bounded() {  # run one ssh/scp leg without letting a stuck channel hang the caller
  "$@" >/dev/null 2>&1 &
  local pid=$! waited=0
  while kill -0 "$pid" 2>/dev/null && (( waited < deadline )); do
    sleep 1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    return 124
  fi
  wait "$pid"
  return $?
}

SCP=(scp -o BatchMode=yes -o ConnectTimeout=15 -q)
# Prefer non-tty: -tt was observed to open+close with empty output after the
# DefaultShell fix. Non-tty still hangs sometimes, but the command runs and
# our .ps1 writes the out file regardless.
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=2)

# Ensure rpc dir exists (ignore hang; scp will prove it)
bounded "${SSH[@]}" "$host" "powershell -NoProfile -NonInteractive -Command \"New-Item -ItemType Directory -Force $remote_dir | Out-Null\"" || true

bounded "${SCP[@]}" "$local_script" "$host:$remote_ps1" \
  || { echo "ssh_ps: shipping command failed" >&2; exit 1; }

# Run; hang after remote completion is OK if the out file is fetchable.
exec_rc=0
bounded "${SSH[@]}" "$host" "powershell -NoProfile -NonInteractive -File $remote_ps1" || exec_rc=$?

fetch_rc=0
bounded "${SCP[@]}" "$host:$remote_out" "$local_out" || fetch_rc=$?
if [[ "$fetch_rc" -ne 0 || ! -s "$local_out" ]]; then
  # One retry after a short wait — remote may still be flushing.
  sleep 2
  bounded "${SCP[@]}" "$host:$remote_out" "$local_out" || fetch_rc=$?
fi

if [[ ! -s "$local_out" ]]; then
  echo "ssh_ps: remote execution produced no output (exec_rc=$exec_rc fetch_rc=$fetch_rc deadline=${deadline}s)" >&2
  exit 124
fi

# Best-effort cleanup; ignore hang.
bounded "${SSH[@]}" "$host" "powershell -NoProfile -NonInteractive -Command \"Remove-Item -Force $remote_ps1,$remote_out -ErrorAction SilentlyContinue\"" || true

rc=$(sed -e 's/\r$//' "$local_out" | sed -n 's/^__rc__=//p' | tail -1)
sed -e 's/\r$//' -e '/^__rc__=/d' "$local_out"
exit "${rc:-0}"
