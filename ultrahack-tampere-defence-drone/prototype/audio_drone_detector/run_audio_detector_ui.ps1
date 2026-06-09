param(
    [string]$VenvPath = "$PSScriptRoot\.venv-audio-detector"
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $VenvPath "Scripts\python.exe"

if (!(Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run install_audio_detector_cpu.ps1 first."
}

& $Python -m streamlit run (Join-Path $PSScriptRoot "app.py")
