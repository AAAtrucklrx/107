$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'android-toolchain.ps1')
Initialize-XiaowoAndroidToolchain -ProjectRoot $projectRoot | Out-Null

$version = (Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'package.json') | ConvertFrom-Json).version
$apkSource = Join-Path $projectRoot 'android\app\build\outputs\apk\demo\debug\app-demo-debug.apk'
$artifactRoot = Join-Path $projectRoot 'artifacts\debug'
$artifact = Join-Path $artifactRoot "xiaowo-$version-demo-debug.apk"
$gradleLog = Join-Path $projectRoot 'artifacts\build-state\gradle-debug.log'

Invoke-XiaowoGradle -ProjectRoot $projectRoot -LogPath $gradleLog -Tasks @(
    ':app:testDemoDebugUnitTest',
    ':app:lintDemoDebug',
    ':app:assembleDemoDebug',
    ':app:assembleDemoDebugAndroidTest'
)

New-Item -ItemType Directory -Path $artifactRoot -Force | Out-Null
Copy-Item -LiteralPath $apkSource -Destination $artifact -Force
$hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath ($artifact + '.sha256') -Value "$hash  $(Split-Path $artifact -Leaf)" -Encoding ascii
Write-Host "Debug APK: $artifact"
Write-Host "SHA-256: $hash"
