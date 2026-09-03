$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path $PSScriptRoot -Parent
$manifestPath = Join-Path $projectRoot 'android\app\src\main\AndroidManifest.xml'
$demoNetworkPath = Join-Path $projectRoot 'android\app\src\demo\res\xml\network_security_config.xml'
$productionNetworkPath = Join-Path $projectRoot 'android\app\src\production\res\xml\network_security_config.xml'
$dataExtractionRulesPath = Join-Path $projectRoot 'android\app\src\main\res\xml\data_extraction_rules.xml'
$updateConfigPath = Join-Path $projectRoot 'android\app\src\main\res\raw\update_config.json'
$packagePath = Join-Path $projectRoot 'package.json'
$gradlePath = Join-Path $projectRoot 'android\app\build.gradle'
$mainActivityPath = Join-Path $projectRoot 'android\app\src\main\java\cn\edu\ustc\xiaowo\MainActivity.java'
$webClientPath = Join-Path $projectRoot 'android\app\src\main\java\cn\edu\ustc\xiaowo\XiaowoWebViewClient.java'
$externalLauncherPath = Join-Path $projectRoot 'android\app\src\main\java\cn\edu\ustc\xiaowo\ExternalLinkLauncher.java'
$androidNamespace = 'http://schemas.android.com/apk/res/android'

