"""Annotate videos with a pretrained flying-object detector."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2


DEFAULT_MODEL_REPO = "devanshty/WingID"
DEFAULT_MODEL_FILE = "yolo11l.pt"
DEFAULT_OUTPUT_ROOT = Path(__file__).with_name("outputs") / "flying_objects"
DEFAULT_LABELS = ("bird", "drone", "airplane", "helicopter")
LABEL_ALIASES = {
    "human": "person",
    "person": "person",
    "bird": "bird",
    "птица": "bird",
    "drone": "drone",
    "uav": "drone",
    "бпла": "drone",
    "бпла коптер": "drone",
    "бпла самелет": "drone",
    "бпла самолет": "drone",
    "airplane": "airplane",
    "aeroplane": "airplane",
    "plane": "airplane",
    "самолет": "airplane",
    "самелет": "airplane",
    "helicopter": "helicopter",
    "вертолет": "helicopter",
}
LABEL_COLORS = {
    "person": (80, 255, 80),
    "bird": (40, 220, 255),
    "drone": (40, 255, 40),
    "airplane": (255, 180, 40),
    "helicopter": (255, 80, 220),
}


@dataclass(frozen=True)
class AiDetection:
    label: str
    raw_label: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    def to_json_dict(self) -> dict[str, Any]:
        record = asdict(self)
        for key, value in record.items():
            if isinstance(value, float):
                record[key] = round(value, 6)
        return record


def import_ai_dependencies():
    try:
        from huggingface_hub import hf_hub_download
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "Missing AI dependencies. Install them with:\n"
            "python3 -m pip install ultralytics huggingface_hub"
        ) from exc
    return hf_hub_download, YOLO


def parse_labels(labels: str | None) -> set[str]:
    if labels is None:
        return set(DEFAULT_LABELS)
    parsed = {canonical_label(part) for part in labels.split(",") if part.strip()}
    parsed.discard("")
    return parsed or set(DEFAULT_LABELS)


def canonical_label(label: str) -> str:
    normalized = label.strip().lower()
    return LABEL_ALIASES.get(normalized, normalized)


def model_label_name(names: Any, class_id: int) -> str:
    if isinstance(names, dict):
        return str(names.get(class_id, class_id))
    if isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
        return str(names[class_id])
    return str(class_id)


def result_to_detections(result: Any, labels: set[str]) -> list[AiDetection]:
    detections: list[AiDetection] = []
    names = getattr(result, "names", {})
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return detections

    for box in boxes:
        class_id = int(box.cls[0].item())
        label = model_label_name(names, class_id)
        normalized_label = canonical_label(label)
        if normalized_label == "background" or normalized_label not in labels:
            continue
        confidence = float(box.conf[0].item())
        x1, y1, x2, y2 = [float(value) for value in box.xyxy[0].tolist()]
        detections.append(
            AiDetection(
                label=normalized_label,
                raw_label=label,
                confidence=confidence,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
            )
        )
    detections.sort(key=lambda detection: detection.confidence, reverse=True)
    return detections


def draw_detections(frame, detections: list[AiDetection], frame_index: int, inference_frame: bool) -> None:
    height, width = frame.shape[:2]
    thickness = max(2, round(min(width, height) / 420))
    font_scale = max(0.55, min(width, height) / 1350)
    font_thickness = max(1, thickness - 1)

    label_counts: dict[str, int] = {}
    for detection in detections:
        normalized_label = detection.label.lower()
        label_counts[normalized_label] = label_counts.get(normalized_label, 0) + 1
        color = LABEL_COLORS.get(normalized_label, (255, 255, 80))
        x1 = int(max(0, min(width - 1, round(detection.x1))))
        y1 = int(max(0, min(height - 1, round(detection.y1))))
        x2 = int(max(0, min(width - 1, round(detection.x2))))
        y2 = int(max(0, min(height - 1, round(detection.y2))))
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

        text = f"{detection.label.title()} {detection.confidence:.2f}"
        (text_w, text_h), baseline = cv2.getTextSize(
            text,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            font_thickness,
        )
        label_y = max(text_h + baseline + 4, y1)
        cv2.rectangle(
            frame,
            (x1, label_y - text_h - baseline - 6),
            (min(width - 1, x1 + text_w + 8), label_y + 2),
            color,
            thickness=cv2.FILLED,
        )
        cv2.putText(
            frame,
            text,
            (x1 + 4, label_y - baseline - 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            font_thickness,
            cv2.LINE_AA,
        )

    summary = " ".join(f"{label}={count}" for label, count in sorted(label_counts.items()))
    if not summary:
        summary = "no flying-object detections"
    status = "AI frame" if inference_frame else "held boxes"
    text = f"frame={frame_index} {status} {summary}"
    cv2.rectangle(frame, (8, 8), (min(width - 1, 8 + len(text) * 13), 42), (0, 0, 0), cv2.FILLED)
    cv2.putText(frame, text, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)


def ensure_output_path(video_path: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{video_path.stem}_flying_objects.mp4"


def annotate_video(args: argparse.Namespace) -> dict[str, Any]:
    hf_hub_download, YOLO = import_ai_dependencies()

    video_path = Path(args.video)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    weights_path = Path(args.weights) if args.weights else None
    if weights_path is None:
        weights_path = Path(
            hf_hub_download(
                repo_id=args.model_repo,
                filename=args.model_file,
            )
        )

    labels = parse_labels(args.labels)
    model = YOLO(str(weights_path))

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    out_dir = Path(args.out_dir)
    output_path = Path(args.output) if args.output else ensure_output_path(video_path, out_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_path.with_suffix(".jsonl")
    summary_path = output_path.with_name(f"{output_path.stem}_summary.json")

    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"Could not create output video: {output_path}")

    frame_stride = max(1, int(args.frame_stride))
    max_frames = None if args.max_frames is None else max(1, int(args.max_frames))
    counts_by_label: dict[str, int] = {}
    frames_with_detections = 0
    inference_frames = 0
    last_detections: list[AiDetection] = []
    start_time = time.time()

    with jsonl_path.open("w", encoding="utf-8") as jsonl:
        frame_index = 0
        while True:
            if max_frames is not None and frame_index >= max_frames:
                break
            ok, frame = cap.read()
            if not ok:
                break

            inference_frame = frame_index % frame_stride == 0
            if inference_frame:
                results = model.predict(
                    frame,
                    conf=args.conf,
                    iou=args.iou,
                    imgsz=args.imgsz,
                    device=args.device,
                    verbose=False,
                )
                last_detections = result_to_detections(results[0], labels)
                inference_frames += 1
                if last_detections:
                    frames_with_detections += 1
                for detection in last_detections:
                    normalized_label = detection.label.lower()
                    counts_by_label[normalized_label] = counts_by_label.get(normalized_label, 0) + 1
                jsonl.write(
                    json.dumps(
                        {
                            "frame_index": frame_index,
                            "timestamp_s": round(frame_index / fps, 6),
                            "detections": [detection.to_json_dict() for detection in last_detections],
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )

            draw_detections(frame, last_detections, frame_index, inference_frame)
            writer.write(frame)
            frame_index += 1

    cap.release()
    writer.release()

    summary = {
        "source": str(video_path),
        "output_path": str(output_path),
        "jsonl_path": str(jsonl_path),
        "summary_path": str(summary_path),
        "model_repo": args.model_repo,
        "model_file": args.model_file,
        "weights_path": str(weights_path),
        "labels": sorted(labels),
        "confidence_threshold": args.conf,
        "iou_threshold": args.iou,
        "image_size": args.imgsz,
        "device": args.device,
        "frame_stride": frame_stride,
        "frame_count": frame_count,
        "processed_frames": frame_index,
        "inference_frames": inference_frames,
        "frames_with_detections": frames_with_detections,
        "counts_by_label": counts_by_label,
        "duration_s": round(frame_index / fps, 6) if fps else None,
        "elapsed_s": round(time.time() - start_time, 3),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a pretrained flying-object detector over a video and draw boxes.",
    )
    parser.add_argument("video", help="Input video path.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_ROOT), help="Directory for outputs.")
    parser.add_argument("--output", default=None, help="Optional exact output MP4 path.")
    parser.add_argument("--weights", default=None, help="Optional local .pt weights path.")
    parser.add_argument("--model-repo", default=DEFAULT_MODEL_REPO)
    parser.add_argument("--model-file", default=DEFAULT_MODEL_FILE)
    parser.add_argument("--labels", default=",".join(DEFAULT_LABELS), help="Comma-separated labels to draw.")
    parser.add_argument("--conf", type=float, default=0.18, help="YOLO confidence threshold.")
    parser.add_argument("--iou", type=float, default=0.50, help="YOLO IoU threshold.")
    parser.add_argument("--imgsz", type=int, default=960, help="YOLO inference image size.")
    parser.add_argument("--device", default=None, help="Ultralytics device, e.g. cpu, mps, 0.")
    parser.add_argument("--frame-stride", type=int, default=2, help="Run AI every N frames and hold boxes between.")
    parser.add_argument("--max-frames", type=int, default=None, help="Optional limit for quick tests.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        summary = annotate_video(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(summary, separators=(",", ":")))
    else:
        print(
            f"processed_frames={summary['processed_frames']} "
            f"inference_frames={summary['inference_frames']} "
            f"frames_with_detections={summary['frames_with_detections']} "
            f"counts={summary['counts_by_label']} "
            f"output={summary['output_path']}"
        )


if __name__ == "__main__":
    main()
