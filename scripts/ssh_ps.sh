#!/bin/bash
# Run one PowerShell command on the 3060 and come back with its output.
#
# 2026-08-12: ssh to this box stopped behaving the way every 3060 script here
# assumes. The failure is silent -- commands run, output sometimes arrives, but
# the exec channel never closes, so `$(ssh ...)` waits for an EOF that never
# comes. From the Mac that is indistinguishable from an unreachable machine, and
# it is how a perfectly healthy 3060 got written up as "SSH not responding":
# ping, TCP 22, key auth, scp and nvidia-smi were fine the entire time.
#
# What was ruled out, in order:
#   * -o ConnectTimeout covers TCP and handshake only, never the stuck channel;
#   * bare PowerShell syntax as the remote command: the sshd default shell does
#     not run it the way the old scripts assume;
#   * -EncodedCommand: returns an empty stdout and a CLIXML error record here;
#   * -tt: closes the session, but the Windows console clears the screen and
#     eats the command output, leaving callers nothing to parse.
#
# What works: feed the script over stdin to `powershell -Command -`, and read
# the answer under a deadline so a channel that refuses to close cannot hang the
# caller. Killing the client can also kill a still-running remote command, so
# anything that must survive (unpacking, training) is launched detached through
# WMI and polled, never run inline.
#
# Usage: bash scripts/ssh_ps.sh user@host '<powershell command text>'
# Env:   FABLE_SSH_DEADLINE (seconds, default 90)
set -uo pipefail

[[ $# -ge 2 ]] || { echo "usage: ssh_ps.sh user@host <powershell command>" >&2; exit 2; }
host="$1"; shift
deadline="${FABLE_SSH_DEADLINE:-90}"

out=$(mktemp -t fable_ssh_ps)
script=$(mktemp -t fable_ssh_ps_cmd)
printf '%s\n' "$*" >"$script"

# ssh must stay in the foreground: backgrounding the client makes it produce no
# output at all on this host, so the deadline is enforced by a watchdog that
# kills the client instead of by running it as a job.
( sleep "$deadline"; pkill -9 -f "powershell -NoProfile -NonInteractive -Command -" >/dev/null 2>&1 ) &
watchdog=$!

ssh -o BatchMode=yes -o ConnectTimeout=15 \
  "$host" "powershell -NoProfile -NonInteractive -Command -" \
  <"$script" >"$out" 2>/dev/null
status=$?
kill "$watchdog" >/dev/null 2>&1
wait "$watchdog" 2>/dev/null
rm -f "$script"
# A killed client still delivered whatever the command printed; the channel hung,
# the command did not fail.
(( status == 137 )) && status=0

sed -e 's/\r$//' -e '/^#< CLIXML$/d' -e '/^<Objs /d' "$out"
rm -f "$out"
exit "$status"
