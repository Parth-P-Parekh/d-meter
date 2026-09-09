param(
    [string]$DatasetRoot = "C:\Users\Admin\TrainingImages\PowerBoard",
    [string]$TaskName = "PowerBoard Production Training Capture"
)

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "Scheduled task: not installed"
}
else {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Scheduled task state: $($task.State)"
    Write-Host "Last result: $($info.LastTaskResult)"
}

$statusPath = Join-Path $DatasetRoot "collector-status.json"
if (Test-Path -LiteralPath $statusPath) {
    Get-Content -Raw -LiteralPath $statusPath
}
else {
    Write-Host "Collector status: no status file at $statusPath"
}
