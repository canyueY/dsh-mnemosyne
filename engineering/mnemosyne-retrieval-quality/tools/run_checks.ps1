# 运行 Mnemosyne 检索质量与认知正确性验收。
# 全部用临时 Bank / fake executor，不碰生产数据。
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Write-Host "== npm test / check =="
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

Write-Host "== diff check =="
Push-Location $repo
try {
  git diff --check
  if ($LASTEXITCODE -ne 0) { Write-Error "git diff --check failed"; exit 1 }
} finally { Pop-Location }

Write-Host "OK"
