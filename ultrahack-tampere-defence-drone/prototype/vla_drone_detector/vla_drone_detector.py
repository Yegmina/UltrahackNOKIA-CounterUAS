"""Image and video runner for a custom edge computing VLA detector."""

from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


DEFAULT_MODEL_ID = ""
MODEL_DISPLAY_NAME = "custom edge computing VLA model"
DEFAULT_OUTPUT_ROOT = Path(__file__).with_name("outputs")
PROMPT_TYPES = ("thermal_counter_uas", "visible_daylight", "low_light_or_noisy", "custom")
THERMAL_POLARITIES = ("black_is_warm", "white_is_warm", "visible_rgb")
DRONE_TYPES = (
    "quadrotor",
    "hexacopter",
    "fixed_wing_uav",
    "fpv_drone",
    "large_multirotor",
    "unknown_drone",
)
AIRPLANE_TYPES = (
    "commercial_airliner",
    "small_propeller_aircraft",
    "jet_aircraft",
    "military_aircraft",
    "glider",
    "unknown_airplane",
)


@dataclass(frozen=True)
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    center_x: float
    center_y: float
    confidence: float
    category: str
    type: str
    thermal_signature: str
    rationale: str

    def to_json_dict(self) -> dict[str, Any]:
        record = asdict(self)
        for key in ("x1", "y1", "x2", "y2", "center_x", "center_y", "confidence"):
            record[key] = round(float(record[key]), 6)
        return record


def candidate_env_paths() -> list[Path]:
    module_path = Path(__file__).resolve()
    repo_root = module_path.parents[2]
    workspace_dir = repo_root.parent
    candidates = [
        Path.cwd() / ".env",
        module_path.with_name(".env"),
        repo_root / ".env",
    ]

    if "-" in workspace_dir.name:
        base_workspace = workspace_dir.with_name(workspace_dir.name.split("-", 1)[0])
        candidates.extend(
            [
                base_workspace / repo_root.name / ".env",
                base_workspace / repo_root.name / "prototype" / "vla_drone_detector" / ".env",
            ]
        )

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def load_local_env() -> None:
    """Load a nearby .env file when python-dotenv is installed."""

    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return

    for candidate in candidate_env_paths():
        if candidate.exists():
            load_dotenv(candidate, override=False)

    found = find_dotenv(usecwd=True)
    if found:
        load_dotenv(found, override=False)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_token(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    token = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return token or fallback


def parse_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if confidence > 1.0 and confidence <= 100.0:
        confidence /= 100.0
    return clamp(confidence, 0.0, 1.0)


def safe_json_loads(text: str) -> Any:
    """Parse clean JSON, fenced JSON, or JSON surrounded by extra prose."""

    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE):
        fenced = match.group(1).strip()
        if fenced:
            candidates.insert(0, fenced)

    block = extract_first_json_block(text)
    if block:
        candidates.append(block)

    errors: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
    raise ValueError("No valid JSON object or array found in model response. " + "; ".join(errors))


def extract_first_json_block(text: str) -> str | None:
    start_positions = [idx for idx, char in enumerate(text) if char in "[{"]
    for start in start_positions:
        stack: list[str] = []
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                stack.append("}")
            elif char == "[":
                stack.append("]")
            elif char in "}]":
                if not stack or stack[-1] != char:
                    break
                stack.pop()
                if not stack:
                    return text[start : index + 1]
    return None


def get_raw_detections(parsed: Any) -> list[Any]:
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, dict):
        return []
    for key in ("detections", "objects", "targets"):
        value = parsed.get(key)
        if isinstance(value, list):
            return value
    return []


def normalized_box_to_pixels(
    box: list[Any] | tuple[Any, ...], image_width: int, image_height: int
) -> tuple[float, float, float, float]:
    if len(box) != 4:
        raise ValueError("box must have four values in [ymin, xmin, ymax, xmax] order")

    coords = [float(value) for value in box]
    if max(abs(value) for value in coords) <= 1.5:
        coords = [value * 1000.0 for value in coords]

    ymin, xmin, ymax, xmax = [clamp(value, 0.0, 1000.0) for value in coords]
    if ymax < ymin:
        ymin, ymax = ymax, ymin
    if xmax < xmin:
        xmin, xmax = xmax, xmin

    x1 = xmin / 1000.0 * float(image_width)
    y1 = ymin / 1000.0 * float(image_height)
    x2 = xmax / 1000.0 * float(image_width)
    y2 = ymax / 1000.0 * float(image_height)
    return x1, y1, x2, y2


