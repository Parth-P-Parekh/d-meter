param(
    [string]$TaskName = "PowerBoard Production Training Capture",
    [string]$Config = (Join-Path $PSScriptRoot "..\production_frame_capture\config.json")
)

$ErrorActionPreference = "Stop"
$runner = (Resolve-Path (Join-Path $PSScriptRoot "run_production_collector.ps1")).Path
$configPath = (Resolve-Path $Config).Path
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Config "{1}"' -f $runner, $configPath
)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -User $env:USERNAME -Force
Write-Host "Installed task: $TaskName"
