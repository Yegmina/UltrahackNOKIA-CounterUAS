$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")

Set-Location $RepoRoot
py -3 -m streamlit run "$ScriptDir\app.py" --server.address 127.0.0.1 --server.port 8510 --server.maxUploadSize 1536