def detection_from_raw(raw: Any, image_width: int, image_height: int) -> Detection | None:
    if not isinstance(raw, dict):
        return None

    box = raw.get("box_2d") or raw.get("bbox") or raw.get("box")
    if not isinstance(box, (list, tuple)):
        return None

    try:
        x1, y1, x2, y2 = normalized_box_to_pixels(box, image_width, image_height)
    except (TypeError, ValueError):
        return None

    if x2 - x1 <= 0.5 or y2 - y1 <= 0.5:
        return None

    confidence = parse_confidence(raw.get("confidence", raw.get("score", raw.get("probability"))))
    category = normalize_token(raw.get("category", raw.get("label")), "unknown")
    if category in {"uav", "uas", "drone_like"}:
        category = "drone"
    elif category in {"aircraft", "plane", "airplane_like", "aeroplane"}:
        category = "airplane"
    if category not in {"drone", "airplane"}:
        return None

    default_type = "unknown_drone" if category == "drone" else "unknown_airplane"
    object_type = normalize_token(raw.get("type", raw.get("class_name", raw.get("class"))), default_type)
    allowed_types = DRONE_TYPES if category == "drone" else AIRPLANE_TYPES
    if object_type not in allowed_types:
        object_type = default_type

    center_x = (x1 + x2) / 2.0
    center_y = (y1 + y2) / 2.0
    return Detection(
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        center_x=center_x,
        center_y=center_y,
        confidence=confidence,
        category=category,
        type=object_type,
        thermal_signature=str(raw.get("thermal_signature", "unknown")).strip() or "unknown",
        rationale=str(raw.get("rationale", "")).strip(),
    )


def filter_detections(
    detections: list[Detection], confidence_threshold: float
) -> list[Detection]:
    return [
        detection
        for detection in detections
        if float(detection.confidence) >= float(confidence_threshold)
    ]


def detections_from_response(
    response_text: str, image_width: int, image_height: int, confidence_threshold: float
) -> list[Detection]:
    parsed = safe_json_loads(response_text)
    detections: list[Detection] = []
    for raw in get_raw_detections(parsed):
        detection = detection_from_raw(raw, image_width, image_height)
        if detection is not None:
            detections.append(detection)
    return filter_detections(detections, confidence_threshold)


def serialize_result(
    source: str,
    model: str,
    prompt_type: str,
    thermal_polarity: str,
    confidence_threshold: float,
    image_width: int,
    image_height: int,
    detections: list[Detection],
    frame_index: int = 0,
    timestamp_s: float = 0.0,
) -> dict[str, Any]:
    return {
        "source": source,
        "frame_index": int(frame_index),
        "timestamp_s": round(float(timestamp_s), 6),
        "model": model or MODEL_DISPLAY_NAME,
        "prompt_type": prompt_type,
        "thermal_polarity": thermal_polarity,
        "confidence_threshold": float(confidence_threshold),
        "image_width": int(image_width),
        "image_height": int(image_height),
        "detections": [detection.to_json_dict() for detection in detections],
    }


def draw_boxes(image: Image.Image, detections: list[Detection]) -> Image.Image:
    annotated = image.convert("RGB").copy()
    draw = ImageDraw.Draw(annotated)
    font = ImageFont.load_default()
    line_width = max(2, int(round(min(annotated.size) / 240)))

    for detection in detections:
        color = (0, 190, 255) if detection.category == "drone" else (255, 176, 0)
        box = [
            int(round(detection.x1)),
            int(round(detection.y1)),
            int(round(detection.x2)),
            int(round(detection.y2)),
        ]
        label = f"{detection.category}:{detection.type} {detection.confidence:.2f}"
        draw.rectangle(box, outline=color, width=line_width)
        label_bounds = draw.textbbox((0, 0), label, font=font)
        label_w = label_bounds[2] - label_bounds[0]
        label_h = label_bounds[3] - label_bounds[1]
        label_x = max(0, box[0])
        label_y = max(0, box[1] - label_h - 6)
        label_rect = [label_x, label_y, label_x + label_w + 6, label_y + label_h + 4]
        draw.rectangle(label_rect, fill=color)
        draw.text((label_x + 3, label_y + 2), label, fill=(0, 0, 0), font=font)
    return annotated


