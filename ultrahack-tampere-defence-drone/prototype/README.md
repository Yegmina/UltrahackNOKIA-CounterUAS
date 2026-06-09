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
- [jetson_runbook.md](jetson_runbook.md) gives the Jetson/laptop setup and run commands for the fusion node.
- [thermal_live_debug_apk.md](thermal_live_debug_apk.md) documents the standalone on-phone APK for testing whether live thermal frames can be shown inside our own app.
- [counter_uas_system_design.md](counter_uas_system_design.md) summarizes the phone + Jetson fusion architecture and current thermal-access decision.

When ADB is unavailable but the phone is visible as MTP storage, use:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\mtp_phone_helper.ps1 -Action CopyApk
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\mtp_phone_helper.ps1 -Action PullLogs
```

Inside the Thermal Live Debug app, `Dump APKs` writes ThermoVue Pro/SOP APK and
native-library artifacts into the current `thermal_live_debug_*` session folder
under `vendor_dump/`; `PullLogs` copies those files back to the laptop.

Install prototype dependencies:

```powershell
py -3 -m pip install -r prototype\requirements-counter-uas.txt
```

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

Run the side-loaded APK privilege/clone audit and pull the log:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\run_thermal_privilege_audit.ps1
```

Run the bridge's exact ThermoVue Pro startup clone path:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\build_thermovue_bridge_probe.ps1
adb install -r prototype\android_thermovue_bridge_probe\build\thermovue-bridge-probe.apk
adb shell pm grant com.yegmina.thermovuebridgeprobe android.permission.CAMERA
adb shell pm grant com.yegmina.thermovuebridgeprobe android.permission.RECORD_AUDIO
adb shell am start -n com.yegmina.thermovuebridgeprobe/.MainActivity --ez privileged true --ei udpMaxFrames 1
```

On the stock side-loaded phone build this should still fail at
`untrusted_app`/Tiny2C `EACCES`/`ctrlBlock=null`. If a vendor/platform install
route is available, this is the first command path that should change to real
thermal frames.

The watch and shell bridge runners invoke `thermal_frame_evidence_validator.py`
after pulling their logs, so a real run reports whether the saved output proves
live, non-empty thermal frames.

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

If the phone stays `unauthorized`, force a fresh USB debugging trust prompt:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\wait_for_adb_and_run_thermal_test.ps1 -Mode shell -ResetHostKey
```

For live thermal UDP forwarding to a Jetson/laptop, start the receiver first:

```powershell
py -3 prototype\thermal_udp_receiver.py --host 0.0.0.0 --port 25000 --save-dir prototype\logs\thermal_udp_frames
```

Or run the combined Counter-UAS fusion node:

```powershell
py -3 prototype\counter_uas_fusion_node.py --rgb-source 0 --thermal-port 25000
```

Add phone microphone confidence from IP Webcam's WAV stream:

```powershell
adb forward tcp:8080 tcp:8080
py -3 prototype\counter_uas_fusion_node.py --rgb-source http://127.0.0.1:8080/video --audio-wav-url http://127.0.0.1:8080/audio.wav
```

Run the full fusion loop without the phone:

```powershell
py -3 prototype\counter_uas_fusion_node.py --demo --audio-demo --no-window --max-frames 90
```

With a programmable stand controller listening over UDP:

```powershell
py -3 prototype\counter_uas_fusion_node.py --rgb-source 0 --thermal-port 25000 --mount-udp-host 192.168.1.60 --mount-udp-port 26000
```

For a headless/demo smoke test:

```powershell
py -3 prototype\counter_uas_fusion_node.py --demo --no-window --max-frames 30
```

Validate that logs or saved frames prove real thermal data:

```powershell
py -3 prototype\thermal_frame_evidence_validator.py --bridge-log prototype\logs\thermovue_bridge_watch\RUN.log
py -3 prototype\thermal_frame_evidence_validator.py --npy prototype\logs\thermal_udp_frames\thermal_1.npy
py -3 prototype\thermal_frame_evidence_validator.py --self-test
```

Analyze raw USB endpoint captures pulled from the debug APK:

```powershell
py -3 prototype\analyze_usb_probe_capture.py prototype\mtp_pulled_logs --write-pgm prototype\logs\usb_probe_previews
```

Inspect pulled ThermoVue APK/native-library dumps:

```powershell
py -3 prototype\inspect_vendor_dump.py prototype\mtp_pulled_logs --out prototype\logs\vendor_dump_report.md
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
