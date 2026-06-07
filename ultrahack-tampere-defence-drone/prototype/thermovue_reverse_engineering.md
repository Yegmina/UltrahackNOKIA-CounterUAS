# ThermoVue Pro Reverse-Engineering Notes

Date: 2026-06-07

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

## Practical Next Steps

Best clean route:

1. Ask Ulefone/InfiSense for the SDK/API that contains:
   - `com.energy.dualmodule.sdk`
   - `com.energy.ac020library`
   - `Tiny2CDualFusionProxy`
   - `IrcamEngine`
   - `IIrFrameCallback`

Fastest technical proof route:

1. Use Frida/Xposed/root instrumentation to hook:

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

## Counter-UAV Prototype Implication

For the hackathon, the fastest reliable demo remains:

- Phone runs ThermoVue or a privileged bridge.
- Thermal packets are forwarded to Jetson.
- Jetson runs detection/fusion across RGB, thermal/IR, and audio.

If we can hook or bridge `onFrame`, we can avoid screen scraping and get true per-pixel thermal data at about 25 fps.
