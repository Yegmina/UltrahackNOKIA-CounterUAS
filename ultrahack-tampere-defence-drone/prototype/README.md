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
- [thermovue_reverse_engineering.md](thermovue_reverse_engineering.md) maps ThermoVue's internal thermal USB/native pipeline and likely raw frame layout.
- [thermovue_sensor_live_viewer.md](thermovue_sensor_live_viewer.md) documents the laptop-side raw thermal packet visualizer.
