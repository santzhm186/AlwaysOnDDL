param(
    [string]$AndroidSdk = $env:ANDROID_SDK_ROOT,
    [string]$JavaHome = $env:JAVA_HOME,
    [string]$BuildToolsVersion = '34.0.0',
    [int]$CompileApi = 35
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$unityBase = 'C:\Program Files\Unity\Hub\Editor\2022.3.62f3c1\Editor\Data\PlaybackEngines\AndroidPlayer'
if (-not $AndroidSdk -and (Test-Path -LiteralPath (Join-Path $unityBase 'SDK'))) { $AndroidSdk = Join-Path $unityBase 'SDK' }
if (-not $JavaHome -and (Test-Path -LiteralPath (Join-Path $unityBase 'OpenJDK'))) { $JavaHome = Join-Path $unityBase 'OpenJDK' }
if (-not $AndroidSdk -or -not $JavaHome) { throw 'Set ANDROID_SDK_ROOT and JAVA_HOME, or pass -AndroidSdk and -JavaHome.' }
$toolsDir = Join-Path $AndroidSdk "build-tools\$BuildToolsVersion"
$androidJar = Join-Path $AndroidSdk "platforms\android-$CompileApi\android.jar"
$javaBin = Join-Path $JavaHome 'bin'
foreach ($required in @($androidJar, (Join-Path $toolsDir 'aapt.exe'), (Join-Path $toolsDir 'd8.bat'), (Join-Path $toolsDir 'apksigner.bat'), (Join-Path $javaBin 'javac.exe'))) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing build dependency: $required" }
}
$env:JAVA_HOME = $JavaHome
$env:PATH = "$javaBin;$env:PATH"
$out = Join-Path $projectRoot 'build'
$gen = Join-Path $out 'generated'
$classes = Join-Path $out 'classes'
$dex = Join-Path $out 'dex'
foreach ($dir in @($out, $gen, $classes, $dex)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$utf8 = New-Object System.Text.UTF8Encoding($false)
[xml]$manifest = Get-Content -LiteralPath (Join-Path $projectRoot 'app\src\main\AndroidManifest.xml') -Raw -Encoding UTF8
$manifest.manifest.SetAttribute('package', 'com.ironwill.app')
$manifestPath = Join-Path $out 'AndroidManifest.xml'
$manifest.Save($manifestPath)
& (Join-Path $toolsDir 'aapt.exe') package -f -m -J $gen -M $manifestPath -S (Join-Path $projectRoot 'app\src\main\res') -I $androidJar -F (Join-Path $out 'resources.ap_') --min-sdk-version 28 --target-sdk-version 35 --version-code 1 --version-name '0.1.0'
if ($LASTEXITCODE -ne 0) { throw 'Android resource packaging failed.' }
$sources = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'app\src\main\java') -Filter '*.java' -Recurse) + @(Get-ChildItem -LiteralPath $gen -Filter '*.java' -Recurse)
$sourceArgs = $sources | ForEach-Object { '"' + $_.FullName.Replace('\','/') + '"' }
$sourceList = Join-Path $out 'sources.txt'
[System.IO.File]::WriteAllLines($sourceList, [string[]]$sourceArgs, $utf8)
& (Join-Path $javaBin 'javac.exe') -encoding UTF-8 --release 8 -classpath $androidJar -d $classes "@$sourceList"
if ($LASTEXITCODE -ne 0) { throw 'Java compilation failed.' }
& (Join-Path $javaBin 'jar.exe') cf (Join-Path $out 'classes.jar') -C $classes .
if ($LASTEXITCODE -ne 0) { throw 'Class archive failed.' }
& (Join-Path $toolsDir 'd8.bat') --lib $androidJar --min-api 28 --output $dex (Join-Path $out 'classes.jar')
if ($LASTEXITCODE -ne 0) { throw 'DEX conversion failed.' }
Copy-Item -LiteralPath (Join-Path $out 'resources.ap_') -Destination (Join-Path $out 'unsigned.apk') -Force
& (Join-Path $javaBin 'jar.exe') uf (Join-Path $out 'unsigned.apk') -C $dex classes.dex
if ($LASTEXITCODE -ne 0) { throw 'APK assembly failed.' }
& (Join-Path $toolsDir 'zipalign.exe') -f 4 (Join-Path $out 'unsigned.apk') (Join-Path $out 'aligned.apk')
if ($LASTEXITCODE -ne 0) { throw 'APK alignment failed.' }
$key = Join-Path $out 'ironwill-development.jks'
if (-not (Test-Path -LiteralPath $key)) {
    & (Join-Path $javaBin 'keytool.exe') -genkeypair -keystore $key -storepass android -keypass android -alias ironwill-dev -keyalg RSA -keysize 2048 -validity 10000 -dname 'CN=IronWill Development,O=Personal Development,C=US'
    if ($LASTEXITCODE -ne 0) { throw 'Development signing key creation failed.' }
}
$apk = Join-Path $out 'IronWill-Android-0.1.apk'
& (Join-Path $toolsDir 'apksigner.bat') sign --ks $key --ks-key-alias ironwill-dev --ks-pass pass:android --key-pass pass:android --out $apk (Join-Path $out 'aligned.apk')
if ($LASTEXITCODE -ne 0) { throw 'APK signing failed.' }
& (Join-Path $toolsDir 'apksigner.bat') verify --verbose $apk
if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed.' }
Get-FileHash -LiteralPath $apk -Algorithm SHA256
Write-Output "Built development APK: $apk"
