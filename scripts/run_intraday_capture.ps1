param()

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location $projectRoot
$python = (Get-Command py.exe -ErrorAction Stop).Source
& $python intraday_update.py
exit $LASTEXITCODE
