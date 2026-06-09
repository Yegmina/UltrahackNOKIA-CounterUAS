# ThermoVue Pro Reverse-Engineering Notes

Date: 2026-06-09

Scope: benign interoperability analysis for reading the Ulefone Armor 28 Ultra Thermal sensor feed for our Counter-UAV prototype. This note avoids copying vendor source or native binaries into the project.

## Executive Summary

ThermoVue Pro does not expose the thermal module through normal Android Camera2, SensorManager, or a stable public API. It is a preinstalled system app that powers/muxes an internal Tiny2C USB thermal module, then reads a vendor UVC/native stream through the `com.energy.*` SDK stack.

The strongest path to a real sensor feed is not Android Camera2. It is one of:

- vendor SDK access for `com.energy.dualmodule.sdk` / `com.energy.ac020library`;
- a privileged/root Android bridge that powers the Tiny2C USB module and reads its UVC packet stream;
- a temporary instrumentation hook on ThermoVue's Java frame callback to forward frames to our own app/Jetson.

## Packages

ThermoVue Pro:

- Package: `com.energy.tc2c`
- Label: ThermoVue Pro
- System path: `/system/app/M190infisens/M190infisens.apk`
- Version: `1.1.0`, versionCode `25062614`
- Main preview activity observed: `com.energy.dualmodule.ui.div.NewHomeActivity`
- Launcher activity: `com.energy.usbCamera.ui.splash.SplashActivity`
- Application class: `com.energy.usbCamera.MyApplication`

Factory / calibration tool:

- Package: `com.energy.tc2c.sop`
- System path: `/system/app/M190infDlp/M190infDlp.apk`
- Version: `1.0.0`, versionCode `250512`
- Launcher activity: `com.energy.tc2c.sop.ui.activity.MainActivity`

Both APKs include the same important vendor libraries/classes. The DLP package is useful because its calibration code is less UI-heavy and clearly shows frame and temperature handling.

## Permissions And Privilege Clues

ThermoVue Pro is a system app and has privileged/sensitive permissions granted, including:

- `android.permission.CAMERA`
- `android.permission.RECORD_AUDIO`
- `android.permission.MANAGE_EXTERNAL_STORAGE`
- `android.permission.READ_PRIVILEGED_PHONE_STATE`
- USB accessory declarations

Shell could not read these module control nodes:

- `/sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode`
- `/sys/class/yft_extcon/tiny2c_mode`
- `/sys/devices/platform/yft_tiny2c_usb/sensor_id`

The APK contains `GPIOUtils`, which writes to:

- `/sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode`
- `/sys/class/yft_extcon/tiny2c_mode`

That is probably the power/mux step that makes the internal thermal USB device appear. A normal Play Store-style app should not be expected to write those paths.

## Runtime USB Evidence

After launching ThermoVue, Android's USB host manager reported an internal device:

- Device address: `/dev/bus/usb/001/002`
- Vendor ID: `13428` decimal = `0x3474`
- Product ID: `17185` decimal = `0x4321`
- Manufacturer: `Thermal Cam Co.,Ltd`
- Product: `Camera`
- Serial: `202206223`

Before ThermoVue starts, this internal USB camera is not reliably visible to a normal app. After ThermoVue starts, the device node appears, but ThermoVue owns the stream.

## 2026-06-08 Connected Phone Access Tests

Device under test:

- Ulefone Armor 28 Ultra Thermal
- ADB serial: `5011AF1010013479`
- Android SDK: 35

ThermoVue is installed as a system app:

```text
codePath=/system/app/M190infisens
versionName=1.1.0
signatures=PackageSignatures{..., signatures:[b9e56080], ...}
pkgFlags=[ SYSTEM HAS_CODE ALLOW_CLEAR_USER_DATA ALLOW_BACKUP LARGE_HEAP ]
appId=10080
```

When ThermoVue is running, its process context is:

```text
u:r:platform_app:s0:c512,c768  u0_a80  ...  com.energy.tc2c
```

This matters because our side-loaded probes run outside that platform-app SELinux domain.

ADB root is not available:

```text
adbd cannot run as root in production builds
/system/bin/sh: su: inaccessible or not found
```