def build_prompt(
    prompt_type: str,
    thermal_polarity: str,
    custom_prompt: str | None = None,
) -> str:
    if prompt_type not in PROMPT_TYPES:
        raise ValueError(f"Unsupported prompt type: {prompt_type}")
    if thermal_polarity not in THERMAL_POLARITIES:
        raise ValueError(f"Unsupported thermal polarity: {thermal_polarity}")

    if thermal_polarity == "black_is_warm":
        polarity_text = (
            "The image or video frame is black-white thermal-like imagery: black means warm, "
            "white means cold. Use that polarity when describing thermal signatures."
        )
    elif thermal_polarity == "white_is_warm":
        polarity_text = (
            "The image or video frame is black-white thermal-like imagery: white means warm, "
            "black means cold. Use that polarity when describing thermal signatures."
        )
    else:
        polarity_text = (
            "The image or video frame is visible RGB imagery. Do not infer heat from color; "
            "set thermal_signature to visible_rgb unless a clear heat-like cue is present."
        )

    preset_text = {
        "thermal_counter_uas": (
            "Perform a counter-UAS scan for small airborne objects in thermal-like imagery. "
            "Look for compact bright or dark silhouettes, rotor-arm shapes, fixed-wing UAV "
            "outlines, and aircraft shapes against the sky or background."
        ),
        "visible_daylight": (
            "Perform a visible daylight scan for airborne drones and airplanes. Use shape, "
            "scale, wing/rotor structure, blur trails, and sky/background separation."
        ),
        "low_light_or_noisy": (
            "Perform a tolerant scan for blurry, noisy, dim, compressed, low-resolution, or "
            "partially occluded airborne drones and airplanes. Lower visual certainty should "
            "be reflected as lower confidence."
        ),
        "custom": (
            "Follow the fixed task and JSON schema below, then apply the custom operator "
            "instructions after the schema."
        ),
    }[prompt_type]

    custom_text = (custom_prompt or "").strip()
    if prompt_type == "custom" and not custom_text:
        custom_text = "No additional custom instructions."
    elif custom_text:
        custom_text = f"Additional operator instructions: {custom_text}"
    else:
        custom_text = "No additional operator instructions."

    drone_types = ", ".join(DRONE_TYPES)
    airplane_types = ", ".join(AIRPLANE_TYPES)

    return f"""
You are a custom edge computing VLA model used for visual target reasoning.
Detect drones and airplanes only. Ignore birds, insects, clouds, trees, vehicles, people, buildings, and sensor artifacts unless the evidence strongly supports drone or airplane.

{polarity_text}
{preset_text}

Allowed drone types: {drone_types}.
Allowed airplane types: {airplane_types}.

Return JSON only, with no prose before or after it. Use this exact top-level shape:
{{
  "detections": [
    {{
      "box_2d": [ymin, xmin, ymax, xmax],
      "confidence": 0.0,
      "category": "drone",
      "type": "quadrotor",
      "thermal_signature": "short description",
      "rationale": "short reason"
    }}
  ]
}}

Rules:
- box_2d must be normalized 0..1000 integer or float coordinates in [ymin, xmin, ymax, xmax] order.
- category must be exactly "drone" or "airplane".
- type must be one allowed type for the category. Use unknown_drone or unknown_airplane when type is unclear.
- confidence must be a calibrated 0.0..1.0 probability.
- Include only detections for drones and airplanes.
- If no drones or airplanes are visible, return {{"detections": []}}.

{custom_text}
""".strip()


def pil_image_to_jpeg_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=92)
    return output.getvalue()


def load_api_client() -> Any:
    load_local_env()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Detector credentials were not loaded from .env or the environment.")

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "Install requirements-vla-drone-detector.txt before running inference."
        ) from exc

    return genai.Client(api_key=api_key)


def call_model(image_bytes: bytes, mime_type: str, prompt: str, model: str) -> str:
    try:
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "Install requirements-vla-drone-detector.txt before running inference."
        ) from exc

    client = load_api_client()
    config_kwargs: dict[str, Any] = {
        "temperature": 0.1,
        "response_mime_type": "application/json",
    }
    if hasattr(types, "ThinkingConfig"):
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(**config_kwargs),
    )
    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("The model returned an empty response.")
    return str(text)


def analyze_pil_image(
    image: Image.Image,
    model: str,
    confidence_threshold: float,
    prompt_type: str,
    thermal_polarity: str,
    custom_prompt: str | None = None,
) -> list[Detection]:
    prompt = build_prompt(prompt_type, thermal_polarity, custom_prompt)
    response_text = call_model(
        pil_image_to_jpeg_bytes(image),
        "image/jpeg",
        prompt,
        model,
    )
    return detections_from_response(
        response_text,
        image_width=image.width,
        image_height=image.height,
        confidence_threshold=confidence_threshold,
    )


def make_output_dir(out_dir: str | None) -> Path:
    if out_dir:
        path = Path(out_dir)
    else:
        path = DEFAULT_OUTPUT_ROOT / time.strftime("run_%Y%m%d_%H%M%S")
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_model_id(value: str | None) -> str:
    load_local_env()
    candidate = (value or "").strip() or os.environ.get("VLA_MODEL", "").strip()
    candidate = candidate or DEFAULT_MODEL_ID
    if not candidate:
        raise RuntimeError(
            "Set VLA_MODEL in .env or pass --model for the custom edge computing VLA model."
        )
    return candidate


