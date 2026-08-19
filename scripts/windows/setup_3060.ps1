<#
.SYNOPSIS
    Rebuild the RTX 3060 training box after an OS reinstall.

.DESCRIPTION
    Run this ON THE WINDOWS MACHINE, in an ADMINISTRATOR PowerShell.

    Every step is idempotent -- re-running is safe and is the intended way to
    fix a partial run. Every step verifies itself and says what failed rather
    than continuing quietly, because the two failure modes this box has
    historically produced are both silent:

      - administrators_authorized_keys with wrong ACLs: sshd ignores the file
        and just keeps asking for a password, never saying why
      - a CPU-only torch: training runs, and is uselessly slow

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup_3060.ps1
#>
[CmdletBinding()]
param(
    [string]$MacPublicKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAjtCeQsUhmpprxG8xmQ+dzCMsD95G9r+7yllPn39owl antigravity",
    [string]$FableRoot    = "C:\fable",
    # Must match the Mac exactly: scripts/train_on_3060.sh refuses to train when
    # the two sides differ, because the numbers stop being comparable with the
    # historical curves.
    [string]$TorchVersion       = "2.8.0",
    [string]$UltralyticsVersion = "8.4.89",
    [string]$NumpyVersion       = "2.0.2",
    [string]$CudaIndex          = "https://download.pytorch.org/whl/cu124",
    [switch]$SkipPython
)

$ErrorActionPreference = "Stop"
$script:Failures = @()

function Step { param($Name) Write-Host "`n=== $Name ===" -ForegroundColor Cyan }
function OK   { param($Msg)  Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Warn { param($Msg)  Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Bad  { param($Msg)  Write-Host "  [FAIL] $Msg" -ForegroundColor Red; $script:Failures += $Msg }

# ---------------------------------------------------------------- 0. admin
Step "0. Checking for administrator rights"
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Bad "Not running as Administrator. Right-click PowerShell -> Run as administrator, then re-run."
    Write-Host "`nAborted." -ForegroundColor Red
    exit 1
}
OK "running as $($identity.Name)"

# ------------------------------------------------------------ 1. OpenSSH
Step "1. OpenSSH Server"
$cap = Get-WindowsCapability -Online -Name OpenSSH.Server* | Select-Object -First 1
if ($cap.State -ne "Installed") {
    Write-Host "  installing OpenSSH.Server (this can take a minute)..."
    Add-WindowsCapability -Online -Name $cap.Name | Out-Null
    OK "installed"
} else { OK "already installed" }

Set-Service -Name sshd -StartupType Automatic
if ((Get-Service sshd).Status -ne "Running") { Start-Service sshd }
if ((Get-Service sshd).Status -eq "Running") { OK "sshd running, starts automatically" }
else { Bad "sshd did not start" }

# ------------------------------------------------------------ 2. firewall
Step "2. Firewall rule for port 22"
if (-not (Get-NetFirewallRule -Name "sshd" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server (sshd)" `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    OK "created"
} else {
    Enable-NetFirewallRule -Name sshd
    OK "already present, ensured enabled"
}

# -------------------------------------------------------- 3. default shell
Step "3. Default shell = PowerShell"
# Every script in the repo is written for PowerShell. Under cmd, `dir /b`
# against C:/fable fails with a UNC error and the failure looks like a missing
# directory rather than a wrong shell.
$psPath = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
New-Item -Path "HKLM:\SOFTWARE\OpenSSH" -Force | Out-Null
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
    -Value $psPath -PropertyType String -Force | Out-Null
OK "DefaultShell -> powershell.exe"

# ------------------------------------------------------- 4. authorized key
Step "4. Installing the Mac's public key"
$isAdminUser = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdminUser) {
    # THE GOTCHA: for members of Administrators, sshd reads ONLY this file, and
    # silently refuses it unless the ACL is exactly Administrators + SYSTEM.
    $keyFile = "C:\ProgramData\ssh\administrators_authorized_keys"
} else {
    $keyFile = "$env:USERPROFILE\.ssh\authorized_keys"
    New-Item -ItemType Directory -Force -Path (Split-Path $keyFile) | Out-Null
}

$existing = if (Test-Path $keyFile) { Get-Content $keyFile -Raw } else { "" }
if ($existing -notmatch [regex]::Escape($MacPublicKey.Split(" ")[1])) {
    Add-Content -Path $keyFile -Value $MacPublicKey -Encoding utf8
    OK "key appended to $keyFile"
} else { OK "key already present in $keyFile" }

if ($keyFile -like "*administrators_authorized_keys") {
    icacls $keyFile /inheritance:r          | Out-Null
    icacls $keyFile /grant "Administrators:F" | Out-Null
    icacls $keyFile /grant "SYSTEM:F"       | Out-Null
    OK "ACL locked to Administrators + SYSTEM (sshd rejects it otherwise, without saying so)"
}
Restart-Service sshd
OK "sshd restarted"

# ------------------------------------------------------------ 5. C:\fable
Step "5. $FableRoot layout"
New-Item -ItemType Directory -Force -Path $FableRoot          | Out-Null
New-Item -ItemType Directory -Force -Path "$FableRoot\datasets" | Out-Null
New-Item -ItemType Directory -Force -Path "$FableRoot\logs"     | Out-Null
OK "$FableRoot, datasets\, logs\"

if (-not (Test-Path "$FableRoot\train_dense.py")) {
    Warn "train_dense.py is MISSING. It is the training entry point, it only ever"
    Warn "  existed on this machine, and it was never committed to the repository."
    Warn "  Look for a backup before training anything. Nothing else was lost in"
    Warn "  the reinstall -- all 20 weight families are on the Mac."
} else { OK "train_dense.py present" }

# ------------------------------------------------------------- 6. python
if (-not $SkipPython) {
    Step "6. Python venv and exact package versions"
    $py = "$FableRoot\.venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if (-not $launcher) {
            Bad "no `py` launcher. Install Python 3.9 from python.org, tick 'Add to PATH', re-run."
        } else {
            & py -3.9 -m venv "$FableRoot\.venv"
            if (Test-Path $py) { OK "venv created" } else { Bad "venv creation failed" }
        }
    } else { OK "venv already exists" }

    if (Test-Path $py) {
        & $py -m pip install --upgrade pip --quiet
        Write-Host "  installing torch $TorchVersion (CUDA build, large download)..."
        & $py -m pip install "torch==$TorchVersion" --index-url $CudaIndex --quiet
        & $py -m pip install "ultralytics==$UltralyticsVersion" "numpy==$NumpyVersion" --quiet
        OK "packages installed"
    }
}

