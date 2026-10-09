<#
.SYNOPSIS
  Registers a Windows Task Scheduler job that runs the forecast capture every 6 hours.

.DESCRIPTION
  The CAMS forecast vintages cannot be reconstructed later, so a missed run is a permanent gap.
  The task therefore wakes the PC, starts as soon as possible after a missed start, and is
  not stopped when the machine goes idle. Each run appends to data\logs\capture.log and the
  script exits non-zero if either source failed or the wind-unit guard tripped.

  One run captures both CAMS air quality and the weather model, so CAMS is fetched 4 times
  a day instead of the planned once. Duplicate vintages are harmless and can be removed by
  payload hash in bronze.

  Registers for the current user, runs only while that user is logged on, needs no admin
  rights. Keeping the PC awake on mains power is a separate, one-time admin step:
      powercfg /change standby-timeout-ac 0
      powercfg /change hibernate-timeout-ac 0

  Run the capture once by hand before registering:
      .\.venv\Scripts\python.exe src\plovdiv\extract\fetch_forecasts.py

.EXAMPLE
  .\scripts\register_capture_task.ps1
  .\scripts\register_capture_task.ps1 -Unregister
#>
param(
    [string]$TaskName = "PlovdivForecastCapture",
    [int]$EveryHours = 6,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$script = Join-Path $repo "src\plovdiv\extract\fetch_forecasts.py"
$logDir = Join-Path $repo "data\logs"
$log = Join-Path $logDir "capture.log"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed task $TaskName"
    return
}

foreach ($p in @($python, $script)) {
    if (-not (Test-Path $p)) { throw "Missing $p. Create the .venv and check the repo layout first." }
}
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# cmd.exe is used only so stdout and stderr can be appended to a log file.
$cmdLine = "/c `"`"$python`" `"$script`" >> `"$log`" 2>&1`""
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdLine -WorkingDirectory $repo

$start = (Get-Date).Date.AddDays(1)  # next local midnight, then every N hours
$trigger = New-ScheduledTaskTrigger -Once -At $start `
    -RepetitionInterval (New-TimeSpan -Hours $EveryHours) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Capture Open-Meteo and CAMS forecast vintages into data\landing" -Force | Out-Null

Write-Host "Registered ${TaskName}: every $EveryHours h from $start. Log: $log"
Write-Host "Check with: Get-ScheduledTaskInfo -TaskName $TaskName"
