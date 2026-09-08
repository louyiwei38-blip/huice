$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..
New-Item -ItemType Directory -Force -Path logs | Out-Null
$env:PYTHONUNBUFFERED = "1"
Write-Host "starting Range MR live (Ctrl+C to stop)"
python -u run_server.py
