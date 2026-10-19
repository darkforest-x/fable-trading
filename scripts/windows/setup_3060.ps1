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
    [string]$TorchvisionVersion = "0.23.0",
    [string]$UltralyticsVersion = "8.4.89",
    [string]$NumpyVersion       = "2.0.2",
    # torch 2.8.0 has no Windows wheel in the cu124 index. The matching
    # cp39/win_amd64 wheels for torch + torchvision are in cu126.
    [string]$CudaIndex          = "https://download.pytorch.org/whl/cu126",
    # Optional Microsoft-signed Win32-OpenSSH MSI fallback. Use this when the
    # Windows capability installer hangs or DISM reports an inconsistent state.
    [string]$OpenSshServerMsi   = "",
    [switch]$SkipPython
)

$ErrorActionPreference = "Stop"
$script:Failures = @()

function Step { param($Name) Write-Host "`n=== $Name ===" -ForegroundColor Cyan }
function OK   { param($Msg)  Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Warn { param($Msg)  Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Bad  { param($Msg)  Write-Host "  [FAIL] $Msg" -ForegroundColor Red; $script:Failures += $Msg }
function Invoke-CheckedNative {
    param([string]$Label, [scriptblock]$Command)
    & $Command
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "$Label failed with native exit code $code"
    }
}

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
$sshdService = Get-Service -Name sshd -ErrorAction SilentlyContinue
if (-not $sshdService) {
    if ($OpenSshServerMsi) {
        if (-not (Test-Path $OpenSshServerMsi -PathType Leaf)) {
            throw "OpenSSH MSI not found: $OpenSshServerMsi"
        }
        $signature = Get-AuthenticodeSignature $OpenSshServerMsi
        if ($signature.Status -ne "Valid" -or
            $signature.SignerCertificate.Subject -notmatch "O=Microsoft Corporation") {
            throw "OpenSSH MSI is not validly signed by Microsoft: $OpenSshServerMsi"
        }
        Invoke-CheckedNative "OpenSSH Server MSI install" {
            & msiexec.exe /i $OpenSshServerMsi ADDLOCAL=Server /qn /norestart
        }
        OK "installed Microsoft-signed Win32-OpenSSH Server MSI"
    } else {
        $cap = Get-WindowsCapability -Online -Name OpenSSH.Server* | Select-Object -First 1
        if ($cap.State -ne "Installed") {
            Write-Host "  installing the Windows OpenSSH capability..."
            Warn "If DISM hangs, cancel and re-run with -OpenSshServerMsi <signed-msi-path>."
            Add-WindowsCapability -Online -Name $cap.Name | Out-Null
            OK "installed Windows capability"
        }
    }
    $sshdService = Get-Service -Name sshd -ErrorAction SilentlyContinue
}

if (-not $sshdService) { throw "sshd service is still missing after OpenSSH installation" }

# The Win32-OpenSSH MSI config uses a relative sftp-server.exe path. Its install
# directory therefore has to be in the machine PATH for modern scp/SFTP clients.
$openSshMsiDir = "C:\Program Files\OpenSSH"
if (Test-Path "$openSshMsiDir\sftp-server.exe") {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    if (($machinePath -split ";") -notcontains $openSshMsiDir) {
        [Environment]::SetEnvironmentVariable("Path", "$machinePath;$openSshMsiDir", "Machine")
        $env:Path = "$env:Path;$openSshMsiDir"
        OK "added $openSshMsiDir to the machine PATH for SFTP"
    }
}

Set-Service -Name sshd -StartupType Automatic
if ((Get-Service sshd).Status -ne "Running") { Start-Service sshd }
if ((Get-Service sshd).Status -eq "Running") { OK "sshd running, starts automatically" }
else { Bad "sshd did not start" }

# ------------------------------------------------------------ 2. firewall
Step "2. Firewall rule for port 22"
$sshRules = @(Get-NetFirewallRule -Name "sshd", "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)
if ($sshRules.Count -eq 0) {
    New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server (sshd)" `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 `
        -RemoteAddress LocalSubnet | Out-Null
    OK "created (LAN only)"
} else {
    $sshRules | Enable-NetFirewallRule
    $sshRules | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress LocalSubnet
    OK "already present; enabled and restricted to LocalSubnet"
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

$keyLines = @(if (Test-Path $keyFile) { Get-Content $keyFile } else { @() })
if (($keyLines -join "`n") -notmatch [regex]::Escape($MacPublicKey.Split(" ")[1])) {
    $keyLines += $MacPublicKey
    # Windows PowerShell 5.1's `-Encoding utf8` writes a BOM. sshd is happier
    # with a BOM-free key file, so write it explicitly.
    [IO.File]::WriteAllLines($keyFile, [string[]]$keyLines, (New-Object Text.UTF8Encoding($false)))
    OK "key appended to $keyFile"
} else { OK "key already present in $keyFile" }

if ($keyFile -like "*administrators_authorized_keys") {
    # Use SIDs rather than localized account names so this also works on a
    # Chinese Windows install. Reset first so an explicit ACE left by a prior
    # manual repair cannot survive /inheritance:r + /grant:r.
    Invoke-CheckedNative "resetting authorized_keys ACL" {
        & icacls.exe $keyFile /reset | Out-Null
    }
    Invoke-CheckedNative "locking authorized_keys inheritance" {
        & icacls.exe $keyFile /inheritance:r | Out-Null
    }
    Invoke-CheckedNative "locking authorized_keys ACL" {
        & icacls.exe $keyFile /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F" | Out-Null
    }
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
    Warn "train_dense.py is MISSING. It is the training entry point. Restore the"
    Warn "  verified Mac backup before training; do not substitute another trainer."
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
        Invoke-CheckedNative "pip upgrade" {
            & $py -m pip install --upgrade pip --quiet
        }

        # PowerShell 5.1 does not turn a failed native command into a terminating
        # error merely because ErrorActionPreference=Stop. Check every pip exit
        # code so a missing CUDA wheel cannot fall through to a later CPU install.
        $torchReport = "$FableRoot\logs\pip-torch-dry-run.json"
        Invoke-CheckedNative "torch/torchvision dry-run" {
            & $py -m pip install --dry-run --report $torchReport `
                "torch==$TorchVersion" "torchvision==$TorchvisionVersion" `
                --index-url $CudaIndex
        }
        Write-Host "  installing torch $TorchVersion + torchvision $TorchvisionVersion (CUDA build, large download)..."
        Invoke-CheckedNative "torch/torchvision install" {
            & $py -m pip install "torch==$TorchVersion" "torchvision==$TorchvisionVersion" `
                --index-url $CudaIndex --quiet
        }

        $appReport = "$FableRoot\logs\pip-app-dry-run.json"
        Invoke-CheckedNative "application packages dry-run" {
            & $py -m pip install --dry-run --report $appReport `
                "ultralytics==$UltralyticsVersion" "numpy==$NumpyVersion"
        }
        Invoke-CheckedNative "application packages install" {
            & $py -m pip install "ultralytics==$UltralyticsVersion" `
                "numpy==$NumpyVersion" --quiet
        }
        Invoke-CheckedNative "pip check" { & $py -m pip check }
        OK "packages installed"
    }
}

# -------------------------------------------------------------- 7. verify
Step "7. Self-check"
$py = "$FableRoot\.venv\Scripts\python.exe"
if (Test-Path $py) {
    $probe = & $py -c "import torch,torchvision,ultralytics,numpy;print(torch.__version__.split('+')[0],torchvision.__version__.split('+')[0],ultralytics.__version__,numpy.__version__,torch.cuda.is_available())" 2>&1
    Write-Host "  $probe"
    $parts = "$probe".Trim().Split(" ")
    if ($parts.Count -ge 5) {
        if ($parts[0] -ne $TorchVersion)       { Bad "torch $($parts[0]) != $TorchVersion -- train_on_3060.sh will refuse to train" } else { OK "torch $TorchVersion" }
        if ($parts[1] -ne $TorchvisionVersion) { Bad "torchvision $($parts[1]) != $TorchvisionVersion" } else { OK "torchvision $TorchvisionVersion" }
        if ($parts[2] -ne $UltralyticsVersion) { Bad "ultralytics $($parts[2]) != $UltralyticsVersion" } else { OK "ultralytics $UltralyticsVersion" }
        if ($parts[3] -ne $NumpyVersion)       { Bad "numpy $($parts[3]) != $NumpyVersion" } else { OK "numpy $NumpyVersion" }
        if ($parts[4] -ne "True")              { Bad "torch.cuda.is_available() is False -- CPU-only build, training would be pointlessly slow" } else { OK "CUDA available" }
    } else { Bad "could not probe the venv: $probe" }

    $nmsProbe = & $py -c "import torch,torchvision;b=torch.tensor([[0.,0.,10.,10.],[1.,1.,9.,9.]],device='cuda');s=torch.tensor([.9,.8],device='cuda');print(torchvision.ops.nms(b,s,.5).cpu().tolist()==[0])" 2>&1
    if ("$nmsProbe".Trim() -eq "True") { OK "torchvision CUDA NMS" }
    else { Bad "torchvision CUDA NMS failed: $nmsProbe" }
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
    Write-Host "  scripts/train_on_3060.sh --check"
} else {
    Write-Host "$($script:Failures.Count) CHECK(S) FAILED:" -ForegroundColor Red
    $script:Failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "`nRe-running this script after fixing them is safe; every step is idempotent." -ForegroundColor Yellow
    exit 1
}
