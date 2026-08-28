[CmdletBinding()]
param(
    [switch]$NoSetup,
    [switch]$SkipVenv
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  JARVIS / Mark Assistant - Launcher" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

function Find-Python {
    $candidates = @(
        (Join-Path $ScriptDir "venv\Scripts\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python311\python.exe"),
        (Join-Path $env:ProgramFiles "Python310\python.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    try {
        $py = Get-Command python -ErrorAction Stop
        return $py.Source
    } catch {
        try {
            $py = Get-Command py -ErrorAction Stop
            return $py.Source
        } catch {
            return $null
        }
    }
}

function Invoke-Setup {
    Write-Host "[1/3] Checking Python environment..." -ForegroundColor Yellow
    if (-not $SkipVenv -and -not (Test-Path (Join-Path $ScriptDir "venv\Scripts\python.exe"))) {
        Write-Host "  Creating virtual environment..." -ForegroundColor Gray
        & $PythonExe -m venv venv
        if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment" }
        $Script:PythonExe = Join-Path $ScriptDir "venv\Scripts\python.exe"
        Write-Host "  Virtual environment created." -ForegroundColor Green
    }
    Write-Host "  Python OK: $PythonExe" -ForegroundColor Green

    Write-Host ""
    Write-Host "[2/3] Installing dependencies..." -ForegroundColor Yellow
    $reqFile = Join-Path $ScriptDir "requirements.txt"
    if (Test-Path $reqFile) {
        & $PythonExe -m pip install --upgrade pip
        & $PythonExe -m pip install -r $reqFile
        if ($LASTEXITCODE -ne 0) { Write-Warning "Some dependencies failed to install; trying to continue anyway." }
        Write-Host "  Dependencies installed." -ForegroundColor Green
    } else {
        Write-Warning "  requirements.txt not found, skipping install."
    }

    Write-Host ""
    Write-Host "[3/3] Checking environment variables..." -ForegroundColor Yellow
    $envFile = Join-Path $ScriptDir ".env"
    $envExample = Join-Path $ScriptDir ".env.example"
    if (-not (Test-Path $envFile) -and (Test-Path $envExample)) {
        Copy-Item $envExample $envFile
        Write-Host "  Created .env from .env.example — please edit it before next run." -ForegroundColor Yellow
    }
    Write-Host "  Setup complete." -ForegroundColor Green
    Write-Host ""
}

$PythonExe = Find-Python
if (-not $PythonExe) {
    Write-Error "Python interpreter not found. Install Python 3.10+ from https://python.org or add it to PATH."
    exit 1
}

if (-not $NoSetup) { Invoke-Setup }

Write-Host "Launching Mark Assistant..." -ForegroundColor Cyan
Write-Host "Dashboard will be available at http://localhost:8080" -ForegroundColor Gray
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

& $PythonExe (Join-Path $ScriptDir "Mark.py")
exit $LASTEXITCODE
