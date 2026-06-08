param(
    [string]$Serial = "5011AF1010013479",
    [int]$WaitBeforeBridgeSeconds = 8,
    [string]$JetsonHost = "",
    [int]$JetsonPort = 25000,
    [int]$StreamSeconds = 120,
    [int]$UdpMaxFrames = 25,
    [switch]$NoSysfsPower
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$Adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
$HelperDex = Join-Path $RepoRoot "prototype\android_usb_shell_helper\build\usb-shell-helper.dex"

function Invoke-Adb {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & $Adb -s $Serial @Args
}

function Invoke-Shell {
    param([string]$Command)
    Invoke-Adb shell $Command
}

if (!(Test-Path $HelperDex)) {
    throw "USB shell helper dex not found. Build it first: prototype\build_usb_shell_helper.ps1"
}

Write-Host "Checking ADB device $Serial..."
$Devices = & $Adb devices -l
$Line = $Devices | Select-String -Pattern $Serial | Select-Object -First 1
if ($null -eq $Line) {
    throw "Device $Serial not listed by adb."
}
if ($Line -match "unauthorized") {
    throw "Device $Serial is unauthorized. Accept the USB debugging prompt on the phone."
}

Write-Host "Pushing USB shell helper..."
Invoke-Adb push $HelperDex /data/local/tmp/usb-shell-helper.dex

Write-Host "Clearing logcat..."
Invoke-Adb logcat -c

Write-Host "Clearing old shell bridge temp dirs..."
Invoke-Shell "rm -rf /data/local/tmp/thermovue_shell_bridge_* /data/local/tmp/thermovue_shell_libs /data/local/tmp/thermovue_shell_dex"

Write-Host "Launching ThermoVue to power the internal thermal USB module..."
Invoke-Shell "am force-stop com.energy.tc2c"
Invoke-Shell "am start -n com.energy.tc2c/com.energy.usbCamera.ui.splash.SplashActivity"
Start-Sleep -Seconds $WaitBeforeBridgeSeconds

$ArgsText = "--stream-seconds $StreamSeconds --udp-max-frames $UdpMaxFrames"
if ($JetsonHost) {
    $ArgsText += " --jetson-host $JetsonHost --jetson-port $JetsonPort"
}
if ($NoSysfsPower) {
    $ArgsText += " --no-sysfs-power"
}

Write-Host "Running shell-side ThermoVue bridge..."
Invoke-Shell "CLASSPATH=/data/local/tmp/usb-shell-helper.dex app_process /system/bin com.yegmina.usbshellhelper.ThermoVueShellBridge $ArgsText"

Write-Host "Pulling latest shell bridge log..."
$LatestDir = Invoke-Shell "ls -d /data/local/tmp/thermovue_shell_bridge_* 2>/dev/null | tail -1"
$LatestDir = ($LatestDir | Select-Object -First 1).Trim()
if (!$LatestDir) {
    throw "No /data/local/tmp/thermovue_shell_bridge_* log directory found."
}

$LocalLogDir = Join-Path $RepoRoot "prototype\logs\thermovue_shell_bridge"
New-Item -ItemType Directory -Path $LocalLogDir -Force | Out-Null
$LocalLog = Join-Path $LocalLogDir ((Split-Path -Leaf $LatestDir) + ".log")
Invoke-Adb pull "$LatestDir/thermovue_shell_bridge.log" $LocalLog

Write-Host "Log saved to $LocalLog"
Select-String -Path $LocalLog -Pattern "classLoad|shellGrant|Shell USB state|initHandleEngine|Tiny2C poll|streamTiny2c|udpThermalFrame|FAIL|Exception|FATAL"

$Validator = Join-Path $RepoRoot "prototype\thermal_frame_evidence_validator.py"
if (Test-Path $Validator) {
    Write-Host "Validating thermal frame evidence..."
    & py -3 $Validator --bridge-log $LocalLog
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Thermal evidence validator did not pass for $LocalLog"
    }
}

Write-Host "Saving focused logcat..."
$LocalLogcat = Join-Path $LocalLogDir ((Split-Path -Leaf $LatestDir) + ".logcat.txt")
Invoke-Adb logcat -d -v time | Out-File -FilePath $LocalLogcat -Encoding utf8
Write-Host "Logcat saved to $LocalLogcat"
Select-String -Path $LocalLogcat -Pattern "ThermoVueShellBridge|TINY2C_STEP|UvcNativeCamDualFusionPreviewManager|StartPreviewTask|USBMonitorManager|IrcamEngine|startPreview result|initHandleEngine|Exception|Fatal|FATAL" | Select-Object -Last 120
