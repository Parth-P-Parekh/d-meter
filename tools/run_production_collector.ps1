param(
    [string]$Config = (Join-Path $PSScriptRoot "..\production_frame_capture\config.json"),
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$arguments = @("-m", "production_frame_capture.collector", "--config", $Config)
if ($Once) { $arguments += "--once" }

Push-Location $projectRoot
try {
    & python @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
