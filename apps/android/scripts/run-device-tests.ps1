$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path $PSScriptRoot -Parent
$version = (Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'package.json') | ConvertFrom-Json).version
$toolchainRoot = if ([string]::IsNullOrWhiteSpace($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)) {
    'F:\AndroidToolchain'
} else {
    [System.IO.Path]::GetFullPath($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)
}
$adb = Join-Path $toolchainRoot 'android-sdk\platform-tools\adb.exe'
$appApk = Join-Path $projectRoot "artifacts\debug\xiaowo-$version-demo-debug.apk"
$testApk = Join-Path $projectRoot 'android\app\build\outputs\apk\androidTest\demo\debug\app-demo-debug-androidTest.apk'
$resultRoot = Join-Path $projectRoot 'artifacts\test-results'
$resultPath = Join-Path $resultRoot 'instrumentation.txt'

if (-not (Test-Path -LiteralPath $adb)) { throw 'ADB is missing.' }
if (-not (Test-Path -LiteralPath $appApk)) { throw 'Debug APK is missing. Run scripts\build-debug.ps1 first.' }
if (-not (Test-Path -LiteralPath $testApk)) { throw 'Android test APK is missing. Run scripts\build-debug.ps1 first.' }

$deviceLine = & $adb devices | Select-String '^(emulator-|[A-Za-z0-9._:-]+)\s+device$' | Select-Object -First 1
if ($null -eq $deviceLine) { throw 'No ready Android device found. Run scripts\start-emulator.ps1 first.' }
$serial = ($deviceLine.ToString() -split '\s+')[0]

& $adb -s $serial install -r $appApk
if ($LASTEXITCODE -ne 0) { throw 'Failed to install the application APK.' }
& $adb -s $serial install -r $testApk
if ($LASTEXITCODE -ne 0) { throw 'Failed to install the instrumentation APK.' }

New-Item -ItemType Directory -Path $resultRoot -Force | Out-Null
$instrumentation = & $adb -s $serial shell am instrument -w -r -e clearPackageData true `
    'cn.edu.ustc.xiaowo.test/androidx.test.runner.AndroidJUnitRunner' 2>&1
$instrumentation | Tee-Object -FilePath $resultPath
if ($LASTEXITCODE -ne 0) { throw 'Android instrumentation command failed.' }
$instrumentationText = $instrumentation | Out-String
if ($instrumentationText -notmatch 'INSTRUMENTATION_CODE:\s*-1') { throw 'Android instrumentation tests did not report success.' }
if ($instrumentationText -match 'FAILURES|INSTRUMENTATION_FAILED|Process crashed') { throw 'Android instrumentation tests reported a failure.' }

Write-Host "Android instrumentation tests: PASS ($serial)"