The app is not debuggable, so `run-as` is also unavailable:

```text
run-as: package not debuggable: com.energy.tc2c
```

Direct instrumentation into ThermoVue was tested with `prototype/android_thermovue_instrumentation_probe`. The APK builds and installs, but Android refuses to run it against ThermoVue because it is not signed with the same key:

```text
Permission Denial: starting instrumentation ... not allowed because package
com.yegmina.thermovueinstrumentationprobe does not have a signature matching
the target com.energy.tc2c
```

So normal debug instrumentation cannot hook ThermoVue on this production phone.

The bridge probe can load many ThermoVue Java classes and native libraries, but it still cannot perform the privileged power step. Calling ThermoVue's `GPIOUtils.powerUpControl()` from our app fails internally with `EACCES` on:

```text
/sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode
/sys/class/yft_extcon/tiny2c_mode
```

When ThermoVue itself powers the module, the device nodes are:

```text
crw-rw---- 1 root  usb    u:object_r:usb_device:s0  189, 1 /dev/bus/usb/001/002
crw-rw---- 1 media system u:object_r:video_device:s0 81, 137 /dev/video0
crw-rw---- 1 media system u:object_r:video_device:s0 81, 138 /dev/video1
```

The USB node is visible only after ThermoVue powers the module. Shell cannot inspect enough sysfs metadata to confirm a usable public V4L2 path, and Android Camera2 still does not advertise a thermal camera.

## 2026-06-09 Direct Vendor-Control Tests

The latest APK tests reached further into the vendor path but still did not
produce raw frames in a side-loaded app.

Confirmed working:

- ThermoVue itself streams real thermal packets. Logcat repeatedly shows
  `UvcNativeCamDualFusionPreviewManager$3.onFrame(...)` and
  `AC020library ... frame_callback memcpy: total_length=4863232`.
- Android shell can grant the already-attached thermal USB device to our app
  using `IUsbManager.grantDevicePermission(...)`.
- Our app can open the real USB device through the vendor
  `com.energy.iruvccamera.usb.USBMonitor.openDevice(...)`.
- The resulting `USBMonitor.UsbControlBlock` reports:

  ```text
  device=/dev/bus/usb/001/002
  vendor=0x3474
  product=0x4321
  manufacturer=Thermal Cam Co.,Ltd
  product=Camera
  serial=202206223
  ```

- The direct native path can build `IrcamEngine`, receive `initHandle`
  success, install an `IIrFrameCallback`, and call `startVideoStream`.

Confirmed blockers:

- With ThermoVue foreground, ThermoVue continues receiving frames but our
  process gets no `IIrFrameCallback` frames and no `Tiny2CDualFusionProxy`
  `rawTemp`/`remapTemp` buffers.
- When ThermoVue is force-stopped during a controlled takeover test, our app
  sees:

  ```text
  usbDeviceCount=0
  CTRLBLOCK proxy initHandleEngine(true) OK result=java.lang.Boolean:false
  frameCount=0 rawTemp=null remapTemp=null
  ```

  This means ThermoVue is not only consuming the stream; it also keeps the
  internal Tiny2C USB module powered/enumerated.
- `adb root` is unavailable, `su` is missing, SELinux is enforcing,
  `run-as com.energy.tc2c` is blocked because ThermoVue is not debuggable, and
  `cmd activity attach-agent com.energy.tc2c ...` fails with:

  ```text
  java.lang.SecurityException: Process not debuggable: com.energy.tc2c
  ```

One unsafe exploratory fallback caused a native crash in `libircmd020.so` after
calling unrelated vendor control methods. The controlled APK path now avoids
that broad reflection pass and only calls explainable startup methods.

Practical conclusion: a normal side-loaded APK can reach USB permission and
vendor object creation, but cannot keep the Tiny2C module alive or take over
the active frame stream on this production phone. Raw thermal access requires a
vendor-supported bridge, privileged/platform install, root, or an in-process
hook inside ThermoVue.

### 2026-06-09 Exact ThermoVue Pro Startup Clone

The bridge APK now has a `privileged-exact-startup` path that mirrors the
decompiled ThermoVue Pro `StartPreviewTask` order:

```text
USBMonitorManager.init/registerMonitor
Tiny2CDualFusionProxy.startRestartTimer
GPIOUtils.powerUpControl / Tiny2C sysfs writes
wait up to 9000 ms for USBMonitorManager.isDeviceConnected
MImageUtils.MRun3(MNN_SR_TINY2C)
MImageUtils.initMNNModelModule(...)
MImageUtils.MRun1(MNN_SR_TINY2C, 4, 2, 5)
Tiny2CDualFusionProxy.initData()
Tiny2CDualFusionProxy.initHandleEngine(ctrlBlock, true)
poll getFrameCount/getRawTempData/getRemapTempData
```

Connected-phone result as a side-loaded APK:

```text
self uid=10305 package=com.yegmina.thermovuebridgeprobe
context=u:r:untrusted_app:s0:...
AUDIT_SYSFS tiny2c_usb_mode ... EACCES
AUDIT_SYSFS tiny2c_mode ... EACCES
sysfsWrite FAIL ... tiny2c_usb_mode ... EACCES
sysfsWrite FAIL ... tiny2c_mode ... EACCES
ExactPro Android USB thermal device not visible after GPIO power-up
ExactPro vendorUsbConnected=false ctrlBlock=null
MNNModelModule MRun3 result=-4
MNNModelModule init result=-3
MNNModelModule MRun1 result=-4
ExactPro initHandleEngine skipped because ctrlBlock=null
ExactPro direct initHandle frameSeen=false
ExactPro worker StartPreviewTask frameSeen=false
DeviceControl explicit startPreview frameSeen=false
```

This is stronger than the earlier broad reflection probes: the startup order is
now aligned with ThermoVue, and the remaining standalone bridge blocker is the
app privilege domain needed to power/mux the internal Tiny2C USB module.

### 2026-06-09 Frida/In-Process Hook Test

The Frida bridge now targets the exact Android PID from `pidof com.energy.tc2c`
before falling back to fuzzy process-name matching. The connected phone still
blocks attach:

```text
ro.debuggable: 0
shell id: ... context=u:r:shell:s0
su: unavailable or not root
Using Android pidof target: com.energy.tc2c pid=12226
Attaching Frida to com.energy.tc2c pid=12226...
Bridge could not attach/run: unable to connect to remote frida-server: closed
```

The hook code remains the best fast proof path if a rooted/debuggable device,
engineering image, Frida Gadget build, or vendor-approved instrumentation route
becomes available.

### USB Permission Handler Test

The bridge probe now registers as a static USB handler for the thermal device:

```text
vendor_id=13428
product_id=17185
```

`dumpsys usb` confirms Android sees the handler:

```text
package_name=com.yegmina.thermovuebridgeprobe
class_name=com.yegmina.thermovuebridgeprobe.MainActivity
filters={ vendor_id=13428 product_id=17185 ... }
```

This does not grant access on the production phone. The framework exposes:

```text
UsbManager method grantPermission(android.hardware.usb.UsbDevice,java.lang.String)
```

but calling it from the side-loaded bridge fails:

```text
java.lang.SecurityException: Access denied, requires: android.permission.MANAGE_USB
```

`pm grant` cannot grant `MANAGE_USB` either:

```text
Permission android.permission.MANAGE_USB ... is not a changeable permission type
```

The normal USB permission dialog also fails in practice. SystemUI starts `UsbPermissionActivity`, but the internal thermal USB device is removed while the dialog is up, then Android sends our pending intent with:

```text
EXTRA_PERMISSION_GRANTED=false
```

This likely happens because ThermoVue is paused/interrupted by the permission UI and powers down or cycles the internal module. Static USB filters therefore do not solve the side-loaded bridge case.

### OEM USB Framework Finding

Decompiling `services.jar` shows why static USB handler registration cannot work for this module on stock firmware:

```text
UsbProfileGroupSettingsManager.resolveActivity(...)
if (usbDevice != null && usbDevice.getProductId() == 17185) {
    Log.d(TAG, "yft ignore YF USB attach notification ---");
    return;
}
```

`17185` decimal is `0x4321`, the thermal module product ID. That branch returns before Android grants permission to the matched USB activity.

There is a separate fixed-handler path:

