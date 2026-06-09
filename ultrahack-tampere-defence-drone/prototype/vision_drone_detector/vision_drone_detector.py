"""Vision UAV detector CLI and reusable helpers."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


DEFAULT_MODEL_PATH = Path(__file__).with_name("models") / "best.pt"
DEFAULT_OUTPUT_ROOT = Path(__file__).with_name("outputs")


@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int
    class_name: str

    def to_json_dict(self) -> dict[str, Any]:
        record = asdict(self)
        for key in ("x1", "y1", "x2", "y2", "confidence"):
            record[key] = round(float(record[key]), 6)
        record["class_id"] = int(record["class_id"])
        return record


def filter_detections(detections: list[Detection], confidence_threshold: float) -> list[Detection]:
    return [
        detection
        for detection in detections
        if float(detection.confidence) >= float(confidence_threshold)
    ]


def serialize_image_result(
    source: str,
    model: str,
    confidence_threshold: float,
    image_width: int,
    image_height: int,
    detections: list[Detection],
) -> dict[str, Any]:
    return {
        "source": source,
        "model": model,
        "confidence_threshold": float(confidence_threshold),
        "image_width": int(image_width),
        "image_height": int(image_height),
        "detections": [detection.to_json_dict() for detection in detections],
    }


def serialize_video_frame_result(
    source: str,
    model: str,
    confidence_threshold: float,
    frame_index: int,
    timestamp_s: float,
    image_width: int,
    image_height: int,
    detections: list[Detection],
) -> dict[str, Any]:
    record = serialize_image_result(
        source,
        model,
        confidence_threshold,
        image_width,
        image_height,
        detections,
    )
    record["frame_index"] = int(frame_index)
    record["timestamp_s"] = round(float(timestamp_s), 6)
    return record


def draw_boxes(image: Image.Image, detections: list[Detection]) -> Image.Image:
    annotated = image.convert("RGB").copy()
    draw = ImageDraw.Draw(annotated)
    font = ImageFont.load_default()
    line_width = max(2, int(round(min(annotated.size) / 250)))

    for detection in detections:
        box = [detection.x1, detection.y1, detection.x2, detection.y2]
        label = f"{detection.class_name} {detection.confidence:.2f}"
        draw.rectangle(box, outline=(255, 60, 40), width=line_width)
        label_box = draw.textbbox((0, 0), label, font=font)
        label_w = label_box[2] - label_box[0]
        label_h = label_box[3] - label_box[1]
        label_x = max(0, int(detection.x1))
        label_y = max(0, int(detection.y1) - label_h - 6)
        draw.rectangle(
            [label_x, label_y, label_x + label_w + 6, label_y + label_h + 4],
            fill=(255, 60, 40),
        )
        draw.text((label_x + 3, label_y + 2), label, fill=(255, 255, 255), font=font)
    return annotated


def load_model(model_path: Path) -> Any:
    if not model_path.exists():
        raise FileNotFoundError(
            f"Model not found: {model_path}. Run download_anti_uav_model.ps1 first."
        )
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError(
            "Install requirements-vision-drone-detector.txt before running inference."
        ) from exc
    return YOLO(str(model_path))


def yolo_result_to_detections(result: Any, confidence_threshold: float) -> list[Detection]:
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []

    xyxy = boxes.xyxy.detach().cpu().numpy()
    confs = boxes.conf.detach().cpu().numpy()
    class_ids = boxes.cls.detach().cpu().numpy().astype(int)
    names = getattr(result, "names", {}) or {}
    detections: list[Detection] = []

    for coords, confidence, class_id in zip(xyxy, confs, class_ids):
        class_name = resolve_class_name(names, int(class_id))
        detections.append(
            Detection(
                x1=float(coords[0]),
                y1=float(coords[1]),
                x2=float(coords[2]),
                y2=float(coords[3]),
                confidence=float(confidence),
                class_id=int(class_id),
                class_name=class_name,
            )
        )
    return filter_detections(detections, confidence_threshold)


def resolve_class_name(names: Any, class_id: int) -> str:
    if isinstance(names, dict):
        return str(names.get(class_id, class_id))
    if isinstance(names, list) and 0 <= class_id < len(names):
        return str(names[class_id])
    return str(class_id)


def make_output_dir(out_dir: str | None) -> Path:
    if out_dir:
        path = Path(out_dir)
    else:
        path = DEFAULT_OUTPUT_ROOT / time.strftime("run_%Y%m%d_%H%M%S")
    path.mkdir(parents=True, exist_ok=True)
    return path


def infer_image(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.path)
    model_path = Path(args.model)
    out_dir = make_output_dir(args.out_dir)
    model = load_model(model_path)

    results = model.predict(
        source=str(source),
        conf=float(args.conf),
        imgsz=int(args.imgsz),
        device=args.device,
        verbose=False,
    )
    result = results[0]
    image = Image.open(source).convert("RGB")
    detections = yolo_result_to_detections(result, float(args.conf))
    annotated = draw_boxes(image, detections)

    annotated_path = out_dir / f"{source.stem}_annotated.png"
    json_path = out_dir / "detections.json"
    annotated.save(annotated_path)
    record = serialize_image_result(
        source=str(source),
        model=str(model_path),
        confidence_threshold=float(args.conf),
        image_width=image.width,
        image_height=image.height,
        detections=detections,
    )
    json_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    return {
        "mode": "image",
        "annotated_path": str(annotated_path),
        "json_path": str(json_path),
        "detection_count": len(detections),
        **record,
    }


def infer_video(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Install opencv-python before running video inference.") from exc

    source = Path(args.path)
    model_path = Path(args.model)
    out_dir = make_output_dir(args.out_dir)
    model = load_model(model_path)

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {source}")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        capture.release()
        raise RuntimeError(f"Could not read video dimensions: {source}")

    annotated_path = out_dir / f"{source.stem}_annotated.mp4"
    jsonl_path = out_dir / "detections.jsonl"
    writer = cv2.VideoWriter(
        str(annotated_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"Could not create video writer: {annotated_path}")

    frame_index = 0
    total_detections = 0
    try:
        with jsonl_path.open("w", encoding="utf-8") as handle:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                results = model.predict(
                    source=frame,
                    conf=float(args.conf),
                    imgsz=int(args.imgsz),
                    device=args.device,
                    verbose=False,
                )
                detections = yolo_result_to_detections(results[0], float(args.conf))
                total_detections += len(detections)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                annotated = draw_boxes(Image.fromarray(rgb), detections)
                writer.write(cv2.cvtColor(np.array(annotated), cv2.COLOR_RGB2BGR))
                record = serialize_video_frame_result(
                    source=str(source),
                    model=str(model_path),
                    confidence_threshold=float(args.conf),
                    frame_index=frame_index,
                    timestamp_s=frame_index / max(fps, 0.001),
                    image_width=width,
                    image_height=height,
                    detections=detections,
                )
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
                frame_index += 1
    finally:
        capture.release()
        writer.release()

    if frame_index == 0:
        raise RuntimeError(f"No frames were read from video: {source}")

    return {
        "mode": "video",
        "source": str(source),
        "model": str(model_path),
        "confidence_threshold": float(args.conf),
        "image_width": width,
        "image_height": height,
        "frame_count": frame_index,
        "detection_count": total_detections,
        "annotated_path": str(annotated_path),
        "jsonl_path": str(jsonl_path),
    }


def add_common_options(parser: argparse.ArgumentParser, duplicate: bool = False) -> None:
    default = argparse.SUPPRESS if duplicate else None
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH) if not duplicate else default)
    parser.add_argument("--conf", type=float, default=0.25 if not duplicate else default)
    parser.add_argument("--imgsz", type=int, default=640 if not duplicate else default)
    parser.add_argument("--device", default="cpu" if not duplicate else default)
    parser.add_argument("--out-dir", default=default)
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if not duplicate else default,
        help="Print machine-readable run summary.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run UAV object detection on images or videos.")
    add_common_options(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    image_parser = subparsers.add_parser("image", help="Detect UAVs in one image.")
    image_parser.add_argument("path")
    add_common_options(image_parser, duplicate=True)

    video_parser = subparsers.add_parser("video", help="Detect UAVs in one video.")
    video_parser.add_argument("path")
    add_common_options(video_parser, duplicate=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "image":
        summary = infer_image(args)
    elif args.command == "video":
        summary = infer_video(args)
    else:
        raise AssertionError(args.command)

    if args.json:
        print(json.dumps(summary, separators=(",", ":")))
    else:
        print(
            f"{args.command} detections={summary['detection_count']} "
            f"annotated={summary['annotated_path']}"
        )


if __name__ == "__main__":
    main()
