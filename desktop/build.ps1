param(
    [string]$Python = "python",
    [ValidatePattern('^\d+\.\d+(\.\d+)?$')]
    [string]$Version = "0.1.7"
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
    --add-data "$projectRoot\LICENSE;." `
    --add-data "$projectRoot\THIRD_PARTY_NOTICES.md;." `
    --add-data "$projectRoot\licenses;licenses" `
    --paths $PSScriptRoot (Join-Path $PSScriptRoot "run.py")
if ($LASTEXITCODE -ne 0) { throw "Windows package build failed ($LASTEXITCODE)." }
$artifact = Join-Path $outputDir "IronWill-Windows-$Version.exe"
try {
    Copy-Item -LiteralPath $artifact -Destination (Join-Path $outputDir "IronWill.exe") -Force
} catch {
    Write-Warning "版本文件已生成，但固定入口暂未覆盖（可能仍在运行）。请使用版本文件；退出旧程序后可将它复制为 IronWill.exe。"
}
Get-FileHash -Algorithm SHA256 -LiteralPath $artifact
