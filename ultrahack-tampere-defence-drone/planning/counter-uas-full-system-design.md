# Counter-UAS Phone + Jetson System Design

Updated: 2026-06-09

## Goal

Build a hackathon-ready Counter-UAS prototype using the Ulefone Armor 28 Ultra Thermal as the sensor head and a Jetson Orin Nano 4 GB as the real-time inference and fusion computer.

The system should detect likely drones from RGB video first, then improve confidence with thermal/IR and audio when those streams are available. If time allows, the phone sits on a controllable rotating stand so the system can keep the drone in view instead of using a fixed field of view.

## System Shape

```text
Ulefone sensor head
  RGB camera stream
  microphone audio stream
  thermal/IR bridge when available
        |
        | USB tethering preferred, private Wi-Fi fallback
        v
Jetson Orin Nano
  ingest adapters
  detector models
  tracker
  sensor fusion
  mount controller
  web dashboard + log recorder
        |
        v
Operator display
  live view
  detection boxes
  confidence score
  bearing / tracking state
  latency + sensor health
```

## Runtime Modes

Use modes so the demo keeps working even when one sensor path fails:

- `demo`: synthetic RGB/audio plus optional replay files, used for table testing.
- `rgb_audio_live`: IP Webcam RGB + microphone into Jetson, used as the first live detector.
- `thermal_fallback`: ThermoVue screen capture or screenrecord, used for visual thermal proof.
- `native_thermal`: ThermoVue bridge UDP raw frames into Jetson, used when native access validates.
- `full_tracking`: RGB + thermal + audio fusion with pan/tilt commands enabled.

The operator UI should show the active mode and sensor health so a missing
thermal stream does not look like a detector failure.

## Current Phone Access Status

As of 2026-06-09, laptop-to-phone USB storage access works, but ADB is not
usable in the current Windows USB mode:

- MTP can copy APKs to `Download` and pull
  `Android/data/com.yegmina.thermallivedebug/files` logs.
- `adb devices` is empty even though Windows shows an `ADB Interface` for
  `VID_0E8D&PID_201D&MI_01`.
- The currently pulled app logs are from an older installed build. They prove
  ThermoVue SDK classes load and our app can reach a non-null USB
  `UsbControlBlock`, but they still show `frameCount=0`, `rawTemp=null`, and
  `remapTemp=null`.
- Native thermal frames are not yet verified. Treat `native_thermal` as the
  primary technical risk until the latest APK logs show a real frame or callback.

Decision tree for the hackathon:

```text
Can latest APK install and run?
  yes -> run Engine Probe / Native Auto -> pull logs over MTP or HTTP
  no  -> use RGB + audio MVP while fixing ADB/USB mode

Do latest logs show raw/remap thermal frames?
  yes -> stream /latest.raw or UDP into Jetson fusion
  no  -> keep thermal as research path, demo RGB/audio/fusion

Can phone stream RGB/audio reliably?
  yes -> main live demo is phone sensor head + Jetson detector
  no  -> use replay/demo mode and focus on fusion/tracking UI
```

## MVP Decision

Use the Jetson as the main compute device.

Reasons:

- Easier to run and swap Python/OpenCV/PyTorch/ONNX models quickly.
- Easier debugging during the hackathon.
- Better sustained inference than a phone-only app.
- Phone stays focused on sensing and streaming.
- Mount control and dashboard are simpler from Linux.

The phone-only app remains a later product direction once the thermal path and model choices are stable.

## Sensor Inputs

### RGB

Use RGB first because it is the fastest reliable input.

Preferred route:

```text
Android IP camera / custom app -> USB tether network -> Jetson OpenCV/GStreamer
```

Target:

- 640x360 or 640x480 for first live model.
- 10-25 FPS depending on model load.
- Drop stale frames; always process newest frame.

### Thermal / IR

Current best native route:

```text
ThermoVue powers internal Tiny2C USB module
ADB shell helper grants USB permission to bridge app
Bridge loads ThermoVue SDK classes
Bridge gets USB ctrlBlock
Bridge initializes Tiny2C/IrcamEngine
Bridge forwards raw temp/IR frames to Jetson over UDP
```

