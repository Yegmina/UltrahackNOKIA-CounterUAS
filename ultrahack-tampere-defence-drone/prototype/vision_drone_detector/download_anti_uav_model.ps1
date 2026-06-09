param(
    [string]$ModelUrl = "https://raw.githubusercontent.com/zsx060/Anti-UAV-datasets/master/best.pt",
    [string]$OutPath = "$PSScriptRoot\models\best.pt",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$outDir = Split-Path -Parent $OutPath
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

if ((Test-Path -LiteralPath $OutPath) -and -not $Force) {
    Write-Host "Model already exists: $OutPath"
    Write-Host "Use -Force to download again."
    exit 0
}

Write-Host "Downloading Anti-UAV model..."
Write-Host $ModelUrl
Invoke-WebRequest -Uri $ModelUrl -OutFile $OutPath

$item = Get-Item -LiteralPath $OutPath
Write-Host "Saved model: $($item.FullName) ($([math]::Round($item.Length / 1MB, 2)) MB)"
