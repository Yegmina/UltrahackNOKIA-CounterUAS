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
    diff_threshold = st.slider("Difference threshold", 1, 100, 18, 1)
    min_area = st.number_input("Minimum motion area", min_value=1.0, max_value=100000.0, value=1000.0)
    blur_kernel = st.selectbox("Blur kernel", [1, 3, 5, 7, 9, 11], index=2)
    morph_kernel = st.selectbox("Morphology kernel", [1, 3, 5, 7, 9], index=1)
    trail_frames = st.slider("Trail / hold frames", 0, 30, 3, 1)
    max_motion_ratio = st.slider("Max motion ratio", 0.01, 1.0, 0.10, 0.01)
    analysis_scale = st.slider("Analysis scale", 0.10, 1.0, 0.50, 0.05)
    shake_protection = st.checkbox("Shake protection", value=True)
    shake_min_shift = st.slider("Shake min shift", 0.0, 20.0, 1.5, 0.1)
    shake_consensus = st.slider("Shake consensus", 0.10, 1.0, 0.72, 0.01)
    shake_consensus_px = st.slider("Shake consensus px", 0.5, 10.0, 2.0, 0.1)
    st.divider()
    roi_mask_enabled = st.checkbox("Use current ROI mask", value=False)

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


def run_detector(args: list[str]) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--json", *args],
        capture_output=True,
        check=False,
        text=True,
        cwd=str(Path(__file__).parent),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or f"Detector exited with code {completed.returncode}.")
    for line in reversed(completed.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(completed.stdout.strip() or "Detector did not return JSON.")


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
        "--out-dir",
        str(out_dir),
    ]
    if not shake_protection:
        args.append("--disable-shake-protection")
    if roi_mask_path is not None:
        args.extend(["--roi-mask", str(roi_mask_path)])
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

    metric_cols = st.columns(5)
    metric_cols[0].metric("Frames", summary["frame_count"])
    metric_cols[1].metric("Motion frames", summary["frames_with_motion"])
    metric_cols[2].metric("Detections", summary["detection_count"])
    metric_cols[3].metric("Shake frames", summary.get("global_motion_detected_frames", 0))
    metric_cols[4].metric("Rejected frames", summary["global_motion_rejected_frames"])

    roi_cols = st.columns(4)
    roi_cols[0].metric("Raw detections", summary.get("raw_detection_count", summary["detection_count"]))
    roi_cols[1].metric("Kept detections", summary.get("kept_detection_count", summary["detection_count"]))
    roi_cols[2].metric("ROI rejected", summary.get("roi_rejected_count", 0))
    roi_cols[3].metric("ROI penalized", summary.get("roi_penalized_count", 0))
    roi_summary = summary.get("roi_mask", {})
    if roi_summary.get("enabled"):
        st.caption(
            f"ROI mode={roi_summary.get('mode')} zones={roi_summary.get('zone_count')} "
            f"path={roi_summary.get('path')}"
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
        )
        st.session_state["roi_mode"] = mode

        uploaded_preview_path = st.session_state.get("uploaded_video_cache_path", "")
        default_path = uploaded_preview_path or (str(SAMPLE_PATH) if SAMPLE_PATH.exists() else "")
        preview_path = st.text_input("Preview video path", default_path, key="roi_preview_path")
        if uploaded_preview_path and preview_path == uploaded_preview_path:
            st.caption(f"Using uploaded video for ROI preview: {Path(uploaded_preview_path).name}")
        frame_index = st.number_input("Frame number", min_value=0, value=0, step=1)

        zone_cols = st.columns([2, 1, 1])
        zone_name = zone_cols[0].text_input(
            "Zone name",
            value=f"zone_{len(st.session_state['roi_zones']) + 1}",
        )
        zone_type = zone_cols[1].selectbox("Zone type", ["ignore", "penalty", "flight"])
        penalty = zone_cols[2].number_input(
            "Penalty",
            min_value=0.0,
            max_value=1.0,
            value=0.5,
            step=0.1,
            disabled=zone_type != "penalty",
        )

        action_cols = st.columns(4)
        if action_cols[0].button("Undo point"):
            if st.session_state["roi_points"]:
                st.session_state["roi_points"].pop()
            st.session_state["roi_last_click"] = None
            st.rerun()
        if action_cols[1].button("Close zone", disabled=len(st.session_state["roi_points"]) < 3):
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
        if action_cols[2].button("Clear zones"):
            st.session_state["roi_zones"] = []
            st.session_state["roi_points"] = []
            st.session_state["roi_last_click"] = None
            st.rerun()
        action_cols[3].download_button(
            "Download mask JSON",
            json.dumps(current_roi_mask_payload(), indent=2),
            file_name="roi_mask.json",
            mime="application/json",
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
        uploaded_mask = st.file_uploader("Upload mask JSON", type=["json"], key="roi_mask_upload")
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
