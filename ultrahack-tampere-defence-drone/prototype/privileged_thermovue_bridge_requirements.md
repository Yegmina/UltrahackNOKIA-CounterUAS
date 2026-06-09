# Privileged ThermoVue Bridge Requirements

Date: 2026-06-09

Goal: run our own Android bridge that powers the Ulefone Armor 28 Ultra Thermal
Tiny2C module, opens the internal UVC thermal USB device, receives the same
thermal frames ThermoVue receives, and streams them to the Jetson.

## What The Stock Phone Blocks

The internal thermal module appears as USB device `0x3474:0x4321` only after
ThermoVue powers it.

Normal side-loaded apps cannot:

- write the Tiny2C power/mux sysfs nodes;
- call `UsbManager.grantPermission(...)`;
- keep the thermal USB device alive while asking Android's USB permission UI;
- instrument ThermoVue, because target-package instrumentation requires a
  matching signature;
- use `run-as`, because ThermoVue is not debuggable.

## Minimum Ulefone/InfiSense Help Needed

One of these would unblock the direct bridge path:

1. A Ulefone/InfiSense SDK sample app that exposes the Tiny2C thermal frame
   callback to third-party code.
2. A platform-signed APK build of our bridge, installed with the same effective
   privilege class as ThermoVue.
3. A temporary engineering firmware or device policy that lets our bridge run as
   a privileged/system app for the hackathon.

The bridge needs:

```text
android.permission.MANAGE_USB
SELinux/domain ability equivalent to ThermoVue's u:r:platform_app:s0
access to /sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode
access to /sys/class/yft_extcon/tiny2c_mode
USB access to VID:PID 3474:4321
```

Additional USB framework detail found in `services.jar`:

- normal USB activity matching is ignored for product ID `17185` / `0x4321`;
- the log line is `yft ignore YF USB attach notification ---`;
- this happens before Android grants permission to a matched static USB handler;
- the fixed-handler path `deviceAttachedForFixedHandler(...)` grants permission
  before launching the handler and bypasses this ignore branch.

So a good vendor-side path is one of:

```text
UsbService.setUsbDeviceConnectionHandler(
  ComponentName("com.yegmina.thermovuebridgeprobe", "com.yegmina.thermovuebridgeprobe.MainActivity")
)
```

or a firmware default/fixed USB host connection handler pointing to:

```text
com.yegmina.thermovuebridgeprobe/.MainActivity
```

The bridge now attempts this fixed-handler setup in privileged mode. On the
stock side-loaded build, the app cannot complete it, but the system/framework
path is confirmed by decompilation.

## 2026-06-09 Side-Loaded Exact-Startup Validation

Build marker:

```text
thermovue-bridge-probe 2026-06-09 privileged-exact-startup
```

The bridge now runs the same high-level order as ThermoVue Pro's
`StartPreviewTask`:

```text
USBMonitorManager.init/registerMonitor
Tiny2CDualFusionProxy.startRestartTimer
GPIOUtils.powerUpControl / Tiny2C sysfs power attempt
wait up to 9000 ms for USBMonitorManager.isDeviceConnected
MImageUtils.MRun3/initMNNModelModule/MRun1
Tiny2CDualFusionProxy.initData
Tiny2CDualFusionProxy.initHandleEngine(ctrlBlock, true)
poll getFrameCount/getRawTempData/getRemapTempData
```

Current connected-phone result as a normal side-loaded APK:

```text
self context=u:r:untrusted_app:s0:...
sysfsWrite FAIL ... tiny2c_usb_mode ... EACCES
sysfsWrite FAIL ... tiny2c_mode ... EACCES
ExactPro Android USB thermal device not visible after GPIO power-up
ExactPro vendorUsbConnected=false ctrlBlock=null
ExactPro initHandleEngine skipped because ctrlBlock=null
ExactPro direct initHandle frameSeen=false
ExactPro worker StartPreviewTask frameSeen=false
DeviceControl explicit startPreview frameSeen=false
```

This is now a clean validation target: the same APK should change from
`untrusted_app`/`EACCES`/`ctrlBlock=null` to sysfs OK, USB connected, non-null
`ctrlBlock`, and non-empty frame data when installed through a real
platform/privileged route.

## Validation Command Sequence

