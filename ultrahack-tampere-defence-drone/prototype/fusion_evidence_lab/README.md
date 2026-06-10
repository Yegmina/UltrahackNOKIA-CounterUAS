# Fusion Evidence Lab

Standalone prototype for combining the hackathon sensors into one drone/aircraft evidence timeline.

It accepts a data collection archive from `prototype/data_collection_firmware`, extracts camera/audio streams, adds optional external videos, estimates alignment, applies optional perspective correction, and produces proof artifacts:

- `fusion_timeline.json`
- `events.json`
- `sync_report.json`
- `evidence_index.json`
- annotated screenshots with boxes
- motion-only mask screenshots
- audio proof images

The frontend shows when the fused system thinks a drone/aircraft event happened and the evidence that caused that decision.

## Run UI

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\fusion_evidence_lab\run_fusion_evidence_ui.ps1
```

Open:

```text
http://127.0.0.1:8511
```

The UI is configured for uploads up to `8192 MB` per file. For very large local files, the path inputs are still faster than browser upload.

## CLI

```powershell
py -3 prototype\fusion_evidence_lab\fusion_evidence_lab.py analyze `
  --archive "C:\path\to\data_collection_full_recording.zip" `
  --extra-video "C:\Users\teres\Downloads\Telegram Desktop\VID_20260610_124556_051.mp4" `
  --out-dir prototype\fusion_evidence_lab\outputs\run `
  --json
```

Fast smoke run:

```powershell
py -3 prototype\fusion_evidence_lab\fusion_evidence_lab.py analyze `
  --archive "C:\path\to\data_collection_full_recording.zip" `
  --extra-video "C:\Users\teres\Downloads\Telegram Desktop\VID_20260610_124556_051.mp4" `
  --max-frames 240 `
  --sample-every 12 `
  --out-dir prototype\fusion_evidence_lab\outputs\smoke `
  --json
```

Inspect inputs without running analysis:

```powershell
py -3 prototype\fusion_evidence_lab\fusion_evidence_lab.py inspect `
  --archive "C:\path\to\data_collection_full_recording.zip" `
  --extra-video "C:\path\to\phone_video.mp4" `
  --json
```

## Fusion Approach

1. Parse the recording archive manifest and timing JSONL files.
2. Extract synchronized camera/audio files into a run cache.
3. Analyze camera and extra-video frames using fixed-camera motion differencing.
4. Score audio windows using RMS plus drone-like band energy.
5. Estimate extra-video sync from embedded creation time, then refine with motion correlation where overlap exists.
6. Bin all evidence on one session-relative UTC timeline.
7. Boost fused confidence when multiple sensors agree in the same time bin.
8. Save event windows with proof artifacts for review.

## Perspective Correction

Optional perspective correction uses a JSON object. Points can be normalized `0..1` or absolute pixels.

```json
{
  "sources": {
    "demo1": {
      "src": [[0.1, 0.2], [0.9, 0.2], [0.95, 0.9], [0.05, 0.9]],
      "dst": [[0, 0], [1, 0], [1, 1], [0, 1]]
    }
  }
}
```

Source keys match stream slugs such as `demo1`, `hp_wide_vision_hd_camera`, or an extra-video slug.

## Detector Imports

Future object detector outputs can be added as JSON or JSONL using `--detector-json`. The importer accepts records with `detections` lists containing confidence and boxes. Imported detections become another fused evidence source.

## Notes

- The UI/CLI do not require an AI API key.
- Provider-backed visual reasoning can be added later by importing its JSON output as another evidence stream.
- Outputs are intentionally ignored by git.
