param(
    [string]$TaskName = "YouthPolicyDailyCollector",
    [int]$Hour = 3,
    [int]$Minute = 0
)

$ErrorActionPreference = "Stop"
$projectDir = $PSScriptRoot
$pythonPath = Join-Path $projectDir ".venv\Scripts\python.exe"
$collectorPath = Join-Path $projectDir "scheduled_collector.py"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "가상환경 Python을 찾을 수 없습니다: $pythonPath"
}

$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument ('"{0}" --once' -f $collectorPath) `
    -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour $Hour -Minute $Minute -Second 0)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "목포 청년 정책 데이터 매일 자동 수집" `
    -Force | Out-Null

Write-Host "등록 완료: $TaskName (매일 $($Hour.ToString('00')):$($Minute.ToString('00')))"
Write-Host "PC가 꺼져 있어 실행을 놓치면 다음 로그인 후 보충 실행됩니다."
Write-Host "실행 로그: $(Join-Path $projectDir 'logs')"
