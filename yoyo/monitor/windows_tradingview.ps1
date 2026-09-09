param([Parameter(Mandatory=$true)][string]$ChartUrl)
# Only a validated chart URI is accepted, including when invoked outside Python.
$ErrorActionPreference = 'Stop'
$stage = 'validate'
try {
    if ($ChartUrl -cnotmatch '\Ahttps://(cn\.|www\.)?tradingview\.com/chart/[A-Za-z0-9]{1,64}/\?symbol=OKX%3A[A-Z0-9]{3,40}\.P&interval=(5|15|30|60|240|1D)\z') {
        throw 'SPIKE_INVALID_URL'
    }
    $session = (Get-Process -Id $PID).SessionId
    $desktop = Get-Process explorer -ErrorAction SilentlyContinue | Where-Object SessionId -eq $session
    if ($session -eq 0 -or -not $desktop) { throw 'SPIKE_INTERACTIVE_SESSION_REQUIRED' }
    $stage = 'package'
    $package = Get-AppxPackage -Name TradingView.Desktop | Select-Object -First 1
    if (-not $package) { throw 'SPIKE_TV_NOT_INSTALLED' }
    $exe = Join-Path $package.InstallLocation 'TradingView.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'SPIKE_TV_NOT_INSTALLED' }
    # TradingView's Windows second-instance handler consumes the last HTTPS argv.
    # Use the registered package's executable instead of the browser URL handler.
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $exe
    $start.Arguments = '"' + $ChartUrl + '"'
    # ShellExecute detaches the GUI child from our receipt pipe. Inherited
    # stdout adds TradingView logger text and can keep the request pipe open.
    $start.UseShellExecute = $true
    $stage = 'dispatch'
    $child = [System.Diagnostics.Process]::Start($start)
    if (-not $child) { throw 'SPIKE_DISPATCH_FAILED' }
    $child.Dispose()
    Write-Output 'requested'
} catch {
    $code = [string]$_.Exception.Message
    if ($code -notin @('SPIKE_INVALID_URL', 'SPIKE_INTERACTIVE_SESSION_REQUIRED', 'SPIKE_TV_NOT_INSTALLED')) {
        $code = 'SPIKE_DISPATCH_FAILED'
    }
    [Console]::Error.WriteLine($code + ';stage=' + $stage + ';hresult=' + $_.Exception.HResult)
    exit 1
}
