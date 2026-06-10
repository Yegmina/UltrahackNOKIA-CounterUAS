$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

py -3 -m streamlit run app.py --server.port 8511 --server.address 127.0.0.1 --server.maxUploadSize 8192
