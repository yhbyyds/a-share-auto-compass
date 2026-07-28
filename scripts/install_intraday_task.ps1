param(
    [string]$TaskName = "AShareCompass_IntradayCapture"
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runner = Join-Path $projectRoot "scripts\run_intraday_capture.ps1"
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powershell -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $runner) -WorkingDirectory $projectRoot

# Fixed sample points avoid treating a stream of correlated quotes as thousands
# of independent observations.  The lunch interval is intentionally omitted.
$times = @("09:35", "10:00", "10:30", "11:00", "13:30", "14:00", "14:30", "14:50")
$triggers = foreach ($time in $times) {
    New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $time
}
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 8) -MultipleInstances IgnoreNew
$task = New-ScheduledTask -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description "A-Share Compass: fixed-time intraday research snapshots."
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State, Description
