"""Streamlit interface for fixed-camera motion differencing."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st


SCRIPT_PATH = Path(__file__).with_name("motion_diff_detector.py")
SAMPLE_PATH = Path.home() / "Downloads" / "fixedcameravideo_2026-06-10_00-10-22.mp4"


st.set_page_config(page_title="Motion Diff Drone Detector", layout="wide")
st.title("Motion Diff Drone Detector")

with st.sidebar:
    diff_threshold = st.slider("Difference threshold", 1, 100, 18, 1)
    min_area = st.number_input("Minimum motion area", min_value=1.0, max_value=100000.0, value=20.0)
    blur_kernel = st.selectbox("Blur kernel", [1, 3, 5, 7, 9, 11], index=2)
    morph_kernel = st.selectbox("Morphology kernel", [1, 3, 5, 7, 9], index=1)
    trail_frames = st.slider("Trail / hold frames", 0, 30, 3, 1)
    max_motion_ratio = st.slider("Max motion ratio", 0.01, 1.0, 0.10, 0.01)
    analysis_scale = st.slider("Analysis scale", 0.10, 1.0, 0.50, 0.05)

tabs = st.tabs(["Upload", "Local path"])


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


def common_cli_args(out_dir: Path) -> list[str]:
    return [
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
        "--out-dir",
        str(out_dir),
    ]


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

    metric_cols = st.columns(4)
    metric_cols[0].metric("Frames", summary["frame_count"])
    metric_cols[1].metric("Motion frames", summary["frames_with_motion"])
    metric_cols[2].metric("Detections", summary["detection_count"])
    metric_cols[3].metric("Rejected frames", summary["global_motion_rejected_frames"])

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
    if upload and st.button("Run motion diff on upload", type="primary"):
        with tempfile.TemporaryDirectory() as temp_root:
            temp_root_path = Path(temp_root)
            source_path = temp_root_path / upload.name
            source_path.write_bytes(upload.read())
            out_dir = temp_root_path / "outputs"
            with st.spinner("Rendering motion-only and overlay videos..."):
                try:
                    summary = run_detector(["video", str(source_path), *common_cli_args(out_dir)])
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
            out_dir = Path(temp_root) / "outputs"
            with st.spinner("Rendering motion-only and overlay videos..."):
                try:
                    summary = run_detector(["video", local_path.strip(), *common_cli_args(out_dir)])
                except Exception as exc:
                    st.error(f"Detector failed: {type(exc).__name__}: {exc}")
                    st.stop()
            render_outputs(summary)
