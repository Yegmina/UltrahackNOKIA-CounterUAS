param(
    [string]$Serial = "5011AF1010013479",
    [ValidateSet("watch", "shell")]
    [string]$Mode = "watch",
    [int]$PollSeconds = 5,
    [int]$TimeoutSeconds = 600,
    [string]$JetsonHost = "",
    [int]$JetsonPort = 25000,
    [switch]$KeepStreaming,
    [int]$StreamSeconds = 3600,
    [int]$UdpMaxFrames = 25,
    [switch]$NoSysfsPower
)

$ErrorActionPreference = "Stop"

$Adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
$Deadline = (Get-Date).AddSeconds($TimeoutSeconds)

Write-Host "Waiting for ADB device $Serial to become authorized..."
while ((Get-Date) -lt $Deadline) {
    $Devices = & $Adb devices -l
    $Line = $Devices | Select-String -Pattern $Serial | Select-Object -First 1
    if ($null -eq $Line) {
        Write-Host "$(Get-Date -Format HH:mm:ss) device not listed"
    } elseif ($Line -match "unauthorized") {
        Write-Host "$(Get-Date -Format HH:mm:ss) unauthorized - accept USB debugging on phone"
    } elseif ($Line -match "\bdevice\b") {
        Write-Host "$(Get-Date -Format HH:mm:ss) authorized"
        if ($Mode -eq "shell") {
            $ArgsList = @(
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", (Join-Path $PSScriptRoot "run_thermal_shell_bridge_test.ps1"),
                "-Serial", $Serial,
                "-JetsonPort", $JetsonPort,
                "-StreamSeconds", $StreamSeconds,
                "-UdpMaxFrames", $UdpMaxFrames
            )
            if ($JetsonHost) {
                $ArgsList += @("-JetsonHost", $JetsonHost)
            }
            if ($NoSysfsPower) {
                $ArgsList += @("-NoSysfsPower")
            }
            & powershell @ArgsList
        } else {
            $ArgsList = @(
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", (Join-Path $PSScriptRoot "run_thermal_bridge_watch_test.ps1"),
                "-Serial", $Serial,
                "-JetsonPort", $JetsonPort,
                "-UdpMaxFrames", $UdpMaxFrames
            )
            if ($JetsonHost) {
                $ArgsList += @("-JetsonHost", $JetsonHost)
            }
            if ($KeepStreaming) {
                $ArgsList += @("-KeepStreaming", "-StreamSeconds", $StreamSeconds)
            }
            & powershell @ArgsList
        }
        exit $LASTEXITCODE
    } else {
        Write-Host "$(Get-Date -Format HH:mm:ss) unexpected adb state: $Line"
    }
    Start-Sleep -Seconds $PollSeconds
}

throw "Timed out waiting for authorized ADB device $Serial after $TimeoutSeconds seconds."
