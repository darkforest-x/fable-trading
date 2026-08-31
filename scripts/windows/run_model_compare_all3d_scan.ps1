# Run the frozen five-checkpoint market scan inside a disposable 3060 bundle.
#
# This file is deliberately not a trainer and never receives a dataset build,
# labels, ACTIVE state, or execution credential.  The Mac has already frozen
# and hashed the OHLCV source files; the 3060 only performs offline inference
# and writes its result ledgers under the supplied disposable bundle root.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z]:/fable/model_compare_exp-15m-ma-launch-model-compare-all3d-20260831-v1$')]
    [string]$Root,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$SourceCommit,

    [ValidateRange(1, 128)]
    [int]$BatchSize = 16
)

$ErrorActionPreference = 'Stop'
$python = 'C:/fable/.venv/Scripts/python.exe'
$scanner = Join-Path $Root 'scripts/scan_15m_ma_launch_model_compare_all3d.py'
$out = Join-Path $Root 'analysis/output/ma_launch_model_compare_all3d_20260831_v1'
$results = Join-Path $Root 'experiments/active/exp-15m-ma-launch-model-compare-all3d-20260831-v1/results'
$exitPath = Join-Path $Root 'scan.exit'
$scanReceipt = Join-Path $results 'scan_receipt.json'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "missing controlled 3060 Python: $python"
}
if (-not (Test-Path -LiteralPath $scanner -PathType Leaf)) {
    throw "missing bundled scanner: $scanner"
}
if (Test-Path -LiteralPath $exitPath) {
    throw "refusing to overwrite completed/failed scan receipt marker: $exitPath"
}

Set-Location -LiteralPath $Root
& $python -u $scanner --scan --out $out --results $results --device 0 --batch-size $BatchSize --worker-source-commit $SourceCommit
$code = $LASTEXITCODE
# A killed child can leave PowerShell's $LASTEXITCODE at zero on this Windows
# host.  A zero marker is valid only when the scanner's atomic terminal receipt
# exists; otherwise a collector could mistake a partial log for completed
# inference and contaminate the frozen comparison.
if ($code -eq 0 -and -not (Test-Path -LiteralPath $scanReceipt -PathType Leaf)) {
    [Console]::Error.WriteLine("scanner exited zero without terminal receipt: $scanReceipt")
    $code = 97
}
[System.IO.File]::WriteAllText($exitPath, ("{0}`n" -f $code), [System.Text.Encoding]::ASCII)
exit $code