After Ulefone provides a privileged/signed route, validate with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\build_thermovue_bridge_probe.ps1
adb install -r prototype\android_thermovue_bridge_probe\build\thermovue-bridge-probe.apk
adb shell am start -n com.yegmina.thermovuebridgeprobe/.MainActivity --ez privileged true
adb shell cat /sdcard/Android/data/com.yegmina.thermovuebridgeprobe/files/*/thermovue_bridge_probe.log
```

To stream frames to the Jetson/laptop receiver, start the receiver first:

```powershell
py -3 prototype\thermal_udp_receiver.py --host 0.0.0.0 --port 25000 --save-dir prototype\data\raw\thermal_udp
```

Then launch the bridge with the receiver IP:

```powershell
adb shell am start -n com.yegmina.thermovuebridgeprobe/.MainActivity --ez privileged true --es jetsonHost <JETSON_OR_LAPTOP_IP> --ei jetsonPort 25000
```

Success criteria:

```text
fixedUsbHandlerBinder OK component=com.yegmina.thermovuebridgeprobe/.MainActivity
sysfsWrite OK path=/sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode value=1
sysfsWrite OK path=/sys/class/yft_extcon/tiny2c_mode value=1
GPIOUtils.powerUpControl invoked
waitForThermalUsb found /dev/bus/usb/001/002 vendor=0x3474 product=0x4321
hiddenUsbGrant ... hasPermission=true
USBMonitor hasPermissionAfterRequest=true
USBMonitor openDevice result=<non-null control block>
Tiny2C poll frameCount increases
getRawTempData returns non-null 256x192 thermal data
frameDump raw_temp path=...
udpThermalFrame sent ... chunks=...
```

On the stock side-loaded phone build, the expected failure is:

```text
sysfsWrite FAIL ... EACCES (Permission denied)
waitForThermalUsb timeout
ExactPro Android USB thermal device not visible after GPIO power-up
ExactPro vendorUsbConnected=false ctrlBlock=null
Tiny2C poll 0 frameCount=0 ... rawTemp=null
```

That failure is useful: it proves the bridge is testing the same privilege gates
that a Ulefone-signed/system build must pass.

## FactoryMode / Changenode Requirement

The connected phone contains a privileged FactoryMode thermal test:

```text
com.yft.factorymode/.InfirayEcoTest
```

When launched through FactoryMode's own menu, that test can power the thermal
module and reaches:

```text
USBMonitor->onAttach
USBMonitor->onGranted
USBMonitor->onConnect
libuvc/device: bNumInterfaces=2
```

The same test is not exported, so shell cannot launch it directly. FactoryMode
also confirms the vendor-side thermal node HAL:

```text
vendor.yft.hardware.changenode@1.0::IChangeNode/default
change_node_data(String node, String data)
is_node_contain(String node)
```

Shell/app_process with FactoryMode's APK on the classpath is still denied:

```text
avc: denied { find } for interface=vendor.yft.hardware.changenode::IChangeNode
sid=u:r:shell:s0
tcontext=u:object_r:hal_changenode_hwservice:s0
```

So the minimum vendor ask is one of:

```text
1. sign our bridge with the same platform/vendor key and install it as a
   platform/system app that can write Tiny2C sysfs nodes and use MANAGE_USB;
2. ship a tiny vendor-signed bridge service that exposes thermal frames or safe
   start/stop/read-frame methods to our app;
3. add an SELinux/package policy that lets our bridge call IChangeNode and open
   the internal thermal USB device.
```

The FactoryMode race test did not work as a workaround. Even after FactoryMode
connected the thermal module, starting our normal bridge produced:

```text
ExactPro Android USB thermal device not visible after GPIO power-up
ExactPro vendor wait connected=false ctrlBlock=null
Tiny2C poll frameCount=0 rawTemp=null remapTemp=null
```

SELinux package-name shortcut check:

- `/system/etc/selinux/plat_seapp_contexts` has the normal `seinfo=platform`
  route to `platform_app`, but no special `com.energy.*` package-name rule.
- `/vendor/etc/selinux/vendor_seapp_contexts` only showed an unrelated Trustonic
  app rule.
- Therefore installing our app under an unused Energy-like package name, such as
  `com.energy.ac020`, is unlikely to gain the ThermoVue privilege domain.
