# Phone Sensor Test Report

Updated: 7 June 2026

Device: Ulefone Armor 28 Ultra

ADB serial:

```text
5011AF1010013479
```

## Summary

The phone works as a practical hackathon sensor node.

Best tested live path:

```text
IP Webcam app on phone
  -> ADB tcp forward 8080
  -> Python/Jetson reads http://127.0.0.1:8080
  -> RGB frames + microphone audio
```

Best tested thermal path:

```text
Ulefone thermal app com.energy.tc2c
  -> ADB screen capture for low-FPS live proof-of-concept
  -> Android screenrecord MP4 for higher-FPS thermal datasets/fallback demos
```

## Tested Paths

### ADB Device

ADB is authorized and working.

```text
5011AF1010013479 device product:GQ5011AF1_EEA model:Armor_28_Ultra device:GQ5011AF1
```

### Thermal App

Likely thermal app package:

```text
com.energy.tc2c
```

Foreground thermal activity after launch:

```text
com.energy.tc2c/com.energy.dualmodule.ui.div.NewHomeActivity
```

Relevant permissions requested by the app include:

- `android.permission.CAMERA`
- `android.permission.RECORD_AUDIO`
- `android.hardware.usb.accessory`
- storage/media permissions
- network permissions

Thermal screen capture worked. A cropped thermal frame with hotspot overlay was produced successfully.

### Thermal Live Capture Over ADB

Command:

```powershell
& "C:\Users\teres\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" prototype\thermal_stream_test.py benchmark --frames 10 --crop 0,0,921,950
```

Measured result:

```text
Frames: 10
Total capture time: 8.93s
Average capture FPS: 1.12
Average frame latency: 893.4 ms
Fastest/slowest frame: 867.8/926.3 ms
Frame size: 921x950
```

Interpretation:

- ADB `screencap` is good enough for proof-of-concept thermal evidence and hotspot testing.
- It is not fast enough for the main real-time drone detector.

### Thermal MP4 Recording

Android `screenrecord` worked for thermal app display recording.

Command shape:

```powershell
adb shell screenrecord --time-limit 5 --bit-rate 8000000 /sdcard/codex_thermal_screenrecord.mp4
adb pull /sdcard/codex_thermal_screenrecord.mp4 prototype\logs\codex_thermal_screenrecord.mp4
```

Measured result from `ffprobe`:

```text
width=1080
height=2400
r_frame_rate=60/1
duration=5.063378
nb_frames=191
```

Interpretation:

- Screen recording is much better than live `screencap` for collecting thermal test clips.
- This is useful for fallback demos, model experiments, and generating validation data.
- This phone build's `screenrecord --help` did not expose a direct stdout/H.264 live-stream mode.

### RGB Camera Via IP Webcam

Package:

```text
com.pas.webcam
```

Server:

```text
http://10.205.122.165:8080
```

ADB forwarded access:

```text
adb forward tcp:8080 tcp:8080
http://127.0.0.1:8080
```

Successful endpoints:

- `/status.json`
- `/shot.jpg`
- `/video`
- `/audio.wav`
- `/audio.aac`
- `/audio.opus`

Measured `/shot.jpg` result:

```text
60 frames in 2.43s = 24.72 FPS
video_size=640x480
orientation=landscape
```

Follow-up run:

```text
10 frames in 0.34s = 29.44 FPS
```

MJPEG endpoint:

```text
/video -> multipart/x-mixed-replace
```

Interpretation:

- IP Webcam is the preferred current path for live RGB detection.
- `/shot.jpg` is simple and already fast enough for a prototype.
- `/video` is the better long-running stream endpoint for OpenCV/Jetson.

### Audio Via IP Webcam

Working endpoints:

```text
/audio.wav  -> audio/x-wav
/audio.aac  -> audio/aac
/audio.opus -> audio/ogg; codecs="opus"
```

Interpretation:

- Microphone streaming is available now.
- Use WAV first for simplest prototype parsing.
- Use AAC/Opus later if network bandwidth matters.

### USB Webcam Mode

Current USB function:

```text
adb
```

`svc usb` advertised possible functions:

```text
mtp, ptp, rndis, midi, ncm
```

It did not advertise `uvc`.

Windows currently sees:

```text
Armor 28 Ultra
```

It does not currently expose the phone as a UVC webcam. The Android `com.android.DeviceAsWebcam` package exists, but no UVC function was exposed in the tested USB function list.

Interpretation:

- Do not rely on USB webcam mode for the hackathon MVP.
- Use IP Webcam + ADB forward or network URL instead.

## Critical Constraint

The thermal app and IP Webcam both use camera ID 0. Launching the thermal app evicted IP Webcam from the camera service:

```text
EVICT device 0 client held by package com.pas.webcam
Evicted by device 0 client for package com.energy.tc2c
```

This means two separate apps cannot currently provide simultaneous RGB and thermal streams.

## Practical Architecture Decision

For the hackathon MVP:

```text
Mode A: Live RGB + audio
  IP Webcam -> Jetson/Python -> detector + audio classifier

Mode B: Thermal evidence
  Thermal app -> ADB screencap for low-FPS live evidence
  Thermal app -> screenrecord MP4 for fallback clips and validation
```

For a true simultaneous RGB + thermal + audio product:

- Build a custom Android app if both sensors are accessible.
- Investigate Ulefone/ThermoVue SDK or vendor APIs.
- Investigate whether the thermal app's MIX mode can provide enough combined visual evidence from one screen stream.
- Otherwise use two devices: one RGB/audio phone stream and one thermal phone stream.

## Current Recommendation

Use this for the first working detector:

```text
IP Webcam /video or /shot.jpg
  -> ADB forward tcp:8080
  -> Python/Jetson detector
  -> dashboard
```

Add thermal as a secondary demonstration source using:

```text
thermal_stream_test.py screen-stream --hotspots
```

or recorded MP4 clips from the thermal app.

