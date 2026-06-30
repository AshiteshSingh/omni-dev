<#
  Omni-Dev installer (Windows / PowerShell)

  One-line install:
    irm https://raw.githubusercontent.com/AshiteshSingh/omni-dev/main/install.ps1 | iex

  What it does:
    1. Clones (or updates) the repo into  %LOCALAPPDATA%\omni-dev
    2. Creates a Python virtualenv and installs dependencies
    3. Installs an `omni` command on your PATH so you can run the CLI
       from any project directory just by typing:  omni
#>

param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "omni-dev"),
    [string]$Repo       = "https://github.com/AshiteshSingh/omni-dev",
    [string]$Branch     = "main",
    [string]$BinDir     = (Join-Path $env:USERPROFILE ".omni\bin")
)

$ErrorActionPreference = "Continue"
try { $PSNativeCommandUseErrorActionPreference = $false } catch {}

function Info($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "  $m" -ForegroundColor Red; throw $m }

# Run a native command quietly and return its exit code. Capturing the merged
# 2>&1 stream INTO A VARIABLE is the key: it swallows git/pip stderr as plain
# data instead of letting PowerShell surface it as a terminating NativeCommandError.
function Invoke-Quiet {
    param([string]$Exe, [string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $captured = & $Exe @Arguments 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return $code
}

Write-Host ""
Write-Host "  OMNI-DEV installer" -ForegroundColor Magenta
Write-Host ""

# --- Prerequisites ---------------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git is required but not found. Install Git, then re-run."
}
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) { Die "Python 3.10+ is required but not found. Install Python, then re-run." }
$pyExe = $py.Source

# --- Clone or update -------------------------------------------------------
if (Test-Path (Join-Path $InstallDir ".git")) {
    Info "Updating existing install at $InstallDir ..."
    [void](Invoke-Quiet "git" @("-C", $InstallDir, "fetch", "--depth", "1", "origin", $Branch))
    $code = Invoke-Quiet "git" @("-C", $InstallDir, "checkout", "-B", $Branch, "origin/$Branch")
    if ($code -ne 0) { Die "git update failed (exit $code)." }
} else {
    if (Test-Path $InstallDir) {
        Warn "Removing existing non-git folder at $InstallDir ..."
        Remove-Item -Recurse -Force $InstallDir
    }
    Info "Cloning $Repo (branch $Branch) ..."
    $code = Invoke-Quiet "git" @("clone", "--quiet", "--depth", "1", "--branch", $Branch, $Repo, $InstallDir)
    if ($code -ne 0) { Die "git clone failed (exit $code)." }
}
if (-not (Test-Path (Join-Path $InstallDir "omni_dev.py"))) {
    Die "Install failed: omni_dev.py not found in $InstallDir"
}

# --- Virtualenv + dependencies --------------------------------------------
$venvPython = Join-Path $InstallDir "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Info "Creating virtualenv ..."
    [void](Invoke-Quiet $pyExe @("-m", "venv", (Join-Path $InstallDir "venv")))
    if (-not (Test-Path $venvPython)) { Die "Failed to create virtualenv." }
}
Info "Installing dependencies (this can take a minute) ..."
[void](Invoke-Quiet $venvPython @("-m", "pip", "install", "--upgrade", "pip"))
$code = Invoke-Quiet $venvPython @("-m", "pip", "install", "-r", (Join-Path $InstallDir "requirements.txt"))
if ($code -ne 0) { Warn "pip reported issues; the CLI may still run. Try 'omni' then /doctor." }
# Safety net: guarantee the critical UI deps even if the bulk install above was
# partial — without prompt_toolkit the slash-command menu silently disabled.
[void](Invoke-Quiet $venvPython @("-m", "pip", "install", "--upgrade", "prompt_toolkit", "rich"))

# --- Install the `omni` launcher on PATH -----------------------------------
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$omniPy = Join-Path $InstallDir "omni_dev.py"

$cmdShim = "@echo off`r`n`"$venvPython`" `"$omniPy`" %*`r`n"
Set-Content -Path (Join-Path $BinDir "omni.cmd") -Value $cmdShim -Encoding ASCII -NoNewline

$ps1Shim = "& `"$venvPython`" `"$omniPy`" `$args`r`n"
Set-Content -Path (Join-Path $BinDir "omni.ps1") -Value $ps1Shim -Encoding UTF8 -NoNewline

# Add BinDir to the user PATH if it isn't already there.
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($userPath -split ';') -notcontains $BinDir) {
    Info "Adding $BinDir to your user PATH ..."
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User")
}
$env:Path = "$env:Path;$BinDir"  # make `omni` available in THIS session too

Write-Host ""
Ok "Installed to $InstallDir"
Write-Host ""
Write-Host "  Start the CLI from any project folder with:" -ForegroundColor White
Write-Host "      omni" -ForegroundColor Magenta
Write-Host ""
Warn "If 'omni' isn't found, open a NEW terminal so the updated PATH loads."
Write-Host ""
