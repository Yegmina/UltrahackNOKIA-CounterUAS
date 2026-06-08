param(
    [string]$Serial = "5011AF1010013479",
    [int]$GrantTimeoutMs = 20000,
    [int]$WaitAfterGrantSeconds = 75,
    [string]$JetsonHost = "",
    [int]$JetsonPort = 25000,
    [switch]$KeepStreaming,
    [int]$StreamSeconds = 3600,
    [int]$UdpMaxFrames = 25,
    [switch]$UseHeadlessFixedHandler,
    [switch]$SkipManualGrant
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$Adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
$BridgeApk = Join-Path $RepoRoot "prototype\android_thermovue_bridge_probe\build\thermovue-bridge-probe.apk"
$HelperDex = Join-Path $RepoRoot "prototype\android_usb_shell_helper\build\usb-shell-helper.dex"

function Invoke-Adb {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & $Adb -s $Serial @Args
}

function Invoke-Shell {
    param([string]$Command)
    Invoke-Adb shell $Command
}

if (!(Test-Path $BridgeApk)) {
    throw "Bridge APK not found. Build it first: prototype\build_thermovue_bridge_probe.ps1"
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

Write-Host "Installing bridge APK..."
Invoke-Adb install -r $BridgeApk

Write-Host "Granting bridge runtime permissions..."
Invoke-Shell "pm grant com.yegmina.thermovuebridgeprobe android.permission.CAMERA"
Invoke-Shell "pm grant com.yegmina.thermovuebridgeprobe android.permission.RECORD_AUDIO"

Write-Host "Pushing USB shell helper..."
Invoke-Adb push $HelperDex /data/local/tmp/usb-shell-helper.dex

Write-Host "Clearing logcat..."
Invoke-Adb logcat -c

if ($UseHeadlessFixedHandler) {
    Write-Host "Setting headless fixed USB handler for package-level USB grant..."
    Invoke-Shell "CLASSPATH=/data/local/tmp/usb-shell-helper.dex app_process /system/bin com.yegmina.usbshellhelper.UsbShellHelper set-fixed-handler"
} else {
    Write-Host "Clearing fixed USB handler so ThermoVue can remain foreground..."
    Invoke-Shell "CLASSPATH=/data/local/tmp/usb-shell-helper.dex app_process /system/bin com.yegmina.usbshellhelper.UsbShellHelper clear-fixed-handler"
}

Write-Host "Restarting apps..."
Invoke-Shell "am force-stop com.energy.tc2c"
Invoke-Shell "am force-stop com.yegmina.thermovuebridgeprobe"

Write-Host "Starting bridge watcher..."
$BridgeStart = "am start -n com.yegmina.thermovuebridgeprobe/.MainActivity --ez watchUsb true"
if ($JetsonHost) {
    $BridgeStart += " --es jetsonHost '$JetsonHost' --ei jetsonPort $JetsonPort"
}
if ($KeepStreaming) {
    $BridgeStart += " --ez keepStreaming true --ei streamSeconds $StreamSeconds"
}
$BridgeStart += " --ei udpMaxFrames $UdpMaxFrames"
Invoke-Shell $BridgeStart

Write-Host "Launching ThermoVue..."
Invoke-Shell "am start -n com.energy.tc2c/com.energy.usbCamera.ui.splash.SplashActivity"

if ($SkipManualGrant) {
    Write-Host "Skipping manual thermal USB grant; relying on fixed-handler/package permission."
} else {
    Write-Host "Granting thermal USB to bridge..."
    Invoke-Shell "CLASSPATH=/data/local/tmp/usb-shell-helper.dex app_process /system/bin com.yegmina.usbshellhelper.UsbShellHelper grant-thermal com.yegmina.thermovuebridgeprobe $GrantTimeoutMs"
}

Write-Host "Waiting $WaitAfterGrantSeconds seconds for frame polling..."
Start-Sleep -Seconds $WaitAfterGrantSeconds

Write-Host "Pulling latest bridge log..."
$LatestDir = Invoke-Shell "ls /sdcard/Android/data/com.yegmina.thermovuebridgeprobe/files | grep bridge_probe_ | tail -1"
$LatestDir = ($LatestDir | Select-Object -First 1).Trim()
if (!$LatestDir) {
    throw "No bridge_probe_* log directory found."
}

$LocalLogDir = Join-Path $RepoRoot "prototype\logs\thermovue_bridge_watch"
New-Item -ItemType Directory -Path $LocalLogDir -Force | Out-Null
$LocalLog = Join-Path $LocalLogDir "$LatestDir.log"
Invoke-Adb pull "/sdcard/Android/data/com.yegmina.thermovuebridgeprobe/files/$LatestDir/thermovue_bridge_probe.log" $LocalLog

Write-Host "Log saved to $LocalLog"
Select-String -Path $LocalLog -Pattern "USB grant watch ready|DeviceControl initHandleEngine|DeviceControl USB state|Tiny2C poll|frameDump|rawTemp=len|remapTemp=len|FAIL|FATAL"

Write-Host "Saving focused logcat..."
$LocalLogcat = Join-Path $LocalLogDir "$LatestDir.logcat.txt"
Invoke-Adb logcat -d -v time | Out-File -FilePath $LocalLogcat -Encoding utf8
Write-Host "Logcat saved to $LocalLogcat"
Select-String -Path $LocalLogcat -Pattern "ThermoVueBridgeProbe|TINY2C_STEP|UvcNativeCamDualFusionPreviewManager|StartPreviewTask|USBMonitorManager|IrcamEngine|startPreview result|initHandleEngine|Exception|Fatal|FATAL" | Select-Object -Last 120
