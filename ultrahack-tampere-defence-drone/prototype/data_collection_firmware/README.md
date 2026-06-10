# Data Collection Firmware Prototype

Laptop-side synchronized data collection for drone datasets. It records selected
camera and microphone sources into separate files, writes timing metadata for
each stream, and adds a small UTC millisecond timestamp overlay to videos.

This is a prototype for data collection, not embedded firmware yet.

## Install

```powershell
py -3 -m pip install -r prototype\data_collection_firmware\requirements-data-collection-firmware.txt
```

FFmpeg must be on `PATH` for Windows DirectShow device discovery.

## UI

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\data_collection_firmware\run_data_collection_ui.ps1
```

Open:

```text
http://127.0.0.1:8510
```

Recommended flow:

1. Press `Refresh devices`.
2. Keep all usable cameras and microphones selected, or remove sources you do not need.
3. Fill session name, location, and notes.
4. Press `Arm` and film the displayed sync code with phones or IR devices.
5. Press `Start recording`.
6. Press `Sync marker flash/beep` while phones or IR devices can see/hear the laptop.
7. Press `Stop recording and finalize`.

## CLI

List devices:

```powershell
py -3 prototype\data_collection_firmware\data_collection_firmware.py --json list-devices
```

Record all detected usable sources for 10 seconds:

```powershell
py -3 prototype\data_collection_firmware\data_collection_firmware.py --json --progress-json record --all-detected --duration-s 10 --sync-beep
```

Record specific devices:

```powershell
py -3 prototype\data_collection_firmware\data_collection_firmware.py --json record --camera-index 0 --audio-index 1 --duration-s 10
```

## Outputs

Each session creates one folder under `prototype\data_collection_firmware\outputs` by default:

- `camera_<device>.mp4`
- `audio_<device>.wav`
- `video_frames_<device>.jsonl`
- `audio_chunks_<device>.jsonl`
- `session_manifest.json`
- `stream_summary.csv`

The manifest includes selected devices, session start/stop UTC timestamps,
monotonic timestamps, start jitter, stream file paths, stream counters, and sync
marker events.

## Synchronization

The collector opens selected devices first, schedules one shared start time, and
then releases all capture workers from one barrier. Every media frame/chunk gets
both UTC and monotonic timing metadata.

Video timestamp overlay:

- Small text in the upper-left corner.
- UTC/world time with milliseconds.
- Visible for `timestamp_visible_s` every `timestamp_interval_s` seconds.

For phone/IR alignment, use the session code, the visible white sync panel,
audible beep markers, timestamp overlays, and `sync_markers` in the manifest.

## Tests

```powershell
py -3 -m py_compile prototype\data_collection_firmware\*.py
py -3 -m pytest prototype\data_collection_firmware\test_data_collection_firmware.py
```
