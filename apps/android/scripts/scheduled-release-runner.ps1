$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path $PSScriptRoot -Parent
$stateRoot = Join-Path $projectRoot 'artifacts\build-state'
$logPath = Join-Path $stateRoot 'scheduled-release.log'
$resultPath = Join-Path $stateRoot 'scheduled-release.result'

New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
Remove-Item -LiteralPath $resultPath -Force -ErrorAction SilentlyContinue
Start-Transcript -LiteralPath $logPath -Force | Out-Null

try {
    & (Join-Path $PSScriptRoot 'build-release.ps1')
    Set-Content -LiteralPath $resultPath -Value 'SUCCESS' -Encoding ascii
    exit 0
} catch {
    Set-Content -LiteralPath $resultPath -Value 'FAILED' -Encoding ascii
    Write-Host ($_ | Out-String)
    exit 1
} finally {
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
}
