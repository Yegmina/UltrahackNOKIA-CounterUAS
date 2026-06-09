$ErrorActionPreference = "Stop"

py -3 -m streamlit run "$PSScriptRoot\app.py" --server.port 8502
