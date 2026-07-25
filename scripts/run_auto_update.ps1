param(
    [switch]$Build,
    [int]$MaxDataAgeDays = 5
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$automationScript = Join-Path $projectRoot "automation.py"

Set-Location -LiteralPath $projectRoot

if (Test-Path -LiteralPath $venvPython) {
    $pythonCommand = $venvPython
    $pythonPrefix = @()
} else {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -eq $launcher) {
        throw "Python was not found. Install Python 3.12 or create .venv."
    }
    $pythonCommand = $launcher.Source
    $pythonPrefix = @("-3.12")
}

$arguments = @(
    $automationScript,
    "--max-data-age-days",
    $MaxDataAgeDays.ToString()
)
if ($Build) {
    $arguments += "--build"
}

& $pythonCommand @pythonPrefix @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Automatic update failed with exit code $LASTEXITCODE. The previous forecast was preserved."
}
