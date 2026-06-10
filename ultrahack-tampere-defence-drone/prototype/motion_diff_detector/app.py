"""Streamlit interface for fixed-camera motion differencing."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
import streamlit as st

try:
    from streamlit_image_coordinates import streamlit_image_coordinates
except ImportError:  # pragma: no cover - runtime dependency hint for Streamlit.
    streamlit_image_coordinates = None


SCRIPT_PATH = Path(__file__).with_name("motion_diff_detector.py")
SAMPLE_PATH = Path.home() / "Downloads" / "fixedcameravideo_2026-06-10_00-10-22.mp4"
UPLOAD_CACHE_ROOT = Path(tempfile.gettempdir()) / "motion_diff_detector_uploads"
RUN_CACHE_ROOT = UPLOAD_CACHE_ROOT / "runs"
PROGRESS_PREFIX = "PROGRESS "
RUN_MANIFEST_NAME = "resume_manifest.json"
COMBINED_SUMMARY_NAME = "combined_summary.json"


st.set_page_config(page_title="Motion Diff Drone Detector", layout="wide")
st.title("Motion Diff Drone Detector")


def cuda_device_count() -> int:
    try:
        if not hasattr(cv2, "cuda"):
            return 0
        return int(cv2.cuda.getCudaEnabledDeviceCount())
    except Exception:
        return 0


PROFILE_TYPE = "motion_diff_detector_parameters"
PROFILE_VERSION = 1
SETTING_DEFAULTS: dict[str, Any] = {
    "diff_threshold": 18,
    "min_area": 1000.0,
    "blur_kernel": 5,
    "morph_kernel": 3,
    "trail_frames": 3,
    "max_motion_ratio": 0.10,
    "analysis_scale": 0.50,
    "overlay_merge_distance": 0.0,
    "overlay_hold_frames": 12,
    "overlay_hold_expand_px": 10.0,
    "processing_backend": "auto",
    "cuda_device": 0,
    "write_overlay_video": True,
    "write_motion_video": True,
    "write_jsonl": True,
    "limit_frame_range": False,
    "start_frame": 0,
    "max_frames": 300,
    "shake_protection": True,
    "shake_min_shift": 1.5,
    "shake_consensus": 0.72,
    "shake_consensus_px": 2.0,
    "shake_frame_stride": 1,
    "shake_analysis_scale": 1.0,
    "shake_max_corners": 240,
    "hysteresis": False,
    "hysteresis_high_threshold": 36,
    "temporal_filter": False,
    "track_confirmation": False,
    "direction_consistency": False,
    "temporal_window_frames": 3,
    "temporal_min_hits": 2,
    "track_confirm_hits": 2,
    "track_max_missed": 2,
    "track_match_distance": 80.0,
    "direction_min_hits": 3,
    "direction_min_displacement": 2.0,
    "direction_cosine": 0.20,
    "drone_track_filter": False,
    "drone_min_track_hits": 3,
    "drone_min_normalized_speed": 0.10,
    "drone_max_normalized_speed": 30.0,
    "screen_decoy_rejection": False,
    "screen_min_track_hits": 8,
    "screen_max_area_cv": 0.08,
    "screen_max_aspect_cv": 0.10,
    "screen_min_path_smoothness": 0.90,
    "screen_min_perimeter_fraction": 0.0,
    "screen_perimeter_margin": 0.10,
    "occlusion_recovery": False,
    "occlusion_max_frames": 8,
    "occlusion_gate_distance": 140.0,
    "roi_mask_enabled": False,
    "semantic_filter": False,
    "semantic_action": "reject",
    "semantic_conf": 0.05,
    "semantic_overlap_threshold": 0.15,
    "semantic_frame_stride": 2,
    "semantic_warmup": True,
    "semantic_motion_gate": False,
    "semantic_imgsz": 960,
    "semantic_model_repo": "devanshty/WingID",
    "semantic_model_file": "yolo11l.pt",
    "semantic_weights": "",
}
SETTING_OPTIONS: dict[str, list[Any]] = {
    "blur_kernel": [1, 3, 5, 7, 9, 11],
    "morph_kernel": [1, 3, 5, 7, 9],
    "processing_backend": ["auto", "cpu", "cuda", "mps"],
    "semantic_action": ["reject", "penalize"],
    "semantic_imgsz": [640, 960, 1280],
}
SETTING_RANGES: dict[str, tuple[float, float]] = {
    "diff_threshold": (1, 100),
    "min_area": (1.0, 100000.0),
    "trail_frames": (0, 30),
    "max_motion_ratio": (0.01, 1.0),
    "analysis_scale": (0.10, 1.0),
    "overlay_merge_distance": (0.0, 500.0),
    "overlay_hold_frames": (0, 120),
    "overlay_hold_expand_px": (0.0, 200.0),
    "cuda_device": (0, 64),
    "start_frame": (0, 1000000000),
    "max_frames": (1, 1000000000),
    "shake_min_shift": (0.0, 20.0),
    "shake_consensus": (0.10, 1.0),
    "shake_consensus_px": (0.5, 10.0),
    "shake_frame_stride": (1, 10),
    "shake_analysis_scale": (0.10, 1.0),
    "shake_max_corners": (12, 300),
    "hysteresis_high_threshold": (1, 255),
    "temporal_window_frames": (1, 10),
    "temporal_min_hits": (1, 10),
    "track_confirm_hits": (1, 10),
    "track_max_missed": (0, 10),
    "track_match_distance": (5.0, 300.0),
    "direction_min_hits": (2, 10),
    "direction_min_displacement": (0.0, 30.0),
    "direction_cosine": (-1.0, 1.0),
    "drone_min_track_hits": (1, 20),
    "drone_min_normalized_speed": (0.0, 5.0),
    "drone_max_normalized_speed": (0.1, 60.0),
    "screen_min_track_hits": (2, 60),
    "screen_max_area_cv": (0.0, 0.50),
    "screen_max_aspect_cv": (0.0, 0.50),
    "screen_min_path_smoothness": (0.0, 1.0),
    "screen_min_perimeter_fraction": (0.0, 1.0),
    "screen_perimeter_margin": (0.0, 0.50),
    "occlusion_max_frames": (0, 60),
    "occlusion_gate_distance": (5.0, 500.0),
    "semantic_conf": (0.01, 0.90),
    "semantic_overlap_threshold": (0.01, 1.0),
    "semantic_frame_stride": (1, 20),
}


def clamp01(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def snap_normalized_point(
    x: float,
    y: float,
    enabled: bool,
    threshold: float,
) -> tuple[float, float, str | None]:
    x = clamp01(x)
    y = clamp01(y)
    if not enabled:
        return x, y, None

    threshold = clamp01(threshold)
    horizontal: str | None = None
    vertical: str | None = None

    if x <= threshold:
        x = 0.0
        horizontal = "left"
    elif x >= 1.0 - threshold:
        x = 1.0
        horizontal = "right"

    if y <= threshold:
        y = 0.0
        vertical = "top"
    elif y >= 1.0 - threshold:
        y = 1.0
        vertical = "bottom"

    if horizontal and vertical:
        return x, y, f"{vertical}-{horizontal} corner"
    if horizontal:
        return x, y, f"{horizontal} edge"
    if vertical:
        return x, y, f"{vertical} edge"
    return x, y, None


def edge_band_points(edge: str, size: float) -> list[list[float]]:
    size = clamp01(size)
    if edge == "bottom":
        return [[0.0, 1.0 - size], [1.0, 1.0 - size], [1.0, 1.0], [0.0, 1.0]]
    if edge == "left":
        return [[0.0, 0.0], [size, 0.0], [size, 1.0], [0.0, 1.0]]
    if edge == "right":
        return [[1.0 - size, 0.0], [1.0, 0.0], [1.0, 1.0], [1.0 - size, 1.0]]
    return [[0.0, 0.0], [1.0, 0.0], [1.0, size], [0.0, size]]


def normalize_zone(zone: dict) -> dict:
    zone_type = str(zone.get("type", "ignore")).lower()
    if zone_type not in {"ignore", "penalty", "flight"}:
        zone_type = "ignore"
    points = [
        [clamp01(float(point[0])), clamp01(float(point[1]))]
        for point in zone.get("points", [])
        if isinstance(point, (list, tuple)) and len(point) == 2
    ]
    return {
        "name": str(zone.get("name") or f"{zone_type}_{len(st.session_state['roi_zones']) + 1}"),
        "type": zone_type,
        "points": points,
        "penalty": clamp01(float(zone.get("penalty", 0.5 if zone_type == "penalty" else 0.0))),
    }


def current_roi_mask_payload() -> dict:
    return {
        "version": 1,
        "mode": st.session_state.get("roi_mode", "fixed"),
        "zones": st.session_state.get("roi_zones", []),
    }


def load_roi_mask_payload(payload: dict) -> None:
    mode = str(payload.get("mode", "fixed")).lower()
    if mode not in {"fixed", "handheld"}:
        raise ValueError("ROI mask mode must be fixed or handheld.")
    zones = [normalize_zone(zone) for zone in payload.get("zones", [])]
    zones = [zone for zone in zones if len(zone["points"]) >= 3]
    st.session_state["roi_mode"] = mode
    st.session_state["roi_zones"] = zones
    st.session_state["roi_points"] = []
    st.session_state["roi_last_click"] = None
    st.session_state["roi_last_snap"] = ""


def coerce_setting(name: str, value: Any) -> Any:
    default = SETTING_DEFAULTS[name]
    if isinstance(default, bool):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", ""}:
                return False
            return default
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            coerced = int(default)
    elif isinstance(default, float):
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            coerced = float(default)
    else:
        coerced = str(value) if value is not None else str(default)

    if name in SETTING_OPTIONS:
        return coerced if coerced in SETTING_OPTIONS[name] else default
    if name in SETTING_RANGES and isinstance(coerced, (int, float)):
        min_value, max_value = SETTING_RANGES[name]
        coerced = min(max(coerced, min_value), max_value)
    return coerced


def initialize_settings_profile() -> None:
    if "settings_profile_values" not in st.session_state:
        st.session_state["settings_profile_values"] = dict(SETTING_DEFAULTS)
    if "settings_profile_revision" not in st.session_state:
        st.session_state["settings_profile_revision"] = 0


def setting_value(name: str) -> Any:
    values = st.session_state.get("settings_profile_values", {})
    return coerce_setting(name, values.get(name, SETTING_DEFAULTS[name]))


def setting_key(name: str) -> str:
    revision = int(st.session_state.get("settings_profile_revision", 0))
    return f"settings_{revision}_{name}"


def select_index(name: str, options: list[Any]) -> int:
    value = setting_value(name)
    return options.index(value) if value in options else options.index(SETTING_DEFAULTS[name])


def apply_settings_profile(payload: dict) -> tuple[int, list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("Parameter profile must be a JSON object.")
    settings = payload.get("settings", payload)
    if not isinstance(settings, dict):
        raise ValueError("Parameter profile settings must be a JSON object.")

    current_values = dict(st.session_state.get("settings_profile_values", SETTING_DEFAULTS))
    applied = 0
    unknown: list[str] = []
    for name, value in settings.items():
        if name not in SETTING_DEFAULTS:
            unknown.append(str(name))
            continue
        current_values[name] = coerce_setting(name, value)
        applied += 1
    st.session_state["settings_profile_values"] = current_values

    roi_payload = payload.get("roi_mask")
    if isinstance(roi_payload, dict):
        load_roi_mask_payload(roi_payload)

    st.session_state["settings_profile_revision"] = int(
        st.session_state.get("settings_profile_revision", 0)
    ) + 1
    return applied, unknown


def settings_profile_payload(settings: dict[str, Any]) -> dict:
    return {
        "version": PROFILE_VERSION,
        "profile_type": PROFILE_TYPE,
        "settings": settings,
        "roi_mask": current_roi_mask_payload(),
    }


if "roi_zones" not in st.session_state:
    st.session_state["roi_zones"] = []
if "roi_points" not in st.session_state:
    st.session_state["roi_points"] = []
if "roi_mode" not in st.session_state:
    st.session_state["roi_mode"] = "fixed"
if "roi_snap_enabled" not in st.session_state:
    st.session_state["roi_snap_enabled"] = True
if "roi_snap_threshold" not in st.session_state:
    st.session_state["roi_snap_threshold"] = 0.035
if "roi_edge_band_size" not in st.session_state:
    st.session_state["roi_edge_band_size"] = 0.20
if "roi_last_snap" not in st.session_state:
    st.session_state["roi_last_snap"] = ""
if "uploaded_video_cache_id" not in st.session_state:
    st.session_state["uploaded_video_cache_id"] = None
if "uploaded_video_cache_path" not in st.session_state:
    st.session_state["uploaded_video_cache_path"] = ""
if "active_stop_file" not in st.session_state:
    st.session_state["active_stop_file"] = None
if "last_summary_path" not in st.session_state:
    st.session_state["last_summary_path"] = ""
if "last_run_root" not in st.session_state:
    st.session_state["last_run_root"] = ""
if "last_resume_manifest_path" not in st.session_state:
    st.session_state["last_resume_manifest_path"] = ""
if "profile_panel_open" not in st.session_state:
    st.session_state["profile_panel_open"] = False
initialize_settings_profile()

with st.sidebar:
    profile_panel_open = bool(st.session_state.get("profile_panel_open", False))
    profile_button_label = f"{'v' if profile_panel_open else '>'} Parameter profile"
    if st.button(
        profile_button_label,
        key="profile_panel_toggle",
        use_container_width=True,
        help="Show or hide import/export controls for saved detector parameters.",
    ):
        st.session_state["profile_panel_open"] = not profile_panel_open
        st.rerun()

    profile_download_slot = None
    if st.session_state.get("profile_panel_open", False):
        profile_message = st.session_state.pop("settings_profile_message", "")
        if profile_message:
            st.success(profile_message)
        uploaded_profile = st.file_uploader(
            "Import parameters JSON",
            type=["json"],
            key="settings_profile_upload",
            help="Loads saved detector sliders, toggles, model settings, and ROI mask.",
        )
        if uploaded_profile is not None:
            upload_id = f"{uploaded_profile.name}:{uploaded_profile.size}"
            if st.session_state.get("settings_profile_upload_id") != upload_id:
                try:
                    payload = json.loads(uploaded_profile.getvalue().decode("utf-8-sig"))
                    applied, unknown = apply_settings_profile(payload)
                except Exception as exc:
                    st.error(f"Could not load parameters: {type(exc).__name__}: {exc}")
                else:
                    st.session_state["settings_profile_upload_id"] = upload_id
                    suffix = f"; ignored {len(unknown)} unknown keys" if unknown else ""
                    st.session_state["settings_profile_message"] = (
                        f"Loaded {applied} parameters{suffix}."
                    )
                    st.rerun()
        profile_download_slot = st.empty()

    st.divider()
    diff_threshold = st.slider(
        "Difference threshold",
        1,
        100,
        setting_value("diff_threshold"),
        1,
        key=setting_key("diff_threshold"),
        help="Minimum pixel brightness change needed before a pixel can become motion.",
    )
    min_area = st.number_input(
        "Minimum motion area",
        min_value=1.0,
        max_value=100000.0,
        value=setting_value("min_area"),
        key=setting_key("min_area"),
        help="Smallest connected moving region accepted as a motion box.",
    )
    blur_kernel = st.selectbox(
        "Blur kernel",
        [1, 3, 5, 7, 9, 11],
        index=select_index("blur_kernel", [1, 3, 5, 7, 9, 11]),
        key=setting_key("blur_kernel"),
        help="Pre-blur amount before differencing. Higher values smooth sensor noise but can soften tiny drones.",
    )
    morph_kernel = st.selectbox(
        "Morphology kernel",
        [1, 3, 5, 7, 9],
        index=select_index("morph_kernel", [1, 3, 5, 7, 9]),
        key=setting_key("morph_kernel"),
        help="Cleanup kernel for joining nearby motion pixels and removing isolated specks.",
    )
    trail_frames = st.slider(
        "Trail / hold frames",
        0,
        30,
        setting_value("trail_frames"),
        1,
        key=setting_key("trail_frames"),
        help="Keeps recent accepted motion visible for several frames in motion-only output.",
    )
    max_motion_ratio = st.slider(
        "Max motion ratio",
        0.01,
        1.0,
        setting_value("max_motion_ratio"),
        0.01,
        key=setting_key("max_motion_ratio"),
        help="Rejects frames where too much of the image changes at once.",
    )
    analysis_scale = st.slider(
        "Analysis scale",
        0.10,
        1.0,
        setting_value("analysis_scale"),
        0.05,
        key=setting_key("analysis_scale"),
        help="Downscale factor for motion analysis. Lower is faster; higher preserves tiny objects.",
    )
    overlay_merge_distance = st.slider(
        "Merge nearby box distance",
        0.0,
        500.0,
        setting_value("overlay_merge_distance"),
        5.0,
        key=setting_key("overlay_merge_distance"),
        help="Overlay-only cleanup. Drone boxes that overlap or are within this pixel distance are drawn as one box.",
    )
    overlay_hold_frames = st.slider(
        "Held box frames",
        0,
        120,
        setting_value("overlay_hold_frames"),
        1,
        key=setting_key("overlay_hold_frames"),
        help="Overlay-only hold. Keeps a fading drone box after motion disappears. Set 0 to disable.",
    )
    overlay_hold_expand_px = st.slider(
        "Held box expansion px",
        0.0,
        200.0,
        setting_value("overlay_hold_expand_px"),
        1.0,
        key=setting_key("overlay_hold_expand_px"),
        disabled=overlay_hold_frames == 0,
        help="Expands held/fading drone boxes by this many pixels on each side.",
    )
    st.divider()
    st.caption("Performance / outputs")
    cuda_devices = cuda_device_count()
    backend_options = ["auto", "cpu", "cuda", "mps"]
    processing_backend = st.selectbox(
        "Processing backend",
        backend_options,
        index=select_index("processing_backend", backend_options),
        key=setting_key("processing_backend"),
        help="Unified backend for motion and AI. auto chooses CUDA for motion when OpenCV supports it, CUDA/MPS for AI when PyTorch supports it, and CPU otherwise.",
    )
    cuda_device_default = int(min(setting_value("cuda_device"), max(0, cuda_devices - 1)))
    cuda_device = st.number_input(
        "CUDA device",
        min_value=0,
        max_value=max(0, cuda_devices - 1),
        value=cuda_device_default,
        step=1,
        key=setting_key("cuda_device"),
        disabled=processing_backend not in {"auto", "cuda"} or cuda_devices == 0,
        help=f"OpenCV CUDA device index for motion processing. Detected OpenCV CUDA devices: {cuda_devices}.",
    )
    write_overlay_video = st.checkbox(
        "Write overlay video",
        value=setting_value("write_overlay_video"),
        key=setting_key("write_overlay_video"),
        help="Writes the annotated MP4. Turn off for fastest settings searches.",
    )
    write_motion_video = st.checkbox(
        "Write motion-only video",
        value=setting_value("write_motion_video"),
        key=setting_key("write_motion_video"),
        help="Writes the motion-mask debug MP4. Turn off unless you need the mask view.",
    )
    write_jsonl = st.checkbox(
        "Write detections JSONL",
        value=setting_value("write_jsonl"),
        key=setting_key("write_jsonl"),
        help="Stores every per-frame detection record. Turn off to reduce I/O during tuning.",
    )
    limit_frame_range = st.checkbox(
        "Limit frame range",
        value=setting_value("limit_frame_range"),
        key=setting_key("limit_frame_range"),
        help="Process only a slice of the video for fast tuning or partial previews.",
    )
    start_frame = st.number_input(
        "Start frame",
        min_value=0,
        value=setting_value("start_frame"),
        step=1,
        key=setting_key("start_frame"),
        disabled=not limit_frame_range,
        help="First source frame to process when frame range limiting is enabled.",
    )
    max_frames = st.number_input(
        "Max frames",
        min_value=1,
        value=setting_value("max_frames"),
        step=30,
        key=setting_key("max_frames"),
        disabled=not limit_frame_range,
        help="Maximum number of frames to process when frame range limiting is enabled.",
    )
    st.divider()
    shake_protection = st.checkbox(
        "Shake protection",
        value=setting_value("shake_protection"),
        key=setting_key("shake_protection"),
        help="Compensates global camera/floor movement before motion differencing.",
    )
    shake_min_shift = st.slider(
        "Shake min shift",
        0.0,
        20.0,
        setting_value("shake_min_shift"),
        0.1,
        key=setting_key("shake_min_shift"),
        disabled=not shake_protection,
        help="Minimum estimated global image shift before shake compensation is considered active.",
    )
    shake_consensus = st.slider(
        "Shake consensus",
        0.10,
        1.0,
        setting_value("shake_consensus"),
        0.01,
        key=setting_key("shake_consensus"),
        disabled=not shake_protection,
        help="Required share of tracked points agreeing on the same global movement.",
    )
    shake_consensus_px = st.slider(
        "Shake consensus px",
        0.5,
        10.0,
        setting_value("shake_consensus_px"),
        0.1,
        key=setting_key("shake_consensus_px"),
        disabled=not shake_protection,
        help="Pixel tolerance for deciding whether tracked points agree on global movement.",
    )
    shake_frame_stride = st.slider(
        "Shake frame stride",
        1,
        10,
        setting_value("shake_frame_stride"),
        1,
        key=setting_key("shake_frame_stride"),
        disabled=not shake_protection,
        help="Estimate global shake every N frames and reuse the last estimate between runs. Higher is faster but less reactive.",
    )
    shake_analysis_scale = st.slider(
        "Shake analysis scale",
        0.10,
        1.0,
        setting_value("shake_analysis_scale"),
        0.05,
        key=setting_key("shake_analysis_scale"),
        disabled=not shake_protection,
        help="Extra downscale used only for shake optical flow. Lower is faster; 1.0 preserves current behavior.",
    )
    shake_max_corners = st.slider(
        "Shake feature points",
        12,
        300,
        setting_value("shake_max_corners"),
        12,
        key=setting_key("shake_max_corners"),
        disabled=not shake_protection,
        help="Maximum tracked feature points for shake estimation. Lower is faster but can reduce consensus quality.",
    )
    st.divider()
    hysteresis = st.checkbox(
        "Hysteresis thresholding",
        value=setting_value("hysteresis"),
        key=setting_key("hysteresis"),
        help="Keeps weak motion only when it connects to a stronger high-threshold seed.",
    )
    hysteresis_high_threshold = st.slider(
        "Hysteresis high threshold",
        1,
        255,
        setting_value("hysteresis_high_threshold"),
        1,
        key=setting_key("hysteresis_high_threshold"),
        disabled=not hysteresis,
        help="Strong-pixel seed threshold used by hysteresis.",
    )
    temporal_filter = st.checkbox(
        "Temporal persistence",
        value=setting_value("temporal_filter"),
        key=setting_key("temporal_filter"),
        help="Rejects motion that appears only once and does not persist across nearby frames.",
    )
    track_confirmation = st.checkbox(
        "Track confirmation",
        value=setting_value("track_confirmation"),
        key=setting_key("track_confirmation"),
        help="Hides a new motion track until it has been seen enough times.",
    )
    direction_consistency = st.checkbox(
        "Direction consistency",
        value=setting_value("direction_consistency"),
        key=setting_key("direction_consistency"),
        help="Rejects tracks that jitter back and forth instead of moving consistently.",
    )
    drone_track_filter = st.checkbox(
        "Track-level drone gate",
        value=setting_value("drone_track_filter"),
        key=setting_key("drone_track_filter"),
        help="Rejects tracks until they are old enough and moving in a configured drone-like speed range.",
    )
    screen_decoy_rejection = st.checkbox(
        "Screen overlay rejection",
        value=setting_value("screen_decoy_rejection"),
        key=setting_key("screen_decoy_rejection"),
        help="Rejects long-lived tracks with nearly constant size/aspect and very smooth paths, which is typical for PNG/video overlays.",
    )
    occlusion_recovery = st.checkbox(
        "Occlusion recovery",
        value=setting_value("occlusion_recovery"),
        key=setting_key("occlusion_recovery"),
        help="Keeps a track alive through short disappearances and rematches near the predicted center.",
    )
    track_tuning_enabled = (
        temporal_filter
        or track_confirmation
        or direction_consistency
        or drone_track_filter
        or screen_decoy_rejection
        or occlusion_recovery
    )
    temporal_window_frames = st.slider(
        "Persistence window",
        1,
        10,
        setting_value("temporal_window_frames"),
        1,
        key=setting_key("temporal_window_frames"),
        disabled=not temporal_filter,
        help="Number of recent frames checked by temporal persistence.",
    )
    temporal_min_hits = st.slider(
        "Persistence min hits",
        1,
        10,
        setting_value("temporal_min_hits"),
        1,
        key=setting_key("temporal_min_hits"),
        disabled=not temporal_filter,
        help="Minimum detections needed inside the persistence window.",
    )
    track_confirm_hits = st.slider(
        "Track confirm hits",
        1,
        10,
        setting_value("track_confirm_hits"),
        1,
        key=setting_key("track_confirm_hits"),
        disabled=not track_confirmation,
        help="Number of matched detections needed before a track is drawn.",
    )
    track_max_missed = st.slider(
        "Track max missed",
        0,
        10,
        setting_value("track_max_missed"),
        1,
        key=setting_key("track_max_missed"),
        disabled=not track_tuning_enabled,
        help="How many missed frames a track can survive before being deleted.",
    )
    track_match_distance = st.slider(
        "Track match distance",
        5.0,
        300.0,
        setting_value("track_match_distance"),
        5.0,
        key=setting_key("track_match_distance"),
        disabled=not track_tuning_enabled,
        help="Maximum pixel distance for matching a detection to an existing track.",
    )
    direction_min_hits = st.slider(
        "Direction min hits",
        2,
        10,
        setting_value("direction_min_hits"),
        1,
        key=setting_key("direction_min_hits"),
        disabled=not direction_consistency,
        help="Minimum track hits before direction consistency can reject jitter.",
    )
    direction_min_displacement = st.slider(
        "Direction min displacement",
        0.0,
        30.0,
        setting_value("direction_min_displacement"),
        0.5,
        key=setting_key("direction_min_displacement"),
        disabled=not direction_consistency,
        help="Small movements below this distance are ignored for direction checks.",
    )
    direction_cosine = st.slider(
        "Direction cosine",
        -1.0,
        1.0,
        setting_value("direction_cosine"),
        0.05,
        key=setting_key("direction_cosine"),
        disabled=not direction_consistency,
        help="Allowed direction similarity. Lower is more tolerant; higher rejects more jitter.",
    )
    drone_min_track_hits = st.slider(
        "Drone gate min hits",
        1,
        20,
        setting_value("drone_min_track_hits"),
        1,
        key=setting_key("drone_min_track_hits"),
        disabled=not drone_track_filter,
        help="Minimum matched detections before a track can be considered a drone candidate.",
    )
    drone_min_normalized_speed = st.slider(
        "Drone min normalized speed",
        0.0,
        5.0,
        setting_value("drone_min_normalized_speed"),
        0.05,
        key=setting_key("drone_min_normalized_speed"),
        disabled=not drone_track_filter,
        help="Minimum center speed divided by sqrt(box area). Raises the bar against static or drifting noise.",
    )
    drone_max_normalized_speed = st.slider(
        "Drone max normalized speed",
        0.1,
        60.0,
        setting_value("drone_max_normalized_speed"),
        0.5,
        key=setting_key("drone_max_normalized_speed"),
        disabled=not drone_track_filter,
        help="Maximum center speed divided by sqrt(box area). Helps reject impossible jumps and bad track matches.",
    )
    screen_min_track_hits = st.slider(
        "Screen min track hits",
        2,
        60,
        setting_value("screen_min_track_hits"),
        1,
        key=setting_key("screen_min_track_hits"),
        disabled=not screen_decoy_rejection,
        help="Number of observations required before screen-overlay rejection can remove a track.",
    )
    screen_max_area_cv = st.slider(
        "Screen max area CV",
        0.0,
        0.50,
        setting_value("screen_max_area_cv"),
        0.01,
        key=setting_key("screen_max_area_cv"),
        disabled=not screen_decoy_rejection,
        help="Maximum allowed relative box-area variation for a screen-like constant-size track.",
    )
    screen_max_aspect_cv = st.slider(
        "Screen max aspect CV",
        0.0,
        0.50,
        setting_value("screen_max_aspect_cv"),
        0.01,
        key=setting_key("screen_max_aspect_cv"),
        disabled=not screen_decoy_rejection,
        help="Maximum allowed relative aspect-ratio variation for a screen-like constant-shape track.",
    )
    screen_min_path_smoothness = st.slider(
        "Screen min smoothness",
        0.0,
        1.0,
        setting_value("screen_min_path_smoothness"),
        0.01,
        key=setting_key("screen_min_path_smoothness"),
        disabled=not screen_decoy_rejection,
        help="Straight/smooth path score required for screen-overlay rejection. 1.0 is nearly a perfect straight path.",
    )
    screen_min_perimeter_fraction = st.slider(
        "Screen min perimeter fraction",
        0.0,
        1.0,
        setting_value("screen_min_perimeter_fraction"),
        0.05,
        key=setting_key("screen_min_perimeter_fraction"),
        disabled=not screen_decoy_rejection,
        help="Optional requirement that recent track centers stay near frame edges. Leave at 0 to reject smooth constant-size overlays anywhere.",
    )
    screen_perimeter_margin = st.slider(
        "Screen perimeter margin",
        0.0,
        0.50,
        setting_value("screen_perimeter_margin"),
        0.01,
        key=setting_key("screen_perimeter_margin"),
        disabled=not screen_decoy_rejection,
        help="Frame-edge band used by perimeter fraction.",
    )
    occlusion_max_frames = st.slider(
        "Occlusion max frames",
        0,
        60,
        setting_value("occlusion_max_frames"),
        1,
        key=setting_key("occlusion_max_frames"),
        disabled=not occlusion_recovery,
        help="How long a track can disappear before it is deleted.",
    )
    occlusion_gate_distance = st.slider(
        "Occlusion gate distance",
        5.0,
        500.0,
        setting_value("occlusion_gate_distance"),
        5.0,
        key=setting_key("occlusion_gate_distance"),
        disabled=not occlusion_recovery,
        help="Maximum pixel distance from predicted center when rematching a reappearing object.",
    )
    st.divider()
    roi_mask_enabled = st.checkbox(
        "Use current ROI mask",
        value=setting_value("roi_mask_enabled"),
        key=setting_key("roi_mask_enabled"),
        help="Applies the mask from the ROI tab to reject, penalize, or constrain detections.",
    )
    st.divider()
    semantic_filter = st.checkbox(
        "Human semantic filter",
        value=setting_value("semantic_filter"),
        key=setting_key("semantic_filter"),
        help="Runs a person detector and suppresses motion boxes that overlap people.",
    )
    semantic_action = st.selectbox(
        "Human motion action",
        ["reject", "penalize"],
        index=select_index("semantic_action", ["reject", "penalize"]),
        key=setting_key("semantic_action"),
        disabled=not semantic_filter,
        help="Reject removes overlapping motion; penalize keeps it but tags it lower priority.",
    )
    semantic_conf = st.slider(
        "Person confidence",
        0.01,
        0.90,
        setting_value("semantic_conf"),
        0.01,
        key=setting_key("semantic_conf"),
        disabled=not semantic_filter,
        help="Minimum person detector confidence. Lower catches distant people but adds false positives.",
    )
    semantic_overlap_threshold = st.slider(
        "Person overlap threshold",
        0.01,
        1.0,
        setting_value("semantic_overlap_threshold"),
        0.01,
        key=setting_key("semantic_overlap_threshold"),
        disabled=not semantic_filter,
        help="Fraction of a motion box covered by a person box before action is applied.",
    )
    semantic_frame_stride = st.slider(
        "Person AI frame stride",
        1,
        20,
        setting_value("semantic_frame_stride"),
        1,
        key=setting_key("semantic_frame_stride"),
        disabled=not semantic_filter,
        help="Runs person detection every N frames and holds boxes between runs. Higher is faster.",
    )
    semantic_warmup = st.checkbox(
        "Warm up human model",
        value=setting_value("semantic_warmup"),
        key=setting_key("semantic_warmup"),
        disabled=not semantic_filter,
        help="Runs one untimed inference before processing so the measured frame speed starts closer to steady state.",
    )
    semantic_motion_gate = st.checkbox(
        "Gate human AI by motion",
        value=setting_value("semantic_motion_gate"),
        key=setting_key("semantic_motion_gate"),
        disabled=not semantic_filter,
        help="Skips person inference while the previous frame had no raw motion. Faster on quiet clips but can miss the first frame of new motion.",
    )
    semantic_imgsz = st.selectbox(
        "Person AI image size",
        [640, 960, 1280],
        index=select_index("semantic_imgsz", [640, 960, 1280]),
        key=setting_key("semantic_imgsz"),
        disabled=not semantic_filter,
        help="Input size for the person detector. Higher can catch smaller people but is slower.",
    )
    with st.expander("Human model"):
        semantic_model_repo = st.text_input(
            "Model repo",
            setting_value("semantic_model_repo"),
            key=setting_key("semantic_model_repo"),
            disabled=not semantic_filter,
            help="Hugging Face repository containing the YOLO person model.",
        )
        semantic_model_file = st.text_input(
            "Model file",
            setting_value("semantic_model_file"),
            key=setting_key("semantic_model_file"),
            disabled=not semantic_filter,
            help="Model file inside the Hugging Face repository.",
        )
        semantic_weights = st.text_input(
            "Local weights path",
            setting_value("semantic_weights"),
            key=setting_key("semantic_weights"),
            disabled=not semantic_filter,
            help="Optional local .pt file. When set, it overrides repo/model download.",
        )

    current_settings = {
        "diff_threshold": int(diff_threshold),
        "min_area": float(min_area),
        "blur_kernel": int(blur_kernel),
        "morph_kernel": int(morph_kernel),
        "trail_frames": int(trail_frames),
        "max_motion_ratio": float(max_motion_ratio),
        "analysis_scale": float(analysis_scale),
        "overlay_merge_distance": float(overlay_merge_distance),
        "overlay_hold_frames": int(overlay_hold_frames),
        "overlay_hold_expand_px": float(overlay_hold_expand_px),
        "processing_backend": processing_backend,
        "cuda_device": int(cuda_device),
        "write_overlay_video": bool(write_overlay_video),
        "write_motion_video": bool(write_motion_video),
        "write_jsonl": bool(write_jsonl),
        "limit_frame_range": bool(limit_frame_range),
        "start_frame": int(start_frame),
        "max_frames": int(max_frames),
        "shake_protection": bool(shake_protection),
        "shake_min_shift": float(shake_min_shift),
        "shake_consensus": float(shake_consensus),
        "shake_consensus_px": float(shake_consensus_px),
        "shake_frame_stride": int(shake_frame_stride),
        "shake_analysis_scale": float(shake_analysis_scale),
        "shake_max_corners": int(shake_max_corners),
        "hysteresis": bool(hysteresis),
        "hysteresis_high_threshold": int(hysteresis_high_threshold),
        "temporal_filter": bool(temporal_filter),
        "track_confirmation": bool(track_confirmation),
        "direction_consistency": bool(direction_consistency),
        "temporal_window_frames": int(temporal_window_frames),
        "temporal_min_hits": int(temporal_min_hits),
        "track_confirm_hits": int(track_confirm_hits),
        "track_max_missed": int(track_max_missed),
        "track_match_distance": float(track_match_distance),
        "direction_min_hits": int(direction_min_hits),
        "direction_min_displacement": float(direction_min_displacement),
        "direction_cosine": float(direction_cosine),
        "drone_track_filter": bool(drone_track_filter),
        "drone_min_track_hits": int(drone_min_track_hits),
        "drone_min_normalized_speed": float(drone_min_normalized_speed),
        "drone_max_normalized_speed": float(drone_max_normalized_speed),
        "screen_decoy_rejection": bool(screen_decoy_rejection),
        "screen_min_track_hits": int(screen_min_track_hits),
        "screen_max_area_cv": float(screen_max_area_cv),
        "screen_max_aspect_cv": float(screen_max_aspect_cv),
        "screen_min_path_smoothness": float(screen_min_path_smoothness),
        "screen_min_perimeter_fraction": float(screen_min_perimeter_fraction),
        "screen_perimeter_margin": float(screen_perimeter_margin),
        "occlusion_recovery": bool(occlusion_recovery),
        "occlusion_max_frames": int(occlusion_max_frames),
        "occlusion_gate_distance": float(occlusion_gate_distance),
        "roi_mask_enabled": bool(roi_mask_enabled),
        "semantic_filter": bool(semantic_filter),
        "semantic_action": semantic_action,
        "semantic_conf": float(semantic_conf),
        "semantic_overlap_threshold": float(semantic_overlap_threshold),
        "semantic_frame_stride": int(semantic_frame_stride),
        "semantic_warmup": bool(semantic_warmup),
        "semantic_motion_gate": bool(semantic_motion_gate),
        "semantic_imgsz": int(semantic_imgsz),
        "semantic_model_repo": semantic_model_repo,
        "semantic_model_file": semantic_model_file,
        "semantic_weights": semantic_weights,
    }
    st.session_state["settings_profile_values"] = dict(current_settings)
    if profile_download_slot is not None:
        with profile_download_slot.container():
            profile_payload = settings_profile_payload(current_settings)
            st.download_button(
                "Export current parameters JSON",
                json.dumps(profile_payload, indent=2),
                file_name="motion_diff_parameters.json",
                mime="application/json",
                help="Saves the current UI parameters and ROI mask for later import.",
                use_container_width=True,
            )

tabs = st.tabs(["Upload", "Local path", "ROI mask"])
rendered_summary_path: str | None = None


def write_current_roi_mask(root: Path) -> Path | None:
    if not roi_mask_enabled:
        return None
    payload = current_roi_mask_payload()
    if not payload["zones"]:
        return None
    mask_path = root / "roi_mask.json"
    mask_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return mask_path


def make_run_root() -> Path:
    run_root = RUN_CACHE_ROOT / f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    run_root.mkdir(parents=True, exist_ok=True)
    return run_root


def request_stop_file(path: str) -> None:
    Path(path).write_text("stop\n", encoding="utf-8")


def remember_run_paths(run_root: Path, out_dir: Path) -> None:
    st.session_state["last_run_root"] = str(run_root)
    st.session_state["last_summary_path"] = str(out_dir / "summary.json")


def load_summary_file(path: str | Path) -> dict | None:
    summary_path = Path(path)
    if not summary_path.exists():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def safe_cache_name(name: str) -> str:
    return "".join(char if char.isalnum() or char in ".-_" else "_" for char in name)


def cache_uploaded_video(uploaded_file) -> Path | None:
    if uploaded_file is None:
        return None

    upload_id = f"{getattr(uploaded_file, 'file_id', '')}:{uploaded_file.name}:{uploaded_file.size}"
    cached_path = st.session_state.get("uploaded_video_cache_path")
    if (
        upload_id == st.session_state.get("uploaded_video_cache_id")
        and cached_path
        and Path(cached_path).exists()
    ):
        return Path(cached_path)

    UPLOAD_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    name = safe_cache_name(uploaded_file.name)
    target = UPLOAD_CACHE_ROOT / f"{Path(name).stem}_{uploaded_file.size}{Path(name).suffix}"
    with target.open("wb") as output:
        output.write(uploaded_file.getbuffer())

    st.session_state["uploaded_video_cache_id"] = upload_id
    st.session_state["uploaded_video_cache_path"] = str(target)
    st.session_state["roi_preview_path"] = str(target)
    st.session_state["roi_last_click"] = None
    st.session_state["roi_last_snap"] = ""
    return target


@st.cache_data(show_spinner=False)
def read_video_frame(path: str, frame_index: int) -> tuple[np.ndarray, int, int, int]:
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if frame_count > 0:
        frame_index = int(np.clip(frame_index, 0, frame_count - 1))
    else:
        frame_index = max(0, int(frame_index))
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError(f"Could not read frame {frame_index} from video: {path}")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), width, height, frame_count


def fit_preview_image(image_rgb: np.ndarray, max_width: int = 900) -> np.ndarray:
    if image_rgb.shape[1] <= max_width:
        return image_rgb
    scale = max_width / float(image_rgb.shape[1])
    return cv2.resize(
        image_rgb,
        (max_width, max(1, int(round(image_rgb.shape[0] * scale)))),
        interpolation=cv2.INTER_AREA,
    )


def zone_color(zone_type: str) -> tuple[int, int, int]:
    return {
        "ignore": (255, 60, 60),
        "penalty": (255, 150, 0),
        "flight": (60, 220, 120),
    }.get(zone_type, (255, 255, 255))


def draw_roi_preview(
    image_rgb: np.ndarray,
    zones: list[dict],
    current_points: list[list[float]],
) -> np.ndarray:
    output = image_rgb.copy()
    height, width = output.shape[:2]
    overlay = output.copy()

    for zone in zones:
        points = zone.get("points", [])
        if len(points) < 3:
            continue
        color = zone_color(zone.get("type", "ignore"))
        pixel_points = np.array(
            [[int(round(x * width)), int(round(y * height))] for x, y in points],
            dtype=np.int32,
        )
        cv2.fillPoly(overlay, [pixel_points], color)
        cv2.polylines(output, [pixel_points], isClosed=True, color=color, thickness=2)
        cv2.putText(
            output,
            f"{zone.get('type', 'ignore')}:{zone.get('name', '')}",
            tuple(pixel_points[0]),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
            cv2.LINE_AA,
        )

    output = cv2.addWeighted(overlay, 0.18, output, 0.82, 0)
    if current_points:
        current_pixels = np.array(
            [[int(round(x * width)), int(round(y * height))] for x, y in current_points],
            dtype=np.int32,
        )
        for point in current_pixels:
            cv2.circle(output, tuple(point), 4, (0, 220, 255), thickness=cv2.FILLED)
        if len(current_pixels) > 1:
            cv2.polylines(output, [current_pixels], isClosed=False, color=(0, 220, 255), thickness=2)
    return output


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "estimating"
    seconds = max(0, int(round(float(seconds))))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s"


def progress_stage_label(stage: str) -> str:
    return {
        "opening_video": "Opening video",
        "loading_semantic_model": "Loading AI model",
        "semantic_model_ready": "AI model ready",
        "warming_semantic_model": "Warming AI model",
        "semantic_model_warm": "AI model warm",
        "processing": "Processing frames",
        "finalizing": "Finalizing outputs",
        "complete": "Complete",
        "stopped": "Stopped",
    }.get(stage, stage.replace("_", " ").title())


def render_progress_event(event: dict, progress_bar, status_placeholder) -> None:
    stage = str(event.get("stage", "processing"))
    stage_label = progress_stage_label(stage)
    processed = int(event.get("processed_frames") or 0)
    total = int(event.get("total_frames") or 0)
    progress = event.get("progress")
    if progress is None:
        progress_value = 0.0
    else:
        progress_value = float(np.clip(float(progress), 0.0, 1.0))
    if stage == "complete":
        progress_value = 1.0

    if total > 0:
        bar_text = f"{stage_label}: {processed}/{total} frames"
    else:
        bar_text = stage_label
    progress_bar.progress(progress_value, text=bar_text)

    eta = format_duration(event.get("eta_s"))
    elapsed = format_duration(event.get("elapsed_s"))
    average_fps = float(event.get("average_fps") or event.get("processing_fps") or 0.0)
    average_ms = event.get("average_ms_per_frame") or event.get("processing_ms_per_frame")
    recent_fps = event.get("recent_fps")
    recent_ms = event.get("recent_ms_per_frame")
    if recent_fps is not None and recent_ms is not None:
        speed_text = (
            f"recent {float(recent_fps):.1f} fps / {float(recent_ms):.1f} ms "
            f"· avg {average_fps:.1f} fps"
        )
    elif average_ms is None:
        speed_text = f"avg {average_fps:.1f} fps"
    else:
        speed_text = f"avg {average_fps:.1f} fps / {float(average_ms):.1f} ms"
    remaining = event.get("remaining_frames")
    remaining_text = f"{remaining} frames left" if remaining is not None else "frames left unknown"
    counts = (
        f"raw={event.get('raw_detection_count', 0)} "
        f"kept={event.get('kept_detection_count', 0)} "
        f"semantic rejected={event.get('semantic_rejected_count', 0)} "
        f"ROI rejected={event.get('roi_rejected_count', 0)} "
        f"drone gate={event.get('drone_track_rejected_count', 0)} "
        f"screen={event.get('screen_decoy_rejected_count', 0)} "
        f"recovered={event.get('occlusion_recovered_count', 0)}"
    )
    engine_counts = (
        f"shake estimated={event.get('shake_estimated_count', 0)} "
        f"reused={event.get('shake_reused_count', 0)} "
        f"AI runs={event.get('semantic_inference_count', 0)} "
        f"AI skipped={event.get('semantic_skipped_count', 0)}"
    )
    status_placeholder.markdown(
        f"**{stage_label}**  \n"
        f"Elapsed `{elapsed}` · ETA `{eta}` · {remaining_text} · {speed_text}  \n"
        f"`{counts}`  \n"
        f"`{engine_counts}`"
    )


def run_detector(args: list[str], stop_file: Path | None = None) -> dict:
    progress_bar = st.progress(0.0, text="Starting detector...")
    status_placeholder = st.empty()
    stop_placeholder = st.empty()
    command = [
        sys.executable,
        str(SCRIPT_PATH),
        "--json",
        "--progress-json",
        "--progress-interval",
        "0.5",
        *args,
    ]
    if stop_file is not None:
        command.extend(["--stop-file", str(stop_file)])
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(Path(__file__).parent),
    )
    lines: list[str] = []
    summary: dict | None = None
    if process.stdout is None:
        raise RuntimeError("Detector did not expose progress output.")

    if stop_file is not None:
        stop_placeholder.button(
            "Stop processing and finalize partial output",
            key=f"stop_{stop_file.name}",
            on_click=request_stop_file,
            args=(str(stop_file),),
            help="Requests a graceful stop. The detector closes video writers and returns a partial summary.",
        )

    try:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            lines.append(line)
            if line.startswith(PROGRESS_PREFIX):
                try:
                    event = json.loads(line[len(PROGRESS_PREFIX) :])
                except json.JSONDecodeError:
                    continue
                render_progress_event(event, progress_bar, status_placeholder)
                continue
            if line.startswith("{"):
                try:
                    summary = json.loads(line)
                except json.JSONDecodeError:
                    pass
    finally:
        if process.poll() is None:
            if stop_file is not None:
                request_stop_file(str(stop_file))
                status_placeholder.markdown(
                    "**Stopping**  \n"
                    "Stop requested. Waiting for partial videos and summaries to finalize."
                )
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    process.terminate()
            else:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    return_code = process.wait()
    stop_placeholder.empty()
    if return_code != 0:
        detail_lines = [line for line in lines if not line.startswith(PROGRESS_PREFIX)]
        detail = "\n".join(detail_lines[-12:])
        raise RuntimeError(detail or f"Detector exited with code {return_code}.")

    if summary is None:
        for line in reversed(lines):
            if line.startswith("{"):
                summary = json.loads(line)
                break
    if summary is None:
        raise RuntimeError("Detector did not return JSON.")

    progress_bar.progress(1.0, text="Stopped" if summary.get("stopped_early") else "Complete")
    status_placeholder.markdown(
        f"**{'Stopped' if summary.get('stopped_early') else 'Complete'}**  \n"
        f"Processed `{summary['frame_count']}` frames in "
        f"`{format_duration(summary.get('processing_seconds'))}` · "
        f"`{summary.get('processing_ms_per_frame', 0.0)}` ms per frame"
    )
    return summary


def common_cli_args(
    out_dir: Path,
    roi_mask_path: Path | None = None,
    start_frame_override: int | None = None,
    max_frames_override: int | None = None,
) -> list[str]:
    args = [
        "--diff-threshold",
        str(int(diff_threshold)),
        "--min-area",
        str(float(min_area)),
        "--blur-kernel",
        str(int(blur_kernel)),
        "--morph-kernel",
        str(int(morph_kernel)),
        "--trail-frames",
        str(int(trail_frames)),
        "--max-motion-ratio",
        str(float(max_motion_ratio)),
        "--analysis-scale",
        str(float(analysis_scale)),
        "--overlay-merge-distance",
        str(float(overlay_merge_distance)),
        "--overlay-hold-frames",
        str(int(overlay_hold_frames)),
        "--overlay-hold-expand-px",
        str(float(overlay_hold_expand_px)),
        "--backend",
        processing_backend,
        "--cuda-device",
        str(int(cuda_device)),
        "--shake-min-shift",
        str(float(shake_min_shift)),
        "--shake-consensus",
        str(float(shake_consensus)),
        "--shake-consensus-px",
        str(float(shake_consensus_px)),
        "--shake-frame-stride",
        str(int(shake_frame_stride)),
        "--shake-analysis-scale",
        str(float(shake_analysis_scale)),
        "--shake-max-corners",
        str(int(shake_max_corners)),
        "--hysteresis-high-threshold",
        str(int(hysteresis_high_threshold)),
        "--temporal-window-frames",
        str(int(temporal_window_frames)),
        "--temporal-min-hits",
        str(int(temporal_min_hits)),
        "--track-confirm-hits",
        str(int(track_confirm_hits)),
        "--track-max-missed",
        str(int(track_max_missed)),
        "--track-match-distance",
        str(float(track_match_distance)),
        "--direction-min-hits",
        str(int(direction_min_hits)),
        "--direction-min-displacement",
        str(float(direction_min_displacement)),
        "--direction-cosine",
        str(float(direction_cosine)),
        "--drone-min-track-hits",
        str(int(drone_min_track_hits)),
        "--drone-min-normalized-speed",
        str(float(drone_min_normalized_speed)),
        "--drone-max-normalized-speed",
        str(float(drone_max_normalized_speed)),
        "--screen-min-track-hits",
        str(int(screen_min_track_hits)),
        "--screen-max-area-cv",
        str(float(screen_max_area_cv)),
        "--screen-max-aspect-cv",
        str(float(screen_max_aspect_cv)),
        "--screen-min-path-smoothness",
        str(float(screen_min_path_smoothness)),
        "--screen-min-perimeter-fraction",
        str(float(screen_min_perimeter_fraction)),
        "--screen-perimeter-margin",
        str(float(screen_perimeter_margin)),
        "--occlusion-max-frames",
        str(int(occlusion_max_frames)),
        "--occlusion-gate-distance",
        str(float(occlusion_gate_distance)),
        "--out-dir",
        str(out_dir),
    ]
    if not write_motion_video:
        args.append("--no-motion-video")
    if not write_overlay_video:
        args.append("--no-overlay-video")
    if not write_jsonl:
        args.append("--no-jsonl")
    effective_start_frame = (
        int(start_frame_override) if start_frame_override is not None else int(start_frame)
    )
    effective_max_frames = (
        int(max_frames_override) if max_frames_override is not None else int(max_frames)
    )
    if limit_frame_range or start_frame_override is not None or max_frames_override is not None:
        args.extend(
            [
                "--start-frame",
                str(max(0, effective_start_frame)),
            ]
        )
        if limit_frame_range or max_frames_override is not None:
            args.extend(["--max-frames", str(max(0, effective_max_frames))])
    if not shake_protection:
        args.append("--disable-shake-protection")
    if hysteresis:
        args.append("--enable-hysteresis")
    if temporal_filter:
        args.append("--enable-temporal-filter")
    if track_confirmation:
        args.append("--enable-track-confirmation")
    if direction_consistency:
        args.append("--enable-direction-consistency")
    if drone_track_filter:
        args.append("--enable-drone-track-filter")
    if screen_decoy_rejection:
        args.append("--enable-screen-decoy-rejection")
    if occlusion_recovery:
        args.append("--enable-occlusion-recovery")
    if roi_mask_path is not None:
        args.extend(["--roi-mask", str(roi_mask_path)])
    if semantic_filter:
        args.extend(
            [
                "--enable-semantic-filter",
                "--semantic-labels",
                "person",
                "--semantic-action",
                semantic_action,
                "--semantic-model-repo",
                semantic_model_repo,
                "--semantic-model-file",
                semantic_model_file,
                "--semantic-conf",
                str(float(semantic_conf)),
                "--semantic-imgsz",
                str(int(semantic_imgsz)),
                "--semantic-frame-stride",
                str(int(semantic_frame_stride)),
                "--semantic-overlap-threshold",
                str(float(semantic_overlap_threshold)),
            ]
        )
        if semantic_weights.strip():
            args.extend(["--semantic-weights", semantic_weights.strip()])
        if semantic_warmup:
            args.append("--semantic-warmup")
        if semantic_motion_gate:
            args.append("--semantic-motion-gate")
    return args


def manifest_path_for_run(run_root: Path) -> Path:
    return run_root / RUN_MANIFEST_NAME


def load_resume_manifest(path: str | Path | None) -> dict | None:
    if not path:
        return None
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / RUN_MANIFEST_NAME
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["manifest_path"] = str(manifest_path)
    return payload


def write_resume_manifest(manifest: dict) -> Path:
    manifest_path = Path(manifest["manifest_path"])
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def create_resume_manifest(run_root: Path, source_path: str | Path) -> dict:
    manifest_path = manifest_path_for_run(run_root)
    manifest = {
        "version": 1,
        "source": str(source_path),
        "run_root": str(run_root),
        "manifest_path": str(manifest_path),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "segments": [],
        "combined_outputs": {},
        "combined_summary_path": "",
    }
    write_resume_manifest(manifest)
    st.session_state["last_resume_manifest_path"] = str(manifest_path)
    return manifest


def segment_root_for(manifest: dict, segment_index: int) -> Path:
    run_root = Path(manifest["run_root"])
    return run_root / "segments" / f"segment_{segment_index:03d}"


def append_segment_to_manifest(
    manifest: dict,
    summary: dict,
    segment_root: Path,
    roi_mask_path: Path | None,
) -> dict:
    segments = list(manifest.get("segments", []))
    segment_index = len(segments) + 1
    segments.append(
        {
            "index": segment_index,
            "segment_root": str(segment_root),
            "summary_path": str(summary.get("summary_path", segment_root / "outputs" / "summary.json")),
            "start_frame": summary.get("start_frame"),
            "end_frame": summary.get("end_frame"),
            "frame_count": summary.get("frame_count", 0),
            "stopped_early": summary.get("stopped_early", False),
            "stop_reason": summary.get("stop_reason"),
            "overlay_path": summary.get("overlay_path"),
            "motion_only_path": summary.get("motion_only_path"),
            "jsonl_path": summary.get("jsonl_path"),
            "roi_mask_path": str(roi_mask_path) if roi_mask_path else None,
            "settings": dict(current_settings),
        }
    )
    manifest["segments"] = segments
    write_resume_manifest(manifest)
    return manifest


def load_segment_summaries(manifest: dict) -> list[dict]:
    summaries: list[dict] = []
    for segment in manifest.get("segments", []):
        summary = load_summary_file(segment.get("summary_path", ""))
        if summary is not None:
            summaries.append(summary)
    return summaries


def resume_next_start_frame(manifest: dict) -> int:
    summaries = load_segment_summaries(manifest)
    if not summaries:
        return 0
    end_frame = summaries[-1].get("end_frame")
    if end_frame is None:
        return int(summaries[-1].get("start_frame") or 0)
    return int(end_frame) + 1


def resume_is_complete(manifest: dict) -> bool:
    summaries = load_segment_summaries(manifest)
    if not summaries:
        return False
    last = summaries[-1]
    source_total_frames = int(last.get("source_total_frames") or 0)
    next_frame = resume_next_start_frame(manifest)
    if source_total_frames > 0 and next_frame >= source_total_frames:
        return True
    return not bool(last.get("stopped_early", False)) and last.get("stop_reason") == "completed"


def concat_file_line(path: Path) -> str:
    escaped = path.as_posix().replace("'", "'\\''")
    return f"file '{escaped}'\n"


def stitch_segment_videos(paths: list[Path], output_path: Path) -> tuple[Path | None, str | None]:
    existing_paths = [path for path in paths if path.exists()]
    if not existing_paths:
        return None, None
    if len(existing_paths) == 1:
        return existing_paths[0], None
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        return existing_paths[-1], "ffmpeg not found; showing the latest segment instead of stitched video."

    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = output_path.with_suffix(".concat.txt")
    list_path.write_text("".join(concat_file_line(path) for path in existing_paths), encoding="utf-8")
    result = subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(output_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not output_path.exists():
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown error"
        return existing_paths[-1], f"Could not stitch segments; showing latest segment. ffmpeg: {detail}"
    return output_path, None


def concatenate_jsonl(paths: list[Path], output_path: Path) -> Path | None:
    existing_paths = [path for path in paths if path.exists()]
    if not existing_paths:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        for path in existing_paths:
            text = path.read_text(encoding="utf-8")
            if not text:
                continue
            output.write(text)
            if not text.endswith("\n"):
                output.write("\n")
    return output_path


SUMMARY_COUNTER_KEYS = [
    "frames_with_motion",
    "detection_count",
    "raw_detection_count",
    "roi_rejected_count",
    "roi_penalized_count",
    "semantic_detection_count",
    "semantic_rejected_count",
    "semantic_penalized_count",
    "temporal_rejected_count",
    "unconfirmed_rejected_count",
    "direction_rejected_count",
    "drone_track_rejected_count",
    "screen_decoy_rejected_count",
    "occlusion_recovered_count",
    "kept_detection_count",
    "global_motion_rejected_frames",
    "global_motion_detected_frames",
    "shake_estimated_frames",
    "shake_reused_frames",
]


def build_combined_summary(
    manifest: dict,
    motion_path: Path | None,
    overlay_path: Path | None,
    jsonl_path: Path | None,
    stitch_warnings: list[str],
) -> dict | None:
    summaries = load_segment_summaries(manifest)
    if not summaries:
        return None

    first = summaries[0]
    last = summaries[-1]
    processing_seconds = sum(float(summary.get("processing_seconds") or 0.0) for summary in summaries)
    frame_count = sum(int(summary.get("frame_count") or 0) for summary in summaries)
    semantic_inference_count = sum(
        int(summary.get("semantic_filter", {}).get("inference_count") or 0)
        for summary in summaries
    )
    semantic_skipped_count = sum(
        int(summary.get("semantic_filter", {}).get("skipped_count") or 0)
        for summary in summaries
    )

    combined = dict(last)
    combined.update(
        {
            "mode": "video",
            "source": first.get("source"),
            "frame_count": frame_count,
            "source_total_frames": last.get("source_total_frames", first.get("source_total_frames", 0)),
            "target_frame_count": last.get("source_total_frames", last.get("target_frame_count", 0)),
            "start_frame": first.get("start_frame"),
            "end_frame": last.get("end_frame"),
            "requested_max_frames": None,
            "stopped_early": not resume_is_complete(manifest),
            "stop_reason": "completed" if resume_is_complete(manifest) else last.get("stop_reason"),
            "duration_s": frame_count / max(float(last.get("fps") or first.get("fps") or 25.0), 0.001),
            "processing_seconds": round(processing_seconds, 3),
            "processing_seconds_per_frame": round(processing_seconds / max(1, frame_count), 6),
            "processing_ms_per_frame": round(processing_seconds * 1000.0 / max(1, frame_count), 3),
            "motion_only_path": str(motion_path) if motion_path else None,
            "overlay_path": str(overlay_path) if overlay_path else None,
            "jsonl_path": str(jsonl_path) if jsonl_path else None,
            "summary_path": str(Path(manifest["run_root"]) / COMBINED_SUMMARY_NAME),
            "segment_count": len(summaries),
            "segments": [
                {
                    "summary_path": summary.get("summary_path"),
                    "start_frame": summary.get("start_frame"),
                    "end_frame": summary.get("end_frame"),
                    "frame_count": summary.get("frame_count"),
                    "stopped_early": summary.get("stopped_early"),
                    "stop_reason": summary.get("stop_reason"),
                }
                for summary in summaries
            ],
            "resume": {
                "manifest_path": manifest.get("manifest_path"),
                "can_continue": not resume_is_complete(manifest),
                "next_start_frame": resume_next_start_frame(manifest),
            },
            "stitch_warnings": stitch_warnings,
        }
    )
    for key in SUMMARY_COUNTER_KEYS:
        combined[key] = sum(int(summary.get(key) or 0) for summary in summaries)

    semantic_summary = dict(combined.get("semantic_filter", {}))
    semantic_summary["inference_count"] = semantic_inference_count
    semantic_summary["skipped_count"] = semantic_skipped_count
    combined["semantic_filter"] = semantic_summary
    combined["outputs"] = {
        "motion_video": motion_path is not None,
        "overlay_video": overlay_path is not None,
        "jsonl": jsonl_path is not None,
    }
    return combined


def refresh_combined_outputs(manifest: dict) -> dict | None:
    summaries = load_segment_summaries(manifest)
    if not summaries:
        return None

    run_root = Path(manifest["run_root"])
    stitch_warnings: list[str] = []
    motion_path, motion_warning = stitch_segment_videos(
        [Path(path) for path in (summary.get("motion_only_path") for summary in summaries) if path],
        run_root / "combined_motion_only.mp4",
    )
    overlay_path, overlay_warning = stitch_segment_videos(
        [Path(path) for path in (summary.get("overlay_path") for summary in summaries) if path],
        run_root / "combined_motion_overlay.mp4",
    )
    if motion_warning:
        stitch_warnings.append(motion_warning)
    if overlay_warning:
        stitch_warnings.append(overlay_warning)
    jsonl_path = concatenate_jsonl(
        [Path(path) for path in (summary.get("jsonl_path") for summary in summaries) if path],
        run_root / "combined_motion_detections.jsonl",
    )
    combined = build_combined_summary(
        manifest,
        motion_path=motion_path,
        overlay_path=overlay_path,
        jsonl_path=jsonl_path,
        stitch_warnings=stitch_warnings,
    )
    if combined is None:
        return None

    summary_path = Path(combined["summary_path"])
    summary_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")
    manifest["combined_summary_path"] = str(summary_path)
    manifest["combined_outputs"] = {
        "motion_only_path": str(motion_path) if motion_path else None,
        "overlay_path": str(overlay_path) if overlay_path else None,
        "jsonl_path": str(jsonl_path) if jsonl_path else None,
    }
    write_resume_manifest(manifest)
    return combined


def continuation_max_frames() -> int | None:
    return int(max_frames) if limit_frame_range else None


def run_motion_segment(
    source_path: str | Path,
    manifest: dict,
    start_frame_override: int | None = None,
    max_frames_override: int | None = None,
) -> dict:
    segment_index = len(manifest.get("segments", [])) + 1
    segment_root = segment_root_for(manifest, segment_index)
    out_dir = segment_root / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stop_file = segment_root / "stop_requested"
    if stop_file.exists():
        stop_file.unlink()
    remember_run_paths(Path(manifest["run_root"]), out_dir)
    st.session_state["last_resume_manifest_path"] = str(manifest["manifest_path"])
    st.session_state["active_stop_file"] = str(stop_file)

    roi_mask_path = write_current_roi_mask(segment_root)
    if roi_mask_enabled and roi_mask_path is None:
        st.warning("ROI mask is enabled, but no zones are defined. Running without ROI filtering.")

    try:
        summary = run_detector(
            [
                "video",
                str(source_path),
                *common_cli_args(
                    out_dir,
                    roi_mask_path,
                    start_frame_override=start_frame_override,
                    max_frames_override=max_frames_override,
                ),
            ],
            stop_file=stop_file,
        )
    finally:
        st.session_state["active_stop_file"] = None

    append_segment_to_manifest(manifest, summary, segment_root, roi_mask_path)
    combined = refresh_combined_outputs(manifest) or summary
    st.session_state["last_summary_path"] = str(combined.get("summary_path", summary.get("summary_path", "")))
    return combined


def render_resume_controls(manifest_path: str | Path | None) -> dict | None:
    manifest = load_resume_manifest(manifest_path)
    if manifest is None:
        return None
    latest_combined = load_summary_file(manifest.get("combined_summary_path", ""))
    if latest_combined is None:
        latest_combined = refresh_combined_outputs(manifest)

    if latest_combined is None:
        return None

    next_frame = resume_next_start_frame(manifest)
    segment_count = len(manifest.get("segments", []))
    can_continue = not resume_is_complete(manifest)
    status = "complete" if not can_continue else f"ready to continue from frame {next_frame}"
    st.caption(f"Resumable run: {segment_count} segment(s), {status}.")

    warnings = latest_combined.get("stitch_warnings", [])
    for warning in warnings:
        st.warning(warning)

    if not can_continue:
        return latest_combined

    cols = st.columns([1, 2])
    if cols[0].button(
        f"Continue from frame {next_frame}",
        type="primary",
        key=f"continue_{Path(manifest['manifest_path']).parent.name}_{segment_count}_{next_frame}",
        help=(
            "Starts a new segment from the next unprocessed source frame, then rebuilds "
            "the cumulative result video."
        ),
    ):
        with st.spinner(f"Continuing from frame {next_frame}..."):
            return run_motion_segment(
                manifest["source"],
                manifest,
                start_frame_override=next_frame,
                max_frames_override=continuation_max_frames(),
            )
    cols[1].caption("Stop again at any point to inspect the cumulative partial output.")
    return latest_combined


def read_jsonl(path: str | Path) -> list[dict]:
    payload = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def existing_path(path: str | Path | None) -> Path | None:
    if not path:
        return None
    candidate = Path(path)
    return candidate if candidate.exists() else None


def render_detections(records: list[dict]) -> None:
    rows = []
    for record in records:
        for detection in record.get("detections", []):
            rows.append(
                {
                    "frame_index": record["frame_index"],
                    "timestamp_s": record["timestamp_s"],
                    "motion_ratio": record["motion_ratio"],
                    "global_motion_detected": record.get("global_motion_detected", False),
                    "global_motion_rejected": record.get("global_motion_rejected", False),
                    **detection,
                }
            )
    if rows:
        st.dataframe(pd.DataFrame(rows[:500]), use_container_width=True, hide_index=True)
    else:
        st.info("No motion detections with the current settings.")


def render_outputs(summary: dict) -> None:
    motion_path = existing_path(summary.get("motion_only_path"))
    overlay_path = existing_path(summary.get("overlay_path"))
    jsonl_path = existing_path(summary.get("jsonl_path"))
    summary_path = existing_path(summary.get("summary_path"))
    motion_bytes = motion_path.read_bytes() if motion_path else None
    overlay_bytes = overlay_path.read_bytes() if overlay_path else None
    jsonl_bytes = jsonl_path.read_bytes() if jsonl_path else None
    summary_bytes = summary_path.read_bytes() if summary_path else json.dumps(summary, indent=2).encode()
    records = read_jsonl(jsonl_path) if jsonl_path else []

    metric_cols = st.columns(6)
    metric_cols[0].metric("Frames", summary["frame_count"])
    metric_cols[1].metric("Motion frames", summary["frames_with_motion"])
    metric_cols[2].metric("Detections", summary["detection_count"])
    metric_cols[3].metric("Shake frames", summary.get("global_motion_detected_frames", 0))
    metric_cols[4].metric("Rejected frames", summary["global_motion_rejected_frames"])
    metric_cols[5].metric("ms / frame", summary.get("processing_ms_per_frame", 0.0))
    if summary.get("stopped_early"):
        st.warning(
            f"Processing stopped early after {summary.get('frame_count', 0)} frames "
            f"({summary.get('stop_reason')})."
        )

    roi_cols = st.columns(4)
    roi_cols[0].metric("Raw detections", summary.get("raw_detection_count", summary["detection_count"]))
    roi_cols[1].metric("Kept detections", summary.get("kept_detection_count", summary["detection_count"]))
    roi_cols[2].metric("ROI rejected", summary.get("roi_rejected_count", 0))
    roi_cols[3].metric("ROI penalized", summary.get("roi_penalized_count", 0))
    filter_cols = st.columns(6)
    filter_cols[0].metric("Temporal rejected", summary.get("temporal_rejected_count", 0))
    filter_cols[1].metric("Unconfirmed rejected", summary.get("unconfirmed_rejected_count", 0))
    filter_cols[2].metric("Direction rejected", summary.get("direction_rejected_count", 0))
    filter_cols[3].metric("Drone gate rejected", summary.get("drone_track_rejected_count", 0))
    filter_cols[4].metric("Screen rejected", summary.get("screen_decoy_rejected_count", 0))
    filter_cols[5].metric("Occlusion recovered", summary.get("occlusion_recovered_count", 0))
    semantic_cols = st.columns(4)
    semantic_cols[0].metric("Semantic objects", summary.get("semantic_detection_count", 0))
    semantic_cols[1].metric("Semantic rejected", summary.get("semantic_rejected_count", 0))
    semantic_cols[2].metric("Semantic penalized", summary.get("semantic_penalized_count", 0))
    semantic_cols[3].metric("Processing seconds", summary.get("processing_seconds", 0.0))
    perf_cols = st.columns(4)
    perf_cols[0].metric("Shake estimated", summary.get("shake_estimated_frames", 0))
    perf_cols[1].metric("Shake reused", summary.get("shake_reused_frames", 0))
    perf_cols[2].metric("AI runs", summary.get("semantic_filter", {}).get("inference_count", 0))
    perf_cols[3].metric("AI skipped", summary.get("semantic_filter", {}).get("skipped_count", 0))
    backend_summary = summary.get("backend", {})
    if backend_summary:
        st.caption(
            f"Backend requested={backend_summary.get('requested')} "
            f"motion={backend_summary.get('motion_backend', backend_summary.get('used'))} "
            f"semantic={backend_summary.get('semantic_device', 'cpu')} "
            f"cuda_devices={backend_summary.get('cuda_device_count')} "
            f"{backend_summary.get('message', '')}"
        )
    roi_summary = summary.get("roi_mask", {})
    if roi_summary.get("enabled"):
        st.caption(
            f"ROI mode={roi_summary.get('mode')} zones={roi_summary.get('zone_count')} "
            f"path={roi_summary.get('path')}"
        )
    semantic_summary = summary.get("semantic_filter", {})
    if semantic_summary.get("enabled"):
        st.caption(
            f"Semantic labels={','.join(semantic_summary.get('labels', []))} "
            f"action={semantic_summary.get('action')} conf={semantic_summary.get('confidence')} "
            f"stride={semantic_summary.get('frame_stride')} device={semantic_summary.get('device')} "
            f"model={semantic_summary.get('model_repo')}"
        )
    output_summary = summary.get("outputs", {})
    st.caption(
        "Outputs: "
        f"overlay={output_summary.get('overlay_video', bool(overlay_path))} "
        f"motion-only={output_summary.get('motion_video', bool(motion_path))} "
        f"jsonl={output_summary.get('jsonl', bool(jsonl_path))}"
    )

    preview_cols = st.columns(2)
    with preview_cols[0]:
        st.subheader("Motion only")
        if motion_bytes is not None and motion_path is not None:
            st.video(motion_bytes)
            st.download_button(
                "Download motion-only video",
                motion_bytes,
                file_name=motion_path.name,
                mime="video/mp4",
            )
        else:
            st.info("Motion-only video was disabled for this run.")
    with preview_cols[1]:
        st.subheader("Overlay")
        if overlay_bytes is not None and overlay_path is not None:
            st.video(overlay_bytes)
            st.download_button(
                "Download overlay video",
                overlay_bytes,
                file_name=overlay_path.name,
                mime="video/mp4",
            )
        else:
            st.info("Overlay video was disabled for this run.")

    if records:
        render_detections(records)
    elif jsonl_path is None:
        st.info("Per-frame JSONL was disabled for this run.")
    else:
        render_detections(records)
    download_cols = st.columns(2)
    if jsonl_bytes is not None:
        download_cols[0].download_button(
            "Download detections JSONL",
            jsonl_bytes,
            file_name="motion_detections.jsonl",
            mime="application/x-ndjson",
        )
    else:
        download_cols[0].info("JSONL disabled")
    download_cols[1].download_button(
        "Download summary JSON",
        summary_bytes,
        file_name="summary.json",
        mime="application/json",
    )


with tabs[0]:
    upload = st.file_uploader("Video file", type=["mp4", "avi", "mov", "mkv"])
    uploaded_video_path = cache_uploaded_video(upload) if upload else None
    if uploaded_video_path is not None:
        st.caption(f"ROI preview source: {uploaded_video_path}")
    if upload and st.button("Run motion diff on upload", type="primary"):
        run_root = make_run_root()
        manifest = create_resume_manifest(run_root, uploaded_video_path)
        with st.spinner("Processing video..."):
            try:
                summary = run_motion_segment(uploaded_video_path, manifest)
            except Exception as exc:
                st.error(f"Detector failed: {type(exc).__name__}: {exc}")
                st.stop()
        summary = render_resume_controls(st.session_state.get("last_resume_manifest_path")) or summary
        render_outputs(summary)
        rendered_summary_path = str(summary.get("summary_path", ""))


with tabs[1]:
    default_path = str(SAMPLE_PATH) if SAMPLE_PATH.exists() else ""
    local_path = st.text_input("Video path", default_path)
    if st.button("Run motion diff on local path", type="primary"):
        if not local_path.strip():
            st.error("Enter a local video path.")
            st.stop()
        run_root = make_run_root()
        manifest = create_resume_manifest(run_root, local_path.strip())
        with st.spinner("Processing video..."):
            try:
                summary = run_motion_segment(local_path.strip(), manifest)
            except Exception as exc:
                st.error(f"Detector failed: {type(exc).__name__}: {exc}")
                st.stop()
        summary = render_resume_controls(st.session_state.get("last_resume_manifest_path")) or summary
        render_outputs(summary)
        rendered_summary_path = str(summary.get("summary_path", ""))


with tabs[2]:
    st.subheader("ROI mask")
    mask_cols = st.columns([2, 1])

    with mask_cols[0]:
        mode = st.radio(
            "Mask mode",
            ["fixed", "handheld"],
            index=0 if st.session_state.get("roi_mode", "fixed") == "fixed" else 1,
            horizontal=True,
            help="Fixed masks are arena-relative; handheld masks are screen-relative guardrails.",
        )
        st.session_state["roi_mode"] = mode

        uploaded_preview_path = st.session_state.get("uploaded_video_cache_path", "")
        default_path = uploaded_preview_path or (str(SAMPLE_PATH) if SAMPLE_PATH.exists() else "")
        preview_path = st.text_input(
            "Preview video path",
            default_path,
            key="roi_preview_path",
            help="Video used only for choosing the frame where you draw ROI polygons.",
        )
        if uploaded_preview_path and preview_path == uploaded_preview_path:
            st.caption(f"Using uploaded video for ROI preview: {Path(uploaded_preview_path).name}")
        frame_index = st.number_input(
            "Frame number",
            min_value=0,
            value=0,
            step=1,
            help="Frame displayed for clicking ROI polygon vertices.",
        )

        default_zone_name = f"zone_{len(st.session_state['roi_zones']) + 1}"
        zone_cols = st.columns([2, 1, 1])
        zone_name = zone_cols[0].text_input(
            "Zone name",
            value=default_zone_name,
            help="Label stored in the mask JSON for debugging and summaries.",
        )
        zone_type = zone_cols[1].selectbox(
            "Zone type",
            ["ignore", "penalty", "flight"],
            help="Ignore rejects motion, penalty marks lower confidence, flight keeps only motion inside flight zones.",
        )
        penalty = zone_cols[2].number_input(
            "Penalty",
            min_value=0.0,
            max_value=1.0,
            value=0.5,
            step=0.1,
            disabled=zone_type != "penalty",
            help="Penalty value saved for penalty zones; disabled for ignore and flight zones.",
        )

        snap_cols = st.columns([1, 1])
        snap_enabled = snap_cols[0].checkbox(
            "Snap to edges/corners",
            key="roi_snap_enabled",
            help=(
                "When clicking near the preview border, x/y are snapped to exact 0.0 or 1.0 "
                "so masks can cover the full edge cleanly."
            ),
        )
        snap_threshold = snap_cols[1].slider(
            "Snap distance",
            min_value=0.0,
            max_value=0.10,
            step=0.005,
            format="%.3f",
            key="roi_snap_threshold",
            disabled=not snap_enabled,
            help="Normalized distance from an edge that should snap a clicked point to that edge.",
        )

        st.caption("Exact corner points")
        corner_cols = st.columns(4)
        corner_shortcuts = [
            ("Top-left", [0.0, 0.0]),
            ("Top-right", [1.0, 0.0]),
            ("Bottom-right", [1.0, 1.0]),
            ("Bottom-left", [0.0, 1.0]),
        ]
        for corner_col, (corner_label, corner_point) in zip(corner_cols, corner_shortcuts):
            if corner_col.button(
                corner_label,
                help=f"Adds an exact {corner_label.lower()} point to the pending polygon.",
            ):
                st.session_state["roi_points"].append(corner_point)
                st.session_state["roi_last_click"] = None
                st.session_state["roi_last_snap"] = f"added {corner_label.lower()} corner"
                st.rerun()

        band_cols = st.columns([1, 1, 1])
        edge_name = band_cols[0].selectbox(
            "Quick edge band",
            ["top", "bottom", "left", "right"],
            help="Creates a perfect rectangular zone locked to one edge of the preview.",
        )
        edge_band_size = band_cols[1].slider(
            "Band size",
            min_value=0.01,
            max_value=0.50,
            step=0.01,
            format="%.2f",
            key="roi_edge_band_size",
            help="Normalized thickness of the edge band. 0.20 means 20% of the image.",
        )
        if band_cols[2].button("Add edge band", help="Adds the selected edge band as a complete zone."):
            band_name = zone_name.strip() or default_zone_name
            if band_name == default_zone_name:
                band_name = f"{edge_name}_{zone_type}_band"
            st.session_state["roi_zones"].append(
                normalize_zone(
                    {
                        "name": band_name,
                        "type": zone_type,
                        "points": edge_band_points(edge_name, edge_band_size),
                        "penalty": penalty if zone_type == "penalty" else 0.0,
                    }
                )
            )
            st.session_state["roi_points"] = []
            st.session_state["roi_last_click"] = None
            st.session_state["roi_last_snap"] = f"added {edge_name} edge band"
            st.rerun()

        action_cols = st.columns(4)
        if action_cols[0].button("Undo point", help="Removes the last clicked polygon vertex."):
            if st.session_state["roi_points"]:
                st.session_state["roi_points"].pop()
            st.session_state["roi_last_click"] = None
            st.session_state["roi_last_snap"] = ""
            st.rerun()
        if action_cols[1].button(
            "Close zone",
            disabled=len(st.session_state["roi_points"]) < 3,
            help="Saves the pending polygon as a zone after at least three points.",
        ):
            st.session_state["roi_zones"].append(
                normalize_zone(
                    {
                        "name": zone_name,
                        "type": zone_type,
                        "points": st.session_state["roi_points"],
                        "penalty": penalty if zone_type == "penalty" else 0.0,
                    }
                )
            )
            st.session_state["roi_points"] = []
            st.session_state["roi_last_click"] = None
            st.session_state["roi_last_snap"] = ""
            st.rerun()
        if action_cols[2].button("Clear zones", help="Deletes all zones from the current in-memory mask."):
            st.session_state["roi_zones"] = []
            st.session_state["roi_points"] = []
            st.session_state["roi_last_click"] = None
            st.session_state["roi_last_snap"] = ""
            st.rerun()
        action_cols[3].download_button(
            "Download mask JSON",
            json.dumps(current_roi_mask_payload(), indent=2),
            file_name="roi_mask.json",
            mime="application/json",
            help="Downloads the normalized mask JSON for reuse.",
        )

        if streamlit_image_coordinates is None:
            st.error("Install streamlit-image-coordinates to edit masks by clicking on the frame.")
        elif preview_path.strip():
            try:
                frame_rgb, source_width, source_height, frame_count = read_video_frame(
                    preview_path.strip(),
                    int(frame_index),
                )
            except Exception as exc:
                st.error(f"Could not read preview frame: {type(exc).__name__}: {exc}")
            else:
                preview_rgb = fit_preview_image(frame_rgb)
                annotated = draw_roi_preview(
                    preview_rgb,
                    st.session_state["roi_zones"],
                    st.session_state["roi_points"],
                )
                coordinate = streamlit_image_coordinates(
                    annotated,
                    key=f"roi_coordinates_{preview_path}_{int(frame_index)}",
                )
                if coordinate:
                    raw_x = float(coordinate["x"]) / float(max(1, annotated.shape[1]))
                    raw_y = float(coordinate["y"]) / float(max(1, annotated.shape[0]))
                    x, y, snap_label = snap_normalized_point(
                        raw_x,
                        raw_y,
                        bool(snap_enabled),
                        float(snap_threshold),
                    )
                    click_key = f"{preview_path}:{int(frame_index)}:{x:.6f}:{y:.6f}"
                    if click_key != st.session_state.get("roi_last_click"):
                        st.session_state["roi_points"].append([x, y])
                        st.session_state["roi_last_click"] = click_key
                        st.session_state["roi_last_snap"] = (
                            f"snapped to {snap_label}" if snap_label else ""
                        )
                        st.rerun()
                snap_note = st.session_state.get("roi_last_snap", "")
                st.caption(
                    f"Preview {source_width}x{source_height}, frames={frame_count or 'unknown'}, "
                    f"pending points={len(st.session_state['roi_points'])}"
                    + (f", {snap_note}" if snap_note else "")
                )
        else:
            st.info("Enter a local preview video path to click polygon vertices.")

    with mask_cols[1]:
        uploaded_mask = st.file_uploader(
            "Upload mask JSON",
            type=["json"],
            key="roi_mask_upload",
            help="Loads a previously saved normalized ROI mask.",
        )
        if uploaded_mask is not None:
            upload_id = f"{uploaded_mask.name}:{uploaded_mask.size}"
            if st.session_state.get("roi_loaded_upload_id") != upload_id:
                try:
                    payload = json.loads(uploaded_mask.getvalue().decode("utf-8"))
                    load_roi_mask_payload(payload)
                except Exception as exc:
                    st.error(f"Could not load mask JSON: {type(exc).__name__}: {exc}")
                else:
                    st.session_state["roi_loaded_upload_id"] = upload_id
                    st.success("Mask JSON loaded.")
                    st.rerun()

        st.metric("Zones", len(st.session_state["roi_zones"]))
        st.metric("Pending points", len(st.session_state["roi_points"]))
        st.json(current_roi_mask_payload(), expanded=False)


last_summary_path = st.session_state.get("last_summary_path", "")
if last_summary_path and last_summary_path != rendered_summary_path:
    latest_summary = load_summary_file(last_summary_path)
    if latest_summary is not None:
        st.divider()
        st.subheader("Latest run output")
        latest_summary = (
            render_resume_controls(st.session_state.get("last_resume_manifest_path"))
            or latest_summary
        )
        render_outputs(latest_summary)
    elif st.session_state.get("last_run_root"):
        st.info("Latest run output is still finalizing. Refresh after a moment if it does not appear.")