Fallback route:

```text
ThermoVue app screen/video capture -> Jetson vision pipeline
```

Raw thermal frame target:

- Sensor-like temp frame: around 256x192x2 bytes.
- Use frame timestamp and checksum in UDP metadata.
- Jetson visualizes thermal as false-color heatmap.

### Audio

First route:

```text
Phone microphone -> IP Webcam /audio.wav -> USB tether or ADB forward -> Jetson
```

Processing:

- 16 kHz mono is enough for first prototype.
- Sliding windows of 0.5-2.0 seconds.
- Current prototype: lightweight RMS + FFT band-energy score.
- Later: log-mel spectrogram classifier.
- Classifier output: drone-like / not-drone-like confidence.

Audio is a confidence side-channel, not the primary detector.

## Jetson Modules

### Ingest

Separate adapters normalize every source into timestamped packets:

```text
RgbFrame {
  source_id
  timestamp_ms
  image_bgr
  frame_id
}

ThermalFrame {
  source_id
  timestamp_ms
  temp_u16_or_raw
  width
  height
  frame_id
}

AudioWindow {
  source_id
  timestamp_ms
  samples
  sample_rate
}
```

Current prototype transport contracts:

```text
RGB: OpenCV VideoCapture source
  Examples: camera index, video file, http://127.0.0.1:8080/video

Thermal: UDP datagrams
  Magic: YEGMINA_THERMAL_RAW_V1
  Payload: little-endian uint16, default 256x192

Audio: HTTP WAV stream
  Example: http://127.0.0.1:8080/audio.wav
  Prototype score: RMS + 80-1200 Hz band-energy confidence

Mount: UDP or serial ASCII line
  PT pan=<speed> tilt=<speed> reason=<track|scan|hold|centered>
```

### Detection

RGB detector:

- Start with an object detector fine-tuned or prompted for drones if available.
- Use YOLO/ONNX/TensorRT if already working; otherwise OpenCV DNN is acceptable for demo.
- Output bounding boxes, class, confidence.

Thermal detector:

- First pass: hot/cold blob motion and shape filtering.
- Later: train a small thermal drone classifier.

Audio detector:

- First pass: band-energy heuristic plus recorded examples.
- Later: log-mel classifier.

### Tracking

Use a simple tracker before fancy fusion:

- Kalman filter per candidate.
- IoU or center-distance association.
- Track state: tentative, confirmed, lost.
- Smooth bearing and confidence over time.

### Fusion

Compute one operator-facing score per track:

```text
fused_score =
  0.60 * rgb_score +
  0.25 * thermal_score +
  0.15 * audio_score +
  track_stability_bonus -
  stale_sensor_penalty
```

For the demo, make the fusion explainable:

- RGB saw a drone-like object.
- Thermal agrees / does not agree.
- Audio agrees / does not agree.
- Confidence changed over time.

## Rotating Phone Mount

The mount is second priority after the fixed-camera detector works.

Recommended control shape:

```text
Jetson tracker target center
  -> pixel error from frame center
  -> PID/deadband controller
  -> serial/Bluetooth/Wi-Fi command
  -> pan/tilt phone stand
```

Minimum command API:

```text
pan_left(speed)
pan_right(speed)
tilt_up(speed)
tilt_down(speed)
stop()
center()
```

Better command API:

```text
set_pan_tilt(pan_deg, tilt_deg, speed)
get_pose()
stop()
```

Tracking behavior:

- Deadband: do not move if target is near center.
- Rate limit commands to avoid oscillation.
- Lose target: slow scan pattern.
- Reacquire: resume track from last bearing.

Do not make mount control part of the critical demo path unless fixed-camera detection is stable.

## Dashboard

Show:

- Live RGB view with boxes and track IDs.
- Optional thermal heatmap panel.
- Fused confidence timeline.
- Current sensor health: RGB FPS, thermal FPS, audio status.
- Latency estimate.
- Mount state: fixed, tracking, scanning, lost.

Keep a replay mode so the demo works even if venue RF, USB, or drone availability fails.

## First Working Build Order

