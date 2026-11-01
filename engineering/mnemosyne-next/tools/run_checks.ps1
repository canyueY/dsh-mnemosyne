[CmdletBinding()]
param(
    [ValidateSet('Baseline', 'Task', 'Final')]
    [string]$Mode = 'Task',
    [ValidatePattern('^E(0[0-9]|1[0-5])$')]
    [string]$TaskId = 'E00',
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$packRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$serviceRoot = Join-Path $repoRoot 'service'
$reportRoot = Join-Path $packRoot 'reports'
$runStamp = (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$logPath = Join-Path $reportRoot "checks-$Mode-$TaskId-$runStamp.log"
$junitPath = Join-Path $reportRoot "checks-$Mode-$TaskId-$runStamp.xml"
$handoffFailures = [System.Collections.Generic.List[string]]::new()

if (-not $Python) {
    $candidates = @(
        (Join-Path $serviceRoot '.venv/Scripts/python.exe'),
        (Join-Path $repoRoot '.venv/Scripts/python.exe'),
        (Join-Path (Split-Path -Parent $repoRoot) '.venv/Scripts/python.exe')
    )
    $Python = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
}
$npmCommand = (Get-Command npm -ErrorAction Stop).Source
if ($Mode -eq 'Task' -and $TaskId -eq 'E00') { $Mode = 'Baseline' }
if ($Mode -eq 'Task' -and $TaskId -eq 'E15') { $Mode = 'Final' }

function Invoke-HandoffCheck {
    param([string]$Label, [string]$Directory, [string]$Executable, [string[]]$Arguments)
    "CHECK $Label | cwd=$Directory" | Tee-Object -FilePath $logPath -Append | Write-Output
    Push-Location -LiteralPath $Directory
    try {
        & $Executable @Arguments 2>&1 | Tee-Object -FilePath $logPath -Append | Write-Output
        $checkExit = $LASTEXITCODE
        "EXIT $checkExit | $Label" | Tee-Object -FilePath $logPath -Append | Write-Output
        if ($checkExit -ne 0) { $handoffFailures.Add("$Label exit=$checkExit") }
    } catch {
        $handoffFailures.Add("$Label failed: $($_.Exception.Message)")
        "ERROR $Label | $($_.Exception.Message)" | Tee-Object -FilePath $logPath -Append | Write-Output
    } finally { Pop-Location }
}

# Defense in depth: redirect application default storage for child processes.
# The tests must still explicitly construct temporary Banks and never load personal configs.
$isolationRoot = Join-Path $reportRoot "isolated-runs/$runStamp"
New-Item -ItemType Directory -Path $isolationRoot -Force | Out-Null
$bankEnvNames = @('MNEMOSYNE_MEMORY_BANK', 'MNEMOSYNE_DATA_DIR', 'DSH_MEMORY_DATA_DIR')
$savedBankEnv = @{}
foreach ($envName in $bankEnvNames) {
    $savedBankEnv[$envName] = [Environment]::GetEnvironmentVariable($envName, 'Process')
    [Environment]::SetEnvironmentVariable($envName, $isolationRoot, 'Process')
}
try {
    Invoke-HandoffCheck 'pack structure' $repoRoot $Python @((Join-Path $PSScriptRoot 'validate_handoff.py'))
    Invoke-HandoffCheck 'python version' $repoRoot $Python @('--version')
    if ($Mode -in @('Baseline', 'Final')) {
        Invoke-HandoffCheck 'python full suite' $serviceRoot $Python @('-m', 'pytest', 'tests', '-q', "--junitxml=$junitPath")
        Invoke-HandoffCheck 'node syntax' $repoRoot $npmCommand @('run', 'check')
        Invoke-HandoffCheck 'node suite' $repoRoot $npmCommand @('test')
        Invoke-HandoffCheck 'existing theory acceptance' $serviceRoot $Python @('scripts/verify_theory_loops.py')
    } else {
        $index = Get-Content -LiteralPath (Join-Path $packRoot 'TASK_INDEX.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $task = @($index.tasks | Where-Object { $_.id -eq $TaskId })
        if ($task.Count -ne 1) { throw "Task not found: $TaskId" }
        foreach ($required in $task[0].required_python_tests) {
            if (-not (Test-Path -LiteralPath (Join-Path $serviceRoot $required) -PathType Leaf)) {
                throw "Required implementation test missing: $required"
            }
        }
        foreach ($required in $task[0].required_node_tests) {
            if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $required) -PathType Leaf)) {
                throw "Required Node test missing: $required"
            }
        }
        $pythonTests = @(@($task[0].existing_python_tests) + @($task[0].required_python_tests) | Sort-Object -Unique)
        if ($pythonTests.Count -gt 0) {
            Invoke-HandoffCheck "python task $TaskId" $serviceRoot $Python (@('-m', 'pytest') + $pythonTests + @('-q', "--junitxml=$junitPath"))
        }
        if ($TaskId -in @('E05', 'E06')) {
            Invoke-HandoffCheck 'node syntax' $repoRoot $npmCommand @('run', 'check')
            Invoke-HandoffCheck 'node suite' $repoRoot $npmCommand @('test')
        }
    }
    if ($Mode -eq 'Final' -or ($Mode -eq 'Task' -and $TaskId -in @('E03', 'E12'))) {
        Invoke-HandoffCheck 'typescript SDK build' (Join-Path $serviceRoot 'sdk-ts') $npmCommand @('run', 'build')
    }
    if ($Mode -eq 'Final') {
        $continuity = Join-Path $serviceRoot 'scripts/verify_memory_continuity.py'
        if (-not (Test-Path -LiteralPath $continuity -PathType Leaf)) {
            $handoffFailures.Add('required E15 continuity script missing')
        } else {
            Invoke-HandoffCheck 'continuity acceptance' $serviceRoot $Python @($continuity)
        }
    }
} finally {
    foreach ($envName in $bankEnvNames) {
        [Environment]::SetEnvironmentVariable($envName, $savedBankEnv[$envName], 'Process')
    }
}
if ($handoffFailures.Count -gt 0) {
    throw "Checks failed (not passed): $($handoffFailures -join '; '). Log: $logPath"
}
Write-Output "Command checks passed. Log: $logPath. Clean-environment installation and case mapping require their own evidence."
