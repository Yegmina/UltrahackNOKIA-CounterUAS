# Vision Drone Detector Prototype

Standalone workspace for testing UAV object detection on images and videos.
The default model path is `prototype/vision_drone_detector/models/best.pt`,
downloaded from `zsx060/Anti-UAV-datasets`.

## Install

```powershell
py -3 -m pip install -r prototype\vision_drone_detector\requirements-vision-drone-detector.txt
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\vision_drone_detector\download_anti_uav_model.ps1
```

The downloaded `best.pt` file is ignored by git.

## Test Interface

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\vision_drone_detector\run_vision_detector_ui.ps1
```

Open:

```text
http://localhost:8502
```

## CLI

Run an image:

```powershell
py -3 prototype\vision_drone_detector\vision_drone_detector.py image .\frame.jpg --conf 0.25 --out-dir prototype\vision_drone_detector\outputs\image_test
```

Run a video:

```powershell
py -3 prototype\vision_drone_detector\vision_drone_detector.py video .\clip.mp4 --conf 0.25 --out-dir prototype\vision_drone_detector\outputs\video_test
```

Common options:

```text
--model prototype\vision_drone_detector\models\best.pt
--conf 0.25
--imgsz 640
--device cpu
--out-dir prototype\vision_drone_detector\outputs\run
```

## Outputs

Image runs write:

- `*_annotated.png`
- `detections.json`

Video runs write:

- `*_annotated.mp4`
- `detections.jsonl`, one record per frame

Detection fields:

```json
{
  "source": "frame.jpg",
  "model": "models/best.pt",
  "confidence_threshold": 0.25,
  "image_width": 640,
  "image_height": 360,
  "detections": [
    {
      "x1": 10.0,
      "y1": 20.0,
      "x2": 80.0,
      "y2": 90.0,
      "confidence": 0.82,
      "class_id": 0,
      "class_name": "uav"
    }
  ]
}
```
