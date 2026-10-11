param(
    [string]$Python = "python",
    [ValidatePattern('^\d+\.\d+(\.\d+)?$')]
    [string]$Version = "0.1.2"
)
$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$outputDir = Join-Path $projectRoot "dist"
$workDir = Join-Path $PSScriptRoot "build"
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

# Install requirements-build.txt into your own virtual environment first.
# This script does not launch monitoring, install startup tasks, or touch user data.
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "IronWill-Windows-$Version" `
    --distpath $outputDir --workpath $workDir --specpath $workDir `
    --paths $PSScriptRoot (Join-Path $PSScriptRoot "run.py")
if ($LASTEXITCODE -ne 0) { throw "Windows package build failed ($LASTEXITCODE)." }
$artifact = Join-Path $outputDir "IronWill-Windows-$Version.exe"
Copy-Item -LiteralPath $artifact -Destination (Join-Path $outputDir "IronWill.exe") -Force
Get-FileHash -Algorithm SHA256 -LiteralPath $artifact
