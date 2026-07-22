#!/bin/bash
# Status for owner_v15_tipval on Windows 3060.
set -euo pipefail
HOST="${FABLE_3060_HOST:-zzc@192.168.1.3}"
NAME="${NAME:-owner_v15_tipval_oomfix}"
# Expand NAME in bash; keep PowerShell $_ escaped as \$_.
ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "
Write-Output ('=== {0} ===' -f (Get-Date))
\$procs = Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -match 'train_dense.*$NAME' }
if (\$procs) { Write-Output ('running pid={0}' -f (\$procs.ProcessId -join ',')) } else { Write-Output 'not running' }
if (Test-Path 'C:\fable\logs\\$NAME.log') {
  Get-Content 'C:\fable\logs\\$NAME.log' -Tail 40
} else { Write-Output 'no log yet' }
Write-Output '--- weights ---'
@(
  \"C:\\fable\\runs\\detect\\runs\\detect\\$NAME\\weights\\best.pt\",
  \"C:\\fable\\runs\\detect\\$NAME\\weights\\best.pt\",
  \"C:\\fable\\models\\$NAME.pt\"
) | ForEach-Object { if (Test-Path \$_) { Get-Item \$_ | Select-Object FullName,Length,LastWriteTime } }
" | tr -d '\r'
