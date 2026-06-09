"""Streamlit interface for testing the audio drone detector."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st


st.set_page_config(page_title="Audio Drone Detector", layout="wide")
st.title("Audio Drone Detector")

with st.sidebar:
    threshold = st.slider("Detection threshold", 0.05, 0.95, 0.65, 0.01)
    consecutive = st.number_input("Consecutive windows", min_value=1, max_value=10, value=3)
    median_kernel = st.selectbox("Median filter", [1, 3, 5, 7], index=1)
    udp_enabled = st.checkbox("Send UDP event")
    udp_host = st.text_input("UDP host", "127.0.0.1")
    udp_port = st.number_input("UDP port", min_value=1, max_value=65535, value=25100)

tabs = st.tabs(["File", "WAV URL"])


def run_detector(args: list[str]) -> dict:
    script = Path(__file__).with_name("audio_drone_detector.py")
    command = [sys.executable, str(script), "--json", *args]
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or f"Detector exited with code {completed.returncode}.")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(completed.stdout.strip() or str(exc)) from exc


def send_udp_record(record: dict, host: str, port: int) -> None:
    import socket

    payload = json.dumps({key: value for key, value in record.items() if key != "windows"}).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload, (host, port))
    finally:
        sock.close()


def render_event(event: dict) -> None:
    verdict = "Drone likely" if event["detected"] else "No confirmed drone"
    st.metric(verdict, f"{event['p_drone']:.3f}", f"{event['consecutive_hits']} window run")
    rows = [
        {
            "start_s": window["start_s"],
            "end_s": window["end_s"],
            "p_drone": window["p_drone"],
            "p_drone_smooth": window["p_drone_smooth"],
        }
        for window in event.get("windows", [])
    ]
    if rows:
        frame = pd.DataFrame(rows)
        st.line_chart(frame, x="start_s", y=["p_drone", "p_drone_smooth"])
        st.dataframe(frame, use_container_width=True, hide_index=True)
    st.json({key: value for key, value in event.items() if key != "windows"})
    if udp_enabled:
        send_udp_record(event, udp_host, int(udp_port))
        st.success(f"Sent UDP event to {udp_host}:{int(udp_port)}")


with tabs[0]:
    upload = st.file_uploader(
        "Audio file",
        type=["wav", "wave", "flac", "ogg", "mp3", "mpeg", "m4a", "aac"],
    )
    if upload and st.button("Run file detector", type="primary"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(upload.name).suffix) as handle:
            handle.write(upload.read())
            temp_path = Path(handle.name)
        with st.spinner("Running Hugging Face model..."):
            try:
                event = run_detector(
                    [
                        "file",
                        str(temp_path),
                        "--threshold",
                        str(threshold),
                        "--consecutive",
                        str(int(consecutive)),
                        "--median-kernel",
                        str(int(median_kernel)),
                    ]
                )
                event["source"] = upload.name
            except Exception as exc:
                st.error(f"Detector failed: {type(exc).__name__}: {exc}")
                st.stop()
            finally:
                temp_path.unlink(missing_ok=True)
        render_event(event)

with tabs[1]:
    url = st.text_input("WAV URL", "http://127.0.0.1:8080/audio.wav")
    max_bytes = st.number_input("Max bytes", min_value=100_000, max_value=100_000_000, value=20_000_000)
    if st.button("Fetch and run URL detector"):
        with st.spinner("Fetching WAV and running model..."):
            try:
                event = run_detector(
                    [
                        "url",
                        url,
                        "--max-bytes",
                        str(int(max_bytes)),
                        "--threshold",
                        str(threshold),
                        "--consecutive",
                        str(int(consecutive)),
                        "--median-kernel",
                        str(int(median_kernel)),
                    ]
                )
            except Exception as exc:
                st.error(f"Detector failed: {type(exc).__name__}: {exc}")
                st.stop()
        render_event(event)

st.download_button(
    "Download interface config",
    json.dumps(
        {
            "threshold": threshold,
            "consecutive_windows": int(consecutive),
            "median_kernel": int(median_kernel),
            "udp": {"enabled": udp_enabled, "host": udp_host, "port": int(udp_port)},
        },
        indent=2,
    ),
    file_name="audio_drone_detector_config.json",
    mime="application/json",
)
