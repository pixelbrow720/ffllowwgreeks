# FlowGreeks validation venv setup (Windows PowerShell)
# Run from backend\scripts\validation\

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Test-Path .venv)) {
    Write-Host "Creating venv..."
    py -3.14 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        py -m venv .venv
    }
}

Write-Host "Activating + upgrading pip..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip wheel

Write-Host "Installing requirements..."
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host ""
Write-Host "Done. Activate with:"
Write-Host "    .\.venv\Scripts\Activate.ps1"
