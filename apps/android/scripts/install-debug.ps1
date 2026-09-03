$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path $PSScriptRoot -Parent
$version = (Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'package.json') | ConvertFrom-Json).version
$toolchainRoot = if ([string]::IsNullOrWhiteSpace($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)) {
    'F:\AndroidToolchain'
} else {
    [System.IO.Path]::GetFullPath($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)
}
$adb = Join-Path $toolchainRoot 'android-sdk\platform-tools\adb.exe'
$apk = Join-Path $projectRoot "artifacts\debug\xiaowo-$version-demo-debug.apk"

if (-not (Test-Path -LiteralPath $adb)) { throw 'ADB is missing.' }
if (-not (Test-Path -LiteralPath $apk)) { throw 'Debug APK is missing. Run scripts\build-debug.ps1 first.' }

& $adb wait-for-device
& $adb install -r $apk
if ($LASTEXITCODE -ne 0) { throw "ADB install failed with exit code $LASTEXITCODE" }
& $adb shell am start -n 'cn.edu.ustc.xiaowo/.MainActivity'