1. Jetson receives live RGB from phone.
2. Jetson runs drone detector on live RGB.
3. Dashboard shows boxes, confidence, FPS, latency.
4. Add audio streaming and a simple audio confidence.
5. Add thermal fallback by screen capture.
6. Add native thermal bridge when frame evidence validator passes.
7. Add thermal confidence into fusion.
8. Add pan/tilt mount control.
9. Record a reliable demo replay.

## Runnable Prototype Artifacts

Current code pieces:

- `prototype/run_thermal_bridge_watch_test.ps1`: installs the Android bridge, launches ThermoVue, grants thermal USB permission to the bridge, and pulls bridge/logcat diagnostics.
- `prototype/android_thermovue_bridge_probe/`: Android bridge probe that loads ThermoVue SDK classes and can UDP-stream raw temp frames when `keepStreaming` is enabled.
- `prototype/android_usb_shell_helper/`: shell UID helper for `IUsbManager` fixed-handler and thermal-device permission grants.
- `prototype/thermal_udp_receiver.py`: laptop/Jetson thermal UDP receiver and heatmap visualizer.
- `prototype/thermal_frame_evidence_validator.py`: pass/fail validator for bridge logs, raw frame dumps, and UDP receiver `.npy` frames.
- `prototype/counter_uas_fusion_node.py`: first combined RGB + thermal UDP + audio fusion/dashboard node, with demo mode and simple heuristic scoring.
- `prototype/pan_tilt_controller.py`: hardware-agnostic pixel-error to pan/tilt command scaffold for a programmable phone stand.

Runnable full-loop demo without the phone:

```text
py -3 prototype/counter_uas_fusion_node.py --demo --audio-demo --no-window --max-frames 90
```

Runnable live RGB/audio path once IP Webcam is active:

```text
adb forward tcp:8080 tcp:8080
py -3 prototype/counter_uas_fusion_node.py --rgb-source http://127.0.0.1:8080/video --audio-wav-url http://127.0.0.1:8080/audio.wav
```

## Native Thermal Bridge Status

What is already proven:

- ThermoVue powers the internal thermal USB module.
- Internal USB device appears as VID `0x3474`, PID `0x4321`.
- Android fixed USB handler can grant our bridge app permission.
- Shell-side `IUsbManager.grantDevicePermission` can grant the thermal USB device to our bridge while ThermoVue stays foreground.
- The bridge can load ThermoVue SDK classes.
- The bridge can get a vendor `USBMonitor$UsbControlBlock`.
- `USBMonitorManager` can reach `connected=true` with a non-null control block.

Current missing piece:

- Raw frame counters still stay at zero until the bridge exactly matches ThermoVue's `initHandleEngine` / `startPreview` sequence.
- Live tests require the phone to be ADB-authorized; when Android reports the device as `unauthorized`, all on-device retry paths are blocked before they reach ThermoVue.

Next thermal test:

```text
watchUsb bridge mode
launch ThermoVue
grant thermal USB to bridge
USBMonitorManager gets ctrlBlock
bridge calls Tiny2CDualFusionProxy.initHandleEngine(ctrlBlock, true)
bridge calls startPreview
bridge polls getFrameCount/getRawTempData
```

## Risk Register

Highest risks:

- Native thermal access remains blocked by vendor SDK state or process assumptions.
- Event Wi-Fi is noisy; prefer USB tethering.
- Pan/tilt hardware may not expose a programmable API.
- Drone visual data may be unavailable or unsafe to collect at venue.
- Jetson 4 GB memory limits model size.

Mitigations:

- Keep RGB-only detector demo working.
- Keep replay mode ready.
- Keep thermal screen-capture fallback.
- Use lightweight models and low resolution first.
- Treat mount as bonus, not dependency.

## Demo Story

The pitch should be:

```text
We turn a rugged thermal phone into a low-cost multi-sensor Counter-UAS node.
The Jetson fuses RGB, thermal/IR, and sound into one explainable confidence score.
The same tracker can later drive a moving phone stand to keep the target centered.
```

The strongest live demo is:

```text
phone camera -> Jetson detector -> dashboard -> track confidence -> optional thermal/audio agreement
```
