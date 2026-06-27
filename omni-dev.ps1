$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -Path $scriptPath

# Activate venv if it exists
$venvPython = Join-Path $scriptPath "venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Host "Starting Omni-Dev with venv Python..." -ForegroundColor Cyan
    & $venvPython omni_dev.py $args
} else {
    Write-Host "Starting Omni-Dev with system Python..." -ForegroundColor Yellow
    python omni_dev.py $args
}
