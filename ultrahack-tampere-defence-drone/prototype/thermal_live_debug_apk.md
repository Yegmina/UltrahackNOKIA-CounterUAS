# Thermal Live Debug APK

APK:

```text
prototype/android_thermal_live_debug/build/thermal-live-debug.apk
```

Purpose: run entirely on the phone and test whether a side-loaded app can get
live thermal frames into its own UI.

## Button Flow

1. Install and open `Thermal Live Debug`.
2. Tap `Self Test` to confirm the preview renderer works.
3. Tap `Scan` to list Camera2 IDs, USB devices, and ThermoVue package status.
4. Tap `USB Probe` to log USB descriptors, interfaces, endpoints, and short
   read attempts from readable IN endpoints.
5. Tap `Power Try` to try direct sysfs and vendor GPIO power paths.
6. Tap `Request USB` if USB VID/PID `0x3474:0x4321` or `0x0ecb:0x20f6`
   appears.
7. Tap `Launch TVue`, wait for ThermoVue to open, then return to this app.
8. Tap `Start SDK`.

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
- multiple visible camera IDs (`0`, `1`, `2`, `3`, and empty);
- `UvcNativeCamDualDeviceControlManager.handleStartPreview(...)`;
- `Tiny2CDualFusionProxy.handleStartPreview(...)`;
- explicit `initHandleEngine(ctrlBlock, true)` plus `startPreview()`;
- targeted method/field dumps for preview, frame, callback, calibration, and
  temperature APIs.

Wait until it prints `native clone autotest finished`, then tap `Share Log`.

If live thermal frames are available, the preview panel will show the real
256x192 thermal value matrix rendered with a color palette, plus min/max/mean/FPS
status. If not, the log panel should show where the path failed: USB visibility,
permission, ThermoVue package loading, native init, preview start, or frame
polling.

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
