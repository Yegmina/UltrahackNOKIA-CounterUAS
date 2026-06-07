# Thermal Stream Test

This is the fastest test for live thermal imagery from the Ulefone Armor 28 Ultra Thermal.

## Setup

1. Enable Developer options on the phone.
2. Enable USB debugging.
3. Connect the phone by USB.
4. Unlock the phone and accept the "Allow USB debugging" prompt.
5. Open the Ulefone thermal camera app manually if package auto-launch does not work.

## Run Probe

From the project root:

```powershell
& "C:\Users\teres\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" prototype\thermal_stream_test.py probe
```

The probe writes logs under `prototype/logs/` and prints any package names that look thermal-related.

## Stream The Thermal App Screen

Open the thermal camera app on the phone, then run:

```powershell
& "C:\Users\teres\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" prototype\thermal_stream_test.py screen-stream --hotspots
```

This captures the phone screen over ADB. If the thermal app is showing live thermal imagery, the Python window becomes a live thermal stream.

## Optional Crop

If the phone UI overlays are noisy, crop to the thermal image area:

```powershell
& "C:\Users\teres\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" prototype\thermal_stream_test.py screen-stream --hotspots --crop 0,200,1080,1600
```

Crop format is:

```text
x,y,width,height
```

## Launch A Candidate Package

After `probe` prints a likely package:

```powershell
& "C:\Users\teres\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" prototype\thermal_stream_test.py launch --package PACKAGE_NAME
```

Or launch and stream in one command:

```powershell
& "C:\Users\teres\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" prototype\thermal_stream_test.py screen-stream --launch PACKAGE_NAME --hotspots
```

## Notes

- This is not raw thermal sensor access yet; it is live screen capture of the thermal app.
- That is still useful for the hackathon because it lets us test latency, hotspot extraction, and fusion logic immediately.
- Raw thermal access depends on how Ulefone exposes the thermal module to Android apps.

