$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$toolchainRoot = if ([string]::IsNullOrWhiteSpace($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)) {
    'F:\AndroidToolchain'
} else {
    [System.IO.Path]::GetFullPath($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)
}
$downloadRoot = Join-Path $toolchainRoot 'downloads'
$stagingRoot = Join-Path $toolchainRoot 'staging'
$javaHome = Join-Path $toolchainRoot 'java\jdk-21'
$sdkRoot = Join-Path $toolchainRoot 'android-sdk'
$avdRoot = Join-Path $toolchainRoot 'avd'

$jdkUrl = 'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jdk_x64_windows_hotspot_21.0.12.1_1.zip'
$jdkSha256 = 'f9d6e191ab098c0d416e7d588a24420a8621cd2f4720dab2459b8b7b2d2d8b4e'
$commandToolsUrl = 'https://dl.google.com/android/repository/commandlinetools-win-16111833_latest.zip'
$commandToolsSha1 = '57d04f2d75eb8e8fffc5000a987e5de4b5a63e9d'

function Assert-WithinToolchain([string]$Path) {
    $root = [IO.Path]::GetFullPath($toolchainRoot).TrimEnd('\') + '\'
    $candidate = [IO.Path]::GetFullPath($Path)
    if (-not $candidate.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside toolchain root: $candidate"
    }
    return $candidate
}

function Get-VerifiedDownload([string]$Uri, [string]$Destination, [string]$Algorithm, [string]$ExpectedHash) {
    if (-not (Test-Path -LiteralPath $Destination)) {
        Write-Host "Downloading $Uri"
        Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $Destination -TimeoutSec 1800
    }
    $actual = (Get-FileHash -LiteralPath $Destination -Algorithm $Algorithm).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedHash.ToLowerInvariant()) {
        throw "Checksum mismatch for $Destination"
    }
}

function New-StagingDirectory([string]$Prefix) {
    $path = Join-Path $stagingRoot ($Prefix + '-' + [guid]::NewGuid().ToString('N'))
    Assert-WithinToolchain $path | Out-Null
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    return $path
}

function Remove-StagingDirectory([string]$Path) {
    $safePath = Assert-WithinToolchain $Path
    if (Test-Path -LiteralPath $safePath) {
        Remove-Item -LiteralPath $safePath -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $downloadRoot, $stagingRoot, (Split-Path $javaHome), $sdkRoot, $avdRoot -Force | Out-Null

$jdkZip = Join-Path $downloadRoot 'temurin-jdk-21.0.12.zip'
Get-VerifiedDownload $jdkUrl $jdkZip 'SHA256' $jdkSha256
if (-not (Test-Path -LiteralPath (Join-Path $javaHome 'bin\java.exe'))) {
    $stage = New-StagingDirectory 'jdk'
    try {
        Expand-Archive -LiteralPath $jdkZip -DestinationPath $stage
        $source = Get-ChildItem -LiteralPath $stage -Directory | Select-Object -First 1
        if ($null -eq $source) { throw 'JDK archive did not contain a root directory' }
        Move-Item -LiteralPath $source.FullName -Destination $javaHome
    } finally {
        Remove-StagingDirectory $stage
    }
}

$commandToolsZip = Join-Path $downloadRoot 'android-commandlinetools-16111833.zip'
Get-VerifiedDownload $commandToolsUrl $commandToolsZip 'SHA1' $commandToolsSha1
$latestToolsRoot = Join-Path $sdkRoot 'cmdline-tools\latest'
$androidCli = Join-Path $latestToolsRoot 'bin\android.exe'
if (-not (Test-Path -LiteralPath $androidCli)) {
    $stage = New-StagingDirectory 'cmdline-tools'
    try {
        Expand-Archive -LiteralPath $commandToolsZip -DestinationPath $stage
        $source = Join-Path $stage 'cmdline-tools'
        if (-not (Test-Path -LiteralPath $source)) { throw 'Android command-line archive layout is invalid' }
        New-Item -ItemType Directory -Path (Join-Path $sdkRoot 'cmdline-tools') -Force | Out-Null
        Move-Item -LiteralPath $source -Destination $latestToolsRoot
    } finally {
        Remove-StagingDirectory $stage
    }
}

$env:JAVA_HOME = $javaHome
$env:ANDROID_HOME = $sdkRoot
$env:ANDROID_SDK_ROOT = $sdkRoot
$env:ANDROID_AVD_HOME = $avdRoot
$env:ANDROID_USER_HOME = Join-Path $toolchainRoot 'user-home'
$env:Path = "$javaHome\bin;$sdkRoot\platform-tools;$sdkRoot\emulator;$env:Path"

$licenses = 1..200 | ForEach-Object { 'y' }
$packages = @(
    'platform-tools',
    'platforms/android-36',
    'build-tools/36.0.0',
    'emulator',
    'system-images/android-35/google_apis/x86_64'
)
$licenses | & $androidCli "--sdk=$sdkRoot" sdk install @packages
if ($LASTEXITCODE -ne 0) { throw "Android SDK install failed with exit code $LASTEXITCODE" }

$avdManager = Join-Path $latestToolsRoot 'bin\avdmanager.bat'
$avdName = 'xiaowo_api35'
$existingAvds = & $avdManager list avd
if ($existingAvds -notmatch "Name:\s+$([regex]::Escape($avdName))") {
    'no' | & $avdManager create avd --force --name $avdName --package 'system-images;android-35;google_apis;x86_64' --device 'pixel_6'
    if ($LASTEXITCODE -ne 0) { throw "AVD creation failed with exit code $LASTEXITCODE" }
}

Write-Host "Toolchain ready at $toolchainRoot"
& (Join-Path $javaHome 'bin\java.exe') -version
& (Join-Path $sdkRoot 'platform-tools\adb.exe') version
& (Join-Path $sdkRoot 'emulator\emulator.exe') -version | Select-Object -First 3
