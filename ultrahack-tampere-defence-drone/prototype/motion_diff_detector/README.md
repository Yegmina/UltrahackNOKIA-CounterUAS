# Motion Diff Drone Prototype

Standalone fixed-camera motion differencing prototype. It turns a video into a black-background motion-only video and an overlay video with boxes around moving regions.

This is useful when the camera is fixed and the main moving object is expected to be a drone.

## Install

```powershell
py -3 -m pip install -r prototype\motion_diff_detector\requirements-motion-diff-detector.txt
```

## Test Interface

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\motion_diff_detector\run_motion_diff_ui.ps1
```

Open:

```text
http://127.0.0.1:8505
```

## CLI

Run the sample fixed-camera video:

```powershell
py -3 prototype\motion_diff_detector\motion_diff_detector.py video "C:\Users\teres\Downloads\fixedcameravideo_2026-06-10_00-10-22.mp4" --out-dir prototype\motion_diff_detector\outputs\sample
```

Common options:

```text
--diff-threshold 18
--min-area 20
--blur-kernel 5
--morph-kernel 3
--trail-frames 3
--max-motion-ratio 0.10
--analysis-scale 0.5
--json
```

## Outputs

Video runs write:

- `*_motion_only.mp4`
- `*_motion_overlay.mp4`
- `motion_detections.jsonl`
- `summary.json`

Per-frame JSONL records:

```json
{
  "source": "fixed-camera.mp4",
  "frame_index": 120,
  "timestamp_s": 6.7,
  "image_width": 960,
  "image_height": 1280,
  "motion_ratio": 0.0012,
  "global_motion_rejected": false,
  "detections": [
    {
      "x1": 410.0,
      "y1": 290.0,
      "x2": 438.0,
      "y2": 316.0,
      "center_x": 424.0,
      "center_y": 303.0,
      "area": 260.0
    }
  ]
}
```

## Offline Checks

```powershell
py -3 -m py_compile prototype\motion_diff_detector\*.py
py -3 -m pytest prototype\motion_diff_detector\test_motion_diff_detector.py
py -3 prototype\motion_diff_detector\motion_diff_detector.py --help
py -3 prototype\motion_diff_detector\motion_diff_detector.py video --help
```
