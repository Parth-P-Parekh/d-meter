[CmdletBinding()]
param(
    [ValidatePattern('^(?:\d{1,3}\.){3}$')]
    [string]$Subnet = '192.168.1.',

    [ValidateRange(0, 255)]
    [int]$StartHost = 0,

    [ValidateRange(0, 255)]
    [int]$EndHost = 255,

    [ValidateRange(100, 10000)]
    [int]$TimeoutMs = 750,

    [ValidateRange(1, 256)]
    [int]$BatchSize = 64,

    [string]$SourceAddress = '192.168.1.17'
)

$ErrorActionPreference = 'Stop'

if ($StartHost -gt $EndHost) {
    throw 'StartHost must be less than or equal to EndHost.'
}

$octets = $Subnet.TrimEnd('.').Split('.')
if ($octets.Count -ne 3 -or ($octets | Where-Object { [int]$_ -lt 0 -or [int]$_ -gt 255 })) {
    throw "Subnet must contain three valid octets, for example '192.168.1.'."
}

if ($SourceAddress) {
    $parsedSource = $null
    if (-not [Net.IPAddress]::TryParse($SourceAddress, [ref]$parsedSource)) {
        throw "Invalid SourceAddress: $SourceAddress"
    }
}

$targets = @($StartHost..$EndHost | ForEach-Object { "$Subnet$_" })
$responsive = [Collections.Generic.List[string]]::new()

Write-Host "Pinging $($targets.Count) addresses from $($targets[0]) through $($targets[-1])..."
if ($SourceAddress) {
    Write-Host "Source address: $SourceAddress"
}

for ($offset = 0; $offset -lt $targets.Count; $offset += $BatchSize) {
    $last = [Math]::Min($offset + $BatchSize - 1, $targets.Count - 1)
    $processes = foreach ($target in $targets[$offset..$last]) {
        $arguments = @('-n', '1', '-w', $TimeoutMs)
        if ($SourceAddress) {
            $arguments += @('-S', $SourceAddress)
        }
        $arguments += $target

        $startInfo = [Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = "$env:SystemRoot\System32\PING.EXE"
        $startInfo.Arguments = $arguments -join ' '
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true

        [pscustomobject]@{
            Target  = $target
            Process = [Diagnostics.Process]::Start($startInfo)
        }
    }

    foreach ($probe in $processes) {
        $probe.Process.WaitForExit()
        if ($probe.Process.ExitCode -eq 0) {
            $responsive.Add($probe.Target)
            Write-Host "UP  $($probe.Target)" -ForegroundColor Green
        }
        $probe.Process.Dispose()
    }
}

if ($responsive.Count -eq 0) {
    Write-Host 'No hosts replied to ICMP echo.' -ForegroundColor Yellow
    exit 1
}

Write-Host "`n$($responsive.Count) host(s) replied:"
$responsive | ForEach-Object { Write-Output $_ }
