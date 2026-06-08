# Prototype

Put runnable prototype code here.

Suggested structure once implementation starts:

- `src/` for application code
- `data/` for small replay samples or synthetic logs
- `notebooks/` for experiments
- `demo/` for pitch-ready scripts

Keep large datasets out of git unless explicitly needed.

## Current Sensor Tests

- [thermal_stream_test.md](thermal_stream_test.md) tests live thermal app screen capture and thermal screenshots over ADB.
- [phone_sensor_smoke_test.md](phone_sensor_smoke_test.md) tests RGB and audio streaming through IP Webcam.
- [phone_sensor_test_report.md](phone_sensor_test_report.md) summarizes the measured phone sensor results.
- [direct_thermal_sensor_attempt.md](direct_thermal_sensor_attempt.md) documents the direct Android sensor/USB/Camera2 access attempt.
- [phone_sensor_capability_20260608.md](phone_sensor_capability_20260608.md) records the latest sound/camera/thermal simultaneity test on the connected phone.
- [thermovue_reverse_engineering.md](thermovue_reverse_engineering.md) maps ThermoVue's internal thermal USB/native pipeline and likely raw frame layout.
- [thermovue_sensor_live_viewer.md](thermovue_sensor_live_viewer.md) documents the laptop-side raw thermal packet visualizer.
- [thermovue_frida_bridge.md](thermovue_frida_bridge.md) documents the Frida-based phone-side raw packet bridge path.

## Native ThermoVue Bridge

Build the bridge APK and shell USB helper:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\build_thermovue_bridge_probe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\build_usb_shell_helper.ps1
```

Run the diagnostic watch/grant test after the phone is authorized in ADB:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\run_thermal_bridge_watch_test.ps1
```

To test Android's fixed-handler grant path without bringing the bridge UI forward:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\run_thermal_bridge_watch_test.ps1 -UseHeadlessFixedHandler -SkipManualGrant
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\wait_for_adb_and_run_thermal_test.ps1 -Mode watch -UseHeadlessFixedHandler -SkipManualGrant
```

Experimental shell-side bridge path, without launching our Android Activity:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\build_usb_shell_helper.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\run_thermal_shell_bridge_test.ps1
```

This runs `ThermoVueShellBridge` through Android `app_process` as the shell UID,
loads ThermoVue's APK/classes/native libraries, grants the thermal USB device to
UID 2000, optionally tries shell-side Tiny2C sysfs power writes, and tries the
same Tiny2C startup sequence. Use `-NoSysfsPower` if ThermoVue should be the only
process touching the power path during a test.

To wait for the phone authorization prompt and automatically run a test:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\wait_for_adb_and_run_thermal_test.ps1 -Mode watch
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\wait_for_adb_and_run_thermal_test.ps1 -Mode shell
```

For live thermal UDP forwarding to a Jetson/laptop, start the receiver first:

```powershell
py -3 prototype\thermal_udp_receiver.py --host 0.0.0.0 --port 25000 --save-dir prototype\logs\thermal_udp_frames
```

Or run the combined Counter-UAS fusion node:

```powershell
py -3 prototype\counter_uas_fusion_node.py --rgb-source 0 --thermal-port 25000
```

With a programmable stand controller listening over UDP:

```powershell
py -3 prototype\counter_uas_fusion_node.py --rgb-source 0 --thermal-port 25000 --mount-udp-host 192.168.1.60 --mount-udp-port 26000
```

For a headless/demo smoke test:

```powershell
py -3 prototype\counter_uas_fusion_node.py --demo --no-window --max-frames 30
```

Pan/tilt command scaffold:

```powershell
py -3 prototype\pan_tilt_controller.py --frame-width 640 --frame-height 360 --target 520,120
```

Then run the phone bridge with the receiver IP:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\run_thermal_bridge_watch_test.ps1 -JetsonHost 192.168.1.50 -JetsonPort 25000 -KeepStreaming -UdpMaxFrames 0
```

Use the Jetson or laptop IP reachable from the phone. For the hackathon setup, prefer USB tethering and the tether interface IP when available.
