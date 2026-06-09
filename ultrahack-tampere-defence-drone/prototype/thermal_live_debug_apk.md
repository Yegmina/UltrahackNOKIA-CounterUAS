# Thermal Live Debug APK

APK:

```text
prototype/android_thermal_live_debug/build/thermal-live-debug.apk
```

Purpose: run entirely on the phone and test whether a side-loaded app can get
live thermal frames into its own UI.

The app also starts a small HTTP debug server on port `8088`. This is important
when USB/ADB is unreliable: open the URL shown in the app log/status from the
laptop browser to read `/log`, `/status`, `/latest.raw`, `/latest.pgm`, and
`/latest.png`. Use `/dump-vendor` to start the ThermoVue APK/native-library dump
from the laptop if the app is already open and reachable. Use `/priv-audit` for
the current Tiny2C privilege/clone diagnostic.

## Button Flow

1. Install and open `Thermal Live Debug`.
2. Tap `Self Test` to confirm the preview renderer works.
3. Tap `Scan` to list Camera2 IDs, USB devices, and ThermoVue package status.
4. Tap `USB Probe` to log USB descriptors, interfaces, endpoints, and short
   read attempts from readable IN endpoints.
   If bytes are read, the app also saves them under the current debug session's
   `usb_probe/` folder so they can be pulled later with the MTP helper.
5. Tap `Dump APKs` to copy ThermoVue Pro and ThermoVue SOP APK/native-library
   artifacts into the current debug session's `vendor_dump/` folder. This is
   the non-ADB path for decompiling the real system app on the laptop.
6. Tap `Priv Audit` to log app SELinux context, ThermoVue package privilege,
   Tiny2C sysfs access, USB visibility, and Pro/SOP clone entry classes.
7. Tap `Power Try` to try direct sysfs and vendor GPIO power paths.
8. Tap `Request USB` if USB VID/PID `0x3474:0x4321` or `0x0ecb:0x20f6`
   appears.
9. Tap `Launch TVue`, wait for ThermoVue to open, then return to this app.
10. Tap `Start SDK`.
11. Tap `Engine Probe` for a focused direct-native attempt around
   `IrcamEngine`, `IrcamEngineBuilder`, and `DualUvcHandleParam`.
   Current builds also create a hidden `SurfaceTexture`/`Surface` and
   reflectively attach it to vendor preview objects before starting the native
   preview path.
12. Tap `Native CAM` to call the decompiled ThermoVue native path directly:
   `DualUvcHandleParam -> IrcamEngine.Builder -> initHandle ->
   setIrFrameCallback -> startVideoStream`.
13. Tap `CtrlBlock` to shell-grant the thermal USB device first, then create a
   real vendor `USBMonitor.UsbControlBlock` and call
   `Tiny2CDualFusionProxy.initHandleEngine(ctrlBlock, true)`.
14. Tap `ForceOpen` to skip the Android USB permission request and directly
   test whether ThermoVue's bundled `USBMonitor.openDevice(...)` can open the
   visible internal USB device without `UsbManager.hasPermission(...)`. This is
   a diagnostic only; expected stock-phone result is `openDevice` failure.
15. Tap `Takeover` for a controlled ownership-transition test. It opens the
   vendor `UsbControlBlock` while ThermoVue has powered the module, waits six
   seconds so the host can stop ThermoVue, then tries the same proxy startup.
16. If ADB is unavailable, use the HTTP URL printed in the log to fetch results
    from the laptop.

To test whether ThermoVue stops its camera/thermal stream when it loses
foreground, use `TVue FG Test` instead of `Launch TVue` + `Start SDK`. It starts
the SDK polling thread first, then launches ThermoVue on top and keeps logging
in the background. Wait 15-20 seconds, return to this app, then tap `Share Log`.

If raw SDK frames are still blocked, use `Cap TVue`. Android will ask for screen
recording permission, then the app starts a foreground capture service and
launches ThermoVue. Leave ThermoVue foreground so its thermal stream stays alive.
Return to this app and tap `Load Cap` to show the latest captured ThermoVue
screen. This is real ThermoVue display capture, not raw sensor bytes.

For native/full-clone reverse engineering, use `Native Auto`. It runs a matrix
of ThermoVue-like startup sequences:

- direct USB endpoint descriptor/read probing;
- relevant ThermoVue DEX class index logging;
- attempted `IIrFrameCallback` registration on known SDK singletons;
- private field-state dumps from live proxy, USB monitor, and preview-control
  objects;
- direct `IrcamEngine`/builder/handle-param probing with heuristic field
  population and likely init/start method calls;
- multiple visible camera IDs (`0`, `1`, `2`, `3`, and empty);
- `UvcNativeCamDualDeviceControlManager.handleStartPreview(...)`;
- `Tiny2CDualFusionProxy.handleStartPreview(...)`;
- explicit `initHandleEngine(ctrlBlock, true)` plus `startPreview()`;
- generic polling of frame/temp/raw/remap fields and zero-argument getters,
  including `byte[]`, `short[]`, `int[]`, and `float[]` frame matrices;
- targeted method/field dumps for preview, frame, callback, calibration, and
  temperature APIs.

Wait until it prints `native clone autotest finished`, then tap `Share Log`.

If you only have time for one native-clone diagnostic, run `Engine Probe` and
share the log. It performs the same ThermoVue bootstrap, waits for a
`UsbControlBlock`, then tries the direct engine path without walking the full
camera/mode matrix.