```text
UsbHostManager:
if (mUsbDeviceConnectionHandler == null) {
    currentSettings.deviceAttached(device);
} else {
    currentSettings.deviceAttachedForFixedHandler(device, mUsbDeviceConnectionHandler);
}

UsbProfileGroupSettingsManager.deviceAttachedForFixedHandler(...):
grantDevicePermission(usbDevice, applicationInfo.uid)
startActivityAsUser(intent.setComponent(componentName), ...)
```

This fixed-handler path grants USB permission before starting the component and bypasses the product-ID ignore branch. `UsbService.setUsbDeviceConnectionHandler(ComponentName)` exists, but it is protected by `MANAGE_USB`. The bridge now attempts this in privileged mode; from a side-loaded app it cannot complete.

Practical ask for Ulefone/InfiSense: either sign/privilege the bridge so it can call `setUsbDeviceConnectionHandler(...)`, or set the default/fixed USB host connection handler in firmware to:

```text
com.yegmina.thermovuebridgeprobe/.MainActivity
```

The bridge uses `singleTop` and logs USB attach intents, so this path should immediately show whether Android granted the internal thermal USB device to our process.

### Privileged Bridge Candidate

`prototype/android_thermovue_bridge_probe` now has a dedicated privileged mode:

```text
adb shell am start -n com.yegmina.thermovuebridgeprobe/.MainActivity --ez privileged true
```

This mode does not launch ThermoVue. It tries to behave like the future vendor-signed bridge:

1. load ThermoVue SDK classes and native libraries;
2. initialize MMKV, Blankj Utils, and the vendor application singletons;
3. write the Tiny2C power sysfs nodes directly;
4. register itself as the fixed USB host connection handler if framework privilege allows it;
5. call ThermoVue `GPIOUtils.powerUpControl()`;
6. wait for USB VID/PID `0x3474:0x4321`;
7. call the framework USB grant method if available;
8. start the vendor Tiny2C preview path;
9. dump `raw_temp_*.bin` / `remap_temp_*.bin` if thermal bytes appear;
10. optionally send raw thermal frames over UDP with `--es jetsonHost <ip> --ei jetsonPort 25000`.

On the stock side-loaded build this now fails at the precise expected gate:

```text
sysfsWrite FAIL path=/sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode value=1 ... EACCES
sysfsWrite FAIL path=/sys/class/yft_extcon/tiny2c_mode value=1 ... EACCES
waitForThermalUsb timeout afterMs=10000
privileged bridge FAIL thermal USB did not appear after power-up
Tiny2C poll 0 frameCount=0 ... rawTemp=null remapTemp=null
```

The matching receiver is:

```text
py -3 prototype\thermal_udp_receiver.py --host 0.0.0.0 --port 25000 --save-dir prototype\data\raw\thermal_udp
```

## Key Java Classes

Primary app:

- `com.energy.dualmodule.sdk.Tiny2CDualFusionProxy`
- `com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager`
- `com.energy.dualmodule.sdk.uvc.USBMonitorManager`
- `com.energy.dualmodule.sdk.uvc.DeviceIrcmdControlManager`
- `com.energy.ac020library.IrcamEngine`
- `com.energy.ac020library.IrcmdEngine`
- `com.energy.ac020library.IrcamEngineBuilder`
- `com.energy.ac020library.bean.DualUvcHandleParam`
- `com.energy.ac020library.bean.IIrFrameCallback`
- `com.energy.iruvccamera.usb.USBMonitor`
- `com.energy.irutilslibrary.LibIRTemp`
- `com.energy.irutilslibrary.LibIRProcess`
- `com.energy.irutilslibrary.LibIRParse`
- `com.energy.ac020library.dual.DualGpuApi`

Factory app:

- `com.energy.tc2c.sop.camera.UvcNativeCamDualCalManager`
- `com.energy.tc2c.sop.camera.UvcNativeCamDualCalManager$mIIrFrameCallback$1`
- `com.energy.tc2c.sop.camera.UvcNativeCamDualCalManager$OnDualTempListener`
- `com.energy.tc2c.sop.camera.UvcNativeCamDualCalManager$OnDualCalibListener`
- `com.energy.tc2c.sop.ui.fragment.DualPreviewFragment`

