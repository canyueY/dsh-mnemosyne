# Provision the dedicated Python runtime for the standalone Mnemosyne plugin.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup-runtime.ps1
#
# The plugin never uses the desktop-pet venv. Runtime lives outside the repo:
#   %LOCALAPPDATA%\Mnemosyne DSH Node\venv
param(
  [string]$Python = "",
  [switch]$Recreate
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$requirements = Join-Path $root "service\requirements.txt"
if (-not (Test-Path $requirements)) { throw "requirements.txt not found: $requirements" }

$runtimeRoot = Join-Path $env:LOCALAPPDATA "Mnemosyne DSH Node"
$venv = Join-Path $runtimeRoot "venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

if ($Recreate -and (Test-Path $venv)) {
  Write-Host "Removing existing runtime: $venv"
  Remove-Item -Recurse -Force $venv
}

if (-not (Test-Path $venvPython)) {
  if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "Python 3.10+ not found on PATH. Pass -Python <path-to-python.exe>." }
    $Python = $cmd.Source
  }
  Write-Host "Creating runtime venv: $venv"
  New-Item -ItemType Directory -Force $runtimeRoot | Out-Null
  & $Python -m venv $venv
  if ($LASTEXITCODE -ne 0) { throw "python -m venv failed ($LASTEXITCODE)" }
}

Write-Host "Installing Mnemosyne Node dependencies..."
& $venvPython -m pip install --disable-pip-version-check --upgrade pip | Out-Host
& $venvPython -m pip install --disable-pip-version-check -r $requirements | Out-Host
if ($LASTEXITCODE -ne 0) { throw "pip install failed ($LASTEXITCODE)" }

& $venvPython -c "import sys, fastapi, uvicorn, pydantic, pydantic_settings, httpx; print('runtime ok:', sys.version.split()[0], '| fastapi', fastapi.__version__, '| uvicorn', uvicorn.__version__)"
if ($LASTEXITCODE -ne 0) { throw "runtime import check failed ($LASTEXITCODE)" }

Write-Host ""
Write-Host "Mnemosyne runtime ready:"
Write-Host "  python : $venvPython"
Write-Host "  bank   : $env:LOCALAPPDATA\Mnemosyne DSH Memory Bank"
