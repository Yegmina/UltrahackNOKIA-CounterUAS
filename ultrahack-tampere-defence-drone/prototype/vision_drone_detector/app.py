"""Streamlit interface for testing UAV object detection."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st


DEFAULT_MODEL_PATH = Path(__file__).with_name("models") / "best.pt"
SCRIPT_PATH = Path(__file__).with_name("vision_drone_detector.py")


st.set_page_config(page_title="Vision Drone Detector", layout="wide")
st.title("Vision Drone Detector")

with st.sidebar:
    model_path = st.text_input("Model path", str(DEFAULT_MODEL_PATH))
    confidence = st.slider("Confidence threshold", 0.05, 0.95, 0.25, 0.01)
    image_size = st.number_input("Image size", min_value=160, max_value=1920, value=640, step=32)
    device = st.text_input("Device", "cpu")

tabs = st.tabs(["Image", "Video"])


def run_detector(args: list[str]) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--json", *args],
        capture_output=True,
        check=False,
        text=True,
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
        "--model",
        model_path,
        "--conf",
        str(confidence),
        "--imgsz",
        str(int(image_size)),
        "--device",
        device,
        "--out-dir",
        str(out_dir),
    ]


def render_detections_table(records: list[dict]) -> None:
    rows = []
    for record in records:
        prefix = {}
        if "frame_index" in record:
            prefix = {
                "frame_index": record["frame_index"],
                "timestamp_s": record["timestamp_s"],
            }
        for detection in record.get("detections", []):
            rows.append({**prefix, **detection})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No detections at this threshold.")


with tabs[0]:
    upload = st.file_uploader("Image file", type=["jpg", "jpeg", "png", "bmp", "webp"])
    if upload and st.button("Run image detector", type="primary"):
        with tempfile.TemporaryDirectory() as temp_root:
            temp_root_path = Path(temp_root)
            source_path = temp_root_path / upload.name
            source_path.write_bytes(upload.read())
            out_dir = temp_root_path / "outputs"
            try:
                summary = run_detector(["image", str(source_path), *common_cli_args(out_dir)])
                annotated_bytes = Path(summary["annotated_path"]).read_bytes()
                json_bytes = Path(summary["json_path"]).read_bytes()
                json_record = json.loads(json_bytes.decode("utf-8"))
            except Exception as exc:
                st.error(f"Detector failed: {type(exc).__name__}: {exc}")
                st.stop()

            st.image(annotated_bytes, caption="Annotated image", use_container_width=True)
            st.metric("Detections", summary["detection_count"])
            render_detections_table([json_record])
            st.download_button(
                "Download annotated image",
                annotated_bytes,
                file_name=f"{Path(upload.name).stem}_annotated.png",
                mime="image/png",
            )
            st.download_button(
                "Download detections JSON",
                json_bytes,
                file_name="detections.json",
                mime="application/json",
            )


with tabs[1]:
    upload = st.file_uploader("Video file", type=["mp4", "avi", "mov", "mkv"])
    if upload and st.button("Run video detector", type="primary"):
        with tempfile.TemporaryDirectory() as temp_root:
            temp_root_path = Path(temp_root)
            source_path = temp_root_path / upload.name
            source_path.write_bytes(upload.read())
            out_dir = temp_root_path / "outputs"
            try:
                summary = run_detector(["video", str(source_path), *common_cli_args(out_dir)])
                annotated_bytes = Path(summary["annotated_path"]).read_bytes()
                jsonl_bytes = Path(summary["jsonl_path"]).read_bytes()
                records = [
                    json.loads(line)
                    for line in jsonl_bytes.decode("utf-8").splitlines()
                    if line.strip()
                ]
            except Exception as exc:
                st.error(f"Detector failed: {type(exc).__name__}: {exc}")
                st.stop()

            st.video(annotated_bytes)
            st.metric("Frames", summary["frame_count"])
            st.metric("Detections", summary["detection_count"])
            render_detections_table(records[:200])
            st.download_button(
                "Download annotated video",
                annotated_bytes,
                file_name=f"{Path(upload.name).stem}_annotated.mp4",
                mime="video/mp4",
            )
            st.download_button(
                "Download detections JSONL",
                jsonl_bytes,
                file_name="detections.jsonl",
                mime="application/x-ndjson",
            )
