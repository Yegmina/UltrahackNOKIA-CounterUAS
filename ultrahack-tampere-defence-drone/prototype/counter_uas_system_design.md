# Counter-UAS Phone + Jetson System Design

## Goal

Build a portable counter-UAS sensing node using the Ulefone Armor 28 Ultra
Thermal as the front-end sensor package and a Jetson Orin Nano as the real-time
fusion/detection computer.

## Prototype Architecture

```text
Ulefone phone
  RGB camera stream
  microphone stream
  thermal bridge, if privileged/vendor access is available
       |
       | USB tethering / Wi-Fi LAN
       v
Jetson Orin Nano 4GB
  RGB drone detector
  audio drone classifier / spectral detector
  thermal detector, when raw thermal frames are available
  multi-sensor fusion + tracking
       |
       v
Operator UI + optional pan/tilt phone mount
```

## Sensor Plan

RGB is the first reliable stream:

- Phone IP Webcam, Android Camera2 app, or a small custom phone app can provide
  RGB frames.
- Jetson runs a lightweight drone detector and reports bounding boxes,
  confidence, latency, and FPS.

Audio is the second reliable stream:

- Phone microphone is streamed as WAV/PCM or recorded locally and forwarded.
- Jetson extracts spectral features and detects drone-like motor signatures.

Thermal is the high-value but privileged stream:

- ThermoVue proves the raw module produces 256x192 thermal planes at about
  25 fps inside the vendor app.
- A normal side-loaded APK cannot currently keep the Tiny2C thermal USB module
  powered or take over the stream after ThermoVue exits.
- Production-quality raw thermal requires a vendor SDK/API, root, platform
  signing, or an in-process ThermoVue hook.
- Screen capture of ThermoVue can be used only as a demo fallback, not as raw
  temperature data.

## Fusion Logic

The Jetson should treat each detector as an independent evidence source:

```text
RGB detection:     bbox, class=drone, confidence, timestamp
Thermal detection: bbox/hot-object track, confidence, timestamp
Audio detection:   bearing optional, motor confidence, timestamp
```

Fusion output:

- confirmed drone when RGB plus thermal or audio agree in time;
- possible drone when only one source is confident;
- tracked target center for pan/tilt control;
- latency and frame-drop metrics for hackathon evaluation.

## Pan/Tilt Stand

First version can be fixed-angle. If detection works, add a phone holder that
can rotate:

- Jetson sends UDP serial-style commands: target center error, pan speed, tilt
  speed.
- Mount controller keeps motion slow and stable to avoid blurring frames.
- Tracking should only move when confidence is stable for several frames.

## Recommended Hackathon Path

1. Get RGB stream from phone to Jetson working.
2. Run drone detector on Jetson and show live boxes/latency.
3. Add phone microphone stream and audio confidence.
4. Keep thermal work as a parallel track:
   - ask mentors/vendor for privileged ThermoVue SDK access;
   - show current evidence that side-loaded direct thermal is blocked;
   - use ThermoVue screen capture only if a thermal visual is needed for demo.
5. If time remains, connect pan/tilt commands to a prebuilt programmable holder
   or simple microcontroller mount.

## Current Thermal Decision

Do not depend on raw thermal for the first demo milestone. The real thermal
feed is technically reachable only past a privilege boundary on the current
phone firmware. Build the fusion pipeline so thermal can plug in later through
the existing UDP/raw-frame receiver.
