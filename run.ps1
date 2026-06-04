$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
& ".\.venv\Scripts\python.exe" ".\sakazi_shop_detector.py" --config ".\config.json" @args
