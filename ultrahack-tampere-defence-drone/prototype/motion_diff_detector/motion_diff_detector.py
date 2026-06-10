"""Fixed-camera frame differencing runner for motion-only drone video previews."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np


DEFAULT_OUTPUT_ROOT = Path(__file__).with_name("outputs")
ROI_ZONE_TYPES = {"ignore", "penalty", "flight"}
ROI_MASK_MODES = {"fixed", "handheld"}


@dataclass(frozen=True)
class MotionConfig:
    diff_threshold: int = 18
    min_area: float = 1000.0
    blur_kernel: int = 5
    morph_kernel: int = 3
    trail_frames: int = 3
    max_motion_ratio: float = 0.10
    analysis_scale: float = 0.5
    shake_protection: bool = True
    shake_min_shift: float = 1.5
    shake_consensus: float = 0.72
    shake_consensus_px: float = 2.0

    def normalized(self) -> "MotionConfig":
        return MotionConfig(
            diff_threshold=int(np.clip(self.diff_threshold, 1, 255)),
            min_area=max(0.0, float(self.min_area)),
            blur_kernel=odd_kernel(self.blur_kernel),
            morph_kernel=odd_kernel(self.morph_kernel),
            trail_frames=max(0, int(self.trail_frames)),
            max_motion_ratio=max(0.0, float(self.max_motion_ratio)),
            analysis_scale=float(np.clip(self.analysis_scale, 0.05, 1.0)),
            shake_protection=bool(self.shake_protection),
            shake_min_shift=max(0.0, float(self.shake_min_shift)),
            shake_consensus=float(np.clip(self.shake_consensus, 0.0, 1.0)),
            shake_consensus_px=max(0.1, float(self.shake_consensus_px)),
        )


@dataclass(frozen=True)
class RoiZone:
    name: str
    type: str
    points: tuple[tuple[float, float], ...]
    penalty: float = 0.0


@dataclass(frozen=True)
class RoiMask:
    version: int
    mode: str
    zones: tuple[RoiZone, ...]


@dataclass(frozen=True)
class MotionDetection:
    x1: float
    y1: float
    x2: float
    y2: float
    center_x: float
    center_y: float
    area: float
    roi_action: str = "keep"
    zone_type: str | None = None
    zone_name: str | None = None
    roi_penalty: float = 0.0

    def to_json_dict(self) -> dict[str, Any]:
        record = asdict(self)
        for key, value in record.items():
            if isinstance(value, float):
                record[key] = round(value, 6)
        return record


@dataclass(frozen=True)
class MotionFrameResult:
    source: str
    frame_index: int
    timestamp_s: float
    image_width: int
    image_height: int
    motion_ratio: float
    global_motion_rejected: bool
    global_motion_detected: bool
    global_dx: float
    global_dy: float
    global_consensus: float
    tracked_vectors: int
    raw_detection_count: int
    roi_rejected_count: int
    roi_penalized_count: int
    detections: list[MotionDetection]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "frame_index": int(self.frame_index),
            "timestamp_s": round(float(self.timestamp_s), 6),
            "image_width": int(self.image_width),
            "image_height": int(self.image_height),
            "motion_ratio": round(float(self.motion_ratio), 8),
            "global_motion_rejected": bool(self.global_motion_rejected),
            "global_motion_detected": bool(self.global_motion_detected),
            "global_dx": round(float(self.global_dx), 6),
            "global_dy": round(float(self.global_dy), 6),
            "global_consensus": round(float(self.global_consensus), 6),
            "tracked_vectors": int(self.tracked_vectors),
            "raw_detection_count": int(self.raw_detection_count),
            "roi_rejected_count": int(self.roi_rejected_count),
            "roi_penalized_count": int(self.roi_penalized_count),
            "detections": [detection.to_json_dict() for detection in self.detections],
        }


@dataclass(frozen=True)
class MotionAnalysis:
    accepted_mask: np.ndarray
    detections: list[MotionDetection]
    motion_ratio: float
    global_motion_rejected: bool
    global_motion_detected: bool
    global_dx: float = 0.0
    global_dy: float = 0.0
    global_consensus: float = 0.0
    tracked_vectors: int = 0
    raw_detection_count: int = 0
    roi_rejected_count: int = 0
    roi_penalized_count: int = 0


def odd_kernel(value: int) -> int:
    kernel = max(1, int(value))
    if kernel % 2 == 0:
        kernel += 1
    return kernel


def normalize_roi_points(points: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(points, (list, tuple)):
        raise ValueError("ROI zone points must be a list of [x, y] pairs.")
    normalized: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("ROI zone points must be [x, y] pairs.")
        x = float(point[0])
        y = float(point[1])
        if not np.isfinite(x) or not np.isfinite(y):
            raise ValueError("ROI zone points must be finite numbers.")
        normalized.append((float(np.clip(x, 0.0, 1.0)), float(np.clip(y, 0.0, 1.0))))
    if len(normalized) < 3:
        raise ValueError("ROI zones require at least three points.")
    return tuple(normalized)


def parse_roi_mask(payload: dict[str, Any]) -> RoiMask:
    if not isinstance(payload, dict):
        raise ValueError("ROI mask must be a JSON object.")

    version = int(payload.get("version", 1))
    if version != 1:
        raise ValueError(f"Unsupported ROI mask version: {version}")

    mode = str(payload.get("mode", "fixed")).strip().lower()
    if mode not in ROI_MASK_MODES:
        raise ValueError(f"ROI mask mode must be one of: {', '.join(sorted(ROI_MASK_MODES))}")

    zones_payload = payload.get("zones", [])
    if not isinstance(zones_payload, list):
        raise ValueError("ROI mask zones must be a list.")

    zones: list[RoiZone] = []
    for index, zone_payload in enumerate(zones_payload, start=1):
        if not isinstance(zone_payload, dict):
            raise ValueError("Each ROI zone must be a JSON object.")
        zone_type = str(zone_payload.get("type", "ignore")).strip().lower()
        if zone_type not in ROI_ZONE_TYPES:
            raise ValueError(f"ROI zone type must be one of: {', '.join(sorted(ROI_ZONE_TYPES))}")
        points = normalize_roi_points(zone_payload.get("points", []))
        default_penalty = 0.5 if zone_type == "penalty" else 0.0
        penalty = float(np.clip(float(zone_payload.get("penalty", default_penalty)), 0.0, 1.0))
        zones.append(
            RoiZone(
                name=str(zone_payload.get("name") or f"{zone_type}_{index}"),
                type=zone_type,
                points=points,
                penalty=penalty,
            )
        )

    return RoiMask(version=version, mode=mode, zones=tuple(zones))


def load_roi_mask(path: str | Path | None) -> RoiMask | None:
    if not path:
        return None
    mask_path = Path(path)
    payload = json.loads(mask_path.read_text(encoding="utf-8"))
    return parse_roi_mask(payload)


def roi_zone_points_pixels(zone: RoiZone, image_width: int, image_height: int) -> np.ndarray:
    return np.array(
        [
            [
                float(np.clip(x * image_width, 0.0, float(image_width))),
                float(np.clip(y * image_height, 0.0, float(image_height))),
            ]
            for x, y in zone.points
        ],
        dtype=np.float32,
    )


def _point_in_polygon(point: tuple[float, float], polygon: np.ndarray) -> bool:
    return cv2.pointPolygonTest(polygon, point, False) >= 0


def _point_in_detection_box(point: np.ndarray, detection: MotionDetection) -> bool:
    x, y = float(point[0]), float(point[1])
    return detection.x1 <= x <= detection.x2 and detection.y1 <= y <= detection.y2


def _orientation(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    eps: float = 1e-9,
) -> bool:
    return (
        min(a[0], b[0]) - eps <= c[0] <= max(a[0], b[0]) + eps
        and min(a[1], b[1]) - eps <= c[1] <= max(a[1], b[1]) + eps
    )


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
    eps: float = 1e-9,
) -> bool:
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)

    if abs(o1) <= eps and _on_segment(a, b, c):
        return True
    if abs(o2) <= eps and _on_segment(a, b, d):
        return True
    if abs(o3) <= eps and _on_segment(c, d, a):
        return True
    if abs(o4) <= eps and _on_segment(c, d, b):
        return True
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def detection_overlaps_zone(
    detection: MotionDetection,
    zone: RoiZone,
    image_width: int,
    image_height: int,
) -> bool:
    polygon = roi_zone_points_pixels(zone, image_width, image_height)
    x1, y1, x2, y2 = detection.x1, detection.y1, detection.x2, detection.y2
    box_points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]

    test_points = [(detection.center_x, detection.center_y), *box_points]
    if any(_point_in_polygon(point, polygon) for point in test_points):
        return True
    if any(_point_in_detection_box(point, detection) for point in polygon):
        return True

    polygon_points = [(float(point[0]), float(point[1])) for point in polygon]
    box_edges = list(zip(box_points, box_points[1:] + box_points[:1]))
    polygon_edges = list(zip(polygon_points, polygon_points[1:] + polygon_points[:1]))
    return any(
        _segments_intersect(box_a, box_b, poly_a, poly_b)
        for box_a, box_b in box_edges
        for poly_a, poly_b in polygon_edges
    )


def filter_detection_by_roi(
    detection: MotionDetection,
    roi_mask: RoiMask | None,
    image_width: int,
    image_height: int,
) -> MotionDetection | None:
    if roi_mask is None or not roi_mask.zones:
        return detection

    ignore_zones = [zone for zone in roi_mask.zones if zone.type == "ignore"]
    for zone in ignore_zones:
        if detection_overlaps_zone(detection, zone, image_width, image_height):
            return None

    flight_zones = [zone for zone in roi_mask.zones if zone.type == "flight"]
    if flight_zones and not any(
        detection_overlaps_zone(detection, zone, image_width, image_height)
        for zone in flight_zones
    ):
        return None

    penalty_zones = [zone for zone in roi_mask.zones if zone.type == "penalty"]
    for zone in penalty_zones:
        if detection_overlaps_zone(detection, zone, image_width, image_height):
            return replace(
                detection,
                roi_action="penalize",
                zone_type=zone.type,
                zone_name=zone.name,
                roi_penalty=zone.penalty,
            )

    return detection


def prepare_gray(frame_bgr: np.ndarray, config: MotionConfig) -> np.ndarray:
    config = config.normalized()
    frame = frame_bgr
    if config.analysis_scale < 0.999:
        frame = cv2.resize(
            frame_bgr,
            (0, 0),
            fx=config.analysis_scale,
            fy=config.analysis_scale,
            interpolation=cv2.INTER_AREA,
        )
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if config.blur_kernel > 1:
        gray = cv2.GaussianBlur(gray, (config.blur_kernel, config.blur_kernel), 0)
    return gray


def cleanup_motion_mask(diff: np.ndarray, config: MotionConfig) -> np.ndarray:
    config = config.normalized()
    _, mask = cv2.threshold(diff, config.diff_threshold, 255, cv2.THRESH_BINARY)
    if config.morph_kernel > 1:
        kernel = np.ones((config.morph_kernel, config.morph_kernel), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def estimate_global_shift(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    min_vectors: int = 12,
    consensus_px: float = 2.0,
) -> tuple[float, float, float, int]:
    points = cv2.goodFeaturesToTrack(
        previous_gray,
        maxCorners=240,
        qualityLevel=0.01,
        minDistance=12,
        blockSize=7,
    )
    if points is None or len(points) < min_vectors:
        return 0.0, 0.0, 0.0, 0

    next_points, status, _err = cv2.calcOpticalFlowPyrLK(
        previous_gray,
        current_gray,
        points,
        None,
        winSize=(21, 21),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
    )
    if next_points is None or status is None:
        return 0.0, 0.0, 0.0, 0

    valid = status.reshape(-1) == 1
    if int(valid.sum()) < min_vectors:
        return 0.0, 0.0, 0.0, int(valid.sum())

    vectors = next_points.reshape(-1, 2)[valid] - points.reshape(-1, 2)[valid]
    median = np.median(vectors, axis=0)
    residuals = np.linalg.norm(vectors - median, axis=1)
    consensus = float(np.mean(residuals <= consensus_px))
    return float(median[0]), float(median[1]), consensus, int(vectors.shape[0])


def analyze_gray_pair(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    config: MotionConfig,
    image_width: int,
    image_height: int,
    roi_mask: RoiMask | None = None,
) -> MotionAnalysis:
    config = config.normalized()
    compare_gray = previous_gray
    global_dx = 0.0
    global_dy = 0.0
    global_consensus = 0.0
    tracked_vectors = 0
    global_motion_detected = False
    if config.shake_protection:
        global_dx, global_dy, global_consensus, tracked_vectors = estimate_global_shift(
            previous_gray,
            current_gray,
            consensus_px=config.shake_consensus_px,
        )
        global_shift = float(np.hypot(global_dx, global_dy))
        global_motion_detected = (
            global_shift >= config.shake_min_shift
            and global_consensus >= config.shake_consensus
        )
        if global_motion_detected:
            transform = np.array(
                [[1.0, 0.0, global_dx], [0.0, 1.0, global_dy]],
                dtype=np.float32,
            )
            compare_gray = cv2.warpAffine(
                previous_gray,
                transform,
                (current_gray.shape[1], current_gray.shape[0]),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )

    diff = cv2.absdiff(current_gray, compare_gray)
    raw_mask = cleanup_motion_mask(diff, config)
    motion_ratio = float(np.count_nonzero(raw_mask) / max(1, raw_mask.size))

    global_motion_rejected = (
        config.max_motion_ratio > 0.0 and motion_ratio > config.max_motion_ratio
    )
    if global_motion_rejected:
        return MotionAnalysis(
            np.zeros_like(raw_mask),
            [],
            motion_ratio,
            True,
            global_motion_detected,
            global_dx,
            global_dy,
            global_consensus,
            tracked_vectors,
        )

    scale_x = raw_mask.shape[1] / float(image_width)
    scale_y = raw_mask.shape[0] / float(image_height)
    area_scale = max(scale_x * scale_y, 1e-9)
    accepted_mask = np.zeros_like(raw_mask)
    contours, _ = cv2.findContours(raw_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    raw_candidates: list[tuple[MotionDetection, np.ndarray]] = []

    for contour in contours:
        area = float(cv2.contourArea(contour) / area_scale)
        if area < config.min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        x1 = np.clip(x / scale_x, 0, image_width)
        y1 = np.clip(y / scale_y, 0, image_height)
        x2 = np.clip((x + w) / scale_x, 0, image_width)
        y2 = np.clip((y + h) / scale_y, 0, image_height)
        if x2 <= x1 or y2 <= y1:
            continue
        raw_candidates.append(
            (
                MotionDetection(
                    x1=float(x1),
                    y1=float(y1),
                    x2=float(x2),
                    y2=float(y2),
                    center_x=float((x1 + x2) / 2.0),
                    center_y=float((y1 + y2) / 2.0),
                    area=area,
                ),
                contour,
            )
        )

    roi_rejected_count = 0
    roi_penalized_count = 0
    kept_candidates: list[tuple[MotionDetection, np.ndarray]] = []
    for detection, contour in raw_candidates:
        filtered_detection = filter_detection_by_roi(
            detection,
            roi_mask,
            image_width,
            image_height,
        )
        if filtered_detection is None:
            roi_rejected_count += 1
            continue
        if filtered_detection.roi_action == "penalize":
            roi_penalized_count += 1
        kept_candidates.append((filtered_detection, contour))

    for _detection, contour in kept_candidates:
        cv2.drawContours(accepted_mask, [contour], -1, 255, thickness=cv2.FILLED)

    detections = [detection for detection, _contour in kept_candidates]
    detections.sort(key=lambda detection: detection.area, reverse=True)
    return MotionAnalysis(
        accepted_mask,
        detections,
        motion_ratio,
        False,
        global_motion_detected,
        global_dx,
        global_dy,
        global_consensus,
        tracked_vectors,
        len(raw_candidates),
        roi_rejected_count,
        roi_penalized_count,
    )


def combine_trail_masks(masks: list[np.ndarray]) -> np.ndarray:
    if not masks:
        raise ValueError("At least one mask is required.")
    combined = np.zeros_like(masks[0])
    for mask in masks:
        combined = cv2.bitwise_or(combined, mask)
    return combined


def render_motion_only(frame_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if mask.shape[:2] != frame_bgr.shape[:2]:
        mask = cv2.resize(
            mask,
            (frame_bgr.shape[1], frame_bgr.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
    output = np.zeros_like(frame_bgr)
    output[mask > 0] = frame_bgr[mask > 0]
    return output


def render_overlay(
    frame_bgr: np.ndarray,
    detections: list[MotionDetection],
    global_motion_rejected: bool = False,
    global_motion_detected: bool = False,
    global_dx: float = 0.0,
    global_dy: float = 0.0,
    global_consensus: float = 0.0,
    roi_active: bool = False,
    roi_rejected_count: int = 0,
    roi_penalized_count: int = 0,
) -> np.ndarray:
    output = frame_bgr.copy()
    for detection in detections:
        x1, y1, x2, y2 = (
            int(round(detection.x1)),
            int(round(detection.y1)),
            int(round(detection.x2)),
            int(round(detection.y2)),
        )
        color = (0, 165, 255) if detection.roi_action == "penalize" else (0, 255, 255)
        cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)
        label = f"motion {detection.area:.0f}px"
        if detection.roi_action == "penalize":
            label = f"penalty {detection.area:.0f}px"
        label_y = max(18, y1 - 6)
        cv2.putText(
            output,
            label,
            (x1, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    if global_motion_rejected:
        cv2.putText(
            output,
            "global residual rejected",
            (12, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    elif global_motion_detected:
        cv2.putText(
            output,
            f"shake compensated dx={global_dx:.1f} dy={global_dy:.1f} c={global_consensus:.2f}",
            (12, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 220, 255),
            2,
            cv2.LINE_AA,
        )
    if roi_active:
        cv2.putText(
            output,
            f"ROI rejected={roi_rejected_count} penalized={roi_penalized_count}",
            (12, 56),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    return output


def serialize_config(config: MotionConfig) -> dict[str, Any]:
    return {
        "diff_threshold": config.diff_threshold,
        "min_area": config.min_area,
        "blur_kernel": config.blur_kernel,
        "morph_kernel": config.morph_kernel,
        "trail_frames": config.trail_frames,
        "max_motion_ratio": config.max_motion_ratio,
        "analysis_scale": config.analysis_scale,
        "shake_protection": config.shake_protection,
        "shake_min_shift": config.shake_min_shift,
        "shake_consensus": config.shake_consensus,
        "shake_consensus_px": config.shake_consensus_px,
    }


def make_output_dir(out_dir: str | None) -> Path:
    if out_dir:
        path = Path(out_dir)
    else:
        path = DEFAULT_OUTPUT_ROOT / time.strftime("run_%Y%m%d_%H%M%S")
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_video_writer(path: Path, fps: float, width: int, height: int) -> cv2.VideoWriter:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Could not create video writer: {path}")
    return writer


def process_video(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.path)
    if not source.exists():
        raise FileNotFoundError(f"Video not found: {source}")

    config = MotionConfig(
        diff_threshold=args.diff_threshold,
        min_area=args.min_area,
        blur_kernel=args.blur_kernel,
        morph_kernel=args.morph_kernel,
        trail_frames=args.trail_frames,
        max_motion_ratio=args.max_motion_ratio,
        analysis_scale=args.analysis_scale,
        shake_protection=not args.disable_shake_protection,
        shake_min_shift=args.shake_min_shift,
        shake_consensus=args.shake_consensus,
        shake_consensus_px=args.shake_consensus_px,
    ).normalized()
    roi_mask = load_roi_mask(args.roi_mask)
    out_dir = make_output_dir(args.out_dir)

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {source}")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        capture.release()
        raise RuntimeError(f"Could not read video dimensions: {source}")

    motion_only_path = out_dir / f"{source.stem}_motion_only.mp4"
    overlay_path = out_dir / f"{source.stem}_motion_overlay.mp4"
    jsonl_path = out_dir / "motion_detections.jsonl"
    summary_path = out_dir / "summary.json"

    motion_writer = open_video_writer(motion_only_path, fps, width, height)
    overlay_writer = open_video_writer(overlay_path, fps, width, height)

    previous_gray: np.ndarray | None = None
    trail_masks: list[np.ndarray] = []
    max_trail_masks = max(1, config.trail_frames + 1)
    frame_index = 0
    frames_with_motion = 0
    total_detections = 0
    total_raw_detections = 0
    total_roi_rejected = 0
    total_roi_penalized = 0
    rejected_frame_count = 0
    global_motion_detected_count = 0
    started_at = time.time()

    try:
        with jsonl_path.open("w", encoding="utf-8") as jsonl:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break

                timestamp_s = frame_index / max(fps, 0.001)
                current_gray = prepare_gray(frame, config)

                if previous_gray is None:
                    accepted_mask = np.zeros_like(current_gray)
                    detections: list[MotionDetection] = []
                    motion_ratio = 0.0
                    global_motion_rejected = False
                    global_motion_detected = False
                    global_dx = 0.0
                    global_dy = 0.0
                    global_consensus = 0.0
                    tracked_vectors = 0
                    raw_detection_count = 0
                    roi_rejected_count = 0
                    roi_penalized_count = 0
                else:
                    analysis = analyze_gray_pair(
                        previous_gray,
                        current_gray,
                        config,
                        image_width=width,
                        image_height=height,
                        roi_mask=roi_mask,
                    )
                    accepted_mask = analysis.accepted_mask
                    detections = analysis.detections
                    motion_ratio = analysis.motion_ratio
                    global_motion_rejected = analysis.global_motion_rejected
                    global_motion_detected = analysis.global_motion_detected
                    global_dx = analysis.global_dx
                    global_dy = analysis.global_dy
                    global_consensus = analysis.global_consensus
                    tracked_vectors = analysis.tracked_vectors
                    raw_detection_count = analysis.raw_detection_count
                    roi_rejected_count = analysis.roi_rejected_count
                    roi_penalized_count = analysis.roi_penalized_count

                if global_motion_rejected:
                    rejected_frame_count += 1
                    trail_masks = [np.zeros_like(accepted_mask)]
                else:
                    trail_masks.append(accepted_mask)
                    trail_masks = trail_masks[-max_trail_masks:]

                trail_mask = combine_trail_masks(trail_masks)
                motion_writer.write(render_motion_only(frame, trail_mask))
                overlay_writer.write(
                    render_overlay(
                        frame,
                        detections,
                        global_motion_rejected,
                        global_motion_detected,
                        global_dx,
                        global_dy,
                        global_consensus,
                        roi_active=roi_mask is not None,
                        roi_rejected_count=roi_rejected_count,
                        roi_penalized_count=roi_penalized_count,
                    )
                )

                total_raw_detections += raw_detection_count
                total_roi_rejected += roi_rejected_count
                total_roi_penalized += roi_penalized_count
                if detections:
                    frames_with_motion += 1
                    total_detections += len(detections)
                if global_motion_detected:
                    global_motion_detected_count += 1

                record = MotionFrameResult(
                    source=str(source),
                    frame_index=frame_index,
                    timestamp_s=timestamp_s,
                    image_width=width,
                    image_height=height,
                    motion_ratio=motion_ratio,
                    global_motion_rejected=global_motion_rejected,
                    global_motion_detected=global_motion_detected,
                    global_dx=global_dx,
                    global_dy=global_dy,
                    global_consensus=global_consensus,
                    tracked_vectors=tracked_vectors,
                    raw_detection_count=raw_detection_count,
                    roi_rejected_count=roi_rejected_count,
                    roi_penalized_count=roi_penalized_count,
                    detections=detections,
                )
                jsonl.write(json.dumps(record.to_json_dict(), separators=(",", ":")) + "\n")

                previous_gray = current_gray
                frame_index += 1
    finally:
        capture.release()
        motion_writer.release()
        overlay_writer.release()

    if frame_index == 0:
        raise RuntimeError(f"No frames were read from video: {source}")

    summary = {
        "mode": "video",
        "source": str(source),
        "image_width": width,
        "image_height": height,
        "fps": fps,
        "frame_count": frame_index,
        "duration_s": frame_index / max(fps, 0.001),
        "frames_with_motion": frames_with_motion,
        "detection_count": total_detections,
        "raw_detection_count": total_raw_detections,
        "roi_rejected_count": total_roi_rejected,
        "roi_penalized_count": total_roi_penalized,
        "kept_detection_count": total_detections,
        "global_motion_rejected_frames": rejected_frame_count,
        "global_motion_detected_frames": global_motion_detected_count,
        "processing_seconds": round(time.time() - started_at, 3),
        "config": serialize_config(config),
        "roi_mask": {
            "enabled": roi_mask is not None,
            "path": str(args.roi_mask) if args.roi_mask else None,
            "mode": roi_mask.mode if roi_mask else None,
            "zone_count": len(roi_mask.zones) if roi_mask else 0,
        },
        "motion_only_path": str(motion_only_path),
        "overlay_path": str(overlay_path),
        "jsonl_path": str(jsonl_path),
        "summary_path": str(summary_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def add_common_options(parser: argparse.ArgumentParser, duplicate: bool = False) -> None:
    default = argparse.SUPPRESS if duplicate else None
    parser.add_argument("--diff-threshold", type=int, default=18 if not duplicate else default)
    parser.add_argument("--min-area", type=float, default=1000.0 if not duplicate else default)
    parser.add_argument("--blur-kernel", type=int, default=5 if not duplicate else default)
    parser.add_argument("--morph-kernel", type=int, default=3 if not duplicate else default)
    parser.add_argument("--trail-frames", type=int, default=3 if not duplicate else default)
    parser.add_argument("--max-motion-ratio", type=float, default=0.10 if not duplicate else default)
    parser.add_argument("--analysis-scale", type=float, default=0.5 if not duplicate else default)
    parser.add_argument(
        "--disable-shake-protection",
        action="store_true",
        default=False if not duplicate else default,
    )
    parser.add_argument("--shake-min-shift", type=float, default=1.5 if not duplicate else default)
    parser.add_argument("--shake-consensus", type=float, default=0.72 if not duplicate else default)
    parser.add_argument("--shake-consensus-px", type=float, default=2.0 if not duplicate else default)
    parser.add_argument("--roi-mask", default=default, help="Path to normalized ROI mask JSON.")
    parser.add_argument("--out-dir", default=default)
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if not duplicate else default,
        help="Print machine-readable run summary.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render motion-only and overlay videos from a fixed camera video."
    )
    add_common_options(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    video_parser = subparsers.add_parser("video", help="Process one video.")
    video_parser.add_argument("path")
    add_common_options(video_parser, duplicate=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "video":
        summary = process_video(args)
    else:
        raise AssertionError(args.command)

    if args.json:
        print(json.dumps(summary, separators=(",", ":")))
    else:
        print(
            f"video frames={summary['frame_count']} "
            f"motion_frames={summary['frames_with_motion']} "
            f"motion_only={summary['motion_only_path']}"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
