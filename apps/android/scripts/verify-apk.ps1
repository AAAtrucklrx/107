param(
    [string]$ApkPath = '',
    [ValidateSet('demoDebug', 'demoRelease')]
    [string]$Variant = 'demoDebug'
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path $PSScriptRoot -Parent
$version = (Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'package.json') | ConvertFrom-Json).version
if ([string]::IsNullOrWhiteSpace($ApkPath)) {
    $folder = if ($Variant -eq 'demoRelease') { 'release' } else { 'debug' }
    $suffix = if ($Variant -eq 'demoRelease') { 'release' } else { 'debug' }
    $ApkPath = Join-Path $projectRoot "artifacts\$folder\xiaowo-$version-demo-$suffix.apk"
} elseif (-not [System.IO.Path]::IsPathRooted($ApkPath)) {
    $ApkPath = Join-Path $projectRoot $ApkPath
}
$ApkPath = [System.IO.Path]::GetFullPath($ApkPath)

$toolchainRoot = if ([string]::IsNullOrWhiteSpace($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)) {
    'F:\AndroidToolchain'
} else {
    [System.IO.Path]::GetFullPath($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)
}
$env:JAVA_HOME = Join-Path $toolchainRoot 'java\jdk-21'
$analyzer = Join-Path $toolchainRoot 'android-sdk\cmdline-tools\latest\bin\apkanalyzer.bat'
$signer = Join-Path $toolchainRoot 'android-sdk\build-tools\36.0.0\apksigner.bat'
$zipalign = Join-Path $toolchainRoot 'android-sdk\build-tools\36.0.0\zipalign.exe'

function Assert-ApkContract([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Read-ManifestValue([string]$Field) {
    $value = & $analyzer manifest $Field $ApkPath
    if ($LASTEXITCODE -ne 0) { throw "apkanalyzer failed for manifest field: $Field" }
    ($value | Out-String).Trim()
}

Assert-ApkContract (Test-Path -LiteralPath $ApkPath) "APK not found: $ApkPath"
Assert-ApkContract (Test-Path -LiteralPath $analyzer) 'apkanalyzer is missing.'
Assert-ApkContract (Test-Path -LiteralPath $signer) 'apksigner is missing.'
Assert-ApkContract (Test-Path -LiteralPath $zipalign) 'zipalign is missing.'

$applicationId = Read-ManifestValue 'application-id'
$versionName = Read-ManifestValue 'version-name'
$versionCode = Read-ManifestValue 'version-code'
$minSdk = Read-ManifestValue 'min-sdk'
$targetSdk = Read-ManifestValue 'target-sdk'
$permissions = @((Read-ManifestValue 'permissions') -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
$expectedPermissions = @(
    'android.permission.INTERNET',
    'cn.edu.ustc.xiaowo.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION'
)

Assert-ApkContract ($applicationId -eq 'cn.edu.ustc.xiaowo') "Unexpected application ID: $applicationId"
Assert-ApkContract ($versionName -eq '0.2.0-demo') "Unexpected versionName: $versionName"
Assert-ApkContract ($versionCode -eq '2') "Unexpected versionCode: $versionCode"
Assert-ApkContract ($minSdk -eq '26') "Unexpected minSdk: $minSdk"
Assert-ApkContract ($targetSdk -eq '36') "Unexpected targetSdk: $targetSdk"
Assert-ApkContract ($null -eq (Compare-Object $expectedPermissions $permissions)) "Unexpected APK permissions: $($permissions -join ', ')"

$signature = & $signer verify --verbose --print-certs $ApkPath 2>&1
if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed.' }
$signatureText = $signature | Out-String
Assert-ApkContract ($signatureText -match 'Verifies') 'APK signature did not verify.'
if ($Variant -eq 'demoDebug') {
    Assert-ApkContract ($signatureText -match 'CN=Android Debug') 'Debug APK is not signed with an Android Debug certificate.'
} else {
    Assert-ApkContract ($signatureText -match 'CN=Xiaowo Demo') 'Demo release APK is not signed with the dedicated Demo certificate.'
    Assert-ApkContract ($signatureText -notmatch 'CN=Android Debug') 'Demo release APK must not use the Android Debug certificate.'
}

& $zipalign -c -P 16 4 $ApkPath | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'APK zip alignment verification failed.' }

$apk = Get-Item -LiteralPath $ApkPath
$hash = (Get-FileHash -LiteralPath $ApkPath -Algorithm SHA256).Hash.ToLowerInvariant()
$report = @(
    'APK verification: PASS',
    "Variant: $Variant",
    "Path: $ApkPath",
    "Application ID: $applicationId",
    "Version: $versionName ($versionCode)",
    "SDK: min $minSdk, target $targetSdk",
    "Permissions: $($permissions -join ', ')",
    "Size: $($apk.Length) bytes",
    "SHA-256: $hash"
)
$reportPath = Join-Path (Split-Path $ApkPath -Parent) "apk-verification-$Variant.txt"
$report | Tee-Object -FilePath $reportPath
