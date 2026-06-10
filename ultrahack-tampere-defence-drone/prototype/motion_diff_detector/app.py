"""Streamlit interface for fixed-camera motion differencing."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

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
PROGRESS_PREFIX = "PROGRESS "


st.set_page_config(page_title="Motion Diff Drone Detector", layout="wide")
st.title("Motion Diff Drone Detector")

if "roi_zones" not in st.session_state:
    st.session_state["roi_zones"] = []
if "roi_points" not in st.session_state:
    st.session_state["roi_points"] = []
if "roi_mode" not in st.session_state:
    st.session_state["roi_mode"] = "fixed"
if "uploaded_video_cache_id" not in st.session_state:
    st.session_state["uploaded_video_cache_id"] = None
if "uploaded_video_cache_path" not in st.session_state:
    st.session_state["uploaded_video_cache_path"] = ""

with st.sidebar:
    diff_threshold = st.slider(
        "Difference threshold",
        1,
        100,
        18,
        1,
        help="Minimum pixel brightness change needed before a pixel can become motion.",
    )
    min_area = st.number_input(
        "Minimum motion area",
        min_value=1.0,
        max_value=100000.0,
        value=1000.0,
        help="Smallest connected moving region accepted as a motion box.",
    )
    blur_kernel = st.selectbox(
        "Blur kernel",
        [1, 3, 5, 7, 9, 11],
        index=2,
        help="Pre-blur amount before differencing. Higher values smooth sensor noise but can soften tiny drones.",
    )
    morph_kernel = st.selectbox(
        "Morphology kernel",
        [1, 3, 5, 7, 9],
        index=1,
        help="Cleanup kernel for joining nearby motion pixels and removing isolated specks.",
    )
    trail_frames = st.slider(
        "Trail / hold frames",
        0,
        30,
        3,
        1,
        help="Keeps recent accepted motion visible for several frames in motion-only output.",
    )
    max_motion_ratio = st.slider(
        "Max motion ratio",
        0.01,
        1.0,
        0.10,
        0.01,
        help="Rejects frames where too much of the image changes at once.",
    )
    analysis_scale = st.slider(
        "Analysis scale",
        0.10,
        1.0,
        0.50,
        0.05,
        help="Downscale factor for motion analysis. Lower is faster; higher preserves tiny objects.",
    )
    shake_protection = st.checkbox(
        "Shake protection",
        value=True,
        help="Compensates global camera/floor movement before motion differencing.",
    )
    shake_min_shift = st.slider(
        "Shake min shift",
        0.0,
        20.0,
        1.5,
        0.1,
        disabled=not shake_protection,
        help="Minimum estimated global image shift before shake compensation is considered active.",
    )
    shake_consensus = st.slider(
        "Shake consensus",
        0.10,
        1.0,
        0.72,
        0.01,
        disabled=not shake_protection,
        help="Required share of tracked points agreeing on the same global movement.",
    )
    shake_consensus_px = st.slider(
        "Shake consensus px",
        0.5,
        10.0,
        2.0,
        0.1,
        disabled=not shake_protection,
        help="Pixel tolerance for deciding whether tracked points agree on global movement.",
    )
    st.divider()
    hysteresis = st.checkbox(
        "Hysteresis thresholding",
        value=False,
        help="Keeps weak motion only when it connects to a stronger high-threshold seed.",
    )
    hysteresis_high_threshold = st.slider(
        "Hysteresis high threshold",
        1,
        255,
        36,
        1,
        disabled=not hysteresis,
        help="Strong-pixel seed threshold used by hysteresis.",
    )
    temporal_filter = st.checkbox(
        "Temporal persistence",
        value=False,
        help="Rejects motion that appears only once and does not persist across nearby frames.",
    )
    track_confirmation = st.checkbox(
        "Track confirmation",
        value=False,
        help="Hides a new motion track until it has been seen enough times.",
    )
    direction_consistency = st.checkbox(
        "Direction consistency",
        value=False,
        help="Rejects tracks that jitter back and forth instead of moving consistently.",
    )
    track_tuning_enabled = temporal_filter or track_confirmation or direction_consistency
    temporal_window_frames = st.slider(
        "Persistence window",
        1,
        10,
        3,
        1,
        disabled=not temporal_filter,
        help="Number of recent frames checked by temporal persistence.",
    )
    temporal_min_hits = st.slider(
        "Persistence min hits",
        1,
        10,
        2,
        1,
        disabled=not temporal_filter,
        help="Minimum detections needed inside the persistence window.",
    )
    track_confirm_hits = st.slider(
        "Track confirm hits",
        1,
        10,
        2,
        1,
        disabled=not track_confirmation,
        help="Number of matched detections needed before a track is drawn.",
    )
    track_max_missed = st.slider(
        "Track max missed",
        0,
        10,
        2,
        1,
        disabled=not track_tuning_enabled,
        help="How many missed frames a track can survive before being deleted.",
    )
    track_match_distance = st.slider(
        "Track match distance",
        5.0,
        300.0,
        80.0,
        5.0,
        disabled=not track_tuning_enabled,
        help="Maximum pixel distance for matching a detection to an existing track.",
    )
    direction_min_hits = st.slider(
        "Direction min hits",
        2,
        10,
        3,
        1,
        disabled=not direction_consistency,
        help="Minimum track hits before direction consistency can reject jitter.",
    )
    direction_min_displacement = st.slider(
        "Direction min displacement",
        0.0,
        30.0,
        2.0,
        0.5,
        disabled=not direction_consistency,
        help="Small movements below this distance are ignored for direction checks.",
    )
    direction_cosine = st.slider(
        "Direction cosine",
        -1.0,
        1.0,
        0.20,
        0.05,
        disabled=not direction_consistency,
        help="Allowed direction similarity. Lower is more tolerant; higher rejects more jitter.",
    )
    st.divider()
    roi_mask_enabled = st.checkbox(
        "Use current ROI mask",
        value=False,
        help="Applies the mask from the ROI tab to reject, penalize, or constrain detections.",
    )
    st.divider()
    semantic_filter = st.checkbox(
        "Human semantic filter",
        value=False,
        help="Runs a person detector and suppresses motion boxes that overlap people.",
    )
    semantic_action = st.selectbox(
        "Human motion action",
        ["reject", "penalize"],
        index=0,
        disabled=not semantic_filter,
        help="Reject removes overlapping motion; penalize keeps it but tags it lower priority.",
    )
    semantic_conf = st.slider(
        "Person confidence",
        0.01,
        0.90,
        0.05,
        0.01,
        disabled=not semantic_filter,
        help="Minimum person detector confidence. Lower catches distant people but adds false positives.",
    )
    semantic_overlap_threshold = st.slider(
        "Person overlap threshold",
        0.01,
        1.0,
        0.15,
        0.01,
        disabled=not semantic_filter,
        help="Fraction of a motion box covered by a person box before action is applied.",
    )
    semantic_frame_stride = st.slider(
        "Person AI frame stride",
        1,
        20,
        2,
        1,
        disabled=not semantic_filter,
        help="Runs person detection every N frames and holds boxes between runs. Higher is faster.",
    )
    semantic_imgsz = st.selectbox(
        "Person AI image size",
        [640, 960, 1280],
        index=1,
        disabled=not semantic_filter,
        help="Input size for the person detector. Higher can catch smaller people but is slower.",
    )
    semantic_device = st.selectbox(
        "Person AI device",
        ["mps", "cpu", ""],
        index=0,
        disabled=not semantic_filter,
        help="Inference device. Use mps on Apple Silicon, cpu as fallback.",
    )
    with st.expander("Human model"):
        semantic_model_repo = st.text_input(
            "Model repo",
            "devanshty/WingID",
            disabled=not semantic_filter,
            help="Hugging Face repository containing the YOLO person model.",
        )
        semantic_model_file = st.text_input(
            "Model file",
            "yolo11l.pt",
            disabled=not semantic_filter,
            help="Model file inside the Hugging Face repository.",
        )
        semantic_weights = st.text_input(
            "Local weights path",
            "",
            disabled=not semantic_filter,
            help="Optional local .pt file. When set, it overrides repo/model download.",
        )

tabs = st.tabs(["Upload", "Local path", "ROI mask"])


def clamp01(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


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


def write_current_roi_mask(root: Path) -> Path | None:
    if not roi_mask_enabled:
        return None
    payload = current_roi_mask_payload()
    if not payload["zones"]:
        return None
    mask_path = root / "roi_mask.json"
    mask_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return mask_path


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
        "processing": "Processing frames",
        "finalizing": "Finalizing outputs",
        "complete": "Complete",
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
    fps = float(event.get("processing_fps") or 0.0)
    ms_per_frame = event.get("processing_ms_per_frame")
    if ms_per_frame is None:
        speed_text = f"{fps:.1f} fps"
    else:
        speed_text = f"{fps:.1f} fps / {float(ms_per_frame):.1f} ms per frame"
    remaining = event.get("remaining_frames")
    remaining_text = f"{remaining} frames left" if remaining is not None else "frames left unknown"
    counts = (
        f"raw={event.get('raw_detection_count', 0)} "
        f"kept={event.get('kept_detection_count', 0)} "
        f"semantic rejected={event.get('semantic_rejected_count', 0)} "
        f"ROI rejected={event.get('roi_rejected_count', 0)}"
    )
    status_placeholder.markdown(
        f"**{stage_label}**  \n"
        f"Elapsed `{elapsed}` · ETA `{eta}` · {remaining_text} · {speed_text}  \n"
        f"`{counts}`"
    )


def run_detector(args: list[str]) -> dict:
    progress_bar = st.progress(0.0, text="Starting detector...")
    status_placeholder = st.empty()
    command = [
        sys.executable,
        str(SCRIPT_PATH),
        "--json",
        "--progress-json",
        "--progress-interval",
        "0.5",
        *args,
    ]
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

    return_code = process.wait()
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

    progress_bar.progress(1.0, text="Complete")
    status_placeholder.markdown(
        f"**Complete**  \n"
        f"Processed `{summary['frame_count']}` frames in "
        f"`{format_duration(summary.get('processing_seconds'))}` · "
        f"`{summary.get('processing_ms_per_frame', 0.0)}` ms per frame"
    )
    return summary


def common_cli_args(out_dir: Path, roi_mask_path: Path | None = None) -> list[str]:
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
        "--shake-min-shift",
        str(float(shake_min_shift)),
        "--shake-consensus",
        str(float(shake_consensus)),
        "--shake-consensus-px",
        str(float(shake_consensus_px)),
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
        "--out-dir",
        str(out_dir),
    ]
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
        if semantic_device:
            args.extend(["--semantic-device", semantic_device])
        if semantic_weights.strip():
            args.extend(["--semantic-weights", semantic_weights.strip()])
    return args


def read_jsonl(path: str | Path) -> list[dict]:
    payload = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


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
    motion_bytes = Path(summary["motion_only_path"]).read_bytes()
    overlay_bytes = Path(summary["overlay_path"]).read_bytes()
    jsonl_bytes = Path(summary["jsonl_path"]).read_bytes()
    summary_bytes = Path(summary["summary_path"]).read_bytes()
    records = read_jsonl(summary["jsonl_path"])

    metric_cols = st.columns(6)
    metric_cols[0].metric("Frames", summary["frame_count"])
    metric_cols[1].metric("Motion frames", summary["frames_with_motion"])
    metric_cols[2].metric("Detections", summary["detection_count"])
    metric_cols[3].metric("Shake frames", summary.get("global_motion_detected_frames", 0))
    metric_cols[4].metric("Rejected frames", summary["global_motion_rejected_frames"])
    metric_cols[5].metric("ms / frame", summary.get("processing_ms_per_frame", 0.0))

    roi_cols = st.columns(4)
    roi_cols[0].metric("Raw detections", summary.get("raw_detection_count", summary["detection_count"]))
    roi_cols[1].metric("Kept detections", summary.get("kept_detection_count", summary["detection_count"]))
    roi_cols[2].metric("ROI rejected", summary.get("roi_rejected_count", 0))
    roi_cols[3].metric("ROI penalized", summary.get("roi_penalized_count", 0))
    filter_cols = st.columns(3)
    filter_cols[0].metric("Temporal rejected", summary.get("temporal_rejected_count", 0))
    filter_cols[1].metric("Unconfirmed rejected", summary.get("unconfirmed_rejected_count", 0))
    filter_cols[2].metric("Direction rejected", summary.get("direction_rejected_count", 0))
    semantic_cols = st.columns(4)
    semantic_cols[0].metric("Semantic objects", summary.get("semantic_detection_count", 0))
    semantic_cols[1].metric("Semantic rejected", summary.get("semantic_rejected_count", 0))
    semantic_cols[2].metric("Semantic penalized", summary.get("semantic_penalized_count", 0))
    semantic_cols[3].metric("Processing seconds", summary.get("processing_seconds", 0.0))
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
            f"stride={semantic_summary.get('frame_stride')} model={semantic_summary.get('model_repo')}"
        )

    preview_cols = st.columns(2)
    with preview_cols[0]:
        st.subheader("Motion only")
        st.video(motion_bytes)
        st.download_button(
            "Download motion-only video",
            motion_bytes,
            file_name=Path(summary["motion_only_path"]).name,
            mime="video/mp4",
        )
    with preview_cols[1]:
        st.subheader("Overlay")
        st.video(overlay_bytes)
        st.download_button(
            "Download overlay video",
            overlay_bytes,
            file_name=Path(summary["overlay_path"]).name,
            mime="video/mp4",
        )

    render_detections(records)
    download_cols = st.columns(2)
    download_cols[0].download_button(
        "Download detections JSONL",
        jsonl_bytes,
        file_name="motion_detections.jsonl",
        mime="application/x-ndjson",
    )
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
        with tempfile.TemporaryDirectory() as temp_root:
            temp_root_path = Path(temp_root)
            out_dir = temp_root_path / "outputs"
            roi_mask_path = write_current_roi_mask(temp_root_path)
            if roi_mask_enabled and roi_mask_path is None:
                st.warning("ROI mask is enabled, but no zones are defined. Running without ROI filtering.")
            with st.spinner("Rendering motion-only and overlay videos..."):
                try:
                    summary = run_detector(
                        ["video", str(uploaded_video_path), *common_cli_args(out_dir, roi_mask_path)]
                    )
                except Exception as exc:
                    st.error(f"Detector failed: {type(exc).__name__}: {exc}")
                    st.stop()
            render_outputs(summary)


with tabs[1]:
    default_path = str(SAMPLE_PATH) if SAMPLE_PATH.exists() else ""
    local_path = st.text_input("Video path", default_path)
    if st.button("Run motion diff on local path", type="primary"):
        if not local_path.strip():
            st.error("Enter a local video path.")
            st.stop()
        with tempfile.TemporaryDirectory() as temp_root:
            temp_root_path = Path(temp_root)
            out_dir = temp_root_path / "outputs"
            roi_mask_path = write_current_roi_mask(temp_root_path)
            if roi_mask_enabled and roi_mask_path is None:
                st.warning("ROI mask is enabled, but no zones are defined. Running without ROI filtering.")
            with st.spinner("Rendering motion-only and overlay videos..."):
                try:
                    summary = run_detector(
                        ["video", local_path.strip(), *common_cli_args(out_dir, roi_mask_path)]
                    )
                except Exception as exc:
                    st.error(f"Detector failed: {type(exc).__name__}: {exc}")
                    st.stop()
            render_outputs(summary)


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

        zone_cols = st.columns([2, 1, 1])
        zone_name = zone_cols[0].text_input(
            "Zone name",
            value=f"zone_{len(st.session_state['roi_zones']) + 1}",
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

        action_cols = st.columns(4)
        if action_cols[0].button("Undo point", help="Removes the last clicked polygon vertex."):
            if st.session_state["roi_points"]:
                st.session_state["roi_points"].pop()
            st.session_state["roi_last_click"] = None
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
            st.rerun()
        if action_cols[2].button("Clear zones", help="Deletes all zones from the current in-memory mask."):
            st.session_state["roi_zones"] = []
            st.session_state["roi_points"] = []
            st.session_state["roi_last_click"] = None
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
                    x = clamp01(float(coordinate["x"]) / float(max(1, annotated.shape[1])))
                    y = clamp01(float(coordinate["y"]) / float(max(1, annotated.shape[0])))
                    click_key = f"{preview_path}:{int(frame_index)}:{x:.6f}:{y:.6f}"
                    if click_key != st.session_state.get("roi_last_click"):
                        st.session_state["roi_points"].append([x, y])
                        st.session_state["roi_last_click"] = click_key
                        st.rerun()
                st.caption(
                    f"Preview {source_width}x{source_height}, frames={frame_count or 'unknown'}, "
                    f"pending points={len(st.session_state['roi_points'])}"
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
