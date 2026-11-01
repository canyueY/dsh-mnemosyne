# Wrapper for scripts/install-dsh.mjs
param(
  [switch]$Local,
  [string]$Tag = "",
  [string]$Profile = "",
  [string]$DshHome = "",
  [switch]$AcceptVersionRisk,
  [switch]$DryRun,
  [switch]$SkipPostCheck
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$mjs = Join-Path $repo 'scripts\install-dsh.mjs'
$argv = @()
if ($Local -or -not $Tag) { $argv += '--local' }
if ($Tag) { $argv += @('--tag', $Tag) }
if ($Profile) { $argv += @('--profile', $Profile) }
if ($DshHome) { $argv += @('--dsh-home', $DshHome); $env:DSH_HOME = $DshHome }
if ($AcceptVersionRisk) { $argv += '--accept-version-risk' }
if ($DryRun) { $argv += '--dry-run' }
if ($SkipPostCheck) { $argv += '--skip-post-check' }

& node $mjs @argv
exit $LASTEXITCODE