If live thermal frames are available, the preview panel will show the real
256x192 thermal value matrix rendered with a color palette, plus min/max/mean/FPS
status. If not, the log panel should show where the path failed: USB visibility,
permission, ThermoVue package loading, native init, preview start, or frame
polling.

Laptop live viewer over Wi-Fi/HTTP:

```powershell
py -3 prototype\thermovue_sensor_live_viewer.py --source http-latest --phone-url http://PHONE_IP:8088
```

Headless one-frame check:

```powershell
py -3 prototype\thermovue_sensor_live_viewer.py --source http-latest --phone-url http://PHONE_IP:8088 --headless --frames 1
```

If the log shows `connected=true` with a non-null `UsbControlBlock`, USB access
is at least partly working. If it still shows `frameCount=0 rawTemp=null`, the
app is blocked later in the ThermoVue/Tiny2C preview engine. The APK then tries
explicit `initHandleEngine(ctrlBlock, true)` and `startPreview()` fallbacks.
If Android keeps showing the USB permission dialog, tap `Stop`. Current builds
also close ThermoVue's `USBMonitorManager` automatically after a short no-frame
timeout so the dialog should not loop forever.

## Current Direct-Thermal Result

2026-06-09 connected-phone tests reached the vendor USB/native boundary but did
not obtain frames inside our side-loaded app:

- `Priv Audit` shows this APK runs as
  `u:r:untrusted_app:s0:c59,c257,c512,c768`; ThermoVue Pro runs as a system
  package in `u:r:platform_app:s0:c512,c768`.
- The Tiny2C sysfs nodes used by ThermoVue exist but are not readable/writable
  from the side-loaded app:
  `/sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode`,
  `/sys/class/yft_extcon/tiny2c_mode`, and
  `/sys/devices/platform/yft_tiny2c_usb/sensor_id`.
- When ThermoVue is foreground, `Priv Audit` sees the internal thermal USB
  device `/dev/bus/usb/001/002` with VID/PID `0x3474:0x4321`; without ThermoVue
  powering the module, the normal app sees `usbDeviceCount=0`.
- The audit can load the Pro clone classes
  `Tiny2CDualFusionProxy`, `USBMonitorManager`,
  `UvcNativeCamDualDeviceControlManager`, `UvcNativeCamDualFusionPreviewManager`,
  `IrcamEngine`, and `DualUvcHandleParam`, plus the SOP classes
  `GPIOUtils`, `UvcNativeCamDualCalManager`, and `USBMonitorManager`.
- ThermoVue streaming is real: logcat shows repeated
  `UvcNativeCamDualFusionPreviewManager$3.onFrame(...)` callbacks and
  `AC020library ... total_length=4863232`.
- Shell helper can grant `/dev/bus/usb/001/002` VID/PID `0x3474:0x4321` to our
  app.
- `CtrlBlock` can create a real vendor `USBMonitor.UsbControlBlock` with
  manufacturer `Thermal Cam Co.,Ltd` and serial `202206223`.
- `Native CAM` can build `IrcamEngine`, receive `initHandle` success, install
  `IIrFrameCallback`, and call `startVideoStream`, but no frame callback fires.
- `ForceOpen` exists to verify whether the vendor `USBMonitor` can bypass
  Android's USB permission gate when ThermoVue keeps the module visible.
  Current result: it cannot. ThermoVue's bundled `USBMonitor.openDevice(...)`
  throws `java.lang.SecurityException: has no permission`.
- While ThermoVue remains foreground, it owns the active stream. When ThermoVue
  is force-stopped, the thermal USB module disappears from normal app view
  (`usbDeviceCount=0`), and `initHandleEngine(ctrlBlock, true)` returns `false`.
- `adb root`, `su`, `run-as com.energy.tc2c`, and `cmd activity attach-agent`
  are blocked on this production phone.

Conclusion: direct raw thermal frames from a normal side-loaded APK are blocked
by the Tiny2C power/USB ownership boundary. A reliable raw thermal bridge needs
one of: vendor SDK/API support, platform-signed/privileged install, root, or an
in-process hook inside ThermoVue. Screen capture remains a demo fallback only;
it is not raw sensor access.

`Share Log` sends the visible debug log text through Android's share sheet.

## Build

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\build_thermal_live_debug.ps1
```

Run the privilege/clone audit from the laptop:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\run_thermal_privilege_audit.ps1
```

Use `-NoLaunchThermoVue` to capture the no-powered-module case.

## USB Without ADB

If Windows sees the phone over USB/MTP but `adb devices` is empty, use the MTP
helper:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\mtp_phone_helper.ps1 -Action CopyApk
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\mtp_phone_helper.ps1 -Action PullLogs
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\mtp_phone_helper.ps1 -Action PullCapture
```

`CopyApk` only places the APK in the phone `Download` folder. Install/open it on
the phone, then use `PullLogs` to bring the debug sessions back to the laptop.
`PullLogs` copies the whole `thermal_live_debug_*` session folder, including
logs, `vendor_dump/` APK/native-lib exports, and any `usb_probe/*.bin` endpoint
captures.

Current Windows observation for the Ulefone connection:

- MTP works and exposes `Armor 28 Ultra` shared storage.
- The connected USB mode is `VID_0E8D&PID_201D`.
- Windows shows `USB\VID_0E8D&PID_201D&MI_01` as a generic `WinUsb Device /
  ADB Interface`, but `adb devices` still returns an empty list.
- The registry also contains an older Android ADB interface entry for
  `VID_0E8D&PID_201C`, so if ADB is needed, try changing the phone USB mode
  from file-transfer/MTP to another debugging-capable mode and reconnecting.
