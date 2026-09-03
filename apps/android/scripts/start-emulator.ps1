[CmdletBinding()]
param(
    [switch]$Headless
)

$ErrorActionPreference = 'Stop'

$toolchainRoot = if ([string]::IsNullOrWhiteSpace($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)) {
    'F:\AndroidToolchain'
} else {
    [System.IO.Path]::GetFullPath($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)
}
$sdkRoot = Join-Path $toolchainRoot 'android-sdk'
$emulator = Join-Path $sdkRoot 'emulator\emulator.exe'
$adb = Join-Path $sdkRoot 'platform-tools\adb.exe'
$env:ANDROID_HOME = $sdkRoot
$env:ANDROID_SDK_ROOT = $sdkRoot
$env:ANDROID_AVD_HOME = Join-Path $toolchainRoot 'avd'

if (-not (Test-Path -LiteralPath $emulator)) {
    throw 'Android Emulator is missing. Run scripts\install-toolchain.ps1 first.'
}

$connectedEmulator = & $adb devices | Select-String '^emulator-\d+\s+device' | Select-Object -First 1
if ($null -eq $connectedEmulator) {
    $arguments = @('-avd', 'xiaowo_api35', '-no-snapshot', '-gpu', 'auto', '-no-boot-anim')
    if ($Headless) { $arguments += @('-no-window', '-no-audio') }
    Start-Process -FilePath $emulator -ArgumentList $arguments -WindowStyle Hidden | Out-Null
}

$deadline = (Get-Date).AddMinutes(4)
do {
    Start-Sleep -Seconds 2
    $connectedEmulator = & $adb devices | Select-String '^emulator-\d+\s+device' | Select-Object -First 1
    if ($null -ne $connectedEmulator) {
        $serial = ($connectedEmulator.ToString() -split '\s+')[0]
        $bootCompleted = (& $adb -s $serial shell getprop sys.boot_completed 2>$null).Trim()
        if ($bootCompleted -eq '1') {
            & $adb -s $serial shell input keyevent 82 | Out-Null
            Write-Host "Android emulator ready: $serial"
            exit 0
        }
    }
} while ((Get-Date) -lt $deadline)

throw 'Android emulator did not finish booting within four minutes.'
