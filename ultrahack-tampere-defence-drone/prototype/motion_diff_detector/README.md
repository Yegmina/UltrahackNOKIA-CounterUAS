# Motion Diff Drone Prototype

Standalone motion differencing prototype. It turns a video into a black-background motion-only video and an overlay video with boxes around moving regions.

This is useful when the camera is fixed or handheld and the main moving object is expected to be a drone.

## Install

```powershell
py -3 -m pip install -r prototype\motion_diff_detector\requirements-motion-diff-detector.txt
```

On macOS/Linux:

```bash
python3 -m pip install -r prototype/motion_diff_detector/requirements-motion-diff-detector.txt
```

## Test Interface

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\motion_diff_detector\run_motion_diff_ui.ps1
```

On macOS/Linux:

```bash
python3 -m streamlit run prototype/motion_diff_detector/app.py --server.address 127.0.0.1 --server.port 8505 --server.maxUploadSize 4096
```

Open:

```text
http://127.0.0.1:8505
```

The app upload limit is configured to 4096 MB in `.streamlit/config.toml`.

The sidebar has a `Parameter profile` expander for settings files:

- `Export current parameters JSON` saves the current sliders, toggles, backend/output settings, semantic filter settings, and current ROI mask.
- `Import parameters JSON` restores those controls from the saved file and reruns the UI with the imported values.

The ROI tab still has a separate `Download mask JSON` button for ROI-only reuse.

Long runs can be stopped from the progress panel. The detector finalizes the partial segment,
shows the cumulative output video, and stores a `resume_manifest.json` under the run folder.
Use `Continue from frame ...` in the latest output panel to process the next segment and rebuild
the combined overlay/motion videos without starting from frame 0.

`Merge nearby box distance` is an overlay-only display cleanup: overlapping drone boxes always
draw as one box, and boxes within the configured pixel distance are merged visually too.
`Held box frames` and `Held box expansion px` are also overlay-only: when a drone box disappears,
the last box fades out for the configured number of frames unless a real box appears nearby.

Preset files live in `prototype/motion_diff_detector/profiles/` and can be imported from the
`Parameter profile` panel. `slow_drone_sensitivity.json` is tuned from the aggressive IR profile
to keep slow, intermittent drone tracks alive longer while preserving the same top ROI mask.

## CLI

Run the sample fixed-camera video:

```powershell
py -3 prototype\motion_diff_detector\motion_diff_detector.py video "C:\Users\teres\Downloads\fixedcameravideo_2026-06-10_00-10-22.mp4" --out-dir prototype\motion_diff_detector\outputs\sample
```

Common options:

```text
--diff-threshold 18
--min-area 1000
--blur-kernel 5
--morph-kernel 3
--trail-frames 3
--max-motion-ratio 0.10
--analysis-scale 0.5
--backend auto
--cuda-device 0
--disable-shake-protection
--shake-min-shift 1.5
--shake-consensus 0.72
--shake-consensus-px 2.0
--enable-hysteresis
--hysteresis-high-threshold 36
--enable-temporal-filter
--temporal-window-frames 3
--temporal-min-hits 2
--enable-track-confirmation
--track-confirm-hits 2
--track-max-missed 2
--track-match-distance 80
--enable-direction-consistency
--direction-min-hits 3
--direction-min-displacement 2
--direction-cosine 0.20
--roi-mask path\to\roi_mask.json
--enable-semantic-filter
--semantic-labels person
--semantic-action reject
--semantic-conf 0.05
--semantic-imgsz 960
--semantic-device mps
--semantic-frame-stride 2
--semantic-overlap-threshold 0.15
--json
```

Backend modes:

- `auto`: use OpenCV CUDA when a CUDA device is available; otherwise fall back to CPU.
- `cpu`: force CPU processing.
- `cuda`: require OpenCV CUDA and fail clearly if no CUDA device/build is available.

CUDA acceleration covers frame resize, grayscale conversion, blur, frame differencing, thresholding, morphology, and shake-compensation warping. Contour extraction, ROI filtering, tracking, semantic filtering, JSON output, and MP4 writing still run on CPU.

## Flying Object AI Overlay

Run the pretrained `devanshty/WingID` model on a video and draw detected `Bird` boxes:

```bash
python3 prototype/motion_diff_detector/flying_object_annotator.py \
  "/path/to/fake-bird-video.MOV" \
  --out-dir local_tests/flying_object_ai \
  --labels bird \
  --conf 0.03 \
  --imgsz 960 \
  --frame-stride 1