## Native Libraries

Important native libraries packaged in ThermoVue include:

- `libAC020sdk.so`
- `libdualuvccamera020.so`
- `libusbuvccamera020.so`
- `libomniircamera020.so`
- `libircmd020.so`
- `libircam.so`
- `libirutilssdk.so`
- `libadvirparse.so`
- `libadvirprocess.so`
- `libadvirtemp.so`
- `libadvirtempac020.so`
- `libdualcommon.so`
- `libdualcalibration.so`
- `libgpudual.so`
- `libMNN.so`
- `libopencv_java4.so`

`IrcamEngine` calls `System.loadLibrary("AC020sdk")`.

## Startup Flow

The preview path is approximately:

1. `Tiny2cDualPreviewFragment` calls:

   ```text
   Tiny2CDualFusionProxy.init(context, 256, 386, 1.0f, 25, vlCameraId, 1440, 1080, 25)
   ```

2. `Tiny2CDualFusionProxy` delegates to `UvcNativeCamDualFusionPreviewManager`.

3. `USBMonitorManager` observes the internal USB camera and requests permission/open control block.

4. `UvcNativeCamDualFusionPreviewManager.initHandleEngine(UsbControlBlock, boolean)` builds:

   ```text
   DualUvcHandleParam
   IrcamEngineBuilder
   DriverType.USB_DUAL_NATIVE_CAM
   IrcamEngine
   ```

5. `IrcamEngine.initHandle(...)` creates/returns an `IrcmdEngine`.

6. `startPreview()` calls:

   ```text
   IrcamEngine.setIrFrameCallback(mIrFrameCallback)
   IrcamEngine.startVideoStream()
   ```

7. Native `AC020library` calls Java:

   ```text
   IIrFrameCallback.onFrame(byte[] frame, int length)
   ```

## Frame Layout

ThermoVue logcat repeatedly emitted:

```text
AC020library ircam_engine.cpp frame_callback memcpy: total_length=4863232
UvcNativeCamDualFusionPreviewManager$3.onFrame(...)
```

The byte count matches the decompiled buffer math exactly:

| Segment | Formula | Bytes |
| --- | ---: | ---: |
| IR image plane | `256 * 192 * 2` | `98,304` |
| Info/telemetry lines | `256 * 2 * 2` | `1,024` |
| Temperature plane | `256 * 192 * 2` | `98,304` |
| Visible frame | `1440 * 1080 * 3` | `4,665,600` |
| Total | sum | `4,863,232` |

Likely packet offsets:

```python
IR_W = 256
IR_H = 192
INFO_LINES = 2
VL_W = 1440
VL_H = 1080

IR_BYTES = IR_W * IR_H * 2
INFO_BYTES = IR_W * INFO_LINES * 2
TEMP_BYTES = IR_W * IR_H * 2
VL_BYTES = VL_W * VL_H * 3

ir = packet[0:IR_BYTES]
info = packet[IR_BYTES:IR_BYTES + INFO_BYTES]
temp = packet[IR_BYTES + INFO_BYTES:IR_BYTES + INFO_BYTES + TEMP_BYTES]
visible = packet[IR_BYTES + INFO_BYTES + TEMP_BYTES:]

assert len(packet) == IR_BYTES + INFO_BYTES + TEMP_BYTES + VL_BYTES
```

ThermoVue then derives:

- `mIrData`
- `mInfoData`
- `mTempData`
- `mTempCompensationData`
- `mNormalTempData`
- `mNormalTempRemapData`
- `mDualNormalData`
- fused/visible preview buffers through `DualGpuApi`

`Tiny2CDualFusionProxy` exposes useful methods internally:

- `getRawTempData(): byte[]`
- `getRemapTempData(): byte[]`
- `getFusionData(byte[])`
- `getPreviewWidth()`
- `getPreviewHeight()`

These are not exported as an Android service/API for third-party apps.

## Why Our Public Android Probe Did Not Work

Our probe app saw normal phone cameras and sensors, but no public thermal camera. This matches the ThermoVue architecture:

