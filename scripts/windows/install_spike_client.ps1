param(
    [string]$InstallRoot = 'C:\fable\spike-client',
    [string]$Python = 'C:\fable\.venv\Scripts\pythonw.exe',
    [Parameter(Mandatory=$true)][string]$LayoutUrl
)
# Install only this display client, never the scanner, models, or notifications.
$ErrorActionPreference = 'Stop'
if ($LayoutUrl -cnotmatch '\Ahttps://(cn\.|www\.)?tradingview\.com/chart/[A-Za-z0-9]{1,64}/?\z') {
    throw 'Invalid saved TradingView layout URL'
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Python unavailable' }
if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot 'yoyo\monitor\desktop_client.py'))) {
    throw 'Copy the Spike client modules before installing'
}
$runtime = Join-Path $env:LOCALAPPDATA 'Spike'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
[IO.File]::WriteAllText((Join-Path $runtime 'tradingview-layout.txt'), $LayoutUrl.TrimEnd('/') + '/', (New-Object Text.UTF8Encoding($false)))
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $Python -Argument '-m yoyo.monitor.desktop_client' -WorkingDirectory $InstallRoot
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$name = 'SpikeDesktopClient'
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne 'Spike owned loopback display client; Mac is the scanner.') {
    throw 'An unrelated task already uses SpikeDesktopClient; inspect before replacing'
}
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Trigger $trigger `
    -Settings $settings -Description 'Spike owned loopback display client; Mac is the scanner.' -Force | Out-Null
if ($existing -and $existing.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $name
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-ScheduledTask -TaskName $name).State -eq 'Running') {
        if ((Get-Date) -gt $deadline) { throw 'Previous Spike display client did not stop; inspect its task before retrying' }
        Start-Sleep -Milliseconds 250
    }
}
Start-ScheduledTask -TaskName $name
$ready = $false
$deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline) {
    try {
        $client = Invoke-RestMethod 'http://127.0.0.1:8766/api/desktop' -TimeoutSec 2
        $ready = $client.target -eq 'windows' -and $client.source -eq 'mac' -and $client.transport -eq 'ssh-loopback'
    } catch { $ready = $false }
    if ($ready -and (Get-ScheduledTask -TaskName $name).State -eq 'Running') { break }
    $ready = $false
    Start-Sleep -Milliseconds 250
}
if (-not $ready) { throw 'Spike client did not become ready. Check Windows login, port 8766, and LOCALAPPDATA\Spike\client.log' }
$shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Spike.url'
[IO.File]::WriteAllText($shortcut, "[InternetShortcut]`r`nURL=http://127.0.0.1:8766/#signals`r`n", (New-Object Text.UTF8Encoding($false)))
Write-Output 'Spike installed: http://127.0.0.1:8766/#signals (this Windows desktop)'