# -------------------------------------------------------------- 7. verify
Step "7. Self-check"
$py = "$FableRoot\.venv\Scripts\python.exe"
if (Test-Path $py) {
    $probe = & $py -c "import torch,ultralytics,numpy;print(torch.__version__.split('+')[0],ultralytics.__version__,numpy.__version__,torch.cuda.is_available())" 2>&1
    Write-Host "  $probe"
    $parts = "$probe".Trim().Split(" ")
    if ($parts.Count -ge 4) {
        if ($parts[0] -ne $TorchVersion)       { Bad "torch $($parts[0]) != $TorchVersion -- train_on_3060.sh will refuse to train" } else { OK "torch $TorchVersion" }
        if ($parts[1] -ne $UltralyticsVersion) { Bad "ultralytics $($parts[1]) != $UltralyticsVersion" } else { OK "ultralytics $UltralyticsVersion" }
        if ($parts[2] -ne $NumpyVersion)       { Bad "numpy $($parts[2]) != $NumpyVersion" } else { OK "numpy $NumpyVersion" }
        if ($parts[3] -ne "True")              { Bad "torch.cuda.is_available() is False -- CPU-only build, training would be pointlessly slow" } else { OK "CUDA available" }
    } else { Bad "could not probe the venv: $probe" }
} else { Warn "no venv to check" }

$gpu = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>&1)
if ($LASTEXITCODE -eq 0) { OK "GPU: $gpu" } else { Bad "nvidia-smi failed -- install the NVIDIA driver" }

# --------------------------------------------------------------- summary
Step "Report this back to the Mac"
$ips = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" }).IPAddress
Write-Host "  hostname : $env:COMPUTERNAME"
Write-Host "  user     : $env:USERNAME"
Write-Host "  IPv4     : $($ips -join ', ')"
Write-Host "  GPU      : $gpu"

Write-Host ""
if ($script:Failures.Count -eq 0) {
    Write-Host "ALL CHECKS PASSED." -ForegroundColor Green
    Write-Host "From the Mac:" -ForegroundColor Green
    Write-Host "  export FABLE_3060_HOST=$env:USERNAME@$($ips[0])"
    Write-Host "  ssh `$FABLE_3060_HOST 'hostname; nvidia-smi --query-gpu=name --format=csv,noheader'"
    Write-Host "  scripts/train_on_3060.sh --check-only"
} else {
    Write-Host "$($script:Failures.Count) CHECK(S) FAILED:" -ForegroundColor Red
    $script:Failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "`nRe-running this script after fixing them is safe; every step is idempotent." -ForegroundColor Yellow
}
