"""Fixed-camera frame differencing runner for motion-only drone video previews."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np


DEFAULT_OUTPUT_ROOT = Path(__file__).with_name("outputs")
ROI_ZONE_TYPES = {"ignore", "penalty", "flight"}
ROI_MASK_MODES = {"fixed", "handheld"}
DEFAULT_SEMANTIC_MODEL_REPO = "devanshty/WingID"
DEFAULT_SEMANTIC_MODEL_FILE = "yolo11l.pt"
PROGRESS_PREFIX = "PROGRESS "
SEMANTIC_ACTIONS = {"reject", "penalize"}
PROCESSING_BACKENDS = {"auto", "cpu", "cuda", "mps"}
STOP_REQUESTED = False
SEMANTIC_LABEL_ALIASES = {
    "human": "person",
    "person": "person",
    "people": "person",
    "pedestrian": "person",
    "bird": "bird",
    "птица": "bird",
}


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
    shake_frame_stride: int = 1
    shake_analysis_scale: float = 1.0
    shake_max_corners: int = 240
    hysteresis: bool = False
    hysteresis_high_threshold: int = 36
    temporal_filter: bool = False
    temporal_window_frames: int = 3
    temporal_min_hits: int = 2
    track_confirmation: bool = False
    track_confirm_hits: int = 2
    track_max_missed: int = 2
    track_match_distance: float = 80.0
    direction_consistency: bool = False
    direction_min_hits: int = 3
    direction_min_displacement: float = 2.0
    direction_cosine: float = 0.20
    drone_track_filter: bool = False
    drone_min_track_hits: int = 3
    drone_min_normalized_speed: float = 0.10
    drone_max_normalized_speed: float = 30.0
    screen_decoy_rejection: bool = False
    screen_min_track_hits: int = 8
    screen_max_area_cv: float = 0.08
    screen_max_aspect_cv: float = 0.10
    screen_min_path_smoothness: float = 0.90
    screen_min_perimeter_fraction: float = 0.0
    screen_perimeter_margin: float = 0.10
    occlusion_recovery: bool = False
    occlusion_max_frames: int = 8
    occlusion_gate_distance: float = 140.0

    def normalized(self) -> "MotionConfig":
        diff_threshold = int(np.clip(self.diff_threshold, 1, 255))
        return MotionConfig(
            diff_threshold=diff_threshold,
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
            shake_frame_stride=max(1, int(self.shake_frame_stride)),
            shake_analysis_scale=float(np.clip(self.shake_analysis_scale, 0.10, 1.0)),
            shake_max_corners=max(12, int(self.shake_max_corners)),
            hysteresis=bool(self.hysteresis),
            hysteresis_high_threshold=int(
                np.clip(max(diff_threshold, int(self.hysteresis_high_threshold)), 1, 255)
            ),
            temporal_filter=bool(self.temporal_filter),
            temporal_window_frames=max(1, int(self.temporal_window_frames)),
            temporal_min_hits=max(1, int(self.temporal_min_hits)),
            track_confirmation=bool(self.track_confirmation),
            track_confirm_hits=max(1, int(self.track_confirm_hits)),
            track_max_missed=max(0, int(self.track_max_missed)),
            track_match_distance=max(1.0, float(self.track_match_distance)),
            direction_consistency=bool(self.direction_consistency),
            direction_min_hits=max(2, int(self.direction_min_hits)),
            direction_min_displacement=max(0.0, float(self.direction_min_displacement)),
            direction_cosine=float(np.clip(self.direction_cosine, -1.0, 1.0)),
            drone_track_filter=bool(self.drone_track_filter),
            drone_min_track_hits=max(1, int(self.drone_min_track_hits)),
            drone_min_normalized_speed=max(0.0, float(self.drone_min_normalized_speed)),
            drone_max_normalized_speed=max(0.0, float(self.drone_max_normalized_speed)),
            screen_decoy_rejection=bool(self.screen_decoy_rejection),
            screen_min_track_hits=max(2, int(self.screen_min_track_hits)),
            screen_max_area_cv=max(0.0, float(self.screen_max_area_cv)),
            screen_max_aspect_cv=max(0.0, float(self.screen_max_aspect_cv)),
            screen_min_path_smoothness=float(np.clip(self.screen_min_path_smoothness, 0.0, 1.0)),
            screen_min_perimeter_fraction=float(np.clip(self.screen_min_perimeter_fraction, 0.0, 1.0)),
            screen_perimeter_margin=float(np.clip(self.screen_perimeter_margin, 0.0, 0.5)),
            occlusion_recovery=bool(self.occlusion_recovery),
            occlusion_max_frames=max(0, int(self.occlusion_max_frames)),
            occlusion_gate_distance=max(1.0, float(self.occlusion_gate_distance)),
        )


@dataclass(frozen=True)
class BackendInfo:
    requested: str
    used: str
    cuda_available: bool
    cuda_device_count: int
    cuda_device: int | None = None
    semantic_device: str = "cpu"
    semantic_cuda_available: bool = False
    semantic_mps_available: bool = False
    message: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "used": self.used,
            "motion_backend": self.used,
            "cuda_available": self.cuda_available,
            "cuda_device_count": self.cuda_device_count,
            "cuda_device": self.cuda_device,
            "semantic_device": self.semantic_device,
            "semantic_cuda_available": self.semantic_cuda_available,
            "semantic_mps_available": self.semantic_mps_available,
            "message": self.message,
        }


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
class SemanticConfig:
    enabled: bool = False
    labels: tuple[str, ...] = ("person",)
    action: str = "reject"
    model_repo: str = DEFAULT_SEMANTIC_MODEL_REPO
    model_file: str = DEFAULT_SEMANTIC_MODEL_FILE
    weights: str | None = None
    confidence: float = 0.05
    iou: float = 0.50
    image_size: int = 960
    device: str | None = None
    frame_stride: int = 2
    overlap_threshold: float = 0.15
    warmup: bool = False
    motion_gate: bool = False

    def normalized(self) -> "SemanticConfig":
        labels = tuple(
            dict.fromkeys(
                canonical_semantic_label(label)
                for label in self.labels
                if canonical_semantic_label(label)
            )
        )
        action = self.action if self.action in SEMANTIC_ACTIONS else "reject"
        device = str(self.device).strip() if self.device is not None else None
        return SemanticConfig(
            enabled=bool(self.enabled),
            labels=labels or ("person",),
            action=action,
            model_repo=str(self.model_repo or DEFAULT_SEMANTIC_MODEL_REPO),
            model_file=str(self.model_file or DEFAULT_SEMANTIC_MODEL_FILE),
            weights=str(self.weights).strip() or None if self.weights else None,
            confidence=float(np.clip(self.confidence, 0.001, 1.0)),
            iou=float(np.clip(self.iou, 0.01, 1.0)),
            image_size=max(320, int(self.image_size)),
            device=device or None,
            frame_stride=max(1, int(self.frame_stride)),
            overlap_threshold=float(np.clip(self.overlap_threshold, 0.0, 1.0)),
            warmup=bool(self.warmup),
            motion_gate=bool(self.motion_gate),
        )


@dataclass(frozen=True)
class SemanticDetection:
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
    track_id: int | None = None
    track_age: int = 0
    track_hits: int = 0
    track_confirmed: bool = True
    motion_dx: float = 0.0
    motion_dy: float = 0.0
    direction_consistent: bool = True
    normalized_speed: float = 0.0
    track_area_cv: float = 0.0
    track_aspect_cv: float = 0.0
    track_path_smoothness: float = 0.0
    track_perimeter_fraction: float = 0.0
    drone_score: float = 0.0
    screen_decoy_score: float = 0.0
    track_action: str = "keep"
    track_filter_reason: str | None = None
    occlusion_recovered: bool = False
    semantic_action: str = "keep"
    semantic_label: str | None = None
    semantic_confidence: float = 0.0
    semantic_overlap: float = 0.0

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
    semantic_detection_count: int
    semantic_rejected_count: int
    semantic_penalized_count: int
    temporal_rejected_count: int
    unconfirmed_rejected_count: int
    direction_rejected_count: int
    drone_track_rejected_count: int
    screen_decoy_rejected_count: int
    occlusion_recovered_count: int
    semantic_detections: list[SemanticDetection]
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
            "semantic_detection_count": int(self.semantic_detection_count),
            "semantic_rejected_count": int(self.semantic_rejected_count),
            "semantic_penalized_count": int(self.semantic_penalized_count),
            "temporal_rejected_count": int(self.temporal_rejected_count),
            "unconfirmed_rejected_count": int(self.unconfirmed_rejected_count),
            "direction_rejected_count": int(self.direction_rejected_count),
            "drone_track_rejected_count": int(self.drone_track_rejected_count),
            "screen_decoy_rejected_count": int(self.screen_decoy_rejected_count),
            "occlusion_recovered_count": int(self.occlusion_recovered_count),
            "semantic_detections": [
                detection.to_json_dict() for detection in self.semantic_detections
            ],
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
    shake_estimated: bool = False
    shake_reused: bool = False
    raw_detection_count: int = 0
    roi_rejected_count: int = 0
    roi_penalized_count: int = 0
    semantic_detections: list[SemanticDetection] = field(default_factory=list)
    semantic_detection_count: int = 0
    semantic_rejected_count: int = 0
    semantic_penalized_count: int = 0
    temporal_rejected_count: int = 0
    unconfirmed_rejected_count: int = 0
    direction_rejected_count: int = 0
    drone_track_rejected_count: int = 0
    screen_decoy_rejected_count: int = 0
    occlusion_recovered_count: int = 0


def canonical_semantic_label(label: str) -> str:
    normalized = str(label).strip().lower()
    return SEMANTIC_LABEL_ALIASES.get(normalized, normalized)


def odd_kernel(value: int) -> int:
    kernel = max(1, int(value))
    if kernel % 2 == 0:
        kernel += 1
    return kernel


def get_cuda_device_count() -> int:
    try:
        if not hasattr(cv2, "cuda"):
            return 0
        return int(cv2.cuda.getCudaEnabledDeviceCount())
    except Exception:
        return 0


def cuda_is_available() -> bool:
    return get_cuda_device_count() > 0


def torch_cuda_is_available() -> bool:
    try:
        import torch
    except ImportError:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def torch_mps_is_available() -> bool:
    try:
        import torch
    except ImportError:
        return False
    try:
        mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
        return bool(mps_backend is not None and mps_backend.is_available())
    except Exception:
        return False


def resolve_semantic_device_for_backend(
    requested: str,
    semantic_cuda_available: bool,
    semantic_mps_available: bool,
) -> str:
    if requested == "cpu":
        return "cpu"
    if requested == "cuda":
        return "cuda" if semantic_cuda_available else "cpu"
    if requested == "mps":
        return "mps" if semantic_mps_available else "cpu"
    if semantic_cuda_available:
        return "cuda"
    if semantic_mps_available:
        return "mps"
    return "cpu"


def resolve_backend(requested: str, cuda_device: int = 0) -> BackendInfo:
    requested = str(requested or "auto").strip().lower()
    if requested not in PROCESSING_BACKENDS:
        raise ValueError(f"Unsupported processing backend: {requested}")

    device_count = get_cuda_device_count()
    motion_cuda_available = device_count > 0
    semantic_cuda_available = torch_cuda_is_available()
    semantic_mps_available = torch_mps_is_available()
    semantic_device = resolve_semantic_device_for_backend(
        requested,
        semantic_cuda_available,
        semantic_mps_available,
    )

    if requested == "cpu":
        return BackendInfo(
            requested=requested,
            used="cpu",
            cuda_available=motion_cuda_available,
            cuda_device_count=device_count,
            semantic_device="cpu",
            semantic_cuda_available=semantic_cuda_available,
            semantic_mps_available=semantic_mps_available,
            message="CPU backend selected.",
        )

    if requested in {"auto", "cuda"} and motion_cuda_available:
        if cuda_device < 0 or cuda_device >= device_count:
            return BackendInfo(
                requested=requested,
                used="cpu",
                cuda_available=True,
                cuda_device_count=device_count,
                semantic_device=semantic_device,
                semantic_cuda_available=semantic_cuda_available,
                semantic_mps_available=semantic_mps_available,
                message=(
                    f"CUDA device {cuda_device} is not available; motion backend fell back to CPU. "
                    f"Detected {device_count} CUDA device(s)."
                ),
            )
        return BackendInfo(
            requested=requested,
            used="cuda",
            cuda_available=True,
            cuda_device_count=device_count,
            cuda_device=int(cuda_device),
            semantic_device=semantic_device,
            semantic_cuda_available=semantic_cuda_available,
            semantic_mps_available=semantic_mps_available,
            message=(
                f"CUDA motion backend selected on device {cuda_device}; "
                f"semantic device={semantic_device}."
            ),
        )

    if requested == "cuda":
        return BackendInfo(
            requested=requested,
            used="cpu",
            cuda_available=False,
            cuda_device_count=device_count,
            semantic_device=semantic_device,
            semantic_cuda_available=semantic_cuda_available,
            semantic_mps_available=semantic_mps_available,
            message=(
                "CUDA was requested, but OpenCV reports no CUDA motion device; "
                f"motion backend fell back to CPU and semantic device={semantic_device}."
            ),
        )

    if requested == "mps":
        return BackendInfo(
            requested=requested,
            used="cpu",
            cuda_available=motion_cuda_available,
            cuda_device_count=device_count,
            semantic_device=semantic_device,
            semantic_cuda_available=semantic_cuda_available,
            semantic_mps_available=semantic_mps_available,
            message=(
                "MPS is only available for the semantic AI model; "
                f"motion backend uses CPU and semantic device={semantic_device}."
            ),
        )

    return BackendInfo(
        requested=requested,
        used="cpu",
        cuda_available=motion_cuda_available,
        cuda_device_count=device_count,
        semantic_device=semantic_device,
        semantic_cuda_available=semantic_cuda_available,
        semantic_mps_available=semantic_mps_available,
        message=f"Auto selected CPU motion backend and semantic device={semantic_device}.",
    )


def apply_semantic_device_override(
    backend_info: BackendInfo,
    semantic_device: str | None,
) -> BackendInfo:
    requested_device = str(semantic_device or "").strip().lower()
    if not requested_device:
        return backend_info
    if requested_device == "cpu":
        selected_device = "cpu"
        message = "Semantic device override selected CPU."
    elif requested_device == "cuda":
        selected_device = "cuda" if backend_info.semantic_cuda_available else "cpu"
        message = (
            "Semantic device override selected CUDA."
            if selected_device == "cuda"
            else "Semantic CUDA override unavailable; semantic device fell back to CPU."
        )
    elif requested_device == "mps":
        selected_device = "mps" if backend_info.semantic_mps_available else "cpu"
        message = (
            "Semantic device override selected MPS."
            if selected_device == "mps"
            else "Semantic MPS override unavailable; semantic device fell back to CPU."
        )
    else:
        selected_device = requested_device
        message = f"Semantic device override selected '{requested_device}'."

    return replace(
        backend_info,
        semantic_device=selected_device,
        message=f"{backend_info.message} {message}",
    )


class CudaMotionProcessor:
    def __init__(self, device_id: int = 0) -> None:
        self.device_id = int(device_id)
        self._blur_filter: Any | None = None
        self._blur_kernel: int | None = None
        self._morph_filters: dict[tuple[int, int], Any] = {}
        cv2.cuda.setDevice(self.device_id)

    def prepare_gray(self, frame_bgr: np.ndarray, config: MotionConfig) -> np.ndarray:
        config = config.normalized()
        gpu_frame = cv2.cuda_GpuMat()
        gpu_frame.upload(frame_bgr)
        if config.analysis_scale < 0.999:
            target_size = (
                max(1, int(round(frame_bgr.shape[1] * config.analysis_scale))),
                max(1, int(round(frame_bgr.shape[0] * config.analysis_scale))),
            )
            gpu_frame = cv2.cuda.resize(gpu_frame, target_size, interpolation=cv2.INTER_AREA)

        gpu_gray = cv2.cuda.cvtColor(gpu_frame, cv2.COLOR_BGR2GRAY)
        if config.blur_kernel > 1:
            if self._blur_filter is None or self._blur_kernel != config.blur_kernel:
                self._blur_filter = cv2.cuda.createGaussianFilter(
                    gpu_gray.type(),
                    gpu_gray.type(),
                    (config.blur_kernel, config.blur_kernel),
                    0,
                )
                self._blur_kernel = config.blur_kernel
            gpu_gray = self._blur_filter.apply(gpu_gray)
        return gpu_gray.download()

    def _morph_filter(self, operation: int, kernel_size: int, mat_type: int) -> Any:
        key = (operation, kernel_size)
        if key not in self._morph_filters:
            kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
            self._morph_filters[key] = cv2.cuda.createMorphologyFilter(
                operation,
                mat_type,
                kernel,
            )
        return self._morph_filters[key]

    def cleanup_motion_mask(self, diff: np.ndarray, config: MotionConfig) -> np.ndarray:
        config = config.normalized()
        if config.hysteresis:
            return cleanup_motion_mask(diff, config)

        gpu_diff = cv2.cuda_GpuMat()
        gpu_diff.upload(diff)
        _threshold, gpu_mask = cv2.cuda.threshold(
            gpu_diff,
            config.diff_threshold,
            255,
            cv2.THRESH_BINARY,
        )
        if config.morph_kernel > 1:
            mat_type = gpu_mask.type()
            gpu_mask = self._morph_filter(cv2.MORPH_OPEN, config.morph_kernel, mat_type).apply(gpu_mask)
            gpu_mask = self._morph_filter(cv2.MORPH_CLOSE, config.morph_kernel, mat_type).apply(gpu_mask)
            gpu_mask = self._morph_filter(cv2.MORPH_DILATE, config.morph_kernel, mat_type).apply(gpu_mask)
        return gpu_mask.download()

    def warp_affine(
        self,
        image: np.ndarray,
        transform: np.ndarray,
        size: tuple[int, int],
    ) -> np.ndarray:
        gpu_image = cv2.cuda_GpuMat()
        gpu_image.upload(image)
        gpu_warped = cv2.cuda.warpAffine(
            gpu_image,
            transform,
            size,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        return gpu_warped.download()

    def diff_and_mask(
        self,
        current_gray: np.ndarray,
        compare_gray: np.ndarray,
        config: MotionConfig,
    ) -> tuple[np.ndarray, np.ndarray]:
        gpu_current = cv2.cuda_GpuMat()
        gpu_compare = cv2.cuda_GpuMat()
        gpu_current.upload(current_gray)
        gpu_compare.upload(compare_gray)
        gpu_diff = cv2.cuda.absdiff(gpu_current, gpu_compare)
        diff = gpu_diff.download()
        return diff, self.cleanup_motion_mask(diff, config)


def create_cuda_processor(backend_info: BackendInfo) -> CudaMotionProcessor | None:
    if backend_info.used != "cuda":
        return None
    assert backend_info.cuda_device is not None
    try:
        return CudaMotionProcessor(backend_info.cuda_device)
    except Exception:
        return None


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
    payload = json.loads(mask_path.read_text(encoding="utf-8-sig"))
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


def roi_zone_points_mask(zone: RoiZone, mask_width: int, mask_height: int) -> np.ndarray:
    if mask_width <= 0 or mask_height <= 0:
        raise ValueError("ROI mask dimensions must be positive.")
    points = roi_zone_points_pixels(zone, mask_width, mask_height)
    points[:, 0] = np.clip(np.round(points[:, 0]), 0, mask_width - 1)
    points[:, 1] = np.clip(np.round(points[:, 1]), 0, mask_height - 1)
    return points.astype(np.int32)


def rasterize_roi_zones(
    roi_mask: RoiMask | None,
    mask_width: int,
    mask_height: int,
    zone_types: set[str],
) -> np.ndarray | None:
    if roi_mask is None or not roi_mask.zones:
        return None
    selected_zones = [zone for zone in roi_mask.zones if zone.type in zone_types]
    if not selected_zones:
        return None

    zone_mask = np.zeros((mask_height, mask_width), dtype=np.uint8)
    for zone in selected_zones:
        polygon = roi_zone_points_mask(zone, mask_width, mask_height)
        cv2.fillPoly(zone_mask, [polygon], 255)
    return zone_mask


def apply_roi_pixel_mask(raw_mask: np.ndarray, roi_mask: RoiMask | None) -> tuple[np.ndarray, bool]:
    if roi_mask is None or not roi_mask.zones:
        return raw_mask, False

    mask_height, mask_width = raw_mask.shape[:2]
    filtered_mask = raw_mask.copy()
    changed = False

    flight_mask = rasterize_roi_zones(roi_mask, mask_width, mask_height, {"flight"})
    if flight_mask is not None:
        filtered_mask = cv2.bitwise_and(filtered_mask, flight_mask)
        changed = True

    ignore_mask = rasterize_roi_zones(roi_mask, mask_width, mask_height, {"ignore"})
    if ignore_mask is not None:
        filtered_mask[ignore_mask > 0] = 0
        changed = True

    return filtered_mask, changed


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


def box_intersection_area(
    ax1: float,
    ay1: float,
    ax2: float,
    ay2: float,
    bx1: float,
    by1: float,
    bx2: float,
    by2: float,
) -> float:
    x1 = max(ax1, bx1)
    y1 = max(ay1, by1)
    x2 = min(ax2, bx2)
    y2 = min(ay2, by2)
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def point_in_semantic_box(x: float, y: float, semantic_detection: SemanticDetection) -> bool:
    return (
        semantic_detection.x1 <= x <= semantic_detection.x2
        and semantic_detection.y1 <= y <= semantic_detection.y2
    )


def semantic_overlap_ratio(
    detection: MotionDetection,
    semantic_detection: SemanticDetection,
) -> float:
    detection_area = max(1e-9, (detection.x2 - detection.x1) * (detection.y2 - detection.y1))
    intersection = box_intersection_area(
        detection.x1,
        detection.y1,
        detection.x2,
        detection.y2,
        semantic_detection.x1,
        semantic_detection.y1,
        semantic_detection.x2,
        semantic_detection.y2,
    )
    if intersection > 0.0:
        return float(np.clip(intersection / detection_area, 0.0, 1.0))
    if point_in_semantic_box(detection.center_x, detection.center_y, semantic_detection):
        return 1.0
    return 0.0


@dataclass(frozen=True)
class SemanticFilterResult:
    candidates: list[tuple[MotionDetection, np.ndarray]]
    rejected_count: int = 0
    penalized_count: int = 0


def filter_candidates_by_semantics(
    candidates: list[tuple[MotionDetection, np.ndarray]],
    semantic_detections: list[SemanticDetection],
    semantic_config: SemanticConfig | None,
) -> SemanticFilterResult:
    if semantic_config is None or not semantic_config.enabled or not semantic_detections:
        return SemanticFilterResult(candidates=candidates)

    config = semantic_config.normalized()
    semantic_detections = [
        semantic_detection
        for semantic_detection in semantic_detections
        if semantic_detection.label in config.labels
        and semantic_detection.confidence >= config.confidence
    ]
    if not semantic_detections:
        return SemanticFilterResult(candidates=candidates)

    kept: list[tuple[MotionDetection, np.ndarray]] = []
    rejected_count = 0
    penalized_count = 0
    for detection, contour in candidates:
        best_detection: SemanticDetection | None = None
        best_overlap = 0.0
        for semantic_detection in semantic_detections:
            overlap = semantic_overlap_ratio(detection, semantic_detection)
            if overlap > best_overlap:
                best_overlap = overlap
                best_detection = semantic_detection

        if best_detection is None or best_overlap < config.overlap_threshold:
            kept.append((detection, contour))
            continue

        tagged_detection = replace(
            detection,
            semantic_action=config.action,
            semantic_label=best_detection.label,
            semantic_confidence=best_detection.confidence,
            semantic_overlap=best_overlap,
        )
        if config.action == "reject":
            rejected_count += 1
            continue

        penalized_count += 1
        kept.append((tagged_detection, contour))

    return SemanticFilterResult(
        candidates=kept,
        rejected_count=rejected_count,
        penalized_count=penalized_count,
    )


class SemanticDetector:
    def __init__(self, config: SemanticConfig):
        self.config = config.normalized()
        self.last_detections: list[SemanticDetection] = []
        self.inference_count = 0
        self.skipped_count = 0
        self.last_inference_ran = False
        try:
            from huggingface_hub import hf_hub_download
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "Missing semantic filter dependencies. Install ultralytics and huggingface-hub."
            ) from exc

        weights_path = Path(self.config.weights) if self.config.weights else None
        if weights_path is None:
            weights_path = Path(
                hf_hub_download(
                    repo_id=self.config.model_repo,
                    filename=self.config.model_file,
                )
            )
        self.weights_path = weights_path
        self.model = YOLO(str(weights_path))

    def warmup(self, width: int, height: int) -> None:
        warmup_frame = np.zeros((max(1, height), max(1, width), 3), dtype=np.uint8)
        predict_kwargs: dict[str, Any] = {
            "conf": self.config.confidence,
            "iou": self.config.iou,
            "imgsz": self.config.image_size,
            "verbose": False,
        }
        if self.config.device is not None:
            predict_kwargs["device"] = self.config.device
        self.model.predict(warmup_frame, **predict_kwargs)
        self.last_detections = []
        self.last_inference_ran = False

    def detect(self, frame_bgr: np.ndarray, frame_index: int) -> list[SemanticDetection]:
        if frame_index % self.config.frame_stride != 0:
            self.skipped_count += 1
            self.last_inference_ran = False
            return self.last_detections

        predict_kwargs: dict[str, Any] = {
            "conf": self.config.confidence,
            "iou": self.config.iou,
            "imgsz": self.config.image_size,
            "verbose": False,
        }
        if self.config.device is not None:
            predict_kwargs["device"] = self.config.device

        results = self.model.predict(frame_bgr, **predict_kwargs)
        self.inference_count += 1
        self.last_inference_ran = True
        result = results[0]
        names = getattr(result, "names", {})
        boxes = getattr(result, "boxes", None)
        detections: list[SemanticDetection] = []
        if boxes is None:
            self.last_detections = detections
            return detections

        for box in boxes:
            class_id = int(box.cls[0].item())
            if isinstance(names, dict):
                raw_label = str(names.get(class_id, class_id))
            elif isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
                raw_label = str(names[class_id])
            else:
                raw_label = str(class_id)
            label = canonical_semantic_label(raw_label)
            if label not in self.config.labels:
                continue
            confidence = float(box.conf[0].item())
            x1, y1, x2, y2 = [float(value) for value in box.xyxy[0].tolist()]
            detections.append(
                SemanticDetection(
                    label=label,
                    raw_label=raw_label,
                    confidence=confidence,
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                )
            )

        detections.sort(key=lambda detection: detection.confidence, reverse=True)
        self.last_detections = detections
        return detections


@dataclass
class MotionTrack:
    track_id: int
    created_frame_index: int
    last_frame_index: int
    center_x: float
    center_y: float
    area: float
    hits: int = 1
    missed: int = 0
    seen_frames: list[int] = field(default_factory=list)
    displacements: list[tuple[float, float]] = field(default_factory=list)
    centers: list[tuple[float, float]] = field(default_factory=list)
    areas: list[float] = field(default_factory=list)
    aspects: list[float] = field(default_factory=list)
    last_dx: float = 0.0
    last_dy: float = 0.0
    last_width: float = 0.0
    last_height: float = 0.0

    def age_at(self, frame_index: int) -> int:
        return max(1, frame_index - self.created_frame_index + 1)

    def recent_hits(self, frame_index: int, window_frames: int) -> int:
        first_frame = frame_index - max(1, window_frames) + 1
        return sum(1 for seen_frame in self.seen_frames if seen_frame >= first_frame)

    def predicted_center(self, frame_index: int) -> tuple[float, float]:
        elapsed_frames = max(1, frame_index - self.last_frame_index)
        return (
            self.center_x + self.last_dx * elapsed_frames,
            self.center_y + self.last_dy * elapsed_frames,
        )


@dataclass(frozen=True)
class MotionTrackFilterResult:
    candidates: list[tuple[MotionDetection, np.ndarray]]
    temporal_rejected_count: int = 0
    unconfirmed_rejected_count: int = 0
    direction_rejected_count: int = 0
    drone_track_rejected_count: int = 0
    screen_decoy_rejected_count: int = 0
    occlusion_recovered_count: int = 0


@dataclass(frozen=True)
class MotionTrackStats:
    normalized_speed: float = 0.0
    area_cv: float = 0.0
    aspect_cv: float = 0.0
    path_smoothness: float = 0.0
    perimeter_fraction: float = 0.0
    drone_score: float = 0.0
    screen_decoy_score: float = 0.0


def coefficient_of_variation(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    array = np.asarray(values, dtype=np.float32)
    mean = float(np.mean(array))
    if abs(mean) < 1e-9:
        return 0.0
    return float(np.std(array) / abs(mean))


def track_path_smoothness(centers: list[tuple[float, float]]) -> float:
    if len(centers) < 3:
        return 0.0
    points = np.asarray(centers, dtype=np.float32)
    steps = np.linalg.norm(points[1:] - points[:-1], axis=1)
    path_length = float(np.sum(steps))
    if path_length < 1e-9:
        return 0.0
    net_displacement = float(np.linalg.norm(points[-1] - points[0]))
    return float(np.clip(net_displacement / path_length, 0.0, 1.0))


def track_perimeter_fraction(
    centers: list[tuple[float, float]],
    image_width: int,
    image_height: int,
    margin_fraction: float,
) -> float:
    if not centers or image_width <= 0 or image_height <= 0 or margin_fraction <= 0.0:
        return 0.0
    margin_x = image_width * margin_fraction
    margin_y = image_height * margin_fraction
    perimeter_hits = 0
    for center_x, center_y in centers:
        if (
            center_x <= margin_x
            or center_x >= image_width - margin_x
            or center_y <= margin_y
            or center_y >= image_height - margin_y
        ):
            perimeter_hits += 1
    return perimeter_hits / float(len(centers))


class MotionTracker:
    HISTORY_LIMIT = 30

    def __init__(self, config: MotionConfig):
        self.config = config.normalized()
        self.tracks: dict[int, MotionTrack] = {}
        self.next_track_id = 1

    @property
    def active(self) -> bool:
        return (
            self.config.temporal_filter
            or self.config.track_confirmation
            or self.config.direction_consistency
            or self.config.drone_track_filter
            or self.config.screen_decoy_rejection
            or self.config.occlusion_recovery
        )

    def reset(self) -> None:
        self.tracks.clear()
        self.next_track_id = 1

    def filter_candidates(
        self,
        candidates: list[tuple[MotionDetection, np.ndarray]],
        frame_index: int,
        image_width: int = 0,
        image_height: int = 0,
    ) -> MotionTrackFilterResult:
        if not self.active:
            return MotionTrackFilterResult(candidates)

        assignments: list[tuple[MotionDetection, np.ndarray, MotionTrack, bool]] = []
        matched_track_ids: set[int] = set()
        max_missed = self._max_missed_frames()
        active_tracks = [
            track
            for track in self.tracks.values()
            if frame_index - track.last_frame_index <= max_missed + 1
        ]
        occlusion_recovered = 0

        for detection, contour in sorted(candidates, key=lambda item: item[0].area, reverse=True):
            track = self._best_track_for_detection(
                detection,
                active_tracks,
                matched_track_ids,
                frame_index,
            )
            recovered_from_occlusion = (
                track is not None and self.config.occlusion_recovery and track.missed > 0
            )
            if track is None:
                track = self._create_track(detection, frame_index)
            else:
                self._update_track(track, detection, frame_index)
            if recovered_from_occlusion:
                occlusion_recovered += 1
            matched_track_ids.add(track.track_id)
            assignments.append((detection, contour, track, recovered_from_occlusion))

        self._age_unmatched_tracks(matched_track_ids)

        kept: list[tuple[MotionDetection, np.ndarray]] = []
        temporal_rejected = 0
        unconfirmed_rejected = 0
        direction_rejected = 0
        drone_track_rejected = 0
        screen_decoy_rejected = 0

        for detection, contour, track, recovered_from_occlusion in assignments:
            recent_hits = track.recent_hits(frame_index, self.config.temporal_window_frames)
            track_confirmed = track.hits >= self.config.track_confirm_hits
            direction_consistent = self._direction_consistent(track)
            stats = self._track_stats(track, image_width, image_height)
            annotated = replace(
                detection,
                track_id=track.track_id,
                track_age=track.age_at(frame_index),
                track_hits=track.hits,
                track_confirmed=track_confirmed,
                motion_dx=track.last_dx,
                motion_dy=track.last_dy,
                direction_consistent=direction_consistent,
                normalized_speed=stats.normalized_speed,
                track_area_cv=stats.area_cv,
                track_aspect_cv=stats.aspect_cv,
                track_path_smoothness=stats.path_smoothness,
                track_perimeter_fraction=stats.perimeter_fraction,
                drone_score=stats.drone_score,
                screen_decoy_score=stats.screen_decoy_score,
                occlusion_recovered=recovered_from_occlusion,
            )

            if self.config.temporal_filter and recent_hits < self.config.temporal_min_hits:
                temporal_rejected += 1
                continue
            if self.config.track_confirmation and not track_confirmed:
                unconfirmed_rejected += 1
                continue
            if self.config.direction_consistency and not direction_consistent:
                direction_rejected += 1
                continue
            track_rejection_reason = self._track_rejection_reason(track, stats)
            if track_rejection_reason == "drone_track":
                drone_track_rejected += 1
                continue
            if track_rejection_reason == "screen_decoy":
                screen_decoy_rejected += 1
                continue
            kept.append((annotated, contour))

        return MotionTrackFilterResult(
            candidates=kept,
            temporal_rejected_count=temporal_rejected,
            unconfirmed_rejected_count=unconfirmed_rejected,
            direction_rejected_count=direction_rejected,
            drone_track_rejected_count=drone_track_rejected,
            screen_decoy_rejected_count=screen_decoy_rejected,
            occlusion_recovered_count=occlusion_recovered,
        )

    def _best_track_for_detection(
        self,
        detection: MotionDetection,
        active_tracks: list[MotionTrack],
        matched_track_ids: set[int],
        frame_index: int,
    ) -> MotionTrack | None:
        best_track: MotionTrack | None = None
        best_distance = float("inf")
        for track in active_tracks:
            if track.track_id in matched_track_ids:
                continue
            track_x, track_y = track.center_x, track.center_y
            if self.config.occlusion_recovery and track.missed > 0:
                track_x, track_y = track.predicted_center(frame_index)
            distance = float(
                np.hypot(detection.center_x - track_x, detection.center_y - track_y)
            )
            area_scale = max(np.sqrt(max(detection.area, track.area, 1.0)) * 1.5, 1.0)
            distance_limit = max(self.config.track_match_distance, area_scale)
            if self.config.occlusion_recovery and track.missed > 0:
                distance_limit = max(distance_limit, self.config.occlusion_gate_distance)
            if distance <= distance_limit and distance < best_distance:
                best_track = track
                best_distance = distance
        return best_track

    def _create_track(self, detection: MotionDetection, frame_index: int) -> MotionTrack:
        track = MotionTrack(
            track_id=self.next_track_id,
            created_frame_index=frame_index,
            last_frame_index=frame_index,
            center_x=detection.center_x,
            center_y=detection.center_y,
            area=detection.area,
            seen_frames=[frame_index],
            centers=[(detection.center_x, detection.center_y)],
            areas=[float(detection.area)],
            aspects=[self._detection_aspect(detection)],
            last_width=max(0.0, detection.x2 - detection.x1),
            last_height=max(0.0, detection.y2 - detection.y1),
        )
        self.tracks[track.track_id] = track
        self.next_track_id += 1
        return track

    def _update_track(
        self,
        track: MotionTrack,
        detection: MotionDetection,
        frame_index: int,
    ) -> None:
        elapsed_frames = max(1, frame_index - track.last_frame_index)
        dx = (detection.center_x - track.center_x) / elapsed_frames
        dy = (detection.center_y - track.center_y) / elapsed_frames
        if frame_index != track.last_frame_index:
            track.displacements.append((float(dx), float(dy)))
            track.displacements = track.displacements[-self.HISTORY_LIMIT:]
        track.center_x = detection.center_x
        track.center_y = detection.center_y
        track.area = detection.area
        track.last_width = max(0.0, detection.x2 - detection.x1)
        track.last_height = max(0.0, detection.y2 - detection.y1)
        track.last_frame_index = frame_index
        track.hits += 1
        track.missed = 0
        track.seen_frames.append(frame_index)
        track.seen_frames = track.seen_frames[-max(10, self.config.temporal_window_frames):]
        track.centers.append((detection.center_x, detection.center_y))
        track.centers = track.centers[-self.HISTORY_LIMIT:]
        track.areas.append(float(detection.area))
        track.areas = track.areas[-self.HISTORY_LIMIT:]
        track.aspects.append(self._detection_aspect(detection))
        track.aspects = track.aspects[-self.HISTORY_LIMIT:]
        track.last_dx = float(dx)
        track.last_dy = float(dy)

    def _age_unmatched_tracks(self, matched_track_ids: set[int]) -> None:
        expired_track_ids: list[int] = []
        max_missed = self._max_missed_frames()
        for track_id, track in self.tracks.items():
            if track_id not in matched_track_ids:
                track.missed += 1
            if track.missed > max_missed:
                expired_track_ids.append(track_id)
        for track_id in expired_track_ids:
            del self.tracks[track_id]

    def _direction_consistent(self, track: MotionTrack) -> bool:
        if track.hits < self.config.direction_min_hits:
            return True
        if len(track.displacements) < 2:
            return True

        current = np.array(track.displacements[-1], dtype=np.float32)
        previous = np.array(track.displacements[:-1], dtype=np.float32)
        previous_mean = previous.mean(axis=0)
        current_norm = float(np.linalg.norm(current))
        previous_norm = float(np.linalg.norm(previous_mean))
        if current_norm < self.config.direction_min_displacement:
            return False
        if previous_norm < self.config.direction_min_displacement:
            return True
        cosine = float(np.dot(current, previous_mean) / max(current_norm * previous_norm, 1e-9))
        return cosine >= self.config.direction_cosine

    def _max_missed_frames(self) -> int:
        if self.config.occlusion_recovery:
            return max(self.config.track_max_missed, self.config.occlusion_max_frames)
        return self.config.track_max_missed

    def _track_rejection_reason(
        self,
        track: MotionTrack,
        stats: MotionTrackStats,
    ) -> str | None:
        if self.config.drone_track_filter:
            speed_max = max(
                self.config.drone_min_normalized_speed,
                self.config.drone_max_normalized_speed,
            )
            if track.hits < self.config.drone_min_track_hits:
                return "drone_track"
            if stats.normalized_speed < self.config.drone_min_normalized_speed:
                return "drone_track"
            if stats.normalized_speed > speed_max:
                return "drone_track"

        if self.config.screen_decoy_rejection and track.hits >= self.config.screen_min_track_hits:
            perimeter_ok = stats.perimeter_fraction >= self.config.screen_min_perimeter_fraction
            stable_area = stats.area_cv <= self.config.screen_max_area_cv
            stable_aspect = stats.aspect_cv <= self.config.screen_max_aspect_cv
            smooth_path = stats.path_smoothness >= self.config.screen_min_path_smoothness
            if perimeter_ok and stable_area and stable_aspect and smooth_path:
                return "screen_decoy"
        return None

    def _track_stats(
        self,
        track: MotionTrack,
        image_width: int,
        image_height: int,
    ) -> MotionTrackStats:
        normalized_speed = self._normalized_speed(track)
        area_cv = coefficient_of_variation(track.areas)
        aspect_cv = coefficient_of_variation(track.aspects)
        path_smoothness = track_path_smoothness(track.centers)
        perimeter_fraction = track_perimeter_fraction(
            track.centers,
            image_width,
            image_height,
            self.config.screen_perimeter_margin,
        )
        screen_decoy_score = self._screen_decoy_score(
            area_cv,
            aspect_cv,
            path_smoothness,
            perimeter_fraction,
        )
        drone_score = self._drone_score(track, normalized_speed, screen_decoy_score)
        return MotionTrackStats(
            normalized_speed=normalized_speed,
            area_cv=area_cv,
            aspect_cv=aspect_cv,
            path_smoothness=path_smoothness,
            perimeter_fraction=perimeter_fraction,
            drone_score=drone_score,
            screen_decoy_score=screen_decoy_score,
        )

    def _normalized_speed(self, track: MotionTrack) -> float:
        if not track.displacements:
            return 0.0
        speed_px = float(np.hypot(track.displacements[-1][0], track.displacements[-1][1]))
        object_scale = max(1.0, float(np.sqrt(max(track.area, 1.0))))
        return speed_px / object_scale

    def _screen_decoy_score(
        self,
        area_cv: float,
        aspect_cv: float,
        path_smoothness: float,
        perimeter_fraction: float,
    ) -> float:
        area_component = 1.0 - float(
            np.clip(area_cv / max(self.config.screen_max_area_cv, 1e-9), 0.0, 1.0)
        )
        aspect_component = 1.0 - float(
            np.clip(aspect_cv / max(self.config.screen_max_aspect_cv, 1e-9), 0.0, 1.0)
        )
        smooth_component = float(np.clip(path_smoothness, 0.0, 1.0))
        if self.config.screen_min_perimeter_fraction > 0.0:
            perimeter_component = float(
                np.clip(
                    perimeter_fraction / self.config.screen_min_perimeter_fraction,
                    0.0,
                    1.0,
                )
            )
        else:
            perimeter_component = float(np.clip(perimeter_fraction, 0.0, 1.0))
        score = (
            0.30 * area_component
            + 0.25 * aspect_component
            + 0.30 * smooth_component
            + 0.15 * perimeter_component
        )
        return float(np.clip(score, 0.0, 1.0))

    def _drone_score(
        self,
        track: MotionTrack,
        normalized_speed: float,
        screen_decoy_score: float,
    ) -> float:
        min_hits = max(1, self.config.drone_min_track_hits)
        hit_component = float(np.clip(track.hits / float(min_hits), 0.0, 1.0))
        speed_max = max(
            self.config.drone_min_normalized_speed,
            self.config.drone_max_normalized_speed,
        )
        speed_component = (
            1.0
            if self.config.drone_min_normalized_speed
            <= normalized_speed
            <= speed_max
            else 0.0
        )
        anti_screen_component = 1.0 - float(np.clip(screen_decoy_score, 0.0, 1.0))
        score = 0.35 * hit_component + 0.40 * speed_component + 0.25 * anti_screen_component
        return float(np.clip(score, 0.0, 1.0))

    @staticmethod
    def _detection_aspect(detection: MotionDetection) -> float:
        width = max(1e-9, detection.x2 - detection.x1)
        height = max(1e-9, detection.y2 - detection.y1)
        return float(width / height)


def prepare_gray(
    frame_bgr: np.ndarray,
    config: MotionConfig,
    cuda_processor: CudaMotionProcessor | None = None,
) -> np.ndarray:
    config = config.normalized()
    if cuda_processor is not None:
        try:
            return cuda_processor.prepare_gray(frame_bgr, config)
        except Exception as exc:
            raise RuntimeError(f"CUDA grayscale preparation failed: {exc}") from exc

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


def cleanup_motion_mask(
    diff: np.ndarray,
    config: MotionConfig,
    morph_kernel_matrix: np.ndarray | None = None,
) -> np.ndarray:
    config = config.normalized()
    if config.hysteresis:
        _, low_mask = cv2.threshold(diff, config.diff_threshold, 255, cv2.THRESH_BINARY)
        _, high_mask = cv2.threshold(
            diff,
            config.hysteresis_high_threshold,
            255,
            cv2.THRESH_BINARY,
        )
        label_count, labels = cv2.connectedComponents(low_mask)
        if label_count <= 1:
            mask = np.zeros_like(low_mask)
        else:
            seed_labels = np.unique(labels[high_mask > 0])
            seed_labels = seed_labels[seed_labels != 0]
            if seed_labels.size == 0:
                mask = np.zeros_like(low_mask)
            else:
                mask = np.isin(labels, seed_labels).astype(np.uint8) * 255
    else:
        _, mask = cv2.threshold(diff, config.diff_threshold, 255, cv2.THRESH_BINARY)
    if config.morph_kernel > 1:
        kernel = (
            morph_kernel_matrix
            if morph_kernel_matrix is not None
            else np.ones((config.morph_kernel, config.morph_kernel), dtype=np.uint8)
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def estimate_global_shift(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    min_vectors: int = 12,
    consensus_px: float = 2.0,
    max_corners: int = 240,
) -> tuple[float, float, float, int]:
    points = cv2.goodFeaturesToTrack(
        previous_gray,
        maxCorners=max(12, int(max_corners)),
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


@dataclass(frozen=True)
class ShakeEstimate:
    dx: float = 0.0
    dy: float = 0.0
    consensus: float = 0.0
    tracked_vectors: int = 0
    estimated: bool = False
    reused: bool = False


def resize_gray_for_shake(gray: np.ndarray, scale: float) -> np.ndarray:
    if scale >= 0.999:
        return gray
    return cv2.resize(gray, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def estimate_configured_global_shift(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    config: MotionConfig,
) -> ShakeEstimate:
    config = config.normalized()
    scale = config.shake_analysis_scale
    previous_for_shift = resize_gray_for_shake(previous_gray, scale)
    current_for_shift = resize_gray_for_shake(current_gray, scale)
    dx, dy, consensus, tracked_vectors = estimate_global_shift(
        previous_for_shift,
        current_for_shift,
        consensus_px=max(0.1, config.shake_consensus_px * scale),
        max_corners=config.shake_max_corners,
    )
    if scale < 0.999:
        dx /= scale
        dy /= scale
    return ShakeEstimate(
        dx=dx,
        dy=dy,
        consensus=consensus,
        tracked_vectors=tracked_vectors,
        estimated=True,
    )


class ShakeEstimator:
    def __init__(self, config: MotionConfig):
        self.config = config.normalized()
        self.last_estimate: ShakeEstimate | None = None
        self.frames_until_estimate = 0

    def estimate(self, previous_gray: np.ndarray, current_gray: np.ndarray) -> ShakeEstimate:
        if self.last_estimate is not None and self.frames_until_estimate > 0:
            self.frames_until_estimate -= 1
            return replace(self.last_estimate, estimated=False, reused=True)

        estimate = estimate_configured_global_shift(previous_gray, current_gray, self.config)
        self.last_estimate = estimate
        self.frames_until_estimate = max(0, self.config.shake_frame_stride - 1)
        return estimate


def analyze_gray_pair(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    config: MotionConfig,
    image_width: int,
    image_height: int,
    roi_mask: RoiMask | None = None,
    semantic_detections: list[SemanticDetection] | None = None,
    semantic_config: SemanticConfig | None = None,
    motion_tracker: MotionTracker | None = None,
    shake_estimator: ShakeEstimator | None = None,
    frame_index: int = 0,
    build_mask: bool = True,
    morph_kernel_matrix: np.ndarray | None = None,
    cuda_processor: CudaMotionProcessor | None = None,
) -> MotionAnalysis:
    config = config.normalized()
    semantic_detections = semantic_detections or []
    compare_gray = previous_gray
    global_dx = 0.0
    global_dy = 0.0
    global_consensus = 0.0
    tracked_vectors = 0
    global_motion_detected = False
    shake_estimated = False
    shake_reused = False
    if config.shake_protection:
        shake = (
            shake_estimator.estimate(previous_gray, current_gray)
            if shake_estimator is not None
            else estimate_configured_global_shift(previous_gray, current_gray, config)
        )
        global_dx = shake.dx
        global_dy = shake.dy
        global_consensus = shake.consensus
        tracked_vectors = shake.tracked_vectors
        shake_estimated = shake.estimated
        shake_reused = shake.reused
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
            if cuda_processor is not None:
                compare_gray = cuda_processor.warp_affine(
                    previous_gray,
                    transform,
                    (current_gray.shape[1], current_gray.shape[0]),
                )
            else:
                compare_gray = cv2.warpAffine(
                    previous_gray,
                    transform,
                    (current_gray.shape[1], current_gray.shape[0]),
                    flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE,
                )

    if cuda_processor is not None:
        _diff, raw_mask = cuda_processor.diff_and_mask(current_gray, compare_gray, config)
    else:
        diff = cv2.absdiff(current_gray, compare_gray)
        raw_mask = cleanup_motion_mask(diff, config, morph_kernel_matrix=morph_kernel_matrix)
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
            shake_estimated=shake_estimated,
            shake_reused=shake_reused,
            semantic_detections=semantic_detections,
            semantic_detection_count=len(semantic_detections),
        )

    scale_x = raw_mask.shape[1] / float(image_width)
    scale_y = raw_mask.shape[0] / float(image_height)
    area_scale = max(scale_x * scale_y, 1e-9)
    accepted_mask = np.zeros_like(raw_mask)

    def candidates_from_mask(mask: np.ndarray) -> list[tuple[MotionDetection, np.ndarray]]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[tuple[MotionDetection, np.ndarray]] = []
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
            candidates.append(
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
        return candidates

    raw_candidates = candidates_from_mask(raw_mask)
    roi_filtered_mask, roi_pixel_mask_active = apply_roi_pixel_mask(raw_mask, roi_mask)
    roi_candidates = candidates_from_mask(roi_filtered_mask) if roi_pixel_mask_active else raw_candidates
    roi_rejected_count = max(0, len(raw_candidates) - len(roi_candidates)) if roi_pixel_mask_active else 0
    roi_penalized_count = 0
    penalty_roi_mask = None
    if roi_mask is not None:
        penalty_zones = tuple(zone for zone in roi_mask.zones if zone.type == "penalty")
        if penalty_zones:
            penalty_roi_mask = RoiMask(
                version=roi_mask.version,
                mode=roi_mask.mode,
                zones=penalty_zones,
            )

    kept_candidates: list[tuple[MotionDetection, np.ndarray]] = []
    for detection, contour in roi_candidates:
        filtered_detection = filter_detection_by_roi(
            detection,
            penalty_roi_mask,
            image_width,
            image_height,
        )
        if filtered_detection is None:
            roi_rejected_count += 1
            continue
        if filtered_detection.roi_action == "penalize":
            roi_penalized_count += 1
        kept_candidates.append((filtered_detection, contour))

    semantic_rejected_count = 0
    semantic_penalized_count = 0
    semantic_filter_result = filter_candidates_by_semantics(
        kept_candidates,
        semantic_detections,
        semantic_config,
    )
    kept_candidates = semantic_filter_result.candidates
    semantic_rejected_count = semantic_filter_result.rejected_count
    semantic_penalized_count = semantic_filter_result.penalized_count

    temporal_rejected_count = 0
    unconfirmed_rejected_count = 0
    direction_rejected_count = 0
    drone_track_rejected_count = 0
    screen_decoy_rejected_count = 0
    occlusion_recovered_count = 0
    if motion_tracker is not None:
        track_filter_result = motion_tracker.filter_candidates(
            kept_candidates,
            frame_index,
            image_width=image_width,
            image_height=image_height,
        )
        kept_candidates = track_filter_result.candidates
        temporal_rejected_count = track_filter_result.temporal_rejected_count
        unconfirmed_rejected_count = track_filter_result.unconfirmed_rejected_count
        direction_rejected_count = track_filter_result.direction_rejected_count
        drone_track_rejected_count = track_filter_result.drone_track_rejected_count
        screen_decoy_rejected_count = track_filter_result.screen_decoy_rejected_count
        occlusion_recovered_count = track_filter_result.occlusion_recovered_count

    if build_mask:
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
        shake_estimated,
        shake_reused,
        len(raw_candidates),
        roi_rejected_count,
        roi_penalized_count,
        semantic_detections,
        len(semantic_detections),
        semantic_rejected_count,
        semantic_penalized_count,
        temporal_rejected_count,
        unconfirmed_rejected_count,
        direction_rejected_count,
        drone_track_rejected_count,
        screen_decoy_rejected_count,
        occlusion_recovered_count,
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
    semantic_detections: list[SemanticDetection] | None = None,
    global_motion_rejected: bool = False,
    global_motion_detected: bool = False,
    global_dx: float = 0.0,
    global_dy: float = 0.0,
    global_consensus: float = 0.0,
    roi_active: bool = False,
    roi_rejected_count: int = 0,
    roi_penalized_count: int = 0,
    motion_filters_active: bool = False,
    temporal_rejected_count: int = 0,
    unconfirmed_rejected_count: int = 0,
    direction_rejected_count: int = 0,
    drone_track_rejected_count: int = 0,
    screen_decoy_rejected_count: int = 0,
    occlusion_recovered_count: int = 0,
    semantic_active: bool = False,
    semantic_rejected_count: int = 0,
    semantic_penalized_count: int = 0,
) -> np.ndarray:
    output = frame_bgr.copy()
    semantic_detections = semantic_detections or []
    for semantic_detection in semantic_detections:
        x1, y1, x2, y2 = (
            int(round(semantic_detection.x1)),
            int(round(semantic_detection.y1)),
            int(round(semantic_detection.x2)),
            int(round(semantic_detection.y2)),
        )
        color = (80, 255, 80) if semantic_detection.label == "person" else (255, 200, 0)
        cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)
        label = f"{semantic_detection.label} {semantic_detection.confidence:.2f}"
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
    for detection in detections:
        x1, y1, x2, y2 = (
            int(round(detection.x1)),
            int(round(detection.y1)),
            int(round(detection.x2)),
            int(round(detection.y2)),
        )
        color = (0, 165, 255) if detection.roi_action == "penalize" else (0, 255, 255)
        if detection.semantic_action == "penalize":
            color = (255, 80, 255)
        cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)
        label = "drone"
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
    if motion_filters_active:
        cv2.putText(
            output,
            (
                f"Filter rejected temporal={temporal_rejected_count} "
                f"unconfirmed={unconfirmed_rejected_count} direction={direction_rejected_count} "
                f"drone={drone_track_rejected_count} screen={screen_decoy_rejected_count} "
                f"recovered={occlusion_recovered_count}"
            ),
            (12, 84),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    if semantic_active:
        cv2.putText(
            output,
            (
                f"Semantic objects={len(semantic_detections)} "
                f"rejected={semantic_rejected_count} penalized={semantic_penalized_count}"
            ),
            (12, 112),
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
        "shake_frame_stride": config.shake_frame_stride,
        "shake_analysis_scale": config.shake_analysis_scale,
        "shake_max_corners": config.shake_max_corners,
        "hysteresis": config.hysteresis,
        "hysteresis_high_threshold": config.hysteresis_high_threshold,
        "temporal_filter": config.temporal_filter,
        "temporal_window_frames": config.temporal_window_frames,
        "temporal_min_hits": config.temporal_min_hits,
        "track_confirmation": config.track_confirmation,
        "track_confirm_hits": config.track_confirm_hits,
        "track_max_missed": config.track_max_missed,
        "track_match_distance": config.track_match_distance,
        "direction_consistency": config.direction_consistency,
        "direction_min_hits": config.direction_min_hits,
        "direction_min_displacement": config.direction_min_displacement,
        "direction_cosine": config.direction_cosine,
        "drone_track_filter": config.drone_track_filter,
        "drone_min_track_hits": config.drone_min_track_hits,
        "drone_min_normalized_speed": config.drone_min_normalized_speed,
        "drone_max_normalized_speed": config.drone_max_normalized_speed,
        "screen_decoy_rejection": config.screen_decoy_rejection,
        "screen_min_track_hits": config.screen_min_track_hits,
        "screen_max_area_cv": config.screen_max_area_cv,
        "screen_max_aspect_cv": config.screen_max_aspect_cv,
        "screen_min_path_smoothness": config.screen_min_path_smoothness,
        "screen_min_perimeter_fraction": config.screen_min_perimeter_fraction,
        "screen_perimeter_margin": config.screen_perimeter_margin,
        "occlusion_recovery": config.occlusion_recovery,
        "occlusion_max_frames": config.occlusion_max_frames,
        "occlusion_gate_distance": config.occlusion_gate_distance,
    }


def serialize_semantic_config(config: SemanticConfig) -> dict[str, Any]:
    config = config.normalized()
    return {
        "enabled": config.enabled,
        "labels": list(config.labels),
        "action": config.action,
        "model_repo": config.model_repo,
        "model_file": config.model_file,
        "weights": config.weights,
        "confidence": config.confidence,
        "iou": config.iou,
        "image_size": config.image_size,
        "device": config.device,
        "frame_stride": config.frame_stride,
        "overlap_threshold": config.overlap_threshold,
        "warmup": config.warmup,
        "motion_gate": config.motion_gate,
    }


def request_stop(_signum: int, _frame: Any) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def stop_requested(args: argparse.Namespace) -> bool:
    if STOP_REQUESTED:
        return True
    stop_file = getattr(args, "stop_file", None)
    return bool(stop_file and Path(stop_file).exists())


def emit_progress(args: argparse.Namespace, stage: str, **payload: Any) -> None:
    if not getattr(args, "progress_json", False):
        return
    record = {"type": "progress", "stage": stage, **payload}
    print(
        f"{PROGRESS_PREFIX}{json.dumps(record, separators=(',', ':'))}",
        file=sys.stderr,
        flush=True,
    )


def progress_payload(
    stage: str,
    started_at: float,
    processed_frames: int,
    total_frames: int,
    recent_frames: int | None = None,
    recent_elapsed_s: float | None = None,
    **payload: Any,
) -> dict[str, Any]:
    elapsed_s = max(0.0, time.time() - started_at)
    processing_fps = processed_frames / elapsed_s if elapsed_s > 0.0 else 0.0
    recent_fps = (
        recent_frames / recent_elapsed_s
        if recent_frames is not None and recent_elapsed_s is not None and recent_elapsed_s > 0.0
        else None
    )
    if total_frames > 0:
        remaining_frames = max(0, total_frames - processed_frames)
        progress = float(np.clip(processed_frames / float(total_frames), 0.0, 1.0))
    else:
        remaining_frames = None
        progress = None
    eta_fps = recent_fps if recent_fps is not None and recent_fps > 0.0 else processing_fps
    eta_s = remaining_frames / eta_fps if remaining_frames is not None and eta_fps > 0.0 else None
    return {
        "processed_frames": int(processed_frames),
        "total_frames": int(total_frames),
        "remaining_frames": remaining_frames,
        "progress": round(progress, 6) if progress is not None else None,
        "elapsed_s": round(elapsed_s, 3),
        "eta_s": round(eta_s, 3) if eta_s is not None else None,
        "processing_fps": round(processing_fps, 3),
        "average_fps": round(processing_fps, 3),
        "recent_fps": round(recent_fps, 3) if recent_fps is not None else None,
        "processing_ms_per_frame": (
            round((elapsed_s / max(1, processed_frames)) * 1000.0, 3)
            if processed_frames > 0
            else None
        ),
        "average_ms_per_frame": (
            round((elapsed_s / max(1, processed_frames)) * 1000.0, 3)
            if processed_frames > 0
            else None
        ),
        "recent_ms_per_frame": (
            round((recent_elapsed_s / max(1, recent_frames)) * 1000.0, 3)
            if recent_frames is not None
            and recent_elapsed_s is not None
            and recent_frames > 0
            else None
        ),
        **payload,
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
    global STOP_REQUESTED
    STOP_REQUESTED = False
    try:
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
    except ValueError:
        pass

    run_started_at = time.time()
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
        shake_frame_stride=args.shake_frame_stride,
        shake_analysis_scale=args.shake_analysis_scale,
        shake_max_corners=args.shake_max_corners,
        hysteresis=args.enable_hysteresis,
        hysteresis_high_threshold=args.hysteresis_high_threshold,
        temporal_filter=args.enable_temporal_filter,
        temporal_window_frames=args.temporal_window_frames,
        temporal_min_hits=args.temporal_min_hits,
        track_confirmation=args.enable_track_confirmation,
        track_confirm_hits=args.track_confirm_hits,
        track_max_missed=args.track_max_missed,
        track_match_distance=args.track_match_distance,
        direction_consistency=args.enable_direction_consistency,
        direction_min_hits=args.direction_min_hits,
        direction_min_displacement=args.direction_min_displacement,
        direction_cosine=args.direction_cosine,
        drone_track_filter=args.enable_drone_track_filter,
        drone_min_track_hits=args.drone_min_track_hits,
        drone_min_normalized_speed=args.drone_min_normalized_speed,
        drone_max_normalized_speed=args.drone_max_normalized_speed,
        screen_decoy_rejection=args.enable_screen_decoy_rejection,
        screen_min_track_hits=args.screen_min_track_hits,
        screen_max_area_cv=args.screen_max_area_cv,
        screen_max_aspect_cv=args.screen_max_aspect_cv,
        screen_min_path_smoothness=args.screen_min_path_smoothness,
        screen_min_perimeter_fraction=args.screen_min_perimeter_fraction,
        screen_perimeter_margin=args.screen_perimeter_margin,
        occlusion_recovery=args.enable_occlusion_recovery,
        occlusion_max_frames=args.occlusion_max_frames,
        occlusion_gate_distance=args.occlusion_gate_distance,
    ).normalized()
    backend_info = resolve_backend(args.backend, int(args.cuda_device))
    cuda_processor = create_cuda_processor(backend_info)
    if backend_info.used == "cuda" and cuda_processor is None:
        backend_info = replace(
            backend_info,
            used="cpu",
            cuda_device=None,
            message=(
                f"{backend_info.message} CUDA initialization failed; "
                "motion backend fell back to CPU."
            ),
        )
    backend_info = apply_semantic_device_override(
        backend_info,
        getattr(args, "semantic_device", None),
    )
    roi_mask = load_roi_mask(args.roi_mask)
    motion_tracker = MotionTracker(config)
    semantic_config = SemanticConfig(
        enabled=args.enable_semantic_filter,
        labels=tuple(part.strip() for part in args.semantic_labels.split(",") if part.strip()),
        action=args.semantic_action,
        model_repo=args.semantic_model_repo,
        model_file=args.semantic_model_file,
        weights=args.semantic_weights,
        confidence=args.semantic_conf,
        iou=args.semantic_iou,
        image_size=args.semantic_imgsz,
        device=backend_info.semantic_device,
        frame_stride=args.semantic_frame_stride,
        overlap_threshold=args.semantic_overlap_threshold,
        warmup=args.semantic_warmup,
        motion_gate=args.semantic_motion_gate,
    ).normalized()
    out_dir = make_output_dir(args.out_dir)
    write_motion_video = not args.no_motion_video
    write_overlay_video = not args.no_overlay_video
    write_jsonl = not args.no_jsonl
    start_frame = max(0, int(args.start_frame))
    max_frames = max(0, int(args.max_frames))

    emit_progress(
        args,
        "opening_video",
        message="Opening video and reading metadata.",
        source=str(source),
        backend=backend_info.to_json_dict(),
    )
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {source}")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    total_video_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if width <= 0 or height <= 0:
        capture.release()
        raise RuntimeError(f"Could not read video dimensions: {source}")

    if start_frame > 0:
        capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    target_frame_count = max(0, total_video_frames - start_frame) if total_video_frames > 0 else 0
    if max_frames > 0:
        target_frame_count = min(target_frame_count, max_frames) if target_frame_count > 0 else max_frames

    semantic_detector: SemanticDetector | None = None
    if semantic_config.enabled:
        emit_progress(
            args,
            "loading_semantic_model",
            **progress_payload(
                "loading_semantic_model",
                run_started_at,
                0,
                target_frame_count,
                message="Loading semantic model weights.",
                model_repo=semantic_config.model_repo,
                model_file=semantic_config.model_file,
            ),
        )
        try:
            semantic_detector = SemanticDetector(semantic_config)
        except Exception:
            capture.release()
            raise
        emit_progress(
            args,
            "semantic_model_ready",
            **progress_payload(
                "semantic_model_ready",
                run_started_at,
                0,
                target_frame_count,
                message="Semantic model loaded.",
                weights_path=str(semantic_detector.weights_path),
            ),
        )
        if semantic_config.warmup:
            emit_progress(
                args,
                "warming_semantic_model",
                **progress_payload(
                    "warming_semantic_model",
                    run_started_at,
                    0,
                    target_frame_count,
                    message="Warming semantic model before timed frame processing.",
                ),
            )
            semantic_detector.warmup(width, height)
            emit_progress(
                args,
                "semantic_model_warm",
                **progress_payload(
                    "semantic_model_warm",
                    run_started_at,
                    0,
                    target_frame_count,
                    message="Semantic model warmup complete.",
                ),
            )

    motion_only_path = out_dir / f"{source.stem}_motion_only.mp4"
    overlay_path = out_dir / f"{source.stem}_motion_overlay.mp4"
    jsonl_path = out_dir / "motion_detections.jsonl"
    summary_path = out_dir / "summary.json"

    motion_writer = open_video_writer(motion_only_path, fps, width, height) if write_motion_video else None
    overlay_writer = open_video_writer(overlay_path, fps, width, height) if write_overlay_video else None
    jsonl = jsonl_path.open("w", encoding="utf-8") if write_jsonl else None
    shake_estimator = ShakeEstimator(config) if config.shake_protection else None
    morph_kernel_matrix = (
        np.ones((config.morph_kernel, config.morph_kernel), dtype=np.uint8)
        if config.morph_kernel > 1
        else None
    )

    previous_gray: np.ndarray | None = None
    trail_masks: list[np.ndarray] = []
    max_trail_masks = max(1, config.trail_frames + 1)
    frame_index = 0
    frames_with_motion = 0
    total_detections = 0
    total_raw_detections = 0
    total_roi_rejected = 0
    total_roi_penalized = 0
    total_semantic_detections = 0
    total_semantic_rejected = 0
    total_semantic_penalized = 0
    total_temporal_rejected = 0
    total_unconfirmed_rejected = 0
    total_direction_rejected = 0
    total_drone_track_rejected = 0
    total_screen_decoy_rejected = 0
    total_occlusion_recovered = 0
    rejected_frame_count = 0
    global_motion_detected_count = 0
    shake_estimated_count = 0
    shake_reused_count = 0
    previous_raw_detection_count = 0
    stopped_early = False
    progress_interval_s = max(0.1, float(args.progress_interval))
    processing_started_at = time.time()
    last_progress_at = processing_started_at
    last_progress_frame = 0

    def make_progress(
        stage: str,
        message: str,
        recent_frames: int | None = None,
        recent_elapsed_s: float | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        semantic_inference_count = semantic_detector.inference_count if semantic_detector else 0
        semantic_skipped_count = semantic_detector.skipped_count if semantic_detector else 0
        return progress_payload(
            stage,
            processing_started_at,
            frame_index,
            target_frame_count,
            recent_frames=recent_frames,
            recent_elapsed_s=recent_elapsed_s,
            message=message,
            frames_with_motion=frames_with_motion,
            raw_detection_count=total_raw_detections,
            kept_detection_count=total_detections,
            roi_rejected_count=total_roi_rejected,
            semantic_rejected_count=total_semantic_rejected,
            temporal_rejected_count=total_temporal_rejected,
            drone_track_rejected_count=total_drone_track_rejected,
            screen_decoy_rejected_count=total_screen_decoy_rejected,
            occlusion_recovered_count=total_occlusion_recovered,
            shake_estimated_count=shake_estimated_count,
            shake_reused_count=shake_reused_count,
            semantic_inference_count=semantic_inference_count,
            semantic_skipped_count=semantic_skipped_count,
            backend=backend_info.to_json_dict(),
            stopped_early=stopped_early,
            **extra,
        )

    emit_progress(
        args,
        "processing",
        **make_progress("processing", "Processing video frames."),
    )

    try:
        while True:
            if max_frames > 0 and frame_index >= max_frames:
                stopped_early = total_video_frames == 0 or start_frame + frame_index < total_video_frames
                break
            if stop_requested(args):
                stopped_early = True
                break

            ok, frame = capture.read()
            if not ok:
                break

            source_frame_index = start_frame + frame_index
            timestamp_s = source_frame_index / max(fps, 0.001)
            current_gray = prepare_gray(frame, config, cuda_processor)

            should_run_semantic = semantic_detector is not None
            if semantic_detector is not None and semantic_config.motion_gate:
                should_run_semantic = previous_gray is not None and previous_raw_detection_count > 0
            if should_run_semantic and semantic_detector is not None:
                semantic_detections = (
                    semantic_detector.detect(frame, source_frame_index)
                )
            else:
                semantic_detections = []
                if semantic_detector is not None:
                    semantic_detector.skipped_count += 1

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
                shake_estimated = False
                shake_reused = False
                raw_detection_count = 0
                roi_rejected_count = 0
                roi_penalized_count = 0
                semantic_detection_count = len(semantic_detections)
                semantic_rejected_count = 0
                semantic_penalized_count = 0
                temporal_rejected_count = 0
                unconfirmed_rejected_count = 0
                direction_rejected_count = 0
                drone_track_rejected_count = 0
                screen_decoy_rejected_count = 0
                occlusion_recovered_count = 0
            else:
                analysis = analyze_gray_pair(
                    previous_gray,
                    current_gray,
                    config,
                    image_width=width,
                    image_height=height,
                    roi_mask=roi_mask,
                    semantic_detections=semantic_detections,
                    semantic_config=semantic_config,
                    motion_tracker=motion_tracker,
                    shake_estimator=shake_estimator,
                    frame_index=source_frame_index,
                    build_mask=write_motion_video,
                    morph_kernel_matrix=morph_kernel_matrix,
                    cuda_processor=cuda_processor,
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
                shake_estimated = analysis.shake_estimated
                shake_reused = analysis.shake_reused
                raw_detection_count = analysis.raw_detection_count
                roi_rejected_count = analysis.roi_rejected_count
                roi_penalized_count = analysis.roi_penalized_count
                semantic_detections = analysis.semantic_detections
                semantic_detection_count = analysis.semantic_detection_count
                semantic_rejected_count = analysis.semantic_rejected_count
                semantic_penalized_count = analysis.semantic_penalized_count
                temporal_rejected_count = analysis.temporal_rejected_count
                unconfirmed_rejected_count = analysis.unconfirmed_rejected_count
                direction_rejected_count = analysis.direction_rejected_count
                drone_track_rejected_count = analysis.drone_track_rejected_count
                screen_decoy_rejected_count = analysis.screen_decoy_rejected_count
                occlusion_recovered_count = analysis.occlusion_recovered_count

            if global_motion_rejected:
                rejected_frame_count += 1
                motion_tracker.reset()
            if write_motion_video:
                if global_motion_rejected:
                    trail_masks = [np.zeros_like(accepted_mask)]
                else:
                    trail_masks.append(accepted_mask)
                    trail_masks = trail_masks[-max_trail_masks:]
                trail_mask = combine_trail_masks(trail_masks)
                assert motion_writer is not None
                motion_writer.write(render_motion_only(frame, trail_mask))
            if write_overlay_video:
                assert overlay_writer is not None
                overlay_writer.write(
                    render_overlay(
                        frame_bgr=frame,
                        detections=detections,
                        semantic_detections=semantic_detections,
                        global_motion_rejected=global_motion_rejected,
                        global_motion_detected=global_motion_detected,
                        global_dx=global_dx,
                        global_dy=global_dy,
                        global_consensus=global_consensus,
                        roi_active=roi_mask is not None,
                        roi_rejected_count=roi_rejected_count,
                        roi_penalized_count=roi_penalized_count,
                        motion_filters_active=motion_tracker.active,
                        temporal_rejected_count=temporal_rejected_count,
                        unconfirmed_rejected_count=unconfirmed_rejected_count,
                        direction_rejected_count=direction_rejected_count,
                        drone_track_rejected_count=drone_track_rejected_count,
                        screen_decoy_rejected_count=screen_decoy_rejected_count,
                        occlusion_recovered_count=occlusion_recovered_count,
                        semantic_active=semantic_detector is not None,
                        semantic_rejected_count=semantic_rejected_count,
                        semantic_penalized_count=semantic_penalized_count,
                    )
                )

            total_raw_detections += raw_detection_count
            total_roi_rejected += roi_rejected_count
            total_roi_penalized += roi_penalized_count
            total_semantic_detections += semantic_detection_count
            total_semantic_rejected += semantic_rejected_count
            total_semantic_penalized += semantic_penalized_count
            total_temporal_rejected += temporal_rejected_count
            total_unconfirmed_rejected += unconfirmed_rejected_count
            total_direction_rejected += direction_rejected_count
            total_drone_track_rejected += drone_track_rejected_count
            total_screen_decoy_rejected += screen_decoy_rejected_count
            total_occlusion_recovered += occlusion_recovered_count
            if shake_estimated:
                shake_estimated_count += 1
            if shake_reused:
                shake_reused_count += 1
            if detections:
                frames_with_motion += 1
                total_detections += len(detections)
            if global_motion_detected:
                global_motion_detected_count += 1

            if jsonl is not None:
                record = MotionFrameResult(
                    source=str(source),
                    frame_index=source_frame_index,
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
                    semantic_detection_count=semantic_detection_count,
                    semantic_rejected_count=semantic_rejected_count,
                    semantic_penalized_count=semantic_penalized_count,
                    temporal_rejected_count=temporal_rejected_count,
                    unconfirmed_rejected_count=unconfirmed_rejected_count,
                    direction_rejected_count=direction_rejected_count,
                    drone_track_rejected_count=drone_track_rejected_count,
                    screen_decoy_rejected_count=screen_decoy_rejected_count,
                    occlusion_recovered_count=occlusion_recovered_count,
                    semantic_detections=semantic_detections,
                    detections=detections,
                )
                jsonl.write(json.dumps(record.to_json_dict(), separators=(",", ":")) + "\n")

            previous_raw_detection_count = raw_detection_count
            previous_gray = current_gray
            frame_index += 1
            now = time.time()
            should_emit_progress = (
                args.progress_json
                and (
                    now - last_progress_at >= progress_interval_s
                    or (
                        target_frame_count > 0
                        and frame_index >= target_frame_count
                    )
                )
            )
            if should_emit_progress:
                emit_progress(
                    args,
                    "processing",
                    **make_progress(
                        "processing",
                        "Processing video frames.",
                        recent_frames=frame_index - last_progress_frame,
                        recent_elapsed_s=now - last_progress_at,
                    ),
                )
                last_progress_at = now
                last_progress_frame = frame_index

        now = time.time()
        emit_progress(
            args,
            "finalizing",
            **make_progress(
                "finalizing",
                "Finalizing output videos and summaries.",
                recent_frames=frame_index - last_progress_frame,
                recent_elapsed_s=now - last_progress_at,
            ),
        )
    finally:
        if jsonl is not None:
            jsonl.close()
        capture.release()
        if motion_writer is not None:
            motion_writer.release()
        if overlay_writer is not None:
            overlay_writer.release()

    if frame_index == 0 and not stopped_early:
        raise RuntimeError(f"No frames were read from video: {source}")

    processing_seconds = time.time() - processing_started_at
    processing_seconds_per_frame = processing_seconds / max(1, frame_index)
    summary = {
        "mode": "video",
        "source": str(source),
        "image_width": width,
        "image_height": height,
        "fps": fps,
        "frame_count": frame_index,
        "source_total_frames": total_video_frames,
        "target_frame_count": target_frame_count,
        "start_frame": start_frame,
        "end_frame": start_frame + frame_index - 1 if frame_index > 0 else None,
        "requested_max_frames": max_frames or None,
        "stopped_early": stopped_early,
        "stop_reason": "stop_requested" if stopped_early and stop_requested(args) else ("frame_limit" if stopped_early else "completed"),
        "duration_s": frame_index / max(fps, 0.001),
        "frames_with_motion": frames_with_motion,
        "detection_count": total_detections,
        "raw_detection_count": total_raw_detections,
        "roi_rejected_count": total_roi_rejected,
        "roi_penalized_count": total_roi_penalized,
        "semantic_detection_count": total_semantic_detections,
        "semantic_rejected_count": total_semantic_rejected,
        "semantic_penalized_count": total_semantic_penalized,
        "temporal_rejected_count": total_temporal_rejected,
        "unconfirmed_rejected_count": total_unconfirmed_rejected,
        "direction_rejected_count": total_direction_rejected,
        "drone_track_rejected_count": total_drone_track_rejected,
        "screen_decoy_rejected_count": total_screen_decoy_rejected,
        "occlusion_recovered_count": total_occlusion_recovered,
        "kept_detection_count": total_detections,
        "global_motion_rejected_frames": rejected_frame_count,
        "global_motion_detected_frames": global_motion_detected_count,
        "shake_estimated_frames": shake_estimated_count,
        "shake_reused_frames": shake_reused_count,
        "processing_seconds": round(processing_seconds, 3),
        "processing_seconds_per_frame": round(processing_seconds_per_frame, 6),
        "processing_ms_per_frame": round(processing_seconds_per_frame * 1000.0, 3),
        "config": serialize_config(config),
        "backend": backend_info.to_json_dict(),
        "roi_mask": {
            "enabled": roi_mask is not None,
            "path": str(args.roi_mask) if args.roi_mask else None,
            "mode": roi_mask.mode if roi_mask else None,
            "zone_count": len(roi_mask.zones) if roi_mask else 0,
        },
        "semantic_filter": {
            **serialize_semantic_config(semantic_config),
            "weights_path": str(semantic_detector.weights_path) if semantic_detector else None,
            "inference_count": semantic_detector.inference_count if semantic_detector else 0,
            "skipped_count": semantic_detector.skipped_count if semantic_detector else 0,
        },
        "outputs": {
            "motion_video": write_motion_video,
            "overlay_video": write_overlay_video,
            "jsonl": write_jsonl,
        },
        "motion_only_path": str(motion_only_path) if write_motion_video else None,
        "overlay_path": str(overlay_path) if write_overlay_video else None,
        "jsonl_path": str(jsonl_path) if write_jsonl else None,
        "summary_path": str(summary_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    final_stage = "stopped" if stopped_early else "complete"
    emit_progress(
        args,
        final_stage,
        **make_progress(
            final_stage,
            "Processing stopped early." if stopped_early else "Processing complete.",
            overlay_path=str(overlay_path) if write_overlay_video else None,
            summary_path=str(summary_path),
        ),
    )
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
        "--backend",
        choices=sorted(PROCESSING_BACKENDS),
        default="auto" if not duplicate else default,
        help=(
            "Unified processing backend. auto selects CUDA for OpenCV motion when available, "
            "CUDA/MPS for semantic AI when available, and CPU fallbacks otherwise."
        ),
    )
    parser.add_argument("--cuda-device", type=int, default=0 if not duplicate else default)
    parser.add_argument(
        "--disable-shake-protection",
        action="store_true",
        default=False if not duplicate else default,
    )
    parser.add_argument("--shake-min-shift", type=float, default=1.5 if not duplicate else default)
    parser.add_argument("--shake-consensus", type=float, default=0.72 if not duplicate else default)
    parser.add_argument("--shake-consensus-px", type=float, default=2.0 if not duplicate else default)
    parser.add_argument("--shake-frame-stride", type=int, default=1 if not duplicate else default)
    parser.add_argument("--shake-analysis-scale", type=float, default=1.0 if not duplicate else default)
    parser.add_argument("--shake-max-corners", type=int, default=240 if not duplicate else default)
    parser.add_argument(
        "--enable-hysteresis",
        action="store_true",
        default=False if not duplicate else default,
        help="Require low-threshold regions to contain a high-threshold motion seed.",
    )
    parser.add_argument(
        "--hysteresis-high-threshold",
        type=int,
        default=36 if not duplicate else default,
    )
    parser.add_argument(
        "--enable-temporal-filter",
        action="store_true",
        default=False if not duplicate else default,
        help="Reject detections that do not persist across a short frame window.",
    )
    parser.add_argument("--temporal-window-frames", type=int, default=3 if not duplicate else default)
    parser.add_argument("--temporal-min-hits", type=int, default=2 if not duplicate else default)
    parser.add_argument(
        "--enable-track-confirmation",
        action="store_true",
        default=False if not duplicate else default,
        help="Hide tentative tracks until they collect enough hits.",
    )
    parser.add_argument("--track-confirm-hits", type=int, default=2 if not duplicate else default)
    parser.add_argument("--track-max-missed", type=int, default=2 if not duplicate else default)
    parser.add_argument("--track-match-distance", type=float, default=80.0 if not duplicate else default)
    parser.add_argument(
        "--enable-direction-consistency",
        action="store_true",
        default=False if not duplicate else default,
        help="Reject confirmed tracks whose velocity changes direction like jitter.",
    )
    parser.add_argument("--direction-min-hits", type=int, default=3 if not duplicate else default)
    parser.add_argument(
        "--direction-min-displacement",
        type=float,
        default=2.0 if not duplicate else default,
    )
    parser.add_argument("--direction-cosine", type=float, default=0.20 if not duplicate else default)
    parser.add_argument(
        "--enable-drone-track-filter",
        action="store_true",
        default=False if not duplicate else default,
        help="Reject tracks that are too young or outside normalized drone-speed bounds.",
    )
    parser.add_argument("--drone-min-track-hits", type=int, default=3 if not duplicate else default)
    parser.add_argument(
        "--drone-min-normalized-speed",
        type=float,
        default=0.10 if not duplicate else default,
        help="Minimum center speed divided by sqrt(box area) for drone-like motion.",
    )
    parser.add_argument(
        "--drone-max-normalized-speed",
        type=float,
        default=30.0 if not duplicate else default,
        help="Maximum center speed divided by sqrt(box area) for drone-like motion.",
    )
    parser.add_argument(
        "--enable-screen-decoy-rejection",
        action="store_true",
        default=False if not duplicate else default,
        help="Reject long-lived tracks with stable size/aspect and overly smooth paths.",
    )
    parser.add_argument("--screen-min-track-hits", type=int, default=8 if not duplicate else default)
    parser.add_argument("--screen-max-area-cv", type=float, default=0.08 if not duplicate else default)
    parser.add_argument("--screen-max-aspect-cv", type=float, default=0.10 if not duplicate else default)
    parser.add_argument(
        "--screen-min-path-smoothness",
        type=float,
        default=0.90 if not duplicate else default,
    )
    parser.add_argument(
        "--screen-min-perimeter-fraction",
        type=float,
        default=0.0 if not duplicate else default,
        help="Optional fraction of recent centers that must be near the frame perimeter.",
    )
    parser.add_argument("--screen-perimeter-margin", type=float, default=0.10 if not duplicate else default)
    parser.add_argument(
        "--enable-occlusion-recovery",
        action="store_true",
        default=False if not duplicate else default,
        help="Keep tracks alive longer and match reappearing detections near predicted centers.",
    )
    parser.add_argument("--occlusion-max-frames", type=int, default=8 if not duplicate else default)
    parser.add_argument("--occlusion-gate-distance", type=float, default=140.0 if not duplicate else default)
    parser.add_argument("--roi-mask", default=default, help="Path to normalized ROI mask JSON.")
    parser.add_argument(
        "--enable-semantic-filter",
        action="store_true",
        default=False if not duplicate else default,
        help="Run a semantic detector and reject/penalize overlapping motion boxes.",
    )
    parser.add_argument(
        "--semantic-labels",
        default="person" if not duplicate else default,
        help="Comma-separated semantic labels to draw/filter, e.g. person,bird.",
    )
    parser.add_argument(
        "--semantic-action",
        choices=sorted(SEMANTIC_ACTIONS),
        default="reject" if not duplicate else default,
        help="How to handle motion boxes overlapping semantic labels.",
    )
    parser.add_argument(
        "--semantic-model-repo",
        default=DEFAULT_SEMANTIC_MODEL_REPO if not duplicate else default,
    )
    parser.add_argument(
        "--semantic-model-file",
        default=DEFAULT_SEMANTIC_MODEL_FILE if not duplicate else default,
    )
    parser.add_argument("--semantic-weights", default=default, help="Optional local YOLO weights path.")
    parser.add_argument("--semantic-conf", type=float, default=0.05 if not duplicate else default)
    parser.add_argument("--semantic-iou", type=float, default=0.50 if not duplicate else default)
    parser.add_argument("--semantic-imgsz", type=int, default=960 if not duplicate else default)
    parser.add_argument(
        "--semantic-device",
        default=default,
        help="Advanced semantic-device override. Usually inherit from --backend. Examples: cuda, mps, cpu.",
    )
    parser.add_argument("--semantic-frame-stride", type=int, default=2 if not duplicate else default)
    parser.add_argument(
        "--semantic-warmup",
        action="store_true",
        default=False if not duplicate else default,
        help="Run one untimed warmup inference before processing video frames.",
    )
    parser.add_argument(
        "--semantic-motion-gate",
        action="store_true",
        default=False if not duplicate else default,
        help="Skip semantic inference while the previous frame had no raw motion candidates.",
    )
    parser.add_argument(
        "--semantic-overlap-threshold",
        type=float,
        default=0.15 if not duplicate else default,
        help="Reject when semantic-box intersection covers this fraction of the motion box.",
    )
    parser.add_argument("--out-dir", default=default)
    parser.add_argument(
        "--no-motion-video",
        action="store_true",
        default=False if not duplicate else default,
        help="Skip writing the motion-only debug MP4.",
    )
    parser.add_argument(
        "--no-overlay-video",
        action="store_true",
        default=False if not duplicate else default,
        help="Skip writing the annotated overlay MP4.",
    )
    parser.add_argument(
        "--no-jsonl",
        action="store_true",
        default=False if not duplicate else default,
        help="Skip writing per-frame detections JSONL.",
    )
    parser.add_argument("--start-frame", type=int, default=0 if not duplicate else default)
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0 if not duplicate else default,
        help="Maximum frames to process. 0 means process to the end.",
    )
    parser.add_argument(
        "--stop-file",
        default=default,
        help="If this file appears during processing, stop gracefully and finalize partial outputs.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if not duplicate else default,
        help="Print machine-readable run summary.",
    )
    parser.add_argument(
        "--progress-json",
        action="store_true",
        default=False if not duplicate else default,
        help="Emit machine-readable progress events to stderr.",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=1.0 if not duplicate else default,
        help="Minimum seconds between progress events.",
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