- the thermal module is behind a vendor Tiny2C USB/sysfs power path;
- Android shell and normal apps cannot read/write the relevant sysfs nodes;
- ThermoVue opens the internal USB camera through its own `USBMonitor` and native `AC020sdk`;
- frames arrive through a vendor JNI callback, not Camera2.

## Current Bridge Strategy

The active non-root prototype keeps ThermoVue in the foreground so it powers the
internal Tiny2C thermal USB module, then uses the Android shell UID to grant our
bridge app permission to the already-attached USB device.

Important Android framework finding:

```text
UsbProfileGroupSettingsManager.resolveActivity(...)
  if productId == 17185:
    "yft ignore YF USB attach notification"
    return
```

That means normal USB attach activity matching is intentionally skipped for the
thermal module. The fixed-handler framework path can bypass this, but launching
our activity as the fixed handler steals foreground from ThermoVue and the module
detaches quickly. The better test path is:

```text
clear fixed USB handler
start bridge watcher in background/behind ThermoVue
launch ThermoVue
wait for VID 0x3474 / PID 0x4321
shell helper calls IUsbManager.grantDevicePermission(device, bridgeUid)
bridge initializes vendor USBMonitorManager and Tiny2CDualFusionProxy
```

The bridge now also has a no-display fixed-handler component:

```text
com.yegmina.thermovuebridgeprobe/.HeadlessUsbAttachActivity
```

This component is intended only to receive Android's package-level USB permission
grant and immediately finish. A separate watcher bridge can remain running and
observe whether the permission appeared without interrupting ThermoVue's preview
lifecycle.

2026-06-09 privilege audit from `Thermal Live Debug`:

```text
self context:
  u:r:untrusted_app:s0:c59,c257,c512,c768

ThermoVue Pro package:
  /system/app/M190infisens/M190infisens.apk
  system app, runtime process observed as u:r:platform_app:s0:c512,c768

Tiny2C sysfs paths from normal side-loaded app:
  /sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode  exists, EACCES
  /sys/class/yft_extcon/tiny2c_mode                    exists, EACCES
  /sys/devices/platform/yft_tiny2c_usb/sensor_id       exists, EACCES

USB visibility:
  without ThermoVue powering module: usbDeviceCount=0
  with ThermoVue foreground: /dev/bus/usb/001/002 VID=0x3474 PID=0x4321
```

The audit also proves the side-loaded APK can load the important Pro and SOP
classes through `DexClassLoader`; the remaining blocker is privilege over the
Tiny2C sysfs power/mux path and live USB stream ownership, not class discovery.

JADX-confirmed vendor startup sequence:

```text
StartPreviewTask.run(MODE_DUAL_FUSION)
  Tiny2CDualFusionProxy.startRestartTimer()
  GPIOUtils.powerUpControl()
  wait until USBMonitorManager.isDeviceConnected()
  MImageUtils model init
  Tiny2CDualFusionProxy.initData()
  Tiny2CDualFusionProxy.initHandleEngine(USBMonitorManager.getCtrlBlock(), true)

UvcNativeCamDualFusionPreviewManager.initHandleEngine(ctrlBlock, true)
  DualUvcHandleParam.setCtrlBlock(ctrlBlock)
  IrcamEngine.Builder()
    .setStreamWidth(256)
    .setStreamHeight(192)
    .setDriverType(USB_DUAL_NATIVE_CAM)
    .setDualUvcHandleParam(...)
    .build()
  IrcamEngine.initHandle(callback)
  callback.onSuccess(...)
    handleDualCalFileRead()
    TempCompensation.getNucTData()
    startPreview()

UvcNativeCamDualFusionPreviewManager.startPreview()
  IrcamEngine.setIrFrameCallback(mIrFrameCallback)
  IrcamEngine.startVideoStream()
  startFrameDataCheck()
```

The bridge now tries the vendor worker path first, polls frame counters/temp
buffers, and only then falls back to explicit `initData`, `initHandleEngine`, and
`startPreview` calls. When `keepStreaming` is enabled and frames appear, it sends
raw temp frames to the Jetson/laptop over UDP using the
`YEGMINA_THERMAL_RAW_V1` chunk protocol consumed by `thermal_udp_receiver.py`.

Second experimental path:

