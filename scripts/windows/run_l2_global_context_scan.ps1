# Run the frozen 15m L2 candidate scan inside a disposable RTX 3060 bundle.
# The worker performs inference only.  Dataset construction, L2 training,
# rendering and reporting remain on the Mac after the terminal scan receipt is
# collected and verified.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^C:/fable/l2_exp-15m-ma-launch-global-context-v1$')]
    [string]$Root,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$SourceCommit,

    [ValidateRange(1, 128)]
    [int]$BatchSize = 32
)

$ErrorActionPreference = 'Stop'
$python = 'C:/fable/.venv/Scripts/python.exe'
$scanner = Join-Path $Root 'scripts/research_15m_ma_launch_l2_global_context.py'
$prereg = Join-Path $Root 'experiments/active/exp-15m-ma-launch-l2-global-context-v1/preregistration.json'
$out = Join-Path $Root 'analysis/output/ma_launch_l2_global_context_v1'
$results = Join-Path $Root 'experiments/active/exp-15m-ma-launch-l2-global-context-v1/results'
$exitPath = Join-Path $Root 'scan.exit'
$scanReceipt = Join-Path $results 'scan_receipt.json'

foreach ($required in @($python, $scanner, $prereg)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "missing immutable scan input: $required"
    }
}
if (Test-Path -LiteralPath $exitPath) {
    throw "refusing to overwrite completed/failed scan marker: $exitPath"
}

Set-Location -LiteralPath $Root
& $python -u $scanner --scan --prereg $prereg --out $out --results $results --device 0 --batch $BatchSize --replicated-source-commit $SourceCommit
$code = $LASTEXITCODE
# A zero process exit is not success unless the scanner atomically published its
# terminal receipt.  This prevents a killed child or partial ledger from being
# collected as a completed frozen scan.
if ($code -eq 0 -and -not (Test-Path -LiteralPath $scanReceipt -PathType Leaf)) {
    [Console]::Error.WriteLine("scanner exited zero without terminal receipt: $scanReceipt")
    $code = 97
}
[System.IO.File]::WriteAllText($exitPath, ("{0}`n" -f $code), [System.Text.Encoding]::ASCII)
exit $code