function Assert-Contract([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

[xml]$manifest = Get-Content -LiteralPath $manifestPath -Raw
$namespace = New-Object System.Xml.XmlNamespaceManager($manifest.NameTable)
$namespace.AddNamespace('android', $androidNamespace)
$permissions = @($manifest.SelectNodes('/manifest/uses-permission', $namespace))
Assert-Contract ($permissions.Count -eq 1) 'Manifest must declare exactly one permission.'
Assert-Contract ($permissions[0].GetAttribute('name', $androidNamespace) -eq 'android.permission.INTERNET') 'Only INTERNET permission is allowed.'

$application = $manifest.SelectSingleNode('/manifest/application', $namespace)
Assert-Contract ($application.GetAttribute('allowBackup', $androidNamespace) -eq 'false') 'Application backup must remain disabled.'
Assert-Contract ($application.GetAttribute('fullBackupContent', $androidNamespace) -eq 'false') 'Full backup must remain disabled.'
Assert-Contract ($application.GetAttribute('dataExtractionRules', $androidNamespace) -eq '@xml/data_extraction_rules') 'Android 12+ data extraction rules must remain attached.'
Assert-Contract ($application.GetAttribute('networkSecurityConfig', $androidNamespace) -eq '@xml/network_security_config') 'Network security config must be attached.'
Assert-Contract ($application.GetAttribute('usesCleartextTraffic', $androidNamespace) -eq '${usesCleartextTraffic}') 'Cleartext policy must be flavor-controlled.'
Assert-Contract ($null -eq $manifest.SelectSingleNode('/manifest/application/provider', $namespace)) 'No content provider should be exported by the shell.'
Assert-Contract ($null -eq $manifest.SelectSingleNode('/manifest/application/activity[@android:name=".ExternalBrowserActivity"]', $namespace)) 'The legacy in-app external browser must remain removed.'

[xml]$dataExtractionRules = Get-Content -LiteralPath $dataExtractionRulesPath -Raw
$expectedBackupDomains = @('root', 'file', 'database', 'sharedpref', 'external', 'device_root', 'device_file', 'device_database', 'device_sharedpref')
foreach ($section in @('cloud-backup', 'device-transfer')) {
    foreach ($domain in $expectedBackupDomains) {
        $rule = $dataExtractionRules.SelectSingleNode("/data-extraction-rules/$section/exclude[@domain='$domain' and @path='.']")
        Assert-Contract ($null -ne $rule) "Backup exclusion is missing for $section/$domain."
    }
}

[xml]$demoNetwork = Get-Content -LiteralPath $demoNetworkPath -Raw
Assert-Contract ($demoNetwork.'network-security-config'.'base-config'.cleartextTrafficPermitted -eq 'false') 'Demo base cleartext traffic must be disabled.'
$demoDomain = $demoNetwork.'network-security-config'.'domain-config'
Assert-Contract ($demoDomain.cleartextTrafficPermitted -eq 'true') 'Demo server must explicitly opt into cleartext.'
Assert-Contract ($demoDomain.domain.'#text' -eq '114.214.241.119') 'Demo cleartext exception must target only the fixed server.'
Assert-Contract ($demoDomain.domain.includeSubdomains -eq 'false') 'Demo cleartext exception must not include subdomains.'

[xml]$productionNetwork = Get-Content -LiteralPath $productionNetworkPath -Raw
Assert-Contract ($productionNetwork.'network-security-config'.'base-config'.cleartextTrafficPermitted -eq 'false') 'Production cleartext traffic must be disabled.'
Assert-Contract ($null -eq $productionNetwork.'network-security-config'.'domain-config') 'Production must not contain a cleartext exception.'

$updateConfig = Get-Content -LiteralPath $updateConfigPath -Raw | ConvertFrom-Json
Assert-Contract ($updateConfig.enabled -eq $false) 'Update checks must remain disabled until an HTTPS manifest is deployed.'
Assert-Contract ($updateConfig.requireHttps -eq $true) 'Update manifests must require HTTPS.'
Assert-Contract ($updateConfig.checkIntervalHours -eq 24) 'Update interval contract changed unexpectedly.'

$package = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
Assert-Contract ($package.version -eq '0.2.0') 'Package version must be 0.2.0.'
Assert-Contract ($null -eq $package.dependencies) 'The Android client must not ship Capacitor runtime dependencies.'
Assert-Contract ($null -eq $package.devDependencies.'@capacitor/cli') 'Capacitor CLI must remain removed.'

$gradle = Get-Content -LiteralPath $gradlePath -Raw
Assert-Contract ($gradle -match 'applicationId\s+"cn\.edu\.ustc\.xiaowo"') 'Android application ID changed unexpectedly.'
Assert-Contract ($gradle -match 'versionCode\s+2(?:\D|$)') 'Android versionCode must be 2.'
Assert-Contract ($gradle -match 'versionName\s+"0\.2\.0"') 'Android versionName must be 0.2.0.'
Assert-Contract ($gradle -match "productFlavors\s*\{[\s\S]*demo\s*\{[\s\S]*production\s*\{") 'Demo and production flavors are required.'
Assert-Contract ($gradle -match "XIAOWO_REQUIRE_HTTPS',\s*'true'") 'Production must require HTTPS.'
Assert-Contract ($gradle -notmatch 'capacitor-android|capacitor\.build') 'Gradle must not depend on Capacitor.'

$mainActivity = Get-Content -LiteralPath $mainActivityPath -Raw
Assert-Contract ($mainActivity -match 'extends\s+AppCompatActivity') 'MainActivity must use the controlled native WebView shell.'
Assert-Contract ($mainActivity -notmatch 'BridgeActivity|com\.getcapacitor') 'Capacitor Bridge must not be exposed to the remote page.'
Assert-Contract ($mainActivity -match 'setAcceptThirdPartyCookies\(mainWebView,\s*false\)') 'Third-party cookies must remain disabled.'

$webClient = Get-Content -LiteralPath $webClientPath -Raw
Assert-Contract ($webClient -match 'if\s*\(!request\.isForMainFrame\(\)\)\s*return false') 'Sub-frame navigation must not be treated as a top-level link.'
$externalLauncher = Get-Content -LiteralPath $externalLauncherPath -Raw
Assert-Contract ($externalLauncher -match 'CustomTabsIntent') 'HTTPS external links must use Custom Tabs.'

$secretHits = & rg -l 'github_pat_[A-Za-z0-9_]{20,}' $projectRoot `
    -g '!node_modules/**' -g '!artifacts/**' -g '!android/.gradle/**' -g '!android/app/build/**' -g '!.git/**' 2>$null
Assert-Contract (($LASTEXITCODE -eq 1) -and -not $secretHits) 'A GitHub PAT-like value was found in project files. Revoke it and remove it before building.'

Write-Host 'Project contract verification: PASS'