def infer_image(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.path)
    out_dir = make_output_dir(args.out_dir)
    model = resolve_model_id(args.model)

    image = Image.open(source).convert("RGB")
    detections = analyze_pil_image(
        image=image,
        model=model,
        confidence_threshold=float(args.conf),
        prompt_type=args.prompt_type,
        thermal_polarity=args.thermal_polarity,
        custom_prompt=args.custom_prompt,
    )
    annotated = draw_boxes(image, detections)

    annotated_path = out_dir / f"{source.stem}_annotated.png"
    json_path = out_dir / "detections.json"
    annotated.save(annotated_path)
    record = serialize_result(
        source=str(source),
        model=model,
        prompt_type=args.prompt_type,
        thermal_polarity=args.thermal_polarity,
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
    out_dir = make_output_dir(args.out_dir)
    model = resolve_model_id(args.model)

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {source}")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        capture.release()
        raise RuntimeError(f"Could not read video dimensions: {source}")

    sample_fps = max(float(args.sample_fps), 0.001)
    sample_every = max(1, int(round(fps / sample_fps)))
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
    sampled_frame_count = 0
    total_sampled_detections = 0
    error_count = 0
    latest_detections: list[Detection] = []

    try:
        with jsonl_path.open("w", encoding="utf-8") as handle:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break

                timestamp_s = frame_index / max(fps, 0.001)
                if frame_index % sample_every == 0:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    image = Image.fromarray(rgb)
                    sampled_frame_count += 1
                    record = serialize_result(
                        source=str(source),
                        model=model,
                        prompt_type=args.prompt_type,
                        thermal_polarity=args.thermal_polarity,
                        confidence_threshold=float(args.conf),
                        image_width=width,
                        image_height=height,
                        detections=[],
                        frame_index=frame_index,
                        timestamp_s=timestamp_s,
                    )
                    try:
                        latest_detections = analyze_pil_image(
                            image=image,
                            model=model,
                            confidence_threshold=float(args.conf),
                            prompt_type=args.prompt_type,
                            thermal_polarity=args.thermal_polarity,
                            custom_prompt=args.custom_prompt,
                        )
                        total_sampled_detections += len(latest_detections)
                        record["detections"] = [
                            detection.to_json_dict() for detection in latest_detections
                        ]
                    except Exception as exc:
                        error_count += 1
                        record["error"] = f"{type(exc).__name__}: {exc}"
                    handle.write(json.dumps(record, separators=(",", ":")) + "\n")

                rgb_for_draw = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                annotated = draw_boxes(Image.fromarray(rgb_for_draw), latest_detections)
                writer.write(cv2.cvtColor(np.array(annotated), cv2.COLOR_RGB2BGR))
                frame_index += 1
    finally:
        capture.release()
        writer.release()

    if frame_index == 0:
        raise RuntimeError(f"No frames were read from video: {source}")
    if sampled_frame_count == error_count and sampled_frame_count > 0:
        raise RuntimeError(
            f"All sampled frames failed. See {jsonl_path} for per-frame errors."
        )

    return {
        "mode": "video",
        "source": str(source),
        "model": model,
        "prompt_type": args.prompt_type,
        "thermal_polarity": args.thermal_polarity,
        "confidence_threshold": float(args.conf),
        "image_width": width,
        "image_height": height,
        "frame_count": frame_index,
        "sampled_frame_count": sampled_frame_count,
        "sample_fps": sample_fps,
        "detection_count": total_sampled_detections,
        "error_count": error_count,
        "annotated_path": str(annotated_path),
        "jsonl_path": str(jsonl_path),
    }


def add_common_options(parser: argparse.ArgumentParser, duplicate: bool = False) -> None:
    default = argparse.SUPPRESS if duplicate else None
    parser.add_argument("--conf", type=float, default=0.25 if not duplicate else default)
    parser.add_argument(
        "--prompt-type",
        choices=PROMPT_TYPES,
        default="thermal_counter_uas" if not duplicate else default,
    )
    parser.add_argument(
        "--thermal-polarity",
        choices=THERMAL_POLARITIES,
        default="black_is_warm" if not duplicate else default,
    )
    parser.add_argument("--sample-fps", type=float, default=1.0 if not duplicate else default)
    parser.add_argument("--model", default=default)
    parser.add_argument("--out-dir", default=default)
    parser.add_argument("--custom-prompt", default=default)
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if not duplicate else default,
        help="Print machine-readable run summary.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a custom edge computing VLA scan on images or videos."
    )
    add_common_options(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    image_parser = subparsers.add_parser("image", help="Scan one image.")
    image_parser.add_argument("path")
    add_common_options(image_parser, duplicate=True)

    video_parser = subparsers.add_parser("video", help="Scan one video.")
    video_parser.add_argument("path")
    add_common_options(video_parser, duplicate=True)
    return parser


def main() -> None:
    load_local_env()
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
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
