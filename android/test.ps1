param([string]$JavaHome = $env:JAVA_HOME)
$ErrorActionPreference = 'Stop'
if (-not $JavaHome) { $JavaHome = 'C:\Program Files\Unity\Hub\Editor\2022.3.62f3c1\Editor\Data\PlaybackEngines\AndroidPlayer\OpenJDK' }
$out = Join-Path $PSScriptRoot 'build\tests'
New-Item -ItemType Directory -Path $out -Force | Out-Null
& (Join-Path $JavaHome 'bin\javac.exe') -encoding UTF-8 --release 8 -d $out (Join-Path $PSScriptRoot 'app\src\main\java\com\ironwill\app\ReminderGate.java') (Join-Path $PSScriptRoot 'app\src\main\java\com\ironwill\app\RelayHttpPolicy.java') (Join-Path $PSScriptRoot 'tests\com\ironwill\app\ReminderGateTest.java')
if ($LASTEXITCODE -ne 0) { throw 'Core test compilation failed.' }
& (Join-Path $JavaHome 'bin\java.exe') -cp $out com.ironwill.app.ReminderGateTest
if ($LASTEXITCODE -ne 0) { throw 'Core tests failed.' }
