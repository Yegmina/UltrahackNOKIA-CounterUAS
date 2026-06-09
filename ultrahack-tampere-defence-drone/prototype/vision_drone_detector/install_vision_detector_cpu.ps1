$ErrorActionPreference = "Stop"

Write-Host "Installing CPU-only PyTorch packages..."
py -3 -m pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

Write-Host "Installing vision detector dependencies..."
py -3 -m pip install --no-cache-dir -r "$PSScriptRoot\requirements-vision-drone-detector.txt"

Write-Host ""
Write-Host "Done. Download the model with:"
Write-Host "powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\download_anti_uav_model.ps1`""
