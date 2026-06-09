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
4. Tap `Power Try` to try direct sysfs and vendor GPIO power paths.
5. Tap `Request USB` if USB VID `0x3474`, PID `0x4321` appears.
6. Tap `Launch TVue`, wait for ThermoVue to open, then return to this app.
7. Tap `Start SDK`.

If live thermal frames are available, the preview panel will show the real
256x192 thermal value matrix rendered with a color palette, plus min/max/mean/FPS
status. If not, the log panel should show where the path failed: USB visibility,
permission, ThermoVue package loading, native init, preview start, or frame
polling.

`Share Log` sends the visible debug log text through Android's share sheet.

## Build

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\build_thermal_live_debug.ps1
```
