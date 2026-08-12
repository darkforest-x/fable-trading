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
# What was ruled out: -o ConnectTimeout (covers TCP and handshake only),
# -EncodedCommand (empty stdout plus a CLIXML error record), stdin-fed
# `-Command -` (works once on a fresh session, then stops), and backgrounding
# the client (produces no output at all).
#
# What works, every time: a pty session closes in ~3s, and scp is reliable. So
# the command is shipped as a file, run under -tt with every stream redirected
# into a file, and the result is fetched with scp. The pty eats console output,
# which is exactly why the answer travels back as a file instead of a stream.
#
# Usage: bash scripts/ssh_ps.sh user@host '<powershell command text>'
# Env:   FABLE_SSH_DEADLINE (seconds per ssh leg, default 60)
set -uo pipefail

[[ $# -ge 2 ]] || { echo "usage: ssh_ps.sh user@host <powershell command>" >&2; exit 2; }
host="$1"; shift
deadline="${FABLE_SSH_DEADLINE:-60}"
tag="ps_$$_$RANDOM"
remote_dir="C:/fable/_rpc"
local_script=$(mktemp -t "$tag")
local_out=$(mktemp -t "${tag}_out")

cleanup() { rm -f "$local_script" "$local_out"; }
trap cleanup EXIT

# $LASTEXITCODE is echoed on the last line so a caller can still see whether the
# remote command failed; the pty swallows exit status.
{
  printf '%s\n' "$*"
  printf '%s\n' 'Write-Output ("__rc__=" + $(if ($LASTEXITCODE -eq $null) { 0 } else { $LASTEXITCODE }))'
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
}

SCP=(scp -o BatchMode=yes -o ConnectTimeout=15 -q)
SSH_TTY=(ssh -tt -o BatchMode=yes -o ConnectTimeout=15)

bounded "${SSH_TTY[@]}" "$host" "powershell -NoProfile -NonInteractive -Command \"New-Item -ItemType Directory -Force $remote_dir | Out-Null\"" \
  || { echo "ssh_ps: remote rpc dir failed" >&2; exit 1; }
bounded "${SCP[@]}" "$local_script" "$host:$remote_dir/$tag.ps1" \
  || { echo "ssh_ps: shipping command failed" >&2; exit 1; }
bounded "${SSH_TTY[@]}" "$host" "powershell -NoProfile -NonInteractive -File $remote_dir/$tag.ps1 *> $remote_dir/$tag.out" \
  || { echo "ssh_ps: remote execution timed out after ${deadline}s" >&2; exit 124; }
bounded "${SCP[@]}" "$host:$remote_dir/$tag.out" "$local_out" \
  || { echo "ssh_ps: fetching output failed" >&2; exit 1; }
bounded "${SSH_TTY[@]}" "$host" "powershell -NoProfile -NonInteractive -Command \"Remove-Item -Force $remote_dir/$tag.* -ErrorAction SilentlyContinue\"" || true

rc=$(sed -e 's/\r$//' "$local_out" | sed -n 's/^__rc__=//p' | tail -1)
sed -e 's/\r$//' -e '/^__rc__=/d' "$local_out"
exit "${rc:-0}"
