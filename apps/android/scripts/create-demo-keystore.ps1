$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path $PSScriptRoot -Parent
$signingRoot = Join-Path $projectRoot 'android\signing'
$keystorePath = Join-Path $signingRoot 'xiaowo-demo.jks'
$propertiesPath = Join-Path $signingRoot 'demo-signing.properties'
$toolchainRoot = if ([string]::IsNullOrWhiteSpace($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)) {
    'F:\AndroidToolchain'
} else {
    [System.IO.Path]::GetFullPath($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)
}
$keytool = Join-Path $toolchainRoot 'java\jdk-21\bin\keytool.exe'

if ((Test-Path -LiteralPath $keystorePath) -xor (Test-Path -LiteralPath $propertiesPath)) {
    throw 'Demo signing state is incomplete. Restore both ignored signing files or remove the partial file manually.'
}
if ((Test-Path -LiteralPath $keystorePath) -and (Test-Path -LiteralPath $propertiesPath)) {
    Write-Host 'Demo signing key already exists.'
    exit 0
}
if (-not (Test-Path -LiteralPath $keytool)) { throw "keytool is missing from $toolchainRoot." }

New-Item -ItemType Directory -Path $signingRoot -Force | Out-Null
$random = New-Object byte[] 36
$generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $generator.GetBytes($random)
} finally {
    $generator.Dispose()
}
$password = [Convert]::ToBase64String($random).Replace('+', 'A').Replace('/', 'B').TrimEnd('=')
$alias = 'xiaowo-demo'

& $keytool -genkeypair -noprompt `
    -keystore $keystorePath `
    -storetype PKCS12 `
    -storepass $password `
    -keypass $password `
    -alias $alias `
    -keyalg RSA `
    -keysize 3072 `
    -validity 3650 `
    -dname 'CN=Xiaowo Demo,O=Xiaowo,C=CN'
if ($LASTEXITCODE -ne 0) { throw "keytool failed with exit code $LASTEXITCODE" }

$lines = @(
    'storeFile=signing/xiaowo-demo.jks',
    "storePassword=$password",
    "keyAlias=$alias",
    "keyPassword=$password"
)
[System.IO.File]::WriteAllLines($propertiesPath, $lines, [System.Text.UTF8Encoding]::new($false))
Write-Host 'Created an ignored local Demo signing key. Keep android\signing backed up privately.'
