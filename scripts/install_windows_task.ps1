param(
    [string]$TaskName = "AShareCompass_AutoUpdate",
    [string]$WeekdayTime = "18:35",
    [string]$SundayTime = "20:30"
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runner = Join-Path $projectRoot "scripts\run_auto_update.ps1"
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$actionArguments = (
    '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Build' -f $runner
)
$action = New-ScheduledTaskAction `
    -Execute $powershell `
    -Argument $actionArguments `
    -WorkingDirectory $projectRoot

$weekdayTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $WeekdayTime
$sundayTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Sunday `
    -At $SundayTime

$principal = New-ScheduledTaskPrincipal `
    -UserId $userId `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

$task = New-ScheduledTask `
    -Action $action `
    -Trigger @($weekdayTrigger, $sundayTrigger) `
    -Principal $principal `
    -Settings $settings `
    -Description "A-Share Compass v7: fetch, retrain, validate, and build."

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Get-ScheduledTask -TaskName $TaskName |
    Select-Object TaskName, State, Description
