param([string]$SourceRoot = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent), [switch]$Uninstall)
# Per-user protocol only. No administrator rights, service, listener, browser
# policy, TradingView setting or scanner restart is required.
$ErrorActionPreference = 'Stop'
$chartLinkRoot = Join-Path $env:LOCALAPPDATA 'Spike\ChartLink'
$protocolKey = 'HKCU:\Software\Classes\spike-tradingview'
$owner = 'Spike.ChartLink.v1'
$existing = Get-ItemProperty -LiteralPath $protocolKey -ErrorAction SilentlyContinue
if ($existing -and $existing.SpikeOwner -ne $owner) { throw 'An unrelated application owns spike-tradingview; no changes made.' }
if ($Uninstall) {
    if ($existing) { Remove-Item -LiteralPath $protocolKey -Recurse }
    Write-Output 'Spike chart link unregistered. Installed files retained.'
    exit 0
}
if (-not (Get-AppxPackage -Name TradingView.Desktop)) { throw 'Install TradingView Desktop before installing this link handler.' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework compiler is unavailable.' }
$source = Join-Path $SourceRoot 'yoyo\monitor\windows_chart_link.cs'
$bridge = Join-Path $SourceRoot 'yoyo\monitor\windows_tradingview.ps1'
if (-not (Test-Path -LiteralPath $source) -or -not (Test-Path -LiteralPath $bridge)) { throw 'The chart link source bundle is incomplete.' }
New-Item -ItemType Directory -Force -Path $chartLinkRoot | Out-Null
# Switch the registration only after a complete build. An older warning dialog
# may still hold its executable open; updates must not replace or kill it.
$release = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.Substring(0,12) + '-' + (Get-FileHash -LiteralPath $bridge -Algorithm SHA256).Hash.Substring(0,12)
$releaseRoot = Join-Path $chartLinkRoot (Join-Path 'releases' $release)
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
$executable = Join-Path $releaseRoot 'SpikeChartLink.exe'
$installedBridge = Join-Path $releaseRoot 'windows_tradingview.ps1'
$temporary = Join-Path $releaseRoot 'SpikeChartLink.build.exe'
try {
    if (-not (Test-Path -LiteralPath $executable) -or -not (Test-Path -LiteralPath $installedBridge)) {
        & $compiler /nologo /target:winexe /optimize+ /codepage:65001 /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll "/out:$temporary" $source
        if ($LASTEXITCODE -ne 0) { throw 'Chart link compilation failed; existing registration unchanged.' }
        Copy-Item -LiteralPath $bridge -Destination $installedBridge -Force
        Move-Item -LiteralPath $temporary -Destination $executable -Force
    }
    New-Item -Path $protocolKey -Force | Out-Null
    Set-Item -LiteralPath $protocolKey -Value 'URL:Spike TradingView chart'
    New-ItemProperty -Path $protocolKey -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $protocolKey -Name SpikeOwner -Value $owner -PropertyType String -Force | Out-Null
    $command = New-Item -Path "$protocolKey\shell\open\command" -Force
    Set-Item -LiteralPath $command.PSPath -Value ('"' + $executable + '" "%1"')
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
}
Write-Output 'Installed: Windows coin links open this PC''s TradingView. No web fallback.'
