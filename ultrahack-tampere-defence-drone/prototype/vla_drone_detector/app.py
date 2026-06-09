"""Streamlit interface for testing the custom edge computing VLA detector."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from vla_drone_detector import (
    MODEL_DISPLAY_NAME,
    PROMPT_TYPES,
    THERMAL_POLARITIES,
    load_local_env,
)


SCRIPT_PATH = Path(__file__).with_name("vla_drone_detector.py")


load_local_env()
st.set_page_config(page_title="VLA Drone And Aircraft Classifier", layout="wide")
st.title("VLA Drone And Aircraft Classifier")
st.caption(f"Testing with a {MODEL_DISPLAY_NAME}.")

with st.sidebar:
    model_id = st.text_input(
        "Custom edge computing VLA model id",
        os.environ.get("VLA_MODEL", ""),
        help="Leave blank to use the runner default or VLA_MODEL from .env.",
    )
    confidence = st.slider("Confidence threshold", 0.05, 0.95, 0.25, 0.01)
    prompt_type = st.selectbox(
        "Prompt type",
        PROMPT_TYPES,
        index=PROMPT_TYPES.index("thermal_counter_uas"),
    )
    thermal_polarity = st.selectbox(
        "Thermal polarity",
        THERMAL_POLARITIES,
        index=THERMAL_POLARITIES.index("black_is_warm"),
    )
    sample_fps = st.number_input(
        "Video sample FPS",
        min_value=0.1,
        max_value=30.0,
        value=1.0,
        step=0.5,
    )
    custom_prompt = st.text_area(
        "Custom prompt suffix",
        "",
        height=120,
        placeholder="Optional extra scene or sensor notes.",
    )

if not os.environ.get("GEMINI_API_KEY"):
    st.info("Add GEMINI_API_KEY to a local .env file before running detection.")

tabs = st.tabs(["Image", "Video"])


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
    args = [
        "--conf",
        str(float(confidence)),
        "--prompt-type",
        str(prompt_type),
        "--thermal-polarity",
        str(thermal_polarity),
        "--sample-fps",
        str(float(sample_fps)),
        "--out-dir",
        str(out_dir),
    ]
    if model_id.strip():
        args.extend(["--model", model_id.strip()])
    if custom_prompt.strip():
        args.extend(["--custom-prompt", custom_prompt.strip()])
    return args


def render_detections_table(records: list[dict]) -> None:
    rows = []
    for record in records:
        prefix = {
            "frame_index": record.get("frame_index"),
            "timestamp_s": record.get("timestamp_s"),
        }
        for detection in record.get("detections", []):
            rows.append({**prefix, **detection})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No detections at this threshold.")


with tabs[0]:
    upload = st.file_uploader("Image file", type=["jpg", "jpeg", "png", "bmp", "webp"])
    if upload and st.button("Run image classifier", type="primary"):
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
    if upload and st.button("Run video classifier", type="primary"):
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
            metric_cols = st.columns(3)
            metric_cols[0].metric("Frames", summary["frame_count"])
            metric_cols[1].metric("Sampled frames", summary["sampled_frame_count"])
            metric_cols[2].metric("Detections", summary["detection_count"])
            if summary.get("error_count"):
                st.warning(f"{summary['error_count']} sampled frame calls failed.")
            render_detections_table(records[:250])
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
