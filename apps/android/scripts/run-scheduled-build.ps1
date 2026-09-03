$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path $PSScriptRoot -Parent
$runner = Join-Path $PSScriptRoot 'scheduled-build-runner.ps1'
$stateRoot = Join-Path $projectRoot 'artifacts\build-state'
$logPath = Join-Path $stateRoot 'scheduled-build.log'
$resultPath = Join-Path $stateRoot 'scheduled-build.result'
$taskName = 'XiaowoAndroidBuild-' + [guid]::NewGuid().ToString('N')
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $runner + '"'

New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
Remove-Item -LiteralPath $resultPath -Force -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $projectRoot
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 45) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $taskName
    $deadline = (Get-Date).AddMinutes(45)
    do {
        Start-Sleep -Seconds 2
        $task = Get-ScheduledTask -TaskName $taskName
        if (Test-Path -LiteralPath $resultPath) { break }
        if ($task.State -notin @('Running', 'Queued')) { break }
    } while ((Get-Date) -lt $deadline)

    if (-not (Test-Path -LiteralPath $resultPath)) {
        throw "Scheduled build did not produce a result. Task state: $($task.State)"
    }
    $result = (Get-Content -LiteralPath $resultPath -Raw).Trim()
    if (Test-Path -LiteralPath $logPath) { Get-Content -LiteralPath $logPath }
    if ($result -ne 'SUCCESS') { throw 'Scheduled build failed. See artifacts\build-state\scheduled-build.log.' }
} finally {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
