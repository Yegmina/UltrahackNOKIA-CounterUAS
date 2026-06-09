param(
    [switch]$SkipBuild,
    [switch]$NoLaunchThermoVue,
    [int]$WaitSeconds = 8,
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"

function Invoke-Native {
    param([string[]]$Command)
    $Exe = $Command[0]
    $Args = @($Command | Select-Object -Skip 1)
    & $Exe @Args
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($Command -join ' ')"
    }
}

$PrototypeRoot = $PSScriptRoot
$RepoRoot = Split-Path $PrototypeRoot -Parent
$Adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
$Apk = Join-Path $PrototypeRoot "android_thermal_live_debug\build\thermal-live-debug.apk"

if (!(Test-Path $Adb)) {
    throw "adb not found at $Adb"
}

if (!$SkipBuild) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PrototypeRoot "build_thermal_live_debug.ps1")
}

if (!(Test-Path $Apk)) {
    throw "APK not found: $Apk"
}

Invoke-Native @($Adb, "install", "-r", $Apk)
Invoke-Native @($Adb, "shell", "am", "start", "-n", "com.yegmina.thermallivedebug/.MainActivity")
Start-Sleep -Seconds 2
Invoke-Native @($Adb, "forward", "tcp:8088", "tcp:8088")

if (!$NoLaunchThermoVue) {
    Invoke-Native @($Adb, "shell", "monkey", "-p", "com.energy.tc2c", "1")
    Start-Sleep -Seconds 6
}

Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8088/priv-audit" | Out-Null
Start-Sleep -Seconds $WaitSeconds

if (!$OutDir) {
    $OutDir = Join-Path $PrototypeRoot "logs\thermal_privilege_audit"
}
New-Item -ItemType Directory -Force $OutDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $OutDir "thermal_privilege_audit_$Stamp.log"
$LogText = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8088/log").Content
Set-Content -Path $LogPath -Value $LogText -Encoding UTF8

Write-Host "Saved $LogPath"
Select-String -Path $LogPath -Pattern @(
    "ThermoVue privilege",
    "self uid",
    "AUDIT_",
    "sysfsWrite",
    "usbDeviceCount",
    "vendor=0x3474",
    "package classloader",
    "class OK",
    "class FAIL",
    "CLONE_"
) -Context 0, 1 | Select-Object -Last 220
