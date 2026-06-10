"""Streamlit interface for synchronized laptop data collection."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

CURRENT_DIR = Path(__file__).parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from data_collection_firmware import DEFAULT_OUTPUT_ROOT, PROGRESS_PREFIX, discover_devices, safe_slug


SCRIPT_PATH = Path(__file__).with_name("data_collection_firmware.py")
RUN_CACHE_ROOT = Path(tempfile.gettempdir()) / "data_collection_firmware_runs"

st.set_page_config(page_title="Data Collection Firmware", layout="wide")
st.title("Data Collection Firmware")


def make_session_token(session_name: str) -> str:
    return f"{safe_slug(session_name)}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def request_stop(path: str) -> None:
    Path(path).write_text("stop\n", encoding="utf-8")


def request_marker(path: str, label: str = "manual_marker") -> None:
    marker = {
        "label": label,
        "source": "streamlit",
        "requested_utc_ns": time.time_ns(),
        "requested_monotonic_ns": time.monotonic_ns(),
    }
    marker_path = Path(path)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    with marker_path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(marker) + "\n")


@st.cache_data(show_spinner=False, ttl=5)
def cached_devices() -> dict:
    return discover_devices()


def device_label(device: dict) -> str:
    index = device.get("index")
    prefix = f"{index}: " if index is not None else ""
    return f"{prefix}{device.get('name', 'unknown')}"


def selected_indices(labels: list[str], devices: list[dict]) -> list[int]:
    lookup = {device_label(device): device for device in devices}
    indices: list[int] = []
    for label in labels:
        device = lookup.get(label)
        if device is None:
            continue
        index = device.get("index")
        if index is not None:
            indices.append(int(index))
    return indices


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "0s"
    seconds = max(0, int(round(float(seconds))))
    minutes, seconds = divmod(seconds, 60)
    if minutes == 0:
        return f"{seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours == 0:
        return f"{minutes}m {seconds:02d}s"
    return f"{hours}h {minutes:02d}m {seconds:02d}s"


def render_progress(event: dict, progress_slot, metric_slot, status_slot) -> None:
    stage = event.get("stage", "recording")
    elapsed = float(event.get("elapsed_s", 0.0) or 0.0)
    if stage == "armed":
        progress_slot.info(
            f"Armed for UTC start `{event.get('session_start_utc')}`. "
            f"Cameras={event.get('cameras', 0)} audio={event.get('audio_inputs', 0)}"
        )
    elif stage == "complete":
        progress_slot.success("Recording finalized.")
    else:
        progress_slot.info(f"Recording `{event.get('session_id', '')}` for {format_duration(elapsed)}")

    cols = metric_slot.columns(4)
    cols[0].metric("Elapsed", format_duration(elapsed))
    cols[1].metric("Video frames", int(event.get("video_frames", 0) or 0))
    cols[2].metric("Audio samples", int(event.get("audio_samples", 0) or 0))
    cols[3].metric("Markers", int(event.get("markers", 0) or 0))
    errors = event.get("errors") or []
    if errors:
        status_slot.warning("\n".join(str(error) for error in errors[-4:]))


def run_collector(args: list[str], stop_file: Path, marker_file: Path) -> dict:
    command = [
        sys.executable,
        str(SCRIPT_PATH),
        "--json",
        "--progress-json",
        *args,
        "--stop-file",
        str(stop_file),
        "--marker-file",
        str(marker_file),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(Path(__file__).parent),
    )
    if process.stdout is None:
        raise RuntimeError("Collector did not expose output.")

    stop_slot = st.empty()
    marker_slot = st.empty()
    flash_slot = st.empty()
    progress_slot = st.empty()
    metric_slot = st.empty()
    status_slot = st.empty()
    lines: list[str] = []
    summary: dict | None = None

    stop_slot.button(
        "Stop recording and finalize",
        key=f"stop_{stop_file.name}",
        on_click=request_stop,
        args=(str(stop_file),),
        help="Requests a graceful stop. The collector closes media files and writes the manifest.",
    )
    marker_slot.button(
        "Sync marker flash/beep",
        key=f"marker_{marker_file.name}",
        on_click=request_marker,
        args=(str(marker_file), "manual_marker"),
        help="Logs a marker event and asks the collector to beep. Keep this visible to phones/IR cameras when syncing.",
    )
    flash_slot.markdown(
        "<div style='height:60px;background:#fff;color:#000;border:2px solid #000;"
        "display:flex;align-items:center;justify-content:center;font-weight:700'>"
        "SYNC PANEL - press marker for logged flash/beep event</div>",
        unsafe_allow_html=True,
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
                render_progress(event, progress_slot, metric_slot, status_slot)
                continue
            if line.startswith("{"):
                try:
                    summary = json.loads(line)
                except json.JSONDecodeError:
                    pass
    finally:
        if process.poll() is None:
            request_stop(str(stop_file))
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    return_code = process.wait()
    if return_code != 0:
        detail = "\n".join(line for line in lines[-16:] if not line.startswith(PROGRESS_PREFIX))
        raise RuntimeError(detail or f"Collector exited with code {return_code}.")
    if summary is None:
        for line in reversed(lines):
            if line.startswith("{"):
                summary = json.loads(line)
                break
    if summary is None:
        raise RuntimeError("Collector did not return JSON.")
    return summary


def render_summary(summary: dict) -> None:
    st.subheader("Session output")
    st.caption(summary.get("out_dir", ""))
    cols = st.columns(5)
    cols[0].metric("Session", summary.get("session_id", ""))
    cols[1].metric("Streams", len(summary.get("streams", [])))
    cols[2].metric("Markers", len(summary.get("sync_markers", [])))
    cols[3].metric("Start jitter ms", summary.get("max_start_jitter_ms"))
    cols[4].metric("Seconds", summary.get("processing_seconds", 0.0))

    streams = summary.get("streams", [])
    if streams:
        st.dataframe(pd.DataFrame(streams), use_container_width=True, hide_index=True)
    marker_rows = summary.get("sync_markers", [])
    if marker_rows:
        st.dataframe(pd.DataFrame(marker_rows), use_container_width=True, hide_index=True)

    manifest_path = Path(summary.get("manifest_path", ""))
    if manifest_path.exists():
        st.download_button(
            "Download session manifest",
            manifest_path.read_bytes(),
            file_name=manifest_path.name,
            mime="application/json",
        )
    for stream in streams:
        media_path = Path(stream.get("path", ""))
        timing_path = Path(stream.get("timing_path", ""))
        col_a, col_b = st.columns(2)
        if media_path.exists():
            col_a.download_button(
                f"Download {media_path.name}",
                media_path.read_bytes(),
                file_name=media_path.name,
            )
        if timing_path.exists():
            col_b.download_button(
                f"Download {timing_path.name}",
                timing_path.read_bytes(),
                file_name=timing_path.name,
                mime="application/x-ndjson",
            )


with st.sidebar:
    st.header("Session")
    session_name = st.text_input("Session name", "data_collection")
    location = st.text_input("Location", "")
    notes = st.text_area("Notes", "")
    output_root = st.text_input("Output root", str(DEFAULT_OUTPUT_ROOT))
    if "armed_session_token" not in st.session_state:
        st.session_state["armed_session_token"] = ""
    if st.button("Arm"):
        st.session_state["armed_session_token"] = make_session_token(session_name)

    st.divider()
    st.header("Capture")
    video_width = st.number_input("Video width", min_value=160, max_value=3840, value=1280, step=160)
    video_height = st.number_input("Video height", min_value=120, max_value=2160, value=720, step=120)
    fps = st.number_input("FPS", min_value=1.0, max_value=120.0, value=30.0, step=1.0)
    audio_rate = st.selectbox("Audio sample rate", [16000, 44100, 48000], index=2)
    audio_channels = st.selectbox("Audio channels", [1, 2], index=0)
    schedule_delay_s = st.slider("Scheduled start delay", 0.5, 10.0, 2.0, 0.5)
    duration_enabled = st.checkbox("Auto-stop after duration", value=False)
    duration_s = st.number_input("Duration seconds", min_value=1.0, value=10.0, step=1.0, disabled=not duration_enabled)
    sync_beep = st.checkbox("Start/marker beep", value=True)
    timestamp_interval_s = st.number_input("Timestamp cadence seconds", min_value=1.0, value=10.0, step=1.0)
    timestamp_visible_s = st.number_input("Timestamp visible seconds", min_value=0.1, value=1.5, step=0.1)

devices = cached_devices()
camera_devices = devices.get("cameras", [])
audio_devices = devices.get("audio_inputs", [])

if st.button("Refresh devices"):
    cached_devices.clear()
    st.rerun()

device_cols = st.columns(2)
with device_cols[0]:
    st.subheader("Cameras")
    camera_labels = [device_label(device) for device in camera_devices]
    selected_camera_labels = st.multiselect("Video sources", camera_labels, default=camera_labels)
    st.json(camera_devices, expanded=False)
with device_cols[1]:
    st.subheader("Microphones / camera audio endpoints")
    audio_labels = [device_label(device) for device in audio_devices]
    selected_audio_labels = st.multiselect("Audio sources", audio_labels, default=audio_labels)
    st.json(audio_devices, expanded=False)

armed_token = st.session_state.get("armed_session_token") or make_session_token(session_name)
st.subheader("Phone / IR sync code")
st.code(armed_token)
st.caption("Film this code before recording. Use the white sync panel and beep markers during recording for alignment.")

if not camera_devices and not audio_devices:
    st.warning("No usable camera or audio input devices were discovered.")

camera_indices = selected_indices(selected_camera_labels, camera_devices)
audio_indices = selected_indices(selected_audio_labels, audio_devices)

if st.button("Start recording", type="primary", disabled=not (camera_indices or audio_indices)):
    run_root = RUN_CACHE_ROOT / f"run_{uuid.uuid4().hex[:8]}"
    run_root.mkdir(parents=True, exist_ok=True)
    stop_file = run_root / "stop_requested"
    marker_file = run_root / "sync_markers.jsonl"
    args = [
        "record",
        "--session-name",
        session_name,
        "--location",
        location,
        "--notes",
        notes,
        "--out-dir",
        output_root,
        "--schedule-delay-s",
        str(float(schedule_delay_s)),
        "--video-width",
        str(int(video_width)),
        "--video-height",
        str(int(video_height)),
        "--fps",
        str(float(fps)),
        "--audio-rate",
        str(int(audio_rate)),
        "--audio-channels",
        str(int(audio_channels)),
        "--timestamp-interval-s",
        str(float(timestamp_interval_s)),
        "--timestamp-visible-s",
        str(float(timestamp_visible_s)),
        "--progress-interval",
        "0.5",
    ]
    if duration_enabled:
        args.extend(["--duration-s", str(float(duration_s))])
    if sync_beep:
        args.append("--sync-beep")
    for index in camera_indices:
        args.extend(["--camera-index", str(index)])
    for index in audio_indices:
        args.extend(["--audio-index", str(index)])
    with st.spinner("Recording session..."):
        try:
            summary = run_collector(args, stop_file, marker_file)
        except Exception as exc:
            st.error(f"Collector failed: {type(exc).__name__}: {exc}")
            st.stop()
    st.session_state["last_summary"] = summary
    render_summary(summary)

if "last_summary" in st.session_state:
    st.divider()
    render_summary(st.session_state["last_summary"])
