param([int]$Port = 8502)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$appPath = Join-Path $PSScriptRoot 'app/ra_mvp_ui.py'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Python 3.11 virtual environment is missing. Follow README.md setup first.'
}
& $pythonPath -m streamlit run $appPath --server.address 127.0.0.1 --server.port $Port
exit $LASTEXITCODE
