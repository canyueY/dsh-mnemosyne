param(
  [string]$Profile = "",
  [string]$DshHome = "",
  [string]$DataDir = "",
  [switch]$Rollback,
  [switch]$SkipData
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$mjs = Join-Path $repo 'scripts\migrate-dsh.mjs'
$argv = @()
if ($Profile) { $argv += @('--profile', $Profile) }
if ($DshHome) { $argv += @('--dsh-home', $DshHome); $env:DSH_HOME = $DshHome }
if ($DataDir) { $argv += @('--data-dir', $DataDir) }
if ($Rollback) { $argv += '--rollback' }
if ($SkipData) { $argv += '--skip-data' }

& node $mjs @argv
exit $LASTEXITCODE
