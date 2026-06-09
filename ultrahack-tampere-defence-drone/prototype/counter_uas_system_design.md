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
- ThermoVue IJPEG photo captures now prove we can extract real 256x192 `uint16`
  IR and temperature planes from `/sdcard/Pictures/thermo_tc2c/*.jpg`.
- `thermovue_ijpeg_live_pull.py` gives a low-rate ADB bridge by triggering
  ThermoVue captures, pulling the IJPEG, extracting `temp_u16le`, and forwarding
  the same UDP frame format the Jetson receiver already understands.
- The latest bridge APK matches ThermoVue Pro's startup order, but a normal
  side-loaded APK still runs as `untrusted_app` and cannot read/write the Tiny2C
  sysfs power/mux nodes.
- When our app is foreground, ThermoVue loses foreground and the internal
  thermal USB device disappears from normal app view. When ThermoVue stays
  foreground, the USB device stays visible, but Android rejects our background
  USB permission request with `granted=false`; an ADB watcher confirmed
  `UsbPermissionActivity` appears briefly, but the visible UI remains ThermoVue
  and the grant still fails.
- ADB shell cannot directly read `/dev/bus/usb/001/002` or write the Tiny2C
  sysfs power/mux nodes on the stock phone.
- FactoryMode can power/connect the module from its privileged thermal test,
  but the activity is not exported and the vendor `IChangeNode` HAL is blocked
  to shell/normal apps by SELinux.
- Starting our bridge immediately after FactoryMode connects the module still
  produced no reusable raw thermal stream.
- Frida/in-process hooking targets the real ThermoVue PID but is blocked on the
  production phone by no root, no debuggable target, and no usable frida-server.
- Production-quality raw thermal requires a vendor SDK/API, root, platform
  signing, or an in-process ThermoVue hook.
- Screen capture of ThermoVue can be used only as a visual fallback, not as raw
  temperature data. Prefer IJPEG extraction for any thermal algorithm tests.
- ThermoVue `.mp4` recordings are visual H.264/AAC files in the confirmed
  sample; no IJPEG/private raw thermal payload was found.

## Thermal Access Modes

Use one stable Jetson interface: `YEGMINA_THERMAL_RAW_V1` UDP frames containing
one 256x192 little-endian `uint16` plane.

Mode A, preferred live mode:

- source: platform-signed/vendor bridge or in-process ThermoVue hook;
- rate: target 20-25 fps;
- status: mapped, but blocked on stock phone permissions.

Mode B, working raw fallback:

- source: ThermoVue foreground + ADB photo trigger + IJPEG extraction;
- rate: about one frame every 1.7-2.2 seconds in current tests;
- status: working and raw, good for calibration and slow fusion evidence.

Mode C, visual fallback:

- source: ThermoVue screen capture, MP4 playback, or normal RGB capture of
  ThermoVue UI;
- rate: near-live visual feed possible;
- status: useful for a demo display or visual detector, but not raw thermal
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

1. Get RGB stream from phone to Jetson working with an IP camera app or our
   custom phone app.
2. Run a drone detector on Jetson and show live boxes/latency.
3. Add phone microphone stream and audio confidence.
4. Start the Jetson fusion node with the thermal UDP listener even if no thermal
   packets are present yet.
5. Keep thermal work as a parallel track:
   - ask mentors/vendor for privileged ThermoVue SDK access;
   - show current evidence that side-loaded direct thermal is blocked;
   - use the IJPEG ADB bridge for low-rate real raw thermal fusion tests;
   - use ThermoVue screen capture only if a thermal visual is needed for demo.
6. If time remains, connect pan/tilt commands to a prebuilt programmable holder
   or simple microcontroller mount.

## Current Thermal Decision

Do not depend on high-FPS native raw thermal for the first demo milestone. The
phone can now provide real low-rate raw thermal through ThermoVue IJPEG capture,
but the true live feed still requires crossing the vendor/platform privilege
boundary. Build and demo RGB + audio + tracking first, use IJPEG thermal when
latency is acceptable, and keep the thermal UDP/raw-frame receiver as the
integration point for vendor/platform access.
