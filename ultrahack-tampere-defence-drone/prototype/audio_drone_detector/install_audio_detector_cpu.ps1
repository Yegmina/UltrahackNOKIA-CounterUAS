param(
    [string]$VenvPath = "$PSScriptRoot\.venv-audio-detector"
)

$ErrorActionPreference = "Stop"

Write-Host "Creating audio detector virtual environment: $VenvPath"
py -3 -m venv $VenvPath

$Python = Join-Path $VenvPath "Scripts\python.exe"
& $Python -m pip install --no-cache-dir --upgrade pip

Write-Host "Installing CPU-only PyTorch..."
& $Python -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

Write-Host "Installing audio detector dependencies..."
& $Python -m pip install --no-cache-dir -r (Join-Path $PSScriptRoot "requirements-audio-drone-detector.txt")

Write-Host ""
Write-Host "Done. Start the UI with:"
Write-Host "`"$Python`" -m streamlit run `"$PSScriptRoot\app.py`""
