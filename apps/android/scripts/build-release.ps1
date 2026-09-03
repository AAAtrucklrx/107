$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'android-toolchain.ps1')
Initialize-XiaowoAndroidToolchain -ProjectRoot $projectRoot | Out-Null
& (Join-Path $PSScriptRoot 'create-demo-keystore.ps1')
if ($LASTEXITCODE -ne 0) { throw "Demo keystore setup failed with exit code $LASTEXITCODE" }

$version = (Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'package.json') | ConvertFrom-Json).version
$apkSource = Join-Path $projectRoot 'android\app\build\outputs\apk\demo\release\app-demo-release.apk'
$artifactRoot = Join-Path $projectRoot 'artifacts\release'
$artifact = Join-Path $artifactRoot "xiaowo-$version-demo-release.apk"
$gradleLog = Join-Path $projectRoot 'artifacts\build-state\gradle-release.log'

Invoke-XiaowoGradle -ProjectRoot $projectRoot -LogPath $gradleLog -Tasks @(
    ':app:testDemoDebugUnitTest',
    ':app:lintDemoRelease',
    ':app:assembleDemoRelease'
)

New-Item -ItemType Directory -Path $artifactRoot -Force | Out-Null
Copy-Item -LiteralPath $apkSource -Destination $artifact -Force
$hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath ($artifact + '.sha256') -Value "$hash  $(Split-Path $artifact -Leaf)" -Encoding ascii
Write-Host "Demo release APK: $artifact"
Write-Host "SHA-256: $hash"
