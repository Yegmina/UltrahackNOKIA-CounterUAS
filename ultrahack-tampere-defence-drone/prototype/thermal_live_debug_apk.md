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
`/latest.png`.

## Button Flow

1. Install and open `Thermal Live Debug`.
2. Tap `Self Test` to confirm the preview renderer works.
3. Tap `Scan` to list Camera2 IDs, USB devices, and ThermoVue package status.
4. Tap `USB Probe` to log USB descriptors, interfaces, endpoints, and short
   read attempts from readable IN endpoints.
   If bytes are read, the app also saves them under the current debug session's
   `usb_probe/` folder so they can be pulled later with the MTP helper.
5. Tap `Power Try` to try direct sysfs and vendor GPIO power paths.
6. Tap `Request USB` if USB VID/PID `0x3474:0x4321` or `0x0ecb:0x20f6`
   appears.
7. Tap `Launch TVue`, wait for ThermoVue to open, then return to this app.
8. Tap `Start SDK`.
9. Tap `Engine Probe` for a focused direct-native attempt around
   `IrcamEngine`, `IrcamEngineBuilder`, and `DualUvcHandleParam`.
   Current builds also create a hidden `SurfaceTexture`/`Surface` and
   reflectively attach it to vendor preview objects before starting the native
   preview path.
10. If ADB is unavailable, use the HTTP URL printed in the log to fetch results
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

`Share Log` sends the visible debug log text through Android's share sheet.

## Build

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\build_thermal_live_debug.ps1
```

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
logs and any `usb_probe/*.bin` endpoint captures.

Current Windows observation for the Ulefone connection:

- MTP works and exposes `Armor 28 Ultra` shared storage.
- The connected USB mode is `VID_0E8D&PID_201D`.
- Windows shows `USB\VID_0E8D&PID_201D&MI_01` as a generic `WinUsb Device /
  ADB Interface`, but `adb devices` still returns an empty list.
- The registry also contains an older Android ADB interface entry for
  `VID_0E8D&PID_201C`, so if ADB is needed, try changing the phone USB mode
  from file-transfer/MTP to another debugging-capable mode and reconnecting.
