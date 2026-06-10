# VLA Scene Target Classifier Prototype

Standalone tester for scanning images and videos with a custom edge computing VLA model. It detects drones, airplanes, people, and distinct static obstacles, estimates boxes and center coordinates, classifies the object type, and writes annotated media plus structured JSON output.

## Install

```powershell
py -3 -m pip install -r prototype\vla_drone_detector\requirements-vla-drone-detector.txt
```

Create a `.env` file in this folder or in the repo root:

```text
GEMINI_API_KEY=your_key_here
VLA_MODEL=your_model_id
```

The key is read locally and is never printed by the runner.

## Test Interface

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\vla_drone_detector\run_vla_detector_ui.ps1
```

Open:

```text
http://127.0.0.1:8503
```

## CLI

Run an image:

```powershell
py -3 prototype\vla_drone_detector\vla_drone_detector.py image .\frame.jpg --conf 0.25 --prompt-type thermal_counter_uas --thermal-polarity black_is_warm --out-dir prototype\vla_drone_detector\outputs\image_test
```

Run a video:

```powershell
py -3 prototype\vla_drone_detector\vla_drone_detector.py video .\clip.mp4 --conf 0.25 --prompt-type low_light_or_noisy --thermal-polarity visible_rgb --sample-fps 1 --out-dir prototype\vla_drone_detector\outputs\video_test
```

Common options:

```text
--conf 0.25
--prompt-type thermal_counter_uas
--thermal-polarity black_is_warm
--sample-fps 1
--model optional_model_id
--out-dir prototype\vla_drone_detector\outputs\run
--json
```

Prompt presets:

- `thermal_counter_uas`: black-white thermal-like counter-UAS and scene-safety scan.
- `visible_daylight`: visible RGB/daylight scan for aircraft, people, and static hazards.
- `low_light_or_noisy`: tolerant mode for blurry, dim, noisy, or compressed video.
- `custom`: fixed schema plus your custom prompt suffix.

Thermal polarity options:

- `black_is_warm`
- `white_is_warm`
- `visible_rgb`

## Outputs

Image runs write:

- `*_annotated.png`
- `detections.json`

Video runs write:

- `*_annotated.mp4`
- `detections.jsonl`, one record per analyzed sampled frame

Detection JSON shape:

```json
{
  "source": "frame.jpg",
  "frame_index": 0,
  "timestamp_s": 0.0,
  "model": "custom edge computing VLA model",
  "prompt_type": "thermal_counter_uas",
  "thermal_polarity": "black_is_warm",
  "confidence_threshold": 0.25,
  "image_width": 640,
  "image_height": 360,
  "detections": [
    {
      "x1": 120.0,
      "y1": 40.0,
      "x2": 170.0,
      "y2": 85.0,
      "center_x": 145.0,
      "center_y": 62.5,
      "confidence": 0.82,
      "category": "drone",
      "type": "quadrotor",
      "thermal_signature": "compact warm body with four arm-like points",
      "rationale": "Small airborne multirotor silhouette against background."
    },
    {
      "x1": 300.0,
      "y1": 180.0,
      "x2": 340.0,
      "y2": 260.0,
      "center_x": 320.0,
      "center_y": 220.0,
      "confidence": 0.78,
      "category": "person",
      "type": "standing_person",
      "thermal_signature": "visible_rgb",
      "rationale": "Upright human-shaped figure in the operating area."
    },
    {
      "x1": 500.0,
      "y1": 20.0,
      "x2": 520.0,
      "y2": 340.0,
      "center_x": 510.0,
      "center_y": 180.0,
      "confidence": 0.73,
      "category": "static_obstacle",
      "type": "pole",
      "thermal_signature": "visible_rgb",
      "rationale": "Fixed vertical pole that may affect navigation or line of sight."
    }
  ]
}
```

Allowed categories:

- `drone`
- `airplane`
- `person`
- `static_obstacle`

Useful type examples:

- Drone: `quadrotor`, `hexacopter`, `fixed_wing_uav`, `fpv_drone`, `large_multirotor`, `unknown_drone`
- Airplane: `commercial_airliner`, `small_propeller_aircraft`, `jet_aircraft`, `military_aircraft`, `glider`, `unknown_airplane`
- Person: `standing_person`, `walking_person`, `running_person`, `crouching_person`, `group_of_people`, `unknown_person`
- Static obstacle: `building`, `tower`, `pole`, `wire`, `tree`, `fence`, `parked_vehicle`, `ground_structure`, `unknown_static_obstacle`

## Offline Checks

```powershell
py -3 -m py_compile prototype\vla_drone_detector\*.py
py -3 -m pytest prototype\vla_drone_detector\test_vla_drone_detector.py
py -3 prototype\vla_drone_detector\vla_drone_detector.py --help
py -3 prototype\vla_drone_detector\vla_drone_detector.py image --help
py -3 prototype\vla_drone_detector\vla_drone_detector.py video --help
```
