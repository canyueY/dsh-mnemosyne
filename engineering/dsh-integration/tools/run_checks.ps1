# 运行 DSH 安装/迁移/接口适配的本地验收。
# 所有安装/迁移实验请先设置临时 DSH_HOME，再运行本脚本。
param(
  [string]$DshHome = "",
  [switch]$IncludeMigration
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if ($DshHome) { $env:DSH_HOME = $DshHome }

Write-Host "== npm test =="
Push-Location $repo
try {
  npm test
  if ($LASTEXITCODE -ne 0) { Write-Error "npm test failed"; exit 1 }
  npm run check
  if ($LASTEXITCODE -ne 0) { Write-Error "npm run check failed"; exit 1 }
} finally { Pop-Location }

Write-Host "== python tests =="
Push-Location (Join-Path $repo 'service')
try {
  py -3 -m pytest tests -q
  if ($LASTEXITCODE -ne 0) { Write-Error "python tests failed"; exit 1 }
} finally { Pop-Location }

if ($IncludeMigration) {
  Write-Host "== install + migration (temporary DSH_HOME) =="
  $tmp = Join-Path $env:TEMP ("dsh-mnemosyne-check-" + [guid]::NewGuid().ToString('N'))
  $env:DSH_HOME = $tmp
  New-Item -ItemType Directory -Force (Join-Path $tmp 'profiles\web') | Out-Null
  [IO.File]::WriteAllText((Join-Path $tmp 'profiles\web\package.json'),
    '{"name":"dsh-profile-web","private":true,"dependencies":{},"dsh":{"profile":{"bundles":["@deepseek-ai/dsh-base","@deepseek-ai/dsh-web-app"]}}}',
    (New-Object Text.UTF8Encoding($false)))
  Push-Location $repo
  try {
    node scripts/install-dsh.mjs --local --profile web
    if ($LASTEXITCODE -ne 0) { Write-Error "install-dsh failed"; exit 1 }
    node scripts/migrate-dsh.mjs --profile web --rollback
  } finally { Pop-Location }
}

Write-Host "OK"