```text
ThermoVueShellBridge via app_process
  runs as UID 2000 / com.android.shell context
  loads ThermoVue APK classes with DexClassLoader
  extracts ThermoVue native libraries to /data/local/tmp
  bootstraps RXBaseApplication paths under /data/local/tmp
  grants thermal USB to UID 2000
  calls the same USBMonitorManager + Tiny2CDualFusionProxy path
```

This avoids launching our Activity and may avoid foreground/lifecycle conflicts
with ThermoVue. It still needs ThermoVue or a privileged actor to power the Tiny2C
module first.

## Public Research Hints

Public information found so far matches the local reverse-engineering result:

- Ulefone markets the phone as using ThermoVue T2 with 25 Hz thermal imaging and
  AI/sharpened output rather than a normal Android camera stream:
  <https://www.ulefone.com/products/armor-28-ultra-thermal-version>
- Another developer reported the same public-API symptom: the Armor 28 Ultra
  thermal camera does not appear in Camera2, and no public answer pointed to a
  standard Android API:
  <https://stackoverflow.com/questions/79608212/how-to-access-thermal-camera-data-on-ulefone-armor-28-ultra-in-a-custom-android>
- A public Tiny2C module specification lists a 256x192 VOx detector, 25 Hz frame
  rate, USB video interface, and USB/IIC/UART control options. This is consistent
  with our local 256x192 raw plane and internal USB VID/PID path:
  <https://ae-pic-a1.aliexpress-media.com/kf/S71f62d8424a4474194f4099e48f020bea.pdf>
- Jetson/Linux Python packages for external USB thermal cameras exist, but they
  assume the thermal camera is directly visible on USB with host permissions.
  They do not solve the phone-side Android permission/power-mux problem:
  <https://pypi.org/project/pythermal/0.2.2/> and
  <https://pypi.org/project/tiny-thermal-camera/>

## Practical Next Steps

Best clean route:

1. Ask Ulefone/InfiSense for the SDK/API that contains:
   - `com.energy.dualmodule.sdk`
   - `com.energy.ac020library`
   - `Tiny2CDualFusionProxy`
   - `IrcamEngine`
   - `IIrFrameCallback`
2. Ask specifically for either:
   - a signed/platform bridge APK template that can run in the same privilege class as ThermoVue; or
   - permission and instructions to install our own app as a privileged system app during the hackathon.
3. Required bridge privileges seen so far:
   - `android.permission.MANAGE_USB` to call `UsbManager.grantPermission(...)`;
   - a platform/system app SELinux domain comparable to ThermoVue's `u:r:platform_app:s0` so `GPIOUtils`/Tiny2C sysfs power control can work.

Fastest technical proof route:

1. Use Frida/Xposed/root/platform-signed instrumentation to hook:

   ```text
   com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager$3.onFrame(byte[], int)
   ```

2. Forward only our own captured frame bytes to a local socket/UDP stream.
3. Parse packet offsets above.
4. Send `temp` plus optional `visible` frames to Jetson.

Most robust product route:

1. Build a privileged/system Android bridge or rooted prototype.
2. Toggle Tiny2C module power/mux via the sysfs paths.
3. Open USB VID/PID `0x3474:0x4321`.
4. Read UVC frames with libusb/libuvc or a small native Android service.
5. Parse the combined packet layout.
6. Stream compact data to Jetson Orin Nano:
   - `temp`: 256x192x16-bit, about 98 KB/frame;
   - optional visible RGB/YUV frame;
   - metadata line for FFC/frame counters/status.

Risky/less likely route:

- A normal app loading ThermoVue's APK classes with `PathClassLoader`. Android native linker namespaces, app-private libraries, sysfs permissions, and USB ownership make this unlikely to work without privileged install/root.
- Plain Android instrumentation from a debug-signed APK. This is now tested and blocked by target-package signature mismatch.

## Counter-UAV Prototype Implication

For the hackathon, the fastest reliable demo remains:

- Phone runs ThermoVue or a privileged bridge.
- Thermal packets are forwarded to Jetson.
- Jetson runs detection/fusion across RGB, thermal/IR, and audio.

If we can hook or bridge `onFrame`, we can avoid screen scraping and get true per-pixel thermal data at about 25 fps.