```

This is a semantic overlay test only. It does not yet suppress or boost motion-diff detections. Outputs:

- `*_flying_objects.mp4`
- `*_flying_objects.jsonl`
- `*_flying_objects_summary.json`

The earlier `Javvanny/yolov8m_flying_objects_detection` test was kept optional via `--model-repo` and `--model-file`, but on the `IMG_2781.MOV` fake-bird clip it mostly mislabeled the hanging props and arena geometry as `airplane`.

## Human Semantic Filter

The motion-diff runner can optionally use the WingID/YOLO11l `person` class to suppress false motion from people:

```bash
python3 prototype/motion_diff_detector/motion_diff_detector.py video \
  "/path/to/video.mp4" \
  --enable-semantic-filter \
  --semantic-labels person \
  --semantic-action reject \
  --semantic-conf 0.05 \
  --semantic-imgsz 960 \
  --semantic-device mps \
  --semantic-frame-stride 2 \
  --semantic-overlap-threshold 0.15
```

Person boxes are drawn in green on the overlay. Motion boxes overlapping person boxes are rejected by default, or tagged as semantic penalties when `--semantic-action penalize` is used. Summary JSON includes `semantic_detection_count`, `semantic_rejected_count`, `semantic_penalized_count`, and `processing_ms_per_frame`.

## Small Drone Noise Filters

The Streamlit sidebar exposes optional filters for noisy low-threshold videos:

- `Hysteresis thresholding`: a low-threshold motion region must contain a stronger high-threshold seed. This keeps weak drone edges attached to strong pixels while dropping low-level codec/sensor noise.
- `Temporal persistence`: a detection must appear enough times inside a short frame window.
- `Track confirmation`: detections are tracked internally but hidden until the track has enough hits.
- `Direction consistency`: confirmed tracks that jitter back and forth are rejected.

Suggested first test for small racing drones:

```text
diff threshold: low enough to see the drone
minimum motion area: low enough for the drone
hysteresis: on, high threshold around 2x diff threshold
temporal persistence: on, window=3, min hits=2
track confirmation: on, confirm hits=2
direction consistency: on only after the first two filters look stable
```

## ROI Masks

The Streamlit UI has an `ROI mask` tab for click-building polygon zones on a selected video frame.
When a video is selected in the `Upload` tab, it is cached to a temporary local path and automatically becomes the ROI preview source.
Use:

- `ignore` for tribunes, people, static props, edge bands, floor bands, lights, and gates that should remove detections.
- `penalty` for regions that may contain useful motion but should be lower confidence.
- `flight` for valid flight space. If any flight zones exist, detections outside them are rejected.
- `Snap to edges/corners` in the ROI tab to make clicks near preview borders land exactly on `0.0` or `1.0`.
- `Quick edge band` to create exact top, bottom, left, or right rectangular masks without hand-clicking all four corners.

`ignore` and `flight` zones are applied directly to the motion mask before contours are extracted.
This clips moving pixels at ROI boundaries instead of dropping a whole bounding box just because it touched an ignored area.
`penalty` zones are applied after contour extraction so the detection can stay visible and be tagged in JSON/overlay output.

Mask modes:

- `fixed` means the normalized mask represents arena/world regions in a static view.
- `handheld` means the normalized mask is screen-relative, useful for broad edge, floor, glare, and center-priority guardrails.

Mask JSON is normalized from `0.0` to `1.0`, so it can be reused between preview/downscaled frames and original videos:

```json
{
  "version": 1,
  "mode": "fixed",
  "zones": [
    {
      "name": "tribunes",
      "type": "ignore",
      "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 0.25], [0.0, 0.25]],
      "penalty": 0.0
    }
  ]
}
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
  "global_motion_detected": true,
  "global_dx": 1.4,
  "global_dy": -0.6,
  "global_consensus": 0.82,
  "tracked_vectors": 151,
  "raw_detection_count": 1,
  "roi_rejected_count": 0,
  "roi_penalized_count": 0,
  "temporal_rejected_count": 0,
  "unconfirmed_rejected_count": 0,
  "direction_rejected_count": 0,
  "detections": [
    {
      "x1": 410.0,
      "y1": 290.0,
      "x2": 438.0,
      "y2": 316.0,
      "center_x": 424.0,
      "center_y": 303.0,
      "area": 260.0,
      "roi_action": "keep",
      "zone_type": null,
      "zone_name": null,
      "roi_penalty": 0.0,
      "track_id": 7,
      "track_age": 4,
      "track_hits": 3,
      "track_confirmed": true,
      "motion_dx": 6.0,
      "motion_dy": -2.0,
      "direction_consistent": true
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
