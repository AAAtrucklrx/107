$ErrorActionPreference = 'Stop'

function Initialize-XiaowoAndroidToolchain {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $toolchainRoot = if ([string]::IsNullOrWhiteSpace($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)) {
        'F:\AndroidToolchain'
    } else {
        [System.IO.Path]::GetFullPath($env:XIAOWO_ANDROID_TOOLCHAIN_ROOT)
    }
    $javaHome = Join-Path $toolchainRoot 'java\jdk-21'
    $sdkRoot = Join-Path $toolchainRoot 'android-sdk'
    $gradleHome = Join-Path $toolchainRoot 'gradle-home'

    if (-not (Test-Path -LiteralPath (Join-Path $javaHome 'bin\java.exe'))) {
        throw 'Portable JDK is missing. Run scripts\install-toolchain.ps1 first.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $sdkRoot 'platforms\android-36'))) {
        throw 'Android SDK 36 is missing. Run scripts\install-toolchain.ps1 first.'
    }

    $env:JAVA_HOME = $javaHome
    $env:ANDROID_HOME = $sdkRoot
    $env:ANDROID_SDK_ROOT = $sdkRoot
    $env:GRADLE_USER_HOME = $gradleHome
    $env:JAVA_OPTS = '-Xmx1536m -Xms256m -Djavax.net.ssl.trustStoreType=Windows-ROOT'
    Remove-Item Env:GRADLE_OPTS -ErrorAction SilentlyContinue
    Remove-Item Env:JAVA_TOOL_OPTIONS -ErrorAction SilentlyContinue
    $env:Path = "$javaHome\bin;$sdkRoot\platform-tools;$env:Path"

    $escapedSdk = $sdkRoot.Replace('\', '\\').Replace(':', '\:')
    Set-Content -LiteralPath (Join-Path $ProjectRoot 'android\local.properties') -Value "sdk.dir=$escapedSdk" -Encoding ascii

    [pscustomobject]@{
        ToolchainRoot = $toolchainRoot
        JavaHome = $javaHome
        SdkRoot = $sdkRoot
        GradleHome = $gradleHome
    }
}

function Invoke-XiaowoGradle {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string[]]$Tasks,
        [Parameter(Mandatory = $true)][string]$LogPath
    )

    New-Item -ItemType Directory -Path (Split-Path $LogPath -Parent) -Force | Out-Null
    Push-Location (Join-Path $ProjectRoot 'android')
    try {
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & .\gradlew.bat --no-daemon --console plain --stacktrace @Tasks 2>&1 | Tee-Object -FilePath $LogPath
            $gradleExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if ($gradleExitCode -ne 0) { throw "Gradle build failed with exit code $gradleExitCode" }
    } finally {
        Pop-Location
    }
}
